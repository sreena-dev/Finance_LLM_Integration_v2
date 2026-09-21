"""Wave 3 Fix 5: build_msme_interest_screen flags the ABSENCE of an interest-on-
delayed-payment account despite a materially-sized MSME payable balance -- a
disclosure gap under the MSMED Act, not a computed ratio."""

import json

import polars as pl

from modes.trial_balance.pipeline.tools import CANONICAL_TB_ALL_COLUMNS, build_msme_interest_screen

_NUMERIC = {"opening_balance", "debit", "credit", "closing_balance"}


def _write_canonical(path, rows):
    full = [{c: r.get(c, 0.0 if c in _NUMERIC else None) for c in CANONICAL_TB_ALL_COLUMNS} for r in rows]
    for f in full:
        f["tb_doc_id"] = "TEST"
    schema = {c: (pl.Float64 if c in _NUMERIC else pl.Utf8) for c in CANONICAL_TB_ALL_COLUMNS}
    pl.DataFrame(full, schema=schema).write_parquet(path)
    return path


def test_flags_disclosure_gap_when_no_interest_account_exists(tmp_path):
    rows = [
        {"gl_code": "1", "gl_name": "MSME Payables", "closing_balance": -142_000_000.0,
         "main_head": "Current liabilities", "mapped_status": "MAPPED"},
    ]
    tb = _write_canonical(tmp_path / "canonical_tb.parquet", rows)

    result = build_msme_interest_screen(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"
    data = json.loads((tmp_path / "msme_interest_screen.json").read_text())
    assert data["disclosure_gap"] is True
    ids = {f["screen_id"] for f in data["finding_records"]}
    assert "MSME-INTEREST-DISCLOSURE-GAP" in ids


def test_no_gap_flagged_when_interest_account_present(tmp_path):
    rows = [
        {"gl_code": "1", "gl_name": "MSME Payables", "closing_balance": -142_000_000.0,
         "main_head": "Current liabilities", "mapped_status": "MAPPED"},
        {"gl_code": "2", "gl_name": "Interest on Delayed Payment to MSME", "closing_balance": -500_000.0,
         "main_head": "Expenses", "mapped_status": "MAPPED"},
    ]
    tb = _write_canonical(tmp_path / "canonical_tb.parquet", rows)

    result = build_msme_interest_screen(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"
    data = json.loads((tmp_path / "msme_interest_screen.json").read_text())
    assert data["disclosure_gap"] is False
    assert data["finding_records"] == []
