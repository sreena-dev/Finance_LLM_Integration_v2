"""Read-only, UNSCOPED queries for the super-admin dashboard.

These deliberately do not reuse `modes/financial_statement/conversations.py` or
Trial Balance's `get_messages`: those filter on the calling user's id, which is
exactly what makes them safe for ordinary use and useless here. Nothing in this
module is reachable except through `app.admin.router`, whose every route sits
behind `require_super_admin`.

Everything degrades independently. A missing optional table, or an unreachable
Trial Balance database, turns into `{"available": False, "reason": ...}` for
that one section -- it never turns into a 500 for the whole dashboard.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.auth import db as auth_db
from app.auth import schema

logger = logging.getLogger(__name__)

MAX_PAGE = 200


def _rows(sql: str, params: tuple | list | None = None) -> list[dict]:
    with auth_db.db_cursor() as cur:
        cur.execute(sql, params or ())
        return [dict(r) for r in cur.fetchall()]


def _one(sql: str, params: tuple | list | None = None) -> dict | None:
    rows = _rows(sql, params)
    return rows[0] if rows else None


def _exists(table: str) -> bool:
    row = _one("SELECT to_regclass(%s) IS NOT NULL AS ok", (f"public.{table}",))
    return bool(row and row["ok"])


def _safe(fn, *args, **kwargs) -> dict:
    """Run one dashboard section; never let it raise into the caller."""
    try:
        return {"available": True, **fn(*args, **kwargs)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("admin section %s failed: %s: %s",
                       getattr(fn, "__name__", "?"), type(exc).__name__, exc)
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}


def _valid_uuid(value: str) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


def _title(text: str | None) -> str:
    text = " ".join((text or "").split())
    return (text[:80] + "…") if len(text) > 80 else (text or "(no question)")


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def _tb_counts() -> dict[str, dict]:
    """Per-user Trial Balance chat counts, keyed by user_id text. Best effort:
    TB lives in a different database that may be down."""
    from modes.trial_balance.pipeline import db as tb_db

    with tb_db.db_cursor() as cur:
        cur.execute(
            "SELECT user_id::text AS user_id, "
            "count(DISTINCT conversation_id) AS conversations, "
            "count(*) AS messages, max(created_at) AS last_at "
            "FROM pipeline_chat_messages WHERE deleted_at IS NULL "
            "GROUP BY user_id"
        )
        return {r["user_id"]: dict(r) for r in cur.fetchall()}


def list_users() -> dict:
    admin_col = "u.is_super_admin" if schema.admin_column_ready() else "false"
    events = schema.events_ready() and _exists("artha_query_events")
    uploads = _exists("financial_statement_live_ingestion")

    ev_join = (
        "LEFT JOIN (SELECT user_id, count(*) AS queries, "
        " avg(elapsed_seconds) FILTER (WHERE status = 'ok') AS avg_elapsed, "
        " count(*) FILTER (WHERE status <> 'ok') AS errors, "
        " max(created_at) AS last_query_at "
        " FROM public.artha_query_events GROUP BY user_id) e ON e.user_id = u.user_id"
        if events else ""
    )
    ev_cols = (
        "coalesce(e.queries,0) AS queries, e.avg_elapsed, "
        "coalesce(e.errors,0) AS errors, e.last_query_at"
        if events else
        "0 AS queries, NULL::float AS avg_elapsed, 0 AS errors, NULL::timestamptz AS last_query_at"
    )
    up_join = (
        "LEFT JOIN (SELECT user_id, count(*) AS uploads "
        " FROM public.financial_statement_live_ingestion GROUP BY user_id) d "
        " ON d.user_id = u.user_id::text"
        if uploads else ""
    )
    up_col = "coalesce(d.uploads,0) AS uploads" if uploads else "0 AS uploads"

    rows = _rows(
        f"""
        SELECT u.user_id::text AS user_id, u.username, u.email, u.display_name,
               u.created_at, u.last_login_at, {admin_col} AS is_super_admin,
               coalesce(m.conversations,0) AS fs_conversations,
               coalesce(m.messages,0) AS fs_messages, m.last_message_at,
               {ev_cols}, {up_col}
        FROM public.artha_users u
        LEFT JOIN (SELECT user_id, count(DISTINCT conversation_id) AS conversations,
                          count(*) AS messages, max(created_at) AS last_message_at
                   FROM public.artha_fs_messages GROUP BY user_id) m
               ON m.user_id = u.user_id
        {ev_join}
        {up_join}
        ORDER BY greatest(u.last_login_at, m.last_message_at, u.created_at) DESC NULLS LAST
        """
    )

    tb_by_user: dict[str, dict] = {}
    tb_ok, tb_reason = True, None
    try:
        tb_by_user = _tb_counts()
    except Exception as exc:  # noqa: BLE001 - TB is a separate database
        tb_ok, tb_reason = False, f"{type(exc).__name__}: {exc}"
        logger.warning("admin: Trial Balance counts unavailable: %s", exc)

    for r in rows:
        t = tb_by_user.get(r["user_id"], {})
        r["tb_conversations"] = t.get("conversations", 0)
        r["tb_messages"] = t.get("messages", 0)
        r["tb_last_at"] = t.get("last_at")
    return {
        "users": rows,
        "tb": {"available": tb_ok, "reason": tb_reason},
        "telemetry_available": events,
    }


def get_user(user_id: str) -> dict | None:
    uid = _valid_uuid(user_id)
    if not uid:
        return None
    listing = list_users()
    for r in listing["users"]:
        if r["user_id"] == uid:
            return {"user": r, "tb": listing["tb"],
                    "telemetry_available": listing["telemetry_available"]}
    return None


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

def list_conversations(user_id: str, mode: str) -> dict:
    uid = _valid_uuid(user_id)
    if not uid:
        return {"conversations": []}

    if mode == "fs":
        rows = _rows(
            """
            SELECT conversation_id::text AS conversation_id,
                   min(created_at) AS started_at, max(created_at) AS last_at,
                   count(*) AS n_messages,
                   (array_agg(content ORDER BY seq) FILTER (WHERE role = 'user'))[1] AS title_src
            FROM public.artha_fs_messages
            WHERE user_id = %s
            GROUP BY conversation_id
            ORDER BY max(created_at) DESC
            LIMIT %s
            """,
            (uid, MAX_PAGE),
        )
    elif mode == "tb":
        from modes.trial_balance.pipeline import db as tb_db

        with tb_db.db_cursor() as cur:
            cur.execute(
                """
                SELECT conversation_id::text AS conversation_id,
                       min(created_at) AS started_at, max(created_at) AS last_at,
                       count(*) AS n_messages,
                       (array_agg(content ORDER BY seq) FILTER (WHERE role = 'user'))[1] AS title_src
                FROM pipeline_chat_messages
                WHERE user_id::text = %s AND deleted_at IS NULL
                GROUP BY conversation_id
                ORDER BY max(created_at) DESC
                LIMIT %s
                """,
                (uid, MAX_PAGE),
            )
            rows = [dict(r) for r in cur.fetchall()]
    else:
        return {"conversations": []}

    for r in rows:
        r["title"] = _title(r.pop("title_src", None))
    return {"conversations": rows}


def get_conversation(mode: str, conversation_id: str) -> dict | None:
    cid = _valid_uuid(conversation_id)
    if not cid:
        return None

    if mode == "fs":
        msgs = _rows(
            """
            SELECT m.seq, m.role, m.content, m.payload, m.rewritten_query,
                   m.created_at, m.user_id::text AS user_id, u.username
            FROM public.artha_fs_messages m
            LEFT JOIN public.artha_users u ON u.user_id = m.user_id
            WHERE m.conversation_id = %s ORDER BY m.seq
            """,
            (cid,),
        )
    elif mode == "tb":
        from modes.trial_balance.pipeline import db as tb_db

        with tb_db.db_cursor() as cur:
            cur.execute(
                "SELECT seq, role, content, payload, tb_doc_id, created_at, "
                "user_id::text AS user_id FROM pipeline_chat_messages "
                "WHERE conversation_id::text = %s AND deleted_at IS NULL ORDER BY seq",
                (cid,),
            )
            msgs = [dict(r) for r in cur.fetchall()]
        if msgs:
            owner = _one("SELECT username FROM public.artha_users WHERE user_id::text = %s",
                         (msgs[0]["user_id"],))
            for m in msgs:
                m["username"] = owner["username"] if owner else None
    else:
        return None

    if not msgs:
        return None
    return {
        "conversation_id": cid,
        "mode": mode,
        "user_id": msgs[0]["user_id"],
        "username": msgs[0].get("username"),
        "messages": msgs,
    }


# ---------------------------------------------------------------------------
# Query-event log
# ---------------------------------------------------------------------------

def list_events(user_id: str | None = None, status: str | None = None,
                limit: int = 100, offset: int = 0) -> dict:
    if not (schema.events_ready() and _exists("artha_query_events")):
        return {"events": [], "available": False,
                "reason": "Query telemetry is not enabled on this database."}
    limit = max(1, min(int(limit), MAX_PAGE))
    offset = max(0, int(offset))

    where, params = [], []
    if user_id:
        uid = _valid_uuid(user_id)
        if not uid:
            return {"events": [], "available": True}
        where.append("e.user_id = %s")
        params.append(uid)
    if status in ("ok", "error", "unavailable"):
        where.append("e.status = %s")
        params.append(status)
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    rows = _rows(
        f"""
        SELECT e.event_id::text AS event_id, e.user_id::text AS user_id, u.username,
               e.conversation_id::text AS conversation_id, e.mode, e.created_at,
               e.query_text, e.status, e.elapsed_seconds, e.prompt_tokens,
               e.completion_tokens, e.tool_calls, e.tools_used, e.confidence,
               e.unsourced, e.has_upload, e.num_chunks_retrieved, e.rewritten,
               e.error_stage, e.error_message
        FROM public.artha_query_events e
        LEFT JOIN public.artha_users u ON u.user_id = e.user_id
        {clause}
        ORDER BY e.created_at DESC LIMIT %s OFFSET %s
        """,
        params + [limit, offset],
    )
    return {"events": rows, "available": True}


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def audit(admin_user_id: str, action: str, target_user_id: str | None = None,
          detail: str | None = None) -> None:
    """Record that an admin looked at something. Never raises: an audit-table
    problem must not stop the admin working, but is logged loudly."""
    try:
        if not schema.audit_ready():
            return
        with auth_db.db_cursor(dict_rows=False) as cur:
            cur.execute(
                "INSERT INTO public.artha_admin_audit "
                "(audit_id, admin_user_id, action, target_user_id, detail) "
                "VALUES (%s, %s, %s, %s, %s)",
                (str(uuid.uuid4()), admin_user_id, action,
                 _valid_uuid(target_user_id) if target_user_id else None,
                 (detail or "")[:500] or None),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not write the admin audit row: %s", exc)


def list_audit(limit: int = 100) -> list[dict]:
    if not (schema.audit_ready() and _exists("artha_admin_audit")):
        return []
    return _rows(
        """
        SELECT a.at, a.action, a.detail, a.admin_user_id::text AS admin_user_id,
               adm.username AS admin_username, a.target_user_id::text AS target_user_id,
               tgt.username AS target_username
        FROM public.artha_admin_audit a
        LEFT JOIN public.artha_users adm ON adm.user_id = a.admin_user_id
        LEFT JOIN public.artha_users tgt ON tgt.user_id = a.target_user_id
        ORDER BY a.at DESC LIMIT %s
        """,
        (max(1, min(int(limit), MAX_PAGE)),),
    )
