"""Tests for backend/tools/risk/build_anomaly_scanner.py's deterministic
sub-checks (TB-021 Benford's Law, TB-023 round-numbers, TB-030 duplicate
descriptions, TB-033 year-consistency). TB-022 (semantic mismatch) needs an
LLM client -- covered separately by its own graceful-degradation test."""

import json

import pytest

from modes.trial_balance.pipeline.tools import build_anomaly_scanner


def _rule_ids(findings):
    return {f["rule_id"] for f in findings}


class TestRoundNumberCheck:
    def test_flags_high_round_figure_percentage(self, make_canonical_tb, tmp_path):
        # 5 of 6 nonzero closing balances are round multiples of 1000 >= 10000 magnitude.
        rows = [
            {"gl_code": "1001", "gl_name": "Cash", "closing_balance": 50_000_000.0},
            {"gl_code": "1002", "gl_name": "Bank", "closing_balance": 20_000.0},
            {"gl_code": "1003", "gl_name": "Advance", "closing_balance": 300_000.0},
            {"gl_code": "2001", "gl_name": "Payable", "closing_balance": -45_000.0},
            {"gl_code": "2002", "gl_name": "Revenue", "closing_balance": -9_999_000.0},
            {"gl_code": "2003", "gl_name": "Receivable", "closing_balance": 123_456.0},
        ]
        canonical_tb_file = make_canonical_tb(rows)
        result = build_anomaly_scanner(canonical_tb_file=str(canonical_tb_file), output_dir=str(tmp_path))
        assert result["execution_status"] == "SUCCESS"

        with open(tmp_path / "anomaly_findings.json") as f:
            payload = json.load(f)
        assert payload["checks"]["TB-023"]["status"] == "FLAGGED"
        assert "TB-023" in _rule_ids(payload["findings"])
        round_findings = [f for f in payload["findings"] if f["rule_id"] == "TB-023"]
        assert len(round_findings) == 5
        assert "2003" not in {f["account"] for f in round_findings}

    def test_passes_when_few_round_figures(self, make_canonical_tb, tmp_path):
        rows = [
            {"gl_code": "1001", "gl_name": "Cash", "closing_balance": 123_456.0},
            {"gl_code": "1002", "gl_name": "Bank", "closing_balance": 654_321.0},
            {"gl_code": "1003", "gl_name": "Advance", "closing_balance": 111_222.0},
        ]
        canonical_tb_file = make_canonical_tb(rows)
        result = build_anomaly_scanner(canonical_tb_file=str(canonical_tb_file), output_dir=str(tmp_path))
        with open(tmp_path / "anomaly_findings.json") as f:
            payload = json.load(f)
        assert payload["checks"]["TB-023"]["status"] == "PASS"


class TestDuplicateDescriptionCheck:
    def test_flags_near_identical_gl_names(self, make_canonical_tb, tmp_path):
        rows = [
            {"gl_code": "1001", "gl_name": "Cash in Hand", "closing_balance": 1000.0},
            {"gl_code": "1002", "gl_name": "Cash on Hand", "closing_balance": 500.0},
            {"gl_code": "1003", "gl_name": "Fully Unrelated Ledger", "closing_balance": 250.0},
        ]
        canonical_tb_file = make_canonical_tb(rows)
        result = build_anomaly_scanner(canonical_tb_file=str(canonical_tb_file), output_dir=str(tmp_path))
        with open(tmp_path / "anomaly_findings.json") as f:
            payload = json.load(f)
        assert payload["checks"]["TB-030"]["status"] == "FLAGGED"
        dup_findings = [f for f in payload["findings"] if f["rule_id"] == "TB-030"]
        assert len(dup_findings) == 1

    def test_passes_when_descriptions_are_distinct(self, make_canonical_tb, tmp_path):
        rows = [
            {"gl_code": "1001", "gl_name": "Cash in Hand", "closing_balance": 1000.0},
            {"gl_code": "2001", "gl_name": "Trade Payables", "closing_balance": -500.0},
        ]
        canonical_tb_file = make_canonical_tb(rows)
        result = build_anomaly_scanner(canonical_tb_file=str(canonical_tb_file), output_dir=str(tmp_path))
        with open(tmp_path / "anomaly_findings.json") as f:
            payload = json.load(f)
        assert payload["checks"]["TB-030"]["status"] == "PASS"


