"""Routes for the Statutory Auditor's Report mode — `/api/statutory-auditor-report/*`.

This mode is form-driven, not chat-driven: `/catalog` fills the entity and
financial-year dropdowns, and `/generate` runs the pipeline for the pair the
user picked.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from app.errors import ModeUnavailableError
from app.schemas import ReportRequest, ReportResponse
from . import adapter

router = APIRouter(
    prefix="/api/statutory-auditor-report", tags=["statutory-auditor-report"]
)


@router.get("/health")
async def health():
    available, reason = adapter.status()
    return {"mode": adapter.MODE_ID, "available": available, "reason": reason}


@router.get("/catalog")
async def get_catalog():
    """Entities and, nested under each, the financial years available for it."""
    try:
        entities = await asyncio.to_thread(adapter.catalog)
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc
    return {"mode": adapter.MODE_ID, "entities": entities}


@router.post("/generate", response_model=ReportResponse)
async def generate(req: ReportRequest):
    """Generate the statutory auditor's report for one entity + financial year."""
    try:
        result = await asyncio.to_thread(
            adapter.generate_report,
            req.entity,
            req.fy_start,
            req.fy_end,
            req.scope,
        )
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc

    return ReportResponse(**result)
