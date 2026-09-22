"""Wave 3 Fix 2: build_foreign_operations_lens, rebuilt to catch EPIL's real
Oman/Muscat/Sri Lanka geography tags -- the existing foreign-currency sensitive-tag
screen only catches currency-symbol keywords (client-confirmed: 3 accounts, Rs 2.74cr),
materially understating the entity's actual overseas exposure."""

import json

import polars as pl

from modes.trial_balance.pipeline.tools import CANONICAL_TB_ALL_COLUMNS, build_foreign_operations_lens

_NUMERIC = {"opening_balance", "debit", "credit", "closing_balance"}


def _write_canonical(path, rows):
    full = [{c: r.get(c, 0.0 if c in _NUMERIC else None) for c in CANONICAL_TB_ALL_COLUMNS} for r in rows]
    for f in full:
        f["tb_doc_id"] = "TEST"
    schema = {c: (pl.Float64 if c in _NUMERIC else pl.Utf8) for c in CANONICAL_TB_ALL_COLUMNS}
    pl.DataFrame(full, schema=schema).write_parquet(path)
    return path


def test_flags_fx_and_overseas_location_ledgers(tmp_path):
    rows = [
        {"gl_code": "20950021", "gl_name": "SBI- MUSCAT (US$) -R", "closing_balance": 6_396_870_000.0,
         "main_head": "Current assets", "mapped_status": "MAPPED"},
        {"gl_code": "20901811", "gl_name": "CASH OMAN", "closing_balance": 100_000.0,
         "main_head": "Current assets", "mapped_status": "MAPPED"},
        {"gl_code": "20908661", "gl_name": "IOB 200-600291-LKR-1", "closing_balance": 274_307_314.65,
         "main_head": "Current assets", "mapped_status": "MAPPED"},
    ]
    tb = _write_canonical(tmp_path / "canonical_tb.parquet", rows)

    result = build_foreign_operations_lens(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"
    data = json.loads((tmp_path / "foreign_operations.json").read_text())
    assert data["applicable"] is True
    assert len(data["ledgers"]) == 3
    locations = {l["location"] for l in data["ledgers"] if l["location"]}
    assert "oman" in locations
    assert "sri_lanka" in locations
    assert data["finding_records"]
    assert data["finding_records"][0]["screen_id"] == "FOREIGN-OPS-SCOPE-GAP"


def test_not_applicable_for_domestic_only_tb(tmp_path):
    rows = [
        {"gl_code": "1", "gl_name": "HDFC Bank Current Account", "closing_balance": 5_000_000.0,
         "main_head": "Current assets", "mapped_status": "MAPPED"},
    ]
    tb = _write_canonical(tmp_path / "canonical_tb.parquet", rows)

    result = build_foreign_operations_lens(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"
    data = json.loads((tmp_path / "foreign_operations.json").read_text())
    assert data["applicable"] is False
    assert data["ledgers"] == []
