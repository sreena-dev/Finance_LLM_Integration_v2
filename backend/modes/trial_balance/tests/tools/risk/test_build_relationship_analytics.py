"""Regression test for build_relationship_analytics.py: a fix to make its
missing-file error messages name the producing tool (e.g. "run
build_fsli_summary first") introduced a KeyError -- the `paths` dict keys
were renamed to include the hint text, but `paths["Canonical TB"]` /
`paths["FSLI Summary"]` downstream still looked up the old short keys.
Caught by an actual end-to-end /audit run, not by a syntax/import check."""

from modes.trial_balance.pipeline.tools import build_relationship_analytics


def test_runs_successfully_with_both_inputs_present(make_canonical_tb, tmp_path):
    canonical_tb_file = make_canonical_tb(
        [{"gl_code": "1001", "gl_name": "Cash", "closing_balance": 1000.0, "main_head": "Current assets", "sub_head_1": "Cash"}]
    )
    # fsli_summary.parquet is read defensively (first_present_column) -- a minimal
    # frame with the columns build_fsli_summary actually writes is enough here.
    import polars as pl

    fsli_path = tmp_path / "fsli_summary.parquet"
    pl.DataFrame({"main_head": ["Current assets"], "sub_head_1": ["Cash"], "closing_balance": [1000.0]}).write_parquet(fsli_path)

    result = build_relationship_analytics(
        canonical_tb_file=str(canonical_tb_file),
        fsli_summary_file=str(fsli_path),
        output_dir=str(tmp_path),
    )
    assert result["execution_status"] == "SUCCESS"


def test_missing_fsli_summary_names_producing_tool(make_canonical_tb, tmp_path):
    canonical_tb_file = make_canonical_tb([{"gl_code": "1001", "gl_name": "Cash", "closing_balance": 1000.0}])

    result = build_relationship_analytics(
        canonical_tb_file=str(canonical_tb_file),
        fsli_summary_file=str(tmp_path / "does_not_exist.parquet"),
        output_dir=str(tmp_path),
    )
    assert result["execution_status"] == "FAILED"
    assert "run build_fsli_summary first" in result["message"]
