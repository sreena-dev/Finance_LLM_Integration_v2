"""
Self-contained read-only DB helper (psycopg3). Mirrors the fail-fast style of the
sibling rag/db.py but shares no code with it — fs_db must stay deletable in one move.
"""
from __future__ import annotations
import threading
from typing import Any
import psycopg
from psycopg.rows import dict_row
from .config import PG

# ONE CONNECTION PER THREAD, NOT ONE PER PROCESS.
#
# A psycopg connection is not safe to share across threads: two threads interleaving on
# one connection corrupt each other's result sets, and the failure is silent and
# intermittent rather than an exception. The module used to hold a single global, which
# was correct while every caller was sequential and became a latent bug the moment the
# FDR started extracting an entity's filings concurrently (`fdr/source.py`).
#
# Thread-local is the smallest change that fixes it: `conn()`, `query()` and `one()` keep
# their exact signatures and behaviour, single-threaded callers see no difference at all,
# and a worker thread simply gets its own session. Connections are reused for the life of
# the thread, so a POOL of long-lived worker threads is also a pool of connections —
# which is why `fdr/source.py` holds its executor at module scope rather than creating
# one per request.
_local = threading.local()


def conn() -> psycopg.Connection:
    c = getattr(_local, "conn", None)
    if c is None or c.closed:
        c = psycopg.connect(autocommit=True, row_factory=dict_row, **PG)
        # Enforce read-only at the session level: this package must never write.
        c.execute("SET default_transaction_read_only = on")
        _local.conn = c
    return c


def close_thread_conn() -> None:
    """Release this thread's connection. For workers that are about to exit."""
    c = getattr(_local, "conn", None)
    if c is not None and not c.closed:
        c.close()
    _local.conn = None


def query(sql: str, params: dict | tuple | None = None) -> list[dict[str, Any]]:
    with conn().cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def one(sql: str, params: dict | tuple | None = None) -> dict | None:
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
    except Exception as e:  # noqa: BLE001 - report, don't crash the flow
        return False, f"{type(e).__name__}: {e}"
