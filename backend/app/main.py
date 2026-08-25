"""Artha.AI — integrated gateway.

One FastAPI app fronting one pipeline per mode. Each mode owns a distinct URL
namespace (`/api/<mode-id>/...`) and its own adapter, so a request for one mode
can never be served by another's pipeline.

Run from the `backend/` directory:
    uvicorn app.main:app --reload --port 8080
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# `backend/` on sys.path so `app.*` and `modes.*` import cleanly regardless of
# the working directory uvicorn was launched from.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# Load the first .env that exists, most-specific first. Two locations because
# the project is deployed two ways: bare-metal/venv runs keep the file next to
# the code (`backend/.env`), while the Docker Compose stack keeps one file at
# the repo root and shares it with the frontend service. Neither existing is
# also fine — under Compose the values arrive as real environment variables via
# `env_file`, and `override=False` below is what stops a stale committed file
# from quietly winning against them.
for _candidate in (_BACKEND_DIR / ".env", _BACKEND_DIR.parent / ".env"):
    if _candidate.is_file():
        load_dotenv(_candidate, override=False)
        break

# Configure the root logger before importing any mode.
#
# Nothing did this previously, which meant the root logger sat at its WARNING
# default with no handler and every `logger.info(...)` this project writes was
# silently dropped. Only yukta's lines ever reached the console — it attaches a
# handler to its own logger — so container logs showed the vendored library's
# internals while the gateway's own view of a request was invisible. That is a
# bad place to be when diagnosing why one request behaved differently from
# another.
#
# `force=True` because an imported pipeline may have called basicConfig first
# (whoever calls it first normally wins, and a vendored module winning would put
# the level outside our control).
logging.basicConfig(
    level=os.environ.get("ARTHA_LOG_LEVEL", "info").upper(),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
    force=True,
)

# yukta attaches a handler to its own logger *and* leaves propagation on, so
# with a root handler now present every one of its lines would be emitted twice.
# Stop it at the yukta logger rather than removing its handler: its own
# formatting stays intact, and this does not reach inside a vendored dependency
# to mutate it.
logging.getLogger("yukta").propagate = False

# Registered before any mode is imported. OpenTelemetry's first TracerProvider
# wins, and each mode's own tracing setup backs off when one already exists — so
# doing it here is what makes all four modes report to the same Phoenix project
# instead of racing to configure it differently. See app/tracing.py.
from app.tracing import setup_tracing  # noqa: E402

setup_tracing()

from app.errors import InvalidRequestError, NotFoundError  # noqa: E402
from app.registry import MODES, describe  # noqa: E402
from modes.financial_diagnostic_report.router import router as fdr_router  # noqa: E402
from modes.financial_statement.router import router as fs_router  # noqa: E402
from modes.sar_chat.router import router as sar_chat_router  # noqa: E402
from modes.statutory_auditor_report.router import router as sar_router  # noqa: E402
from modes.trial_balance.router import router as tb_router  # noqa: E402

app = FastAPI(
    title="Artha.AI — Integrated Audit Platform",
    version="1.0.0",
    description=(
        "Gateway over the Fin_Audit_QA pipelines. One namespace per mode: "
        "financial statements, statutory auditor's report, trial balance and "
        "financial diagnostic report."
    ),
)

# In both supported deployments the browser talks to the gateway same-origin —
# Vite proxies /api in dev, nginx proxies it in the container — so CORS is
# normally not exercised at all and the safe default is to allow nothing.
# Set ARTHA_CORS_ALLOW_ORIGINS to a comma-separated origin list only when the
# UI is genuinely served from a different origin than the API.
#
# `allow_credentials` deliberately tracks whether real origins were configured:
# credentialed requests combined with a wildcard origin is rejected by every
# browser, so the previous `["*"]` + credentials pairing could never have worked
# for a cross-origin caller anyway.
_cors_origins = [
    origin.strip()
    for origin in os.environ.get("ARTHA_CORS_ALLOW_ORIGINS", "").split(",")
    if origin.strip()
]

if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials="*" not in _cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# Adapters raise domain errors; the HTTP status is decided once, here, so every
# mode answers the same way instead of each route re-deciding. The distinction
# that matters to a caller: a bad *reference* (unknown doc_id, expired token) is
# 404 and retrying is pointless, an incoherent *request* is 400, and only a
# genuinely unavailable mode is 503 — the one status that does invite a retry.
@app.exception_handler(NotFoundError)
async def _not_found_handler(_request: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(InvalidRequestError)
async def _invalid_request_handler(_request: Request, exc: InvalidRequestError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


for _router in (sar_router, sar_chat_router, fs_router, tb_router, fdr_router):
    app.include_router(_router)


@app.get("/api/modes")
async def list_modes(probe: bool = False):
    """List the modes the UI should render.

    Availability probing is opt-in (`?probe=true`) because it imports every
    pipeline and touches the databases — far too slow for first paint. The UI
    lists modes immediately, then probes each one's `/health` in the background.
    """
    return {"modes": [describe(m, probe=probe) for m in MODES]}


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "modes": [m.id for m in MODES],
        "integrated": [m.id for m in MODES if m.integrated],
    }
