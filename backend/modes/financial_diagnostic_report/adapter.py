"""Adapter for the Financial Diagnostic Report mode.

This mode answers only from `as_db`, the hosted Postgres of parsed MCA XBRL
filings — see `pipeline/xbrl_fetch/__init__.py` for why that path does not
reuse a trust/panel apparatus built for PDF/OCR-extracted figures. `pipeline/`
goes on `sys.path` so `xbrl_fetch` (vendored verbatim) is importable, the same
arrangement the Financial Statement and Trial Balance modes use for their
vendored trees.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from app.errors import InvalidRequestError, ModeUnavailableError, NotFoundError

from . import clients
from . import config as CFG

logger = logging.getLogger(__name__)

MODE_ID = "financial-diagnostic-report"

# Readiness is probed by `GET /api/modes?probe=true` on page load, so it must not
# re-open a database connection on every call. The verdict is cached briefly —
# long enough to make a page load cheap, short enough that a database coming back
# up is noticed without a restart.
_STATUS_TTL_SECONDS = 30.0
_status_lock = threading.Lock()
_status_cache: dict[str, Any] = {"at": 0.0, "value": None}


def _probe() -> tuple[bool, str | None]:
    try:
        _ensure_xbrl_importable()
        from xbrl_fetch import xbrl_entities as XE
        XE.list_entities()
    except Exception as exc:  # noqa: BLE001 - any failure degrades this mode only
        return False, f"as_db unreachable: {type(exc).__name__}: {exc}"
    return True, None


def status() -> tuple[bool, str | None]:
    """Is this mode able to answer right now, and if not, what would fix it."""
    now = time.monotonic()
    with _status_lock:
        cached = _status_cache["value"]
        if cached is not None and (now - _status_cache["at"]) < _STATUS_TTL_SECONDS:
            return cached

    verdict = _probe()
    with _status_lock:
        _status_cache["at"] = time.monotonic()
        _status_cache["value"] = verdict
    return verdict


_LLM_TTL_SECONDS = 60.0
_llm_cache: dict[str, Any] = {"at": 0.0, "value": None, "busy": False}


def _refresh_llm_status() -> None:
    try:
        verdict = clients.probe()
    except Exception as exc:  # noqa: BLE001 - probe() should not raise, but never kill the thread
        verdict = {"reachable": False, "reason": type(exc).__name__}
    with _status_lock:
        _llm_cache.update(at=time.monotonic(), value=verdict, busy=False)


def llm_status() -> dict[str, Any]:
    """Whether the narrative LLM is reachable, for the health route.

    Never makes the health route wait on the model: a stale answer is served at once and
    refreshed in the background. Only the very first call blocks, and then for at most the
    probe's short timeout. A False here does not make the mode unavailable (every narrative
    block falls back to its deterministic path) but it is reported, so a fallback is never
    mistaken for the model having run."""
    now = time.monotonic()
    with _status_lock:
        cached = _llm_cache["value"]
        fresh = cached is not None and (now - _llm_cache["at"]) < _LLM_TTL_SECONDS
        if fresh:
            return cached
        if cached is not None:
            if not _llm_cache["busy"]:
                _llm_cache["busy"] = True
                threading.Thread(target=_refresh_llm_status, daemon=True).start()
            return cached
    _refresh_llm_status()
    return _llm_cache["value"]


def _require_available() -> None:
    available, reason = status()
    if not available:
        raise ModeUnavailableError(MODE_ID, reason or "unavailable")


# ===================================================================================
# XBRL direct-fetch path (as_db).
#
# `xbrl_fetch` reads its own `XBRLAS_*` env vars directly, against as_db. The
# only thing this adapter does for it is put `pipeline/` on `sys.path`.
# ===================================================================================

_xbrl_path_ready = False


def _ensure_xbrl_importable() -> None:
    global _xbrl_path_ready
    if _xbrl_path_ready:
        return
    import sys
    from pathlib import Path

    pipeline_dir = Path(__file__).resolve().parent / "pipeline"
    if str(pipeline_dir) not in sys.path:
        sys.path.insert(0, str(pipeline_dir))
    _xbrl_path_ready = True


def _xbrl_meta(doc_id: str, fetch_meta_fn, action_desc: str) -> dict[str, Any]:
    """Shared doc_id-validate / fetch-meta / not-found boilerplate for every
    `/xbrl/*` block below. Only the plumbing is shared — each block's own
    build_* logic and its own fetch calls stay in that block's function."""
    if not doc_id:
        raise InvalidRequestError(f"A doc_id must be selected before {action_desc}.")

    try:
        meta = fetch_meta_fn(doc_id)
    except Exception as exc:  # noqa: BLE001
        raise ModeUnavailableError(
            MODE_ID, f"Could not reach as_db: {type(exc).__name__}: {exc}") from exc

    if meta is None:
        raise NotFoundError(f"No filing with doc_id {doc_id!r} in as_db.")

    return meta


