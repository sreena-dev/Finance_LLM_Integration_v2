"""One row per query, success or failure -- for the admin dashboard.

WHY THIS EXISTS
---------------
Latency, token counts and tool-call counts were only ever printed to stdout, and
a failed query left no trace anywhere (a turn is stored only after it succeeds).
The dashboard's cost, reliability and slowest-query views need those numbers, so
they are written to `artha_query_events`.

THE ONE RULE: TELEMETRY MUST NEVER AFFECT A USER'S ANSWER
---------------------------------------------------------
Recording is fire-and-forget on a daemon thread, every failure is swallowed and
logged, and nothing here can raise into the caller. A slow or broken telemetry
write must not delay, fail or alter the answer that triggered it.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid

from .auth import db as auth_db
from .auth import schema

logger = logging.getLogger(__name__)

RETENTION_DAYS = 90
_SWEEP_EVERY_SECONDS = 6 * 3600
_last_sweep = 0.0
_sweep_lock = threading.Lock()

_MAX_TEXT = 4000  # a query or error message is bounded; never store unbounded text


def _clip(value, limit: int = _MAX_TEXT):
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "..."


def _write(event: dict) -> None:
    try:
        if not schema.events_ready():
            return
        with auth_db.db_cursor(dict_rows=False) as cur:
            cur.execute(
                "INSERT INTO public.artha_query_events ("
                " event_id, user_id, conversation_id, mode, query_text, status,"
                " elapsed_seconds, prompt_tokens, completion_tokens, tool_calls,"
                " tools_used, confidence, unsourced, has_upload,"
                " num_chunks_retrieved, rewritten, error_stage, error_message"
                ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s)",
                (
                    str(uuid.uuid4()),
                    event.get("user_id") or None,
                    event.get("conversation_id") or None,
                    event.get("mode") or "unknown",
                    _clip(event.get("query_text")),
                    event.get("status") or "ok",
                    event.get("elapsed_seconds"),
                    event.get("prompt_tokens"),
                    event.get("completion_tokens"),
                    event.get("tool_calls"),
                    json.dumps(event.get("tools_used") or []),
                    event.get("confidence"),
                    event.get("unsourced"),
                    event.get("has_upload"),
                    event.get("num_chunks_retrieved"),
                    event.get("rewritten"),
                    event.get("error_stage"),
                    _clip(event.get("error_message"), 1000),
                ),
            )
        _maybe_sweep()
    except Exception as exc:  # noqa: BLE001 - see module docstring
        logger.warning("could not record a query event: %s: %s",
                       type(exc).__name__, exc)


def _maybe_sweep() -> None:
    """Delete events past retention, at most once per interval per process."""
    global _last_sweep
    now = time.time()
    if now - _last_sweep < _SWEEP_EVERY_SECONDS:
        return
    if not _sweep_lock.acquire(blocking=False):
        return
    try:
        if now - _last_sweep < _SWEEP_EVERY_SECONDS:
            return
        _last_sweep = now
        with auth_db.db_cursor(dict_rows=False) as cur:
            cur.execute(
                "DELETE FROM public.artha_query_events "
                "WHERE created_at < now() - (%s || ' days')::interval",
                (str(RETENTION_DAYS),),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("query-event retention sweep failed: %s", exc)
    finally:
        _sweep_lock.release()


def record_query_event(**event) -> None:
    """Record one query. Returns immediately; the write happens on a thread."""
    try:
        threading.Thread(target=_write, args=(event,), daemon=True,
                         name="artha-query-event").start()
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not start the telemetry writer: %s", exc)
