"""build_financial_ratios (chat_query_build_spec_v2.md decision: Q83's 6 audit ratios need
Current Assets/Liabilities/Inventory/Receivables/Payables/Interest Expense/Debt, none of
which exist in financial_snapshot_statistics.json -- verified by direct code read -- so this
is a genuine new deterministic pipeline tool, pattern build_materiality.py, reading
snapshot_drilldown.parquet instead."""

import json

import polars as pl

from modes.trial_balance.pipeline.tools import build_financial_ratios


def _write_snapshot(tmp_path, rows):
    (tmp_path / "snapshot_drilldown.parquet").parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(tmp_path / "snapshot_drilldown.parquet")


def _write_stats(tmp_path, stats):
    (tmp_path / "financial_snapshot_statistics.json").write_text(json.dumps(stats))


def test_computes_current_ratio_from_matched_components(tmp_path):
    _write_snapshot(tmp_path, {
        "node_name": ["Current assets", "Current liabilities", "Inventory", "Trade Receivables", "Trade Payables", "Borrowings", "Finance Cost"],
        "hierarchy_level": [1, 1, 2, 2, 2, 2, 2],
        "closing_balance": [1000.0, 500.0, 200.0, 300.0, 150.0, 400.0, 50.0],
    })
    _write_stats(tmp_path, {"total_revenue": 2000.0, "total_expenses": 1500.0, "total_equity": 1000.0, "total_assets": 3000.0})

    result = build_financial_ratios(output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"
    ratios = result["ratios"]
    assert ratios["current"]["value"] == 2.0
    assert ratios["quick"]["value"] == (1000.0 - 200.0) / 500.0
    assert ratios["revenue_to_assets"]["value"] == round(2000.0 / 3000.0, 4)

    out_path = tmp_path / "financial_ratios.json"
    assert out_path.exists()


def test_missing_components_degrade_to_null_without_failing_tool(tmp_path):
    _write_snapshot(tmp_path, {
        "node_name": ["Current assets"], "hierarchy_level": [1], "closing_balance": [1000.0],
    })
    _write_stats(tmp_path, {"total_revenue": 2000.0, "total_expenses": 1500.0, "total_equity": 1000.0, "total_assets": 3000.0})

    result = build_financial_ratios(output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"
    assert result["ratios"]["current"]["value"] is None
    assert "current_liabilities" in result["ratios"]["current"]["components_missing"]
    # net_margin only needs the stats file, not the drilldown, so it should still compute.
    assert result["ratios"]["net_margin"]["value"] == round((2000.0 - 1500.0) / 2000.0, 4)


def test_missing_snapshot_drilldown_raises_pipeline_file_error(tmp_path):
    result = build_financial_ratios(output_dir=str(tmp_path))
    assert result["execution_status"] == "FAILED"
    assert "not found" in result["message"].lower() or "snapshot" in result["message"].lower()
