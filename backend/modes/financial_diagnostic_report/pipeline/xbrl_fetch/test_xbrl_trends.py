"""
test_xbrl_trends.py — Hermetic Unit Tests for Block 5: Key Trends & Structural Drift.

100% offline, zero database/network dependencies.
Validates:
1. Multi-period panel assembly and chronological sorting
2. Common-size balance sheet & P&L drift arithmetic (percentage points)
3. Extended DuPont decomposition & leverage dominance attribution (ROE = Margin × Turnover × Leverage)
4. The 4 Canonical Audit Trend Archetypes:
   - Receivables Divergence (S05)
   - Liquidity Mix Shift (S17)
   - Provisions Volatility (S16)
   - Coverage Direction Drift (S15)
5. Structural Drift Signals (S09 CWIP, S10 Non-Current Other, S02 Payables, S06 Accruals)
6. Single-period graceful degradation (initial reporting period, e.g. ONGC Green)
7. Safe language linting (Spec §17)
8. LLM synthesis and resilient deterministic fallback
9. Threshold isolation via xbrl_trend_thresholds.py
"""
from __future__ import annotations
import json
import pytest

from .xbrl_trend_thresholds import TrendThresholds, THRESHOLDS
from .xbrl_trends import (
    assemble_time_series,
    compute_common_size_and_drift,
    compute_dupont_decomposition,
    evaluate_trend_signals,
    deterministic_trends,
    build_key_trends,
    lint_trend_output,
    _safe_div,
)
from .xbrl_trend_concepts import SINGLE_PERIOD_DISCLOSURE


def _fact(concept: str, val: float, fy_end: str, fy_start: str = "2020-04-01") -> dict:
    return {
        "concept_name": concept,
        "fy_start": fy_start,
        "fy_end": fy_end,
        "value_numeric": val,
        "unit": "INR",
    }


# ---------------------------------------------------------------------------
# Test 1: Time Series Assembly
# ---------------------------------------------------------------------------

def test_assemble_time_series_sorting_and_panel():
    facts = [
        # Period 2022
        _fact("Assets", 1000.0, "2022-03-31"),
        _fact("RevenueFromOperations", 800.0, "2022-03-31"),
        # Period 2020 (earlier period passed out of order)
        _fact("Assets", 800.0, "2020-03-31"),
        _fact("RevenueFromOperations", 600.0, "2020-03-31"),
        # Period 2021
        _fact("Assets", 900.0, "2021-03-31"),
        _fact("RevenueFromOperations", 700.0, "2021-03-31"),
    ]

    series = assemble_time_series(facts)
    assert series["series_years"] == 3
    assert series["period_labels"] == ["2020-03-31", "2021-03-31", "2022-03-31"]
    panel = series["panel"]
    assert len(panel) == 3
    assert panel[0]["assets"] == 800.0
    assert panel[1]["assets"] == 900.0
    assert panel[2]["assets"] == 1000.0


# ---------------------------------------------------------------------------
# Test 2: Common-Size & Drift Arithmetic
# ---------------------------------------------------------------------------

def test_common_size_and_drift():
    facts = [
        # t-1: Assets 1000 cr, CWIP 50 cr (5%), Other Non-Current 20 cr (2%)
        _fact("Assets", 1000.0 * 1e7, "2020-03-31"),
        _fact("CapitalWorkInProgress", 50.0 * 1e7, "2020-03-31"),
        _fact("OtherNoncurrentAssets", 20.0 * 1e7, "2020-03-31"),
        _fact("Equity", 600.0 * 1e7, "2020-03-31"),
        _fact("BorrowingsNoncurrent", 300.0 * 1e7, "2020-03-31"),
        # t0: Assets 1200 cr, CWIP 180 cr (15% -> +10 pp drift), Other Non-Current 72 cr (6% -> +4 pp drift)
        _fact("Assets", 1200.0 * 1e7, "2021-03-31"),
        _fact("CapitalWorkInProgress", 180.0 * 1e7, "2021-03-31"),
        _fact("OtherNoncurrentAssets", 72.0 * 1e7, "2021-03-31"),
        _fact("Equity", 600.0 * 1e7, "2021-03-31"),
        _fact("BorrowingsNoncurrent", 500.0 * 1e7, "2021-03-31"),
    ]

    series = assemble_time_series(facts)
    cs = compute_common_size_and_drift(series)

    # CWIP drifted from 5.0% to 15.0% = +10.0 pp
    cwip_drift = cs["drifts"]["asset_cwip_share"]
    assert pytest.approx(cwip_drift, 0.01) == 10.0

    # Other non-current drifted from 2.0% to 6.0% = +4.0 pp (exceeds 3 pp threshold)
    other_nc_drift = cs["drifts"]["asset_other_noncur_share"]
    assert pytest.approx(other_nc_drift, 0.01) == 4.0

    # Highlights should include CWIP and Other Non-Current Assets
    items = [h["item"] for h in cs["highlights"]]
    assert "Capital Work-in-Progress (CWIP)" in items
    assert "Other Non-Current Assets" in items


