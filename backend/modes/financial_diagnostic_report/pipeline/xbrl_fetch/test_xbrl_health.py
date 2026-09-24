"""
test_xbrl_health.py — Hermetic Unit Tests for Block 4: Financial Health Summary.

100% offline, zero database/network dependencies.
Validates:
1. Native unit scaling detection (_detect_native_scale) and formatting (_format_amount)
2. Business context and activity extraction (products, NIC codes, CIN industry, turnover shares)
3. Balance sheet structure and capital mix computation (assets, CWIP breakdown, investments, liquidity, debt/equity)
4. Performance decomposition (profit delta, revenue driver, depletion/impairment, provisions, deferred tax, cash backing)
5. Deterministic narrative synthesis (production audit phrasing matching FDR Spec §7, §8, §9)
6. Safe audit language linting (SA 315 compliance, §17)
7. LLM integration and resilient deterministic fallback
"""
from __future__ import annotations
import json
import pytest

from .xbrl_health import (
    _clean_str,
    _safe_div,
    _detect_native_scale,
    _format_amount,
    extract_business_context,
    compute_health_metrics,
    deterministic_health_summary,
    lint_health_output,
    build_health_summary,
)


def _fact(concept: str, val: float, fy_end: str = "2024-03-31", fy_start: str = "2023-04-01") -> dict:
    return {
        "concept_name": concept,
        "fy_start": fy_start,
        "fy_end": fy_end,
        "value_numeric": val,
        "unit": "INR",
        "has_dimensions": False,
    }


def _disclosure(concept: str, text: str) -> dict:
    return {
        "concept_name": concept,
        "text": text,
        "section_title": "Notes",
        "disclosure_category": "AccountingPolicies",
    }


# ---------------------------------------------------------------------------
# Test 1: Native Unit Scaling & Amount Formatting
# ---------------------------------------------------------------------------

def test_detect_native_scale_and_formatting():
    # Disclosure explicitly states Millions
    disc_millions = [_disclosure("LevelOfRoundingUsedInFinancialStatements", "Millions")]
    scale_m = _detect_native_scale(disc_millions)
    assert scale_m == "Millions"
    assert _format_amount(1437941 * 1e6, scale_m) == "₹1,437,941 M"
    assert _format_amount(255613 * 1e6, scale_m) == "₹255,613 M"

    # Disclosure explicitly states Crores
    disc_crores = [_disclosure("LevelOfRoundingUsedInFinancialStatements", "In Crores")]
    scale_cr = _detect_native_scale(disc_crores)
    assert scale_cr == "Crores"
    assert _format_amount(587.57 * 1e7, scale_cr) == "₹587.57 cr"

    # Disclosure explicitly states Lakhs
    disc_lakhs = [_disclosure("LevelOfRoundingUsedInFinancialStatements", "Lakhs")]
    scale_l = _detect_native_scale(disc_lakhs)
    assert scale_l == "Lakhs"
    assert _format_amount(45.00 * 1e5, scale_l) == "₹45.00 lakh"

    # Edge cases
    assert _format_amount(0.0, "Millions") == "₹0.00"
    assert _format_amount(None, "Crores") == "N/A"
    assert _format_amount(-23004 * 1e6, "Millions") == "-₹23,004 M"


# ---------------------------------------------------------------------------
# Test 2: Business Context & Activity Extraction
# ---------------------------------------------------------------------------

def test_extract_business_context():
    disclosures = [
        _disclosure("NameOfMainProductOrService", "Crude Oil"),
        _disclosure("NameOfMainProductOrService", "Natural Gas"),
        _disclosure("NICCodeOfProductOrService", "061"),
        _disclosure("LevelOfRoundingUsedInFinancialStatements", "Millions"),
    ]
    turnover_facts = [
        {
            "concept_name": "PercentageToTotalTurnoverOfCompany",
            "dimensions": {"PrincipalBusinessActivitiesOfCompany": "Crude Oil"},
            "value_numeric": 72.5,
        },
        {
            "concept_name": "PercentageToTotalTurnoverOfCompany",
            "dimensions": {"PrincipalBusinessActivitiesOfCompany": "Natural Gas"},
            "value_numeric": 21.0,
        },
    ]
    cin = "L74899DL1993GOI054155"

    ctx = extract_business_context(turnover_facts, disclosures, cin)
    assert ctx["main_products"] == ["Crude Oil", "Natural Gas"]
    assert ctx["nic_codes"] == ["061"]
    assert ctx["cin_industry"] == "74899"
    assert ctx["rounding_scale"] == "Millions"
    assert len(ctx["turnover_shares"]) == 2
    assert "Core activities and main products/services: Crude Oil, Natural Gas." in ctx["summary"]
    assert "Product NIC classification: 061." in ctx["summary"]


