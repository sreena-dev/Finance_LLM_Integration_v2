"""Wave 2 Fix 3: build_netting_screen detects -R/-P/-M clearing-suffix pairs and computes
their net balance -- EPIL's GL 20950021/20950022 ("SBI- MUSCAT (US$) -R"/"...-P") were
reported gross, inflating Cash and Bank to ~Rs 52,629cr against a true ~Rs 700cr."""

import json

import polars as pl
import pytest

from modes.trial_balance.pipeline.tools import CANONICAL_TB_ALL_COLUMNS, build_netting_screen

_NUMERIC = {"opening_balance", "debit", "credit", "closing_balance"}


def _write_canonical(path, rows):
    full = [{c: r.get(c, 0.0 if c in _NUMERIC else None) for c in CANONICAL_TB_ALL_COLUMNS} for r in rows]
    for f in full:
        f["tb_doc_id"] = "TEST"
    schema = {c: (pl.Float64 if c in _NUMERIC else pl.Utf8) for c in CANONICAL_TB_ALL_COLUMNS}
    pl.DataFrame(full, schema=schema).write_parquet(path)
    return path


def test_nets_a_genuine_receipt_payment_pair(tmp_path):
    rows = [
        {"gl_code": "20950021", "gl_name": "SBI- MUSCAT (US$) -R", "closing_balance": 6_396_870_000.0,
         "main_head": "Revenue from operations", "mapped_status": "MAPPED"},
        {"gl_code": "20950022", "gl_name": "SBI- MUSCAT (US$) -P", "closing_balance": -6_389_870_000.0,
         "main_head": "Current assets", "mapped_status": "MAPPED"},
    ]
    tb = _write_canonical(tmp_path / "canonical_tb.parquet", rows)

    result = build_netting_screen(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"

    data = json.loads((tmp_path / "netting_screen.json").read_text())
    assert data["pairs_detected"] == 1
    pair = data["pairs"][0]
    assert set(pair["gl_codes"]) == {"20950021", "20950022"}
    assert pair["net_balance"] == 7_000_000.0

    # Only the first leg carries the net balance -- the other is zeroed, so a consumer
    # that SUMS net_closing_balance across the TB gets the net figure exactly once,
    # not double-counted across both legs.
    netted = pl.read_parquet(tmp_path / "netted_balances.parquet").to_dicts()
    assert sum(row["net_closing_balance"] for row in netted) == 7_000_000.0
    assert sorted(row["net_closing_balance"] for row in netted) == [0.0, 7_000_000.0]


def test_does_not_net_same_sign_legs(tmp_path):
    # Same stripped name, both legs positive -- a coincidence of naming, not a genuine
    # receipt/payment clearing pair. Must NOT be netted.
    rows = [
        {"gl_code": "1", "gl_name": "UBI-R-002-FLC Sorana", "closing_balance": 100.0,
         "main_head": "Current assets", "mapped_status": "MAPPED"},
        {"gl_code": "2", "gl_name": "UBI-R-002-FLC Sorana", "closing_balance": 50.0,
         "main_head": "Current assets", "mapped_status": "MAPPED"},
    ]
    tb = _write_canonical(tmp_path / "canonical_tb.parquet", rows)

    result = build_netting_screen(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"
    data = json.loads((tmp_path / "netting_screen.json").read_text())
    assert data["pairs_detected"] == 0


def test_ignores_accounts_with_no_clearing_suffix(tmp_path):
    rows = [
        {"gl_code": "1", "gl_name": "Ordinary Bank Account", "closing_balance": 1000.0,
         "main_head": "Current assets", "mapped_status": "MAPPED"},
    ]
    tb = _write_canonical(tmp_path / "canonical_tb.parquet", rows)

    result = build_netting_screen(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"
    data = json.loads((tmp_path / "netting_screen.json").read_text())
    assert data["pairs_detected"] == 0


def test_space_delimited_suffix_pair_detected(tmp_path):
    # Caught live in an EPIL re-triage: "IOB-R 2471"/"IOB-P 2471" -- the R/P token is
    # space-separated from its trailing digits, not its own hyphen-delimited segment, so
    # the original hyphen-only split missed this shape entirely (~Rs 580cr on that run).
    rows = [
        {"gl_code": "20909101", "gl_name": "IOB-R 2471", "closing_balance": 5_803_748_328.03,
         "main_head": "Current assets", "mapped_status": "MAPPED"},
        {"gl_code": "20909102", "gl_name": "IOB-P 2471", "closing_balance": -5_803_525_559.70,
         "main_head": "Current assets", "mapped_status": "MAPPED"},
    ]
    tb = _write_canonical(tmp_path / "canonical_tb.parquet", rows)

    result = build_netting_screen(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"
    data = json.loads((tmp_path / "netting_screen.json").read_text())
    assert data["pairs_detected"] == 1
    assert data["pairs"][0]["net_balance"] == pytest.approx(222_768.33, abs=0.01)


def test_no_hyphen_space_only_suffix_pair_detected(tmp_path):
    # "CANARA BANK R"/"CANARA BANK P" -- no hyphen anywhere in the name at all.
    rows = [
        {"gl_code": "1", "gl_name": "CANARA BANK R", "closing_balance": 168_142.66,
         "main_head": "Current assets", "mapped_status": "MAPPED"},
        {"gl_code": "2", "gl_name": "CANARA BANK P", "closing_balance": -10_010.00,
         "main_head": "Current assets", "mapped_status": "MAPPED"},
    ]
    tb = _write_canonical(tmp_path / "canonical_tb.parquet", rows)

    result = build_netting_screen(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"
    data = json.loads((tmp_path / "netting_screen.json").read_text())
    assert data["pairs_detected"] == 1
    assert data["pairs"][0]["net_balance"] == pytest.approx(158_132.66, abs=0.01)


def test_glued_trailing_letter_pair_detected(tmp_path):
    # A letter suffix glued directly onto a digit string with no delimiter at all
    # ("...295P"). Only forms a pair when a genuine counterpart ("...295R") exists --
    # a lone glued-letter account (no counterpart) must still NOT be netted.
    rows = [
        {"gl_code": "1", "gl_name": "IOB-189002000000295P", "closing_balance": -100.0,
         "main_head": "Current assets", "mapped_status": "MAPPED"},
        {"gl_code": "2", "gl_name": "IOB-189002000000295R", "closing_balance": 120.0,
         "main_head": "Current assets", "mapped_status": "MAPPED"},
    ]
    tb = _write_canonical(tmp_path / "canonical_tb.parquet", rows)

    result = build_netting_screen(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"
    data = json.loads((tmp_path / "netting_screen.json").read_text())
    assert data["pairs_detected"] == 1
    assert data["pairs"][0]["net_balance"] == pytest.approx(20.0, abs=0.01)


def test_glued_trailing_letter_with_no_counterpart_is_not_netted(tmp_path):
    rows = [
        {"gl_code": "1", "gl_name": "IOB-189002000000295P", "closing_balance": -3_597_937_621.27,
         "main_head": "Current assets", "mapped_status": "MAPPED"},
    ]
    tb = _write_canonical(tmp_path / "canonical_tb.parquet", rows)

    result = build_netting_screen(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"
    data = json.loads((tmp_path / "netting_screen.json").read_text())
    assert data["pairs_detected"] == 0


def test_lc_clearing_suffix_pair_also_detected(tmp_path):
    # The "Other Risk Areas" doc's item 7: the -R/-P defect also runs through LC-clearing
    # accounts, not just plain bank accounts.
    rows = [
        {"gl_code": "20909491", "gl_name": "UBI-R-002-FLC Sorana", "closing_balance": 2_560_000.0,
         "main_head": "Current assets", "mapped_status": "MAPPED"},
        {"gl_code": "20909492", "gl_name": "UBI-P-002-FLC Sorana", "closing_balance": -2_500_000.0,
         "main_head": "Current liabilities", "mapped_status": "MAPPED"},
    ]
    tb = _write_canonical(tmp_path / "canonical_tb.parquet", rows)

    result = build_netting_screen(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"
    data = json.loads((tmp_path / "netting_screen.json").read_text())
    assert data["pairs_detected"] == 1
    assert data["pairs"][0]["net_balance"] == 60_000.0
