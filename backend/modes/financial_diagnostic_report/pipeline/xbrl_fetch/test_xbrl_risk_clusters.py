"""
Hermetic test suite for Block 6: Key Risk Clusters with Interactions.
Runs 100% offline with zero network and zero database dependencies.
"""
from __future__ import annotations
import pytest

from . import xbrl_risk_concepts as C
from .xbrl_risk_clusters import (
    detect_signals_and_metrics,
    evaluate_risk_interactions,
    deterministic_risk_clusters,
    lint_risk_clusters,
    build_risk_clusters,
    _clean_quote,
    _extract_qualification_snippet,
)


def _fact(concept: str, val: float, fy_start=None, fy_end="2025-03-31") -> dict:
    return {
        "concept_name": concept,
        "value_numeric": val,
        "fy_start": fy_start,
        "fy_end": fy_end,
        "unit": "INR",
    }


def test_risk_concepts_declarations():
    """Validates cluster registry integrity and non-duplication."""
    assert len(C.CLUSTER_IDS) == 6
    assert "RC01" in C.CLUSTER_NAMES
    assert "RC06" in C.CLUSTER_NAMES
    for cid in C.CLUSTER_IDS:
        assert cid in C.CLUSTER_ASSERTIONS, f"Missing assertions for {cid}"
        assert len(C.CLUSTER_ASSERTIONS[cid]) >= 2, f"Too few assertions for {cid}"
        assert cid in C.CLUSTER_SPECIALISTS, f"Missing specialist for {cid}"

    num_concepts = C.all_risk_numeric_concepts()
    assert len(num_concepts) == len(set(num_concepts)), "Duplicate numeric concepts found"
    assert "CashFlowsFromUsedInOperatingActivities" in num_concepts
    assert "CapitalWorkInProgress" in num_concepts


def test_detect_signals_and_metrics_leveraged_entity():
    """Tests signal detection for an operating entity with debt leverage and CWIP."""
    rows = [
        _fact("CurrentAssets", 50000000.0),
        _fact("CurrentLiabilities", 80000000.0),  # Net current liabilities
        _fact("CashFlowsFromUsedInOperatingActivities", -15000000.0),  # Negative OCF
        _fact("ProfitLossForPeriod", 10000000.0),
        _fact("RevenueFromOperations", 200000000.0),
        _fact("TradeReceivablesCurrent", 70000000.0),  # >25% of revenue
        _fact("PropertyPlantAndEquipment", 100000000.0),
        _fact("CapitalWorkInProgress", 50000000.0),  # 33% CWIP ratio
        _fact("Equity", 100000000.0),
        _fact("BorrowingsNoncurrent", 180000000.0),  # D/E = 1.8x
    ]
    signals, lookup = detect_signals_and_metrics(rows)
    sig_ids = {s["id"] for s in signals}

    assert "SIG_OCF_NEG" in sig_ids
    assert "SIG_NET_CURR_LIAB" in sig_ids
    assert "SIG_REC_CONCENTRATION" in sig_ids
    assert "SIG_CWIP_CONCENTRATION" in sig_ids
    assert "SIG_LEVERAGE_HIGH" in sig_ids
    assert lookup["de_ratio"] == 1.8


def test_evaluate_risk_interactions_reinforcing():
    """Negative OCF + High Leverage must trigger a reinforcing interaction."""
    signals = [
        {"id": "SIG_OCF_NEG", "cluster_id": "RC01"},
        {"id": "SIG_LEVERAGE_HIGH", "cluster_id": "RC04"},
    ]
    interactions = evaluate_risk_interactions(signals, {"pat": 100})
    assert len(interactions) >= 1
    rel = interactions[0]
    assert rel["cluster_a"] == "RC01"
    assert rel["cluster_b"] == "RC04"
    assert rel["relationship"] == "reinforcing"


def test_evaluate_risk_interactions_offsetting_pre_revenue():
    """Operating loss / negative cash flow in a debt-free startup must trigger offsetting interaction."""
    signals = [
        {"id": "SIG_OCF_NEG", "cluster_id": "RC01"},
        {"id": "SIG_DEBT_FREE", "cluster_id": "RC04"},
        {"id": "SIG_PRE_REVENUE", "cluster_id": "RC02"},
    ]
    interactions = evaluate_risk_interactions(signals, {"pat": -120000000.0, "borrowings": 0.0})
    assert any(i["relationship"] == "offsetting" for i in interactions)
    offsetting_item = next(i for i in interactions if i["relationship"] == "offsetting")
    assert offsetting_item["cluster_a"] == "RC01"
    assert offsetting_item["cluster_b"] == "RC04"
    assert "debt-free" in offsetting_item["rationale"].lower()