def _fy_label(meta: dict[str, Any]) -> str:
    fy_end = meta.get("fy_end")
    fy_start = meta.get("fy_start")
    return (f"FY{(fy_start.year if fy_start else fy_end.year - 1)}-{str(fy_end.year)[-2:]}"
            if fy_end else "unknown")


def _company_identity(meta: dict[str, Any]) -> tuple[str, str]:
    company_name = meta.get("company_name") or meta.get("entity_cin") or ""
    cin = meta.get("entity_cin") or ""
    return company_name, cin


def xbrl_entities() -> list[dict[str, Any]]:
    """Every filing in as_db, for the picker. Raises ModeUnavailableError if as_db
    cannot be reached — checked by trying, not by a separate probe, since listing
    filings IS the cheapest possible read against it."""
    _ensure_xbrl_importable()
    from xbrl_fetch import xbrl_entities as XE

    try:
        return XE.list_entities()
    except Exception as exc:  # noqa: BLE001
        raise ModeUnavailableError(
            MODE_ID, f"Could not reach as_db: {type(exc).__name__}: {exc}") from exc


def xbrl_dashboard(doc_id: str) -> dict[str, Any]:
    """Block 2, computed directly from as_db for one filing.

    A `doc_id` that does not exist in `documents` is a 404, not an empty
    dashboard — the two mean different things to a caller (wrong id vs. a real
    filing with nothing computable, which the 8 known-broken filings in as_db
    still return successfully, with `computed_count: 0`, per
    `xbrl_ingestion_integrity_check.md`).
    """
    _ensure_xbrl_importable()
    from xbrl_fetch import xbrl_dashboard as XD
    from xbrl_fetch import xbrl_fetch as XF

    meta = _xbrl_meta(doc_id, XF.fetch_doc_meta, "a dashboard can be built")

    rows = XF.fetch_doc_metrics(doc_id)
    payload = XD.build_dashboard(rows, doc_id)

    return {
        "doc_id": doc_id,
        "entity_cin": meta.get("entity_cin") or "",
        "company_name": meta.get("company_name") or meta.get("entity_cin") or "",
        "fy_label": _fy_label(meta),
        **payload,
    }


def xbrl_business_profile(doc_id: str) -> dict[str, Any]:
    """Block 3: Business Profile, computed directly from as_db for one filing."""
    _ensure_xbrl_importable()
    from xbrl_fetch import xbrl_business_profile as XBP
    from xbrl_fetch import xbrl_profile_fetch as XPF

    meta = _xbrl_meta(doc_id, XPF.fetch_profile_meta, "a business profile can be built")

    metric_rows = XPF.fetch_profile_metrics(doc_id)
    disclosure_rows = XPF.fetch_profile_disclosures(doc_id)

    fy_label = _fy_label(meta)
    company_name, cin = _company_identity(meta)

    return XBP.build_business_profile(
        doc_id=doc_id,
        company_name=company_name,
        cin=cin,
        fy_label=fy_label,
        metric_rows=metric_rows,
        disclosure_rows=disclosure_rows,
        chat_fn=clients.chat,
    )