# ---------------------------------------------------------------------------
# Test 3: Balance Sheet Structure & Performance Decomposition (ONGC-style)
# ---------------------------------------------------------------------------

def test_compute_health_metrics_ongc_archetype():
    # t0 = 2024-03-31, t_prev = 2023-03-31
    # Scale: Millions
    facts = [
        # t0 Structure
        _fact("Assets", 4500000.0 * 1e6, "2024-03-31"),
        _fact("ProducingProperties", 1437941.0 * 1e6, "2024-03-31"),
        _fact("CapitalWorkInProgress", 470819.0 * 1e6, "2024-03-31"),
        _fact("FacilitiesInProgress", 255613.0 * 1e6, "2024-03-31"),
        _fact("ExploratoryWellsInProgress", 153929.0 * 1e6, "2024-03-31"),
        _fact("DevelopmentWellsInProgress", 61277.0 * 1e6, "2024-03-31"),
        _fact("NoncurrentInvestments", 1133786.0 * 1e6, "2024-03-31"),
        _fact("CurrentAssets", 618024.0 * 1e6, "2024-03-31"),
        _fact("CurrentLiabilities", 371643.0 * 1e6, "2024-03-31"),
        _fact("Equity", 3000000.0 * 1e6, "2024-03-31"),
        _fact("BorrowingsNoncurrent", 50000.0 * 1e6, "2024-03-31"),
        # t0 Performance
        _fact("RevenueFromOperations", 1384020.0 * 1e6, "2024-03-31"),
        _fact("ProfitLossForPeriod", 328940.0 * 1e6, "2024-03-31"),
        _fact("DepreciationDepletionAndAmortisationExpense", 251338.0 * 1e6, "2024-03-31"),
        _fact("ProvisionsCurrent", 20000.0 * 1e6, "2024-03-31"),
        _fact("ProvisionsNoncurrent", 19476.0 * 1e6, "2024-03-31"),
        _fact("DeferredTaxExpense", -23004.0 * 1e6, "2024-03-31"),  # Credit
        _fact("CashFlowsFromUsedInOperatingActivities", 600000.0 * 1e6, "2024-03-31"),

        # t_prev Structure & Performance
        _fact("RevenueFromOperations", 1555170.0 * 1e6, "2023-03-31"),
        _fact("ProfitLossForPeriod", 356103.0 * 1e6, "2023-03-31"),
        _fact("ProvisionsCurrent", 15000.0 * 1e6, "2023-03-31"),
        _fact("ProvisionsNoncurrent", 13054.0 * 1e6, "2023-03-31"),  # total 28,054M -> +40.7%
        _fact("CashFlowsFromUsedInOperatingActivities", 580000.0 * 1e6, "2023-03-31"),
    ]

    disclosures = [
        _disclosure("LevelOfRoundingUsedInFinancialStatements", "Millions"),
        _disclosure("NameOfMainProductOrService", "Crude Oil"),
        _disclosure("NameOfMainProductOrService", "Natural Gas"),
    ]
    meta = {
        "entity_cin": "L74899DL1993GOI054155",
        "company_name": "Oil and Natural Gas Corporation Limited",
    }

    metrics = compute_health_metrics(facts, [], disclosures, meta)
    assert metrics["scale"] == "Millions"
    st = metrics["structure"]
    assert st["dominant_label"] == "oil & gas assets"
    assert pytest.approx(st["dominant_fixed"] / 1e6, 0.1) == 1437941.0
    assert pytest.approx(st["facilities_prog"] / 1e6, 0.1) == 255613.0
    assert pytest.approx(st["exploratory_wells"] / 1e6, 0.1) == 153929.0
    assert pytest.approx(st["development_wells"] / 1e6, 0.1) == 61277.0
    assert pytest.approx(st["inv_share"], 0.1) == (1133786.0 / 4500000.0) * 100  # ≈25%
    assert st["net_wc"] == (618024.0 - 371643.0) * 1e6  # positive surplus

    pf = metrics["performance"]
    assert pytest.approx(pf["pat_prev"] / 1e6, 0.1) == 356103.0
    assert pytest.approx(pf["pat_t0"] / 1e6, 0.1) == 328940.0
    assert pf["delta_pat"] < 0  # fell
    assert pf["delta_rev"] < 0  # revenue decline
    assert pytest.approx(pf["depr_imp"] / 1e6, 0.1) == 251338.0
    assert pytest.approx(pf["prov_t0"] / 1e6, 0.1) == 39476.0
    assert pytest.approx(pf["pct_prov"], 0.1) == pytest.approx(40.7, 0.2)
    assert pf["cash_backing_ratio"] > 1.0  # OCF > PAT


