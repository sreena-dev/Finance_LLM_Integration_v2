"""Wave 8 remark #26: DTA zero-movement + no current-tax P&L line (Ind AS 12
recoverability question). Regression-tests the exact false-positive Wave 5's own
live re-triage caught: Schedule III's "Current Tax Liabilities (Net)" is a
BALANCE-SHEET note that must not satisfy the current-tax P&L check."""

import json

import polars as pl

from modes.trial_balance.pipeline.tools import CANONICAL_TB_ALL_COLUMNS, build_dta_recoverability_screen

_NUMERIC = {"opening_balance", "debit", "credit", "closing_balance"}


def _write_canonical(path, rows):
    full = [{c: r.get(c, 0.0 if c in _NUMERIC else None) for c in CANONICAL_TB_ALL_COLUMNS} for r in rows]
    for f in full:
        f["tb_doc_id"] = "TEST"
    schema = {c: (pl.Float64 if c in _NUMERIC else pl.Utf8) for c in CANONICAL_TB_ALL_COLUMNS}
    pl.DataFrame(full, schema=schema).write_parquet(path)
    return path


def test_dormant_dta_with_no_current_tax_pl_line_flags_recoverability(tmp_path):
    rows = [
        {"gl_code": "1", "gl_name": "Deferred Tax Asset", "opening_balance": -1_452_000_000.0,
         "closing_balance": -1_452_000_000.0, "mapped_status": "MAPPED"},
    ]
    tb = _write_canonical(tmp_path / "canonical_tb.parquet", rows)

    result = build_dta_recoverability_screen(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"
    data = json.loads((tmp_path / "dta_recoverability_screen.json").read_text())
    assert data["recoverability_flag"] is True
    assert len(data["zero_movement_accounts"]) == 1
    assert data["current_tax_pl_accounts_found"] == 0
    assert len(data["finding_records"]) == 1


def test_current_tax_liabilities_balance_sheet_note_does_not_false_positive(tmp_path):
    # The exact false positive caught during Wave 5's live re-triage: a BS-side
    # "Current Tax Liabilities (Net)" note (GST/TCS/cess payables) must not satisfy
    # the current-tax P&L check just because it contains the words "current tax".
    rows = [
        {"gl_code": "1", "gl_name": "Deferred Tax Asset", "opening_balance": -1_452_000_000.0,
         "closing_balance": -1_452_000_000.0, "mapped_status": "MAPPED"},
        {"gl_code": "2", "gl_name": "GST Payable", "main_head": "Current Tax Liabilities (Net)",
         "closing_balance": -500_000.0, "bs_pl": "BS", "mapped_status": "MAPPED"},
    ]
    tb = _write_canonical(tmp_path / "canonical_tb.parquet", rows)

    build_dta_recoverability_screen(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    data = json.loads((tmp_path / "dta_recoverability_screen.json").read_text())
    assert data["current_tax_pl_accounts_found"] == 0
    assert data["recoverability_flag"] is True


def test_real_current_tax_pl_line_suppresses_the_flag(tmp_path):
    rows = [
        {"gl_code": "1", "gl_name": "Deferred Tax Asset", "opening_balance": -1_452_000_000.0,
         "closing_balance": -1_452_000_000.0, "mapped_status": "MAPPED"},
        {"gl_code": "2", "gl_name": "Current Tax Expense", "closing_balance": 200_000_000.0,
         "bs_pl": "PL", "mapped_status": "MAPPED"},
    ]
    tb = _write_canonical(tmp_path / "canonical_tb.parquet", rows)

    build_dta_recoverability_screen(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    data = json.loads((tmp_path / "dta_recoverability_screen.json").read_text())
    assert data["current_tax_pl_accounts_found"] == 1
    assert data["recoverability_flag"] is False
    assert data["finding_records"] == []


def test_not_applicable_when_no_dta_ledger(tmp_path):
    rows = [
        {"gl_code": "1", "gl_name": "Cash at Bank", "closing_balance": -50_000.0, "mapped_status": "MAPPED"},
    ]
    tb = _write_canonical(tmp_path / "canonical_tb.parquet", rows)

    build_dta_recoverability_screen(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    data = json.loads((tmp_path / "dta_recoverability_screen.json").read_text())
    assert data["recoverability_flag"] is False
    assert data["finding_records"] == []