def test_deterministic_risk_clusters_package():
    """Validates full package schema generation via deterministic engine."""
    rows = [
        _fact("CurrentAssets", 100000.0),
        _fact("CurrentLiabilities", 50000.0),
        _fact("RevenueFromOperations", 0.0),  # Pre-revenue
        _fact("ProfitLossForPeriod", -10000.0),
        _fact("CashFlowsFromUsedInOperatingActivities", -15000.0),
        _fact("Equity", 50000000.0),
        _fact("BorrowingsCurrent", 0.0),
        _fact("BorrowingsNoncurrent", 0.0),
        _fact("PropertyPlantAndEquipment", 20000.0),
        _fact("CapitalWorkInProgress", 0.0),
    ]
    payload = build_risk_clusters(
        doc_id="test_doc.xml",
        company_name="TEST GREEN LTD",
        cin="U12345DL2024GOI123456",
        fy_label="FY2024-25",
        metric_rows=rows,
        disclosure_rows=[],
        use_llm=False,
    )
    assert payload["formed"] is True
    assert payload["doc_id"] == "test_doc.xml"
    assert "risk_clusters" in payload
    assert "interactions" in payload
    assert len(payload["risk_clusters"]) == 6

    # Verify all canonical IDs exist
    cluster_ids = [c["id"] for c in payload["risk_clusters"]]
    for cid in C.CLUSTER_IDS:
        assert cid in cluster_ids, f"Missing canonical cluster {cid}"

    # Verify raised cluster schema
    raised = [c for c in payload["risk_clusters"] if c.get("raised")]
    assert len(raised) >= 1
    c = raised[0]
    assert "id" in c
    assert "theme" in c
    assert "contributing_signals" in c
    assert "alt_explanations" in c
    assert "affected_assertions" in c
    assert "recommended_response" in c
    assert "priority_rank" in c
    assert "priority_reasoning" in c

    # Verify unraised cluster schema and substantive reason
    unraised = [c for c in payload["risk_clusters"] if not c.get("raised")]
    assert len(unraised) >= 1
    for u in unraised:
        assert len(u["reason"]) > 10, f"Unraised cluster {u['id']} missing substantive reason"
        assert u["priority_rank"] is None



def test_safe_language_lint_removes_prohibited_words():
    """Spec §17: Prohibited words like 'fraudulent', 'insolvent', 'proves' must be replaced."""
    bad_data = {
        "risk_clusters": [
            {
                "id": "RC01",
                "theme": "This proves the company is fraudulent",
                "control_implications": "The company is insolvent and will fail.",
                "alt_explanations": ["This confirms misstatement."],
                "recommended_response": {"nature": "Testing proves insolvency."},
                "evidence_request": "Confirm failure.",
                "priority_reasoning": "High because it will fail.",
            }
        ]
    }
    cleaned = lint_risk_clusters(bad_data)
    c = cleaned["risk_clusters"][0]
    assert "proves" not in c["theme"].lower()
    assert "fraudulent" not in c["theme"].lower()
    assert "insolvent" not in c["control_implications"].lower()
    assert "will fail" not in c["control_implications"].lower()


def test_llm_failure_falls_back_cleanly_without_crash():
    """When chat_fn raises, build_risk_clusters must catch it and produce valid fallback."""
    def _exploding_chat(*a, **k):
        raise ConnectionError("LLM service offline")

    rows = [_fact("RevenueFromOperations", 1000.0), _fact("Equity", 5000.0)]
    payload = build_risk_clusters(
        doc_id="test_doc.xml",
        company_name="TEST LTD",
        cin="L12345",
        fy_label="FY2024-25",
        metric_rows=rows,
        disclosure_rows=[],
        use_llm=True,
        chat_fn=_exploding_chat,
    )
    assert payload["formed"] is True
    assert len(payload["risk_clusters"]) >= 1
    assert "ConnectionError" not in payload["reason"]


