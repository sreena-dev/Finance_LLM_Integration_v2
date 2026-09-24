"""
xbrl_trends.py — Block 5: Key Trends & Structural Drift Engine.

Fulfills FDR Audit Planning Intelligence Specification v3.0:
- Layer 2: Financial Structure & Structural Drift (§7)
- Layer 3: Performance Decomposition & Causality Attribution (§8)
- Layer 4: Financial Quality Trends (§9)
- Statistical Series Constraints (§9.5)
- Output Architecture Row 5 (§14.1)

Picks up all diagnostic cutoffs and pacing tolerances from `xbrl_trend_thresholds.py`.
Separates DB credentials strictly via `xbrl_config.py`.
Dual execution: LLM synthesis with authoritative deterministic fallback.
"""
from __future__ import annotations
from datetime import date
import json
import logging
import re
from typing import Any, Callable

from .xbrl_trend_thresholds import THRESHOLDS, TrendThresholds
from .xbrl_trend_concepts import (
    SIGNAL_IDS,
    SIGNAL_TITLES,
    CLEAN_SIGNAL_REASONS,
    SINGLE_PERIOD_DISCLOSURE,
)
from . import xbrl_trend_prompt as P

logger = logging.getLogger(__name__)

_CRORE = 1e7
_LAKH = 1e5

_PROHIBITED_REPLACEMENTS: dict[str, str] = {
    "proves": "indicates",
    "proved": "indicated",
    "certifies": "records",
    "certified": "recorded",
    "guarantees": "suggests",
    "guaranteed": "suggested",
    "is fraudulent": "warrants audit corroboration",
    "fraud": "reporting divergence",
    "will fail": "exhibits liquidity pressure",
    "insolvent": "under capital constraint",
    "falsified": "divergent",
}


def _safe_div(num: float | None, denom: float | None, default: float = 0.0) -> float:
    if num is None or denom is None or abs(denom) < 1e-6:
        return default
    return num / denom


def _format_inr(val: float | None) -> str:
    if val is None:
        return "N/A"
    if val == 0:
        return "₹0.00"
    abs_val = abs(val)
    sign = "-" if val < 0 else ""
    if abs_val >= _CRORE:
        return f"{sign}₹{abs_val / _CRORE:,.2f} cr"
    return f"{sign}₹{abs_val / _LAKH:,.2f} lakh"


def _format_pp(val: float) -> str:
    return f"{val:+.2f} pp"


def _format_pct(val: float) -> str:
    return f"{val * 100:+.2f}%"


def _clean_str(text: str) -> str:
    s = text
    for phrase, rep in _PROHIBITED_REPLACEMENTS.items():
        s = re.sub(re.escape(phrase), rep, s, flags=re.IGNORECASE)
    return s


def lint_trend_output(data: dict[str, Any]) -> dict[str, Any]:
    """Enforces Spec §17 safe-language rules across all narrative fields."""
    for k in ("summary_lede", "dupont_narrative", "reason"):
        if k in data and isinstance(data[k], str):
            data[k] = _clean_str(data[k])

    for card in data.get("structural_drift_cards", []):
        for k in ("title", "observation", "audit_lead", "evidence_lead"):
            if k in card and isinstance(card[k], str):
                card[k] = _clean_str(card[k])

    for item in data.get("common_size_highlights", []):
        if "narrative" in item and isinstance(item["narrative"], str):
            item["narrative"] = _clean_str(item["narrative"])

    return data


# ---------------------------------------------------------------------------
# Time Series Assembly (§9.5)
# ---------------------------------------------------------------------------

