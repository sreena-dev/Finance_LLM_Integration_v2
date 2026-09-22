"""End-to-end coverage for backend/scripts/cleanup_sessions.cleanup(): actual
directory removal, gated correctly on both the retention window and the
durable-copy safety check. Uses a temp directory in place of the real
Trial_Balance/sessions/ (never touches real session data). Skipped
automatically if no DB is reachable (see conftest.py's db_available)."""

import pytest

import modes.trial_balance.scripts.cleanup_sessions as cleanup_module
from modes.trial_balance.pipeline.db import create_session, db_cursor, record_artifact_files


def _cleanup_db(session_id):
    with db_cursor(dict_rows=False) as cur:
        cur.execute("DELETE FROM pipeline_artifact_files WHERE session_id = %s", (session_id,))
        cur.execute("DELETE FROM pipeline_sessions WHERE session_id = %s", (session_id,))


def _make_old_session(db_available, session_id_suffix):
    if not db_available:
        pytest.skip("No reachable Postgres DB for this test run.")
    session_id = create_session(mode="SINGLE_TB", source="pytest", tb_doc_id=f"PYTEST_CLEANUP_{session_id_suffix}")
    with db_cursor(dict_rows=False) as cur:
        cur.execute(
            "UPDATE pipeline_sessions SET created_at = now() - interval '200 days' WHERE session_id = %s",
            (session_id,),
        )
    return session_id


@pytest.fixture
def sessions_root(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    root.mkdir()
    monkeypatch.setattr(cleanup_module, "SESSIONS_ROOT", root)
    return root


def test_removes_local_dir_when_every_file_confirmed_in_minio(db_available, sessions_root):
    session_id = _make_old_session(db_available, "MINIO_CONFIRMED")
    try:
        session_dir = sessions_root / session_id
        session_dir.mkdir()
        (session_dir / "canonical_tb.parquet").write_bytes(b"fake")
        record_artifact_files(session_id, "pytest_tool", [{
            "artifact_path": str(session_dir / "canonical_tb.parquet"),
            "checksum_sha256": "abc", "size_bytes": 4, "format": "parquet",
            "storage_backend": "minio", "storage_uri": "s3://tb-artifacts/x/canonical_tb.parquet",
        }])

        stats = cleanup_module.cleanup(older_than_days=90, dry_run=False)

        assert not session_dir.exists()
        assert stats["locally_removed"] >= 1
    finally:
        _cleanup_db(session_id)


def test_retains_local_dir_when_a_file_has_no_durable_copy(db_available, sessions_root):
    session_id = _make_old_session(db_available, "LOCAL_ONLY")
    try:
        session_dir = sessions_root / session_id
        session_dir.mkdir()
        (session_dir / "canonical_tb.parquet").write_bytes(b"fake")
        record_artifact_files(session_id, "pytest_tool", [{
            "artifact_path": str(session_dir / "canonical_tb.parquet"),
            "checksum_sha256": "abc", "size_bytes": 4, "format": "parquet",
            "storage_backend": "local", "storage_uri": None,
        }])

        stats = cleanup_module.cleanup(older_than_days=90, dry_run=False)

        assert session_dir.exists()
        assert stats["retained_no_durable_copy"] >= 1
    finally:
        _cleanup_db(session_id)


def test_retains_local_dir_when_session_never_registered_any_artifacts(db_available, sessions_root):
    """The exact case the 17 real backfilled-but-not-MinIO-uploaded sessions
    are in today: registered, but every row is local-only -- must never be
    deleted while MinIO stays disabled."""
    session_id = _make_old_session(db_available, "UNREGISTERED")
    try:
        session_dir = sessions_root / session_id
        session_dir.mkdir()
        (session_dir / "canonical_tb.parquet").write_bytes(b"fake")
        # No record_artifact_files call at all -- zero rows for this session.

        stats = cleanup_module.cleanup(older_than_days=90, dry_run=False)

        assert session_dir.exists()
        assert stats["retained_no_durable_copy"] >= 1
    finally:
        _cleanup_db(session_id)


def test_dry_run_never_removes_anything(db_available, sessions_root):
    session_id = _make_old_session(db_available, "DRY_RUN")
    try:
        session_dir = sessions_root / session_id
        session_dir.mkdir()
        (session_dir / "canonical_tb.parquet").write_bytes(b"fake")
        record_artifact_files(session_id, "pytest_tool", [{
            "artifact_path": str(session_dir / "canonical_tb.parquet"),
            "checksum_sha256": "abc", "size_bytes": 4, "format": "parquet",
            "storage_backend": "minio", "storage_uri": "s3://tb-artifacts/x/canonical_tb.parquet",
        }])

        stats = cleanup_module.cleanup(older_than_days=90, dry_run=True)

        assert session_dir.exists(), "dry-run must never actually delete anything"
        assert stats["soft_deleted"] == 0, "dry-run must not soft-delete either"
    finally:
        _cleanup_db(session_id)


def test_recent_session_is_left_alone_entirely(db_available, sessions_root):
    if not db_available:
        pytest.skip("No reachable Postgres DB for this test run.")
    session_id = create_session(mode="SINGLE_TB", source="pytest", tb_doc_id="PYTEST_CLEANUP_RECENT")
    try:
        session_dir = sessions_root / session_id
        session_dir.mkdir()
        (session_dir / "canonical_tb.parquet").write_bytes(b"fake")
        record_artifact_files(session_id, "pytest_tool", [{
            "artifact_path": str(session_dir / "canonical_tb.parquet"),
            "checksum_sha256": "abc", "size_bytes": 4, "format": "parquet",
            "storage_backend": "minio", "storage_uri": "s3://tb-artifacts/x/canonical_tb.parquet",
        }])

        cleanup_module.cleanup(older_than_days=90, dry_run=False)

        assert session_dir.exists(), "a session inside the retention window must never be touched"
    finally:
        _cleanup_db(session_id)
