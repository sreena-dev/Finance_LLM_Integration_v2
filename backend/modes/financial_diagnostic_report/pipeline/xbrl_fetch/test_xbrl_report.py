"""
Hermetic tests for `xbrl_report.to_markdown()` — pure text assembly, no DB, no
network, no LLM. Pins that every section renders from a synthetic payload
shaped like the real block functions' output, and that missing/not-formed
blocks degrade to a stated placeholder rather than crashing.
"""
from __future__ import annotations

from .xbrl_report import to_markdown

DASH = {
    "doc_id": "doc-1",
    "company_name": "Test Co Ltd",
    "entity_cin": "U12345DL2000PLC000001",
    "fy_label": "FY2023-24",
    "lede": "Computed directly from as_db.",
    "tiles": [
        {"id": "T01", "label": "Current ratio", "computed": True, "display": "1.20",
         "unit_label": "x", "movement_label": "up from 1.10x", "attention": False},
        {"id": "T02", "label": "Debt-equity", "computed": False,
         "reason": "No borrowings reported this year."},
    ],
    "total_count": 2,
    "computed_count": 1,
}

PROFILE = {
    "formed": True,
    "fields": [{"key": "model", "label": "Business model", "text": "Manufacturing."}],
    "reason": "",
}

HEALTH = {
    "formed": True,
    "business_type": "Manufacturer of widgets.",
    "structure": "Assets are dominated by PPE.",
    "performance": "Profit grew on flat margin.",
    "citations": [{"concept": "Assets", "value": 100, "scale": "crore"}],
    "reason": "",
}

TRENDS = {
    "formed": True,
    "series_years": 3,
    "period_labels": ["2022", "2023", "2024"],
    "summary_lede": "Assets grew steadily.",
    "dupont_narrative": "Margin flat, turnover up.",
    "dupont_schedule": [{"period_date": "2024", "margin_display": "10%",
                          "turnover_display": "1.1x", "multiplier_display": "1.5x",
                          "roe_display": "16.5%"}],
    "structural_drift_cards": [{"signal_id": "S01", "title": "Rising CWIP",
                                 "severity": "high", "observation": "CWIP up 40%.",
                                 "audit_lead": "Check capitalisation.",
                                 "evidence_lead": "Project cost records."}],
    "common_size_highlights": [{"item": "PPE", "drift_pp": 3.2,
                                 "narrative": "PPE share rose."}],
}

RISK = {
    "risk_clusters": [
        {"id": "RC01", "theme": "Working-capital stress", "raised": True,
         "priority_rank": 1, "inherent_risk": "high", "diagnostic_confidence": "high",
         "significant_risk": True,
         "contributing_signals": [{"signal": "Negative OCF", "source_trace": "financial_facts: OCF"}],
         "alt_explanations": ["Growth-phase cash burn."],
         "affected_assertions": ["Completeness", "Valuation"],
         "recommended_response": {"nature": "Substantive testing", "timing": "Year-end",
                                   "extent": "Sample vouchers"},
         "evidence_request": "Cash flow forecasts.",
         "specialist_referral": "None",
         "priority_reasoning": "Prioritised due to cash deficit."},
        {"id": "RC06", "theme": "Government-dependency", "raised": False,
         "reason": "No grant signals detected."},
    ],
    "interactions": [{"cluster_a": "RC01", "cluster_b": "RC04",
                       "relationship": "reinforcing", "rationale": "Compounds risk."}],
}


def test_full_report_contains_every_block_heading():
    text = to_markdown(DASH, PROFILE, HEALTH, TRENDS, RISK)
    for heading in ("Coverage", "Executive dashboard", "Business profile",
                    "Financial health summary", "Key trends", "Key risk clusters",
                    "Audit-planning matrix", "Evidence matrix", "Planning summary"):
        assert heading in text


def test_raised_cluster_gets_full_card_and_unraised_gets_table_row():
    text = to_markdown(DASH, PROFILE, HEALTH, TRENDS, RISK)
    assert "Working-capital stress" in text
    assert "Negative OCF" in text
    assert "Government-dependency" in text
    assert "No grant signals detected." in text


def test_matrix_lists_only_raised_clusters_by_priority():
    text = to_markdown(DASH, PROFILE, HEALTH, TRENDS, RISK)
    matrix_section = text.split("## 7. Audit-planning matrix")[1].split("## 8.")[0]
    assert "Working-capital stress" in matrix_section
    assert "Government-dependency" not in matrix_section


def test_missing_blocks_degrade_to_placeholder_not_crash():
    text = to_markdown(DASH, None, None, None, None)
    assert "Not formed for this run." in text
    assert "Not built for this run." in text
    assert "No cluster reached the activation threshold" in text


def test_single_period_trends_shows_notice_not_dupont_table():
    single_period = {**TRENDS, "series_years": 1}
    text = to_markdown(DASH, PROFILE, HEALTH, single_period, RISK)
    assert "Initial reporting period notice" in text
    assert "Extended DuPont" not in text
