"""Tests for build_sample_selection -- SA 530 audit sampling (Monetary Unit / PPS and
Stratified Random), the one screen in this pipeline that deliberately samples instead of
testing the full population."""

import json
from pathlib import Path

import pytest

from modes.trial_balance.pipeline.tools import build_sample_selection
from modes.trial_balance.tests.conftest_phase2 import write_canonical

pl = pytest.importorskip("polars")


def _write_materiality(tmp_path, overall=1_000_000.0, performance=750_000.0):
    (tmp_path / "materiality.json").write_text(json.dumps({
        "thresholds": {"overall": overall, "performance": performance, "clearly_trivial": 50_000.0},
    }), encoding="utf-8")


def _rows(n, base_gl=1000):
    """n mapped accounts, closing balances spread across several orders of magnitude so
    both certainty items and systematic-interval items appear in a MUS sample."""
    rows = []
    for i in range(n):
        rows.append({
            "gl_code": str(base_gl + i), "gl_name": f"Account {i}",
            "closing_balance": float(50_000 * (i + 1)),
            "main_head": "Current Assets", "sub_head_1": "Trade receivables",
            "account_type": "Asset", "mapped_status": "MAPPED",
        })
    return rows


def _load(tmp_path):
    return json.loads((Path(tmp_path) / "sample_selection.json").read_text(encoding="utf-8"))


class TestBothMethodsRun:
    def test_writes_both_methods_by_default(self, tmp_path):
        tb = write_canonical(tmp_path / "canonical_tb.parquet", _rows(20))
        _write_materiality(tmp_path)
        result = build_sample_selection(canonical_tb_file=str(tb), output_dir=str(tmp_path))
        assert result["execution_status"] == "SUCCESS"
        payload = _load(tmp_path)
        assert payload["methods_run"] == ["mus", "stratified"]
        assert "mus" in payload and "stratified" in payload

    def test_combined_total_is_mus_plus_stratified(self, tmp_path):
        """Wave 8 remark #13: MUS and stratified are two independent SA 530 techniques
        with their own valid sample sizes -- narrative text needs one canonical combined
        figure to cite instead of inventing its own."""
        tb = write_canonical(tmp_path / "canonical_tb.parquet", _rows(20))
        _write_materiality(tmp_path)
        build_sample_selection(canonical_tb_file=str(tb), output_dir=str(tmp_path))
        payload = _load(tmp_path)
        strat_total = sum(s["sample_size"] for s in payload["stratified"]["strata"].values())
        assert payload["total_sampled_items_mus_plus_stratified"] == payload["mus"]["sample_size"] + strat_total

    def test_method_param_restricts_to_one(self, tmp_path):
        tb = write_canonical(tmp_path / "canonical_tb.parquet", _rows(10))
        _write_materiality(tmp_path)
        build_sample_selection(canonical_tb_file=str(tb), output_dir=str(tmp_path), method="mus")
        payload = _load(tmp_path)
        assert payload["methods_run"] == ["mus"]
        assert "stratified" not in payload


class TestMUS:
    def test_reproducible_with_the_same_seed(self, tmp_path):
        tb = write_canonical(tmp_path / "canonical_tb.parquet", _rows(30))
        _write_materiality(tmp_path)
        build_sample_selection(canonical_tb_file=str(tb), output_dir=str(tmp_path), random_seed=7)
        first = _load(tmp_path)["mus"]["sampled_items"]
        build_sample_selection(canonical_tb_file=str(tb), output_dir=str(tmp_path), random_seed=7)
        second = _load(tmp_path)["mus"]["sampled_items"]
        assert first == second

    def test_different_seed_can_change_the_sample(self, tmp_path):
        tb = write_canonical(tmp_path / "canonical_tb.parquet", _rows(30))
        _write_materiality(tmp_path)
        build_sample_selection(canonical_tb_file=str(tb), output_dir=str(tmp_path), random_seed=1)
        a = _load(tmp_path)["mus"]["random_start"]
        build_sample_selection(canonical_tb_file=str(tb), output_dir=str(tmp_path), random_seed=2)
        b = _load(tmp_path)["mus"]["random_start"]
        assert a != b

    def test_item_larger_than_the_interval_is_a_certainty_item(self, tmp_path):
        rows = _rows(5) + [{
            "gl_code": "9999", "gl_name": "Huge Balance", "closing_balance": 50_000_000.0,
            "main_head": "Current Assets", "sub_head_1": "Trade receivables",
            "account_type": "Asset", "mapped_status": "MAPPED",
        }]
        tb = write_canonical(tmp_path / "canonical_tb.parquet", rows)
        _write_materiality(tmp_path)
        build_sample_selection(canonical_tb_file=str(tb), output_dir=str(tmp_path))
        mus = _load(tmp_path)["mus"]
        huge = next(i for i in mus["sampled_items"] if i["gl_code"] == "9999")
        assert huge["selected_with_certainty"] is True
        assert huge["hits"] > 1

    def test_unknown_confidence_level_falls_back_to_95_and_says_so(self, tmp_path):
        tb = write_canonical(tmp_path / "canonical_tb.parquet", _rows(10))
        _write_materiality(tmp_path)
        build_sample_selection(canonical_tb_file=str(tb), output_dir=str(tmp_path), confidence_level=0.80)
        mus = _load(tmp_path)["mus"]
        assert mus["reliability_factor"] == 3.0
        assert "95%" in mus["reliability_factor_note"]

    def test_known_confidence_levels_use_the_standard_table(self, tmp_path):
        tb = write_canonical(tmp_path / "canonical_tb.parquet", _rows(10))
        _write_materiality(tmp_path)
        for level, factor in ((0.90, 2.3), (0.95, 3.0), (0.99, 4.6)):
            build_sample_selection(canonical_tb_file=str(tb), output_dir=str(tmp_path), confidence_level=level)
            mus = _load(tmp_path)["mus"]
            assert mus["reliability_factor"] == factor
            assert mus["reliability_factor_note"] is None


