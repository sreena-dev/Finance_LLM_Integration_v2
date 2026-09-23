"""Adapter for the `Statutory_Auditor_Report` branch pipeline.

The branch's `Prod/` package is vendored verbatim as `sar_prod_v3/` — that name
is not cosmetic: the branch code imports itself absolutely
(`from sar_prod_v3.agent import ...`, `from sar_prod_v3.tool_sar import ...`),
so the package has to be importable under exactly that name. Putting this
mode's directory on sys.path satisfies those imports without editing a single
line of branch code.

Two independent capabilities are exposed, and they fail independently:

  * **catalog** — reads entity/FY rows straight from the `documents` table.
    Needs only `FINANCE_DSN` + psycopg2, so the dropdowns still populate on a
    machine with no LLM endpoint and no `yukta` install.
  * **generation** — constructs `SARReportPipeline`, which builds four LLM
    agents up front and therefore needs the full dependency set.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from app.errors import ModeUnavailableError

MODE_ID = "statutory-auditor-report"
BRANCH = "Statutory_Auditor_Report"

_MODE_DIR = Path(__file__).resolve().parent

_pipeline_lock = threading.Lock()
_pipeline = None


def _ensure_path() -> None:
    if str(_MODE_DIR) not in sys.path:
        sys.path.insert(0, str(_MODE_DIR))


# ---------------------------------------------------------------------------
# Catalog — powers the entity / financial-year dropdowns
# ---------------------------------------------------------------------------

def _fetch_tools():
    _ensure_path()
    try:
        from sar_prod_v3.tool_sar import FetchTools  # type: ignore
        return FetchTools
    except Exception as exc:  # noqa: BLE001
        raise ModeUnavailableError(MODE_ID, f"{type(exc).__name__}: {exc}") from exc


def _db_conn():
    _ensure_path()
    try:
        from sar_prod_v3.tool_sar import _get_db_conn  # type: ignore
        return _get_db_conn()
    except Exception as exc:  # noqa: BLE001
        raise ModeUnavailableError(MODE_ID, f"{type(exc).__name__}: {exc}") from exc


def catalog() -> list[dict]:
    """Every ingested entity with the financial years available for it.

    Shaped for a cascading pair of dropdowns: pick an entity, and its `years`
    list is exactly what the second dropdown should offer. Returning the years
    nested (rather than a flat global year list) is what stops the UI from
    offering an entity/FY combination that has no document behind it.
    """
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
    except Exception as exc:  # noqa: BLE001
        raise ModeUnavailableError(
            MODE_ID, f"Could not read the documents table: {exc}"
        ) from exc
    finally:
        conn.close()

    by_entity: dict[str, dict] = {}
    for company, fy_start, fy_end, doc_id, doc_name in rows:
        entry = by_entity.setdefault(
            company, {"entity": company, "years": []}
        )
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
# Generation
# ---------------------------------------------------------------------------

def _get_pipeline():
    """Build SARReportPipeline once and cache it (it builds 4 agents)."""
    global _pipeline
    if _pipeline is not None:
        return _pipeline

    with _pipeline_lock:
        if _pipeline is not None:
            return _pipeline
        _ensure_path()
        try:
            from sar_prod_v3.pipeline.sar_report_pipeline import (  # type: ignore
                SARReportPipeline,
            )
            _pipeline = SARReportPipeline()
        except Exception as exc:  # noqa: BLE001
            raise ModeUnavailableError(
                MODE_ID, f"Could not initialise the SAR pipeline — {type(exc).__name__}: {exc}"
            ) from exc
        return _pipeline


def status() -> tuple[bool, str | None]:
    """(available, reason) — never raises. Availability here means the catalog
    is readable; report generation additionally needs the LLM stack."""
    try:
        catalog()
        return True, None
    except ModeUnavailableError as exc:
        return False, exc.reason


def generate_report(entity: str, fy_start: int, fy_end: int, scope: str = "standalone") -> dict:
    """Run the SAR pipeline for one entity/FY. Blocking — call in a thread."""
    if scope not in {"standalone", "consolidated"}:
        raise ModeUnavailableError(
            MODE_ID, f"Unsupported scope '{scope}' — expected standalone or consolidated."
        )

    pipeline = _get_pipeline()
    started = time.perf_counter()

    try:
        result = pipeline.run(
            company=entity, fy_start=fy_start, fy_end=fy_end, scope=scope
        )
    except ValueError as exc:
        # Raised by the pipeline when no document matches entity + FY.
        raise ModeUnavailableError(MODE_ID, str(exc)) from exc
    except ModeUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ModeUnavailableError(
            MODE_ID, f"SAR generation failed — {type(exc).__name__}: {exc}"
        ) from exc

    return {
        "mode": MODE_ID,
        "entity": entity,
        "fy_label": f"FY {fy_start}-{str(fy_end)[-2:]}",
        "scope": scope,
        "report_md": result.get("report", ""),
        "display_response": result.get("display_response", ""),
        "executive_summary": result.get("executive_summary", ""),
        "detailed_report": result.get("detailed_report", ""),
        "parsed": result.get("parsed", {}) or {},
        "observations": result.get("observations", []) or [],
        # Gap-closure Phase 1 additions — see sar_prod_v3/GAP_CLOSURE_LOG.md.
        # `.get(..., "complete")` / `[]` defaults keep this adapter working
        # unchanged against an older pipeline build that predates these keys.
        "review_status": result.get("review_status", "complete"),
        "review_status_reasons": result.get("review_status_reasons", []) or [],
        "quality_flags": result.get("quality_flags", {}) or {},
        "doc_meta": result.get("doc_meta", {}) or {},
        "elapsed_seconds": round(time.perf_counter() - started, 2),
    }
