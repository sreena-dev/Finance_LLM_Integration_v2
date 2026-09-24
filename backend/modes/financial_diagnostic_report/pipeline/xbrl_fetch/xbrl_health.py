"""
xbrl_health.py — Computational engine and narrative synthesizer for Block 4: Financial Health Summary.

Implements FDR Specification v3.0:
- Layer 2: Financial Structure & Asset Mix (§7)
- Layer 3: Performance Decomposition & Causality Attribution (§8)
- Layer 4: Financial Quality & Cash Conversion (§9)
- Safe Language Rules (§17)
- Output Architecture Row 4 (§14.1)

Directly respects native XBRL reporting units (Millions, Crores, Lakhs) via
`LevelOfRoundingUsedInFinancialStatements` from `disclosures`.
"""
from __future__ import annotations
import json
import logging
import re
from typing import Any

from . import xbrl_health_prompt as P

logger = logging.getLogger(__name__)

_PROHIBITED_REPLACEMENTS: dict[str, str] = {
    "proves": "indicates",
    "proved": "indicated",
    "certifies": "records",
    "certified": "recorded",
    "guarantees": "suggests",
    "guaranteed": "suggested",
    "is fraudulent": "warrants audit corroboration",
    "fraudulent": "divergent",
    "fraud": "reporting divergence",
    "will fail": "exhibits liquidity pressure",
    "insolvent": "under capital constraint",
    "falsified": "divergent",
}


def _clean_str(text: str) -> str:
    s = text
    for phrase, rep in _PROHIBITED_REPLACEMENTS.items():
        s = re.sub(rf"\b{re.escape(phrase)}\b", rep, s, flags=re.IGNORECASE)
    return s


def _safe_div(num: float | None, denom: float | None, default: float = 0.0) -> float:
    if num is None or denom is None or abs(denom) < 1e-6:
        return default
    return num / denom


def _detect_native_scale(disclosures: list[dict[str, Any]], max_val: float = 0.0) -> str:
    """Detects native reporting scale from LevelOfRoundingUsedInFinancialStatements or magnitude."""
    for d in disclosures:
        if d.get("concept_name") == "LevelOfRoundingUsedInFinancialStatements":
            text = (d.get("text") or "").strip().lower()
            if "million" in text:
                return "Millions"
            if "crore" in text:
                return "Crores"
            if "lakh" in text:
                return "Lakhs"
            if "actual" in text:
                return "Actuals"

    # Magnitude heuristic if disclosure is absent
    if max_val >= 1e9:
        return "Crores"
    if 0 < max_val < 1e7:
        return "Lakhs"
    return "Crores"


def _format_amount(val: float | None, scale: str) -> str:
    """Formats values in the exact native reporting scale of the filing."""
    if val is None:
        return "N/A"
    if val == 0:
        return "₹0.00"
    abs_val = abs(val)
    sign = "-" if val < 0 else ""

    if scale == "Millions":
        # e.g., ₹1,437,941 M
        millions = abs_val / 1e6
        if 0 < millions < 0.005:
            return f"{sign}<₹0.01 M"
        return f"{sign}₹{millions:,.0f} M" if abs(millions - round(millions)) < 0.01 else f"{sign}₹{millions:,.2f} M"

    if scale == "Crores":
        # e.g., ₹587.57 cr
        cr = abs_val / 1e7
        if 0 < cr < 0.005:
            return f"{sign}<₹0.01 cr"
        return f"{sign}₹{cr:,.2f} cr"

    if scale == "Lakhs":
        # e.g., ₹45.00 lakh
        lakh = abs_val / 1e5
        if 0 < lakh < 0.005:
            return f"{sign}<₹0.01 lakh"
        return f"{sign}₹{lakh:,.2f} lakh"

    return f"{sign}₹{abs_val:,.2f}"



