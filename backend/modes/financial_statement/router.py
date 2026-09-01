"""Routes for the Financial Statement mode — `/api/financial-statement/*`.

Authentication is attached to this router as a whole in `app/main.py`, not
declared per route. The `Depends(require_user)` below are how a handler gets
hold of *which* user is calling, which it needs because chat history is scoped
to them.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.auth.db import AuthDBError
from app.auth.deps import CurrentUser, require_user
from app.auth.schema import ensure_schema
from app.errors import ModeUnavailableError
from app.schemas import QueryRequest, QueryResponse
from . import adapter
from . import conversations as convo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/financial-statement", tags=["financial-statement"])


@router.get("/health")
async def health():
    available, reason = await asyncio.to_thread(adapter.status)
    return {"mode": adapter.MODE_ID, "available": available, "reason": reason}


def _history_unavailable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail=f"Chat history is unavailable: {exc}",
    )


@router.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest, user: CurrentUser = Depends(require_user)):
    """Answer a financial-statement question, in the context of its conversation."""
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
        "query received: user=%s sha1=%s len=%d %r",
        user.username,
        hashlib.sha1(text.encode()).hexdigest()[:12],
        len(text),
        text,
    )

    # History is read from the database, never taken from the request. The
    # client cannot rewrite its own past, and a conversation resumes correctly
    # after a refresh or on a different machine.
    history: list[dict] = []
    conversation_id = (req.conversation_id or "").strip()
    try:
        await asyncio.to_thread(ensure_schema)
        if conversation_id:
            history = await asyncio.to_thread(
                convo.history_for_rewriter, user.user_id, conversation_id
            )
            if not history:
                # Either it does not exist or it is someone else's. Same answer
                # for both — confirming which would leak that the id is real.
                raise HTTPException(status_code=404,
                                    detail="No such conversation.")
        else:
            conversation_id = convo.new_conversation_id()
    except AuthDBError as exc:
        raise _history_unavailable(exc) from exc

    try:
        # The pipeline is blocking (psycopg2 + synchronous LLM tool-calling
        # rounds), so it must not run on the event loop.
        result = await asyncio.to_thread(adapter.run_query, text, history=history)
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc

    result["conversation_id"] = conversation_id

    # Persisted only after a successful run: a failed turn would otherwise leave
    # an orphan question in the thread, and the UI already reports the failure.
    try:
        await asyncio.to_thread(
            convo.append_turns,
            user.user_id, conversation_id, text,
            result.get("final_answer") or result.get("summary") or "",
            result, result.get("rewritten_query") or "",
        )
    except AuthDBError as exc:
        # The answer is good; only the record of it failed. Returning it is far
        # better than discarding a completed run over a storage problem.
        logger.error("Could not save the conversation turn: %s", exc)

    return QueryResponse(**result)


# ---------------------------------------------------------------------------
# Conversations
#
# Every one of these is scoped to the authenticated user inside the SQL, not
# checked afterwards — see modes/financial_statement/conversations.py.
# ---------------------------------------------------------------------------

@router.get("/conversations")
async def list_conversations(user: CurrentUser = Depends(require_user)):
    """This user's Financial Statements conversations, most recent first."""
    try:
        await asyncio.to_thread(ensure_schema)
        rows = await asyncio.to_thread(convo.list_conversations, user.user_id)
    except AuthDBError as exc:
        raise _history_unavailable(exc) from exc
    return {"conversations": rows}


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str,
                           user: CurrentUser = Depends(require_user)):
    """Every turn of one conversation, for rehydrating the thread.

    Assistant turns carry the stored `payload` — the full original response — so
    a reopened conversation renders with its evidence and citations intact rather
    than as bare text.
    """
    try:
        await asyncio.to_thread(ensure_schema)
        messages = await asyncio.to_thread(
            convo.get_messages, user.user_id, conversation_id
        )
    except AuthDBError as exc:
        raise _history_unavailable(exc) from exc

    if not messages:
        raise HTTPException(status_code=404, detail="No such conversation.")
    return {"conversation_id": conversation_id, "messages": messages}


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str,
                              user: CurrentUser = Depends(require_user)):
    try:
        await asyncio.to_thread(ensure_schema)
        removed = await asyncio.to_thread(
            convo.delete_conversation, user.user_id, conversation_id
        )
    except AuthDBError as exc:
        raise _history_unavailable(exc) from exc

    if not removed:
        raise HTTPException(status_code=404, detail="No such conversation.")
    return None
