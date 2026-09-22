"""Wave 8 remark #25: unbilled revenue vs receivables movement correlation (Ind AS 115
timing question) -- flags divergent movement independent of absolute materiality, and
degrades gracefully to a balance-only report without a prior-period TB."""

import json

import polars as pl

from modes.trial_balance.pipeline.tools import CANONICAL_TB_ALL_COLUMNS, build_unbilled_revenue_screen

_NUMERIC = {"opening_balance", "debit", "credit", "closing_balance"}


def _write_canonical(path, rows):
    full = [{c: r.get(c, 0.0 if c in _NUMERIC else None) for c in CANONICAL_TB_ALL_COLUMNS} for r in rows]
    for f in full:
        f["tb_doc_id"] = "TEST"
    schema = {c: (pl.Float64 if c in _NUMERIC else pl.Utf8) for c in CANONICAL_TB_ALL_COLUMNS}
    pl.DataFrame(full, schema=schema).write_parquet(path)
    return path


def test_divergent_movement_flagged_independent_of_materiality(tmp_path):
    cy_rows = [
        {"gl_code": "1", "gl_name": "Unbilled Revenue", "closing_balance": -1_429_200_000.0, "mapped_status": "MAPPED"},
        {"gl_code": "2", "gl_name": "Trade Receivables", "closing_balance": -500_000_000.0, "mapped_status": "MAPPED"},
    ]
    py_rows = [
        {"gl_code": "1", "gl_name": "Unbilled Revenue", "closing_balance": -1_000_000.0, "mapped_status": "MAPPED"},
        {"gl_code": "2", "gl_name": "Trade Receivables", "closing_balance": -1_129_300_000.0, "mapped_status": "MAPPED"},
    ]
    cy_tb = _write_canonical(tmp_path / "cy_canonical_tb.parquet", cy_rows)
    py_tb = _write_canonical(tmp_path / "py_canonical_tb.parquet", py_rows)

    result = build_unbilled_revenue_screen(
        canonical_tb_file=str(cy_tb), prior_canonical_tb_file=str(py_tb), output_dir=str(tmp_path)
    )
    assert result["execution_status"] == "SUCCESS"
    data = json.loads((tmp_path / "unbilled_revenue_screen.json").read_text())
    assert data["divergent_movement"] is True
    assert data["unbilled_revenue_movement"] > 0
    assert data["trade_receivables_movement"] < 0
    assert len(data["finding_records"]) == 1


def test_no_prior_tb_degrades_to_balance_only_report(tmp_path):
    cy_rows = [
        {"gl_code": "1", "gl_name": "Unbilled Revenue", "closing_balance": -50_000.0, "mapped_status": "MAPPED"},
    ]
    cy_tb = _write_canonical(tmp_path / "canonical_tb.parquet", cy_rows)

    result = build_unbilled_revenue_screen(canonical_tb_file=str(cy_tb), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"
    data = json.loads((tmp_path / "unbilled_revenue_screen.json").read_text())
    assert data["divergent_movement"] is False
    assert data["unbilled_revenue_movement"] is None
    assert len(data["finding_records"]) == 1
    assert data["finding_records"][0]["screen_id"] == "UNBILLED-REVENUE-BALANCE-ONLY"


def test_not_applicable_when_no_unbilled_revenue_ledger(tmp_path):
    cy_rows = [
        {"gl_code": "1", "gl_name": "Cash at Bank", "closing_balance": -50_000.0, "mapped_status": "MAPPED"},
    ]
    cy_tb = _write_canonical(tmp_path / "canonical_tb.parquet", cy_rows)

    result = build_unbilled_revenue_screen(canonical_tb_file=str(cy_tb), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"
    data = json.loads((tmp_path / "unbilled_revenue_screen.json").read_text())
    assert data["finding_records"] == []
