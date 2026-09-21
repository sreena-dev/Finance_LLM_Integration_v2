"""Integration tests for backend/db.py's SESSION STORE section (pipeline_sessions /
pipeline_findings / pipeline_artifacts) against the real Postgres DB. Skipped
automatically if no DB is reachable (see conftest.py's db_available fixture) --
this whole section previously had zero test coverage at all.

Every test uses a unique session_id and hard-deletes its own rows in a finally
block, so a failed run never leaves stray rows for the next run to trip over."""

import pytest

from modes.trial_balance.pipeline.db import (
    add_findings,
    create_session,
    find_latest_session,
    get_findings,
    get_session,
    list_sessions,
    record_tool_artifacts,
    update_session_status,
)


def _cleanup(session_id):
    from modes.trial_balance.pipeline.db import db_cursor

    with db_cursor(dict_rows=False) as cur:
        cur.execute("DELETE FROM pipeline_artifacts WHERE session_id = %s", (session_id,))
        cur.execute("DELETE FROM pipeline_findings WHERE session_id = %s", (session_id,))
        cur.execute("DELETE FROM pipeline_sessions WHERE session_id = %s", (session_id,))


@pytest.fixture
def live_session(db_available):
    """A real pipeline_sessions row, cleaned up afterward regardless of outcome."""
    if not db_available:
        pytest.skip("No reachable Postgres DB for this test run.")
    session_id = create_session(mode="SINGLE_TB", source="pytest", tb_doc_id="PYTEST_SESSION_DOC")
    yield session_id
    _cleanup(session_id)


class TestCreateAndGetSession:
    def test_create_session_returns_a_session_id(self, live_session):
        assert isinstance(live_session, str) and live_session

    def test_get_session_round_trips_the_fields_it_was_created_with(self, live_session):
        row = get_session(live_session)
        assert row is not None
        assert row["session_id"] == live_session
        assert row["mode"] == "SINGLE_TB"
        assert row["source"] == "pytest"
        assert row["tb_doc_id"] == "PYTEST_SESSION_DOC"
        assert row["status"] == "RUNNING"

    def test_get_session_returns_none_for_an_unknown_session_id(self, db_available):
        if not db_available:
            pytest.skip("No reachable Postgres DB for this test run.")
        assert get_session("no-such-session-id") is None


class TestUpdateSessionStatus:
    def test_status_transitions_to_success(self, live_session):
        update_session_status(live_session, "SUCCESS")
        assert get_session(live_session)["status"] == "SUCCESS"

    def test_failure_records_the_error_message(self, live_session):
        update_session_status(live_session, "FAILED", error_message="boom")
        row = get_session(live_session)
        assert row["status"] == "FAILED"
        assert row["error_message"] == "boom"


class TestFindLatestSession:
    def test_finds_the_session_by_doc_id_and_status(self, live_session):
        update_session_status(live_session, "SUCCESS")
        found = find_latest_session("PYTEST_SESSION_DOC")
        assert found is not None
        assert found["session_id"] == live_session

    def test_does_not_find_a_session_that_has_not_reached_the_requested_status(self, live_session):
        # live_session is left RUNNING (never updated) -- default status filter is SUCCESS.
        found = find_latest_session("PYTEST_SESSION_DOC")
        assert found is None or found["session_id"] != live_session

    def test_status_none_matches_regardless_of_status(self, live_session):
        found = find_latest_session("PYTEST_SESSION_DOC", status=None)
        assert found is not None
        assert found["session_id"] == live_session

    def test_tb_doc_id_prior_narrows_to_comparison_sessions(self, db_available):
        if not db_available:
            pytest.skip("No reachable Postgres DB for this test run.")
        sid = create_session(
            mode="COMPARISON", source="pytest",
            tb_doc_id="PYTEST_CMP_CY", tb_doc_id_prior="PYTEST_CMP_PY",
        )
        try:
            update_session_status(sid, "SUCCESS")
            found = find_latest_session("PYTEST_CMP_CY", "PYTEST_CMP_PY")
            assert found is not None and found["session_id"] == sid
            # Without the prior doc_id, the SINGLE_TB-style query (tb_doc_id_prior IS NULL)
            # must not match this comparison row.
            assert find_latest_session("PYTEST_CMP_CY") is None
        finally:
            _cleanup(sid)


class TestListSessions:
    def test_a_freshly_created_session_appears_in_the_listing(self, live_session):
        rows = list_sessions(limit=200)
        assert any(r["session_id"] == live_session for r in rows)

    def test_limit_is_respected(self, live_session):
        rows = list_sessions(limit=1)
        assert len(rows) <= 1


class TestFindingsRoundTrip:
    def test_add_and_get_findings(self, live_session):
        n = add_findings(live_session, [
            {"category": "materiality", "severity": "high", "statement": "Test finding A",
             "evidence_uids": ["TB-000"], "gl_code": "1001"},
            {"category": "variance", "severity": "medium", "statement": "Test finding B"},
        ])
        assert n == 2
        all_findings = get_findings(live_session)
        assert len(all_findings) == 2
        statements = {f["statement"] for f in all_findings}
        assert statements == {"Test finding A", "Test finding B"}

    def test_get_findings_filters_by_severity(self, live_session):
        add_findings(live_session, [
            {"category": "materiality", "severity": "high", "statement": "High one"},
            {"category": "materiality", "severity": "low", "statement": "Low one"},
        ])
        high_only = get_findings(live_session, severity="high")
        assert len(high_only) == 1
        assert high_only[0]["statement"] == "High one"

    def test_get_findings_filters_by_category(self, live_session):
        add_findings(live_session, [
            {"category": "materiality", "severity": "medium", "statement": "Materiality one"},
            {"category": "variance", "severity": "medium", "statement": "Variance one"},
        ])
        materiality_only = get_findings(live_session, category="materiality")
        assert len(materiality_only) == 1
        assert materiality_only[0]["statement"] == "Materiality one"

    def test_add_findings_with_an_empty_list_is_a_harmless_no_op(self, live_session):
        assert add_findings(live_session, []) == 0
        assert get_findings(live_session) == []

    def test_evidence_uids_default_to_an_empty_list_when_absent(self, live_session):
        add_findings(live_session, [{"category": "risk", "severity": "low", "statement": "No evidence supplied"}])
        row = get_findings(live_session)[0]
        assert row["evidence_uids"] == []


class TestRecordToolArtifacts:
    def test_records_without_error_and_is_queryable_directly(self, live_session, db_available):
        record_tool_artifacts(live_session, "build_materiality", ["materiality.json"], pipeline_status="SUCCESS")
        from modes.trial_balance.pipeline.db import db_cursor

        with db_cursor() as cur:
            cur.execute("SELECT * FROM pipeline_artifacts WHERE session_id = %s", (live_session,))
            rows = [dict(r) for r in cur.fetchall()]
        assert len(rows) == 1
        assert rows[0]["tool_name"] == "build_materiality"
        assert rows[0]["artifact_paths"] == ["materiality.json"]
        assert rows[0]["pipeline_status"] == "SUCCESS"

    def test_artifact_paths_defaults_to_an_empty_list_when_none(self, live_session):
        record_tool_artifacts(live_session, "some_tool", None)
        from modes.trial_balance.pipeline.db import db_cursor

        with db_cursor() as cur:
            cur.execute("SELECT artifact_paths FROM pipeline_artifacts WHERE session_id = %s", (live_session,))
            row = cur.fetchone()
        assert row["artifact_paths"] == []
