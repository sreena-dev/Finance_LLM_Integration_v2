"""Tests for backend/tools/reasoning/validate_tb_pipeline.py's fixed checks:
(1) the expected_files list previously named the wrong filename
("relationship_analysis.json" vs. what build_relationship_analytics.py
actually writes, "relationship_analytics.json") -- patched with a defensive
existence fallback rather than fixed at the source; (2) the financial
integrity check previously read a "metric"/"balance" keyed structure from
financial_snapshot.json that tool never actually writes, so fin_status was
always "UNKNOWN" -- now reads financial_snapshot_statistics.json's real
total_assets/total_liabilities/total_equity keys instead; (3) unconditionally
requiring processing_manifest.json/grouping_ground_truth.parquet (upload-path
-only artifacts) made every DB-sourced (load_tb_from_db) SINGLE_TB run fail
pipeline integrity by construction -- now only required when mapping_summary.
json/processing_manifest.json indicate this was actually an upload run;
(4) the financial-integrity check compared only Assets vs Liabilities+Equity,
which fails on almost every real (unclosed/interim) trial balance since
current-period P&L hasn't been rolled into retained earnings yet -- now nets
Revenue+Expenses into the comparison, matching the whole-TB signed-sum
identity that holds whether or not the books have been closed."""

import json

import pytest

from modes.trial_balance.pipeline.tools import validate_tb_pipeline

_EXPECTED_FILES = [
    "processing_manifest.json",
    "grouping_ground_truth.parquet",
    "canonical_tb.parquet",
    "layer1_results.json",
    "financial_snapshot.json",
    "materiality.json",
    "relationship_analytics.json",
    "risk_indicators.json",
    "sensitive_accounts.json",
    "consolidated_exceptions.json",
]


def _touch_all_expected_files(out_dir):
    for f in _EXPECTED_FILES:
        p = out_dir / f
        if f.endswith(".parquet"):
            import polars as pl

            pl.DataFrame({"x": [1]}).write_parquet(p)
        else:
            with open(p, "w") as fh:
                json.dump({}, fh)


_DB_SOURCED_EXPECTED_FILES = [f for f in _EXPECTED_FILES if f not in ("processing_manifest.json", "grouping_ground_truth.parquet")]


def test_db_sourced_run_not_blocked_by_missing_upload_only_files(tmp_path):
    """A canonical TB from load_tb_from_db never produces processing_manifest.json or
    grouping_ground_truth.parquet -- their absence must not fail pipeline integrity."""
    for f in _DB_SOURCED_EXPECTED_FILES:
        p = tmp_path / f
        if f.endswith(".parquet"):
            import polars as pl

            pl.DataFrame({"x": [1]}).write_parquet(p)
        else:
            with open(p, "w") as fh:
                json.dump({}, fh)

    result = validate_tb_pipeline(output_dir=str(tmp_path))

    with open(tmp_path / "validation_report.json") as f:
        report = json.load(f)
    assert report["pipeline_integrity"]["missing_outputs"] == []
    assert report["pipeline_integrity"]["overall_status"] == "PASS"


def test_upload_sourced_run_still_requires_manifest_and_ground_truth(tmp_path):
    """When mapping_summary.json is present (an upload run happened), a genuinely
    missing processing_manifest.json must still be flagged, not silently skipped."""
    for f in _DB_SOURCED_EXPECTED_FILES:
        p = tmp_path / f
        if f.endswith(".parquet"):
            import polars as pl

            pl.DataFrame({"x": [1]}).write_parquet(p)
        else:
            with open(p, "w") as fh:
                json.dump({}, fh)
    with open(tmp_path / "mapping_summary.json", "w") as f:
        json.dump({}, f)
    # processing_manifest.json / grouping_ground_truth.parquet deliberately NOT created.

    result = validate_tb_pipeline(output_dir=str(tmp_path))

    with open(tmp_path / "validation_report.json") as f:
        report = json.load(f)
    assert "processing_manifest.json" in report["pipeline_integrity"]["missing_outputs"]
    assert "grouping_ground_truth.parquet" in report["pipeline_integrity"]["missing_outputs"]
    assert report["pipeline_integrity"]["overall_status"] == "FAILED"


