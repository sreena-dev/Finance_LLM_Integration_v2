"""Routes for the Financial Diagnostic Report mode — `/api/financial-diagnostic-report/*`."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from app.errors import ModeNotIntegratedError, ModeUnavailableError
from app.schemas import QueryRequest, QueryResponse
from . import adapter

router = APIRouter(
    prefix="/api/financial-diagnostic-report", tags=["financial-diagnostic-report"]
)


@router.get("/health")
async def health():
    available, reason = adapter.status()
    return {"mode": adapter.MODE_ID, "available": available, "reason": reason}


@router.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    text = req.query.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    try:
        result = await asyncio.to_thread(adapter.run_query, text)
    except (ModeNotIntegratedError, ModeUnavailableError) as exc:
        raise exc.as_http() from exc

    return QueryResponse(**result)
