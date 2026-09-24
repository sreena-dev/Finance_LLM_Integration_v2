"""Aggregates for the dashboard's "how do we optimise this" view.

Each section is computed independently and wrapped by `queries._safe`, so a
missing table or a bad row in one never blanks the rest.

WHAT IS MEASURED VS INFERRED
----------------------------
Latency, tokens, tool calls, confidence and errors are MEASURED: they come from
`artha_query_events`, written at query time. Two signals are HEURISTICS and are
labelled as such in the response:
  * "no data found" answers -- a phrase match on the stored answer text, because
    the pipeline does not flag a retrieval miss as a field;
  * "never-used tools" -- tools seen at any time in the events table but not in
    the window. The full tool registry is not consulted, so a tool that has
    NEVER been called since telemetry started cannot appear here.
"""

from __future__ import annotations

from app.auth import schema

from .queries import _exists, _rows, _safe

_MISS_PHRASES = (
    "%no annual report found%",
    "%not located in the%",
    "%could not be located%",
    "%no matching%",
    "%not available in the ingested%",
)


def _days(days: int) -> int:
    return max(1, min(int(days), 365))


def _usage(days: int) -> dict:
    daily = _rows(
        """
        SELECT date_trunc('day', created_at)::date AS day,
               count(*) AS queries, count(DISTINCT user_id) AS users
        FROM public.artha_query_events
        WHERE created_at >= now() - (%s || ' days')::interval
        GROUP BY 1 ORDER BY 1
        """,
        (str(days),),
    )
    top = _rows(
        """
        SELECT u.username, count(*) AS queries,
               avg(e.elapsed_seconds) FILTER (WHERE e.status = 'ok') AS avg_elapsed
        FROM public.artha_query_events e
        LEFT JOIN public.artha_users u ON u.user_id = e.user_id
        WHERE e.created_at >= now() - (%s || ' days')::interval
        GROUP BY u.username ORDER BY count(*) DESC LIMIT 10
        """,
        (str(days),),
    )
    modes = _rows(
        "SELECT mode, count(*) AS queries FROM public.artha_query_events "
        "WHERE created_at >= now() - (%s || ' days')::interval GROUP BY mode "
        "ORDER BY 2 DESC",
        (str(days),),
    )
    totals = _rows(
        "SELECT count(*) AS queries, count(DISTINCT user_id) AS active_users "
        "FROM public.artha_query_events "
        "WHERE created_at >= now() - (%s || ' days')::interval",
        (str(days),),
    )[0]
    return {"daily": daily, "top_users": top, "modes": modes, **totals}


def _performance(days: int) -> dict:
    pct = _rows(
        """
        SELECT count(*) AS n,
               percentile_cont(0.5)  WITHIN GROUP (ORDER BY elapsed_seconds) AS p50,
               percentile_cont(0.9)  WITHIN GROUP (ORDER BY elapsed_seconds) AS p90,
               percentile_cont(0.99) WITHIN GROUP (ORDER BY elapsed_seconds) AS p99,
               avg(elapsed_seconds) AS mean
        FROM public.artha_query_events
        WHERE status = 'ok' AND elapsed_seconds IS NOT NULL
          AND created_at >= now() - (%s || ' days')::interval
        """,
        (str(days),),
    )[0]
    daily = _rows(
        """
        SELECT date_trunc('day', created_at)::date AS day,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY elapsed_seconds) AS p50,
               percentile_cont(0.9) WITHIN GROUP (ORDER BY elapsed_seconds) AS p90
        FROM public.artha_query_events
        WHERE status = 'ok' AND elapsed_seconds IS NOT NULL
          AND created_at >= now() - (%s || ' days')::interval
        GROUP BY 1 ORDER BY 1
        """,
        (str(days),),
    )
    slowest = _rows(
        """
        SELECT e.event_id::text AS event_id, e.created_at, e.query_text,
               e.elapsed_seconds, e.tool_calls, e.tools_used, e.prompt_tokens,
               e.conversation_id::text AS conversation_id, u.username,
               e.user_id::text AS user_id
        FROM public.artha_query_events e
        LEFT JOIN public.artha_users u ON u.user_id = e.user_id
        WHERE e.status = 'ok' AND e.elapsed_seconds IS NOT NULL
          AND e.created_at >= now() - (%s || ' days')::interval
        ORDER BY e.elapsed_seconds DESC LIMIT 10
        """,
        (str(days),),
    )
    by_tool = _rows(
        """
        SELECT t.tool, count(*) AS calls, avg(e.elapsed_seconds) AS avg_elapsed
        FROM public.artha_query_events e,
             LATERAL jsonb_array_elements_text(coalesce(e.tools_used, '[]'::jsonb)) AS t(tool)
        WHERE e.status = 'ok' AND e.elapsed_seconds IS NOT NULL
          AND e.created_at >= now() - (%s || ' days')::interval
        GROUP BY t.tool HAVING count(*) >= 2
        ORDER BY avg(e.elapsed_seconds) DESC LIMIT 15
        """,
        (str(days),),
    )
    return {"percentiles": pct, "daily": daily, "slowest": slowest,
            "latency_by_tool": by_tool}