# ---------------------------------------------------------------------------
# Test 3: Extended DuPont Profitability & Leverage Decomposition
# ---------------------------------------------------------------------------

def test_dupont_decomposition_and_leverage_dominance():
    facts = [
        # t-1: Revenue 1000, Assets 1000, Equity 500, PAT 100
        # Margin = 10%, Turnover = 1.0x, Multiplier = 2.0x => ROE = 20%
        _fact("RevenueFromOperations", 1000.0 * 1e7, "2020-03-31"),
        _fact("Assets", 1000.0 * 1e7, "2020-03-31"),
        _fact("Equity", 500.0 * 1e7, "2020-03-31"),
        _fact("ProfitLossForPeriod", 100.0 * 1e7, "2020-03-31"),
        # t0: Revenue 1000, Assets 1200, Equity 400 (debt added), PAT 100
        # Margin = 10%, Turnover = 0.833x, Multiplier = 3.0x => ROE = 25% (ROE expanded due to leverage!)
        _fact("RevenueFromOperations", 1000.0 * 1e7, "2021-03-31"),
        _fact("Assets", 1200.0 * 1e7, "2021-03-31"),
        _fact("Equity", 400.0 * 1e7, "2021-03-31"),
        _fact("ProfitLossForPeriod", 100.0 * 1e7, "2021-03-31"),
    ]

    series = assemble_time_series(facts)
    dup = compute_dupont_decomposition(series)

    p0 = dup["periods"][0]
    p1 = dup["periods"][1]

    assert pytest.approx(p0["roe"], 0.01) == 0.20
    assert pytest.approx(p1["roe"], 0.01) == 0.25
    assert dup["leverage_dominated"] is True

    # Signal S13 should fire
    cs = compute_common_size_and_drift(series)
    signals = evaluate_trend_signals(series, cs, dup)
    sig_ids = [s["signal_id"] for s in signals]
    assert "S13" in sig_ids


# ---------------------------------------------------------------------------
# Test 4: The 4 Canonical Audit Trend Archetypes
# ---------------------------------------------------------------------------

def test_archetype_1_receivables_divergence_s05():
    """Archetype 1: Receivables up while revenue fell; turnover drops."""
    facts = [
        # t-1: Rev 1000 cr, Rec 100 cr, Turnover 10.0
        _fact("RevenueFromOperations", 1000.0 * 1e7, "2020-03-31"),
        _fact("TradeReceivablesCurrent", 100.0 * 1e7, "2020-03-31"),
        _fact("TradeReceivablesTurnoverRatio", 10.0, "2020-03-31"),
        _fact("Assets", 2000.0 * 1e7, "2020-03-31"),
        # t0: Rev 800 cr (-20%), Rec 130 cr (+30%), Turnover 7.5
        _fact("RevenueFromOperations", 800.0 * 1e7, "2021-03-31"),
        _fact("TradeReceivablesCurrent", 130.0 * 1e7, "2021-03-31"),
        _fact("TradeReceivablesTurnoverRatio", 7.5, "2021-03-31"),
        _fact("Assets", 2000.0 * 1e7, "2021-03-31"),
    ]

    series = assemble_time_series(facts)
    cs = compute_common_size_and_drift(series)
    dup = compute_dupont_decomposition(series)
    signals = evaluate_trend_signals(series, cs, dup)

    s05 = next((s for s in signals if s["signal_id"] == "S05"), None)
    assert s05 is not None
    assert s05["severity"] == "high"
    assert "Divergence is a lead — timing, customer mix or collection pace." in s05["audit_lead"]
    assert "Receivables increased +30.0%" in s05["observation"]