def assemble_time_series(metric_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Groups raw facts by reporting period (`fy_end`), chronological sort (t-2, t-1, t0).
    Builds a canonical panel mapping for financial structure, DuPont, and quality trends.
    """
    periods_map: dict[str, dict[str, float]] = {}
    periods_order: list[str] = []

    for r in metric_rows:
        end_d = r.get("fy_end")
        if not end_d:
            continue
        period_key = str(end_d)
        if period_key not in periods_map:
            periods_map[period_key] = {}
            periods_order.append(period_key)

        cname = r.get("concept_name")
        val = r.get("value_numeric")
        if cname and val is not None:
            periods_map[period_key][cname] = float(val)

    periods_order.sort()
    series_years = len(periods_order)

    # Build structured panel
    panel: list[dict[str, Any]] = []
    for p_key in periods_order:
        raw = periods_map[p_key]

        assets = raw.get("Assets")
        cur_assets = raw.get("CurrentAssets")
        noncur_assets = raw.get("NoncurrentAssets")
        if noncur_assets is None and assets is not None and cur_assets is not None:
            noncur_assets = max(0.0, assets - cur_assets)

        ppe = raw.get("PropertyPlantAndEquipment", 0.0)
        cwip = raw.get("CapitalWorkInProgress", 0.0)
        investments = (
            raw.get("Investments")
            or (raw.get("NoncurrentInvestments", 0.0) + raw.get("CurrentInvestments", 0.0))
        )
        receivables = raw.get("TradeReceivablesCurrent", 0.0) + raw.get("TradeReceivablesNoncurrent", 0.0)
        cash_eq = raw.get("CashAndCashEquivalents", 0.0)
        other_bank = raw.get("BankBalanceOtherThanCashAndCashEquivalents", 0.0)
        other_cur_fin = raw.get("OtherCurrentFinancialAssets", 0.0)
        other_noncur = raw.get("OtherNoncurrentAssets", 0.0)
        other_cur = raw.get("OtherCurrentAssets", 0.0)

        equity = raw.get("Equity")
        if equity is None:
            equity = raw.get("EquityShareCapital", 0.0) + raw.get("OtherEquity", 0.0)

        borrowings_nc = raw.get("BorrowingsNoncurrent", 0.0)
        borrowings_cur = raw.get("BorrowingsCurrent", 0.0)
        total_borrowings = borrowings_nc + borrowings_cur

        trade_payables = raw.get("TradePayablesCurrent", 0.0) + raw.get("TradePayablesNoncurrent", 0.0)
        provisions = raw.get("ProvisionsCurrent", 0.0) + raw.get("ProvisionsNoncurrent", 0.0)

        rev = raw.get("RevenueFromOperations", 0.0)
        pat = raw.get("ProfitLossForPeriod", 0.0)
        pbt = raw.get("ProfitBeforeTax", 0.0)
        fin_cost = raw.get("FinanceCosts", 0.0)
        depr = raw.get("DepreciationDepletionAndAmortisationExpense", 0.0)
        materials = raw.get("CostOfMaterialsConsumed", 0.0)
        emp_exp = raw.get("EmployeeBenefitsExpense", 0.0)
        other_exp = raw.get("OtherExpenses", 0.0)
        ocf = raw.get("CashFlowsFromUsedInOperatingActivities", 0.0)

        rec_turnover = raw.get("TradeReceivablesTurnoverRatio")
        dscr = raw.get("DebtServiceCoverageRatio")
        icr = raw.get("InterestCoverageRatio")

        panel.append({
            "period_date": p_key,
            "assets": assets,
            "cur_assets": cur_assets,
            "noncur_assets": noncur_assets,
            "ppe": ppe,
            "cwip": cwip,
            "investments": investments,
            "receivables": receivables,
            "cash_eq": cash_eq,
            "other_bank": other_bank,
            "cash_and_bank": cash_eq + other_bank,
            "other_cur_fin": other_cur_fin,
            "other_noncur": other_noncur,
            "other_cur": other_cur,
            "equity": equity,
            "borrowings_nc": borrowings_nc,
            "borrowings_cur": borrowings_cur,
            "total_borrowings": total_borrowings,
            "trade_payables": trade_payables,
            "provisions": provisions,
            "revenue": rev,
            "pat": pat,
            "pbt": pbt,
            "ebit": pbt + fin_cost,
            "finance_costs": fin_cost,
            "depreciation": depr,
            "materials": materials,
            "employee_exp": emp_exp,
            "other_exp": other_exp,
            "ocf": ocf,
            "rec_turnover": rec_turnover,
            "dscr": dscr,
            "icr": icr,
        })

    return {
        "series_years": series_years,
        "period_labels": periods_order,
        "panel": panel,
    }


# ---------------------------------------------------------------------------
# Common-Size & Drift Computation (§7)
# ---------------------------------------------------------------------------

def compute_common_size_and_drift(
    series: dict[str, Any],
    thresholds: TrendThresholds = THRESHOLDS,
) -> dict[str, Any]:
    panel: list[dict[str, Any]] = series["panel"]
    series_years: int = series["series_years"]

    if series_years == 0:
        return {"periods": [], "drifts": {}, "highlights": []}

    periods_data = []
    for p in panel:
        assets = p["assets"] or 0.0
        rev = p["revenue"] or 0.0

        asset_mix = {
            "ppe_share": _safe_div(p["ppe"], assets) * 100,
            "cwip_share": _safe_div(p["cwip"], assets) * 100,
            "investments_share": _safe_div(p["investments"], assets) * 100,
            "receivables_share": _safe_div(p["receivables"], assets) * 100,
            "cash_bank_share": _safe_div(p["cash_and_bank"], assets) * 100,
            "other_noncur_share": _safe_div(p["other_noncur"], assets) * 100,
            "other_cur_share": _safe_div(p["other_cur"], assets) * 100,
        }

        funding_mix = {
            "equity_share": _safe_div(p["equity"], assets) * 100,
            "borrowings_share": _safe_div(p["total_borrowings"], assets) * 100,
            "payables_share": _safe_div(p["trade_payables"], assets) * 100,
            "provisions_share": _safe_div(p["provisions"], assets) * 100,
        }

        cost_mix = {
            "materials_share": _safe_div(p["materials"], rev) * 100 if rev > 0 else 0.0,
            "employee_share": _safe_div(p["employee_exp"], rev) * 100 if rev > 0 else 0.0,
            "finance_share": _safe_div(p["finance_costs"], rev) * 100 if rev > 0 else 0.0,
            "depr_share": _safe_div(p["depreciation"], rev) * 100 if rev > 0 else 0.0,
            "pat_margin": _safe_div(p["pat"], rev) * 100 if rev > 0 else 0.0,
        }

        periods_data.append({
            "period_date": p["period_date"],
            "asset_mix": asset_mix,
            "funding_mix": funding_mix,
            "cost_mix": cost_mix,
        })

    drifts: dict[str, float] = {}
    highlights: list[dict[str, Any]] = []

    if series_years >= 2:
        t0 = periods_data[-1]
        t_base = periods_data[0]

        # Asset mix drifts (percentage points)
        for k in ("ppe_share", "cwip_share", "investments_share", "receivables_share", "cash_bank_share", "other_noncur_share"):
            delta_pp = t0["asset_mix"][k] - t_base["asset_mix"][k]
            drifts[f"asset_{k}"] = delta_pp

        # Funding mix drifts
        for k in ("equity_share", "borrowings_share", "payables_share", "provisions_share"):
            delta_pp = t0["funding_mix"][k] - t_base["funding_mix"][k]
            drifts[f"funding_{k}"] = delta_pp

        # Evaluate significant drifts
        # 1. CWIP Drift
        cwip_drift = drifts.get("asset_cwip_share", 0.0)
        if abs(cwip_drift) >= (thresholds.CWIP_DRIFT_THRESHOLD * 100):
            direction = "expanding" if cwip_drift > 0 else "contracting"
            highlights.append({
                "item": "Capital Work-in-Progress (CWIP)",
                "category": "asset_mix",
                "drift_pp": round(cwip_drift, 2),
                "direction": direction,
                "narrative": (
                    f"CWIP share of total assets drifted {cwip_drift:+.2f} pp across the reporting horizon, "
                    f"indicating significant capital expenditure accumulation."
                ),
            })

        # 2. Other Non-Current Assets Drift (Spec S10)
        other_nc_drift = drifts.get("asset_other_noncur_share", 0.0)
        if abs(other_nc_drift) >= (thresholds.NON_CURRENT_OTHER_DRIFT_THRESHOLD * 100):
            direction = "expanding" if other_nc_drift > 0 else "contracting"
            highlights.append({
                "item": "Other Non-Current Assets",
                "category": "asset_mix",
                "drift_pp": round(other_nc_drift, 2),
                "direction": direction,
                "narrative": (
                    f"Other non-current assets shifted {other_nc_drift:+.2f} pp of balance sheet volume, "
                    f"requiring verification of advance or deposit classifications."
                ),
            })

        # 3. Borrowings Share Drift
        borr_drift = drifts.get("funding_borrowings_share", 0.0)
        if abs(borr_drift) >= (thresholds.BORROWINGS_DRIFT_THRESHOLD * 100):
            direction = "leveraging" if borr_drift > 0 else "de-leveraging"
            highlights.append({
                "item": "Total Borrowings (Leverage Structure)",
                "category": "funding_mix",
                "drift_pp": round(borr_drift, 2),
                "direction": direction,
                "narrative": (
                    f"Debt financing contribution to capital structure drifted {borr_drift:+.2f} pp."
                ),
            })

    return {
        "periods": periods_data,
        "drifts": drifts,
        "highlights": highlights,
    }


# ---------------------------------------------------------------------------
# Extended DuPont Decomposition (§8)
# ---------------------------------------------------------------------------

def compute_dupont_decomposition(
    series: dict[str, Any],
    thresholds: TrendThresholds = THRESHOLDS,
) -> dict[str, Any]:
    panel: list[dict[str, Any]] = series["panel"]
    series_years: int = series["series_years"]

    periods_dupont = []
    for p in panel:
        assets = p["assets"] or 0.0
        equity = p["equity"] or 0.0
        rev = p["revenue"] or 0.0
        pat = p["pat"] or 0.0

        net_margin = _safe_div(pat, rev) if rev > 0 else 0.0
        asset_turnover = _safe_div(rev, assets) if assets > 0 else 0.0
        equity_multiplier = _safe_div(assets, equity) if equity > 0 else 0.0
        roe = net_margin * asset_turnover * equity_multiplier

        periods_dupont.append({
            "period_date": p["period_date"],
            "net_margin": net_margin,
            "asset_turnover": asset_turnover,
            "equity_multiplier": equity_multiplier,
            "roe": roe,
            "margin_display": f"{net_margin * 100:.2f}%",
            "turnover_display": f"{asset_turnover:.2f}×",
            "multiplier_display": f"{equity_multiplier:.2f}×",
            "roe_display": f"{roe * 100:.2f}%",
            # Raw bound figures each ratio was divided from — carried through so
            # the UI can show the actual arithmetic, not just its result.
            "raw_pat": pat,
            "raw_revenue": rev,
            "raw_assets": assets,
            "raw_equity": equity,
        })

    attribution: dict[str, Any] = {}
    leverage_dominated = False

    if series_years >= 2:
        t0 = periods_dupont[-1]
        t_prev = periods_dupont[-2]

        delta_roe = t0["roe"] - t_prev["roe"]
        delta_margin = t0["net_margin"] - t_prev["net_margin"]
        delta_turnover = t0["asset_turnover"] - t_prev["asset_turnover"]
        delta_mult = t0["equity_multiplier"] - t_prev["equity_multiplier"]

        # Logarithmic or sequential decomposition
        # ΔROE = (ΔMargin)*Turnover_prev*Multiplier_prev + Margin_t0*(ΔTurnover)*Multiplier_prev + Margin_t0*Turnover_t0*(ΔMultiplier)
        margin_contrib = delta_margin * t_prev["asset_turnover"] * t_prev["equity_multiplier"]
        turnover_contrib = t0["net_margin"] * delta_turnover * t_prev["equity_multiplier"]
        leverage_contrib = t0["net_margin"] * t0["asset_turnover"] * delta_mult

        attribution = {
            "delta_roe": delta_roe,
            "margin_contrib": margin_contrib,
            "turnover_contrib": turnover_contrib,
            "leverage_contrib": leverage_contrib,
        }

        # Check leverage dominance (Spec §8.2, Signal S13)
        if delta_roe > thresholds.MIN_MEANINGFUL_ROE_DELTA:
            if leverage_contrib > (delta_roe * thresholds.DUPONT_LEVERAGE_DOMINANCE) and (delta_margin <= 0 or delta_turnover <= 0):
                leverage_dominated = True

    return {
        "periods": periods_dupont,
        "attribution": attribution,
        "leverage_dominated": leverage_dominated,
    }


# ---------------------------------------------------------------------------
# Trend Signals & Archetypes Detection Engine (§7, §8, §9)
# ---------------------------------------------------------------------------

def evaluate_trend_signals(
    series: dict[str, Any],
    common_size: dict[str, Any],
    dupont: dict[str, Any],
    thresholds: TrendThresholds = THRESHOLDS,
) -> list[dict[str, Any]]:
    panel = series["panel"]
    series_years = series["series_years"]

    if series_years < 2:
        return []

    t0 = panel[-1]
    t_prev = panel[-2]
    signals: list[dict[str, Any]] = []

    # -----------------------------------------------------------------------
    # Archetype 1: Receivables Divergence (Spec §7, §8, Signal S05)
    # -----------------------------------------------------------------------
    rec_t0 = t0["receivables"]
    rec_prev = t_prev["receivables"]
    rev_t0 = t0["revenue"]
    rev_prev = t_prev["revenue"]

    delta_rec_pct = _safe_div(rec_t0 - rec_prev, rec_prev) if rec_prev > 0 else 0.0
    delta_rev_pct = _safe_div(rev_t0 - rev_prev, rev_prev) if rev_prev > 0 else 0.0

    rec_fired = False
    rec_obs = ""
    rec_lead = ""

    if rec_t0 > 0 and (rec_t0 > rec_prev):
        # Case A: Receivables up while revenue fell
        if delta_rev_pct < 0:
            rec_fired = True
            to_t0 = t0.get("rec_turnover")
            to_prev = t_prev.get("rec_turnover")
            to_note = f"; receivable turnover {to_t0:.2f} vs {to_prev:.2f}" if (to_t0 and to_prev) else ""
            rec_obs = (
                f"Receivables increased {delta_rec_pct * 100:+.1f}% ({_format_inr(rec_prev)} → {_format_inr(rec_t0)}) "
                f"while revenue fell {delta_rev_pct * 100:+.1f}% ({_format_inr(rev_prev)} → {_format_inr(rev_t0)}){to_note}."
            )
            rec_lead = "Divergence is a lead — timing, customer mix or collection pace."
        # Case B: Receivables growth outpaced revenue growth by > 10%
        elif delta_rec_pct > (delta_rev_pct + thresholds.RECEIVABLE_PACING_TOLERANCE):
            rec_fired = True
            rec_obs = (
                f"Trade receivables growth ({delta_rec_pct * 100:+.1f}%) substantially outpaced "
                f"revenue expansion ({delta_rev_pct * 100:+.1f}%), expanding working capital lockup."
            )
            rec_lead = "Receivable velocity divergence warrants cut-off and aging verification."

    if rec_fired:
        signals.append({
            "signal_id": "S05",
            "title": SIGNAL_TITLES["S05"],
            "drift_type": "operational_divergence",
            "severity": "high" if delta_rev_pct < 0 else "medium",
            "observation": rec_obs,
            "audit_lead": rec_lead,
            "evidence_lead": "Debtor aging schedule, subsequent realization testing, and credit period terms.",
        })

    # -----------------------------------------------------------------------
    # Archetype 2: Liquidity Mix Shift (Spec §7, Signal S17)
    # -----------------------------------------------------------------------
    other_bank_t0 = t0["other_bank"]
    other_bank_prev = t_prev["other_bank"]
    fin_cur_t0 = t0["other_cur_fin"]
    fin_cur_prev = t_prev["other_cur_fin"]

    delta_bank = other_bank_t0 - other_bank_prev
    delta_fin = fin_cur_t0 - fin_cur_prev

    # Substantial swing between other bank balances (term deposits) and other current financial assets
    if (other_bank_prev > 0 or fin_cur_prev > 0) and abs(delta_bank) > 0 and abs(delta_fin) > 0:
        # Check offsetting directions
        if (delta_bank > 0 and delta_fin < 0) or (delta_bank < 0 and delta_fin > 0):
            pct_bank = _safe_div(delta_bank, other_bank_prev) if other_bank_prev > 0 else 1.0
            pct_fin = _safe_div(delta_fin, fin_cur_prev) if fin_cur_prev > 0 else -1.0
            if abs(pct_bank) >= thresholds.LIQUIDITY_MIX_SHIFT_THRESHOLD or abs(pct_fin) >= thresholds.LIQUIDITY_MIX_SHIFT_THRESHOLD:
                signals.append({
                    "signal_id": "S17",
                    "title": SIGNAL_TITLES["S17"],
                    "drift_type": "balance_sheet_reclassification",
                    "severity": "medium",
                    "observation": (
                        f"Other bank balances shifted {_format_inr(other_bank_prev)} → {_format_inr(other_bank_t0)} "
                        f"({pct_bank * 100:+.1f}%) while other current financial assets shifted "
                        f"{_format_inr(fin_cur_prev)} → {_format_inr(fin_cur_t0)} ({pct_fin * 100:+.1f}%)."
                    ),
                    "audit_lead": "A classification / placement shift to explain.",
                    "evidence_lead": "Bank confirmation certificates, lien markings, and deposit maturity profiles.",
                })

    # -----------------------------------------------------------------------
    # Archetype 3: Provisions & Estimate Behaviour (Spec §9.3, Signal S16)
    # -----------------------------------------------------------------------
    prov_t0 = t0["provisions"]
    prov_prev = t_prev["provisions"]
    if prov_prev > 0 and prov_t0 > 0:
        delta_prov_pct = _safe_div(prov_t0 - prov_prev, prov_prev)
        if abs(delta_prov_pct) >= thresholds.PROVISION_VOLATILITY_THRESHOLD:
            signals.append({
                "signal_id": "S16",
                "title": SIGNAL_TITLES["S16"],
                "drift_type": "estimate_volatility",
                "severity": "medium",
                "observation": (
                    f"Provisions, impairment & write-offs shifted {delta_prov_pct * 100:+.1f}% year-on-year "
                    f"({_format_inr(prov_prev)} → {_format_inr(prov_t0)})."
                ),
                "audit_lead": "An estimate-behaviour signal to corroborate, never a conclusion on intent.",
                "evidence_lead": "Actuarial reports, expected credit loss (ECL) models, and management estimation papers.",
            })

    # -----------------------------------------------------------------------
    # Archetype 4: Coverage & Solvency Direction Drift (Spec §8.1, Signal S15)
    # -----------------------------------------------------------------------
    dscr_t0 = t0.get("dscr")
    dscr_prev = t_prev.get("dscr")
    icr_t0 = t0.get("icr")
    icr_prev = t_prev.get("icr")

    cov_fired = False
    cov_obs = ""
    cov_lead = ""

    if dscr_t0 is not None and dscr_prev is not None and dscr_prev > 0:
        delta_dscr = _safe_div(dscr_t0 - dscr_prev, dscr_prev)
        if delta_dscr <= thresholds.COVERAGE_DRIFT_THRESHOLD:
            cov_fired = True
            cov_obs = (
                f"Debt-service coverage fell {dscr_t0:.2f} vs {dscr_prev:.2f} "
                f"({delta_dscr * 100:.1f}%) on lower EBIT."
            )
            cov_lead = "The direction, not the level, is the lead; absolute coverage remains high."
    elif icr_t0 is not None and icr_prev is not None and icr_prev > 0:
        delta_icr = _safe_div(icr_t0 - icr_prev, icr_prev)
        if delta_icr <= thresholds.COVERAGE_DRIFT_THRESHOLD:
            cov_fired = True
            cov_obs = (
                f"Interest coverage ratio contracted {icr_t0:.2f} vs {icr_prev:.2f} "
                f"({delta_icr * 100:.1f}%) across periods."
            )
            cov_lead = "The direction, not the level, is the lead."

    if cov_fired:
        signals.append({
            "signal_id": "S15",
            "title": SIGNAL_TITLES["S15"],
            "drift_type": "solvency_drift",
            "severity": "medium",
            "observation": cov_obs,
            "audit_lead": cov_lead,
            "evidence_lead": "Borrowing agreements, covenant compliance certificates, and debt repayment schedules.",
        })

    # -----------------------------------------------------------------------
    # Structural Drift Signals (S09 CWIP, S10 Non-Current Other, S02 Payables)
    # -----------------------------------------------------------------------
    # CWIP Drift (S09)
    cwip_drift_pp = common_size.get("drifts", {}).get("asset_cwip_share", 0.0)
    if cwip_drift_pp >= (thresholds.CWIP_DRIFT_THRESHOLD * 100):
        signals.append({
            "signal_id": "S09",
            "title": SIGNAL_TITLES["S09"],
            "drift_type": "capital_accumulation",
            "severity": "high" if cwip_drift_pp >= 10.0 else "medium",
            "observation": (
                f"Capital Work-in-Progress share of assets expanded by {cwip_drift_pp:+.2f} pp "
                f"({_format_inr(t_prev['cwip'])} → {_format_inr(t0['cwip'])})."
            ),
            "audit_lead": "Sustained CWIP expansion flags potential project delays, cost overruns, or idle assets.",
            "evidence_lead": "Project milestone reports, physical inspection certificates, and capitalization schedule.",
        })

    # Non-Current Other Drift (S10)
    other_nc_pp = common_size.get("drifts", {}).get("asset_other_noncur_share", 0.0)
    if abs(other_nc_pp) >= (thresholds.NON_CURRENT_OTHER_DRIFT_THRESHOLD * 100):
        signals.append({
            "signal_id": "S10",
            "title": SIGNAL_TITLES["S10"],
            "drift_type": "structural_shift",
            "severity": "medium",
            "observation": (
                f"Other non-current assets share shifted by {other_nc_pp:+.2f} pp "
                f"({_format_inr(t_prev['other_noncur'])} → {_format_inr(t0['other_noncur'])})."
            ),
            "audit_lead": "Rapid growth in residual asset categories requires verification of underlying advances.",
            "evidence_lead": "Contractual advance ledgers, dispute schedules, and recovery confirmations.",
        })

    # Payables Funding Growth (S02)
    pay_t0 = t0["trade_payables"]
    pay_prev = t_prev["trade_payables"]
    if pay_prev > 0 and pay_t0 > pay_prev:
        delta_pay_pct = _safe_div(pay_t0 - pay_prev, pay_prev)
        cost_t0 = t0["materials"] + t0["other_exp"]
        cost_prev = t_prev["materials"] + t_prev["other_exp"]
        delta_cost_pct = _safe_div(cost_t0 - cost_prev, cost_prev) if cost_prev > 0 else 0.0
        if delta_pay_pct > (delta_cost_pct + thresholds.PAYABLES_GROWTH_TOLERANCE):
            signals.append({
                "signal_id": "S02",
                "title": SIGNAL_TITLES["S02"],
                "drift_type": "working_capital_drag",
                "severity": "medium",
                "observation": (
                    f"Trade payables expanded {delta_pay_pct * 100:+.1f}% "
                    f"while operating costs changed {delta_cost_pct * 100:+.1f}%."
                ),
                "audit_lead": "Operational growth financed through stretched vendor credit; evaluate MSME compliance.",
                "evidence_lead": "MSME overdue registers, vendor confirmation statements, and post-balance-sheet settlements.",
            })

    # DuPont Leverage Dominance (S13)
    if dupont.get("leverage_dominated"):
        signals.append({
            "signal_id": "S13",
            "title": SIGNAL_TITLES["S13"],
            "drift_type": "profitability_decomposition",
            "severity": "high",
            "observation": (
                "DuPont 3-way decomposition indicates ROE expansion is primarily driven by equity multiplier "
                "leverage, while operating margins or asset turnover remained flat or contracted."
            ),
            "audit_lead": "Leverage-driven ROE masks underlying operational margin contraction.",
            "evidence_lead": "Debt covenants, interest coverage capacity, and debt-equity gearing analysis.",
        })

    # Accruals-Heavy Earnings (S06)
    assets_t0 = t0["assets"] or 0.0
    if assets_t0 > 0:
        accruals = t0["pat"] - t0["ocf"]
        accruals_ratio = accruals / assets_t0
        if accruals_ratio >= thresholds.ACCRUALS_RATIO_ALERT:
            signals.append({
                "signal_id": "S06",
                "title": SIGNAL_TITLES["S06"],
                "drift_type": "quality_of_earnings",
                "severity": "high",
                "observation": (
                    f"Accruals ratio ({accruals_ratio * 100:.1f}% of total assets) reflects earnings "
                    f"decoupled from cash generation (PAT {_format_inr(t0['pat'])} vs OCF {_format_inr(t0['ocf'])})."
                ),
                "audit_lead": "High accrual content indicates subjective revenue or uncollected debtor accretion.",
                "evidence_lead": "Cash flow operating reconciliation, revenue recognition milestones, and debtor realization.",
            })

    return signals


# ---------------------------------------------------------------------------
# Deterministic Fallback Synthesis
# ---------------------------------------------------------------------------

def deterministic_trends(
    company_name: str,
    cin: str,
    fy_label: str,
    series: dict[str, Any],
    common_size: dict[str, Any],
    dupont: dict[str, Any],
    signals: list[dict[str, Any]],
    thresholds: TrendThresholds = THRESHOLDS,
) -> dict[str, Any]:
    series_years = series["series_years"]
    period_labels = series["period_labels"]

    # Single-period handling (Spec §9.5)
    if series_years <= 1:
        return {
            "doc_id": "",
            "company_name": company_name,
            "cin": cin,
            "fy_label": fy_label,
            "series_years": series_years,
            "period_labels": period_labels,
            "summary_lede": (
                f"{company_name} is in its initial reporting period ({fy_label}). "
                f"{SINGLE_PERIOD_DISCLOSURE}"
            ),
            "dupont_narrative": "DuPont multi-year decomposition requires at least two comparable reporting periods.",
            "structural_drift_cards": [],
            "common_size_highlights": [],
        }

    # Multi-period synthesis
    fired_count = len(signals)
    drift_highlights = common_size.get("highlights", [])
    lede_parts = [
        f"Multi-period diagnostic panel across {series_years} periods ({', '.join(period_labels)}) "
        f"identifies {fired_count} directional drift signal(s) requiring audit planning attention."
    ]

    # Archetype highlights in lede
    sig_ids = {s["signal_id"] for s in signals}
    if "S05" in sig_ids:
        lede_parts.append("Trade receivables growth diverged from revenue trajectory, pointing to customer mix or collection pace shifts.")
    if "S17" in sig_ids:
        lede_parts.append("Material reclassification and placement shifts observed across short-term bank balances and current financial assets.")
    if "S16" in sig_ids:
        lede_parts.append("Provision and impairment allowances exhibit sharp year-on-year volatility.")
    if "S15" in sig_ids:
        lede_parts.append("Debt service coverage contracted directionally on EBIT compression.")

    summary_lede = " ".join(lede_parts)

    # DuPont narrative
    dup_periods = dupont.get("periods", [])
    if dup_periods:
        latest = dup_periods[-1]
        dupont_narrative = (
            f"Latest ROE stands at {latest['roe_display']} (Net Margin {latest['margin_display']} × "
            f"Asset Turnover {latest['turnover_display']} × Equity Multiplier {latest['multiplier_display']}). "
        )
        if dupont.get("leverage_dominated"):
            dupont_narrative += "Decomposition indicates ROE expansion is predominantly leverage-driven rather than operating margin expansion."
        else:
            dupont_narrative += "Profitability drivers reflect operating margin and asset productivity alignment."
    else:
        dupont_narrative = "DuPont components computed across reporting periods."

    return {
        "doc_id": "",
        "company_name": company_name,
        "cin": cin,
        "fy_label": fy_label,
        "series_years": series_years,
        "period_labels": period_labels,
        "summary_lede": summary_lede,
        "dupont_narrative": dupont_narrative,
        "structural_drift_cards": signals,
        "common_size_highlights": drift_highlights,
    }


# ---------------------------------------------------------------------------
# Master Entrypoint: build_key_trends
# ---------------------------------------------------------------------------

def build_key_trends(
    doc_id: str,
    company_name: str,
    cin: str,
    fy_label: str,
    metric_rows: list[dict[str, Any]],
    *,
    thresholds: TrendThresholds = THRESHOLDS,
    use_llm: bool = True,
    chat_fn: Any = None,
) -> dict[str, Any]:
    """
    Master builder for Block 5: Key Trends & Structural Drift.
    Builds time series, common-size drift, DuPont decomposition, detects signals,
    and returns a publication-grade payload with LLM or deterministic engine.
    """
    series = assemble_time_series(metric_rows)
    common_size = compute_common_size_and_drift(series, thresholds)
    dupont = compute_dupont_decomposition(series, thresholds)
    signals = evaluate_trend_signals(series, common_size, dupont, thresholds)

    series_years = series["series_years"]
    period_labels = series["period_labels"]

    formed = False
    payload_data: dict[str, Any] = {}
    reason = ""

    # If single period, deterministic disclosure is required by Spec §9.5
    if series_years <= 1:
        fallback = deterministic_trends(
            company_name, cin, fy_label, series, common_size, dupont, signals, thresholds
        )
        payload_data = lint_trend_output(fallback)
        payload_data["doc_id"] = doc_id
        payload_data["formed"] = True
        payload_data["reason"] = SINGLE_PERIOD_DISCLOSURE
        return payload_data

    # Multi-period LLM path
    if use_llm and chat_fn is None:
        reason = "LLM synthesis bypassed — chat function was not configured. Deterministic trends engine used."
    elif use_llm:
        try:
            # Build structured context blocks
            cs_lines = [
                f"- Period {p['period_date']}: Asset Mix (PPE: {p['asset_mix']['ppe_share']:.1f}%, "
                f"CWIP: {p['asset_mix']['cwip_share']:.1f}%, Rec: {p['asset_mix']['receivables_share']:.1f}%, "
                f"Cash: {p['asset_mix']['cash_bank_share']:.1f}%, Other Non-Current: {p['asset_mix']['other_noncur_share']:.1f}%) | "
                f"Funding Mix (Equity: {p['funding_mix']['equity_share']:.1f}%, Debt: {p['funding_mix']['borrowings_share']:.1f}%, "
                f"Payables: {p['funding_mix']['payables_share']:.1f}%)"
                for p in common_size.get("periods", [])
            ]
            common_size_block = "COMMON-SIZE STRUCTURE & DRIFT:\n" + "\n".join(cs_lines)

            dup_lines = [
                f"- Period {p['period_date']}: Margin {p['margin_display']} × Turnover {p['turnover_display']} × Multiplier {p['multiplier_display']} = ROE {p['roe_display']}"
                for p in dupont.get("periods", [])
            ]
            dupont_block = "DUPONT PROFITABILITY DECOMPOSITION:\n" + "\n".join(dup_lines)

            sig_lines = [
                f"- [{s['signal_id']}] {s['title']}: {s['observation']} Lead: {s['audit_lead']}"
                for s in signals
            ]
            signals_block = "DETECTED TREND & DRIFT SIGNALS:\n" + "\n".join(sig_lines) if sig_lines else "DETECTED SIGNALS: (None flagged)"

            user_turn = P.build_trend_user_prompt(
                company_name=company_name,
                cin=cin,
                fy_label=fy_label,
                series_years=series_years,
                period_labels=period_labels,
                common_size_block=common_size_block,
                dupont_block=dupont_block,
                signals_block=signals_block,
            )

            messages = [
                {"role": "system", "content": P.SYSTEM_PROMPT},
                {"role": "user", "content": user_turn},
            ]
            raw_response = chat_fn(messages, max_tokens=1500, temperature=0.0)

            clean_json = re.sub(r'^```(?:json)?\s*', '', raw_response.strip())
            clean_json = re.sub(r'\s*```$', '', clean_json).strip()

            parsed = json.loads(clean_json)
            if "structural_drift_cards" in parsed and isinstance(parsed["structural_drift_cards"], list):
                payload_data = lint_trend_output(parsed)
                formed = True
            else:
                reason = "LLM response lacked required structural_drift_cards; fell back to deterministic engine."
        except Exception as e:
            logger.warning(f"Trends LLM synthesis bypassed: {type(e).__name__}: {e}")
            reason = f"LLM synthesis unavailable ({type(e).__name__}); using deterministic trends engine."

    if not formed:
        fallback = deterministic_trends(
            company_name, cin, fy_label, series, common_size, dupont, signals, thresholds
        )
        payload_data = lint_trend_output(fallback)
        formed = True

    payload_data["doc_id"] = doc_id
    payload_data["company_name"] = company_name
    payload_data["cin"] = cin
    payload_data["fy_label"] = fy_label
    payload_data["series_years"] = series_years
    payload_data["period_labels"] = period_labels
    payload_data["formed"] = formed
    payload_data["reason"] = reason

    # Attach analytical structures for front-end schedules and transparency
    payload_data["common_size_schedule"] = common_size.get("periods", [])
    payload_data["dupont_schedule"] = dupont.get("periods", [])

    return payload_data
