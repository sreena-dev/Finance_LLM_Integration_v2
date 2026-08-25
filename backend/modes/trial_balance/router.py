"""Routes for the Trial Balance mode — `/api/trial-balance/*`.

Upload one or two Excel trial balances, then run any of three independent
sub-features against the resulting `doc_id`(s): `ask` (chat), `audit` (risk
report + workbook download), `validate` (deterministic rule gate). See
adapter.py for the source and scope of what's integrated.

Nine of these routes are the contract the web UI is built against — their paths,
request bodies and response shapes are fixed and must not drift:

    POST /upload · GET /preview · POST /upload-mapped · GET /documents
    DELETE /documents/{doc_id} · POST /ask · POST /audit
    POST /audit/workbook · POST /validate

The two grouping-file uploads and the three `/pdfs` routes are driven by the
Audit tab's grouping and PDF-evidence controls. `POST /validate/upload` — the
full-fidelity raw-bytes validation path, see `adapter.validate_upload` — has no
UI and is API-only.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile

from app.errors import ModeUnavailableError, NotFoundError
from app.schemas import (
    TBAskRequest,
    TBAskResponse,
    TBAuditRequest,
    TBGeneralAskRequest,
    TBGeneralAskResponse,
    TBGroupingMappedRequest,
    TBGroupingResponse,
    TBPdfInfo,
    TBPdfResponse,
    TBPreviewResponse,
    TBUploadMappedRequest,
    TBUploadResponse,
    TBValidateRequest,
    TBValidateResponse,
    TrialBalanceInfo,
)
from . import adapter

router = APIRouter(prefix="/api/trial-balance", tags=["trial-balance"])

_EXCEL_CONTENT_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/msexcel",
    "application/x-msexcel",
    "application/x-ms-excel",
    "application/x-excel",
    "application/x-dos_ms_excel",
    "application/xls",
    "application/x-xls",
    "application/vnd.ms-office",
}

# Tunable rule parameters, taken from the JSON request model so the multipart
# `/validate/upload` path can never drift from `/validate`'s accepted set.
_VALIDATE_PARAM_NAMES = set(TBValidateRequest.model_fields) - {"doc_id", "doc_id_prior"}


async def _read_excel(file: UploadFile, fallback_name: str, what: str) -> tuple[str, bytes]:
    """Validate that an upload looks like Excel and return (filename, bytes).

    Detection is deliberately permissive on MIME type — browsers (Windows
    especially) report `.xls` as `application/octet-stream` and other generic
    types — so the filename extension wins whenever it is unambiguous.
    """
    name = file.filename or fallback_name
    low = name.lower()
    ct = (file.content_type or "").strip().lower().split(";")[0].strip()
    if not (low.endswith((".xlsx", ".xls")) or ct in _EXCEL_CONTENT_TYPES):
        raise HTTPException(
            status_code=400,
            detail=f"only Excel (.xlsx/.xls) files are supported for {what}",
        )
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="the uploaded file is empty")
    return name, data


def _mapping_error(exc: ValueError) -> HTTPException:
    """Translate an adapter ValueError into its HTTP form. A parse failure that
    left a preview token behind becomes a 422 carrying `needs_mapping`, which is
    the browser's cue to open the column-mapper instead of showing a dead end."""
    if getattr(exc, "needs_mapping", False):
        return HTTPException(
            status_code=422,
            detail={
                "message": str(exc),
                "needs_mapping": True,
                "preview_token": exc.preview_token,  # type: ignore[attr-defined]
            },
        )
    return HTTPException(status_code=422, detail=f"could not read this file: {exc}")


@router.get("/health")
async def health():
    available, reason = adapter.status()
    return {"mode": adapter.MODE_ID, "available": available, "reason": reason}


# ---------------------------------------------------------------------------
# Ingest / storage
# ---------------------------------------------------------------------------

@router.post("/upload", response_model=TBUploadResponse)
async def upload(file: UploadFile = File(...)):
    name, data = await _read_excel(file, "trial_balance.xlsx", "a trial balance")
    try:
        info = await asyncio.to_thread(adapter.upload, name, data)
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc
    except ValueError as exc:
        raise _mapping_error(exc) from None
    return TBUploadResponse(**info)


@router.get("/preview", response_model=TBPreviewResponse)
async def preview(token: str):
    """Row/column preview for the column-mapper, after auto-detection failed."""
    result = await asyncio.to_thread(adapter.preview, token)
    return TBPreviewResponse(**result)


@router.post("/upload-mapped", response_model=TBUploadResponse)
async def upload_mapped(req: TBUploadMappedRequest):
    try:
        info = await asyncio.to_thread(
            adapter.upload_mapped,
            req.token, req.sheet_name, req.header_row, req.account_col,
            req.debit_col, req.credit_col, req.balance_col, req.code_col,
        )
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc
    except NotFoundError:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return TBUploadResponse(**info)