def test_archetype_2_liquidity_mix_shift_s17():
    """Archetype 2: Other bank balances jumped while other current financial assets fell."""
    facts = [
        # t-1: Other bank balances 150 cr, Other current financial assets 100 cr
        _fact("BankBalanceOtherThanCashAndCashEquivalents", 150.0 * 1e7, "2020-03-31"),
        _fact("OtherCurrentFinancialAssets", 100.0 * 1e7, "2020-03-31"),
        _fact("Assets", 1000.0 * 1e7, "2020-03-31"),
        # t0: Other bank balances 280 cr (+86%), Other current financial assets 30 cr (-70%)
        _fact("BankBalanceOtherThanCashAndCashEquivalents", 280.0 * 1e7, "2021-03-31"),
        _fact("OtherCurrentFinancialAssets", 30.0 * 1e7, "2021-03-31"),
        _fact("Assets", 1000.0 * 1e7, "2021-03-31"),
    ]

    series = assemble_time_series(facts)
    cs = compute_common_size_and_drift(series)
    dup = compute_dupont_decomposition(series)
    signals = evaluate_trend_signals(series, cs, dup)

    s17 = next((s for s in signals if s["signal_id"] == "S17"), None)
    assert s17 is not None
    assert "A classification / placement shift to explain." in s17["audit_lead"]


def test_archetype_3_provisions_volatility_s16():
    """Archetype 3: Provision, impairment & write-offs up > 25% YoY."""
    facts = [
        # t-1: Provisions 100 cr
        _fact("ProvisionsCurrent", 60.0 * 1e7, "2020-03-31"),
        _fact("ProvisionsNoncurrent", 40.0 * 1e7, "2020-03-31"),
        _fact("Assets", 1000.0 * 1e7, "2020-03-31"),
        # t0: Provisions 140 cr (+40%)
        _fact("ProvisionsCurrent", 80.0 * 1e7, "2021-03-31"),
        _fact("ProvisionsNoncurrent", 60.0 * 1e7, "2021-03-31"),
        _fact("Assets", 1000.0 * 1e7, "2021-03-31"),
    ]

    series = assemble_time_series(facts)
    cs = compute_common_size_and_drift(series)
    dup = compute_dupont_decomposition(series)
    signals = evaluate_trend_signals(series, cs, dup)

    s16 = next((s for s in signals if s["signal_id"] == "S16"), None)
    assert s16 is not None
    assert "An estimate-behaviour signal to corroborate, never a conclusion on intent." in s16["audit_lead"]


def test_archetype_4_coverage_direction_drift_s15():
    """Archetype 4: Debt-service coverage fell sharply on lower EBIT."""
    facts = [
        # t-1: DSCR 222.33
        _fact("DebtServiceCoverageRatio", 222.33, "2020-03-31"),
        _fact("Assets", 1000.0 * 1e7, "2020-03-31"),
        # t0: DSCR 66.89 (-69.9%)
        _fact("DebtServiceCoverageRatio", 66.89, "2021-03-31"),
        _fact("Assets", 1000.0 * 1e7, "2021-03-31"),
    ]

    series = assemble_time_series(facts)
    cs = compute_common_size_and_drift(series)
    dup = compute_dupont_decomposition(series)
    signals = evaluate_trend_signals(series, cs, dup)

    s15 = next((s for s in signals if s["signal_id"] == "S15"), None)
    assert s15 is not None
    assert "The direction, not the level, is the lead" in s15["audit_lead"]


# ---------------------------------------------------------------------------
# Test 5: Single-Period Entities Graceful Degradation (e.g. ONGC Green)
# ---------------------------------------------------------------------------

def test_single_period_initial_year_degradation():
    facts = [
        _fact("Assets", 500.0 * 1e7, "2024-03-31"),
        _fact("RevenueFromOperations", 0.0, "2024-03-31"),
        _fact("TradeReceivablesCurrent", 0.0, "2024-03-31"),
    ]

    payload = build_key_trends(
        doc_id="ONGC_GREEN_2024_2025",
        company_name="ONGC Green Limited",
        cin="U35105DL2024GOI427427",
        fy_label="FY2024-25",
        metric_rows=facts,
        use_llm=False,
    )

    assert payload["series_years"] == 1
    assert payload["formed"] is True
    assert payload["structural_drift_cards"] == []
    assert SINGLE_PERIOD_DISCLOSURE in payload["summary_lede"]
    assert "initial reporting period" in payload["reason"].lower()


# ---------------------------------------------------------------------------
# Test 6: Safe Language Linting (Spec §17)
# ---------------------------------------------------------------------------

def test_safe_language_linting():
    dirty = {
        "summary_lede": "This proves the management is fraudulent and the company will fail.",
        "dupont_narrative": "ROE certified and guaranteed by the model.",
        "structural_drift_cards": [
            {
                "signal_id": "S05",
                "title": "Proves fraud",
                "observation": "Proved the books are falsified.",
                "audit_lead": "Certifies insolvency.",
                "evidence_lead": "Guarantees default.",
            }
        ],
    }

    cleaned = lint_trend_output(dirty)
    assert "proves" not in cleaned["summary_lede"].lower()
    assert "fraudulent" not in cleaned["summary_lede"].lower()
    assert "will fail" not in cleaned["summary_lede"].lower()
    assert "certified" not in cleaned["dupont_narrative"].lower()
    assert "guaranteed" not in cleaned["dupont_narrative"].lower()
    card = cleaned["structural_drift_cards"][0]
    assert "proves" not in card["title"].lower()
    assert "falsified" not in card["observation"].lower()


