"""Wave 3 Fix 3: build_deposit_margin_money_screen cross-references margin-money FD
balances against build_contract_exposure_lens's bank_guarantee category -- the client's
own complaint was that this cross-reference was never made anywhere in the existing
output."""

import json

import polars as pl

from modes.trial_balance.pipeline.tools import (
    CANONICAL_TB_ALL_COLUMNS,
    build_contract_exposure_lens,
    build_deposit_margin_money_screen,
)

_NUMERIC = {"opening_balance", "debit", "credit", "closing_balance"}


def _write_canonical(path, rows):
    full = [{c: r.get(c, 0.0 if c in _NUMERIC else None) for c in CANONICAL_TB_ALL_COLUMNS} for r in rows]
    for f in full:
        f["tb_doc_id"] = "TEST"
    schema = {c: (pl.Float64 if c in _NUMERIC else pl.Utf8) for c in CANONICAL_TB_ALL_COLUMNS}
    pl.DataFrame(full, schema=schema).write_parquet(path)
    return path


def test_cross_references_guarantee_book_when_contract_exposure_has_run(tmp_path):
    rows = [
        {"gl_code": "1", "gl_name": "Fixed Deposit Against Margin Money for Guarantees", "closing_balance": 100_000_000.0,
         "main_head": "Current assets", "mapped_status": "MAPPED"},
        {"gl_code": "2", "gl_name": "Short Term Deposits", "closing_balance": 245_170_000.0,
         "main_head": "Current assets", "mapped_status": "MAPPED"},
        {"gl_code": "3", "gl_name": "Bank Guarantee Margin", "closing_balance": 50_000_000.0,
         "main_head": "Current liabilities", "mapped_status": "MAPPED"},
    ]
    tb = _write_canonical(tmp_path / "canonical_tb.parquet", rows)

    build_contract_exposure_lens(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    result = build_deposit_margin_money_screen(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"

    data = json.loads((tmp_path / "deposit_margin_money_screen.json").read_text())
    # Only the FD and Short Term Deposits accounts are deposit-shaped; "Bank Guarantee
    # Margin" is the guarantee-book account itself, not a deposit.
    assert data["deposits"]["member_count"] == 2
    assert data["guarantee_book_balance"] is not None
    assert data["finding_records"]
    assert "Guarantee book" in data["finding_records"][0]["gap"]


def test_degrades_gracefully_when_contract_exposure_has_not_run(tmp_path):
    rows = [
        {"gl_code": "1", "gl_name": "Short Term Deposits", "closing_balance": 100_000_000.0,
         "main_head": "Current assets", "mapped_status": "MAPPED"},
    ]
    tb = _write_canonical(tmp_path / "canonical_tb.parquet", rows)

    result = build_deposit_margin_money_screen(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"
    data = json.loads((tmp_path / "deposit_margin_money_screen.json").read_text())
    assert data["guarantee_book_balance"] is None
    assert "has not run" in data["finding_records"][0]["gap"]
