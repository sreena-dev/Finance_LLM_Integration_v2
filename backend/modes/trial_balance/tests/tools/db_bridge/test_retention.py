"""Phase 3 retention: backend/db.py's soft_delete_expired_sessions /
list_expired_sessions / list_artifact_files_for_session, and
backend/scripts/cleanup_sessions.py's local-deletion safety gate (never
remove a local directory unless every one of its files has a confirmed
MinIO copy recorded). Skipped automatically if no DB is reachable (see
conftest.py's db_available fixture)."""

import pytest

from modes.trial_balance.pipeline.db import (
    create_session,
    db_cursor,
    list_artifact_files_for_session,
    list_expired_sessions,
    record_artifact_files,
    soft_delete_expired_sessions,
)
from modes.trial_balance.scripts.cleanup_sessions import _session_is_safe_to_delete_locally


def _cleanup(session_id):
    with db_cursor(dict_rows=False) as cur:
        cur.execute("DELETE FROM pipeline_artifact_files WHERE session_id = %s", (session_id,))
        cur.execute("DELETE FROM pipeline_sessions WHERE session_id = %s", (session_id,))


@pytest.fixture
def old_session(db_available):
    """A pipeline_sessions row backdated to 200 days old (safely past any
    retention window this test exercises), cleaned up afterward."""
    if not db_available:
        pytest.skip("No reachable Postgres DB for this test run.")
    session_id = create_session(mode="SINGLE_TB", source="pytest", tb_doc_id="PYTEST_RETENTION_DOC")
    with db_cursor(dict_rows=False) as cur:
        cur.execute(
            "UPDATE pipeline_sessions SET created_at = now() - interval '200 days' WHERE session_id = %s",
            (session_id,),
        )
    yield session_id
    _cleanup(session_id)


@pytest.fixture
def recent_session(db_available):
    if not db_available:
        pytest.skip("No reachable Postgres DB for this test run.")
    session_id = create_session(mode="SINGLE_TB", source="pytest", tb_doc_id="PYTEST_RETENTION_RECENT_DOC")
    yield session_id
    _cleanup(session_id)


class TestSoftDeleteExpiredSessions:
    def test_soft_deletes_a_session_older_than_the_window(self, old_session):
        expired = soft_delete_expired_sessions(older_than_days=90)
        assert old_session in expired

        with db_cursor() as cur:
            cur.execute("SELECT deleted_at FROM pipeline_sessions WHERE session_id = %s", (old_session,))
            row = cur.fetchone()
        assert row["deleted_at"] is not None

    def test_does_not_soft_delete_a_recent_session(self, recent_session):
        expired = soft_delete_expired_sessions(older_than_days=90)
        assert recent_session not in expired

        with db_cursor() as cur:
            cur.execute("SELECT deleted_at FROM pipeline_sessions WHERE session_id = %s", (recent_session,))
            row = cur.fetchone()
        assert row["deleted_at"] is None

    def test_does_not_re_flag_an_already_soft_deleted_session(self, old_session):
        first = soft_delete_expired_sessions(older_than_days=90)
        assert old_session in first
        second = soft_delete_expired_sessions(older_than_days=90)
        assert old_session not in second


class TestListExpiredSessions:
    def test_appears_after_soft_delete_not_before(self, old_session):
        assert old_session not in [s["session_id"] for s in list_expired_sessions()]
        soft_delete_expired_sessions(older_than_days=90)
        assert old_session in [s["session_id"] for s in list_expired_sessions()]


class TestSessionSafeToDeleteLocally:
    def test_session_with_no_registered_files_is_never_safe(self, old_session):
        safe, reason = _session_is_safe_to_delete_locally(old_session)
        assert safe is False
        assert "no pipeline_artifact_files rows" in reason

    def test_session_with_a_local_only_file_is_not_safe(self, old_session):
        record_artifact_files(old_session, "pytest_tool", [{
            "artifact_path": "/fake/path/canonical_tb.parquet",
            "checksum_sha256": "abc",
            "size_bytes": 10,
            "format": "parquet",
            "storage_backend": "local",
            "storage_uri": None,
        }])
        safe, reason = _session_is_safe_to_delete_locally(old_session)
        assert safe is False
        assert "no confirmed MinIO copy" in reason

    def test_session_with_all_files_confirmed_in_minio_is_safe(self, old_session):
        record_artifact_files(old_session, "pytest_tool", [
            {
                "artifact_path": "/fake/path/canonical_tb.parquet",
                "checksum_sha256": "abc",
                "size_bytes": 10,
                "format": "parquet",
                "storage_backend": "minio",
                "storage_uri": "s3://tb-artifacts/sessions/x/canonical_tb.parquet",
            },
            {
                "artifact_path": "/fake/path/materiality.json",
                "checksum_sha256": "def",
                "size_bytes": 20,
                "format": "json",
                "storage_backend": "minio",
                "storage_uri": "s3://tb-artifacts/sessions/x/materiality.json",
            },
        ])
        safe, reason = _session_is_safe_to_delete_locally(old_session)
        assert safe is True
        assert "all 2 file(s) confirmed in MinIO" in reason

    def test_one_local_only_file_among_otherwise_minio_files_blocks_deletion(self, old_session):
        """A partial durable copy must never be treated as good enough."""
        record_artifact_files(old_session, "pytest_tool", [
            {
                "artifact_path": "/fake/path/canonical_tb.parquet",
                "checksum_sha256": "abc",
                "size_bytes": 10,
                "format": "parquet",
                "storage_backend": "minio",
                "storage_uri": "s3://tb-artifacts/sessions/x/canonical_tb.parquet",
            },
            {
                "artifact_path": "/fake/path/materiality.json",
                "checksum_sha256": "def",
                "size_bytes": 20,
                "format": "json",
                "storage_backend": "local",
                "storage_uri": None,
            },
        ])
        safe, reason = _session_is_safe_to_delete_locally(old_session)
        assert safe is False


class TestListArtifactFilesForSession:
    def test_returns_only_this_sessions_rows(self, old_session, recent_session):
        record_artifact_files(old_session, "pytest_tool", [{
            "artifact_path": "/fake/a.json", "checksum_sha256": "a", "size_bytes": 1,
            "format": "json", "storage_backend": "local", "storage_uri": None,
        }])
        record_artifact_files(recent_session, "pytest_tool", [{
            "artifact_path": "/fake/b.json", "checksum_sha256": "b", "size_bytes": 1,
            "format": "json", "storage_backend": "local", "storage_uri": None,
        }])

        old_files = list_artifact_files_for_session(old_session)
        assert len(old_files) == 1
        assert old_files[0]["artifact_path"] == "/fake/a.json"
