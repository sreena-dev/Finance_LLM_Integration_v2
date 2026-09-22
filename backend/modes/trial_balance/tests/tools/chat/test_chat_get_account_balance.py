"""chat_get_account_balance: exact gl_code / gl_name_contains point lookup,
now DuckDB-backed (backend/tools/duckdb_query.py) with Valkey caching
(chat_cacheable in chat.py) layered on top. Cache behavior is exercised
against the autouse fake_valkey fixture (tests/conftest.py)."""

import polars as pl

from modes.trial_balance.pipeline.tools import chat_get_account_balance
import modes.trial_balance.pipeline.tools.chat as chat_module


def _write_tb(tmp_path):
    path = tmp_path / "canonical_tb.parquet"
    pl.DataFrame({
        "gl_code": ["1001", "1002"],
        "gl_name": ["Cash in Hand", "Bank Account"],
        "closing_balance": [100.0, 200.0],
    }).write_parquet(path)
    return path


def test_exact_gl_code_match_returns_single_dict(tmp_path):
    path = _write_tb(tmp_path)
    result = chat_get_account_balance(str(path), gl_code="1002")
    assert result["execution_status"] == "SUCCESS"
    assert result["data"]["gl_name"] == "Bank Account"


def test_gl_name_contains_case_insensitive(tmp_path):
    path = _write_tb(tmp_path)
    result = chat_get_account_balance(str(path), gl_name_contains="bank")
    assert result["execution_status"] == "SUCCESS"
    assert result["data"]["gl_code"] == "1002"


def test_no_match_fails_cleanly(tmp_path):
    path = _write_tb(tmp_path)
    result = chat_get_account_balance(str(path), gl_code="9999")
    assert result["execution_status"] == "FAILED"
    assert result["data"] is None


def test_neither_arg_provided_fails_cleanly(tmp_path):
    path = _write_tb(tmp_path)
    result = chat_get_account_balance(str(path))
    assert result["execution_status"] == "FAILED"
    assert "Provide gl_code" in result["message"]


def test_missing_file_fails_cleanly_through_the_pipeline_tool_envelope():
    """chat_get_account_balance is exported as the @pipeline_tool-wrapped
    version, which catches PipelineFileError and turns it into a FAILED
    envelope response -- it never propagates to the caller (see
    pipeline_tool.py's except (PipelineFileError, PipelineDBError) clause)."""
    result = chat_get_account_balance("/does/not/exist.parquet", gl_code="1001")
    assert result["execution_status"] == "FAILED"
    assert "not found" in result["message"].lower()


# ── Caching behavior ─────────────────────────────────────────────────────

def test_identical_calls_hit_cache_and_skip_duckdb(tmp_path, monkeypatch):
    path = _write_tb(tmp_path)
    call_count = {"n": 0}
    real_query_parquet = chat_module.query_parquet

    def _counting_query_parquet(*args, **kwargs):
        call_count["n"] += 1
        return real_query_parquet(*args, **kwargs)

    monkeypatch.setattr(chat_module, "query_parquet", _counting_query_parquet)

    r1 = chat_get_account_balance(str(path), gl_code="1001")
    r2 = chat_get_account_balance(str(path), gl_code="1001")

    assert r1 == r2
    assert call_count["n"] == 1, "second identical call should hit the Valkey cache, not re-query DuckDB"


def test_different_args_do_not_share_a_cache_entry(tmp_path, monkeypatch):
    path = _write_tb(tmp_path)
    call_count = {"n": 0}
    real_query_parquet = chat_module.query_parquet

    def _counting_query_parquet(*args, **kwargs):
        call_count["n"] += 1
        return real_query_parquet(*args, **kwargs)

    monkeypatch.setattr(chat_module, "query_parquet", _counting_query_parquet)

    chat_get_account_balance(str(path), gl_code="1001")
    chat_get_account_balance(str(path), gl_code="1002")

    assert call_count["n"] == 2


def test_cache_invalidates_when_artifact_is_regenerated(tmp_path, monkeypatch):
    """The actual correctness requirement: a session's canonical_tb.parquet can
    be regenerated mid-session, and a cached answer from before the rewrite
    must never be served afterward."""
    path = _write_tb(tmp_path)
    call_count = {"n": 0}
    real_query_parquet = chat_module.query_parquet

    def _counting_query_parquet(*args, **kwargs):
        call_count["n"] += 1
        return real_query_parquet(*args, **kwargs)

    monkeypatch.setattr(chat_module, "query_parquet", _counting_query_parquet)

    r1 = chat_get_account_balance(str(path), gl_code="1001")
    assert r1["data"]["closing_balance"] == 100.0

    # Regenerate the file with a changed balance for the same gl_code.
    pl.DataFrame({
        "gl_code": ["1001", "1002"],
        "gl_name": ["Cash in Hand", "Bank Account"],
        "closing_balance": [999.0, 200.0],
    }).write_parquet(path)

    r2 = chat_get_account_balance(str(path), gl_code="1001")
    assert r2["data"]["closing_balance"] == 999.0
    assert call_count["n"] == 2, "regenerated artifact must produce a cache miss, not a stale hit"
