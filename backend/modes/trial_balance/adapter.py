"""Adapter for the Trial Balance mode.

Sourced from the `Finance_llm_v2` repo (`main` branch, commit `3a6e9bc`) — that
repo also ships an unrelated chat/RAG feature, a PDF-uploads corpus and a
Financial Diagnostic Report feature; none of those is integrated here. Only the
trial-balance-related code was vendored, verbatim, into `pipeline/yukta_rag/` —
that package name is required: the vendored code imports itself absolutely
(`from yukta_rag.trial_balance.trial_balance import ...`, etc.), so this
mode's directory must be on `sys.path` and the folder must keep that name.
Exactly ONE patch was made inside the vendored tree — `pipeline/yukta_rag/
core/config.py`, so Trial Balance's LLM settings cannot collide with SAR's.
Everything else is byte-for-byte the source repo's.

Three independent sub-features share one stored-TB layer (a trial balance is
uploaded once and referenced by `doc_id` from all three):

  * **ask**      — conversational Q&A over one stored TB (`TBAnalysisPipeline`,
    needs the LLM stack).
  * **audit**    — FSLI/risk-analytics narrative report + a downloadable
    workbook (`AuditPipeline`, needs the LLM stack). Optionally takes a
    client chart-of-accounts / management FSLI grouping file, uploaded
    separately and referenced by `grouping_token`.
  * **validate** — deterministic PASS/WARNING/HALT rule gate over one TB, or a
    prior/current-year comparison (`tbv_pipeline`, pure Python — no LLM). Runs
    either over an already-stored `doc_id` or over freshly uploaded raw bytes;
    the raw path resolves strictly more (see `validate_upload`).

Alongside them, `ask_general` answers a question with NO trial balance at all,
retrieving across the Ind AS / annual-report / reference corpora (`FinanceRAG`).
A trial balance is not a precondition for asking a question, so the UI routes
here whenever none is selected.

Storage/ingest/list/delete is `trial_balance.py`'s module-level API and needs
only the database, so it (and `validate`) still works on a machine with no
`yukta` install or LLM endpoint reachable — mirroring how SAR's catalog stays
up when only its report generation is down.

A fourth capability sits alongside those three rather than under them:

  * **PDF evidence** — annual-report / auditor-comment PDFs, page-chunked,
    embedded and stored in their own `finance_uploads` database
    (`uploads/uploads.py`). Passing their ids as `upload_doc_ids` to `audit`
    lets it quantify document-sourced risk items and cite page numbers, filling
    the report's Quantified Risk Areas section. Entirely optional: with no ids
    the audit reports `doc_evidence_status: "no_docs"` and is otherwise
    unchanged.

This last one is the only part of the mode that needs a database other than
`FINANCE_LLM_DSN`, and the only part that needs the embedding endpoint. It is
isolated behind its own lazy loader (`_load_uploads`) so an unreachable or
unprovisioned `UPLOADS_DSN` degrades PDF evidence alone — `status()`,
upload/list/delete, `ask`, `audit` without PDFs, and `validate` all keep working
and never touch it.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from app.errors import InvalidRequestError, ModeUnavailableError, NotFoundError

from . import token_store

MODE_ID = "trial-balance"
BRANCH = "Finance_llm_v2 (main @ 3a6e9bc)"

_PIPELINE_DIR = Path(__file__).resolve().parent / "pipeline"

_store_lock = threading.Lock()
_store_state = {"loaded": False, "error": None}

_ask_lock = threading.Lock()
_ask_pipeline = None

_audit_lock = threading.Lock()
_audit_pipeline = None

_uploads_lock = threading.Lock()
_uploads_state = {"loaded": False, "error": None}

_chat_lock = threading.Lock()
_chat_pipeline = None
_chat_sessions = None

# Files whose columns couldn't be auto-detected, and parsed grouping overrides,
# are held by `token_store` (a sibling module, copied from the source repo) so
# the browser can show a column-mapper and retry without re-uploading. It is
# filesystem- rather than memory-backed; see its docstring for why.

# Per (session, doc) conversation memory for `ask`.
_ask_sessions = None  # yukta_rag.chat.memory.SessionStore, built lazily


def _ensure_path() -> None:
    if str(_PIPELINE_DIR) not in sys.path:
        sys.path.insert(0, str(_PIPELINE_DIR))


def _require_yukta() -> None:
    """Fail fast when `yukta` is absent, for the two LLM-backed sub-features.

    `ask` and `audit` import it unconditionally when building their agents;
    `validate` and the storage layer never touch it.
    """
    import importlib.util

    if importlib.util.find_spec("yukta") is None:
        raise RuntimeError(
            "The 'yukta' package is not installed, and this sub-feature "
            "requires it to build its agents. It is not on PyPI — install it "
            "from your internal index or a source checkout."
        )


def _load_store():
    """Import the vendored trial_balance storage module once; ensure its table
    exists. Raises ModeUnavailableError on failure. Needs only the database."""
    if not _store_state["loaded"]:
        with _store_lock:
            if not _store_state["loaded"]:
                try:
                    _ensure_path()
                    import yukta_rag.trial_balance.trial_balance as tb_store  # type: ignore

                    tb_store.ensure_tb_input_schema()
                    _store_state["error"] = None
                except Exception as exc:  # noqa: BLE001 - degrade this mode only
                    _store_state["error"] = f"{type(exc).__name__}: {exc}"
                finally:
                    _store_state["loaded"] = True

    if _store_state["error"]:
        raise ModeUnavailableError(MODE_ID, _store_state["error"])

    import yukta_rag.trial_balance.trial_balance as tb_store  # type: ignore

    return tb_store


def _load_uploads():
    """Import the vendored PDF-uploads module once; create its database/tables if
    needed. Raises ModeUnavailableError on failure.

    Separate from `_load_store` because this is the mode's only dependency on a
    second database (`UPLOADS_DSN`) and on the embedding endpoint. Failing here
    must take down PDF evidence and nothing else, so no other entry point calls
    this — `audit` reaches it only when `upload_doc_ids` is non-empty.
    """
    if not _uploads_state["loaded"]:
        with _uploads_lock:
            if not _uploads_state["loaded"]:
                try:
                    _ensure_path()
                    import yukta_rag.uploads.uploads as up_store  # type: ignore

                    up_store.ensure_uploads_schema()
                    _uploads_state["error"] = None
                except Exception as exc:  # noqa: BLE001 - degrade this feature only
                    _uploads_state["error"] = (
                        f"PDF evidence is unavailable — the uploads database "
                        f"(UPLOADS_DSN) could not be prepared. {type(exc).__name__}: {exc}"
                    )
                finally:
                    _uploads_state["loaded"] = True

    if _uploads_state["error"]:
        raise ModeUnavailableError(MODE_ID, _uploads_state["error"])

    import yukta_rag.uploads.uploads as up_store  # type: ignore

    return up_store


def _get_ask_pipeline():
    """Build TBAnalysisPipeline once and cache it (it builds 2 agents)."""
    global _ask_pipeline, _ask_sessions
    if _ask_pipeline is not None:
        return _ask_pipeline

    with _ask_lock:
        if _ask_pipeline is not None:
            return _ask_pipeline
        _ensure_path()
        try:
            _require_yukta()
            from yukta_rag.trial_balance.tb_pipeline import TBAnalysisPipeline  # type: ignore
            from yukta_rag.chat.memory import SessionStore  # type: ignore

            _ask_pipeline = TBAnalysisPipeline()
            _ask_sessions = SessionStore()
        except Exception as exc:  # noqa: BLE001
            raise ModeUnavailableError(
                MODE_ID, f"Could not initialise the TB analysis pipeline — {type(exc).__name__}: {exc}"
            ) from exc
        return _ask_pipeline


def _get_chat_pipeline():
    """Build FinanceRAG once and cache it (it builds an LLM client + a splitter agent).

    This is the corpus-wide Q&A the source UI falls back to when no trial balance
    is active — it answers from the Ind AS / annual-report / reference corpora
    rather than from a stored TB, so it needs no `doc_id`.
    """
    global _chat_pipeline, _chat_sessions
    if _chat_pipeline is not None:
        return _chat_pipeline

    with _chat_lock:
        if _chat_pipeline is not None:
            return _chat_pipeline
        _ensure_path()
        try:
            _require_yukta()
            from yukta_rag.chat.memory import SessionStore  # type: ignore
            from yukta_rag.chat.pipeline import FinanceRAG  # type: ignore

            _chat_pipeline = FinanceRAG()
            _chat_sessions = SessionStore()
        except Exception as exc:  # noqa: BLE001
            raise ModeUnavailableError(
                MODE_ID,
                f"Could not initialise the corpus Q&A pipeline — {type(exc).__name__}: {exc}",
            ) from exc
        return _chat_pipeline


def _get_audit_pipeline():
    """Build AuditPipeline once and cache it (it builds LLM clients + 2 agents)."""
    global _audit_pipeline
    if _audit_pipeline is not None:
        return _audit_pipeline

    with _audit_lock:
        if _audit_pipeline is not None:
            return _audit_pipeline
        _ensure_path()
        try:
            _require_yukta()
            from yukta_rag.audit.audit_pipeline import AuditPipeline  # type: ignore

            _audit_pipeline = AuditPipeline()
        except Exception as exc:  # noqa: BLE001
            raise ModeUnavailableError(
                MODE_ID, f"Could not initialise the audit pipeline — {type(exc).__name__}: {exc}"
            ) from exc
        return _audit_pipeline


def _wrap(exc: Exception, detail: str | None = None) -> ModeUnavailableError:
    return ModeUnavailableError(MODE_ID, detail or f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def status() -> tuple[bool, str | None]:
    """(available, reason) — never raises. Available means the storage layer
    (upload/list/delete/validate/grouping-upload) is reachable; `ask`/`audit`
    additionally need the LLM stack and report their own reason if that's what's
    missing. `validate_upload` needs neither and is unaffected by this."""
    try:
        _load_store()
        return True, None
    except ModeUnavailableError as exc:
        return False, exc.reason


# ---------------------------------------------------------------------------
# Ingest / storage
# ---------------------------------------------------------------------------

def _needs_mapping(exc: Exception, filename: str, data: bytes) -> ValueError:
    """Stash raw bytes under a short-lived token and build the ValueError the
    router turns into a 422 carrying `needs_mapping`/`preview_token`, so the
    caller can offer the column-mapper instead of a dead end."""
    err = ValueError(str(exc))
    err.needs_mapping = True  # type: ignore[attr-defined]
    err.preview_token = token_store.put_preview(filename, data)  # type: ignore[attr-defined]
    return err


def upload(filename: str, data: bytes) -> dict:
    """Parse + store an Excel trial balance. On a ValueError (auto-detection
    failed) re-raises with `needs_mapping`/`preview_token` attached, so the
    caller can offer the column-mapper via `preview()`/`upload_mapped()`."""
    tb_store = _load_store()
    try:
        return tb_store.ingest_trial_balance(filename, data)
    except ValueError as exc:
        raise _needs_mapping(exc, filename, data) from None


def preview(token: str) -> dict:
    """Row/column preview of a file stashed by a failed `upload()` or
    `upload_grouping()`, for the column-mapper UI."""
    entry = token_store.get_preview(token)
    if entry is None:
        raise NotFoundError("preview token not found or expired — please re-upload the file")
    tb_store = _load_store()
    result = tb_store.preview_workbook(entry["data"])
    result["filename"] = entry["filename"]
    return result


def upload_mapped(
    token: str,
    sheet_name: str,
    header_row: int,
    account_col: int,
    debit_col: int | None = None,
    credit_col: int | None = None,
    balance_col: int | None = None,
    code_col: int | None = None,
) -> dict:
    """Ingest using an explicit user-supplied column mapping (same token
    `upload()` issued on failure)."""
    entry = token_store.get_preview(token)
    if entry is None:
        raise NotFoundError("preview token expired — please re-upload the file and map again")
    tb_store = _load_store()
    return tb_store.ingest_trial_balance_mapped(
        entry["filename"], entry["data"],
        sheet_name=sheet_name, header_row=header_row, account_col=account_col,
        debit_col=debit_col, credit_col=credit_col, balance_col=balance_col,
        code_col=code_col,
    )


def list_documents() -> list[dict]:
    tb_store = _load_store()
    return tb_store.list_trial_balances()


def delete_document(doc_id: str) -> bool:
    tb_store = _load_store()
    return tb_store.delete_trial_balance(doc_id)


# ---------------------------------------------------------------------------
# Ask (TB Analysis chat)
# ---------------------------------------------------------------------------

def ask(doc_id: str, question: str, session_id: str | None = None) -> dict:
    """Answer a question about one stored TB, with per-session follow-up memory."""
    pipeline = _get_ask_pipeline()
    mem = _ask_sessions.get(f"{session_id or 'default'}:{doc_id}")
    try:
        result = pipeline.ask_tb(doc_id, question, history=mem.turns())
    except (InvalidRequestError, NotFoundError):
        raise
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    except ModeUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc
    mem.add(question, result.get("answer", ""))
    return result


def ask_general(question: str, session_id: str | None = None,
                upload_doc_ids: list[str] | None = None) -> dict:
    """Answer a question from the corpora, with no trial balance involved.

    The counterpart to `ask`: that one grounds every figure in one stored TB, this
    one retrieves across the Ind AS / annual-report / reference corpora (and any
    supplied PDFs). A trial balance is not a precondition for asking a question,
    which is why the source UI routes here whenever none is selected.

    Its own session memory, keyed without a doc_id — mixing the two histories
    would let a TB-specific follow-up resolve against a corpus answer.
    """
    q = (question or "").strip()
    if not q:
        raise InvalidRequestError("question must not be empty")
    pipeline = _get_chat_pipeline()
    if upload_doc_ids:
        _load_uploads()
    mem = _chat_sessions.get(f"general:{session_id or 'default'}")
    try:
        result = pipeline.ask(q, history=mem.turns(), upload_doc_ids=upload_doc_ids or None)
    except (InvalidRequestError, NotFoundError):
        raise
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    except ModeUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc
    mem.add(q, result.get("answer", ""))
    return result


# ---------------------------------------------------------------------------
# Optional PDF evidence (annual report / auditor comments)
#
# The only part of this mode needing UPLOADS_DSN and the embedding endpoint.
# ---------------------------------------------------------------------------

def upload_pdf(filename: str, data: bytes) -> dict:
    """Page-chunk, embed and store a PDF. Idempotent — `doc_id` is a hash of the
    file content, so re-uploading replaces its chunks rather than duplicating.

    Raises ValueError when the PDF yields no extractable text, which in practice
    means a scanned/image-only file. There is no OCR, so that is a dead end worth
    naming rather than storing a document that can never match a query.
    """
    up_store = _load_uploads()
    try:
        info = up_store.ingest_uploaded_pdf(filename, data)
    except ModeUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc
    if not info.get("chunks"):
        raise ValueError(
            "no extractable text found — this looks like a scanned or image-only "
            "PDF, and OCR is not supported"
        )
    return info


def list_pdfs() -> list[dict]:
    return _load_uploads().list_uploaded_docs()


def delete_pdf(doc_id: str) -> bool:
    return _load_uploads().delete_uploaded_doc(doc_id)


def pdf_status() -> tuple[bool, str | None]:
    """(available, reason) for PDF evidence alone — never raises.

    Lets the UI hide or explain the PDF picker without inferring it from a failed
    upload, and keeps that judgement out of the mode-level `status()`.
    """
    try:
        _load_uploads()
        return True, None
    except ModeUnavailableError as exc:
        return False, exc.reason


# ---------------------------------------------------------------------------
# Optional client chart-of-accounts / management FSLI grouping (rule C7)
#
# Parsing needs only pandas, so this whole section works without `yukta`, a
# reachable LLM endpoint, or even a database — the stored TB is consulted only to
# improve column detection and to report coverage, and its absence degrades
# rather than fails. The resulting token is spent on `audit()`, which does need
# the full stack.
# ---------------------------------------------------------------------------

def _tb_accounts_for(doc_id: str | None, doc_id_2: str | None) -> list[dict]:
    """Accounts of the trial balance(s) a grouping file is being matched against.

    Never raises, and never touches the database unless a doc_id was actually
    supplied — value-matching is a reliability boost, not a precondition, so a
    DB outage degrades grouping detection to header-keyword mode rather than
    failing an upload that would otherwise have parsed fine.
    """
    if not (doc_id or doc_id_2):
        return []
    try:
        tb_store = _load_store()
    except ModeUnavailableError:
        return []
    accounts: list[dict] = []
    for did in (doc_id, doc_id_2):
        if not did:
            continue
        try:
            tb = tb_store.get_trial_balance(did)
        except Exception:  # noqa: BLE001 - never block the upload on this
            tb = None
        if tb:
            accounts.extend(tb.get("accounts") or [])
    return accounts


def _grouping_result(override: dict[str, str], tb_accounts: list[dict]) -> dict:
    """Token + how much of the selected trial balance the grouping actually covers.

    `n_matched` is the honest signal: an override can parse into hundreds of rows
    and still classify nothing (wrong file, or codes that don't correspond), in
    which case the audit silently falls back to keyword inference. Callers show
    this so a mismatch is visible before the run rather than inferred from odd
    figures afterwards.

    `n_entries` is the number of lookup keys, which is larger than the file's row
    count — each row contributes its code and its name. Only `n_matched` is
    meaningful to a user.
    """
    _ensure_path()
    from yukta_rag.audit.audit_grouping import count_tb_matches  # type: ignore

    return {
        "grouping_token": token_store.put_grouping(override),
        "n_entries": len(override),
        "n_matched": count_tb_matches(override, tb_accounts),
        "n_tb_accounts": len(tb_accounts),
        # Deprecated alias of n_entries. Redundant, but the source API returns it
        # for older clients, so dropping it would be a silent contract change.
        "n_rows": len(override),
    }


def upload_grouping(filename: str, data: bytes,
                    doc_id: str | None = None, doc_id_2: str | None = None) -> dict:
    """Parse a grouping file and return a token to pass as `grouping_token` on
    `audit()`.

    `doc_id`/`doc_id_2` identify the trial balance(s) the grouping applies to.
    When given, their real account codes/names drive column detection by
    value-matching instead of header guessing — far more reliable across real
    client files, whose headers vary too much for any fixed keyword list.

    On a detection failure raises with `needs_mapping`/`preview_token` attached,
    exactly like `upload()`, so the caller can offer the manual mapper.

    Needs no database of its own — only `_tb_accounts_for` does, and only when a
    doc_id is given.
    """
    _ensure_path()
    from yukta_rag.audit.audit_grouping import (  # type: ignore
        GroupingParseError, parse_grouping_file,
    )

    tb_accounts = _tb_accounts_for(doc_id, doc_id_2)
    try:
        override = parse_grouping_file(data, tb_accounts=tb_accounts or None)
    except GroupingParseError as exc:
        raise _needs_mapping(exc, filename, data) from None
    return _grouping_result(override, tb_accounts)


def upload_grouping_mapped(
    token: str,
    sheet_name: str,
    header_row: int,
    code_col: int | None = None,
    name_col: int | None = None,
    group_col: int | None = None,
    heading_mode: bool = False,
    doc_id: str | None = None,
    doc_id_2: str | None = None,
) -> dict:
    """Apply an explicit, user-supplied grouping-file column mapping (same token
    `upload_grouping()` issued on failure)."""
    entry = token_store.get_preview(token)
    if entry is None:
        raise NotFoundError("preview token expired — please re-upload the file and map again")
    _ensure_path()
    from yukta_rag.audit.audit_grouping import parse_grouping_file_with_map  # type: ignore

    override = parse_grouping_file_with_map(
        entry["data"], sheet_name=sheet_name, header_row=header_row,
        code_col=code_col, name_col=name_col, group_col=group_col,
        heading_mode=heading_mode,
    )
    return _grouping_result(override, _tb_accounts_for(doc_id, doc_id_2))


def _resolve_grouping(grouping_token: str | None) -> dict[str, str] | None:
    if not grouping_token:
        return None
    override = token_store.get_grouping(grouping_token)
    if override is None:
        raise NotFoundError("grouping upload not found or expired — please re-upload it")
    return override


# ---------------------------------------------------------------------------
# Audit-mode risk analytics
# ---------------------------------------------------------------------------

def audit(
    doc_id: str,
    doc_id_prior: str | None = None,
    entity: str | None = None,
    engagement_context: str | None = None,
    framework: str | None = None,
    grouping_token: str | None = None,
    upload_doc_ids: list[str] | None = None,
) -> dict:
    if doc_id_prior and doc_id_prior == doc_id:
        raise InvalidRequestError(
            "pick two different trial balances for a two-period audit"
        )
    grouping_override = _resolve_grouping(grouping_token)
    # Fail fast with the uploads-specific reason rather than letting the pipeline
    # swallow it: extract_doc_risk_items() catches everything and degrades to
    # `doc_evidence_status: "unavailable"`, so a misconfigured UPLOADS_DSN would
    # otherwise show up as a silently PDF-less report.
    if upload_doc_ids:
        _load_uploads()
    pipeline = _get_audit_pipeline()
    metadata = {k: v for k, v in {
        "entity": entity, "engagement_context": engagement_context, "framework": framework,
    }.items() if v}
    try:
        return pipeline.audit(doc_id, metadata=metadata or None, doc_id_prior=doc_id_prior,
                              grouping_override=grouping_override,
                              upload_doc_ids=upload_doc_ids or None)
    except (InvalidRequestError, NotFoundError):
        raise
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    except ModeUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc


def audit_workbook(doc_id: str, doc_id_prior: str | None = None,
                   upload_doc_ids: list[str] | None = None) -> bytes:
    if doc_id_prior and doc_id_prior == doc_id:
        raise InvalidRequestError(
            "pick two different trial balances for a two-period audit"
        )
    if upload_doc_ids:
        _load_uploads()
    pipeline = _get_audit_pipeline()
    try:
        return pipeline.audit_workbook(doc_id, doc_id_prior,
                                       upload_doc_ids=upload_doc_ids or None)
    except (InvalidRequestError, NotFoundError):
        raise
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    except ModeUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc


# ---------------------------------------------------------------------------
# Deterministic validation gate
# ---------------------------------------------------------------------------

def validate(doc_id: str, doc_id_prior: str | None = None, **params) -> dict:
    """No LLM involved — pure rule evaluation over already-stored TB(s).

    Convenience path: reuses the stored, already-parsed TB rather than requiring
    a re-upload. `company_code` and raw formula text do not survive in that
    parsed JSON, so the rules needing them (TB-027, and the L1 company-code
    filter) report SKIPPED rather than fabricating a result. Use
    `validate_upload()` when those matter.
    """
    if doc_id_prior and doc_id_prior == doc_id:
        raise InvalidRequestError(
            "pick two different trial balances for a comparison"
        )
    _load_store()
    _ensure_path()
    try:
        from yukta_rag.tb_validation import tbv_pipeline  # type: ignore

        params = {k: v for k, v in params.items() if v is not None}
        if doc_id_prior:
            return tbv_pipeline.run_comparison(py_doc_id=doc_id_prior, cy_doc_id=doc_id, **params)
        return tbv_pipeline.run_single(doc_id=doc_id, **params)
    except (InvalidRequestError, NotFoundError):
        raise
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    except ModeUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc


def validate_upload(data: bytes, label: str | None = None,
                    data_prior: bytes | None = None, label_prior: str | None = None,
                    **params) -> dict:
    """Full-fidelity path — validate freshly uploaded raw bytes.

    Resolves all 9 Step-0 column roles (including `company_code`) and preserves
    raw formula-cell access, neither of which survives in an already-stored TB's
    parsed JSON. Needs no database at all: nothing is stored, so this works even
    when `_load_store()` would fail.
    """
    _ensure_path()
    try:
        from yukta_rag.tb_validation import tbv_pipeline  # type: ignore

        params = {k: v for k, v in params.items() if v is not None}
        if data_prior is not None:
            return tbv_pipeline.run_comparison(
                py_data=data_prior, py_label=label_prior,
                cy_data=data, cy_label=label, **params)
        return tbv_pipeline.run_single(data=data, label=label, **params)
    except (InvalidRequestError, NotFoundError):
        raise
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    except ModeUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc
