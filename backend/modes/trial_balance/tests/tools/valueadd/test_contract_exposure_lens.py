"""Wave 3 Fix 1: build_contract_exposure_lens, rebuilt (no source/git history survives
the original) against EPIL's own cited accounts -- GL 30101003 claims income with no
receivable, GL 21050000 mobilisation advance, GL 21001001 a credit-balance "advance",
and the 16-account "Amount Recoverable Others" population."""

import json

import polars as pl
import pytest

from modes.trial_balance.pipeline.tools import CANONICAL_TB_ALL_COLUMNS, build_contract_exposure_lens

_NUMERIC = {"opening_balance", "debit", "credit", "closing_balance"}


def _write_canonical(path, rows):
    full = [{c: r.get(c, 0.0 if c in _NUMERIC else None) for c in CANONICAL_TB_ALL_COLUMNS} for r in rows]
    for f in full:
        f["tb_doc_id"] = "TEST"
    schema = {c: (pl.Float64 if c in _NUMERIC else pl.Utf8) for c in CANONICAL_TB_ALL_COLUMNS}
    pl.DataFrame(full, schema=schema).write_parquet(path)
    return path


def test_claims_receivable_finding_fires(tmp_path):
    rows = [
        {"gl_code": "30101003", "gl_name": "Income-Claims Receivable", "closing_balance": -832_100_000.0,
         "main_head": "Revenue from operations", "mapped_status": "MAPPED"},
    ]
    tb = _write_canonical(tmp_path / "canonical_tb.parquet", rows)

    result = build_contract_exposure_lens(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"
    data = json.loads((tmp_path / "contract_exposure.json").read_text())
    assert data["applicable"] is True
    assert data["categories"]["claims_receivable"]["member_count"] == 1
    ids = {f["screen_id"] for f in data["finding_records"]}
    assert "CONTRACT-CLAIMS" in ids


def test_credit_balance_mobilisation_advance_flagged(tmp_path):
    rows = [
        {"gl_code": "21050000", "gl_name": "Mob Adv BG - Oman", "closing_balance": 2_057_700_000.0,
         "main_head": "Current assets", "mapped_status": "MAPPED"},
        {"gl_code": "21001001", "gl_name": "Mob Adv BG - S", "closing_balance": -1_858_900_000.0,
         "main_head": "Current assets", "mapped_status": "MAPPED"},
    ]
    tb = _write_canonical(tmp_path / "canonical_tb.parquet", rows)

    result = build_contract_exposure_lens(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"
    data = json.loads((tmp_path / "contract_exposure.json").read_text())
    assert data["categories"]["mobilisation_advance"]["member_count"] == 2
    sign_findings = [f for f in data["finding_records"] if f["screen_id"] == "CONTRACT-MOB-ADV-SIGN"]
    assert len(sign_findings) == 1
    assert "21001001" in sign_findings[0]["account"]


def test_amounts_withheld_recoverable_population_flagged(tmp_path):
    rows = [
        {"gl_code": str(1000 + i), "gl_name": f"Withheld by Customer {i}", "closing_balance": 20_885_000.0,
         "main_head": "Current assets", "mapped_status": "MAPPED"}
        for i in range(16)
    ]
    tb = _write_canonical(tmp_path / "canonical_tb.parquet", rows)

    result = build_contract_exposure_lens(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"
    data = json.loads((tmp_path / "contract_exposure.json").read_text())
    assert data["categories"]["amounts_withheld_recoverable"]["member_count"] == 16
    ids = {f["screen_id"] for f in data["finding_records"]}
    assert "CONTRACT-WITHHELD-RECOVERABLE" in ids


def test_withheld_recoverable_matches_epils_own_truncated_gl_names(tmp_path):
    # Caught live: EPIL's real gl_name text is shorter than the original keyword list
    # expected -- "WITHHELD CUSTOMER" (no "by"), "AMOUNT RECOVERABLE V" (truncated, no
    # "from vendor"), "EMD RECOVERABLE". The client's own cited Rs 208.85cr "Withheld by
    # Customer" figure is modeled here exactly (GL 21005002, closing_balance
    # 2088511942.41).
    rows = [
        {"gl_code": "21005002", "gl_name": "WITHHELD CUSTOMER", "closing_balance": 2_088_511_942.41,
         "main_head": "Current liabilities", "mapped_status": "MAPPED"},
        {"gl_code": "21006005", "gl_name": "EMD RECOVERABLE", "closing_balance": 14_844_085.00,
         "main_head": "Current assets", "mapped_status": "MAPPED"},
        {"gl_code": "21050053", "gl_name": "AMOUNT RECOVERABLE V", "closing_balance": 6_174_380.12,
         "main_head": "Current assets", "mapped_status": "MAPPED"},
    ]
    tb = _write_canonical(tmp_path / "canonical_tb.parquet", rows)

    result = build_contract_exposure_lens(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"
    data = json.loads((tmp_path / "contract_exposure.json").read_text())
    cat = data["categories"]["amounts_withheld_recoverable"]
    assert cat["member_count"] == 3
    assert cat["balance"] == pytest.approx(2_109_530_407.53, abs=0.01)


def test_not_applicable_when_no_ledger_matches(tmp_path):
    rows = [
        {"gl_code": "1", "gl_name": "Salaries and Wages", "closing_balance": 20_000_000.0,
         "main_head": "Expenses", "mapped_status": "MAPPED"},
    ]
    tb = _write_canonical(tmp_path / "canonical_tb.parquet", rows)

    result = build_contract_exposure_lens(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"
    data = json.loads((tmp_path / "contract_exposure.json").read_text())
    assert data["applicable"] is False
    assert data["finding_records"] == []
