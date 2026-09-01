"""A read-write connection pool for the platform's own tables.

WHY A NEW POOL RATHER THAN REUSING ONE OF THE THREE THAT EXIST
--------------------------------------------------------------
This module writes to `finance_llm` — the same physical database SAR, FDR and
the FS reports tools read. None of the existing helpers can be borrowed, and
each for a different reason:

  `fs_db/db.py`                       issues `SET default_transaction_read_only
                                      = on` on every connection. Writes fail.
  `tools_fs.Database.get_reports_connection()`
                                      returns a single unpooled connection that
                                      `api_server` caches and the agent holds
                                      open across a whole request. Writing on it
                                      would interleave with the pipeline's reads
                                      inside its transaction.
  `trial_balance/pipeline/db.py`      is hardwired to `TB_DB_*`, which is a
                                      different host and a different database
                                      entirely.

So: our own pool, our own connections, short transactions. The *shape* below is
lifted from the Trial Balance helper, which is the one place in this repo that
already gets commit/rollback/return-to-pool right.

WHAT THIS TOUCHES
-----------------
Only tables named `artha_*`, which this project creates. Nothing reads or writes
`documents`, `text_chunks`, `table_chunks` or any other pre-existing table
through here.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager

_pool = None
_pool_lock = threading.Lock()


class AuthDBError(RuntimeError):
    """The platform database could not be reached or a statement failed."""


def dsn() -> str | None:
    """The platform DSN, most specific first.

    `ARTHA_DB_DSN` exists so the users and conversation tables can later be
    moved to their own database without touching any code; unset, they live in
    the same `finance_llm` the rest of the platform already uses.
    """
    for key in ("ARTHA_DB_DSN", "FINANCE_DSN", "FINANCE_LLM_DSN"):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    return None


def _get_pool():
    global _pool
    if _pool is not None:
        return _pool

    with _pool_lock:
        if _pool is not None:
            return _pool

        target = dsn()
        if not target:
            raise AuthDBError(
                "No platform database configured. Set FINANCE_DSN (the same "
                "database the Statutory Auditor's Report mode uses) or "
                "ARTHA_DB_DSN."
            )

        try:
            from psycopg2.pool import ThreadedConnectionPool
        except ImportError as exc:  # pragma: no cover
            raise AuthDBError(f"psycopg2 is not installed: {exc}") from exc

        try:
            _pool = ThreadedConnectionPool(
                int(os.environ.get("ARTHA_DB_POOL_MIN", "1")),
                int(os.environ.get("ARTHA_DB_POOL_MAX", "5")),
                dsn=target,
            )
        except Exception as exc:  # noqa: BLE001
            raise AuthDBError(
                f"Could not connect to the platform database: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        return _pool


@contextmanager
def db_cursor(dict_rows: bool = True):
    """A cursor that commits on success and rolls back on failure.

    Always returns its connection to the pool, including on the error path —
    leaking one here would slowly starve the pool and present later as an
    unrelated hang.
    """
    import psycopg2.extras

    pool = _get_pool()
    conn = pool.getconn()
    factory = psycopg2.extras.RealDictCursor if dict_rows else None
    try:
        with conn.cursor(cursor_factory=factory) as cur:
            yield cur
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:
            pass
        if isinstance(exc, AuthDBError):
            raise
        raise AuthDBError(f"{type(exc).__name__}: {exc}") from exc
    finally:
        pool.putconn(conn)


def ping() -> tuple[bool, str | None]:
    """(reachable, reason) — never raises. For health reporting."""
    try:
        with db_cursor(dict_rows=False) as cur:
            cur.execute("SELECT 1")
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def reset_pool() -> None:
    """Close and drop the pool — for tests, and after a credentials change."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.closeall()
            except Exception:
                pass
            _pool = None