@router.get("/documents", response_model=list[TrialBalanceInfo])
async def list_documents():
    try:
        docs = await asyncio.to_thread(adapter.list_documents)
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc
    return [
        TrialBalanceInfo(
            doc_id=d["doc_id"], filename=d["filename"], sheet=d.get("sheet"),
            periods=d.get("periods") or [],
            uploaded_at=str(d["uploaded_at"]) if d.get("uploaded_at") else None,
        )
        for d in docs
    ]


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str):
    try:
        ok = await asyncio.to_thread(adapter.delete_document, doc_id)
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc
    if not ok:
        raise HTTPException(status_code=404, detail="no such trial balance")
    return {"status": "ok", "doc_id": doc_id}


# ---------------------------------------------------------------------------
# Ask (TB Analysis chat)
# ---------------------------------------------------------------------------

@router.post("/ask", response_model=TBAskResponse)
async def ask(req: TBAskRequest):
    if not req.doc_id.strip():
        raise HTTPException(status_code=400, detail="doc_id must not be empty")
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="question cannot be empty")
    try:
        result = await asyncio.to_thread(
            adapter.ask, req.doc_id, req.question, req.session_id
        )
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc
    return TBAskResponse(**result)


@router.post("/ask-general", response_model=TBGeneralAskResponse)
async def ask_general(req: TBGeneralAskRequest):
    """Answer a question with no trial balance attached.

    A trial balance is not a precondition for asking a question — this answers
    from the Ind AS / annual-report / reference corpora instead. Kept as its own
    route rather than making `/ask`'s doc_id optional, so that endpoint's contract
    stays exactly as the UI and any existing client already rely on it.
    """
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="question cannot be empty")
    try:
        result = await asyncio.to_thread(
            adapter.ask_general, req.question, req.session_id, req.upload_doc_ids
        )
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc
    return TBGeneralAskResponse(**result)


# ---------------------------------------------------------------------------
# Audit-mode risk analytics
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# PDF evidence (optional corroboration for `audit`)
#
# Its own database and embedding endpoint, so it reports availability separately
# from the mode — see adapter.pdf_status().
# ---------------------------------------------------------------------------

@router.get("/pdfs/health")
async def pdfs_health():
    available, reason = await asyncio.to_thread(adapter.pdf_status)
    return {"feature": "pdf-evidence", "available": available, "reason": reason}


@router.post("/pdfs", response_model=TBPdfResponse)
async def upload_pdf(file: UploadFile = File(...)):
    """Page-chunk, embed and store a PDF for use as audit evidence.

    Idempotent: `doc_id` is a content hash, so re-uploading the same file
    replaces its chunks instead of duplicating them.
    """
    name = file.filename or "document.pdf"
    ct = (file.content_type or "").strip().lower().split(";")[0].strip()
    if not (name.lower().endswith(".pdf") or ct == "application/pdf"):
        raise HTTPException(status_code=400, detail="only PDF files are supported here")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="the uploaded file is empty")
    try:
        info = await asyncio.to_thread(adapter.upload_pdf, name, data)
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return TBPdfResponse(**info)


@router.get("/pdfs", response_model=list[TBPdfInfo])
async def list_pdfs():
    try:
        docs = await asyncio.to_thread(adapter.list_pdfs)
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc
    return [
        TBPdfInfo(
            doc_id=d["doc_id"], filename=d["filename"],
            total_pages=d["total_pages"], total_chunks=d["total_chunks"],
            uploaded_at=str(d["uploaded_at"]) if d.get("uploaded_at") else None,
        )
        for d in docs
    ]


@router.delete("/pdfs/{doc_id}")
async def delete_pdf(doc_id: str):
    try:
        ok = await asyncio.to_thread(adapter.delete_pdf, doc_id)
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc
    if not ok:
        raise HTTPException(status_code=404, detail="no such uploaded PDF")
    return {"status": "ok", "doc_id": doc_id}


@router.post("/audit/upload-grouping", response_model=TBGroupingResponse)
async def upload_grouping(
    file: UploadFile = File(...),
    doc_id: str | None = Form(None),
    doc_id_2: str | None = Form(None),
):
    """Upload an optional chart-of-accounts / management FSLI grouping file.

    Parsed immediately; returns a short-lived `grouping_token` to pass on the
    `/audit` request. Pass `doc_id`/`doc_id_2` (the trial balance(s) this
    grouping applies to) so columns can be detected by matching real account
    codes/names rather than guessing from header text — and so the response can
    report how many accounts the grouping actually classifies.
    """
    name, data = await _read_excel(file, "grouping.xlsx", "a grouping file")
    try:
        result = await asyncio.to_thread(
            adapter.upload_grouping, name, data, doc_id, doc_id_2
        )
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc
    except ValueError as exc:
        raise _mapping_error(exc) from None
    return TBGroupingResponse(**result)


@router.post("/audit/upload-grouping-mapped", response_model=TBGroupingResponse)
async def upload_grouping_mapped(req: TBGroupingMappedRequest):
    """Apply an explicit grouping-file column mapping, after auto-detection failed."""
    try:
        result = await asyncio.to_thread(
            adapter.upload_grouping_mapped,
            req.token, req.sheet_name, req.header_row, req.code_col,
            req.name_col, req.group_col, req.heading_mode, req.doc_id, req.doc_id_2,
        )
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc
    except NotFoundError:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return TBGroupingResponse(**result)


