"""build_financial_snapshot writes financial_snapshot_statistics.json, the file
build_financial_ratios/build_materiality read total_equity from.

TB-R17: closing_balance is debit-positive, so Equity (a credit-normal head) carries a
NEGATIVE raw sign -- risk.py's going-concern screen already negates it for net_worth
(net_worth = -equity_raw). This file used to write total_equity un-negated, so
Debt/Equity and ROCE in build_financial_ratios (which divide/add against it directly,
against an already-positive total_debt from find_fsli_component's abs()) computed with
an inverted sign."""

import json

import polars as pl

from modes.trial_balance.pipeline.tools import build_financial_snapshot


def _write_fsli_summary(tmp_path, rows):
    base = {
        "main_head": None, "hierarchy_level": 1, "hierarchy_path": None, "node_name": None,
        "parent_node_name": None, "opening_balance": 0.0, "debit": 0.0, "credit": 0.0,
        "closing_balance": 0.0, "leaf_gl_count": 1, "descendant_gl_count": 0, "node_type": "LEAF",
    }
    full_rows = [{**base, **r} for r in rows]
    pl.DataFrame(full_rows).write_parquet(tmp_path / "fsli_summary.parquet")


def test_total_equity_is_negated_to_a_positive_real_world_value(tmp_path):
    # Equity is credit-normal, so its raw closing_balance is negative (-5,000.00 here) --
    # the real-world equity value is positive 5,000.00.
    _write_fsli_summary(tmp_path, [
        {"main_head": "Equity", "hierarchy_path": "Equity", "node_name": "Equity", "closing_balance": -5000.0},
        {"main_head": "Assets", "hierarchy_path": "Assets", "node_name": "Assets", "closing_balance": 5000.0},
    ])

    result = build_financial_snapshot(canonical_tb_file="unused.parquet", output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"

    stats = json.loads((tmp_path / "financial_snapshot_statistics.json").read_text())
    assert stats["total_equity"] == 5000.0
    # Assets is debit-normal and already carries the correct positive sign -- must not be
    # touched by this fix.
    assert stats["total_assets"] == 5000.0
