"""Master database module (flattened, single-file — mirrors backend/tools.py).

Layout: connection pool + cursor context manager -> MAIN/LIVE repository (over
document_table/tb_table and live_document_table/live_tb_table) -> pipeline_sessions /
pipeline_findings / pipeline_artifacts session store. Formerly
backend/db/{connection,repository,session_store}.py.
"""

import json
import uuid
from contextlib import contextmanager
from typing import Optional

import psycopg2
import psycopg2.extras
import psycopg2.pool

from modes.trial_balance.pipeline.config import settings

# backend.tools imports db_cursor/repository functions from THIS module at its own
# module level, so importing backend.tools back here at module level would circularly
# deadlock whichever of the two modules is imported first. PipelineDBError /
# CANONICAL_TB_ALL_COLUMNS / DOCUMENT_OPTIONAL_COLUMNS are only ever needed inside a
# function body below, never at module load time, so each is imported locally at the
# point of use instead -- that fully breaks the cycle in both directions.

# =============================================================================
# CONNECTION (formerly backend/db/connection.py)
# =============================================================================

_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        from modes.trial_balance.pipeline.tools import PipelineDBError

        try:
            _pool = psycopg2.pool.SimpleConnectionPool(
                settings.DB_POOL_MIN,
                settings.DB_POOL_MAX,
                host=settings.DB_HOST,
                port=settings.DB_PORT,
                dbname=settings.DB_NAME,
                user=settings.DB_USER,
                password=settings.DB_PASSWORD,
            )
        except psycopg2.OperationalError as e:
            raise PipelineDBError(f"Could not connect to trial_balance_db: {e}")
    return _pool


@contextmanager
def db_cursor(dict_rows: bool = True):
    """Checkout a pooled connection + cursor. Commits on success, rolls back on error."""
    from modes.trial_balance.pipeline.tools import PipelineDBError

    pool = _get_pool()
    conn = pool.getconn()
    try:
        cursor_factory = psycopg2.extras.RealDictCursor if dict_rows else None
        with conn.cursor(cursor_factory=cursor_factory) as cur:
            yield cur
        conn.commit()
    except Exception as e:
        conn.rollback()
        if isinstance(e, PipelineDBError):
            raise
        raise PipelineDBError(f"Database operation failed: {e}")
    finally:
        pool.putconn(conn)


# =============================================================================
# REPOSITORY (formerly backend/db/repository.py)
#
# Over document_table/tb_table (MAIN, read-only from this codebase) and
# live_document_table/live_tb_table (LIVE, write-capable for interactive
# ingestion). Operations are split by write scope on purpose — nothing in this
# section can write to MAIN, and nothing reads from LIVE for comparison/report
# purposes, so "which table a UI action touches" is structural, not a runtime flag.
# =============================================================================

# ingest_to_db.py (TB_ingestion) writes mapped_status lowercase ("mapped"/
# "unmapped") straight from the finished TB_GROUPING workbook's own "Mapped
# Status" column text. Every TB-v2 tool compares against the uppercase
# MAPPED_STATUS_* constants (backend/tools.py), so a MAIN row's raw value
# would silently never match -- every DB-sourced document would look 100%
# unmapped downstream. Normalized once here, the single MAIN-read choke
# point, rather than patched in every caller.
_MAPPED_STATUS_UPPER = {"mapped": "MAPPED", "unmapped": "UNMAPPED", "unmatched": "UNMATCHED"}


def _normalize_mapped_status(rows: list) -> list:
    for row in rows:
        status = row.get("mapped_status")
        if isinstance(status, str):
            row["mapped_status"] = _MAPPED_STATUS_UPPER.get(status.strip().lower(), status.strip().upper())
    return rows

# ---------------------------------------------------------------------------
# MAIN (document_table / tb_table) — READ ONLY
# ---------------------------------------------------------------------------


def list_main_documents(entity_id: Optional[str] = None, financial_year: Optional[str] = None) -> list:
    query = "SELECT * FROM document_table WHERE 1=1"
    params = []
    if entity_id:
        query += " AND entity_id = %s"
        params.append(entity_id)
    if financial_year:
        query += " AND financial_year = %s"
        params.append(financial_year)
    query += " ORDER BY fy_period_start DESC NULLS LAST, id DESC"
    with db_cursor() as cur:
        cur.execute(query, params)
        return [dict(r) for r in cur.fetchall()]


