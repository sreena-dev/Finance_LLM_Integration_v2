"""Routes for the Financial Statement mode — `/api/financial-statement/*`."""

from __future__ import annotations

import asyncio
import hashlib
import logging

from fastapi import APIRouter, HTTPException

from app.errors import ModeUnavailableError
from app.schemas import QueryRequest, QueryResponse
from . import adapter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/financial-statement", tags=["financial-statement"])


@router.get("/health")
async def health():
    available, reason = adapter.status()
    return {"mode": adapter.MODE_ID, "available": available, "reason": reason}


@router.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    """Answer a financial-statement question via this branch's RAG pipeline."""
    text = req.query.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    # Log the query as `repr` plus a short digest, not as plain text. The
    # pipeline's tool-selection is sensitive to the exact characters it receives,
    # and the same question typed in the UI and sent by curl can take completely
    # different tool paths — one answering in 3 iterations, the other looping
    # until it hits MAX_TOOL_ITERATIONS. Without the escapes visible (stray
    # newlines, NBSP, en-dashes, smart quotes) and a digest to compare requests
    # by, there is no way to tell whether two runs were even given the same
    # input, and the difference is invisible in a rendered chat bubble.
    logger.info(
        "query received: sha1=%s len=%d %r",
        hashlib.sha1(text.encode()).hexdigest()[:12],
        len(text),
        text,
    )

    try:
        # The pipeline is blocking (psycopg2 + synchronous LLM tool-calling
        # rounds), so it must not run on the event loop.
        result = await asyncio.to_thread(adapter.run_query, text)
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc

    return QueryResponse(**result)