def extract_business_context(
    turnover_facts: list[dict[str, Any]],
    disclosures: list[dict[str, Any]],
    cin: str,
) -> dict[str, Any]:
    """Extracts business activity, main products, NIC codes, and turnover shares."""
    main_products: list[str] = []
    nic_codes: list[str] = []
    rounding_scale = "Crores"

    for d in disclosures:
        cname = d.get("concept_name")
        text = (d.get("text") or "").strip()
        if not text or text == "NA":
            continue

        if cname == "NameOfMainProductOrService" and text not in main_products:
            # Clean newlines
            clean_p = " ".join(text.split())
            if clean_p not in main_products:
                main_products.append(clean_p)
        elif cname == "NICCodeOfProductOrService" and text not in nic_codes:
            nic_codes.append(text)
        elif cname == "LevelOfRoundingUsedInFinancialStatements":
            t_low = text.lower()
            if "million" in t_low:
                rounding_scale = "Millions"
            elif "lakh" in t_low:
                rounding_scale = "Lakhs"
            elif "crore" in t_low:
                rounding_scale = "Crores"

    # Turnover shares
    shares: list[dict[str, Any]] = []
    for tf in turnover_facts:
        val = tf.get("value_numeric")
        if val is not None:
            dim = tf.get("dimensions") or {}
            label = dim.get("PrincipalBusinessActivitiesOfCompany") or "Activity"
            shares.append({"activity": label, "share_pct": float(val) * 100 if float(val) <= 1.0 else float(val)})

    # Extract 5-digit industry code from CIN
    cin_industry = cin[1:6] if len(cin) >= 6 and cin[1:6].isdigit() else ""

    summary_parts = []
    if main_products:
        summary_parts.append(f"Core activities and main products/services: {', '.join(main_products)}.")
    elif cin_industry:
        summary_parts.append(f"Operating in industry sector NIC: {cin_industry}.")
    else:
        summary_parts.append("Registered corporate commercial operations.")

    if nic_codes and nic_codes != ["NA"]:
        summary_parts.append(f"Product NIC classification: {', '.join(nic_codes)}.")

    return {
        "main_products": main_products,
        "nic_codes": nic_codes,
        "cin_industry": cin_industry,
        "turnover_shares": shares,
        "rounding_scale": rounding_scale,
        "summary": " ".join(summary_parts),
    }


