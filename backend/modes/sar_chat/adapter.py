"""Adapter for the `SAR Q&A` chat mode.

Reuses the sar_prod_v3 package (vendored under statutory_auditor_report/)
and the SARChatPipeline built from chat_agents.py.

Two capabilities:
  * catalog  — entity/FY dropdown data (same DB query as the report mode).
  * ask      — run the 3-step chat pipeline for one user turn.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from app.errors import ModeUnavailableError

MODE_ID = "sar-chat"

# sar_prod_v3 lives inside the statutory_auditor_report mode directory.
_SAR_MODE_DIR = Path(__file__).resolve().parent.parent / "statutory_auditor_report"


def _ensure_path() -> None:
    """Add the statutory_auditor_report dir to sys.path so sar_prod_v3 is importable."""
    p = str(_SAR_MODE_DIR)
    if p not in sys.path:
        sys.path.insert(0, p)


# ---------------------------------------------------------------------------
# Catalog — same DB query as the report mode
# ---------------------------------------------------------------------------

def _db_conn():
    _ensure_path()
    try:
        from sar_prod_v3.tool_sar import _get_db_conn  # type: ignore
        return _get_db_conn()
    except Exception as exc:
        raise ModeUnavailableError(MODE_ID, f"{type(exc).__name__}: {exc}") from exc


def catalog() -> list[dict]:
    """Every ingested entity with the financial years available for it."""
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT company, fy_start, fy_end, doc_id, doc_name
                FROM documents
                WHERE company IS NOT NULL
                  AND fy_start IS NOT NULL
                  AND fy_end IS NOT NULL
                ORDER BY company, fy_end DESC
                """
            )
            rows = cur.fetchall()
    except ModeUnavailableError:
        raise
    except Exception as exc:
        raise ModeUnavailableError(
            MODE_ID, f"Could not read the documents table: {exc}"
        ) from exc
    finally:
        conn.close()

    by_entity: dict[str, dict] = {}
    for company, fy_start, fy_end, doc_id, doc_name in rows:
        entry = by_entity.setdefault(company, {"entity": company, "years": []})
        entry["years"].append(
            {
                "fy_start": fy_start,
                "fy_end": fy_end,
                "label": f"FY {fy_start}-{str(fy_end)[-2:]}",
                "doc_id": doc_id,
                "doc_name": doc_name,
            }
        )
    return sorted(by_entity.values(), key=lambda e: e["entity"].lower())


# ---------------------------------------------------------------------------
# Chat pipeline — built once, reused per request
# ---------------------------------------------------------------------------

_chat_pipeline = None
_chat_lock = threading.Lock()


def _get_chat_pipeline():
    """Build SARChatPipeline once and cache it."""
    global _chat_pipeline
    if _chat_pipeline is not None:
        return _chat_pipeline

    with _chat_lock:
        if _chat_pipeline is not None:
            return _chat_pipeline
        _ensure_path()
        try:
            from sar_prod_v3.chat_agents import SARChatPipeline  # type: ignore
            from sar_prod_v3.agent import _build_llm              # type: ignore
            _chat_pipeline = SARChatPipeline(
                llm_client=_build_llm(max_tokens=3500, temperature=0.1),
                enable_tracing=False,
            )
        except Exception as exc:
            raise ModeUnavailableError(
                MODE_ID,
                f"Could not initialise the SAR chat pipeline — {type(exc).__name__}: {exc}",
            ) from exc
    return _chat_pipeline


def status() -> tuple[bool, str | None]:
    """(available, reason) — never raises."""
    try:
        catalog()
        return True, None
    except ModeUnavailableError as exc:
        return False, exc.reason


def answer_question(
    query: str,
    company: str,
    fy_start: int,
    history: list[dict],
) -> dict:
    """
    Run the 3-step SAR chat pipeline for one user turn.

    The company and fy_start are injected as a [Context:] prefix so the
    Rewriter agent can extract them without the user having to type them.

    Args:
        query:    The user's raw question.
        company:  Entity name (from catalog dropdown selection).
        fy_start: Financial year start (from catalog dropdown selection).
        history:  Previous turns as [{"role": "user"|"assistant", "content": "..."}].

    Returns:
        {"mode": ..., "query": ..., "final_answer": ..., "evidences_md": ""}
    """
    pipeline = _get_chat_pipeline()

    # Inject company/FY context so the rewriter can pick it up
    contextual_query = f"[Context: Company = {company}, FY starting {fy_start}] {query}"

    try:
        answer = pipeline.ask(user_query=contextual_query, history=history)
    except ModeUnavailableError:
        raise
    except Exception as exc:
        raise ModeUnavailableError(
            MODE_ID, f"SAR chat failed — {type(exc).__name__}: {exc}"
        ) from exc

    return {
        "mode": MODE_ID,
        "query": query,
        "final_answer": answer,
        "evidences_md": "",
    }
