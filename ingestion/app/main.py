"""HTTP surface for the ingestion service.

Four routes, and deliberately no more. This service converts a scanned PDF into
verified tables; it holds no user state, enforces no authorisation, and knows
nothing about conversations or entities. The gateway in front of it does all of
that, and is the only thing that should be able to reach this port.

    GET  /health                     readiness, including what is actually installed
    POST /ingest                     upload -> job id
    GET  /ingest/{job_id}            poll one job
    GET  /ingest/{job_id}/events     server-sent progress, ending with the result
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

# Load .env BEFORE importing config, which snapshots the environment at import.
#
# In Docker this is redundant -- compose passes the file through `env_file`. Run
# directly with uvicorn on a host it is not: nothing else loads it, so
# LLM_BASE_URL is unset, the VLM capability probe never runs, and the service
# reports `vlm_configured: false` while the endpoint is sitting there configured
# in .env. That looks like a broken vision model rather than an unloaded file.
#
# Searched most-specific first and never with override, so a variable already
# exported in the shell keeps winning -- the same rule and the same reasoning as
# backend/app/main.py.
_SERVICE_DIR = Path(__file__).resolve().parent.parent
for _candidate in (_SERVICE_DIR / ".env", _SERVICE_DIR.parent / ".env"):
    if _candidate.is_file():
        load_dotenv(_candidate, override=False)
        break

from .config import Config  # noqa: E402
from .jobs import REGISTRY  # noqa: E402

logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Artha.AI ingestion", version="1.0.0")
router = APIRouter(prefix="/ingest", tags=["ingest"])

# Readiness state for /health, written ONLY by _warm_up_dependencies (a single
# background thread) and read by /health. Never written from the event loop,
# and never used to trigger an import from a request handler -- see that
# function's docstring for why either of those would reintroduce the exact
# stall this exists to remove. Plain dict rather than a lock: CPython's GIL
# makes a single whole-dict reassignment atomic, and there is exactly one
# writer, so a torn read is not possible.
_readiness: dict = {"done": False}


def _warm_up_dependencies() -> None:
    """Import docling (and cv2 / pypdfium2) once, off the event loop, so
    `/health` never has to.

    `docling`'s own import chain pulls in torch and transformers and is
    genuinely slow -- commonly 15-60+ seconds on a cold process, more if a
    CUDA context has to initialise. This module's `/health` handler used to
    do `from . import convert as convert_mod` inline, which meant Python
    only actually ran `convert.py`'s top-level `import docling` the FIRST
    time any request reached that line -- almost always the caller's own
    health-check poll, arriving right after this process started. That
    request paid the whole import cost synchronously, blocking this
    process's single asyncio event loop for the entire duration (nothing
    else could be served either), and the caller's HTTP client gave up long
    before it finished: `HTTPConnectionPool(...): Read timed out
    (read timeout=10)`, reported as "not reachable" even though the process
    was up the whole time and only busy loading.

    Run in a background OS thread (via `run_in_executor`, see
    `_warm_up_dependencies_task` below) so the event loop stays free to
    serve `/health` immediately -- reporting `"done": false` honestly, in
    milliseconds, until this finishes -- rather than hanging.

    `/health` MUST NOT import `convert`/`cv2`/`pypdfium2` itself, even after
    this runs: Python's import lock is a real OS-level lock, not
    asyncio-aware, so a request arriving while THIS thread is mid-import
    would still block the event loop waiting for that lock -- exactly the
    same stall, just racier. Once this function finishes, `_readiness`
    already carries everything `/health` needs; nothing there triggers a
    fresh import.
    """
    try:
        from . import convert as convert_mod
        docling = convert_mod.DOCLING_AVAILABLE
    except Exception:
        logger.exception("docling import failed during startup warm-up")
        docling = False

    try:
        import cv2  # noqa: F401
        opencv = True
    except ImportError:
        opencv = False

    try:
        import pypdfium2  # noqa: F401
        pdfium = True
    except ImportError:
        pdfium = False

    _readiness.update(done=True, docling=docling, opencv=opencv, pypdfium=pdfium)
    logger.info(
        "dependency warm-up finished: docling=%s opencv=%s pypdfium2=%s",
        docling, opencv, pdfium,
    )


@app.on_event("startup")
async def _start_reaper() -> None:
    """Free finished jobs on a timer, not only when another upload arrives.

    Without this a service that converts a batch and then goes quiet holds
    every one of those results -- whole extracted documents, page images
    included -- until the next upload happens to call `_reap`.
    """
    REGISTRY.start_reaper()


@app.on_event("startup")
async def _warm_up_dependencies_task() -> None:
    """Kick off `_warm_up_dependencies` in a background thread and return
    immediately -- startup (and therefore the port opening) is not held up
    waiting for docling to import; only `/health` waits, and only until
    `_readiness["done"]` flips, which it can check without blocking."""
    asyncio.get_event_loop().run_in_executor(None, _warm_up_dependencies)


@app.get("/health")
async def health():
    """Readiness, reported per component.

    Names each optional dependency separately rather than answering a single
    boolean, because the failure modes are genuinely different: without docling
    nothing works at all, while without a reachable vision model the pipeline
    still runs and simply vouches for less. A caller that cannot tell those
    apart cannot explain either of them to a user.

    Reads `_readiness` only -- see `_warm_up_dependencies`'s docstring for why
    this must never import anything itself. While the background warm-up is
    still running, this returns instantly with `"available": false` and a
    `reason` that says so plainly, instead of hanging until the import
    finishes and risking the caller's own read-timeout.
    """
    if not _readiness.get("done"):
        return {
            "available": False,
            "docling": None,
            "opencv": None,
            "pypdfium2": None,
            "vlm_configured": Config.vlm_configured(),
            "vlm_model": Config.VLM_MODEL or None,
            "render_dpi": Config.RENDER_DPI,
            "reason": (
                "Still starting up: docling and its dependencies are loading in "
                "the background (this can take up to a minute on a cold start). "
                "Retry shortly -- the service is not down."
            ),
        }

    ready = _readiness["docling"] and _readiness["opencv"] and _readiness["pypdfium"]
    return {
        "available": ready,
        "docling": _readiness["docling"],
        "opencv": _readiness["opencv"],
        "pypdfium2": _readiness["pypdfium"],
        "vlm_configured": Config.vlm_configured(),
        "vlm_model": Config.VLM_MODEL or None,
        "render_dpi": Config.RENDER_DPI,
        "reason": None if ready else (
            "Install ingestion/requirements.txt: docling, opencv-python-headless "
            "and pypdfium2 are all required to convert a scanned PDF."
        ),
    }


@router.post("")
async def ingest(file: UploadFile = File(...)):
    """Accept one PDF and start converting it."""
    filename = (file.filename or "upload.pdf").strip()
    # Basename only. The gateway sanitises too, but this service must not depend
    # on that: a client-supplied name is never a path here, and nothing in this
    # process writes it to disk.
    filename = filename.replace("\\", "/").rsplit("/", 1)[-1] or "upload.pdf"

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if len(data) > Config.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"The file is {len(data) // (1024 * 1024)}MB; the limit is "
                f"{Config.MAX_UPLOAD_BYTES // (1024 * 1024)}MB."
            ),
        )
    if not data.startswith(b"%PDF"):
        raise HTTPException(
            status_code=415,
            detail="Only PDF files are accepted. This file is not a PDF.",
        )

    job = REGISTRY.create(filename)
    REGISTRY.start(job, data)
    return {"job_id": job.job_id, "filename": filename, "status": job.status}


@router.get("/{job_id}")
async def poll(job_id: str):
    job = REGISTRY.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such ingestion job.")
    return job.as_dict()


@router.get("/{job_id}/events")
async def events(job_id: str):
    """Stream progress as server-sent events, finishing with the result.

    The final ``result`` frame carries the whole extracted document, so a caller
    that watches the stream never needs a second request. Heartbeats keep the
    connection alive through the long convert stage: nginx sits in front of this
    with a proxy timeout, and a four-minute silence looks like a dead upstream.
    """
    job = REGISTRY.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such ingestion job.")

    async def stream():
        subscriber = job.subscribe()
        loop = asyncio.get_running_loop()
        try:
            while True:
                try:
                    event = await loop.run_in_executor(None, subscriber.get, True, 15.0)
                except Exception:
                    yield ": keep-alive\n\n"
                    continue
                if event is None:
                    break
                yield f"event: progress\ndata: {json.dumps(event.as_dict())}\n\n"

            if job.status == "done":
                yield f"event: result\ndata: {json.dumps(job.result)}\n\n"
            else:
                payload = {"error": job.error or "Ingestion failed."}
                yield f"event: error\ndata: {json.dumps(payload)}\n\n"
        finally:
            job.unsubscribe(subscriber)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx buffers proxied responses by default, which holds every
            # event until the stream closes and defeats the point entirely.
            "X-Accel-Buffering": "no",
        },
    )


app.include_router(router)