def test_multi_period_isolation_prevents_prior_year_overwrite():
    """Validates that facts from prior comparative periods do NOT overwrite current year facts."""
    rows = [
        # Current year (2021-03-31)
        _fact("ProfitLossForPeriod", 6524706000.0, fy_end="2021-03-31"),
        _fact("RevenueFromOperations", 30383642000.0, fy_end="2021-03-31"),
        _fact("TradeReceivablesCurrent", 40065598000.0, fy_end="2021-03-31"),
        _fact("CapitalWorkInProgress", 1195538000.0, fy_end="2021-03-31"),
        _fact("CashFlowsFromUsedInOperatingActivities", 10953737000.0, fy_end="2021-03-31"),
        # Prior year (2020-03-31) - coming later in the list
        _fact("ProfitLossForPeriod", 15621552000.0, fy_end="2020-03-31"),
        _fact("RevenueFromOperations", 49723256000.0, fy_end="2020-03-31"),
        _fact("TradeReceivablesCurrent", 40556297000.0, fy_end="2020-03-31"),
        _fact("CapitalWorkInProgress", 1237253000.0, fy_end="2020-03-31"),
        _fact("CashFlowsFromUsedInOperatingActivities", 12247049000.0, fy_end="2020-03-31"),
    ]
    signals, lookup = detect_signals_and_metrics(rows)
    assert lookup["pat"] == 6524706000.0
    assert lookup["trade_rec"] == 40065598000.0
    assert lookup["cwip"] == 1195538000.0
    assert lookup["rev"] == 30383642000.0
    assert lookup["ocf"] == 10953737000.0
    # OCF (10,953.74m) > PAT (6,524.71m), so SIG_OCF_LAG_PAT must NOT trigger!
    sig_ids = {s["id"] for s in signals}
    assert "SIG_OCF_LAG_PAT" not in sig_ids
    # Trade receivables ratio: 40,065.6m / 30,383.6m = ~131.9%
    assert "SIG_REC_CONCENTRATION" in sig_ids
    rec_sig = next(s for s in signals if s["id"] == "SIG_REC_CONCENTRATION")
    assert "131.9%" in rec_sig["signal"] or "₹4,006.56 cr" in rec_sig["signal"]


def test_investment_concentration_signal_and_response():
    """Holding entity with massive investments (>30% of assets) must trigger RC03 with Valuation specialist."""
    rows = [
        _fact("Assets", 45985290000.0),
        _fact("NoncurrentInvestments", 43316280000.0),  # 94.2% of assets
        _fact("PropertyPlantAndEquipment", 20000.0),
        _fact("CapitalWorkInProgress", 0.0),
        _fact("Equity", 45818370000.0),
        _fact("RevenueFromOperations", 0.0),
    ]
    payload = build_risk_clusters(
        doc_id="ongc.xml",
        company_name="ONGC GREEN LIMITED",
        cin="U35105DL2024GOI427427",
        fy_label="FY2024-25",
        metric_rows=rows,
        disclosure_rows=[],
        use_llm=False,
    )
    rc03 = next(c for c in payload["risk_clusters"] if c["id"] == "RC03")
    assert rc03["raised"] is True
    assert "Asset and Investment Valuation" in rc03["theme"]
    assert rc03["significant_risk"] is True
    assert "Valuation" in rc03["specialist_referral"]
    assert "DCF" in rc03["evidence_request"]
    assert rc03["priority_rank"] == 1


def test_pre_revenue_zero_receivables_no_debtor_circularisation():
    """RC02 for a pre-revenue company with ₹0 debtors must NOT request debtor circularisation."""
    rows = [
        _fact("RevenueFromOperations", 0.0),
        _fact("TradeReceivablesCurrent", 0.0),
        _fact("Equity", 10000000.0),
    ]
    payload = build_risk_clusters(
        doc_id="test.xml",
        company_name="DEV CO",
        cin="U12345",
        fy_label="FY2024-25",
        metric_rows=rows,
        disclosure_rows=[],
        use_llm=False,
    )
    rc02 = next(c for c in payload["risk_clusters"] if c["id"] == "RC02")
    assert rc02["raised"] is True
    assert "circularisation" not in rc02["recommended_response"]["nature"].lower()
    assert "aged debtors" not in rc02["evidence_request"].lower()
    assert "cut-off" in rc02["recommended_response"]["nature"].lower()


