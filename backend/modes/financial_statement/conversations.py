"""Persisted Financial Statements chat history, scoped to one user.

Every statement here reads or writes `artha_fs_messages` and nothing else.

WHY A CONVERSATION IS NOT A ROW
-------------------------------
There is no conversations table. A conversation is a `GROUP BY conversation_id`
over the messages, and its title is derived from its first user turn. Two
reasons: it keeps this change to the two tables it was scoped to, and it removes
a parent row whose `updated_at` can silently drift out of step with its
children — a class of bug that only shows up as a mis-sorted sidebar weeks later.

WHY EVERY QUERY FILTERS ON user_id
-----------------------------------
`user_id` is in the WHERE clause of every read and every delete, never checked
afterwards in Python. A conversation id is a uuid and unguessable, but "hard to
guess" is not an access control; the filter is. One user cannot read or delete
another's history even with the id in hand.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.auth.db import db_cursor

# How many turns the rewriter is shown. Matches the window SAR's chat pipeline
# uses (`SARChatPipeline.format_history`), which is the pattern this mirrors.
HISTORY_TURNS = 8

# A title is the first user turn, trimmed. Enough to recognise a thread in a
# narrow sidebar without truncating mid-word more often than necessary.
_TITLE_CHARS = 70


def new_conversation_id() -> str:
    return str(uuid.uuid4())


def _title_from(text: str) -> str:
    text = " ".join((text or "").split())
    if len(text) <= _TITLE_CHARS:
        return text or "New conversation"
    return text[:_TITLE_CHARS].rsplit(" ", 1)[0] + "…"


def list_conversations(user_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """This user's conversations, most recently active first."""
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT conversation_id,
                   min(created_at) AS started_at,
                   max(created_at) AS last_at,
                   count(*)        AS n_messages,
                   (array_agg(content ORDER BY seq)
                        FILTER (WHERE role = 'user'))[1] AS title_src
              FROM public.artha_fs_messages
             WHERE user_id = %s
             GROUP BY conversation_id
             ORDER BY max(created_at) DESC
             LIMIT %s
            """,
            (str(user_id), int(limit)),
        )
        return [
            {
                "conversation_id": str(r["conversation_id"]),
                "title": _title_from(r["title_src"] or ""),
                "started_at": r["started_at"].isoformat() if r["started_at"] else None,
                "last_at": r["last_at"].isoformat() if r["last_at"] else None,
                "n_messages": r["n_messages"],
            }
            for r in cur.fetchall()
        ]


def get_messages(user_id: str, conversation_id: str) -> list[dict[str, Any]]:
    """Every turn in one conversation, oldest first.

    Returns [] both for "no such conversation" and "belongs to someone else" —
    the caller turns that into a 404 without confirming the id exists.
    """
    with db_cursor() as cur:
        try:
            cur.execute(
                """
                SELECT seq, role, content, payload, rewritten_query, created_at
                  FROM public.artha_fs_messages
                 WHERE user_id = %s AND conversation_id = %s
                 ORDER BY seq
                """,
                (str(user_id), str(conversation_id)),
            )
        except Exception:
            # A malformed uuid is a bad reference, not a server fault.
            return []
        return [
            {
                "seq": r["seq"],
                "role": r["role"],
                "content": r["content"],
                "payload": r["payload"],
                "rewritten_query": r["rewritten_query"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in cur.fetchall()
        ]


def history_for_rewriter(user_id: str, conversation_id: str,
                         turns: int = HISTORY_TURNS) -> list[dict[str, str]]:
    """The last `turns` messages as {"role", "content"} — the rewriter's input.

    Assistant turns contribute their prose, not their evidence tables: the
    rewriter needs enough to resolve "it" and "that year", and feeding it whole
    cited answers would spend most of the context window restating figures it is
    not being asked about.
    """
    rows = get_messages(user_id, conversation_id)
    return [{"role": r["role"], "content": r["content"]} for r in rows[-turns:]]


def append_turns(user_id: str, conversation_id: str, question: str,
                 answer_text: str, payload: dict[str, Any],
                 rewritten_query: str = "") -> None:
    """Record one exchange — the question and its answer — in a single transaction.

    Called only AFTER the pipeline has answered. Writing the user turn up front
    would leave an orphan question in the thread whenever a run fails, and the
    UI already shows that failure inline; persisting it would make every
    transient outage permanently visible in the history.

    `seq` is computed inside the same statement as the insert, so two concurrent
    turns cannot both claim the same number — the unique index on
    (conversation_id, seq) is what makes that a guarantee rather than a hope.
    """
    with db_cursor() as cur:
        cur.execute(
            "SELECT coalesce(max(seq), 0) AS n FROM public.artha_fs_messages "
            "WHERE conversation_id = %s",
            (str(conversation_id),),
        )
        base = (cur.fetchone() or {"n": 0})["n"] or 0

        cur.execute(
            """
            INSERT INTO public.artha_fs_messages
                (message_id, conversation_id, user_id, seq, role, content,
                 payload, rewritten_query)
            VALUES (%s, %s, %s, %s, 'user',      %s, NULL, %s),
                   (%s, %s, %s, %s, 'assistant', %s, %s,  NULL)
            """,
            (
                str(uuid.uuid4()), str(conversation_id), str(user_id), base + 1,
                question, rewritten_query or None,
                str(uuid.uuid4()), str(conversation_id), str(user_id), base + 2,
                answer_text, json.dumps(payload or {}),
            ),
        )


def delete_conversation(user_id: str, conversation_id: str) -> int:
    """Remove one conversation. Only ever deletes from `artha_fs_messages`."""
    with db_cursor() as cur:
        try:
            cur.execute(
                "DELETE FROM public.artha_fs_messages "
                "WHERE user_id = %s AND conversation_id = %s",
                (str(user_id), str(conversation_id)),
            )
        except Exception:
            return 0
        return cur.rowcount or 0
