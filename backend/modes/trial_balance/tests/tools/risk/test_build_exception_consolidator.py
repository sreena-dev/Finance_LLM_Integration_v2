"""Regression test for build_exception_consolidator.py: materiality_file,
financial_snapshot_file, risk_indicators_file, sensitive_accounts_file,
relationship_analytics_file, and relationship_graph_file used to be required
positional-or-keyword params with no default -- omitting even one raised a
raw Python TypeError (caught by pipeline_tool's generic exception handler as
an opaque "Unexpected exception" traceback), which is exactly what happened
in a real /audit run once every upstream tool had produced its artifact but
the LLM didn't enumerate all 6 file paths in one tool call. They now default
to their canonical filename inside output_dir, matching the convention every
other multi-input risk/materiality tool in this chain already uses."""

import json

import polars as pl
import pytest

from modes.trial_balance.pipeline.tools import build_exception_consolidator


@pytest.fixture
def minimal_pipeline_artifacts(make_canonical_tb, tmp_path):
    canonical_tb_file = make_canonical_tb(
        [{"gl_code": "1001", "gl_name": "Cash", "closing_balance": 1000.0, "main_head": "Current assets"}]
    )
    for name in (
        "materiality.json",
        "financial_snapshot.json",
        "risk_indicators.json",
        "sensitive_accounts.json",
        "relationship_analytics.json",
        "relationship_graph.json",
    ):
        with open(tmp_path / name, "w") as f:
            json.dump({}, f)
    return canonical_tb_file


def test_succeeds_with_only_canonical_tb_and_output_dir(minimal_pipeline_artifacts, tmp_path):
    """Previously raised TypeError: missing required positional arguments."""
    result = build_exception_consolidator(canonical_tb_file=str(minimal_pipeline_artifacts), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"


def test_explicit_file_paths_still_work(minimal_pipeline_artifacts, tmp_path):
    result = build_exception_consolidator(
        canonical_tb_file=str(minimal_pipeline_artifacts),
        materiality_file=str(tmp_path / "materiality.json"),
        financial_snapshot_file=str(tmp_path / "financial_snapshot.json"),
        risk_indicators_file=str(tmp_path / "risk_indicators.json"),
        sensitive_accounts_file=str(tmp_path / "sensitive_accounts.json"),
        relationship_analytics_file=str(tmp_path / "relationship_analytics.json"),
        relationship_graph_file=str(tmp_path / "relationship_graph.json"),
        output_dir=str(tmp_path),
    )
    assert result["execution_status"] == "SUCCESS"


def test_account_sensitivity_key_produces_exceptions(make_canonical_tb, tmp_path):
    """build_sensitive_detector.py writes its per-account list under
    "account_sensitivity", not "sensitive_accounts" -- reading the wrong key
    silently produced zero sensitivity-sourced exceptions on every real run,
    even with a Critical-priority sensitive account present."""
    canonical_tb_file = make_canonical_tb(
        [{"gl_code": "190591", "gl_name": "JV Vendor", "closing_balance": -107664557316.32, "main_head": "Non-current assets"}]
    )
    for name in ("materiality.json", "financial_snapshot.json", "risk_indicators.json", "relationship_analytics.json", "relationship_graph.json"):
        with open(tmp_path / name, "w") as f:
            json.dump({}, f)
    with open(tmp_path / "sensitive_accounts.json", "w") as f:
        json.dump({
            "account_sensitivity": [
                {"gl_code": "190591", "gl_name": "JV Vendor", "account": "190591 - JV Vendor",
                 "category": "related_party", "sensitivity_score": 80, "priority": "Critical"},
            ]
        }, f)

    result = build_exception_consolidator(canonical_tb_file=str(canonical_tb_file), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"
    assert "1 canonical exception" in result["message"]

    with open(tmp_path / "consolidated_exceptions.json") as f:
        payload = json.load(f)
    assert len(payload.get("exceptions", [])) == 1


def test_critical_risks_key_produces_exceptions(make_canonical_tb, tmp_path):
    """build_risk_indicators.py writes its escalated per-account entries under
    "critical_risks", not "indicators" -- reading the wrong key silently
    produced zero risk-sourced exceptions on every real run."""
    canonical_tb_file = make_canonical_tb(
        [{"gl_code": "2001", "gl_name": "Suspense Account", "closing_balance": 500000.0, "main_head": "Current liabilities"}]
    )
    for name in ("materiality.json", "financial_snapshot.json", "sensitive_accounts.json", "relationship_analytics.json", "relationship_graph.json"):
        with open(tmp_path / name, "w") as f:
            json.dump({}, f)
    with open(tmp_path / "risk_indicators.json", "w") as f:
        json.dump({
            "critical_risks": [
                {"account": "2001 - Suspense Account", "fsli": "Current liabilities", "score": 95, "risk_level": "Critical"},
            ]
        }, f)

    result = build_exception_consolidator(canonical_tb_file=str(canonical_tb_file), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"
    assert "1 canonical exception" in result["message"]


def test_wrong_extension_arg_corrected_instead_of_crashing(minimal_pipeline_artifacts, tmp_path):
    """A real /audit run against gemma-4-26b showed the LLM passing
    financial_snapshot.parquet (binary) into financial_snapshot_file, which
    only ever expects the .json sibling -- _load_json's json.load() on binary
    Parquet bytes crashed with a raw UnicodeDecodeError, surfaced as an opaque
    "Unexpected exception" traceback. financial_snapshot_file is now resolved
    through resolve_artifact_path, which corrects back to the canonical .json
    file in output_dir when the wrong-extension path exists alongside it."""
    pl.DataFrame({"x": [1]}).write_parquet(tmp_path / "financial_snapshot.parquet")

    result = build_exception_consolidator(
        canonical_tb_file=str(minimal_pipeline_artifacts),
        financial_snapshot_file=str(tmp_path / "financial_snapshot.parquet"),
        output_dir=str(tmp_path),
    )
    assert result["execution_status"] == "SUCCESS"