def _cost(days: int) -> dict:
    daily = _rows(
        """
        SELECT date_trunc('day', created_at)::date AS day,
               coalesce(sum(prompt_tokens), 0) AS prompt_tokens,
               coalesce(sum(completion_tokens), 0) AS completion_tokens
        FROM public.artha_query_events
        WHERE created_at >= now() - (%s || ' days')::interval
        GROUP BY 1 ORDER BY 1
        """,
        (str(days),),
    )
    avg = _rows(
        "SELECT avg(prompt_tokens) AS avg_prompt, avg(completion_tokens) AS avg_completion "
        "FROM public.artha_query_events WHERE status = 'ok' "
        "AND created_at >= now() - (%s || ' days')::interval",
        (str(days),),
    )[0]
    top_users = _rows(
        """
        SELECT u.username,
               coalesce(sum(e.prompt_tokens), 0) + coalesce(sum(e.completion_tokens), 0) AS tokens,
               count(*) AS queries
        FROM public.artha_query_events e
        LEFT JOIN public.artha_users u ON u.user_id = e.user_id
        WHERE e.created_at >= now() - (%s || ' days')::interval
        GROUP BY u.username ORDER BY tokens DESC LIMIT 10
        """,
        (str(days),),
    )
    heaviest = _rows(
        """
        SELECT e.event_id::text AS event_id, e.created_at, e.query_text, e.prompt_tokens,
               e.completion_tokens, e.tool_calls, u.username,
               e.conversation_id::text AS conversation_id, e.user_id::text AS user_id
        FROM public.artha_query_events e
        LEFT JOIN public.artha_users u ON u.user_id = e.user_id
        WHERE e.prompt_tokens IS NOT NULL
          AND e.created_at >= now() - (%s || ' days')::interval
        ORDER BY e.prompt_tokens DESC LIMIT 10
        """,
        (str(days),),
    )
    return {"daily": daily, "averages": avg, "top_users": top_users,
            "heaviest_queries": heaviest}


def _reliability(days: int) -> dict:
    status = _rows(
        "SELECT status, count(*) AS n FROM public.artha_query_events "
        "WHERE created_at >= now() - (%s || ' days')::interval GROUP BY status",
        (str(days),),
    )
    by_stage = _rows(
        "SELECT coalesce(error_stage, 'unknown') AS stage, status, count(*) AS n "
        "FROM public.artha_query_events WHERE status <> 'ok' "
        "AND created_at >= now() - (%s || ' days')::interval "
        "GROUP BY 1, 2 ORDER BY 3 DESC",
        (str(days),),
    )
    recent = _rows(
        """
        SELECT e.event_id::text AS event_id, e.created_at, e.query_text, e.status,
               e.error_stage, e.error_message, u.username, e.user_id::text AS user_id
        FROM public.artha_query_events e
        LEFT JOIN public.artha_users u ON u.user_id = e.user_id
        WHERE e.status <> 'ok' AND e.created_at >= now() - (%s || ' days')::interval
        ORDER BY e.created_at DESC LIMIT 15
        """,
        (str(days),),
    )
    return {"status": status, "by_stage": by_stage, "recent_failures": recent}


