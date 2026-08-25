"""Chat-query Bucket-A build: chat_query_comparison is the parametric replacement for
chat_get_zero_movement_accounts/chat_get_classification_changes/chat_get_gl_integrity_
issues's comparison half (chat_query_build_spec_v2.md section 5). delta_type='reclassified'
is the one genuine cross-artifact build -- structural_delta.json cannot answer it (verified
by direct code read of run_comparison_structural.py), so it joins the two canonical TBs
directly."""

import json

import polars as pl

from modes.trial_balance.pipeline.tools import chat_query_comparison


def test_new_and_removed_read_structural_delta(tmp_path):
    path = tmp_path / "structural_delta.json"
    path.write_text(json.dumps({
        "new_ledgers": [{"gl_code": "500", "gl_name": "New GL"}],
        "removed_ledgers": [{"gl_code": "600", "gl_name": "Old GL"}],
    }))
    new_result = chat_query_comparison(delta_type="new", structural_delta_file=str(path))
    assert new_result["execution_status"] == "SUCCESS"
    assert new_result["data"][0]["gl_code"] == "500"

    removed_result = chat_query_comparison(delta_type="removed", structural_delta_file=str(path))
    assert removed_result["execution_status"] == "SUCCESS"
    assert removed_result["data"][0]["gl_code"] == "600"


def test_zero_movement_filters_no_change_flag(tmp_path):
    path = tmp_path / "comparison_variance.parquet"
    pl.DataFrame({
        "gl_code": ["1", "2"], "gl_name": ["A", "B"], "py_closing": [100.0, 200.0],
        "cy_closing": [100.0, 250.0], "variance": [0.0, 50.0], "variance_pct": [0.0, 25.0],
        "flag": ["NO_CHANGE", "MEDIUM"],
    }).write_parquet(path)
    result = chat_query_comparison(delta_type="zero-movement", comparison_variance_file=str(path))
    assert result["execution_status"] == "SUCCESS"
    assert len(result["data"]) == 1
    assert result["data"][0]["gl_code"] == "1"


def test_material_variance_filters_by_explicit_flag(tmp_path):
    path = tmp_path / "comparison_variance.parquet"
    pl.DataFrame({
        "gl_code": ["1", "2"], "gl_name": ["A", "B"], "py_closing": [100.0, 100.0],
        "cy_closing": [900.0, 150.0], "variance": [800.0, 50.0], "variance_pct": [800.0, 50.0],
        "flag": ["CRITICAL", "MEDIUM"],
    }).write_parquet(path)
    result = chat_query_comparison(delta_type="material-variance", flag="CRITICAL", comparison_variance_file=str(path))
    assert result["execution_status"] == "SUCCESS"
    assert len(result["data"]) == 1
    assert result["data"][0]["gl_code"] == "1"


def test_reclassified_joins_py_cy_canonical_tb_on_gl_code(tmp_path):
    py_path = tmp_path / "py_canonical_tb.parquet"
    cy_path = tmp_path / "cy_canonical_tb.parquet"
    pl.DataFrame({
        "gl_code": ["1", "2"], "gl_name": ["A", "B"], "closing_balance": [100.0, 200.0],
        "main_head": ["Current liabilities", "Equity"], "sub_head_1": ["Borrowings", "Reserves"], "sub_head_2": [None, None],
    }).write_parquet(py_path)
    pl.DataFrame({
        "gl_code": ["1", "2"], "gl_name": ["A", "B"], "closing_balance": [500.0, 200.0],
        "main_head": ["Non-current liabilities", "Equity"], "sub_head_1": ["Borrowings", "Reserves"], "sub_head_2": [None, None],
    }).write_parquet(cy_path)

    result = chat_query_comparison(delta_type="reclassified", py_canonical_tb_file=str(py_path), cy_canonical_tb_file=str(cy_path))
    assert result["execution_status"] == "SUCCESS"
    assert len(result["data"]) == 1
    row = result["data"][0]
    assert row["gl_code"] == "1"
    assert row["py_main_head"] == "Current liabilities"
    assert row["cy_main_head"] == "Non-current liabilities"
    assert row["balance_delta"] == 400.0


def test_unknown_delta_type_fails_cleanly():
    result = chat_query_comparison(delta_type="not_a_real_type")
    assert result["execution_status"] == "FAILED"
    assert result["data"] is None


def test_new_with_materiality_filters_by_real_threshold_and_adds_closing_balance(tmp_path):
    # N9 root-cause fix: 'new' AND 'above materiality' must be answerable in one call, using
    # the real materiality.json threshold -- not an LLM eyeball guess, and not a 20-call
    # per-GL-code workaround.
    structural_path = tmp_path / "structural_delta.json"
    structural_path.write_text(json.dumps({
        "new_ledgers": [
            {"gl_code": "100", "gl_name": "Big New GL"},
            {"gl_code": "200", "gl_name": "Small New GL"},
        ],
    }))
    cy_path = tmp_path / "cy_canonical_tb.parquet"
    pl.DataFrame({"gl_code": ["100", "200"], "closing_balance": [5_000_000.0, 100.0]}).write_parquet(cy_path)
    materiality_path = tmp_path / "materiality.json"
    materiality_path.write_text(json.dumps({"thresholds": {"overall": 1_000_000.0, "performance": 750_000.0}}))

    result = chat_query_comparison(
        delta_type="new",
        structural_delta_file=str(structural_path),
        cy_canonical_tb_file=str(cy_path),
        materiality_file=str(materiality_path),
    )
    assert result["execution_status"] == "SUCCESS"
    assert len(result["data"]) == 1
    assert result["data"][0]["gl_code"] == "100"
    assert result["data"][0]["closing_balance"] == 5_000_000.0
    assert "materiality" in result["message"]


def test_removed_without_materiality_file_still_adds_closing_balance_unfiltered(tmp_path):
    structural_path = tmp_path / "structural_delta.json"
    structural_path.write_text(json.dumps({
        "removed_ledgers": [{"gl_code": "600", "gl_name": "Old GL"}],
    }))
    py_path = tmp_path / "py_canonical_tb.parquet"
    pl.DataFrame({"gl_code": ["600"], "closing_balance": [42.0]}).write_parquet(py_path)

    result = chat_query_comparison(
        delta_type="removed", structural_delta_file=str(structural_path), py_canonical_tb_file=str(py_path),
    )
    assert result["execution_status"] == "SUCCESS"
    assert result["data"][0]["closing_balance"] == 42.0
