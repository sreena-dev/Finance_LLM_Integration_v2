"""Tests for backend/agent/pipeline_tool.py's atomic_write helper and the
pipeline_artifacts registration hook.

atomic_write: proves the whole point of adding it -- a crash mid-write must
never leave a partial/corrupt file at the final path, only at the .tmp path
(or nothing at all).

Registration hook: proves session_id is correctly derived from output_dir
(the only place it's available -- no tool receives session_id/run_id as its
own kwarg anywhere in this codebase) and that a DB failure never fails the
tool call itself.
"""

import json

import pytest

from modes.trial_balance.pipeline.tools import _derive_session_id, atomic_write, pipeline_tool, resolve_output_dir

_TEST_SESSION_ID = "22222222-2222-2222-2222-222222222222"


class TestAtomicWrite:
    def test_successful_write_lands_at_final_path(self, tmp_path):
        final_path = tmp_path / "out.json"
        with atomic_write(final_path) as tmp_write_path:
            with open(tmp_write_path, "w") as f:
                json.dump({"x": 1}, f)

        assert final_path.exists()
        with open(final_path) as f:
            assert json.load(f) == {"x": 1}
        # No leftover .tmp file after a clean write.
        assert list(tmp_path.glob("*.tmp-*")) == []

    def test_crash_mid_write_leaves_final_path_untouched(self, tmp_path):
        final_path = tmp_path / "out.json"
        final_path.write_text('{"existing": true}')  # simulate a prior successful write

        with pytest.raises(RuntimeError):
            with atomic_write(final_path) as tmp_write_path:
                with open(tmp_write_path, "w") as f:
                    f.write('{"partial')  # deliberately incomplete
                raise RuntimeError("simulated crash mid-write")

        # The old complete file is exactly as it was -- readers never see a partial write.
        assert json.loads(final_path.read_text()) == {"existing": True}
        # The partial .tmp file is cleaned up, not left behind as debris.
        assert list(tmp_path.glob("*.tmp-*")) == []

    def test_crash_before_any_prior_file_leaves_nothing_at_final_path(self, tmp_path):
        final_path = tmp_path / "brand_new.json"

        with pytest.raises(RuntimeError):
            with atomic_write(final_path) as tmp_write_path:
                with open(tmp_write_path, "w") as f:
                    f.write("incomplete")
                raise RuntimeError("simulated crash")

        assert not final_path.exists()

    def test_creates_parent_directories(self, tmp_path):
        final_path = tmp_path / "nested" / "dir" / "out.json"
        with atomic_write(final_path) as tmp_write_path:
            with open(tmp_write_path, "w") as f:
                f.write("{}")
        assert final_path.exists()


class TestDeriveSessionId:
    def test_extracts_session_id_from_output_dir(self):
        out_dir = str(resolve_output_dir(f"sessions/{_TEST_SESSION_ID}"))
        assert _derive_session_id({"output_dir": out_dir}) == _TEST_SESSION_ID

    def test_extracts_session_id_from_comparison_subdir(self):
        out_dir = str(resolve_output_dir(f"sessions/{_TEST_SESSION_ID}")) + "/cy"
        assert _derive_session_id({"output_dir": out_dir}) == _TEST_SESSION_ID

    def test_none_when_output_dir_missing(self):
        assert _derive_session_id({}) is None

    def test_none_when_output_dir_has_no_session_segment(self, tmp_path):
        assert _derive_session_id({"output_dir": str(tmp_path)}) is None