class TestStratified:
    def test_every_stratum_gets_at_least_one_item_when_populated(self, tmp_path):
        # Spans Critical (>100x OM) down to Below Threshold (<0.75x OM) at OM=1,000,000.
        rows = [
            {"gl_code": "1", "gl_name": "Critical", "closing_balance": 200_000_000.0,
             "main_head": "Assets", "account_type": "Asset", "mapped_status": "MAPPED"},
            {"gl_code": "2", "gl_name": "High", "closing_balance": 30_000_000.0,
             "main_head": "Assets", "account_type": "Asset", "mapped_status": "MAPPED"},
            {"gl_code": "3", "gl_name": "Medium", "closing_balance": 6_000_000.0,
             "main_head": "Assets", "account_type": "Asset", "mapped_status": "MAPPED"},
            {"gl_code": "4", "gl_name": "Low", "closing_balance": 900_000.0,
             "main_head": "Assets", "account_type": "Asset", "mapped_status": "MAPPED"},
            {"gl_code": "5", "gl_name": "Trivial", "closing_balance": 10_000.0,
             "main_head": "Assets", "account_type": "Asset", "mapped_status": "MAPPED"},
        ]
        tb = write_canonical(tmp_path / "canonical_tb.parquet", rows)
        _write_materiality(tmp_path)
        build_sample_selection(canonical_tb_file=str(tb), output_dir=str(tmp_path))
        strata = _load(tmp_path)["stratified"]["strata"]
        for name in ("Critical", "High", "Medium", "Low", "Below Threshold"):
            assert strata[name]["population_count"] == 1, name
            assert strata[name]["sample_size"] == 1, name

    def test_reproducible_with_the_same_seed(self, tmp_path):
        tb = write_canonical(tmp_path / "canonical_tb.parquet", _rows(40))
        _write_materiality(tmp_path)
        build_sample_selection(canonical_tb_file=str(tb), output_dir=str(tmp_path), random_seed=3)
        first = _load(tmp_path)["stratified"]["strata"]
        build_sample_selection(canonical_tb_file=str(tb), output_dir=str(tmp_path), random_seed=3)
        second = _load(tmp_path)["stratified"]["strata"]
        assert first == second


class TestScopingAndDegradation:
    def test_population_head_scopes_the_sample(self, tmp_path):
        rows = _rows(5) + [{
            "gl_code": "8000", "gl_name": "Share Capital", "closing_balance": -5_000_000.0,
            "main_head": "Equity", "account_type": "Equity", "mapped_status": "MAPPED",
        }]
        tb = write_canonical(tmp_path / "canonical_tb.parquet", rows)
        _write_materiality(tmp_path)
        build_sample_selection(canonical_tb_file=str(tb), output_dir=str(tmp_path), population_head="Equity")
        payload = _load(tmp_path)
        assert payload["mus"]["population_count"] == 1
        assert payload["mus"]["sampled_items"][0]["gl_code"] == "8000"

    def test_fails_clearly_without_materiality_thresholds(self, tmp_path):
        tb = write_canonical(tmp_path / "canonical_tb.parquet", _rows(5))
        (tmp_path / "materiality.json").write_text(json.dumps({"thresholds": {}}), encoding="utf-8")
        result = build_sample_selection(canonical_tb_file=str(tb), output_dir=str(tmp_path))
        assert result["execution_status"] == "FAILED"

    def test_degrades_when_population_is_empty(self, tmp_path):
        rows = [{
            "gl_code": "1", "gl_name": "Unmapped Only", "closing_balance": 100.0,
            "mapped_status": "UNMATCHED",
        }]
        tb = write_canonical(tmp_path / "canonical_tb.parquet", rows)
        _write_materiality(tmp_path)
        result = build_sample_selection(canonical_tb_file=str(tb), output_dir=str(tmp_path))
        assert result["execution_status"] == "SUCCESS"
        assert result["pipeline_status"] == "WARNING"
        assert not (tmp_path / "sample_selection.json").exists()