def test_debt_free_no_covenant_or_loan_schedule():
    """RC04 for a debt-free company must search MCA-21 charges and bank confirmation, not loan covenants."""
    rows = [
        _fact("BorrowingsCurrent", 0.0),
        _fact("BorrowingsNoncurrent", 0.0),
        _fact("Equity", 100000000.0),
    ]
    payload = build_risk_clusters(
        doc_id="test.xml",
        company_name="DEBT FREE CO",
        cin="U12345",
        fy_label="FY2024-25",
        metric_rows=rows,
        disclosure_rows=[],
        use_llm=False,
    )
    rc04 = next(c for c in payload["risk_clusters"] if c["id"] == "RC04")
    assert rc04["raised"] is True
    assert "covenant" not in rc04["recommended_response"]["nature"].lower()
    assert "repayment schedule" not in rc04["evidence_request"].lower()
    assert "mca-21" in rc04["recommended_response"]["extent"].lower() or "mca-21" in rc04["evidence_request"].lower()


def test_statutory_auditor_qualification_raises_rc05_priority_1():
    """Auditor qualifications in disclosures must raise RC05 as Priority 1 with SA 705 response."""
    rows = [
        _fact("RevenueFromOperations", 10000000.0),
        _fact("Equity", 50000000.0),
    ]
    disclosures = [
        {
            "concept_name": "AuditorsQualificationsReservationsAdverseRemarksInAuditorsReport",
            "text": "The Company has not made the Provision for Expected Credit Loss in respect of Trade Receivables as prescribed under Ind AS-109.",
        }
    ]
    payload = build_risk_clusters(
        doc_id="test.xml",
        company_name="QUALIFIED CO",
        cin="U12345",
        fy_label="FY2020-21",
        metric_rows=rows,
        disclosure_rows=disclosures,
        use_llm=False,
    )
    rc05 = next(c for c in payload["risk_clusters"] if c["id"] == "RC05")
    assert rc05["raised"] is True
    assert "Auditor Qualification" in rc05["theme"]
    assert rc05["significant_risk"] is True
    assert rc05["priority_rank"] == 1
    assert "SA 705" in rc05["recommended_response"]["nature"]


def test_mis_bound_shareholding_disclosure_does_not_raise_false_auditor_qualification():
    """Regression: a routine Board's-Report disclosure (shareholding structure)
    bound under an auditor-qualification concept name must NOT be treated as a
    qualification — the concept name says where as_db filed the text, not what
    it says, and this exact text was observed to false-positive a Priority 1
    significant risk before the content-level guardrail was added."""
    rows = [
        _fact("RevenueFromOperations", 10000000.0),
        _fact("Equity", 50000000.0),
    ]
    disclosures = [
        {
            "concept_name": "AuditorsQualificationsReservationsAdverseRemarksInAuditorsReport",
            "text": "Holding Shares of OOIL as Nominee of ONGC Videsh Ltd. 13",
        }
    ]
    signals, _ = detect_signals_and_metrics(rows, disclosures)
    assert not any(s["id"] == "SIG_AUDITOR_QUALIFICATION" for s in signals)

    payload = build_risk_clusters(
        doc_id="test.xml",
        company_name="NOMINEE CO",
        cin="U12345",
        fy_label="FY2020-21",
        metric_rows=rows,
        disclosure_rows=disclosures,
        use_llm=False,
    )
    rc05 = next(c for c in payload["risk_clusters"] if c["id"] == "RC05")
    assert rc05["raised"] is False


def test_clean_quote_does_not_truncate_at_abbreviations():
    """Validates that _clean_quote does not truncate sentences at abbreviations like Note No. or Rs."""
    text = "We refer to Note No. 34 regarding advance of Rs. 2500.00 Lakhs received against land sale which is pending reconciliation."
    quote = _clean_quote(text, 120)
    assert not quote.endswith("Note No.")
    assert not quote.endswith("Note No")
    assert "Note No. 34" in quote
    assert not quote.endswith("Rs.")


def test_extract_qualification_snippet_skips_boilerplate():
    """Validates that _extract_qualification_snippet bypasses boilerplate 'We have audited' introductory text."""
    boilerplate = "We have audited the Standalone financial statements of M/S ABC Ltd which comprise the Balance sheet. Basis for Qualified Opinion: Attention is drawn to Note No. 24 regarding disputed excise liability of Rs. 50 cr."
    extracted = _extract_qualification_snippet(boilerplate, 150)
    assert "We have audited" not in extracted
    assert "disputed excise liability" in extracted


