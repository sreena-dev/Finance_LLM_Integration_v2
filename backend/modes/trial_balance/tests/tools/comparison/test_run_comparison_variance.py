"""Comparative-analysis QA fix (follow-up): run_comparison_variance.py must use the same
CRITICAL/HIGH/MEDIUM/LOW severity vocabulary as the Excel workbook (backend/tools/_shared.py
::movement_flag), not its own separate NO_THRESHOLD/HIGH_PRIORITY/MEDIUM/LOW vocabulary that
never included CRITICAL at all -- a real PY->CY run showed a ~5.9x-overall-materiality
variance ("Cash Call Request") correctly flagged CRITICAL in the workbook but NO_THRESHOLD
in the report, because the two paths computed severity independently. Also: materiality_file
must default to the canonical filename in output_dir when not explicitly supplied, instead
of silently degrading every account to NO_THRESHOLD."""

import json

import polars as pl

from modes.trial_balance.pipeline.tools import run_comparison_variance


def _write_materiality(tmp_path, overall=1_000_000.0, performance=750_000.0):
    (tmp_path / "materiality.json").write_text(json.dumps({
        "thresholds": {"overall": overall, "performance": performance, "clearly_trivial": 50_000.0},
    }))


def test_critical_flag_reachable_for_large_variance(tmp_path):
    # Variance ~5.9x overall materiality (1,000,000) -- matches the real "Cash Call
    # Request" scenario that regressed to NO_THRESHOLD.
    _write_materiality(tmp_path)
    py_df = pl.DataFrame({"gl_code": ["100"], "gl_name": ["Cash Call Request"], "closing_balance": [1_000_000.0]})
    cy_df = pl.DataFrame({"gl_code": ["100"], "gl_name": ["Cash Call Request"], "closing_balance": [6_900_000.0]})
    py_path = tmp_path / "py.parquet"
    cy_path = tmp_path / "cy.parquet"
    py_df.write_parquet(py_path)
    cy_df.write_parquet(cy_path)

    result = run_comparison_variance(
        str(py_path), str(cy_path), materiality_file=str(tmp_path / "materiality.json"), output_dir=str(tmp_path)
    )
    assert result["execution_status"] == "SUCCESS"

    rows = pl.read_parquet(tmp_path / "comparison_variance.parquet").to_dicts()
    row = next(r for r in rows if r["gl_code"] == "100")
    assert row["flag"] == "CRITICAL"


def test_materiality_file_defaults_to_canonical_path_when_not_supplied(tmp_path):
    # Regression: previously, an agent call without an explicit materiality_file (or one
    # made before CY's materiality.json existed) silently degraded every account to
    # NO_THRESHOLD. Now defaults to <output_dir>/materiality.json.
    _write_materiality(tmp_path)
    py_df = pl.DataFrame({"gl_code": ["100"], "gl_name": ["X"], "closing_balance": [1_000_000.0]})
    cy_df = pl.DataFrame({"gl_code": ["100"], "gl_name": ["X"], "closing_balance": [6_900_000.0]})
    py_path = tmp_path / "py.parquet"
    cy_path = tmp_path / "cy.parquet"
    py_df.write_parquet(py_path)
    cy_df.write_parquet(cy_path)

    result = run_comparison_variance(str(py_path), str(cy_path), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"

    rows = pl.read_parquet(tmp_path / "comparison_variance.parquet").to_dicts()
    assert rows[0]["flag"] == "CRITICAL"


def test_no_threshold_flag_no_longer_produced(tmp_path):
    # NO_THRESHOLD must never appear again -- even with no materiality data available at
    # all, a genuinely-material-looking variance should degrade to LOW (via movement_flag
    # returning None), never a vocabulary term the Excel workbook doesn't share.
    py_df = pl.DataFrame({"gl_code": ["100"], "gl_name": ["X"], "closing_balance": [1_000_000.0]})
    cy_df = pl.DataFrame({"gl_code": ["100"], "gl_name": ["X"], "closing_balance": [6_900_000.0]})
    py_path = tmp_path / "py.parquet"
    cy_path = tmp_path / "cy.parquet"
    py_df.write_parquet(py_path)
    cy_df.write_parquet(cy_path)

    result = run_comparison_variance(str(py_path), str(cy_path), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"

    rows = pl.read_parquet(tmp_path / "comparison_variance.parquet").to_dicts()
    assert rows[0]["flag"] == "LOW"
    assert all(r["flag"] != "NO_THRESHOLD" for r in rows)
