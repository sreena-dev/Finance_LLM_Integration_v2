"""Routes for the SAR Q&A chat mode — `/api/sar-chat/*`.

Uses the same entity/FY catalog as the report mode.
The user picks entity+FY in the frontend, then asks questions in a chat thread.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from app.errors import ModeUnavailableError
from app.schemas import SARChatRequest, SARChatResponse
from . import adapter

router = APIRouter(prefix="/api/sar-chat", tags=["sar-chat"])


@router.get("/health")
async def health():
    available, reason = adapter.status()
    return {"mode": adapter.MODE_ID, "available": available, "reason": reason}


@router.get("/catalog")
async def get_catalog():
    """Entities and financial years available — same catalog as the report mode."""
    try:
        entities = await asyncio.to_thread(adapter.catalog)
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc
    return {"mode": adapter.MODE_ID, "entities": entities}


@router.post("/ask", response_model=SARChatResponse)
async def ask(req: SARChatRequest):
    """
    Run one Q&A turn for the selected entity and financial year.

    The frontend supplies the company and fy_start from the catalog dropdown,
    so the user only needs to type the question.
    """
    try:
        result = await asyncio.to_thread(
            adapter.answer_question,
            req.query,
            req.company,
            req.fy_start,
            req.history,
        )
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc
    return SARChatResponse(**result)
