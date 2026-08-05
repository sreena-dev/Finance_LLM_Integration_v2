"""Adapter for the Trial Balance mode.

Sourced from the `Finance_llm_v2_naveen` repo (`main` branch) — that repo also
ships an unrelated chat/RAG feature and a Financial Diagnostic Report feature;
neither is integrated here. Only the trial-balance-related code was vendored,
verbatim, into `pipeline/yukta_rag/` — that package name is required: the
vendored code imports itself absolutely
(`from yukta_rag.trial_balance.trial_balance import ...`, etc.), so this
mode's directory must be on `sys.path` and the folder must keep that name.
Two small, explained patches were made inside the vendored tree (see
`pipeline/yukta_rag/core/config.py` and
`pipeline/yukta_rag/trial_balance/_sources.py`) — everything else is
untouched.

Three independent sub-features share one stored-TB layer (a trial balance is
uploaded once and referenced by `doc_id` from all three):

  * **ask**      — conversational Q&A over one stored TB (`TBAnalysisPipeline`,
    needs the LLM stack).
  * **audit**    — FSLI/risk-analytics narrative report + a downloadable
    workbook (`AuditPipeline`, needs the LLM stack).
  * **validate** — deterministic PASS/WARNING/HALT rule gate over one TB, or a
    prior/current-year comparison (`tbv_pipeline`, pure Python — no LLM).

Storage/ingest/list/delete is `trial_balance.py`'s module-level API and needs
only the database, so it (and `validate`) still works on a machine with no
`yukta` install or LLM endpoint reachable — mirroring how SAR's catalog stays
up when only its report generation is down.
"""

from __future__ import annotations

import secrets
import sys
import threading
import time
from pathlib import Path

from app.errors import ModeUnavailableError

MODE_ID = "trial-balance"
BRANCH = "Finance_llm_v2_naveen (main)"

_PIPELINE_DIR = Path(__file__).resolve().parent / "pipeline"

_store_lock = threading.Lock()
_store_state = {"loaded": False, "error": None}

_ask_lock = threading.Lock()
_ask_pipeline = None

_audit_lock = threading.Lock()
_audit_pipeline = None

# In-memory, process-lifetime token store for Excel files whose columns
# couldn't be auto-detected, so the browser can show a column-mapper and
# retry without re-uploading. Mirrors the source repo's own server.py — this
# glue was inline in its FastAPI routes, not a separate module to vendor.
_PREVIEW_TTL = 1800  # 30 minutes
_preview_lock = threading.Lock()
_preview_store: dict[str, dict] = {}

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
    (upload/list/delete/validate) is reachable; `ask`/`audit` additionally need
    the LLM stack and report their own reason if that's what's missing."""
    try:
        _load_store()
        return True, None
    except ModeUnavailableError as exc:
        return False, exc.reason


# ---------------------------------------------------------------------------
# Ingest / storage
# ---------------------------------------------------------------------------

def upload(filename: str, data: bytes) -> dict:
    """Parse + store an Excel trial balance. On a ValueError (auto-detection
    failed) stashes the raw bytes under a short-lived token and re-raises with
    `needs_mapping`/`preview_token` attached, so the caller can offer the
    column-mapper via `preview()`/`upload_mapped()`."""
    tb_store = _load_store()
    try:
        return tb_store.ingest_trial_balance(filename, data)
    except ValueError as exc:
        token = _store_for_preview(filename, data)
        err = ValueError(str(exc))
        err.needs_mapping = True  # type: ignore[attr-defined]
        err.preview_token = token  # type: ignore[attr-defined]
        raise err from None


def _store_for_preview(filename: str, data: bytes) -> str:
    token = secrets.token_urlsafe(16)
    expires = time.monotonic() + _PREVIEW_TTL
    with _preview_lock:
        expired = [k for k, v in _preview_store.items() if v["expires"] < time.monotonic()]
        for k in expired:
            del _preview_store[k]
        _preview_store[token] = {"filename": filename, "data": data, "expires": expires}
    return token


def preview(token: str) -> dict:
    """Row/column preview of a file stashed by a failed `upload()`, for the
    column-mapper UI."""
    with _preview_lock:
        entry = _preview_store.get(token)
    if entry is None or entry["expires"] < time.monotonic():
        raise ValueError("preview token not found or expired — please re-upload the file")
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
    with _preview_lock:
        entry = _preview_store.get(token)
    if entry is None or entry["expires"] < time.monotonic():
        raise ValueError("preview token expired — please re-upload the file and map again")
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
    except ValueError as exc:
        raise _wrap(exc, str(exc)) from exc
    except ModeUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc
    mem.add(question, result.get("answer", ""))
    return result


# ---------------------------------------------------------------------------
# Audit-mode risk analytics
# ---------------------------------------------------------------------------

def audit(
    doc_id: str,
    doc_id_prior: str | None = None,
    entity: str | None = None,
    engagement_context: str | None = None,
    framework: str | None = None,
) -> dict:
    if doc_id_prior and doc_id_prior == doc_id:
        raise ModeUnavailableError(
            MODE_ID, "pick two different trial balances for a two-period audit"
        )
    pipeline = _get_audit_pipeline()
    metadata = {k: v for k, v in {
        "entity": entity, "engagement_context": engagement_context, "framework": framework,
    }.items() if v}
    try:
        return pipeline.audit(doc_id, metadata=metadata or None, doc_id_prior=doc_id_prior)
    except ValueError as exc:
        raise _wrap(exc, str(exc)) from exc
    except ModeUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc


def audit_workbook(doc_id: str, doc_id_prior: str | None = None) -> bytes:
    if doc_id_prior and doc_id_prior == doc_id:
        raise ModeUnavailableError(
            MODE_ID, "pick two different trial balances for a two-period audit"
        )
    pipeline = _get_audit_pipeline()
    try:
        return pipeline.audit_workbook(doc_id, doc_id_prior)
    except ValueError as exc:
        raise _wrap(exc, str(exc)) from exc
    except ModeUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc


# ---------------------------------------------------------------------------
# Deterministic validation gate
# ---------------------------------------------------------------------------

def validate(doc_id: str, doc_id_prior: str | None = None, **params) -> dict:
    """No LLM involved — pure rule evaluation over already-stored TB(s)."""
    if doc_id_prior and doc_id_prior == doc_id:
        raise ModeUnavailableError(
            MODE_ID, "pick two different trial balances for a comparison"
        )
    _load_store()
    _ensure_path()
    try:
        from yukta_rag.tb_validation import tbv_pipeline  # type: ignore

        params = {k: v for k, v in params.items() if v is not None}
        if doc_id_prior:
            return tbv_pipeline.run_comparison(py_doc_id=doc_id_prior, cy_doc_id=doc_id, **params)
        return tbv_pipeline.run_single(doc_id=doc_id, **params)
    except ValueError as exc:
        raise _wrap(exc, str(exc)) from exc
    except ModeUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc
