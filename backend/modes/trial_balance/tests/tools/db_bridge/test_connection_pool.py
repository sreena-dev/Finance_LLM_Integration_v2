"""backend/db.py's connection pool must be ThreadedConnectionPool, not
SimpleConnectionPool -- nearly every route in router.py is a plain `def`, which
FastAPI runs in a threadpool executor, so concurrent requests from different
authenticated users already call getconn()/putconn() from multiple OS threads
simultaneously. SimpleConnectionPool's own docstring says it "can't be shared
across different threads"; this pins the fix so it can't silently regress."""

import psycopg2.pool
import pytest

from modes.trial_balance.pipeline import db as db_module


@pytest.fixture(autouse=True)
def _reset_pool():
    """_get_pool() is a lazy module-level singleton -- reset it before and after
    so this test observes a fresh construction rather than a pool an earlier
    test already built."""
    original = db_module._pool
    db_module._pool = None
    yield
    db_module._pool = original


def test_get_pool_constructs_a_threaded_connection_pool(db_available):
    if not db_available:
        pytest.skip("No reachable Postgres DB for this test run.")
    pool = db_module._get_pool()
    # ThreadedConnectionPool and SimpleConnectionPool are siblings (both extend
    # AbstractConnectionPool directly), not parent/child, so this alone rules out
    # SimpleConnectionPool -- it wouldn't pass isinstance() for the other type.
    assert isinstance(pool, psycopg2.pool.ThreadedConnectionPool)
