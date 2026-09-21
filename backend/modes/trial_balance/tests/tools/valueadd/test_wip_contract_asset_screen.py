"""Wave 8 remark #27: WIP/contract-asset balance implausibly small relative to revenue
scale for a construction/EPC entity mid-execution on active contracts. REL-09
(risk.py's expected_relationships.json) answers a different question and is untouched
by this screen."""

import json

import polars as pl

from modes.trial_balance.pipeline.tools import CANONICAL_TB_ALL_COLUMNS, build_wip_contract_asset_screen

_NUMERIC = {"opening_balance", "debit", "credit", "closing_balance"}


def _write_canonical(path, rows):
    full = [{c: r.get(c, 0.0 if c in _NUMERIC else None) for c in CANONICAL_TB_ALL_COLUMNS} for r in rows]
    for f in full:
        f["tb_doc_id"] = "TEST"
    schema = {c: (pl.Float64 if c in _NUMERIC else pl.Utf8) for c in CANONICAL_TB_ALL_COLUMNS}
    pl.DataFrame(full, schema=schema).write_parquet(path)
    return path


def test_implausibly_small_wip_vs_revenue_flagged(tmp_path):
    rows = [
        {"gl_code": "1", "gl_name": "Work in Progress", "closing_balance": -34_000.0, "mapped_status": "MAPPED"},
        {"gl_code": "2", "gl_name": "Revenue from Operations", "closing_balance": -16_900_000_000_000.0, "mapped_status": "MAPPED"},
    ]
    tb = _write_canonical(tmp_path / "canonical_tb.parquet", rows)

    result = build_wip_contract_asset_screen(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"
    data = json.loads((tmp_path / "wip_contract_asset_screen.json").read_text())
    assert data["implausibly_small"] is True
    assert len(data["finding_records"]) == 1


def test_proportionate_wip_not_flagged(tmp_path):
    rows = [
        {"gl_code": "1", "gl_name": "Work in Progress", "closing_balance": -500_000_000.0, "mapped_status": "MAPPED"},
        {"gl_code": "2", "gl_name": "Revenue from Operations", "closing_balance": -1_700_000_000.0, "mapped_status": "MAPPED"},
    ]
    tb = _write_canonical(tmp_path / "canonical_tb.parquet", rows)

    build_wip_contract_asset_screen(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    data = json.loads((tmp_path / "wip_contract_asset_screen.json").read_text())
    assert data["implausibly_small"] is False
    assert data["finding_records"] == []


def test_not_applicable_when_no_wip_ledger(tmp_path):
    rows = [
        {"gl_code": "1", "gl_name": "Cash at Bank", "closing_balance": -50_000.0, "mapped_status": "MAPPED"},
    ]
    tb = _write_canonical(tmp_path / "canonical_tb.parquet", rows)

    build_wip_contract_asset_screen(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    data = json.loads((tmp_path / "wip_contract_asset_screen.json").read_text())
    assert data["finding_records"] == []