def compute_health_metrics(
    facts: list[dict[str, Any]],
    turnover_facts: list[dict[str, Any]],
    disclosures: list[dict[str, Any]],
    meta: dict[str, Any],
) -> dict[str, Any]:
    """Computes exact balance sheet structure, liquidity, capital mix, and performance decomposition."""
    cin = meta.get("entity_cin") or ""
    company_name = meta.get("company_name") or ""
    biz_ctx = extract_business_context(turnover_facts, disclosures, cin)

    # Group facts by period (fy_end)
    by_period: dict[str, dict[str, float]] = {}
    for f in facts:
        end_d = f.get("fy_end")
        if not end_d:
            continue
        p_key = str(end_d)
        by_period.setdefault(p_key, {})
        cname = f.get("concept_name")
        val = f.get("value_numeric")
        if cname and val is not None:
            by_period[p_key][cname] = float(val)

    sorted_periods = sorted(by_period.keys(), reverse=True)
    t0_key = sorted_periods[0] if sorted_periods else ""
    t_prev_key = sorted_periods[1] if len(sorted_periods) > 1 else ""

    raw_t0 = by_period.get(t0_key, {})
    raw_prev = by_period.get(t_prev_key, {})

    max_val = max(raw_t0.values(), default=0.0)
    scale = biz_ctx.get("rounding_scale") or _detect_native_scale(disclosures, max_val)

    # 1. Structure Analysis (t0)
    assets = raw_t0.get("Assets", 0.0)
    ppe = raw_t0.get("PropertyPlantAndEquipment", 0.0)
    producing_props = raw_t0.get("ProducingProperties", 0.0)
    cwip = raw_t0.get("CapitalWorkInProgress", 0.0)
    facilities_prog = raw_t0.get("FacilitiesInProgress", 0.0)
    exploratory_wells = raw_t0.get("ExploratoryWellsInProgress", 0.0)
    development_wells = raw_t0.get("DevelopmentWellsInProgress", 0.0)

    inv = (
        raw_t0.get("Investments")
        or (raw_t0.get("NoncurrentInvestments", 0.0) + raw_t0.get("CurrentInvestments", 0.0))
    )
    ca = raw_t0.get("CurrentAssets", 0.0)
    cl = raw_t0.get("CurrentLiabilities", 0.0)
    net_wc = ca - cl
    current_ratio = _safe_div(ca, cl)

    ppe_share = _safe_div(ppe, assets) * 100
    producing_share = _safe_div(producing_props, assets) * 100
    cwip_share = _safe_div(cwip, assets) * 100
    inv_share = _safe_div(inv, assets) * 100
    ca_share = _safe_div(ca, assets) * 100

    # Determine what genuinely dominates the asset base
    if producing_props > 0 and (producing_props > ppe or producing_share >= 25.0):
        dominant_type = "producing_props"
        dominant_label = "oil & gas assets"
        dominant_val = producing_props
        dominant_share = producing_share
    elif cwip > ppe and (cwip_share >= 35.0 or (assets > 0 and cwip > assets * 0.4)):
        dominant_type = "cwip"
        dominant_label = "capital work-in-progress"
        dominant_val = cwip
        dominant_share = cwip_share
    elif ppe >= cwip and ppe_share >= 20.0:
        dominant_type = "ppe"
        dominant_label = "property, plant and equipment"
        dominant_val = ppe
        dominant_share = ppe_share
    elif inv_share >= 40.0:
        dominant_type = "investments"
        dominant_label = "non-current investments"
        dominant_val = inv
        dominant_share = inv_share
    elif ca_share >= 50.0:
        dominant_type = "current_assets"
        dominant_label = "current assets"
        dominant_val = ca
        dominant_share = ca_share
    else:
        candidates = [
            (producing_props, "oil & gas assets", "producing_props", producing_share),
            (ppe, "property, plant and equipment", "ppe", ppe_share),
            (cwip, "capital work-in-progress", "cwip", cwip_share),
            (inv, "non-current investments", "investments", inv_share),
            (ca, "current assets", "current_assets", ca_share),
        ]
        top = max(candidates, key=lambda c: c[0])
        dominant_val, dominant_label, dominant_type, dominant_share = top

    equity = (
        raw_t0.get("Equity")
        or (raw_t0.get("EquityShareCapital", 0.0) + raw_t0.get("OtherEquity", 0.0))
    )
    borr_nc = raw_t0.get("BorrowingsNoncurrent", 0.0)
    borr_cur = raw_t0.get("BorrowingsCurrent", 0.0)
    total_borr = borr_nc + borr_cur
    total_cap = equity + total_borr
    equity_share = _safe_div(equity, total_cap) * 100
    debt_share = _safe_div(total_borr, total_cap) * 100

    # 2. Performance Decomposition (t0 vs t_prev)
    rev_t0 = raw_t0.get("RevenueFromOperations", 0.0)
    rev_prev = raw_prev.get("RevenueFromOperations", 0.0)
    delta_rev = rev_t0 - rev_prev
    pct_rev = _safe_div(delta_rev, rev_prev) * 100

    pat_t0 = raw_t0.get("ProfitLossForPeriod", 0.0)
    pat_prev = raw_prev.get("ProfitLossForPeriod", 0.0)
    delta_pat = pat_t0 - pat_prev
    pct_pat = _safe_div(delta_pat, abs(pat_prev)) * 100

    pbt_t0 = raw_t0.get("ProfitBeforeTax", 0.0)
    pbt_prev = raw_prev.get("ProfitBeforeTax", 0.0)

    dda = raw_t0.get("DepreciationDepletionAndAmortisationExpense", 0.0)
    imp = raw_t0.get("ImpairmentLossRecognisedInProfitOrLossPropertyPlantAndEquipment", 0.0)
    depr_imp = dda + imp

    prov_t0 = raw_t0.get("ProvisionsCurrent", 0.0) + raw_t0.get("ProvisionsNoncurrent", 0.0)
    prov_prev = raw_prev.get("ProvisionsCurrent", 0.0) + raw_prev.get("ProvisionsNoncurrent", 0.0)
    delta_prov = prov_t0 - prov_prev
    pct_prov = _safe_div(delta_prov, prov_prev) * 100

    tax = raw_t0.get("TaxExpense", 0.0)
    def_tax = raw_t0.get("DeferredTaxExpense", 0.0)

    ocf_t0 = raw_t0.get("CashFlowsFromUsedInOperatingActivities", 0.0)
    ocf_prev = raw_prev.get("CashFlowsFromUsedInOperatingActivities", 0.0)
    cash_backing_ratio = _safe_div(ocf_t0, pat_t0) if pat_t0 > 0 else 0.0

    return {
        "scale": scale,
        "biz_ctx": biz_ctx,
        "t0_key": t0_key,
        "t_prev_key": t_prev_key,
        "structure": {
            "assets": assets,
            "dominant_type": dominant_type,
            "dominant_fixed": dominant_val,
            "dominant_label": dominant_label,
            "dominant_share": dominant_share,
            "ppe": ppe,
            "ppe_share": ppe_share,
            "cwip": cwip,
            "cwip_share": cwip_share,
            "facilities_prog": facilities_prog,
            "exploratory_wells": exploratory_wells,
            "development_wells": development_wells,
            "investments": inv,
            "inv_share": inv_share,
            "ca": ca,
            "cl": cl,
            "net_wc": net_wc,
            "current_ratio": current_ratio,
            "equity": equity,
            "borrowings": total_borr,
            "total_cap": total_cap,
            "equity_share": equity_share,
            "debt_share": debt_share,
        },
        "performance": {
            "rev_t0": rev_t0,
            "rev_prev": rev_prev,
            "delta_rev": delta_rev,
            "pct_rev": pct_rev,
            "pat_t0": pat_t0,
            "pat_prev": pat_prev,
            "delta_pat": delta_pat,
            "pct_pat": pct_pat,
            "pbt_t0": pbt_t0,
            "pbt_prev": pbt_prev,
            "depr_imp": depr_imp,
            "dda": dda,
            "impairment": imp,
            "prov_t0": prov_t0,
            "prov_prev": prov_prev,
            "delta_prov": delta_prov,
            "pct_prov": pct_prov,
            "tax": tax,
            "deferred_tax": def_tax,
            "ocf_t0": ocf_t0,
            "ocf_prev": ocf_prev,
            "cash_backing_ratio": cash_backing_ratio,
        },
    }


