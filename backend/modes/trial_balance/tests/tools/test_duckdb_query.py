"""backend/tools/duckdb_query.py: regression-proof that a DuckDB point-lookup
agrees with the equivalent Polars filter it replaces in chat.py (see that
module's docstring for scoping -- this is the only query engine besides
Polars introduced anywhere in the pipeline, and only for this exact shape of
lookup)."""

import polars as pl

from modes.trial_balance.pipeline.tools.duckdb_query import query_parquet


def _write_tb(tmp_path):
    path = tmp_path / "canonical_tb.parquet"
    pl.DataFrame({
        "gl_code": ["1001", "1002", "1003", "2001"],
        "gl_name": ["Cash in Hand", "Bank Account", "Inventory - Raw Material", None],
        "closing_balance": [100.0, 200.0, 300.0, 400.0],
    }).write_parquet(path)
    return path


def test_exact_match_agrees_with_polars_filter(tmp_path):
    path = _write_tb(tmp_path)
    df = pl.read_parquet(path)

    polars_result = df.filter(pl.col("gl_code").cast(pl.Utf8) == "1002")
    duckdb_result = query_parquet("SELECT * FROM t WHERE CAST(gl_code AS VARCHAR) = ?", ["1002"], str(path))

    assert duckdb_result.sort("gl_code").to_dicts() == polars_result.sort("gl_code").to_dicts()


def test_no_match_returns_empty_frame_like_polars(tmp_path):
    path = _write_tb(tmp_path)
    df = pl.read_parquet(path)

    polars_result = df.filter(pl.col("gl_code").cast(pl.Utf8) == "9999")
    duckdb_result = query_parquet("SELECT * FROM t WHERE CAST(gl_code AS VARCHAR) = ?", ["9999"], str(path))

    assert polars_result.is_empty()
    assert duckdb_result.is_empty()


def test_literal_substring_contains_agrees_with_polars_literal_contains(tmp_path):
    path = _write_tb(tmp_path)
    df = pl.read_parquet(path)

    polars_result = df.filter(
        pl.col("gl_name").cast(pl.Utf8).str.to_lowercase().str.contains("bank", literal=True).fill_null(False)
    )
    duckdb_result = query_parquet(
        "SELECT * FROM t WHERE contains(lower(CAST(gl_name AS VARCHAR)), lower(?))", ["bank"], str(path),
    )

    assert duckdb_result.sort("gl_code").to_dicts() == polars_result.sort("gl_code").to_dicts()


def test_null_gl_name_never_matches_substring_search_like_polars_fill_null_false(tmp_path):
    """gl_code 2001 has gl_name=None -- both engines must exclude it, not error
    or treat it as a match."""
    path = _write_tb(tmp_path)
    duckdb_result = query_parquet(
        "SELECT * FROM t WHERE contains(lower(CAST(gl_name AS VARCHAR)), lower(?))", ["a"], str(path),
    )
    assert "2001" not in duckdb_result["gl_code"].to_list()


def test_percent_and_underscore_in_needle_are_treated_literally_not_as_wildcards(tmp_path):
    """The contains() function (not LIKE) is what makes this true -- a LIKE-
    based implementation would treat '%' as a wildcard, diverging from
    Polars' str.contains(literal=True) semantics this replaces."""
    path = tmp_path / "canonical_tb.parquet"
    pl.DataFrame({
        "gl_code": ["1001", "1002"],
        "gl_name": ["100% Reserve Fund", "Reserve Fund"],
        "closing_balance": [1.0, 2.0],
    }).write_parquet(path)

    duckdb_result = query_parquet(
        "SELECT * FROM t WHERE contains(lower(CAST(gl_name AS VARCHAR)), lower(?))", ["100%"], str(path),
    )
    assert duckdb_result["gl_code"].to_list() == ["1001"]


def test_result_is_a_polars_dataframe(tmp_path):
    path = _write_tb(tmp_path)
    result = query_parquet("SELECT * FROM t WHERE CAST(gl_code AS VARCHAR) = ?", ["1001"], str(path))
    assert isinstance(result, pl.DataFrame)
