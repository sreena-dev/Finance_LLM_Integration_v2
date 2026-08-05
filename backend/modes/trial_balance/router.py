"""Routes for the Trial Balance mode — `/api/trial-balance/*`.

Upload one or two Excel trial balances, then run any of three independent
sub-features against the resulting `doc_id`(s): `ask` (chat), `audit` (risk
report + workbook download), `validate` (deterministic rule gate). See
adapter.py for the source and scope of what's integrated.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, File, HTTPException, Response, UploadFile

from app.errors import ModeUnavailableError
from app.schemas import (
    TBAskRequest,
    TBAskResponse,
    TBAuditRequest,
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


@router.get("/health")
async def health():
    available, reason = adapter.status()
    return {"mode": adapter.MODE_ID, "available": available, "reason": reason}


# ---------------------------------------------------------------------------
# Ingest / storage
# ---------------------------------------------------------------------------

@router.post("/upload", response_model=TBUploadResponse)
async def upload(file: UploadFile = File(...)):
    name = file.filename or "trial_balance.xlsx"
    low = name.lower()
    ct = (file.content_type or "").strip().lower().split(";")[0].strip()
    if not (low.endswith((".xlsx", ".xls")) or ct in _EXCEL_CONTENT_TYPES):
        raise HTTPException(
            status_code=400,
            detail="only Excel (.xlsx/.xls) files are supported for a trial balance",
        )
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="the uploaded file is empty")

    try:
        info = await asyncio.to_thread(adapter.upload, name, data)
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc
    except ValueError as exc:
        if getattr(exc, "needs_mapping", False):
            raise HTTPException(
                status_code=422,
                detail={
                    "message": str(exc),
                    "needs_mapping": True,
                    "preview_token": exc.preview_token,  # type: ignore[attr-defined]
                },
            )
        raise HTTPException(status_code=422, detail=f"could not read this file: {exc}")
    return TBUploadResponse(**info)


@router.get("/preview", response_model=TBPreviewResponse)
async def preview(token: str):
    """Row/column preview for the column-mapper, after auto-detection failed."""
    try:
        result = await asyncio.to_thread(adapter.preview, token)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
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


# ---------------------------------------------------------------------------
# Audit-mode risk analytics
# ---------------------------------------------------------------------------

@router.post("/audit")
async def audit(req: TBAuditRequest) -> dict:
    if not req.doc_id.strip():
        raise HTTPException(status_code=400, detail="doc_id must not be empty")
    try:
        return await asyncio.to_thread(
            adapter.audit, req.doc_id, req.doc_id_prior,
            req.entity, req.engagement_context, req.framework,
        )
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc


@router.post("/audit/workbook")
async def audit_workbook(req: TBAuditRequest):
    if not req.doc_id.strip():
        raise HTTPException(status_code=400, detail="doc_id must not be empty")
    try:
        data = await asyncio.to_thread(
            adapter.audit_workbook, req.doc_id, req.doc_id_prior
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
# ---------------------------------------------------------------------------

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