def deterministic_health_summary(
    company_name: str,
    cin: str,
    fy_label: str,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    """Generates authoritative, deterministic structure and performance narrative."""
    scale = metrics["scale"]
    st = metrics["structure"]
    pf = metrics["performance"]
    biz = metrics["biz_ctx"]
    scale_div = 1e6 if scale == "Millions" else (1e7 if scale == "Crores" else 1e5)

    # 1. Business Type Narrative
    biz_type = biz["summary"]

    # 2. Structure Narrative
    str_parts = []
    # Asset Base
    if st.get("dominant_type") == "cwip":
        dom_str = _format_amount(st["cwip"], scale)
        dom_desc = f"Asset base is dominated by capital work-in-progress ({dom_str}, ≈{st['cwip_share']:.0f}% of assets)"
        cwip_parts = []
        if st["facilities_prog"] > 0:
            cwip_parts.append(f"facilities in progress {_format_amount(st['facilities_prog'], scale)}")
        if st["exploratory_wells"] > 0:
            cwip_parts.append(f"exploratory wells in progress {_format_amount(st['exploratory_wells'], scale)}")
        if st["development_wells"] > 0:
            cwip_parts.append(f"development wells {_format_amount(st['development_wells'], scale)}")
        if cwip_parts:
            dom_desc += f" — {', '.join(cwip_parts)} —"

        if st["ppe"] / scale_div >= 0.01:
            dom_desc += f" with completed property, plant and equipment of {_format_amount(st['ppe'], scale)}"
        elif st["ppe"] > 0:
            dom_desc += f" with completed property, plant and equipment nominal ({_format_amount(st['ppe'], scale)})"
        else:
            dom_desc += " with no commissioned operational fixed assets"

        if st["investments"] > 0 and st["inv_share"] >= 1.0:
            dom_desc += f" and a {_format_amount(st['investments'], scale)} non-current investment book (≈{st['inv_share']:.0f}% of assets)."
        else:
            dom_desc += "."

    elif st.get("dominant_type") == "producing_props":
        dom_str = _format_amount(st["dominant_fixed"], scale)
        dom_desc = f"Asset base is dominated by {st['dominant_label']} ({dom_str})"
        cwip_parts = []
        if st["facilities_prog"] > 0:
            cwip_parts.append(f"facilities in progress {_format_amount(st['facilities_prog'], scale)}")
        if st["exploratory_wells"] > 0:
            cwip_parts.append(f"exploratory wells in progress {_format_amount(st['exploratory_wells'], scale)}")
        if st["development_wells"] > 0:
            cwip_parts.append(f"development wells {_format_amount(st['development_wells'], scale)}")
        if cwip_parts:
            dom_desc += f" plus very large capital work-in-progress — {', '.join(cwip_parts)} —"
        elif st["cwip"] > 0 and st["cwip"] / scale_div >= 0.01:
            dom_desc += f" plus capital work-in-progress of {_format_amount(st['cwip'], scale)}"

        if st["investments"] > 0 and st["inv_share"] >= 1.0:
            dom_desc += f" and a {_format_amount(st['investments'], scale)} non-current investment book (≈{st['inv_share']:.0f}% of assets)."
        else:
            dom_desc += "."

    else:
        dom_str = _format_amount(st["dominant_fixed"], scale)
        dom_desc = f"Asset base is dominated by {st['dominant_label']} ({dom_str})"
        cwip_parts = []
        if st["facilities_prog"] > 0:
            cwip_parts.append(f"facilities in progress {_format_amount(st['facilities_prog'], scale)}")
        if st["exploratory_wells"] > 0:
            cwip_parts.append(f"exploratory wells in progress {_format_amount(st['exploratory_wells'], scale)}")
        if st["development_wells"] > 0:
            cwip_parts.append(f"development wells {_format_amount(st['development_wells'], scale)}")
        if cwip_parts:
            dom_desc += f" plus capital work-in-progress — {', '.join(cwip_parts)} —"
        elif st["cwip"] > 0 and st["cwip"] / scale_div >= 0.01:
            dom_desc += f" plus capital work-in-progress of {_format_amount(st['cwip'], scale)}"

        if st["investments"] > 0 and st["inv_share"] >= 1.0:
            dom_desc += f" and a {_format_amount(st['investments'], scale)} non-current investment book (≈{st['inv_share']:.0f}% of assets)."
        else:
            dom_desc += "."

    str_parts.append(dom_desc)

    # Liquidity
    ca_str = _format_amount(st["ca"], scale)
    cl_str = _format_amount(st["cl"], scale)
    net_str = _format_amount(abs(st["net_wc"]), scale)
    if st["net_wc"] >= 0:
        str_parts.append(
            f"Liquidity is a comfortable net-current-asset position (current assets {ca_str} vs current liabilities {cl_str}, surplus of {net_str})."
        )
    else:
        str_parts.append(
            f"Liquidity is a net current-liability position (current assets {ca_str} vs current liabilities {cl_str}, shortfall of {net_str})."
        )

    # Funding Mix
    eq_str = _format_amount(st["equity"], scale)
    if st["borrowings"] == 0 or (st["total_cap"] > 0 and st["debt_share"] < 5.0):
        str_parts.append(
            "Funding is overwhelmingly equity and internal accruals; borrowings are immaterial to the capital structure."
        )
    else:
        borr_str = _format_amount(st["borrowings"], scale)
        str_parts.append(
            f"Funding is {st['equity_share']:.0f}% equity ({eq_str}) and {st['debt_share']:.0f}% borrowings ({borr_str})."
        )

    structure_text = " ".join(str_parts)

    # 3. Performance Narrative
    perf_parts = []
    pat_t0_str = _format_amount(pf["pat_t0"], scale)
    pat_prev_str = _format_amount(pf["pat_prev"], scale)

    if pf["pat_prev"] != 0:
        dir_word = "rose" if pf["delta_pat"] > 0 else "fell"
        perf_parts.append(f"Profit {dir_word} {pat_prev_str} → {pat_t0_str}.")
    else:
        perf_parts.append(f"Profit for the period reported at {pat_t0_str}.")

    decomp_legs = []
    if pf["rev_prev"] > 0 and abs(pf["delta_rev"]) / scale_div >= 0.005:
        rev_word = "growth" if pf["delta_rev"] > 0 else "decline"
        decomp_legs.append(f"a revenue {rev_word} ({pf['pct_rev']:+.1f}%)")

    if pf["depr_imp"] / scale_div >= 0.005:
        decomp_legs.append(f"depletion/impairment ({_format_amount(pf['depr_imp'], scale)})")

    if pf["prov_t0"] / scale_div >= 0.005:
        prov_note = f", up {pf['pct_prov']:+.1f}%" if pf["prov_prev"] > 0 else ""
        decomp_legs.append(f"provisions/write-offs ({_format_amount(pf['prov_t0'], scale)}{prov_note})")

    if pf["deferred_tax"] < 0 and abs(pf["deferred_tax"]) / scale_div >= 0.005:
        decomp_legs.append(f"partly cushioned by a deferred-tax credit ({_format_amount(abs(pf['deferred_tax']), scale)})")

    if decomp_legs:
        perf_parts.append(f"Decomposed, the movement is attributable to {', '.join(decomp_legs)}.")

    # Cash Backing
    ocf_str = _format_amount(pf["ocf_t0"], scale)
    if pf["pat_t0"] > 0:
        if pf["ocf_t0"] >= pf["pat_t0"]:
            perf_parts.append(
                f"Operating cash flow ({ocf_str}) strongly exceeds profit — earnings are cash-backed, a positive quality signal read against the business model, not mechanically."
            )
        else:
            perf_parts.append(
                f"Operating cash flow ({ocf_str}) lags profit — earnings are accrual-heavy, requiring scrutiny of debtor and inventory realisations."
            )
    else:
        perf_parts.append(f"Operating cash flow closed at {ocf_str}.")

    performance_text = " ".join(perf_parts)

    return {
        "doc_id": "",
        "company_name": company_name,
        "cin": cin,
        "fy_label": fy_label,
        "formed": True,
        "business_type": biz_type,
        "structure": structure_text,
        "performance": performance_text,
        "citations": [
            {"concept": "Assets", "value": st["assets"], "scale": scale},
            {"concept": "CurrentAssets", "value": st["ca"], "scale": scale},
            {"concept": "CurrentLiabilities", "value": st["cl"], "scale": scale},
            {"concept": "Equity", "value": st["equity"], "scale": scale},
            {"concept": "ProfitLossForPeriod", "value": pf["pat_t0"], "scale": scale},
            {"concept": "CashFlowsFromUsedInOperatingActivities", "value": pf["ocf_t0"], "scale": scale},
        ],
        "reason": "",
    }


def lint_health_output(data: dict[str, Any]) -> dict[str, Any]:
    """Sanitizes text fields to enforce SA 315 safe language rules."""
    for key in ("business_type", "structure", "performance"):
        if key in data and isinstance(data[key], str):
            data[key] = _clean_str(data[key])
    return data


def build_health_summary(
    doc_id: str,
    company_name: str,
    cin: str,
    fy_label: str,
    facts: list[dict[str, Any]],
    turnover_facts: list[dict[str, Any]],
    disclosures: list[dict[str, Any]],
    meta: dict[str, Any],
    *,
    use_llm: bool = True,
    chat_fn: Any = None,
) -> dict[str, Any]:
    """
    Master entrypoint for Block 4: Financial Health Summary.
    Computes grounded metrics and synthesizes interpreted narrative.
    """
    metrics = compute_health_metrics(facts, turnover_facts, disclosures, meta)
    fallback = deterministic_health_summary(company_name, cin, fy_label, metrics)
    fallback["doc_id"] = doc_id

    if not use_llm or chat_fn is None:
        fallback["reason"] = (
            "Deterministic health summary engine used (LLM synthesis bypassed or chat function unconfigured)."
        )
        return lint_health_output(fallback)

    try:
        scale = metrics["scale"]
        st = metrics["structure"]
        pf = metrics["performance"]
        biz = metrics["biz_ctx"]

        struct_lines = [
            f"- Total Assets: {_format_amount(st['assets'], scale)}",
            f"- Dominant Asset: {st['dominant_label']} ({_format_amount(st['dominant_fixed'], scale)})",
            f"- CWIP: {_format_amount(st['cwip'], scale)} (Facilities: {_format_amount(st['facilities_prog'], scale)}, Exploratory: {_format_amount(st['exploratory_wells'], scale)}, Dev: {_format_amount(st['development_wells'], scale)})",
            f"- Investments: {_format_amount(st['investments'], scale)} ({st['inv_share']:.1f}% of assets)",
            f"- Current Assets: {_format_amount(st['ca'], scale)} vs Current Liabilities: {_format_amount(st['cl'], scale)} (Net WC: {_format_amount(st['net_wc'], scale)})",
            f"- Capital Funding: Equity {_format_amount(st['equity'], scale)} ({st['equity_share']:.1f}%), Debt {_format_amount(st['borrowings'], scale)} ({st['debt_share']:.1f}%)",
        ]
        perf_lines = [
            f"- Profit for period: {_format_amount(pf['pat_prev'], scale)} → {_format_amount(pf['pat_t0'], scale)} ({pf['pct_pat']:+.1f}%)",
            f"- Revenue: {_format_amount(pf['rev_prev'], scale)} → {_format_amount(pf['rev_t0'], scale)} ({pf['pct_rev']:+.1f}%)",
            f"- Depletion/Impairment: {_format_amount(pf['depr_imp'], scale)}",
            f"- Provisions: {_format_amount(pf['prov_prev'], scale)} → {_format_amount(pf['prov_t0'], scale)} ({pf['pct_prov']:+.1f}%)",
            f"- Tax / Deferred Tax: {_format_amount(pf['tax'], scale)} / {_format_amount(pf['deferred_tax'], scale)}",
            f"- Operating Cash Flow: {_format_amount(pf['ocf_t0'], scale)} (vs Profit: {_format_amount(pf['pat_t0'], scale)})",
        ]

        user_prompt = P.build_health_user_prompt(
            company_name=company_name,
            cin=cin,
            fy_label=fy_label,
            business_context=biz["summary"],
            structure_context="\n".join(struct_lines),
            performance_context="\n".join(perf_lines),
        )

        response = chat_fn([
            {"role": "system", "content": P.SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ])

        # Extract JSON
        raw_text = response.strip() if isinstance(response, str) else str(response)
        match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if match:
            parsed = json.loads(match.group(0))
            cleaned = lint_health_output(parsed)
            fallback["business_type"] = cleaned.get("business_type") or fallback["business_type"]
            fallback["structure"] = cleaned.get("structure") or fallback["structure"]
            fallback["performance"] = cleaned.get("performance") or fallback["performance"]
            fallback["reason"] = "Synthesized via LLM audit diagnostic assistant."
            return fallback

    except Exception as exc:
        logger.warning(f"LLM health summary generation failed, falling back to deterministic: {exc}")
        fallback["reason"] = f"Deterministic engine used (LLM synthesis fallback: {exc})"

    return lint_health_output(fallback)