# ---------------------------------------------------------------------------
# Test 4: Deterministic Summary Formulation
# ---------------------------------------------------------------------------

def test_deterministic_health_summary_narrative():
    facts = [
        # t0
        _fact("Assets", 4500000.0 * 1e6, "2024-03-31"),
        _fact("ProducingProperties", 1437941.0 * 1e6, "2024-03-31"),
        _fact("FacilitiesInProgress", 255613.0 * 1e6, "2024-03-31"),
        _fact("ExploratoryWellsInProgress", 153929.0 * 1e6, "2024-03-31"),
        _fact("DevelopmentWellsInProgress", 61277.0 * 1e6, "2024-03-31"),
        _fact("NoncurrentInvestments", 1133786.0 * 1e6, "2024-03-31"),
        _fact("CurrentAssets", 618024.0 * 1e6, "2024-03-31"),
        _fact("CurrentLiabilities", 371643.0 * 1e6, "2024-03-31"),
        _fact("Equity", 3000000.0 * 1e6, "2024-03-31"),
        _fact("BorrowingsNoncurrent", 10000.0 * 1e6, "2024-03-31"),  # <5% borrowings
        # t0 Performance
        _fact("RevenueFromOperations", 1384020.0 * 1e6, "2024-03-31"),
        _fact("ProfitLossForPeriod", 328940.0 * 1e6, "2024-03-31"),
        _fact("DepreciationDepletionAndAmortisationExpense", 251338.0 * 1e6, "2024-03-31"),
        _fact("ProvisionsCurrent", 20000.0 * 1e6, "2024-03-31"),
        _fact("ProvisionsNoncurrent", 19476.0 * 1e6, "2024-03-31"),
        _fact("DeferredTaxExpense", -23004.0 * 1e6, "2024-03-31"),
        _fact("CashFlowsFromUsedInOperatingActivities", 600000.0 * 1e6, "2024-03-31"),
        # t_prev
        _fact("RevenueFromOperations", 1555170.0 * 1e6, "2023-03-31"),
        _fact("ProfitLossForPeriod", 356103.0 * 1e6, "2023-03-31"),
        _fact("ProvisionsCurrent", 15000.0 * 1e6, "2023-03-31"),
        _fact("ProvisionsNoncurrent", 13054.0 * 1e6, "2023-03-31"),
    ]
    disclosures = [
        _disclosure("LevelOfRoundingUsedInFinancialStatements", "Millions"),
        _disclosure("NameOfMainProductOrService", "Crude Oil"),
        _disclosure("NameOfMainProductOrService", "Natural Gas"),
    ]
    meta = {
        "entity_cin": "L74899DL1993GOI054155",
        "company_name": "Oil and Natural Gas Corporation Limited",
    }

    metrics = compute_health_metrics(facts, [], disclosures, meta)
    summary = deterministic_health_summary(
        "Oil and Natural Gas Corporation Limited",
        "L74899DL1993GOI054155",
        "FY2023-24",
        metrics,
    )

    st_text = summary["structure"]
    assert "dominated by oil & gas assets (₹1,437,941 M)" in st_text
    assert "facilities in progress ₹255,613 M" in st_text
    assert "exploratory wells in progress ₹153,929 M" in st_text
    assert "development wells ₹61,277 M" in st_text
    assert "non-current investment book (≈25% of assets)" in st_text
    assert "current assets ₹618,024 M vs current liabilities ₹371,643 M" in st_text
    assert "surplus of ₹246,381 M" in st_text
    assert "Funding is overwhelmingly equity and internal accruals; borrowings are immaterial" in st_text

    pf_text = summary["performance"]
    assert "Profit fell ₹356,103 M → ₹328,940 M." in pf_text
    assert "revenue decline" in pf_text
    assert "depletion/impairment (₹251,338 M)" in pf_text
    assert "provisions/write-offs (₹39,476 M, up +40.7%)" in pf_text
    assert "deferred-tax credit (₹23,004 M)" in pf_text
    assert "Operating cash flow (₹600,000 M) strongly exceeds profit — earnings are cash-backed" in pf_text


