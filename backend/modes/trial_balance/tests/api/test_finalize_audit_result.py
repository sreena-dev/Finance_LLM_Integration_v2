"""Covers backend.routes._finalize_audit_result's COMPARISON-mode
parsing of comparison_reasoning.json into result["findings"]/["findings_summary"]/
["context_note"] -- previously a confirmed gap (the route explicitly skipped
COMPARISON's reasoning file, so AuditCard's Focus Areas always rendered empty
for comparison runs)."""

import json

import polars as pl

from modes.trial_balance.router import _finalize_audit_result


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def test_finalize_audit_result_parses_comparison_reasoning(tmp_path):
    run_dir = tmp_path / "run"
    cy_dir = run_dir / "cy"
    comparison_dir = run_dir / "comparison"
    cy_dir.mkdir(parents=True)
    comparison_dir.mkdir(parents=True)

    pl.DataFrame({"gl_code": ["1"], "closing_balance": [100.0]}).write_parquet(
        cy_dir / "canonical_tb.parquet"
    )

    _write_json(
        comparison_dir / "comparison_reasoning.json",
        {
            "summary": {"executive_summary": "Two comparison observations were generated."},
            "observations": [
                {
                    "observation": {
                        "priority": "High",
                        "title": "Continuity Break",
                        "detailed_observation": "GL 1001 shows a PY-closing to CY-opening mismatch.",
                        "recommended_procedures": ["Obtain roll-forward schedule."],
                    }
                },
                {
                    "observation": {
                        "priority": "Medium",
                        "title": "Structural Delta",
                        "executive_summary": "3 new ledgers appeared in CY.",
                        "recommended_procedures": ["Obtain chart-of-accounts change log."],
                    }
                },
            ],
        },
    )

    result = _finalize_audit_result("COMPARISON", run_dir)

    assert result["context_note"] == "Two comparison observations were generated."
    assert result["findings_summary"] == {"high": 1, "medium": 1}
    assert len(result["findings"]) == 2

    first = result["findings"][0]
    assert first["risk_rating"] == "high"
    assert first["reference_id"] == "F01"
    assert first["observation"] == "GL 1001 shows a PY-closing to CY-opening mismatch."
    assert first["evidence_requested"] == ["Obtain roll-forward schedule."]

    second = result["findings"][1]
    assert second["reference_id"] == "F02"
    assert second["observation"] == "3 new ledgers appeared in CY."


def test_finalize_audit_result_comparison_missing_reasoning_file_is_graceful(tmp_path):
    run_dir = tmp_path / "run"
    cy_dir = run_dir / "cy"
    cy_dir.mkdir(parents=True)
    pl.DataFrame({"gl_code": ["1"], "closing_balance": [100.0]}).write_parquet(
        cy_dir / "canonical_tb.parquet"
    )

    result = _finalize_audit_result("COMPARISON", run_dir)

    assert "findings" not in result
    assert "findings_summary" not in result