def test_expected_files_accepts_relationship_analytics_filename(tmp_path):
    _touch_all_expected_files(tmp_path)
    result = validate_tb_pipeline(output_dir=str(tmp_path))

    with open(tmp_path / "validation_report.json") as f:
        report = json.load(f)
    assert report["pipeline_integrity"]["missing_outputs"] == []


def test_missing_relationship_analytics_is_flagged(tmp_path):
    _touch_all_expected_files(tmp_path)
    (tmp_path / "relationship_analytics.json").unlink()
    validate_tb_pipeline(output_dir=str(tmp_path))

    with open(tmp_path / "validation_report.json") as f:
        report = json.load(f)
    assert "relationship_analytics.json" in report["pipeline_integrity"]["missing_outputs"]


def test_financial_integrity_passes_on_balanced_statistics(tmp_path):
    """Debit-positive convention: assets positive, liabilities/equity negative --
    matches classify_row's actual sign output, not an arbitrary all-positive fixture."""
    _touch_all_expected_files(tmp_path)
    with open(tmp_path / "financial_snapshot_statistics.json", "w") as f:
        json.dump({"total_assets": 100000.0, "total_liabilities": -60000.0, "total_equity": -40000.0}, f)

    validate_tb_pipeline(output_dir=str(tmp_path))

    with open(tmp_path / "validation_report.json") as f:
        report = json.load(f)
    assert report["financial_validation"]["status"] == "PASS"


def test_financial_integrity_fails_on_unbalanced_statistics(tmp_path):
    _touch_all_expected_files(tmp_path)
    with open(tmp_path / "financial_snapshot_statistics.json", "w") as f:
        json.dump({"total_assets": 100000.0, "total_liabilities": -60000.0, "total_equity": 0.0}, f)

    validate_tb_pipeline(output_dir=str(tmp_path))

    with open(tmp_path / "validation_report.json") as f:
        report = json.load(f)
    assert report["financial_validation"]["status"] == "FAILED"


def test_unclosed_period_pl_nets_into_equity_and_still_passes(tmp_path):
    """A real, un-closed trial balance where current-period profit hasn't been rolled into
    retained earnings yet: Assets=100, Liabilities=-30, Equity=-20 (short by 50) is only
    "unbalanced" if Revenue/Expenses are ignored. With Revenue=-80, Expenses=30 (net period
    profit of 50, credit-signed), the full Assets+Liabilities+Equity+Revenue+Expenses
    identity sums to exactly zero -- this must PASS, not FAILED, on a genuinely balanced
    but unclosed TB."""
    _touch_all_expected_files(tmp_path)
    with open(tmp_path / "financial_snapshot_statistics.json", "w") as f:
        json.dump({
            "total_assets": 100.0,
            "total_liabilities": -30.0,
            "total_equity": -20.0,
            "total_revenue": -80.0,
            "total_expenses": 30.0,
        }, f)

    validate_tb_pipeline(output_dir=str(tmp_path))

    with open(tmp_path / "validation_report.json") as f:
        report = json.load(f)
    assert report["financial_validation"]["status"] == "PASS"


def test_genuine_imbalance_still_fails_even_with_pl_netted(tmp_path):
    """Netting P&L into equity must not mask a real imbalance -- if the full five-category
    sum still doesn't net to zero, that's a genuine data problem, not an unclosed-books
    false positive."""
    _touch_all_expected_files(tmp_path)
    with open(tmp_path / "financial_snapshot_statistics.json", "w") as f:
        json.dump({
            "total_assets": 100.0,
            "total_liabilities": -30.0,
            "total_equity": -20.0,
            "total_revenue": -10.0,
            "total_expenses": 0.0,
        }, f)

    validate_tb_pipeline(output_dir=str(tmp_path))

    with open(tmp_path / "validation_report.json") as f:
        report = json.load(f)
    assert report["financial_validation"]["status"] == "FAILED"


def test_financial_status_stays_unknown_when_statistics_file_absent(tmp_path):
    _touch_all_expected_files(tmp_path)
    validate_tb_pipeline(output_dir=str(tmp_path))

    with open(tmp_path / "validation_report.json") as f:
        report = json.load(f)
    assert report["financial_validation"]["status"] == "UNKNOWN"