def fetch_main_document(tb_doc_id: str) -> Optional[dict]:
    with db_cursor() as cur:
        cur.execute("SELECT * FROM document_table WHERE tb_doc_id = %s LIMIT 1", (tb_doc_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def fetch_main_document_by_entity_fy(entity_id: str, financial_year: str) -> Optional[dict]:
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM document_table WHERE entity_id = %s AND financial_year = %s "
            "ORDER BY document_version DESC NULLS LAST, id DESC LIMIT 1",
            (entity_id, financial_year),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def fetch_main_lines(tb_doc_id: str) -> list:
    from modes.trial_balance.pipeline.tools import CANONICAL_TB_ALL_COLUMNS

    with db_cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(CANONICAL_TB_ALL_COLUMNS)} FROM tb_table WHERE tb_doc_id = %s",
            (tb_doc_id,),
        )
        return _normalize_mapped_status([dict(r) for r in cur.fetchall()])


# ---------------------------------------------------------------------------
# LIVE (live_document_table / live_tb_table) — WRITE (upsert) for interactive ingestion
# ---------------------------------------------------------------------------


def upsert_live_document(doc: dict) -> str:
    """Delete-then-insert by tb_doc_id (no unique constraint exists to ON CONFLICT against).
    custom_field_1/2/3 are reserved/not-yet-defined -- passed through as
    optional/nullable (doc.get, not doc[...]) so callers never have to supply
    them."""
    from modes.trial_balance.pipeline.tools import DOCUMENT_OPTIONAL_COLUMNS

    tb_doc_id = doc["tb_doc_id"]
    doc = {**{c: None for c in DOCUMENT_OPTIONAL_COLUMNS}, **doc}
    with db_cursor(dict_rows=False) as cur:
        cur.execute("DELETE FROM live_document_table WHERE tb_doc_id = %s", (tb_doc_id,))
        cur.execute(
            """
            INSERT INTO live_document_table
                (entity_id, entity_name, cin, company_name, fy_period_start, fy_period_end,
                 tb_doc_id, tb_doc_name, statement_type, financial_year, has_grouping,
                 grouping_doc_id, grouping_doc_name, document_version, modification_dump,
                 custom_field_1, custom_field_2, custom_field_3)
            VALUES (%(entity_id)s, %(entity_name)s, %(cin)s, %(company_name)s,
                    %(fy_period_start)s, %(fy_period_end)s, %(tb_doc_id)s, %(tb_doc_name)s,
                    %(statement_type)s, %(financial_year)s, %(has_grouping)s,
                    %(grouping_doc_id)s, %(grouping_doc_name)s, %(document_version)s,
                    %(modification_dump)s, %(custom_field_1)s, %(custom_field_2)s, %(custom_field_3)s)
            """,
            doc,
        )
    return tb_doc_id


def insert_live_lines_batch(tb_doc_id: str, rows: list) -> int:
    """Batched insert via execute_values. `rows` are dicts already shaped to
    CANONICAL_TB_ALL_COLUMNS (custom_field_1/2/3 optional/nullable)."""
    from modes.trial_balance.pipeline.tools import CANONICAL_TB_ALL_COLUMNS

    if not rows:
        return 0
    cols = CANONICAL_TB_ALL_COLUMNS
    values = [tuple(r.get(c) for c in cols) for r in rows]
    with db_cursor(dict_rows=False) as cur:
        cur.execute("DELETE FROM live_tb_table WHERE tb_doc_id = %s", (tb_doc_id,))
        psycopg2.extras.execute_values(
            cur,
            f"INSERT INTO live_tb_table ({', '.join(cols)}) VALUES %s",
            values,
        )
    return len(values)


# =============================================================================
# SESSION STORE (formerly backend/db/session_store.py)
#
# pipeline_sessions / pipeline_findings CRUD. Modeled on
# trial_balance_v1/postgres_session_store.py, scoped to what the frontend's
# /documents, /documents/{id}, findings/evidence endpoints need.
# =============================================================================


