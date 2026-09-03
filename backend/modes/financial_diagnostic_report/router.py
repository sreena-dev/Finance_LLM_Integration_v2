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

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.errors import InvalidRequestError, ModeUnavailableError, NotFoundError

from . import adapter
from . import config as CFG
from .schemas import FDREntitiesResponse, FDRQueryRequest, FDRQueryResponse

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
    except (InvalidRequestError, NotFoundError):
        # 400 / 404 via the gateway's own exception handlers, so every mode
        # answers a bad request the same way.
        raise

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


@router.post("/cache/invalidate")
async def invalidate(entity_id: str | None = None):
    """Drop cached evaluations so the next question re-reads the corpus.

    Exists because the corpus can gain a filing under a long-lived process, and
    re-reading should not require a restart.
    """
    dropped = await asyncio.to_thread(adapter.invalidate, entity_id)
    return {"invalidated": dropped, "entity_id": entity_id}