def xbrl_risk_clusters(doc_id: str) -> dict[str, Any]:
    """Block 6: Key Risk Clusters with Interactions, computed directly from as_db for one filing."""
    _ensure_xbrl_importable()
    from xbrl_fetch import xbrl_risk_clusters as XRC
    from xbrl_fetch import xbrl_risk_fetch as XRF

    meta = _xbrl_meta(doc_id, XRF.fetch_risk_meta, "risk clusters can be built")

    metric_rows = XRF.fetch_risk_metrics(doc_id)
    disclosure_rows = XRF.fetch_risk_disclosures(doc_id)

    fy_label = _fy_label(meta)
    company_name, cin = _company_identity(meta)

    return XRC.build_risk_clusters(
        doc_id=doc_id,
        company_name=company_name,
        cin=cin,
        fy_label=fy_label,
        metric_rows=metric_rows,
        disclosure_rows=disclosure_rows,
        chat_fn=clients.chat,
    )


def xbrl_company_overview(doc_id: str) -> dict[str, Any]:
    """Block 1: Company Overview — entity identity, statement flavour, and reporting
    framework, read structurally from as_db (namespace + a genuine compliance
    disclosure, never inferred). Additive and standalone, like `xbrl_signals`."""
    _ensure_xbrl_importable()
    from xbrl_fetch import xbrl_overview as XOV
    from xbrl_fetch import xbrl_risk_fetch as XRF

    meta = _xbrl_meta(doc_id, XRF.fetch_risk_meta, "the company overview can be built")
    fy_label = _fy_label(meta)
    company_name, cin = _company_identity(meta)

    overview = XOV.build_company_overview(doc_id, meta, company_name)
    overview["fy_label"] = fy_label
    overview["cin"] = cin
    return overview


def xbrl_signals(doc_id: str) -> dict[str, Any]:
    """The S01-S27 signal library, evaluated directly against as_db for one filing's
    entity. Additive and standalone: unlike `xbrl_risk_clusters` above, this does not
    synthesise cluster packages or call the LLM — it returns the raw, closed-set
    SignalResult list (FIRED / NOT_FIRED / ABSTAIN / NOT_APPLICABLE, each with its
    reason where it didn't run) so the signal engine can be inspected and reviewed on
    its own before any decision is made about wiring it into cluster synthesis."""
    _ensure_xbrl_importable()
    from xbrl_fetch import xbrl_signal_engine as XSE
    from xbrl_fetch import xbrl_risk_fetch as XRF

    meta = _xbrl_meta(doc_id, XRF.fetch_risk_meta, "signals can be evaluated")
    fy_label = _fy_label(meta)
    company_name, cin = _company_identity(meta)

    results = XSE.evaluate_signals_as_dicts(doc_id)
    return {
        "doc_id": doc_id,
        "company_name": company_name,
        "cin": cin,
        "fy_label": fy_label,
        "signals": results,
        "caveats": (
            "The outputs are leads for audit attention, not findings, opinions or "
            "conclusions.",
            "Materiality is applied separately (xbrl_signal_materiality.py) and is not "
            "reflected in a signal's FIRED/NOT_FIRED status.",
            "Cluster synthesis (risk-cluster packages, interactions, LLM narration) is "
            "out of scope for this endpoint.",
        ),
    }


def xbrl_trends(doc_id: str) -> dict[str, Any]:
    """Block 5: Key Trends & Structural Drift, computed directly from as_db for one filing."""
    _ensure_xbrl_importable()
    from xbrl_fetch import xbrl_trends as XT
    from xbrl_fetch import xbrl_trend_fetch as XTF

    meta = _xbrl_meta(doc_id, XTF.fetch_trend_meta, "trends can be computed")

    metric_rows = XTF.fetch_trend_facts(doc_id)

    fy_label = _fy_label(meta)
    company_name, cin = _company_identity(meta)

    return XT.build_key_trends(
        doc_id=doc_id,
        company_name=company_name,
        cin=cin,
        fy_label=fy_label,
        metric_rows=metric_rows,
        chat_fn=clients.chat,
    )