def test_other_income_pre_tax_loss_mitigation_narrative():
    """When PBT < 0, other income must report cushioning of operating loss, never % of profit."""
    rows = [
        _fact("ProfitBeforeTax", -606447881.0),
        _fact("OtherIncome", 449008985.0),
        _fact("Equity", 1000000000.0),
    ]
    signals, _ = detect_signals_and_metrics(rows)
    oi_sig = next(s for s in signals if s["id"] == "SIG_OTHER_INCOME_HIGH")
    assert "cushioned an operating loss" in oi_sig["signal"]
    assert "% of profit before tax" not in oi_sig["signal"]
    assert "-₹105.55 cr" in oi_sig["signal"]


def test_positive_ocf_with_accounting_loss_never_claims_cash_outflow():
    """When OCF > 0 but PAT < 0, Interaction 2 (cash outflow offset by debt free) must NOT trigger."""
    signals = [
        {"id": "SIG_DEBT_FREE", "cluster_id": "RC04"},
        {"id": "SIG_REC_CONCENTRATION", "cluster_id": "RC02"},
    ]
    # pat is negative (-₹39.95 cr) but OCF was positive (SIG_OCF_NEG not in signals)
    interactions = evaluate_risk_interactions(signals, {"pat": -399500000.0, "ocf": 686400000.0})
    # Must NOT have RC01 <-> RC04 interaction claiming cash outflow!
    assert not any(i["cluster_a"] == "RC01" and i["cluster_b"] == "RC04" for i in interactions)


def test_auditor_qualification_domain_dispatch():
    """Validates that receivables qualifications link RC05 <-> RC02, while liability/advance reservations link RC05 <-> RC01."""
    # 1. Receivables qualification
    rows_rec = [
        _fact("RevenueFromOperations", 100000000.0),
        _fact("TradeReceivablesCurrent", 80000000.0),
    ]
    disc_rec = [{
        "concept_name": "AuditorsQualificationsReservationsAdverseRemarksInAuditorsReport",
        "text": "The Company has not made the Provision for Expected Credit Loss in respect of Trade Receivables as prescribed under Ind AS-109.",
    }]
    sig_rec, look_rec = detect_signals_and_metrics(rows_rec, disc_rec)
    inter_rec = evaluate_risk_interactions(sig_rec, look_rec)
    assert any(i["cluster_a"] == "RC05" and i["cluster_b"] == "RC02" for i in inter_rec)
    assert not any(i["cluster_a"] == "RC05" and i["cluster_b"] == "RC01" for i in inter_rec)

    # 2. Advance / Liabilities reservation (e.g. WB HIDCO)
    rows_liab = [
        _fact("RevenueFromOperations", 100000000.0),
        _fact("TradeReceivablesCurrent", 35000000.0),  # > 25% of revenue
    ]
    disc_liab = [{
        "concept_name": "AuditorsQualificationsReservationsOrAdverseRemarksInAuditorsReport",
        "text": "Attention is drawn regarding advance of Rs. 256.09 cr received against sale/lease of land which is lying outstanding and unadjusted as on March 31, 2021.",
    }]
    sig_liab, look_liab = detect_signals_and_metrics(rows_liab, disc_liab)
    inter_liab = evaluate_risk_interactions(sig_liab, look_liab)
    # Must link to RC01 (unadjusted advances/liabilities), NOT falsely to RC02 (receivables ECL)
    assert any(i["cluster_a"] == "RC05" and i["cluster_b"] == "RC01" for i in inter_liab)
    assert not any(i["cluster_a"] == "RC05" and i["cluster_b"] == "RC02" for i in inter_liab)


def test_risk_clusters_format_inr_adaptive_scaling():
    from .xbrl_risk_clusters import _format_inr
    assert _format_inr(None) == "N/A"
    assert _format_inr(0) == "₹0.00"
    assert _format_inr(50_000) == "₹0.50 lakh"
    assert _format_inr(4_500_000) == "₹45.00 lakh"
    assert _format_inr(10_000_000) == "₹1.00 cr"
    assert _format_inr(250_000_000) == "₹25.00 cr"
    assert _format_inr(-15_000) == "-₹0.15 lakh"
    assert _format_inr(-30_000_000) == "-₹3.00 cr"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


