"""Tests for backend/tools/reasoning/build_audit_reasoning.py's report-wiring
fix: the tool used to write audit_reasoning_payload.json with no
`audit_observations` key, while build_docx_report.py/build_report_markdown.py
both default-read audit_reasoning.json and look for
`audit_observations[].observation` -- so the LLM/deterministic narrative
never reached those reports. This pins the corrected filename + shape.

Fixtures build consolidated_exceptions.json/validation_report.json directly
(matching build_audit_reasoning's own documented read contract), rather than
running the full upstream chain -- isolates this tool the same way
test_custom_field_passthrough.py isolates build_canonical_tb."""

import json

import pytest

from modes.trial_balance.pipeline.tools import build_audit_reasoning


@pytest.fixture
def gated_pipeline_artifacts(tmp_path):
    validation_report = {
        "report_readiness": {"status": "READY"},
        "reasoning_readiness": {"status": "LLM Ready"},
    }
    val_path = tmp_path / "validation_report.json"
    with open(val_path, "w") as f:
        json.dump(validation_report, f)

    consolidated_exceptions = {
        "exceptions": [
            {
                "metadata": {"exception_id": "EXC1"},
                "audit_planning": {
                    "audit_assertions": ["Existence", "Valuation"],
                    "required_evidence": ["Bank confirmation", "Reconciliation"],
                },
            }
        ],
        "clusters": [
            {
                "cluster_id": "CL1",
                "cluster_name": "Suspense Account Cluster",
                "business_process": "Treasury",
                "fsli": "Cash and Cash Equivalents",
                "cluster_severity": "High",
                "cluster_score": 0.9,
                "member_count": 1,
                "critical_count": 0,
                "high_count": 1,
                "total_balance": 50000.0,
                "risk_themes": ["Large suspense balance"],
                "member_exceptions": ["EXC1"],
            }
        ],
    }
    exc_path = tmp_path / "consolidated_exceptions.json"
    with open(exc_path, "w") as f:
        json.dump(consolidated_exceptions, f)

    return {"validation_report_file": str(val_path), "consolidated_exceptions_file": str(exc_path)}


def test_writes_audit_reasoning_json_not_payload_filename(gated_pipeline_artifacts, tmp_path):
    result = build_audit_reasoning(
        consolidated_exceptions_file=gated_pipeline_artifacts["consolidated_exceptions_file"],
        validation_report_file=gated_pipeline_artifacts["validation_report_file"],
        output_dir=str(tmp_path),
    )
    assert result["execution_status"] == "SUCCESS"
    assert (tmp_path / "audit_reasoning.json").exists()
    assert not (tmp_path / "audit_reasoning_payload.json").exists()
    assert result["artifacts"] == [str(tmp_path / "audit_reasoning.json")]


def test_audit_observations_shaped_for_report_builders(gated_pipeline_artifacts, tmp_path):
    build_audit_reasoning(
        consolidated_exceptions_file=gated_pipeline_artifacts["consolidated_exceptions_file"],
        validation_report_file=gated_pipeline_artifacts["validation_report_file"],
        output_dir=str(tmp_path),
    )
    with open(tmp_path / "audit_reasoning.json") as f:
        payload = json.load(f)

    assert "audit_observations" in payload
    assert len(payload["audit_observations"]) == 1

    # Exact access pattern build_docx_report.py / build_report_markdown.py use:
    observations = [o.get("observation", {}) for o in payload["audit_observations"]]
    assert len(observations) == 1
    obs = observations[0]
    assert obs["cluster_id"] == "CL1"
    assert obs["priority"] == "High"
    assert obs["title"] == "Suspense Account Cluster"
    assert obs["detailed_observation"]
    assert obs["safe_limitation"]  # disclaimer carried through from the finding
    assert "Existence" in obs["affected_assertions"] or "Valuation" in obs["affected_assertions"]