# ---------------------------------------------------------------------------
# Test 7: Master Entrypoint & LLM Fallback Recovery
# ---------------------------------------------------------------------------

def test_build_key_trends_llm_fallback_on_exception():
    facts = [
        _fact("Assets", 1000.0 * 1e7, "2020-03-31"),
        _fact("RevenueFromOperations", 500.0 * 1e7, "2020-03-31"),
        _fact("TradeReceivablesCurrent", 50.0 * 1e7, "2020-03-31"),
        _fact("Assets", 1200.0 * 1e7, "2021-03-31"),
        _fact("RevenueFromOperations", 400.0 * 1e7, "2021-03-31"),
        _fact("TradeReceivablesCurrent", 90.0 * 1e7, "2021-03-31"),
    ]

    def failing_chat(msgs, **kwargs):
        raise RuntimeError("LLM API connection timed out.")

    payload = build_key_trends(
        doc_id="DOC123",
        company_name="Test Power Corp",
        cin="U12345DL2000SGC123456",
        fy_label="FY2020-21",
        metric_rows=facts,
        use_llm=True,
        chat_fn=failing_chat,
    )

    assert payload["formed"] is True
    assert "LLM synthesis unavailable" in payload["reason"]
    assert len(payload["structural_drift_cards"]) > 0


def test_build_key_trends_llm_success_path():
    facts = [
        _fact("Assets", 1000.0 * 1e7, "2020-03-31"),
        _fact("RevenueFromOperations", 500.0 * 1e7, "2020-03-31"),
        _fact("Assets", 1200.0 * 1e7, "2021-03-31"),
        _fact("RevenueFromOperations", 400.0 * 1e7, "2021-03-31"),
    ]

    mock_json_response = json.dumps({
        "structural_drift_cards": [
            {
                "signal_id": "S05",
                "title": "Receivables Divergence",
                "drift_type": "operational_divergence",
                "severity": "high",
                "observation": "Receivables expanded while revenue contracted.",
                "audit_lead": "Timing or collection pace lead.",
                "evidence_lead": "Debtor ledger.",
            }
        ],
        "summary_lede": "Multi-period trend analysis identifies key operational divergence.",
        "dupont_narrative": "Profitability decomposition indicates margin pressure.",
    })

    def working_chat(msgs, **kwargs):
        return f"```json\n{mock_json_response}\n```"

    payload = build_key_trends(
        doc_id="DOC123",
        company_name="Test Power Corp",
        cin="U12345DL2000SGC123456",
        fy_label="FY2020-21",
        metric_rows=facts,
        use_llm=True,
        chat_fn=working_chat,
    )

    assert payload["formed"] is True
    assert len(payload["structural_drift_cards"]) == 1
    assert payload["structural_drift_cards"][0]["signal_id"] == "S05"
    assert "operational divergence" in payload["summary_lede"]


# ---------------------------------------------------------------------------
# Test 8: Threshold Isolation Verification
# ---------------------------------------------------------------------------

def test_threshold_isolation():
    # Verify default threshold values
    assert THRESHOLDS.NON_CURRENT_OTHER_DRIFT_THRESHOLD == 0.03
    assert THRESHOLDS.CWIP_DRIFT_THRESHOLD == 0.05
    assert THRESHOLDS.PROVISION_VOLATILITY_THRESHOLD == 0.25
    assert THRESHOLDS.COVERAGE_DRIFT_THRESHOLD == -0.20

    # Custom threshold instantiation overrides without modifying defaults
    custom = TrendThresholds(CWIP_DRIFT_THRESHOLD=0.20)
    assert custom.CWIP_DRIFT_THRESHOLD == 0.20
    assert THRESHOLDS.CWIP_DRIFT_THRESHOLD == 0.05


def test_trend_format_inr_adaptive_scaling():
    from .xbrl_trends import _format_inr
    assert _format_inr(None) == "N/A"
    assert _format_inr(0) == "₹0.00"
    assert _format_inr(50_000) == "₹0.50 lakh"
    assert _format_inr(4_500_000) == "₹45.00 lakh"
    assert _format_inr(10_000_000) == "₹1.00 cr"
    assert _format_inr(512_681_000) == "₹51.27 cr"
    assert _format_inr(-150_000) == "-₹1.50 lakh"
    assert _format_inr(-25_000_000) == "-₹2.50 cr"


