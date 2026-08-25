"""Chat-query Bucket-A build: chat_query_fsli_table is the parametric replacement for
chat_get_gl_top_n/chat_get_revenue_expense_contribution/chat_get_ppe_rollup/
chat_get_borrowings_summary/chat_get_common_size_table/chat_get_dimension_counts/
chat_get_tb_control_totals (chat_query_build_spec_v2.md section 5)."""

import polars as pl

from modes.trial_balance.pipeline.tools import chat_query_fsli_table

_CANONICAL_COLS = {
    "gl_code": ["1", "2", "3"],
    "gl_name": ["Sales - Domestic", "Sales - Export", "Rent Expense"],
    "opening_balance": [0.0, 0.0, 0.0],
    "debit": [0.0, 0.0, 500.0],
    "credit": [800.0, 200.0, 0.0],
    "closing_balance": [800.0, 200.0, 500.0],
    "bs_pl": ["PL", "PL", "PL"],
    "main_head": ["Revenue from operations", "Revenue from operations", "Expenses"],
    "sub_head_1": ["Domestic Sales", "Export Sales", "Rent"],
    "sub_head_2": [None, None, None],
    "account_type": ["Income", "Income", "Expense"],
    "mapped_status": ["MAPPED", "MAPPED", "MAPPED"],
}


def _write_canonical(tmp_path):
    path = tmp_path / "canonical_tb.parquet"
    pl.DataFrame(_CANONICAL_COLS).write_parquet(path)
    return path


def test_control_totals_metric_computes_from_canonical_tb_directly(tmp_path):
    path = _write_canonical(tmp_path)
    result = chat_query_fsli_table(metric="control_totals", canonical_tb_file=str(path))
    assert result["execution_status"] == "SUCCESS"
    totals = result["data"][0]
    assert totals["total_debit"] == 500.0
    assert totals["total_credit"] == 1000.0
    assert totals["difference"] == -500.0


def test_pct_of_total_computes_revenue_contribution(tmp_path):
    path = _write_canonical(tmp_path)
    result = chat_query_fsli_table(metric="pct_of_total", filter="revenue", canonical_tb_file=str(path))
    assert result["execution_status"] == "SUCCESS"
    rows = {r["gl_code"]: r["pct_of_total"] for r in result["data"]}
    assert rows["1"] == 80.0
    assert rows["2"] == 20.0


def test_balance_metric_top_n_sorts_by_absolute_closing_balance(tmp_path):
    path = _write_canonical(tmp_path)
    result = chat_query_fsli_table(metric="balance", top_n=1, canonical_tb_file=str(path))
    assert result["execution_status"] == "SUCCESS"
    assert len(result["data"]) == 1
    assert result["data"][0]["gl_code"] == "1"


def test_count_metric_reports_distinct_counts_and_missing_dimensions(tmp_path):
    path = _write_canonical(tmp_path)
    result = chat_query_fsli_table(metric="count", canonical_tb_file=str(path))
    assert result["execution_status"] == "SUCCESS"
    by_dim = {r["dimension"]: r for r in result["data"]}
    assert by_dim["gl_code"]["distinct_count"] == 3
    assert by_dim["cost_centre"]["distinct_count"] is None
    assert "does not exist" in by_dim["cost_centre"]["note"]


def test_fsli_node_level_filters_fsli_summary(tmp_path):
    fsli_path = tmp_path / "fsli_summary.parquet"
    pl.DataFrame({
        "main_head": ["Non-current assets", "Non-current assets"],
        "sub_head_1": ["Gross Block", "Accumulated Depreciation"],
        "sub_head_2": [None, None],
        "node_name": ["Gross Block", "Accumulated Depreciation"],
        "hierarchy_path": ["Non-current assets > Gross Block", "Non-current assets > Accumulated Depreciation"],
        "closing_balance": [10000.0, -3000.0],
    }).write_parquet(fsli_path)

    result = chat_query_fsli_table(metric="balance", level="fsli_node", filter="gross block", fsli_summary_file=str(fsli_path))
    assert result["execution_status"] == "SUCCESS"
    assert len(result["data"]) == 1
    assert result["data"][0]["node_name"] == "Gross Block"


def test_common_size_metric_reads_snapshot_drilldown_and_diffs_py(tmp_path):
    cy_path = tmp_path / "snapshot_drilldown_cy.parquet"
    py_path = tmp_path / "snapshot_drilldown_py.parquet"
    pl.DataFrame({
        "main_head": ["Current assets"], "sub_head_1": ["Cash"], "sub_head_2": [None],
        "node_name": ["Cash"], "hierarchy_level": [2], "closing_balance": [100.0],
        "percent_of_parent": [50.0], "percent_of_main_head": [10.0],
    }).write_parquet(cy_path)
    pl.DataFrame({
        "main_head": ["Current assets"], "sub_head_1": ["Cash"], "sub_head_2": [None],
        "node_name": ["Cash"], "hierarchy_level": [2], "closing_balance": [50.0],
        "percent_of_parent": [40.0], "percent_of_main_head": [6.0],
    }).write_parquet(py_path)

    result = chat_query_fsli_table(metric="common_size", snapshot_drilldown_file=str(cy_path), py_snapshot_drilldown_file=str(py_path))
    assert result["execution_status"] == "SUCCESS"
    row = result["data"][0]
    assert row["percent_of_main_head"] == 10.0
    assert row["percent_of_main_head_py"] == 6.0
    assert row["percent_of_main_head_change"] == 4.0


def test_unknown_metric_fails_cleanly():
    result = chat_query_fsli_table(metric="not_a_real_metric")
    assert result["execution_status"] == "FAILED"
    assert result["data"] is None