def test_cwip_dominant_infrastructure_spv():
    # Tests SPV under construction (e.g. AP Gas Infrastructure)
    # Assets: 216.11 cr, CWIP: 195.32 cr (90%), PPE: 8,758 Rs (<0.01 cr), Investments: 20.44 cr
    facts = [
        _fact("Assets", 2161138251.0, "2020-03-31"),
        _fact("CapitalWorkInProgress", 1953239165.0, "2020-03-31"),
        _fact("PropertyPlantAndEquipment", 8758.0, "2020-03-31"),
        _fact("NoncurrentInvestments", 204360000.0, "2020-03-31"),
        _fact("CurrentAssets", 3478027.0, "2020-03-31"),
        _fact("CurrentLiabilities", 2913047950.0, "2020-03-31"),
        _fact("Equity", -751909699.0, "2020-03-31"),
        _fact("RevenueFromOperations", 0.0, "2020-03-31"),
        _fact("ProfitLossForPeriod", -147325152.0, "2020-03-31"),
        _fact("DepreciationDepletionAndAmortisationExpense", 47827.0, "2020-03-31"),  # 47,827 Rs (<0.01 cr)
        _fact("ProvisionsCurrent", 11000000.0, "2020-03-31"),
        _fact("CashFlowsFromUsedInOperatingActivities", 248788686.0, "2020-03-31"),
        # t_prev
        _fact("ProfitLossForPeriod", 1700000.0, "2019-03-31"),
        _fact("ProvisionsCurrent", 10800000.0, "2019-03-31"),
    ]
    disclosures = [_disclosure("LevelOfRoundingUsedInFinancialStatements", "Actual")]
    meta = {
        "entity_cin": "U11100AP2009SGC107233",
        "company_name": "ANDHRA PRADESH GAS INFRASTRUCTURE CORPORATION PRIVATE LIMITED",
    }

    metrics = compute_health_metrics(facts, [], disclosures, meta)
    assert metrics["structure"]["dominant_type"] == "cwip"

    summary = deterministic_health_summary(
        "ANDHRA PRADESH GAS INFRASTRUCTURE CORPORATION PRIVATE LIMITED",
        "U11100AP2009SGC107233",
        "FY2019-20",
        metrics,
    )

    st_text = summary["structure"]
    # Verify CWIP is stated as dominating the asset base, NOT PPE (₹0.00 cr)!
    assert "Asset base is dominated by capital work-in-progress (₹195.32 cr, ≈90% of assets)" in st_text
    assert "completed property, plant and equipment nominal (<₹0.01 cr)" in st_text
    assert "₹0.00 cr" not in st_text

    pf_text = summary["performance"]
    # Depletion of 47k Rs (<0.01 cr) should NOT be cited as a causal reason!
    assert "depletion/impairment" not in pf_text
    assert "provisions/write-offs (₹1.10 cr" in pf_text



# ---------------------------------------------------------------------------
# Test 5: Safe Language Linting (Spec §17)
# ---------------------------------------------------------------------------

def test_lint_health_output():
    dirty_data = {
        "business_type": "This proves fraudulent activity in operations.",
        "structure": "The structure guarantees solvency.",
        "performance": "Profit certified by entity will fail soon.",
    }
    cleaned = lint_health_output(dirty_data)
    assert "proves" not in cleaned["business_type"].lower()
    assert "indicates divergent activity" in cleaned["business_type"]
    assert "guarantees" not in cleaned["structure"].lower()
    assert "suggests" in cleaned["structure"]
    assert "certified" not in cleaned["performance"].lower()
    assert "recorded" in cleaned["performance"]
    assert "will fail" not in cleaned["performance"]
    assert "exhibits liquidity pressure" in cleaned["performance"]


