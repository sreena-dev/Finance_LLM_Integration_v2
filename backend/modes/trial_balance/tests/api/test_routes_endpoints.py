"""HTTP-level tests for the 13 route handlers in backend/routes.py that previously had
zero direct test coverage (existing tests only exercised the private orchestration
helpers _run_core_analytics_chain/_finalize_audit_result via direct calls, never the
FastAPI route functions themselves through a real request/response cycle).

Uses a minimal FastAPI app wrapping just backend.routes.router -- not backend.main's
app -- so these tests don't trigger backend.main's Phoenix tracing/verify_packs
module-level side effects. call_tool/get_agent/the db session-store functions are
mocked throughout: these are routing-and-response-shape tests, not integration tests
for the tools or the database (those are covered elsewhere)."""

import io
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytest.importorskip(
    "yukta",
    reason="yukta is installed from a local path and published to no index, so it is "
           "absent on a clean checkout -- see requirements.txt. These tests import "
           "backend.agent, which needs it.",
)

from modes.trial_balance.pipeline.agent import ToolNotAvailableError
from modes.trial_balance.router import router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestHealth:
    def test_health_returns_ok_with_storage_block(self, client):
        """/health now reports per-engine storage status (backend/health_checks.py)
        alongside the original top-level fields -- exact status of each engine
        depends on what's actually reachable in the test environment (Postgres
        real/reachable, Valkey/MinIO disabled or unreachable), so this asserts
        shape and the presence of all four engines, not fixed status values."""
        resp = client.get("/api/trial-balance/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == "trial-balance"
        assert body["available"] is True
        assert body["status"] in ("ok", "degraded")
        assert set(body["storage"].keys()) == {"postgres", "valkey", "minio", "duckdb"}
        for engine, result in body["storage"].items():
            assert result["status"] in ("ok", "disabled", "error"), f"{engine} had unexpected status {result['status']!r}"


class TestUpload:
    def test_successful_upload_returns_token_and_preview(self, client):
        fake_result = {"execution_status": "SUCCESS", "artifacts": [], "sheets": ["Sheet1"]}
        with patch("modes.trial_balance.router.call_tool", return_value=fake_result) as m:
            resp = client.post(
                "/api/trial-balance/upload", files={"file": ("tb.xlsx", io.BytesIO(b"fake-bytes"), "application/octet-stream")}
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["filename"] == "tb.xlsx"
        assert "token" in body and body["token"]
        assert body["execution_status"] == "SUCCESS"
        m.assert_called_once()
        assert m.call_args.args[0] == "preview_excel_data"

    def test_upload_needing_mapping_returns_422_with_preview_token(self, client):
        fake_result = {"execution_status": "NEEDS_MAPPING", "message": "Ambiguous columns"}
        with patch("modes.trial_balance.router.call_tool", return_value=fake_result):
            resp = client.post(
                "/api/trial-balance/upload", files={"file": ("tb.xlsx", io.BytesIO(b"fake-bytes"), "application/octet-stream")}
            )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["needs_mapping"] is True
        assert "preview_token" in detail

    def test_upload_when_preview_tool_unavailable_returns_424(self, client):
        with patch("modes.trial_balance.router.call_tool", side_effect=ToolNotAvailableError("preview_excel_data")):
            resp = client.post(
                "/api/trial-balance/upload", files={"file": ("tb.xlsx", io.BytesIO(b"fake-bytes"), "application/octet-stream")}
            )
        assert resp.status_code == 424


class TestPreview:
    def test_unknown_token_returns_404(self, client):
        resp = client.get("/api/trial-balance/preview", params={"token": "no-such-token"})
        assert resp.status_code == 404

    def test_known_token_returns_its_stored_preview(self, client):
        fake_result = {"execution_status": "SUCCESS", "sheets": ["Sheet1"]}
        with patch("modes.trial_balance.router.call_tool", return_value=fake_result):
            upload_resp = client.post(
                "/api/trial-balance/upload", files={"file": ("tb.xlsx", io.BytesIO(b"x"), "application/octet-stream")}
            )
        token = upload_resp.json()["token"]
        resp = client.get("/api/trial-balance/preview", params={"token": token})
        assert resp.status_code == 200
        assert resp.json()["execution_status"] == "SUCCESS"


def _upload_tb(client) -> str:
    with patch("modes.trial_balance.router.call_tool", return_value={"execution_status": "SUCCESS", "sheets": []}):
        resp = client.post("/api/trial-balance/upload", files={"file": ("tb.xlsx", io.BytesIO(b"x"), "application/octet-stream")})
    return resp.json()["token"]


def _upload_grouping(client) -> str:
    with patch("modes.trial_balance.router.call_tool", return_value={"execution_status": "SUCCESS", "sheets": []}):
        resp = client.post(
            "/api/trial-balance/audit/upload-grouping",
            files={"file": ("grouping.xlsx", io.BytesIO(b"x"), "application/octet-stream")},
        )
    return resp.json()["token"]


class TestUploadMapped:
    def test_unknown_token_returns_404(self, client):
        resp = client.post("/api/trial-balance/upload-mapped", json={"token": "no-such-token", "column_mapping": {}})
        assert resp.status_code == 404

    def test_missing_grouping_token_is_no_longer_an_error(self, client):
        """grouping_token is genuinely optional: ingest_tb_to_live can parse a
        single self-contained workbook (Scenario A/C) with no second file at
        all, the same way a Live template submission can. Omitting it must
        reach the tool with grouping_file_path=None, not a 400."""
        token = _upload_tb(client)
        ingest_result = {"execution_status": "SUCCESS", "artifacts": ["canonical_tb.parquet"], "tb_doc_id": "X"}
        validation_result = {"execution_status": "SUCCESS", "artifacts": ["layer1_results.json"]}
        with patch("modes.trial_balance.router.call_tool", side_effect=[ingest_result, validation_result]) as m:
            resp = client.post("/api/trial-balance/upload-mapped", json={"token": token, "column_mapping": {}})
        assert resp.status_code == 200
        assert m.call_args_list[0].kwargs["grouping_file_path"] is None

    def test_unknown_grouping_token_returns_404(self, client):
        token = _upload_tb(client)
        resp = client.post(
            "/api/trial-balance/upload-mapped",
            json={"token": token, "column_mapping": {}, "grouping_token": "no-such-token"},
        )
        assert resp.status_code == 404
        assert "grouping token" in resp.json()["detail"].lower()

    def test_known_token_processes_and_validates(self, client):
        token = _upload_tb(client)
        grouping_token = _upload_grouping(client)

        ingest_result = {"execution_status": "SUCCESS", "artifacts": ["canonical_tb.parquet"], "tb_doc_id": "X"}
        validation_result = {"execution_status": "SUCCESS", "artifacts": ["layer1_results.json"]}
        with patch("modes.trial_balance.router.call_tool", side_effect=[ingest_result, validation_result]) as m:
            resp = client.post(
                "/api/trial-balance/upload-mapped",
                json={
                    "token": token,
                    "column_mapping": {"A": "gl_code"},
                    "grouping_token": grouping_token,
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["execution_status"] == "SUCCESS"
        assert body["layer1_validation"]["execution_status"] == "SUCCESS"
        assert m.call_args_list[0].args[0] == "ingest_tb_to_live"
        assert m.call_args_list[1].args[0] == "validate_layer1_tb"

    def test_grouping_path_is_actually_passed_to_the_tool(self, client):
        """The specific defect: grouping_file_path must reach ingest_tb_to_live,
        and must be the grouping upload's path, not the TB's."""
        token = _upload_tb(client)
        grouping_token = _upload_grouping(client)

        with patch("modes.trial_balance.router.call_tool", return_value={"execution_status": "FAILED"}) as m:
            client.post(
                "/api/trial-balance/upload-mapped",
                json={"token": token, "column_mapping": {}, "grouping_token": grouping_token},
            )
        kwargs = m.call_args_list[0].kwargs
        assert kwargs["grouping_file_path"].endswith("grouping.xlsx")
        assert kwargs["tb_grouping_template_path"].endswith("tb.xlsx")
        assert kwargs["grouping_file_path"] != kwargs["tb_grouping_template_path"]

    def test_artifacts_are_written_under_the_configured_output_dir(self, client):
        """Regression: the output_dir was the bare token, and resolve_output_dir()
        anchors a relative path to the REPO ROOT -- so every upload dropped a stray
        <repo_root>/<uuid>/ directory of parquet and JSON outside OUTPUT_DIR entirely.
        Artifacts must stay inside the configured output tree, beside the uploaded
        source files they were derived from."""
        from modes.trial_balance.pipeline.config import settings

        token = _upload_tb(client)
        grouping_token = _upload_grouping(client)

        with patch("modes.trial_balance.router.call_tool", return_value={"execution_status": "FAILED"}) as m:
            client.post(
                "/api/trial-balance/upload-mapped",
                json={"token": token, "column_mapping": {}, "grouping_token": grouping_token},
            )

        output_dir = Path(m.call_args_list[0].kwargs["output_dir"]).resolve()
        assert output_dir.is_relative_to(Path(settings.OUTPUT_DIR).resolve())
        assert output_dir.name == token

    def test_processing_failure_skips_validation(self, client):
        token = _upload_tb(client)
        grouping_token = _upload_grouping(client)

        with patch("modes.trial_balance.router.call_tool", return_value={"execution_status": "FAILED"}) as m:
            resp = client.post(
                "/api/trial-balance/upload-mapped",
                json={"token": token, "column_mapping": {}, "grouping_token": grouping_token},
            )
        assert resp.status_code == 200
        assert resp.json()["execution_status"] == "FAILED"
        assert "layer1_validation" not in resp.json()
        m.assert_called_once()  # ingest_tb_to_live only -- validate_layer1_tb never called


class TestDocumentsList:
    def test_lists_documents(self, client):
        fake = {"execution_status": "SUCCESS", "documents": [{"tb_doc_id": "D1"}]}
        with patch("modes.trial_balance.router.call_tool", return_value=fake) as m:
            resp = client.get("/api/trial-balance/documents")
        assert resp.status_code == 200
        assert resp.json() == fake
        assert m.call_args.args[0] == "list_db_documents"

    def test_tool_unavailable_returns_424(self, client):
        with patch("modes.trial_balance.router.call_tool", side_effect=ToolNotAvailableError("list_db_documents")):
            resp = client.get("/api/trial-balance/documents")
        assert resp.status_code == 424


class TestDocumentGet:
    def test_not_found_returns_404(self, client):
        with patch("modes.trial_balance.router.call_tool", return_value={"execution_status": "FAILED", "message": "no such doc"}):
            resp = client.get("/api/trial-balance/documents/UNKNOWN")
        assert resp.status_code == 404

    def test_found_document_attaches_layer1_validation(self, client):
        load_result = {"execution_status": "SUCCESS", "artifacts": ["/tmp/x/canonical_tb.parquet"]}
        validation_result = {"execution_status": "SUCCESS"}
        with patch("modes.trial_balance.router.call_tool", side_effect=[load_result, validation_result]) as m:
            resp = client.get("/api/trial-balance/documents/D1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["layer1_validation"] == validation_result
        assert m.call_args_list[0].args[0] == "load_tb_from_db"
        assert m.call_args_list[1].args[0] == "validate_layer1_tb"


class TestDocumentDelete:
    def test_deletes_and_returns_the_tool_result(self, client):
        fake = {"execution_status": "SUCCESS"}
        with patch("modes.trial_balance.router.call_tool", return_value=fake) as m:
            resp = client.delete("/api/trial-balance/documents/D1")
        assert resp.status_code == 200
        assert resp.json() == fake
        assert m.call_args.args[0] == "delete_db_document"
        assert m.call_args.kwargs["tb_doc_id"] == "D1"


class TestAsk:
    def test_successful_answer_records_success_session(self, client):
        with patch("modes.trial_balance.router.create_session", return_value="sess-1") as m_create, \
             patch("modes.trial_balance.router.update_session_status") as m_update, \
             patch("modes.trial_balance.router.llm_reachable", return_value=True), \
             patch("modes.trial_balance.router.get_agent") as m_agent:
            m_agent.return_value.invoke.return_value = "The answer is 42."
            resp = client.post("/api/trial-balance/ask", json={"doc_id": "D1", "question": "What is the answer?"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["answer"] == "The answer is 42."
        assert body["session_id"] == "sess-1"
        assert body["available"] is True
        m_create.assert_called_once()
        m_update.assert_called_once_with("sess-1", "SUCCESS")

    def test_agent_failure_records_failed_session_and_error_response(self, client):
        with patch("modes.trial_balance.router.create_session", return_value="sess-2"), \
             patch("modes.trial_balance.router.update_session_status") as m_update, \
             patch("modes.trial_balance.router.llm_reachable", return_value=True), \
             patch("modes.trial_balance.router.get_agent") as m_agent:
            m_agent.return_value.invoke.side_effect = RuntimeError("LLM exploded")
            resp = client.post("/api/trial-balance/ask", json={"doc_id": "D1", "question": "Anything?"})
        assert resp.status_code == 500
        m_update.assert_called_once_with("sess-2", "FAILED", error_message="LLM exploded")
        # The session record keeps the real message for operators; the client must not.
        body = resp.json()
        assert "LLM exploded" not in str(body)
        assert "error_id" in body

    def test_unreachable_llm_answers_plainly_without_leaking_the_endpoint(self, client):
        """Regression: the agent swallows connection failures and returns the raw error
        text (including the internal LLM host:port) as its answer, which was handed
        straight to the end user as the chat response."""
        with patch("modes.trial_balance.router.create_session", return_value="sess-down"), \
             patch("modes.trial_balance.router.update_session_status"), \
             patch("modes.trial_balance.router.llm_reachable", return_value=False), \
             patch("modes.trial_balance.router.get_agent") as m_agent:
            resp = client.post("/api/trial-balance/ask", json={"doc_id": "D1", "question": "Anything?"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert "temporarily unavailable" in body["answer"]
        assert "30004" not in body["answer"]  # no endpoint port
        assert "http" not in body["answer"].lower()
        m_agent.assert_not_called()  # never start a doomed agent loop

    def test_session_creation_failure_degrades_gracefully(self, client):
        """create_session() is a live DB write and sat outside the handler's try block,
        so a Postgres hiccup at that instant produced a bare unhandled 500."""
        with patch("modes.trial_balance.router.create_session", side_effect=RuntimeError("db down")):
            resp = client.post("/api/trial-balance/ask", json={"doc_id": "D1", "question": "Anything?"})
        assert resp.status_code == 500
        assert "db down" not in str(resp.json())


class TestAskGeneral:
    def test_successful_answer(self, client):
        with patch("modes.trial_balance.router.create_session", return_value="sess-3"), \
             patch("modes.trial_balance.router.update_session_status") as m_update, \
             patch("modes.trial_balance.router.llm_reachable", return_value=True), \
             patch("modes.trial_balance.router.get_agent") as m_agent:
            m_agent.return_value.invoke.return_value = "General answer."
            resp = client.post("/api/trial-balance/ask-general", json={"question": "What is ISA 320?"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["answer"] == "General answer."
        assert body["session_id"] == "sess-3"
        assert body["available"] is True
        m_update.assert_called_once_with("sess-3", "SUCCESS")

    def test_unreachable_llm_answers_plainly(self, client):
        with patch("modes.trial_balance.router.create_session", return_value="sess-4"), \
             patch("modes.trial_balance.router.update_session_status"), \
             patch("modes.trial_balance.router.llm_reachable", return_value=False), \
             patch("modes.trial_balance.router.get_agent") as m_agent:
            resp = client.post("/api/trial-balance/ask-general", json={"question": "What is ISA 320?"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert "temporarily unavailable" in body["answer"]
        m_agent.assert_not_called()


class TestValidate:
    def test_load_failure_returns_the_tool_result_directly(self, client):
        fake = {"execution_status": "FAILED", "message": "doc not found"}
        with patch("modes.trial_balance.router.call_tool", return_value=fake):
            resp = client.post("/api/trial-balance/validate", json={"doc_id": "UNKNOWN"})
        assert resp.status_code == 200
        assert resp.json() == fake

    def test_successful_load_runs_layer1_validation(self, client):
        load_result = {"execution_status": "SUCCESS", "artifacts": ["/tmp/x/canonical_tb.parquet"]}
        validation_result = {"execution_status": "SUCCESS", "rule_results": []}
        with patch("modes.trial_balance.router.call_tool", side_effect=[load_result, validation_result]) as m:
            resp = client.post("/api/trial-balance/validate", json={"doc_id": "D1"})
        assert resp.status_code == 200
        assert resp.json() == validation_result
        assert m.call_args_list[1].args[0] == "validate_layer1_tb"


class TestAuditUploadGrouping:
    def test_successful_upload(self, client):
        fake_result = {"execution_status": "SUCCESS", "sheets": []}
        with patch("modes.trial_balance.router.call_tool", return_value=fake_result) as m:
            resp = client.post(
                "/api/trial-balance/audit/upload-grouping",
                files={"file": ("grouping.xlsx", io.BytesIO(b"x"), "application/octet-stream")},
                data={"doc_id": "D1"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "token" in body
        assert m.call_args.kwargs.get("is_grouping") is True


class TestAudit:
    """The /audit handler is the most complex route -- covers both the llm_reachable()
    skip path (the exact behavior hardened earlier this session to fix the comparative-
    audit hang) and the normal path where the agent is invoked."""

    def _patch_common(self):
        return (
            patch("modes.trial_balance.router.create_session", return_value="sess-audit"),
            patch("modes.trial_balance.router.update_session_status"),
            patch("modes.trial_balance.router._run_layer1_precheck", return_value={"execution_status": "SUCCESS"}),
            patch("modes.trial_balance.router._finalize_audit_result", return_value={"report": "ok"}),
        )

    def test_session_creation_failure_degrades_gracefully(self, client):
        """create_session() sat above this handler's try block, so a DB outage at that
        exact call produced FastAPI's bare unhandled-exception 500 instead of the
        graceful degradation every other failure path here gets. Observed live during
        a real Postgres hiccup, not hypothetical."""
        with patch("modes.trial_balance.router.create_session", side_effect=RuntimeError("connection to server failed")):
            resp = client.post("/api/trial-balance/audit", json={"doc_id": "D1"})
        assert resp.status_code == 500
        body = resp.json()
        assert "connection to server failed" not in str(body)
        assert "error_id" in body

    def test_single_tb_skips_agent_when_llm_unreachable(self, client):
        p_create, p_update, p_precheck, p_finalize = self._patch_common()
        with p_create, p_update, p_precheck, p_finalize, \
             patch("modes.trial_balance.router.llm_reachable", return_value=False), \
             patch("modes.trial_balance.router.get_agent") as m_agent:
            resp = client.post("/api/trial-balance/audit", json={"doc_id": "D1"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == "SINGLE_TB"
        assert body["result"] == {"report": "ok"}
        m_agent.return_value.invoke.assert_not_called()

    def test_single_tb_invokes_agent_when_llm_reachable(self, client):
        p_create, p_update, p_precheck, p_finalize = self._patch_common()
        with p_create, p_update, p_precheck, p_finalize, \
             patch("modes.trial_balance.router.llm_reachable", return_value=True), \
             patch("modes.trial_balance.router.get_agent") as m_agent:
            resp = client.post("/api/trial-balance/audit", json={"doc_id": "D1"})
        assert resp.status_code == 200
        m_agent.return_value.invoke.assert_called_once()

    def test_comparison_mode_selected_when_doc_id_prior_given(self, client):
        p_create, p_update, p_precheck, p_finalize = self._patch_common()
        with p_create, p_update, p_precheck, p_finalize, \
             patch("modes.trial_balance.router.llm_reachable", return_value=False), \
             patch("modes.trial_balance.router.get_agent"):
            resp = client.post("/api/trial-balance/audit", json={"doc_id": "CY", "doc_id_prior": "PY"})
        assert resp.status_code == 200
        assert resp.json()["mode"] == "COMPARISON"

    def test_exception_during_audit_records_failed_session(self, client):
        with patch("modes.trial_balance.router.create_session", return_value="sess-fail"), \
             patch("modes.trial_balance.router.update_session_status") as m_update, \
             patch("modes.trial_balance.router._run_layer1_precheck", side_effect=RuntimeError("boom")):
            resp = client.post("/api/trial-balance/audit", json={"doc_id": "D1"})
        assert resp.status_code == 500
        m_update.assert_called_once_with("sess-fail", "FAILED", error_message="boom")


class TestAuditWorkbook:
    def test_no_session_found_returns_404(self, client):
        with patch("modes.trial_balance.router.find_latest_session", return_value=None):
            resp = client.post("/api/trial-balance/audit/workbook", json={"doc_id": "UNKNOWN"})
        assert resp.status_code == 404

    def test_missing_report_file_returns_404(self, client, tmp_path):
        with patch("modes.trial_balance.router.find_latest_session", return_value={"session_id": "sess-x"}), \
             patch("modes.trial_balance.router.resolve_output_dir", return_value=tmp_path):
            resp = client.post("/api/trial-balance/audit/workbook", json={"doc_id": "D1", "format": "xlsx"})
        assert resp.status_code == 404

    def test_unsupported_format_returns_400(self, client, tmp_path):
        with patch("modes.trial_balance.router.find_latest_session", return_value={"session_id": "sess-x"}), \
             patch("modes.trial_balance.router.resolve_output_dir", return_value=tmp_path):
            resp = client.post("/api/trial-balance/audit/workbook", json={"doc_id": "D1", "format": "pdf"})
        assert resp.status_code == 400

    def test_existing_report_file_is_returned(self, client, tmp_path):
        (tmp_path / "TB_Audit.xlsx").write_bytes(b"fake-xlsx-bytes")
        with patch("modes.trial_balance.router.find_latest_session", return_value={"session_id": "sess-x"}), \
             patch("modes.trial_balance.router.resolve_output_dir", return_value=tmp_path):
            resp = client.post("/api/trial-balance/audit/workbook", json={"doc_id": "D1", "format": "xlsx"})
        assert resp.status_code == 200
        assert resp.content == b"fake-xlsx-bytes"
        assert resp.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