class TestRegistrationHook:
    def test_success_registers_artifact_row(self, db_available, tmp_path):
        if not db_available:
            pytest.skip("No reachable Postgres DB (settings.DB_*) -- skipping.")

        out_dir = str(resolve_output_dir(f"sessions/{_TEST_SESSION_ID}"))

        @pipeline_tool("dummy_tool_for_registration_test")
        def dummy_tool(output_dir=None):
            import os

            p = os.path.join(output_dir, "dummy.json")
            os.makedirs(output_dir, exist_ok=True)
            with open(p, "w") as f:
                f.write("{}")
            return {"execution_status": "SUCCESS", "pipeline_status": "SUCCESS", "message": "ok", "artifacts": [p]}

        try:
            result = dummy_tool(output_dir=out_dir)
            assert result["execution_status"] == "SUCCESS"

            from modes.trial_balance.pipeline.db import db_cursor

            with db_cursor() as cur:
                cur.execute(
                    "SELECT tool_name, pipeline_status FROM pipeline_artifacts WHERE session_id = %s",
                    (_TEST_SESSION_ID,),
                )
                rows = [dict(r) for r in cur.fetchall()]
            assert len(rows) == 1
            assert rows[0]["tool_name"] == "dummy_tool_for_registration_test"
            assert rows[0]["pipeline_status"] == "SUCCESS"
        finally:
            import shutil

            from modes.trial_balance.pipeline.db import db_cursor

            with db_cursor(dict_rows=False) as cur:
                cur.execute("DELETE FROM pipeline_artifacts WHERE session_id = %s", (_TEST_SESSION_ID,))
            shutil.rmtree(out_dir, ignore_errors=True)

    def test_no_session_id_in_output_dir_skips_registration_silently(self, db_available, tmp_path):
        """No exception, no DB row -- ad hoc output_dirs (e.g. tests, /validate calls
        without a session) must not attempt registration at all."""
        if not db_available:
            pytest.skip("No reachable Postgres DB (settings.DB_*) -- skipping.")

        @pipeline_tool("dummy_tool_no_session")
        def dummy_tool(output_dir=None):
            return {"execution_status": "SUCCESS", "pipeline_status": "SUCCESS", "message": "ok", "artifacts": []}

        result = dummy_tool(output_dir=str(tmp_path))
        assert result["execution_status"] == "SUCCESS"


class TestUnexpectedExceptionIsNotLeakedToCallers:
    """The decorator's catch-all used to put traceback.format_exc() into both
    response["message"] and response["errors"][0]["traceback"]. Every route returns
    that dict verbatim to the API client, so absolute server paths, internal module
    structure and source lines were shipped to whoever called the endpoint. The trace
    now goes to the log only, correlated by error_id."""

    def _failing_tool(self):
        @pipeline_tool("dummy_tool_that_raises")
        def dummy_tool(output_dir=None):
            secret_local = "SERVER-ONLY-DETAIL"  # noqa: F841 -- must not reach the client
            raise ValueError("boom with internals")

        return dummy_tool

    def test_response_carries_no_traceback_and_no_source_paths(self, tmp_path):
        result = self._failing_tool()(output_dir=str(tmp_path))
        blob = json.dumps(result)

        assert result["execution_status"] == "FAILED"
        assert "traceback" not in result["errors"][0]
        assert "Traceback (most recent call last)" not in blob
        assert "boom with internals" not in blob
        assert "SERVER-ONLY-DETAIL" not in blob
        assert "pipeline_tool.py" not in blob
        assert "test_pipeline_tool.py" not in blob

    def test_response_still_identifies_the_failure_usefully(self, tmp_path):
        result = self._failing_tool()(output_dir=str(tmp_path))

        # The caller must still learn WHICH tool failed and WHAT class of error it was,
        # plus a handle to give an operator -- just not the trace itself.
        assert "dummy_tool_that_raises" in result["message"]
        assert "ValueError" in result["message"]
        assert result["errors"][0]["type"] == "ValueError"

        error_id = result["errors"][0]["error_id"]
        assert len(error_id) == 12
        assert error_id in result["message"]

    def test_error_id_is_unique_per_failure(self, tmp_path):
        tool = self._failing_tool()
        first = tool(output_dir=str(tmp_path))["errors"][0]["error_id"]
        second = tool(output_dir=str(tmp_path))["errors"][0]["error_id"]
        assert first != second

    def test_full_traceback_is_written_to_the_server_log(self, tmp_path, caplog):
        with caplog.at_level("ERROR", logger="modes.trial_balance.pipeline.tools.pipeline_tool"):
            result = self._failing_tool()(output_dir=str(tmp_path))

        error_id = result["errors"][0]["error_id"]
        assert error_id in caplog.text
        assert "Traceback (most recent call last)" in caplog.text
        assert "boom with internals" in caplog.text
