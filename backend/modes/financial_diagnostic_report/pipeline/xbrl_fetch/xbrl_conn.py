"""
Self-contained read-only DB helper (psycopg3) for `as_db`. Mirrors `fs_db/db.py`'s
thread-local pattern exactly — that part of the precedent is just "talk to Postgres
safely from a thread pool," not fact-store logic, and is worth reusing as-is.
"""
from __future__ import annotations
import threading
from typing import Any
import psycopg
from psycopg.rows import dict_row
from .xbrl_config import PG

# One connection per thread, not one per process — see fs_db/db.py's docstring for why
# this matters the moment more than one caller extracts concurrently.
_local = threading.local()


def conn() -> psycopg.Connection:
    c = getattr(_local, "conn", None)
    if c is None or c.closed:
        c = psycopg.connect(autocommit=True, row_factory=dict_row, **PG)
        # Enforce read-only at the session level: this package must never write to as_db.
        c.execute("SET default_transaction_read_only = on")
        _local.conn = c
    return c


def close_thread_conn() -> None:
    """Release this thread's connection. For workers that are about to exit."""
    c = getattr(_local, "conn", None)
    if c is not None and not c.closed:
        c.close()
    _local.conn = None


def query(sql: str, params: dict | tuple | list | None = None) -> list[dict[str, Any]]:
    with conn().cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def one(sql: str, params: dict | tuple | list | None = None) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def ping(timeout: int = 5) -> tuple[bool, str]:
    """Fast reachability check. Returns (ok, message) — never raises."""
    try:
        p = dict(PG)
        p["connect_timeout"] = timeout
        with psycopg.connect(**p) as c:
            v = c.execute("SELECT version()").fetchone()[0]
        return True, str(v)
    except Exception as exc:  # noqa: BLE001 - report, don't crash the flow
        return False, f"{type(exc).__name__}: {exc}"
