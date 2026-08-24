"""Chat-query Bucket-A build: chat_query_flags is the parametric replacement for
chat_get_layer1_findings/chat_get_description_quality/chat_get_negative_balance_accounts/
chat_get_sensitive_items/chat_get_gl_integrity_issues's duplicate-code half
(chat_query_build_spec_v2.md section 5). One test per category."""

import json

import polars as pl

from modes.trial_balance.pipeline.tools import chat_query_flags


def test_duplicate_gl_code_filters_layer1_findings(tmp_path):
    findings_path = tmp_path / "layer1_findings.parquet"
    pl.DataFrame({
        "rule_id": ["TB-002", "TB-003", "TB-005"],
        "rule_name": ["a", "b", "c"],
        "severity": ["Blocking", "Blocking", "Warning"],
        "status": ["HALTED", "HALTED", "WARNING"],
        "gl_code": ["100", "200", "300"],
        "gl_name": ["X", "Y", "Z"],
        "source_sheet": ["s1", "s1", "s1"],
        "source_row": [1, 2, 3],
        "message": ["Blank GL code", "Duplicate GL code", "Mismatch"],
    }).write_parquet(findings_path)

    result = chat_query_flags(category="duplicate_gl_code", layer1_findings_file=str(findings_path))
    assert result["execution_status"] == "SUCCESS"
    assert len(result["data"]) == 2
    assert {r["rule_id"] for r in result["data"]} == {"TB-002", "TB-003"}


def test_duplicate_description_parses_tb030_and_scans_generic_names(tmp_path):
    anomaly_path = tmp_path / "anomaly_findings.json"
    anomaly_path.write_text(json.dumps({
        "findings": [{
            "rule_id": "TB-030", "account": "100",
            "description": "GL '100' (Bank Charges) and GL '200' (Bank Charges Paid) are 92% similar -- possible duplicate account setup.",
            "trigger_value": 0.92,
        }],
    }))
    canonical_path = tmp_path / "canonical_tb.parquet"
    pl.DataFrame({
        "gl_code": ["300", "400"], "gl_name": ["Misc", "Legitimate Account Name"],
        "opening_balance": [0.0, 0.0], "debit": [0.0, 0.0], "credit": [0.0, 0.0], "closing_balance": [100.0, 200.0],
        "bs_pl": ["BS", "BS"], "main_head": ["Assets", "Assets"], "sub_head_1": ["A", "A"], "sub_head_2": [None, None],
        "account_type": ["Assets", "Assets"], "mapped_status": ["MAPPED", "MAPPED"],
    }).write_parquet(canonical_path)

    result = chat_query_flags(
        category="duplicate_description", anomaly_findings_file=str(anomaly_path), canonical_tb_file=str(canonical_path),
    )
    assert result["execution_status"] == "SUCCESS"
    checks = {r.get("check") for r in result["data"]}
    assert "fuzzy_duplicate_pair" in checks
    assert "generic_or_blank_description" in checks
    generic_rows = [r for r in result["data"] if r.get("check") == "generic_or_blank_description"]
    assert generic_rows[0]["gl_code"] == "300"


def test_sign_anomaly_reads_tb000_from_layer1_results(tmp_path):
    results_path = tmp_path / "layer1_results.json"
    results_path.write_text(json.dumps([
        {"rule": "TB-000", "rule_name": "Sign convention", "status": "WARNING", "severity": "Blocking", "message": "3 accounts opposite normal side"},
        {"rule": "TB-002", "rule_name": "x", "status": "PASS", "severity": "Blocking", "message": "ok"},
    ]))
    result = chat_query_flags(category="sign_anomaly", layer1_results_file=str(results_path))
    assert result["execution_status"] == "SUCCESS"
    assert len(result["data"]) == 1
    assert result["data"][0]["rule"] == "TB-000"


def test_sensitive_keyword_filters_by_category(tmp_path):
    sens_path = tmp_path / "sensitive_accounts.parquet"
    pl.DataFrame({
        "gl_code": ["1", "2"], "gl_name": ["Loan From Parent Co", "GST Payable"],
        "category": ["related_party", "statutory_dues"], "balance": [1000.0, 500.0],
    }).write_parquet(sens_path)

    result = chat_query_flags(category="sensitive_keyword", sensitive_accounts_file=str(sens_path), sensitive_category="related_party")
    assert result["execution_status"] == "SUCCESS"
    assert len(result["data"]) == 1
    assert result["data"][0]["category"] == "related_party"


def test_negative_balance_flags_expected_positive_accounts(tmp_path):
    canonical_path = tmp_path / "canonical_tb.parquet"
    pl.DataFrame({
        "gl_code": ["1", "2"], "gl_name": ["Trade Receivable - ABC", "Cash in Hand"],
        "opening_balance": [0.0, 0.0], "debit": [0.0, 0.0], "credit": [0.0, 0.0],
        "closing_balance": [-500.0, 200.0],
        "bs_pl": ["BS", "BS"], "main_head": ["Current assets", "Current assets"],
        "sub_head_1": ["Trade Receivables", "Cash"], "sub_head_2": [None, None],
        "account_type": ["Assets", "Assets"], "mapped_status": ["MAPPED", "MAPPED"],
    }).write_parquet(canonical_path)

    result = chat_query_flags(category="negative_balance", canonical_tb_file=str(canonical_path))
    assert result["execution_status"] == "SUCCESS"
    assert len(result["data"]) == 1
    assert result["data"][0]["gl_code"] == "1"


def test_dormant_reuses_shared_dormant_rows_from_canonical(tmp_path):
    canonical_path = tmp_path / "canonical_tb.parquet"
    pl.DataFrame({
        "gl_code": ["1"], "gl_name": ["Share Capital"],
        "opening_balance": [1000.0], "debit": [0.0], "credit": [0.0], "closing_balance": [1000.0],
        "bs_pl": ["BS"], "main_head": ["Equity"], "sub_head_1": ["Share Capital"], "sub_head_2": [None],
        "account_type": ["Equity"], "mapped_status": ["MAPPED"],
    }).write_parquet(canonical_path)

    result = chat_query_flags(category="dormant", canonical_tb_file=str(canonical_path))
    assert result["execution_status"] == "SUCCESS"
    assert result["data"][0]["gl_code"] == "1"


def test_mapping_integrity_reports_duplicate_gls_collapse_count(tmp_path):
    stats_path = tmp_path / "grouping_statistics.json"
    stats_path.write_text(json.dumps({"duplicate_gls": 4}))
    result = chat_query_flags(category="mapping_integrity", grouping_statistics_file=str(stats_path))
    assert result["execution_status"] == "SUCCESS"
    assert result["data"][0]["duplicate_gls_collapsed"] == 4


def test_unknown_category_fails_cleanly():
    result = chat_query_flags(category="not_a_real_category")
    assert result["execution_status"] == "FAILED"
    assert result["data"] is None


def test_missing_file_raises_pipeline_file_error_handled_by_decorator():
    result = chat_query_flags(category="duplicate_gl_code", layer1_findings_file="/nonexistent/path.parquet")
    assert result["execution_status"] == "FAILED"
    assert "not found" in result["message"].lower()
