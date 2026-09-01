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
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
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

from app.auth.deps import require_user  # noqa: E402
from app.auth.router import router as auth_router  # noqa: E402
from app.errors import InvalidRequestError, NotFoundError  # noqa: E402
from app.mode_loader import IMPORT_FAILURES, load_routers  # noqa: E402
from app.registry import MODES, describe  # noqa: E402

# Imported one at a time rather than with five module-scope `from ... import`
# statements. A mode router is not inert — Trial Balance's verifies its
# knowledge packs and creates a directory at import time, and pulls in a tree
# that imports python-docx. One missing dependency there used to abort the
# import of this module and stop uvicorn, taking down four modes that had
# nothing to do with it. See app/mode_loader.py.
_MODE_ROUTERS = (
    ("statutory-auditor-report", "modes.statutory_auditor_report.router"),
    ("sar-chat", "modes.sar_chat.router"),
    ("financial-statement", "modes.financial_statement.router"),
    ("trial-balance", "modes.trial_balance.router"),
    ("financial-diagnostic-report", "modes.financial_diagnostic_report.router"),
)

_loaded_routers = load_routers(_MODE_ROUTERS)

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


# Sign-up and sign-in are the only routes besides /api/health reachable without
# a token — a chicken-and-egg requirement rather than a policy choice.
app.include_router(auth_router)

# EVERY mode is authenticated by this one argument. Attaching the dependency
# here rather than inside each mode's router is what lets four working
# pipelines gain authentication without a single line changing in their own
# files — and it cannot accidentally cover /api/health, which is declared
# directly on `app` below and which the container healthcheck polls with no
# credentials (see backend/Dockerfile). Middleware was the alternative and was
# rejected: it would have to re-implement path matching plus an allowlist, and
# would also intercept /docs, /openapi.json and CORS preflight OPTIONS.
for _mode_id, _router in _loaded_routers:
    app.include_router(_router, dependencies=[Depends(require_user)])


def _register_unavailable(mode) -> None:
    """Answer 503 under a mode whose router could not be imported.

    Without this its endpoints would 404, and a 404 says "this route does not
    exist" — which reads as a client mistake and sends someone looking for a
    typo. The honest answer is 503 with the import error, which names the
    missing dependency. It matches the status the rest of this gateway already
    uses for an unavailable mode, and 503 is the one status that invites a retry.
    """
    reason = IMPORT_FAILURES[mode.id]
    # Behind the same auth dependency as a working mode. Otherwise this would be
    # the one route set in the gateway that answers an anonymous caller, and it
    # would answer with an exception string naming internal packages and paths.
    stub = APIRouter(prefix=mode.base_path, tags=[mode.id],
                     dependencies=[Depends(require_user)])

    @stub.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    async def _unavailable(path: str):  # noqa: ARG001 - path is the catch-all
        raise HTTPException(status_code=503, detail=reason)

    app.include_router(stub)


for _mode in MODES:
    if _mode.id in IMPORT_FAILURES:
        _register_unavailable(_mode)


@app.get("/api/modes", dependencies=[Depends(require_user)])
async def list_modes(probe: bool = False):
    """List the modes the UI should render.

    Availability probing is opt-in (`?probe=true`) because it imports every
    pipeline and touches the databases — far too slow for first paint. The UI
    lists modes immediately, then probes each one's `/health` in the background.
    """
    return {"modes": [describe(m, probe=probe) for m in MODES]}


# DELIBERATELY UNAUTHENTICATED. backend/Dockerfile's HEALTHCHECK calls this
# every 30 seconds with a plain urllib request that carries no token; requiring
# one here would mark the container unhealthy and restart-loop it forever. It
# probes nothing and opens no database connection, so it discloses only that the
# gateway is running and which modes are compiled in.
@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "modes": [m.id for m in MODES],
        "integrated": [m.id for m in MODES if m.integrated],
    }