@router.post("/audit")
async def audit(req: TBAuditRequest) -> dict:
    if not req.doc_id.strip():
        raise HTTPException(status_code=400, detail="doc_id must not be empty")
    try:
        return await asyncio.to_thread(
            adapter.audit, req.doc_id, req.doc_id_prior,
            req.entity, req.engagement_context, req.framework, req.grouping_token,
            req.upload_doc_ids,
        )
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc


@router.post("/audit/workbook")
async def audit_workbook(req: TBAuditRequest):
    """Download the TB_Audit.xlsx workbook (deterministic — no LLM).

    Reads `doc_id`, `doc_id_prior` and `upload_doc_ids` — the last of which fills
    the Quantified Risk Areas sheet from the supplied PDFs.

    `grouping_token` is *accepted* (this shares `TBAuditRequest` with `/audit`)
    but has no effect here: the workbook builder takes no `grouping_override`
    (`AuditPipeline.audit_workbook` has no such parameter upstream), so its sheets
    always reflect the keyword mapping engine. Call `/audit` for a grouping-aware
    result.
    """
    if not req.doc_id.strip():
        raise HTTPException(status_code=400, detail="doc_id must not be empty")
    try:
        data = await asyncio.to_thread(
            adapter.audit_workbook, req.doc_id, req.doc_id_prior, req.upload_doc_ids
        )
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="TB_Audit.xlsx"'},
    )


# ---------------------------------------------------------------------------
# Deterministic validation gate
#
# Two paths to the same rule engine. `/validate` reuses a stored TB and is what
# the UI calls. `/validate/upload` takes raw bytes and resolves strictly more —
# `company_code` and formula cells, which the stored parsed JSON discards — so
# rules TB-027 and the L1 company-code filter can return a real verdict there
# instead of SKIPPED.
# ---------------------------------------------------------------------------

@router.post("/validate/upload", response_model=TBValidateResponse)
async def validate_upload(
    file: UploadFile = File(...),
    file_prior: UploadFile | None = File(None),
    tolerance_pct: float | None = Form(None),
    documented_convention: str | None = Form(None),
    engagement_period: str | None = Form(None),
    target_currency: str | None = Form(None),
    rounding_account_threshold: float | None = Form(None),
    variance_materiality_pct: float | None = Form(None),
    target_company_code: str | None = Form(None),
    external_pl_figure: float | None = Form(None),
    tb_period_year: int | None = Form(None),
    params_json: str | None = Form(None),
):
    """Validate freshly uploaded raw trial balance bytes. Nothing is stored.

    Supplying `file_prior` runs a PY-vs-CY comparison instead of a single-TB
    gate. `params_json` optionally carries the list/dict-shaped parameters
    (`never_invert_accounts`, `approved_group_master`, `required_heads`,
    `tax_head_sign_map`, `sub_ledger_ref`) as one JSON-encoded form field, since
    multipart form fields cannot express them directly.
    """
    name, data = await _read_excel(file, "trial_balance.xlsx", "a trial balance")
    name_prior, data_prior = None, None
    if file_prior is not None:
        name_prior, data_prior = await _read_excel(
            file_prior, "trial_balance_prior.xlsx", "a prior-period trial balance"
        )

    params: dict = {}
    if params_json:
        try:
            params = json.loads(params_json)
        except ValueError:
            raise HTTPException(status_code=400, detail="params_json is not valid JSON")
        if not isinstance(params, dict):
            raise HTTPException(status_code=400, detail="params_json must be a JSON object")
        unknown = sorted(set(params) - _VALIDATE_PARAM_NAMES)
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"unsupported parameter(s) in params_json: {', '.join(unknown)}",
            )
    for key, value in (
        ("tolerance_pct", tolerance_pct),
        ("documented_convention", documented_convention),
        ("engagement_period", engagement_period),
        ("target_currency", target_currency),
        ("rounding_account_threshold", rounding_account_threshold),
        ("variance_materiality_pct", variance_materiality_pct),
        ("target_company_code", target_company_code),
        ("external_pl_figure", external_pl_figure),
        ("tb_period_year", tb_period_year),
    ):
        if value is not None:
            params[key] = value

    try:
        result = await asyncio.to_thread(
            adapter.validate_upload, data, name, data_prior, name_prior, **params
        )
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc
    return TBValidateResponse(**result)


@router.post("/validate", response_model=TBValidateResponse)
async def validate(req: TBValidateRequest):
    if not req.doc_id.strip():
        raise HTTPException(status_code=400, detail="doc_id must not be empty")
    params = req.model_dump(exclude={"doc_id", "doc_id_prior"}, exclude_none=True)
    try:
        result = await asyncio.to_thread(
            adapter.validate, req.doc_id, req.doc_id_prior, **params
        )
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc
    return TBValidateResponse(**result)
