"""Storage-plane health checks for GET /health's `storage` block.

Each check is independent and never raises -- a failure in one engine must
never break the others or the overall /health response. Every check reports
one of three statuses:
  "ok"       -- reachable and responded within the check's own short timeout
  "disabled" -- the engine is intentionally off via config (Valkey/MinIO can
                both be disabled; Postgres and DuckDB cannot, they're always
                checked)
  "error"    -- enabled but unreachable/misbehaving; `detail` carries a
                short, client-safe reason (never a raw exception with
                connection strings/credentials in it)
"""

import time

from modes.trial_balance.pipeline.config import settings

_CHECK_TIMEOUT_SECONDS = 2


def _timed(fn) -> dict:
    start = time.monotonic()
    try:
        result = fn()
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        if isinstance(result, dict):
            return {**result, "latency_ms": latency_ms}
        return {"status": "ok", "latency_ms": latency_ms}
    except Exception as e:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {"status": "error", "detail": f"{type(e).__name__}", "latency_ms": latency_ms}


def check_postgres() -> dict:
    def _ping():
        from modes.trial_balance.pipeline.db import db_cursor

        with db_cursor() as cur:
            cur.execute("SELECT 1")
        return {"status": "ok"}

    return _timed(_ping)


def check_valkey() -> dict:
    if not settings.VALKEY_ENABLED:
        return {"status": "disabled"}

    def _ping():
        from modes.trial_balance.pipeline.valkey_client import get_valkey_client

        client = get_valkey_client()
        if client is None:
            return {"status": "error", "detail": "client unavailable"}
        client.ping()
        return {"status": "ok"}

    return _timed(_ping)


def check_minio() -> dict:
    if not settings.MINIO_ENABLED:
        return {"status": "disabled"}

    def _ping():
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "s3",
            endpoint_url=settings.MINIO_ENDPOINT,
            aws_access_key_id=settings.MINIO_ACCESS_KEY,
            aws_secret_access_key=settings.MINIO_SECRET_KEY,
            region_name="us-east-1",
            config=Config(connect_timeout=_CHECK_TIMEOUT_SECONDS, read_timeout=_CHECK_TIMEOUT_SECONDS, retries={"max_attempts": 0}),
        )
        client.head_bucket(Bucket=settings.MINIO_BUCKET)
        return {"status": "ok"}

    return _timed(_ping)


def check_duckdb() -> dict:
    """DuckDB is in-process/embedded, not a server -- 'reachable' means the
    library imports and a trivial in-memory query executes, not a network
    ping."""
    def _ping():
        import duckdb

        con = duckdb.connect(":memory:")
        try:
            con.execute("SELECT 1")
        finally:
            con.close()
        return {"status": "ok"}

    return _timed(_ping)


def check_storage_health() -> dict:
    return {
        "postgres": check_postgres(),
        "valkey": check_valkey(),
        "minio": check_minio(),
        "duckdb": check_duckdb(),
    }
