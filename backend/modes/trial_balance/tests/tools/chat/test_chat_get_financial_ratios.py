"""chat_get_financial_ratios (chat_query_build_spec_v2.md tool 5) stays a fixed-enum point
lookup, not a chat_query_fsli_table filter -- ratio formulas are a closed, audit-standard
set."""

import json

from modes.trial_balance.pipeline.tools import chat_get_financial_ratios


def _write_ratios(tmp_path):
    path = tmp_path / "financial_ratios.json"
    path.write_text(json.dumps({
        "ratios": {
            "current": {"value": 1.5, "formula": "Current Assets / Current Liabilities", "components_missing": []},
            "roce": {"value": None, "formula": "EBIT / (Total Equity + Total Debt)", "components_missing": ["total_debt"]},
        },
    }))
    return path


def test_named_ratio_returns_single_entry(tmp_path):
    path = _write_ratios(tmp_path)
    result = chat_get_financial_ratios(str(path), ratio_name="current")
    assert result["execution_status"] == "SUCCESS"
    assert result["data"]["current"]["value"] == 1.5


def test_missing_ratio_reports_components_missing_in_message(tmp_path):
    path = _write_ratios(tmp_path)
    result = chat_get_financial_ratios(str(path), ratio_name="roce")
    assert result["execution_status"] == "SUCCESS"
    assert "total_debt" in result["message"]


def test_no_ratio_name_returns_all(tmp_path):
    path = _write_ratios(tmp_path)
    result = chat_get_financial_ratios(str(path))
    assert result["execution_status"] == "SUCCESS"
    assert set(result["data"].keys()) == {"current", "roce"}


def test_unknown_ratio_name_fails_cleanly(tmp_path):
    path = _write_ratios(tmp_path)
    result = chat_get_financial_ratios(str(path), ratio_name="not_a_real_ratio")
    assert result["execution_status"] == "FAILED"