def _quality(days: int) -> dict:
    conf = _rows(
        "SELECT coalesce(confidence, 'unrated') AS confidence, count(*) AS n "
        "FROM public.artha_query_events WHERE status = 'ok' "
        "AND created_at >= now() - (%s || ' days')::interval "
        "GROUP BY 1 ORDER BY 2 DESC",
        (str(days),),
    )
    unsourced = _rows(
        "SELECT count(*) FILTER (WHERE unsourced) AS unsourced, count(*) AS total "
        "FROM public.artha_query_events WHERE status = 'ok' "
        "AND created_at >= now() - (%s || ' days')::interval",
        (str(days),),
    )[0]
    unsourced_recent = _rows(
        """
        SELECT e.event_id::text AS event_id, e.created_at, e.query_text, u.username,
               e.conversation_id::text AS conversation_id, e.user_id::text AS user_id
        FROM public.artha_query_events e
        LEFT JOIN public.artha_users u ON u.user_id = e.user_id
        WHERE e.status = 'ok' AND e.unsourced
          AND e.created_at >= now() - (%s || ' days')::interval
        ORDER BY e.created_at DESC LIMIT 10
        """,
        (str(days),),
    )
    out = {"confidence": conf, "unsourced": unsourced,
           "unsourced_recent": unsourced_recent}

    # Reasons a confidence was lowered live only in the stored answer payload,
    # not in the event row.
    out["lowered_reasons"] = _rows(
        """
        SELECT payload->'checks'->>'reduced_reason' AS reason, count(*) AS n
        FROM public.artha_fs_messages
        WHERE role = 'assistant' AND payload->'checks'->>'reduced_from' IS NOT NULL
          AND created_at >= now() - (%s || ' days')::interval
        GROUP BY 1 ORDER BY 2 DESC LIMIT 10
        """,
        (str(days),),
    )
    # HEURISTIC: a phrase match, because a retrieval miss is not a stored field.
    like = " OR ".join(["m.content ILIKE %s"] * len(_MISS_PHRASES))
    out["no_data_answers"] = _rows(
        f"""
        SELECT m.conversation_id::text AS conversation_id, m.created_at,
               u.username, m.user_id::text AS user_id,
               (SELECT p.content FROM public.artha_fs_messages p
                 WHERE p.conversation_id = m.conversation_id AND p.seq = m.seq - 1) AS question
        FROM public.artha_fs_messages m
        LEFT JOIN public.artha_users u ON u.user_id = m.user_id
        WHERE m.role = 'assistant' AND ({like})
          AND m.created_at >= now() - (%s || ' days')::interval
        ORDER BY m.created_at DESC LIMIT 15
        """,
        list(_MISS_PHRASES) + [str(days)],
    )
    out["no_data_is_heuristic"] = True
    return out


def _tools(days: int) -> dict:
    used = _rows(
        """
        SELECT t.tool, count(*) AS calls
        FROM public.artha_query_events e,
             LATERAL jsonb_array_elements_text(coalesce(e.tools_used, '[]'::jsonb)) AS t(tool)
        WHERE e.created_at >= now() - (%s || ' days')::interval
        GROUP BY t.tool ORDER BY 2 DESC
        """,
        (str(days),),
    )
    ever = _rows(
        """
        SELECT DISTINCT t.tool
        FROM public.artha_query_events e,
             LATERAL jsonb_array_elements_text(coalesce(e.tools_used, '[]'::jsonb)) AS t(tool)
        """
    )
    used_names = {r["tool"] for r in used}
    unused = sorted(r["tool"] for r in ever if r["tool"] not in used_names)
    return {"used": used, "unused_in_window": unused,
            "unused_is_heuristic": True}