def xbrl_health_summary(doc_id: str) -> dict[str, Any]:
    """Block 4: Financial Health Summary — Structure & Performance, Interpreted."""
    _ensure_xbrl_importable()
    from xbrl_fetch import xbrl_health as XH
    from xbrl_fetch import xbrl_health_fetch as XHF

    meta = _xbrl_meta(doc_id, XHF.fetch_health_meta, "health summary can be computed")

    facts = XHF.fetch_health_facts(doc_id)
    turnover_facts = XHF.fetch_business_turnover_facts(doc_id)
    disclosures = XHF.fetch_health_disclosures(doc_id)

    fy_label = _fy_label(meta)
    company_name, cin = _company_identity(meta)

    return XH.build_health_summary(
        doc_id=doc_id,
        company_name=company_name,
        cin=cin,
        fy_label=fy_label,
        facts=facts,
        turnover_facts=turnover_facts,
        disclosures=disclosures,
        meta=meta,
        chat_fn=clients.chat,
    )


# ===================================================================================
# Auditor-tunable thresholds. The registry/store do the work; this layer only gives the
# router one place to call, with the same sys.path arrangement as the blocks above.
# ===================================================================================

def thresholds_snapshot() -> dict[str, Any]:
    _ensure_xbrl_importable()
    from xbrl_fetch import xbrl_threshold_registry as RT
    return RT.snapshot()


def thresholds_update(values: dict[str, float], actor: str) -> dict[str, Any]:
    """Validate and persist. A bad request raises InvalidRequestError and changes nothing."""
    _ensure_xbrl_importable()
    from xbrl_fetch import xbrl_threshold_registry as RT
    try:
        snap = RT.apply_display_values(values, actor)
    except RT.ThresholdError as exc:
        raise InvalidRequestError(str(exc)) from exc
    logger.info("FDR thresholds updated by %s (%d key(s) submitted)", actor, len(values))
    return snap


def thresholds_reset(keys: list[str] | None, actor: str) -> dict[str, Any]:
    _ensure_xbrl_importable()
    from xbrl_fetch import xbrl_threshold_registry as RT
    snap = RT.reset(keys, actor)
    logger.info("FDR thresholds reset by %s (%s)", actor, "all" if not keys else f"{len(keys)} key(s)")
    return snap


def _thresholds_note() -> str:
    """A report must say which thresholds produced it (spec Sec 16). Silent when every
    threshold is at its shipped default; otherwise lists each one changed by an auditor."""
    from xbrl_fetch import xbrl_threshold_registry as RT
    changed = RT.applied_overrides()
    if not changed:
        return ""
    lines = ["", "## Thresholds applied", "",
             "This report was produced with auditor-set thresholds that differ from the shipped "
             "defaults:", ""]
    for c in changed:
        lines.append(f"- {c['label']} ({c['used_in']}): {c['value']:g} {c['unit']} "
                     f"(default {c['default']:g} {c['unit']})")
    return chr(10).join(lines) + chr(10)


def xbrl_report_pdf(doc_id: str) -> tuple[str, bytes]:
    """The downloadable XBRL Direct report: (filename, PDF bytes).

    Runs the same five block functions the XBRL Direct tab itself calls
    (`xbrl_dashboard`, `xbrl_business_profile`, `xbrl_health_summary`,
    `xbrl_trends`, `xbrl_risk_clusters`) and hands their payloads to
    `xbrl_report.to_markdown`, which reorders them into the spec's
    planning-first structure — same figures and wording the screen already
    shows, just reordered and paginated rather than scrolled.
    """
    from . import pdf as PDF
    from xbrl_fetch import xbrl_report as XR

    dash = xbrl_dashboard(doc_id)
    try:
        profile = xbrl_business_profile(doc_id)
    except Exception:  # noqa: BLE001 - optional enrichment, dashboard is the anchor
        profile = None
    try:
        health = xbrl_health_summary(doc_id)
    except Exception:  # noqa: BLE001
        health = None
    try:
        trends = xbrl_trends(doc_id)
    except Exception:  # noqa: BLE001
        trends = None
    try:
        risk = xbrl_risk_clusters(doc_id)
    except Exception:  # noqa: BLE001
        risk = None

    text = XR.to_markdown(dash, profile, health, trends, risk)
    text += _thresholds_note()
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in dash.get("company_name", doc_id))
    return f"FDR_XBRL_{safe}.pdf", PDF.to_pdf(text)