class TestYearConsistencyCheck:
    def test_flags_stale_year_reference(self, make_canonical_tb, tmp_path):
        rows = [
            {"gl_code": "1001", "gl_name": "Advance - Project FY2016-17", "closing_balance": 300_000.0},
            {"gl_code": "1002", "gl_name": "Cash in Hand", "closing_balance": 1000.0},
        ]
        canonical_tb_file = make_canonical_tb(rows)
        result = build_anomaly_scanner(canonical_tb_file=str(canonical_tb_file), output_dir=str(tmp_path), tb_year=2026)
        with open(tmp_path / "anomaly_findings.json") as f:
            payload = json.load(f)
        assert payload["checks"]["TB-033"]["status"] == "FLAGGED"
        year_findings = [f for f in payload["findings"] if f["rule_id"] == "TB-033"]
        assert year_findings[0]["account"] == "1001"
        assert year_findings[0]["trigger_value"] == 2016

    def test_skipped_when_no_tb_year_supplied(self, make_canonical_tb, tmp_path):
        rows = [{"gl_code": "1001", "gl_name": "Advance - Project FY2016-17", "closing_balance": 300_000.0}]
        canonical_tb_file = make_canonical_tb(rows)
        result = build_anomaly_scanner(canonical_tb_file=str(canonical_tb_file), output_dir=str(tmp_path))
        with open(tmp_path / "anomaly_findings.json") as f:
            payload = json.load(f)
        assert payload["checks"]["TB-033"]["status"] == "SKIPPED"
        assert "TB-033" not in _rule_ids(payload["findings"])

    def test_recent_year_within_gap_not_flagged(self, make_canonical_tb, tmp_path):
        rows = [{"gl_code": "1001", "gl_name": "Provision for Audit Fee - FY2023-24", "closing_balance": 5000.0}]
        canonical_tb_file = make_canonical_tb(rows)
        result = build_anomaly_scanner(canonical_tb_file=str(canonical_tb_file), output_dir=str(tmp_path), tb_year=2026)
        with open(tmp_path / "anomaly_findings.json") as f:
            payload = json.load(f)
        # 2026 - 2023 = 3, at the threshold (> 3 required to flag) -- must not flag.
        assert payload["checks"]["TB-033"]["status"] == "PASS"


class TestSemanticMismatchGracefulDegradation:
    def test_skipped_when_no_llm_client_supplied(self, make_canonical_tb, tmp_path):
        rows = [{"gl_code": "1001", "gl_name": "Office Rent Paid", "closing_balance": 1000.0}]
        canonical_tb_file = make_canonical_tb(rows)
        result = build_anomaly_scanner(canonical_tb_file=str(canonical_tb_file), output_dir=str(tmp_path))
        assert result["execution_status"] == "SUCCESS"  # never fails just because llm_client is absent
        with open(tmp_path / "anomaly_findings.json") as f:
            payload = json.load(f)
        assert payload["checks"]["TB-022"]["status"] == "SKIPPED"

    def test_skipped_gracefully_when_llm_call_raises(self, make_canonical_tb, tmp_path):
        rows = [
            {"gl_code": "1001", "gl_name": "Office Rent Paid", "closing_balance": 1000.0, "main_head": "Fixed Assets"},
        ]
        canonical_tb_file = make_canonical_tb(rows)

        class _BrokenLlmClient:
            def generate(self, *args, **kwargs):
                raise RuntimeError("simulated LLM outage")

        result = build_anomaly_scanner(
            canonical_tb_file=str(canonical_tb_file), output_dir=str(tmp_path), llm_client=_BrokenLlmClient()
        )
        assert result["execution_status"] == "SUCCESS"  # LLM failure must never crash the pipeline
        with open(tmp_path / "anomaly_findings.json") as f:
            payload = json.load(f)
        assert payload["checks"]["TB-022"]["status"] == "SKIPPED"
        assert "LLM call failed" in payload["checks"]["TB-022"]["reason"]


class TestEmptyCanonicalTb:
    def test_empty_canonical_tb_produces_no_findings_without_crashing(self, make_canonical_tb, tmp_path):
        canonical_tb_file = make_canonical_tb([])
        result = build_anomaly_scanner(canonical_tb_file=str(canonical_tb_file), output_dir=str(tmp_path))
        assert result["execution_status"] == "SUCCESS"
        assert result["artifacts"] == []