def _ingestion(days: int) -> dict:
    if not _exists("financial_statement_live_ingestion"):
        return {"documents": 0, "by_grade": [], "note": "no uploads yet"}
    unread = ("CASE WHEN jsonb_typeof(quality->'unreadable_cells') = 'array' "
              "THEN jsonb_array_length(quality->'unreadable_cells') ELSE 0 END")
    recov = ("CASE WHEN jsonb_typeof(quality->'recovered_cells') = 'array' "
             "THEN jsonb_array_length(quality->'recovered_cells') ELSE 0 END")
    foot = ("CASE WHEN jsonb_typeof(quality->'failed_footings') = 'array' "
            "THEN jsonb_array_length(quality->'failed_footings') ELSE 0 END")
    edits = ("CASE WHEN jsonb_typeof(quality->'user_edits') = 'array' "
             "THEN jsonb_array_length(quality->'user_edits') ELSE 0 END")
    window = "uploaded_at >= now() - (%s || ' days')::interval"
    totals = _rows(
        f"""
        SELECT count(*) AS documents,
               avg({unread}) AS avg_unreadable, avg({recov}) AS avg_recovered,
               avg({foot}) AS avg_failed_footings,
               count(*) FILTER (WHERE {edits} > 0) AS hand_edited,
               count(*) FILTER (WHERE {unread} > 0) AS with_unreadable
        FROM public.financial_statement_live_ingestion WHERE {window}
        """,
        (str(days),),
    )[0]
    by_grade = _rows(
        f"SELECT coalesce(quality->>'grade', 'unknown') AS grade, count(*) AS n "
        f"FROM public.financial_statement_live_ingestion WHERE {window} "
        "GROUP BY 1 ORDER BY 2 DESC",
        (str(days),),
    )
    by_version = _rows(
        f"SELECT coalesce(ingest_version, 'unknown') AS version, count(*) AS n, "
        f"avg({unread}) AS avg_unreadable "
        f"FROM public.financial_statement_live_ingestion WHERE {window} "
        "GROUP BY 1 ORDER BY 2 DESC",
        (str(days),),
    )
    worst = _rows(
        f"SELECT filename, company, financial_year, {unread} AS unreadable, "
        f"{foot} AS failed_footings, quality->>'grade' AS grade, user_id "
        f"FROM public.financial_statement_live_ingestion WHERE {window} "
        f"ORDER BY {unread} DESC LIMIT 10",
        (str(days),),
    )
    daily = _rows(
        f"SELECT date_trunc('day', uploaded_at)::date AS day, count(*) AS uploads "
        f"FROM public.financial_statement_live_ingestion WHERE {window} "
        "GROUP BY 1 ORDER BY 1",
        (str(days),),
    )
    return {**totals, "by_grade": by_grade, "by_version": by_version,
            "worst_documents": worst, "daily": daily}


def _repeats(days: int) -> dict:
    rows = _rows(
        """
        SELECT lower(regexp_replace(trim(query_text), '\\s+', ' ', 'g')) AS question,
               count(*) AS asked, count(DISTINCT user_id) AS users,
               avg(elapsed_seconds) FILTER (WHERE status = 'ok') AS avg_elapsed
        FROM public.artha_query_events
        WHERE query_text IS NOT NULL
          AND created_at >= now() - (%s || ' days')::interval
        GROUP BY 1 HAVING count(*) > 1
        ORDER BY count(*) DESC LIMIT 10
        """,
        (str(days),),
    )
    return {"questions": rows}


def build(days: int = 30) -> dict:
    """Every section, each degrading independently."""
    days = _days(days)
    events_ok = schema.events_ready() and _exists("artha_query_events")
    unavailable = {"available": False,
                   "reason": "Query telemetry is not enabled on this database."}
    ev = (lambda fn: _safe(fn, days)) if events_ok else (lambda fn: dict(unavailable))
    return {
        "days": days,
        "telemetry_available": events_ok,
        "usage": ev(_usage),
        "performance": ev(_performance),
        "cost": ev(_cost),
        "reliability": ev(_reliability),
        "quality": ev(_quality),
        "tools": ev(_tools),
        "repeats": ev(_repeats),
        "ingestion": _safe(_ingestion, days),
    }
