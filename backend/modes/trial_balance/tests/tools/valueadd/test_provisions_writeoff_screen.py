"""Wave 3 Fix 4: build_provisions_writeoff_screen keeps Provisions and Write-Offs as
their own distinct categories, never merged with build_estimation_exposure's Estimated
Liabilities figure -- per the client's own note these had never previously been
examined separately."""

import json

import polars as pl

from modes.trial_balance.pipeline.tools import CANONICAL_TB_ALL_COLUMNS, build_provisions_writeoff_screen

_NUMERIC = {"opening_balance", "debit", "credit", "closing_balance"}


def _write_canonical(path, rows):
    full = [{c: r.get(c, 0.0 if c in _NUMERIC else None) for c in CANONICAL_TB_ALL_COLUMNS} for r in rows]
    for f in full:
        f["tb_doc_id"] = "TEST"
    schema = {c: (pl.Float64 if c in _NUMERIC else pl.Utf8) for c in CANONICAL_TB_ALL_COLUMNS}
    pl.DataFrame(full, schema=schema).write_parquet(path)
    return path


def test_provisions_and_estimated_liabilities_reported_as_distinct_figures(tmp_path):
    rows = [
        {"gl_code": "1", "gl_name": "Provision for Warranty", "closing_balance": -916_600_000.0,
         "main_head": "Current liabilities", "mapped_status": "MAPPED"},
        {"gl_code": "2", "gl_name": "Estimated Liabilities - Gratuity", "closing_balance": -1_926_500_000.0,
         "main_head": "Non-current liabilities", "mapped_status": "MAPPED"},
    ]
    tb = _write_canonical(tmp_path / "canonical_tb.parquet", rows)

    result = build_provisions_writeoff_screen(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"
    data = json.loads((tmp_path / "provisions_writeoff_screen.json").read_text())
    # "Provision for Warranty" matches; "Estimated Liabilities" is explicitly excluded
    # from the provisions population, kept as its own distinct figure.
    assert data["provisions"]["member_count"] == 1
    assert data["provisions"]["balance"] == 916_600_000.0


def test_estimated_liabilities_excluded_even_with_a_real_source_typo(tmp_path):
    # Live EPIL data spells this "ESTIMATED LIABILTIES" (missing an "i") -- the exclude
    # keyword must tolerate that misspelling, not just the correct spelling.
    rows = [
        {"gl_code": "1", "gl_name": "ESTIMATED LIABILTIES", "closing_balance": -1_926_510_017.68,
         "main_head": "Non-current liabilities", "mapped_status": "MAPPED"},
        {"gl_code": "2", "gl_name": "Provision for Warranty", "closing_balance": -916_600_000.0,
         "main_head": "Current liabilities", "mapped_status": "MAPPED"},
    ]
    tb = _write_canonical(tmp_path / "canonical_tb.parquet", rows)

    result = build_provisions_writeoff_screen(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"
    data = json.loads((tmp_path / "provisions_writeoff_screen.json").read_text())
    assert data["provisions"]["member_count"] == 1
    assert data["provisions"]["balance"] == 916_600_000.0


def test_write_offs_flagged_as_regularity_matter(tmp_path):
    rows = [
        {"gl_code": "1", "gl_name": "Write Off for Doubtful Recovery", "closing_balance": 119_300_000.0,
         "main_head": "Expenses", "mapped_status": "MAPPED"},
    ]
    tb = _write_canonical(tmp_path / "canonical_tb.parquet", rows)

    result = build_provisions_writeoff_screen(canonical_tb_file=str(tb), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"
    data = json.loads((tmp_path / "provisions_writeoff_screen.json").read_text())
    assert data["write_offs"]["member_count"] == 1
    writeoff_findings = [f for f in data["finding_records"] if f["screen_id"] == "WRITEOFF"]
    assert len(writeoff_findings) == 1
    assert writeoff_findings[0]["regularity_flag"] is True