def create_session(mode: str, source: str, tb_doc_id: Optional[str] = None,
                    tb_doc_id_prior: Optional[str] = None, entity_id: Optional[str] = None,
                    financial_year: Optional[str] = None) -> str:
    session_id = str(uuid.uuid4())
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO pipeline_sessions
                (session_id, mode, tb_doc_id, tb_doc_id_prior, source, status, entity_id, financial_year)
            VALUES (%s, %s, %s, %s, %s, 'RUNNING', %s, %s)
            """,
            (session_id, mode, tb_doc_id, tb_doc_id_prior, source, entity_id, financial_year),
        )
    return session_id


def update_session_status(session_id: str, status: str, error_message: Optional[str] = None) -> None:
    with db_cursor() as cur:
        cur.execute(
            "UPDATE pipeline_sessions SET status = %s, error_message = %s, updated_at = now() "
            "WHERE session_id = %s AND deleted_at IS NULL",
            (status, error_message, session_id),
        )


def get_session(session_id: str) -> Optional[dict]:
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM pipeline_sessions WHERE session_id = %s AND deleted_at IS NULL",
            (session_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def find_latest_session(tb_doc_id: str, tb_doc_id_prior: Optional[str] = None,
                         status: Optional[str] = "SUCCESS") -> Optional[dict]:
    """Most recent session for a doc_id (+ optional prior-year doc_id for
    COMPARISON runs) -- used by /audit/workbook to locate which session's
    output_dir holds the report for a given doc_id, since the frontend
    requests downloads by doc_id, not session_id."""
    query = "SELECT * FROM pipeline_sessions WHERE tb_doc_id = %s AND deleted_at IS NULL"
    params = [tb_doc_id]
    if tb_doc_id_prior:
        query += " AND tb_doc_id_prior = %s"
        params.append(tb_doc_id_prior)
    else:
        query += " AND tb_doc_id_prior IS NULL"
    if status:
        query += " AND status = %s"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT 1"
    with db_cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()
        return dict(row) if row else None


def list_sessions(limit: int = 50, offset: int = 0) -> list:
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM pipeline_sessions WHERE deleted_at IS NULL "
            "ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (limit, offset),
        )
        return [dict(r) for r in cur.fetchall()]


def add_findings(session_id: str, findings: list) -> int:
    """Each finding: {category, severity, statement, evidence_uids: [...], gl_code?}."""
    if not findings:
        return 0
    with db_cursor(dict_rows=False) as cur:
        for f in findings:
            cur.execute(
                """
                INSERT INTO pipeline_findings (session_id, category, severity, statement, evidence_uids, gl_code)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    session_id,
                    f["category"],
                    f["severity"],
                    f["statement"],
                    json.dumps(f.get("evidence_uids", [])),
                    f.get("gl_code"),
                ),
            )
    return len(findings)


def record_tool_artifacts(run_id: str, tool_name: str, artifact_paths: list, pipeline_status: Optional[str] = None) -> None:
    """Best-effort index of which tool wrote what, for a given pipeline_sessions.session_id.
    Purely additive/observability -- downstream tools never read from pipeline_artifacts,
    they still get their input paths from the producing tool's own `artifacts` response
    list (see the pipeline_tool decorator in backend/tools.py)."""
    with db_cursor(dict_rows=False) as cur:
        cur.execute(
            """
            INSERT INTO pipeline_artifacts (session_id, tool_name, artifact_paths, pipeline_status)
            VALUES (%s, %s, %s, %s)
            """,
            (run_id, tool_name, json.dumps(artifact_paths or []), pipeline_status),
        )


def get_findings(session_id: str, severity: Optional[str] = None, category: Optional[str] = None) -> list:
    query = "SELECT * FROM pipeline_findings WHERE session_id = %s AND deleted_at IS NULL"
    params = [session_id]
    if severity:
        query += " AND severity = %s"
        params.append(severity)
    if category:
        query += " AND category = %s"
        params.append(category)
    with db_cursor() as cur:
        cur.execute(query, params)
        return [dict(r) for r in cur.fetchall()]
