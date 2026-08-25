"""TB-R01/R02: the GL account count must come from len(canonical_df), never from
mapping_summary.json alone -- that file is upload-path-only and absent for every
DB-sourced run, which previously silently defaulted every count to 0."""

import polars as pl

from modes.trial_balance.pipeline.tools import build_report_markdown


def _write_canonical_tb(path, n_rows=7):
    pl.DataFrame({
        "gl_code": [str(i) for i in range(n_rows)],
        "gl_name": [f"Account {i}" for i in range(n_rows)],
        "opening_balance": [0.0] * n_rows,
        "debit": [100.0] * n_rows,
        "credit": [100.0] * n_rows,
        "closing_balance": [0.0] * n_rows,
        "main_head": ["Assets"] * n_rows,
    }).write_parquet(path / "canonical_tb.parquet")


def test_count_uses_canonical_df_when_mapping_summary_absent(tmp_path):
    _write_canonical_tb(tmp_path, n_rows=7)

    result = build_report_markdown(output_dir=str(tmp_path))

    assert result["execution_status"] == "SUCCESS"
    markdown = result["data"]["markdown"]
    assert "comprises 7 GL account(s)" in markdown
    assert "comprises 0 GL account(s)" not in markdown


def test_mapped_unmapped_breakdown_omitted_without_mapping_summary(tmp_path):
    _write_canonical_tb(tmp_path, n_rows=3)

    result = build_report_markdown(output_dir=str(tmp_path))

    markdown = result["data"]["markdown"]
    # No "0 account(s) did not match" false-positive line when mapping_summary.json
    # was never written for this (DB-sourced) run.
    assert "0 account(s) did not match a distinct entry" not in markdown
