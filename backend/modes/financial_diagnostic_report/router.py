"""Routes for the Financial Diagnostic Report mode — `/api/financial-diagnostic-report/*`.

This module moves JSON and decides nothing. Every judgement — which subject a
question is about, whether a diagnostic fired, whether a figure can be trusted —
is made in `intent.py`, `answers.py` and the vendored `pipeline/fdr/`, where it
is versioned and testable. A route that re-worded an answer or filtered a
finding would be a second, untested place where the report says something.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.errors import InvalidRequestError, ModeUnavailableError, NotFoundError

from . import adapter
from . import config as CFG
from .schemas import (
    FDREntitiesResponse,
    FDRQueryRequest,
    FDRQueryResponse,
    FDRReportManifestResponse,
    FDRReportRequest,
    FDRReportResponse,
    XbrlDashboardResponse,
    XbrlEntitiesResponse,
    XbrlBusinessProfileResponse,
    XbrlHealthSummaryResponse,
    XbrlRiskClustersResponse,
    XbrlSignalsResponse,
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
    # The retrieval path's dependencies are reported SEPARATELY from the mode's
    # own availability, because they are not the same thing: the diagnostics
    # answer with no model at all, so an embedding or generation outage leaves
    # most of this mode working and must not show it as down.
    return {"mode": adapter.MODE_ID, "available": available, "reason": reason,
            "retrieval": await asyncio.to_thread(adapter.retrieval_status)}


@router.get("/entities", response_model=FDREntitiesResponse)
async def entities():
    """Every entity in the filings corpus, for the picker.

    One GROUP BY over `documents` — it deliberately extracts nothing. Populating
    a dropdown must not cost a minute of database time.
    """
    try:
        rows = await asyncio.to_thread(adapter.entities)
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc
    return FDREntitiesResponse(entities=rows, count=len(rows))


@router.post("/query", response_model=FDRQueryResponse)
async def query(req: FDRQueryRequest):
    """Answer one question about the selected entity, from a live read.

    Bounded by `FDR_EVALUATE_TIMEOUT_SECONDS`. When the bound is hit the request
    fails but the extraction it started keeps running and populates the cache, so
    the retry a user naturally makes is the one that answers quickly.
    """
    settings = CFG.load()
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                adapter.run_query, req.entity_id, req.query, refresh=req.refresh
            ),
            timeout=settings.evaluate_timeout_seconds,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=(
                f"Reading {req.entity_id}'s filings took longer than "
                f"{settings.evaluate_timeout_seconds}s. The read is still running — "
                f"ask again shortly and it will answer from the completed read."
            ),
        )
    except ModeUnavailableError as exc:
        # 503 — the one status that invites a retry. Converted here because the
        # gateway registers handlers for the other two only.
        raise exc.as_http() from exc
        # InvalidRequestError / NotFoundError propagate unhandled: 400 / 404 via the
        # gateway's own exception handlers, so every mode answers a bad request the same way.

    return FDRQueryResponse(**result)


@router.post("/query/stream")
async def query_stream(req: FDRQueryRequest):
    """The same answer as `/query`, watched instead of waited for.

    WHAT IS STREAMED, AND WHAT DELIBERATELY IS NOT
    ----------------------------------------------
    Progress is streamed; the answer is not streamed a word at a time. There is
    no language model in this pipeline, so the answer text does not arrive in
    pieces — it is formatted from computed values and exists in full the instant
    the arithmetic finishes. Revealing it character by character would be an
    animation imitating a model that is not there, and on an audit surface that
    is a lie about how the answer was produced.

    What a user actually waits on is the READ: parsing and binding each filing,
    a second or so apiece. So that is what comes down the wire — one event per
    filing, naming the year and how many figures it yielded — and then the
    finished answer in a single frame. On a cache hit the whole exchange is one
    progress event and the answer, which is the honest picture of what happened.

    A POST is used rather than an EventSource GET because the question travels
    in the body; the client reads the response stream directly.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def emit(event: dict) -> None:
        # Called from the extraction worker THREADS, so it may not touch the
        # loop directly. `call_soon_threadsafe` is the only safe hand-off.
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def work() -> None:
        try:
            result = adapter.run_query(
                req.entity_id, req.query, refresh=req.refresh,
                # Real token streaming, and ONLY where it is real: the retrieval
                # path generates its answer a piece at a time, so the pieces are
                # forwarded as they arrive. The deterministic path emits none,
                # because its answer is computed whole.
                on_token=lambda piece: emit({"type": "token", "text": piece}),
                progress=lambda stage, detail: emit({
                    "type": "progress",
                    "stage": stage,
                    "message": adapter._stage_message(stage, detail),
                    **{k: v for k, v in detail.items() if k in ("i", "n", "fy")},
                }),
            )
            emit({"type": "answer", **result})
        except (InvalidRequestError, NotFoundError) as exc:
            emit({"type": "error", "detail": str(exc), "status": 400
                  if isinstance(exc, InvalidRequestError) else 404})
        except ModeUnavailableError as exc:
            emit({"type": "error", "detail": exc.reason, "status": 503})
        except Exception as exc:  # noqa: BLE001 - a stream must fail visibly
            emit({"type": "error", "detail": f"{type(exc).__name__}: {exc}",
                  "status": 500})
        finally:
            emit({"type": "done"})

    asyncio.create_task(asyncio.to_thread(work))

    async def frames():
        while True:
            try:
                event = await asyncio.wait_for(
                    queue.get(), timeout=settings_heartbeat_seconds()
                )
            except asyncio.TimeoutError:
                # A comment frame keeps an idle proxy from closing a long read.
                yield ": keep-alive\n\n"
                continue
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("type") == "done":
                return

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        # nginx buffers proxied responses by default, which would hold every
        # progress frame until the request completed — turning a stream back
        # into a wait. These two headers are what make it actually stream.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )


def settings_heartbeat_seconds() -> float:
    """How long to wait for an event before sending a keep-alive comment."""
    return 15.0


@router.get("/report/manifest", response_model=FDRReportManifestResponse)
async def report_manifest():
    """What the report will contain, before any of it is built.

    Answered without touching the database, so the browser can lay out the
    blocks it is about to receive while the first read is still running.
    """
    from . import report as REP
    return FDRReportManifestResponse(blocks=adapter.report_manifest(),
                                     report_version=REP.REPORT_VERSION)


@router.post("/report", response_model=FDRReportResponse)
async def report(req: FDRReportRequest):
    """The whole report in one response, for a script or an integration.

    The browser uses `/report/stream` instead — same builders, same output, but
    delivered block by block so the first section can be read while the rest are
    still being built.
    """
    settings = CFG.load()
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(adapter.run_report, req.entity_id, refresh=req.refresh),
            timeout=settings.evaluate_timeout_seconds,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=(
                f"Reading {req.entity_id}'s filings took longer than "
                f"{settings.evaluate_timeout_seconds}s. The read is still running — "
                f"ask again shortly and it will build from the completed read."
            ),
        )
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc

    return FDRReportResponse(**result)


@router.post("/report/stream")
async def report_stream(req: FDRReportRequest):
    """The report, watched as it is built.

    Two kinds of event travel here, and they mean different things to a reader:

      progress  the filings being read, one event per filing. This is the wait.
      block     one finished section, delivered the moment it is built.

    Blocks arrive by completion rather than by number — every block is currently
    arithmetic and lands at once, but the narrated ones coming next will not, and
    a client that already orders what it receives will not need changing then.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def emit(event: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def work() -> None:
        try:
            result = adapter.run_report(
                req.entity_id,
                refresh=req.refresh,
                progress=lambda stage, detail: emit({
                    "type": "progress",
                    "stage": stage,
                    "message": adapter._stage_message(stage, detail),
                    **{k: v for k, v in detail.items() if k in ("i", "n", "fy", "count")},
                }),
                on_block=lambda block: emit({"type": "block", **block}),
            )
            emit({"type": "complete",
                  "entity_id": result["entity_id"],
                  "provenance": result["provenance"],
                  "versions": result["versions"],
                  "report_version": result["report_version"],
                  "elapsed_seconds": result["elapsed_seconds"]})
        except (InvalidRequestError, NotFoundError) as exc:
            emit({"type": "error", "detail": str(exc),
                  "status": 400 if isinstance(exc, InvalidRequestError) else 404})
        except ModeUnavailableError as exc:
            emit({"type": "error", "detail": exc.reason, "status": 503})
        except Exception as exc:  # noqa: BLE001 - a stream must fail visibly
            emit({"type": "error", "detail": f"{type(exc).__name__}: {exc}",
                  "status": 500})
        finally:
            emit({"type": "done"})

    asyncio.create_task(asyncio.to_thread(work))

    async def frames():
        while True:
            try:
                event = await asyncio.wait_for(
                    queue.get(), timeout=settings_heartbeat_seconds()
                )
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                continue
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("type") == "done":
                return

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )


@router.post("/report/download")
async def report_download(req: FDRReportRequest):
    """The same report as a PDF, built from the same block builders.

    A rendering of each block's own markdown, concatenated — same run, same
    numbered blocks, same "[n] citation" sources the screen shows, and
    nothing besides them (see `adapter.report_pdf`) — so the file cannot say
    anything the screen did not; only the layout is paginated rather than
    scrolled.
    """
    settings = CFG.load()
    try:
        filename, content = await asyncio.wait_for(
            asyncio.to_thread(adapter.report_pdf, req.entity_id,
                              refresh=req.refresh),
            timeout=settings.evaluate_timeout_seconds,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"Building {req.entity_id}'s report took longer than "
                   f"{settings.evaluate_timeout_seconds}s. Please retry.",
        )
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc

    return StreamingResponse(
        iter([content]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/cache/invalidate")
async def invalidate(entity_id: str | None = None):
    """Drop cached evaluations so the next question re-reads the corpus.

    Exists because the corpus can gain a filing under a long-lived process, and
    re-reading should not require a restart.
    """
    dropped = await asyncio.to_thread(adapter.invalidate, entity_id)
    return {"invalidated": dropped, "entity_id": entity_id}


# ===================================================================================
# XBRL direct-fetch path (as_db) — additive. See adapter.py's matching section for
# why this does not reuse `entities`/`report/*` above: a different database, no
# panel, no trust scoring, because none of that applies to tagged XBRL facts.
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




