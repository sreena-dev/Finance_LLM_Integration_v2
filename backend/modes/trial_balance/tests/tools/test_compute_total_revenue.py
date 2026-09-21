"""Wave 2 Fix 2a: compute_total_revenue is the single shared revenue figure that
build_audit_ratio_pack, build_relationship_expectations, and STAT-GST-OUT now all defer
to, instead of each independently keyword-matching revenue (the mechanism behind a live
EPIL run showing revenue stated three different ways in the same report)."""

import json

import polars as pl

from modes.trial_balance.pipeline.tools import compute_total_revenue


def test_prefers_financial_snapshot_when_present(tmp_path):
    (tmp_path / "financial_snapshot_statistics.json").write_text(
        json.dumps({"total_revenue": -51157780000.0}), encoding="utf-8"
    )
    result = compute_total_revenue(tmp_path)
    assert result["basis"] == "financial_snapshot"
    assert result["balance"] == 51157780000.0  # absolute-valued


def test_falls_back_to_gl_name_when_snapshot_absent(tmp_path):
    tb_df = pl.DataFrame({
        "gl_code": ["1", "2"],
        "gl_name": ["Sales Revenue", "Rent Expense"],
        "main_head": ["Revenue from operations", "Expenses"],
        "sub_head_1": [None, None],
        "sub_head_2": [None, None],
        "closing_balance": [-5_000_000.0, 500_000.0],
    })
    result = compute_total_revenue(tmp_path, tb_df)
    assert result["basis"] == "gl_name_fallback"
    assert result["balance"] == 5_000_000.0


def test_same_revenue_across_three_consumers(tmp_path):
    # Regression test for the "revenue stated 3 different ways" client symptom: a single
    # fixture TB, with a real financial_snapshot_statistics.json present, must produce the
    # SAME revenue balance from build_audit_ratio_pack, build_relationship_expectations,
    # and build_statutory_screen's STAT-GST-OUT screen.
    from modes.trial_balance.pipeline.tools import (
        CANONICAL_TB_ALL_COLUMNS,
        build_audit_ratio_pack,
        build_relationship_expectations,
        build_statutory_screen,
    )

    rows = [
        {"gl_code": "3000", "gl_name": "Sales Revenue", "closing_balance": -100_000_000.0,
         "main_head": "Revenue from operations", "sub_head_1": "Revenue",
         "account_type": "Income", "mapped_status": "MAPPED"},
        {"gl_code": "1500", "gl_name": "Trade Receivable", "closing_balance": 80_000_000.0,
         "main_head": "Current assets", "sub_head_1": "Trade receivables",
         "account_type": "Asset", "mapped_status": "MAPPED"},
        {"gl_code": "5000", "gl_name": "GST Payable", "closing_balance": -5_000_000.0,
         "main_head": "Current liabilities", "sub_head_1": "Other current liabilities",
         "account_type": "Liability", "mapped_status": "MAPPED"},
    ]
    numeric = {"opening_balance", "debit", "credit", "closing_balance"}
    full = [{c: r.get(c, 0.0 if c in numeric else None) for c in CANONICAL_TB_ALL_COLUMNS} for r in rows]
    for f in full:
        f["tb_doc_id"] = "TEST"
    schema = {c: (pl.Float64 if c in numeric else pl.Utf8) for c in CANONICAL_TB_ALL_COLUMNS}
    tb_path = tmp_path / "canonical_tb.parquet"
    pl.DataFrame(full, schema=schema).write_parquet(tb_path)

    (tmp_path / "financial_snapshot_statistics.json").write_text(
        json.dumps({"total_revenue": -100_000_000.0, "total_expenses": 0.0}), encoding="utf-8"
    )

    ratio_pack = build_audit_ratio_pack(canonical_tb_file=str(tb_path), output_dir=str(tmp_path))
    assert ratio_pack["execution_status"] == "SUCCESS"
    ratio_pack_data = json.loads((tmp_path / "audit_ratio_pack.json").read_text())
    revenue_balance_1 = ratio_pack_data["components_resolved"]["revenue"]["balance"]

    rel = build_relationship_expectations(canonical_tb_file=str(tb_path), output_dir=str(tmp_path))
    assert rel["execution_status"] == "SUCCESS"
    rel01 = next(r for r in json.loads((tmp_path / "relationship_expectations.json").read_text())["results"] if r["id"] == "REL-01")
    revenue_balance_2 = 80_000_000.0 / rel01["observed_ratio"] if rel01.get("observed_ratio") else None

    stat = build_statutory_screen(canonical_tb_file=str(tb_path), output_dir=str(tmp_path))
    assert stat["execution_status"] == "SUCCESS"
    stat_data = json.loads((tmp_path / "statutory_screen.json").read_text())
    gst_out = next(r for r in stat_data["results"] if r["id"] == "STAT-GST-OUT")
    revenue_balance_3 = gst_out["base_balance"]

    assert revenue_balance_1 == 100_000_000.0
    assert revenue_balance_3 == 100_000_000.0
    if revenue_balance_2 is not None:
        assert round(revenue_balance_2, 2) == 100_000_000.0
