"""Routes for the Financial Diagnostic Report mode — `/api/financial-diagnostic-report/*`.

This module moves JSON and decides nothing. Every judgement is made in
`adapter.py` and the vendored `pipeline/xbrl_fetch/`, where it is versioned
and testable. A route that re-worded an answer or filtered a finding would be
a second, untested place where the report says something.
"""

from __future__ import annotations

import asyncio

import os

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.auth.deps import CurrentUser, require_user
from app.errors import InvalidRequestError, ModeUnavailableError

from . import adapter
from . import config as CFG
from .schemas import (
    XbrlDashboardResponse,
    XbrlEntitiesResponse,
    XbrlBusinessProfileResponse,
    XbrlHealthSummaryResponse,
    XbrlRiskClustersResponse,
    XbrlSignalsResponse,
    XbrlThresholdReset,
    XbrlThresholdUpdate,
    XbrlThresholdsResponse,
    XbrlCompanyOverviewResponse,
    XbrlTrendsResponse,
)

router = APIRouter(
    prefix="/api/financial-diagnostic-report", tags=["financial-diagnostic-report"]
)


@router.get("/health")
async def health():
    # Probing opens a database connection, so it runs off the event loop.
    available, reason = await asyncio.to_thread(adapter.status)
    llm = await asyncio.to_thread(adapter.llm_status)
    return {"mode": adapter.MODE_ID, "available": available, "reason": reason, "llm": llm}


# ===================================================================================
# XBRL direct-fetch path (as_db) — a different database, no panel, no trust
# scoring, because none of that applies to tagged XBRL facts.
# ===================================================================================

@router.get("/xbrl/entities", response_model=XbrlEntitiesResponse)
async def xbrl_entities():
    """Every filing in as_db, for the picker."""
    try:
        rows = await asyncio.to_thread(adapter.xbrl_entities)
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc
    return XbrlEntitiesResponse(entities=rows, count=len(rows))


@router.get("/xbrl/dashboard", response_model=XbrlDashboardResponse)
async def xbrl_dashboard(doc_id: str = Query(..., min_length=1, max_length=200)):
    """Block 2, computed directly from as_db for one filing — no report run, no
    panel, no cache. A single read, shaped once, returned."""
    try:
        result = await asyncio.to_thread(adapter.xbrl_dashboard, doc_id)
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc
    return XbrlDashboardResponse(**result)


@router.get("/xbrl/business-profile", response_model=XbrlBusinessProfileResponse)
async def xbrl_business_profile(doc_id: str = Query(..., min_length=1, max_length=200)):
    """Block 3: Business Profile, computed directly from as_db for one filing."""
    try:
        result = await asyncio.to_thread(adapter.xbrl_business_profile, doc_id)
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc
    return XbrlBusinessProfileResponse(**result)


@router.get("/xbrl/risk-clusters", response_model=XbrlRiskClustersResponse)
async def xbrl_risk_clusters(doc_id: str = Query(..., min_length=1, max_length=200)):
    """Block 6: Key Risk Clusters with Interactions, computed directly from as_db for one filing."""
    try:
        result = await asyncio.to_thread(adapter.xbrl_risk_clusters, doc_id)
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc
    return XbrlRiskClustersResponse(**result)


@router.get("/xbrl/company-overview", response_model=XbrlCompanyOverviewResponse)
async def xbrl_company_overview(doc_id: str = Query(..., min_length=1, max_length=200)):
    """Block 1: Company Overview — entity identity, statement flavour, reporting
    framework, computed directly from as_db for one filing."""
    try:
        result = await asyncio.to_thread(adapter.xbrl_company_overview, doc_id)
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc
    return XbrlCompanyOverviewResponse(**result)