# ---------------------------------------------------------------------------
# Test 6: Master Entrypoint & LLM Synthesis / Fallback
# ---------------------------------------------------------------------------

def test_build_health_summary_deterministic_bypass():
    facts = [
        _fact("Assets", 1000.0 * 1e7, "2024-03-31"),
        _fact("PropertyPlantAndEquipment", 600.0 * 1e7, "2024-03-31"),
        _fact("RevenueFromOperations", 500.0 * 1e7, "2024-03-31"),
        _fact("ProfitLossForPeriod", 50.0 * 1e7, "2024-03-31"),
        _fact("RevenueFromOperations", 400.0 * 1e7, "2023-03-31"),
        _fact("ProfitLossForPeriod", 40.0 * 1e7, "2023-03-31"),
    ]
    disclosures = [_disclosure("LevelOfRoundingUsedInFinancialStatements", "Crores")]
    meta = {"entity_cin": "U40102UP2010GOI040007", "company_name": "NBPPL"}

    res = build_health_summary(
        doc_id="test_nbppl",
        company_name="NBPPL",
        cin="U40102UP2010GOI040007",
        fy_label="FY2023-24",
        facts=facts,
        turnover_facts=[],
        disclosures=disclosures,
        meta=meta,
        use_llm=False,
    )
    assert res["doc_id"] == "test_nbppl"
    assert res["formed"] is True
    assert "Deterministic health summary engine used" in res["reason"]
    assert "₹600.00 cr" in res["structure"]
    assert "Profit rose ₹40.00 cr → ₹50.00 cr." in res["performance"]


def test_build_health_summary_llm_success_and_linting():
    facts = [_fact("Assets", 1000.0 * 1e7, "2024-03-31")]
    disclosures = [_disclosure("LevelOfRoundingUsedInFinancialStatements", "Crores")]
    meta = {"entity_cin": "U40102UP2010GOI040007", "company_name": "NBPPL"}

    mock_llm_json = json.dumps({
        "business_type": "Specialized heavy equipment engineering.",
        "structure": "Asset base is dominated by specialized tooling. Debt is immaterial.",
        "performance": "Operating revenue proved resilient with strong cash backing.",
    })

    def mock_chat(messages):
        return f"```json\n{mock_llm_json}\n```"

    res = build_health_summary(
        doc_id="test_llm",
        company_name="NBPPL",
        cin="U40102UP2010GOI040007",
        fy_label="FY2023-24",
        facts=facts,
        turnover_facts=[],
        disclosures=disclosures,
        meta=meta,
        use_llm=True,
        chat_fn=mock_chat,
    )
    assert res["business_type"] == "Specialized heavy equipment engineering."
    assert "Asset base is dominated by specialized tooling" in res["structure"]
    # Verify linting sanitized "proved" -> "indicated"
    assert "proved" not in res["performance"]
    assert "indicated resilient" in res["performance"]
    assert res["reason"] == "Synthesized via LLM audit diagnostic assistant."


def test_build_health_summary_llm_error_fallback():
    facts = [_fact("Assets", 1000.0 * 1e7, "2024-03-31")]
    disclosures = [_disclosure("LevelOfRoundingUsedInFinancialStatements", "Crores")]
    meta = {"entity_cin": "U40102UP2010GOI040007", "company_name": "NBPPL"}

    def exploding_chat(messages):
        raise RuntimeError("LLM gateway timeout")

    res = build_health_summary(
        doc_id="test_fail",
        company_name="NBPPL",
        cin="U40102UP2010GOI040007",
        fy_label="FY2023-24",
        facts=facts,
        turnover_facts=[],
        disclosures=disclosures,
        meta=meta,
        use_llm=True,
        chat_fn=exploding_chat,
    )
    # Resilient fallback: formed is True, deterministic narrative populated, reason notes fallback
    assert res["formed"] is True
    assert "Deterministic engine used (LLM synthesis fallback: LLM gateway timeout)" in res["reason"]
    assert len(res["structure"]) > 0
