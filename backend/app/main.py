"""Artha.AI — integrated gateway.

One FastAPI app fronting one pipeline per mode. Each mode owns a distinct URL
namespace (`/api/<mode-id>/...`) and its own adapter, so a request for one mode
can never be served by another's pipeline.

Run from the `backend/` directory:
    uvicorn app.main:app --reload --port 8080
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# `backend/` on sys.path so `app.*` and `modes.*` import cleanly regardless of
# the working directory uvicorn was launched from.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

load_dotenv(_BACKEND_DIR / ".env")

from app.registry import MODES, describe  # noqa: E402
from modes.financial_diagnostic_report.router import router as fdr_router  # noqa: E402
from modes.financial_statement.router import router as fs_router  # noqa: E402
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for _router in (sar_router, fs_router, tb_router, fdr_router):
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