@router.get("/xbrl/signals", response_model=XbrlSignalsResponse)
async def xbrl_signals(doc_id: str = Query(..., min_length=1, max_length=200)):
    """The S01-S27 signal library, evaluated directly against as_db for one filing's
    entity. Additive and standalone from `/xbrl/risk-clusters`: returns the raw
    FIRED/NOT_FIRED/ABSTAIN/NOT_APPLICABLE signal list with no cluster synthesis or LLM
    narration, so the signal engine can be reviewed on its own."""
    try:
        result = await asyncio.to_thread(adapter.xbrl_signals, doc_id)
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc
    return XbrlSignalsResponse(**result)


@router.get("/xbrl/health-summary", response_model=XbrlHealthSummaryResponse)
async def xbrl_health_summary(doc_id: str = Query(..., min_length=1, max_length=200)):
    """Block 4: Financial Health Summary — Structure & Performance, Interpreted."""
    try:
        result = await asyncio.to_thread(adapter.xbrl_health_summary, doc_id)
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc
    return XbrlHealthSummaryResponse(**result)


@router.get("/xbrl/trends", response_model=XbrlTrendsResponse)
async def xbrl_trends(doc_id: str = Query(..., min_length=1, max_length=200)):
    """Block 5: Key Trends & Structural Drift, computed directly from as_db for one filing."""
    try:
        result = await asyncio.to_thread(adapter.xbrl_trends, doc_id)
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc
    return XbrlTrendsResponse(**result)


@router.get("/xbrl/report/download")
async def xbrl_report_download(doc_id: str = Query(..., min_length=1, max_length=200)):
    """The XBRL Direct tab's five blocks, as a single planning-first PDF.

    Runs the same block functions the tab itself calls and reorders their
    payloads per the spec's output architecture (§14.1) — see
    `adapter.xbrl_report_pdf` / `xbrl_report.to_markdown` for the reordering
    rationale.
    """
    settings = CFG.load()
    try:
        filename, content = await asyncio.wait_for(
            asyncio.to_thread(adapter.xbrl_report_pdf, doc_id),
            timeout=settings.evaluate_timeout_seconds,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"Building the report for {doc_id} took longer than "
                   f"{settings.evaluate_timeout_seconds}s. Please retry.",
        )
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc

    return StreamingResponse(
        iter([content]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ===================================================================================
# Auditor-tunable thresholds. A save changes the report for EVERYONE, so who may save is
# one setting: FDR_THRESHOLD_EDIT_ROLE = "any" (default: any signed-in user) or "admin"
# (super administrators only). Reading is open to any signed-in user either way.
# ===================================================================================

def _threshold_editor(current: CurrentUser = Depends(require_user)) -> CurrentUser:
    policy = (os.environ.get("FDR_THRESHOLD_EDIT_ROLE") or "any").strip().lower()
    if policy == "admin" and not current.is_super_admin:
        # 403 with a JSON detail, never 401: the client signs the user out on any 401.
        raise HTTPException(status_code=403, detail="Changing thresholds is restricted to super administrators.")
    return current


@router.get("/xbrl/thresholds", response_model=XbrlThresholdsResponse)
async def xbrl_thresholds():
    """Every tunable threshold with its default, current value and bounds."""
    return await asyncio.to_thread(adapter.thresholds_snapshot)


@router.put("/xbrl/thresholds", response_model=XbrlThresholdsResponse)
async def xbrl_thresholds_update(body: XbrlThresholdUpdate, editor: CurrentUser = Depends(_threshold_editor)):
    """Validate and save. All-or-nothing: one out-of-range value rejects the whole request."""
    try:
        return await asyncio.to_thread(adapter.thresholds_update, body.values, editor.name)
    except InvalidRequestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/xbrl/thresholds/reset", response_model=XbrlThresholdsResponse)
async def xbrl_thresholds_reset(body: XbrlThresholdReset, editor: CurrentUser = Depends(_threshold_editor)):
    """Restore shipped defaults - for the listed keys, or for all of them when none are given."""
    return await asyncio.to_thread(adapter.thresholds_reset, body.keys, editor.name)
