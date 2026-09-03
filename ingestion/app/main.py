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


@app.get("/health")
async def health():
    """Readiness, reported per component.

    Names each optional dependency separately rather than answering a single
    boolean, because the failure modes are genuinely different: without docling
    nothing works at all, while without a reachable vision model the pipeline
    still runs and simply vouches for less. A caller that cannot tell those
    apart cannot explain either of them to a user.
    """
    from . import convert as convert_mod

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

    ready = convert_mod.DOCLING_AVAILABLE and opencv and pdfium
    return {
        "available": ready,
        "docling": convert_mod.DOCLING_AVAILABLE,
        "opencv": opencv,
        "pypdfium2": pdfium,
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
