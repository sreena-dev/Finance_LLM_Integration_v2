"""Provisional materiality proxies, structural ratios and risk rating (spec 8).

All figures are TB-derived and labelled provisional; they are for prioritisation
only and never override auditor-set materiality or materiality by nature/context.
"""

from __future__ import annotations

from yukta_rag.audit.audit_config import (ASSET_HEAVY_REVENUE_RATIO, BENCHMARK_PCT,
                                          PBT_HEALTHY_PCT_OF_REVENUE,
                                          VALUE_COVERAGE_WARN_THRESHOLD)
from yukta_rag.core.financial_math import ratio as _fm_ratio

# typical benchmark percentages used only to size a provisional threshold (spec 8.1)
_BENCHMARK_PCT = BENCHMARK_PCT


def materiality_proxies(statements: dict) -> dict:
    """Provisional materiality proxies from the statement subtotals (spec 8.1)."""
    pl, bs = statements["profit_and_loss"], statements["balance_sheet"]
    pbt = pl["net_profit"]           # closest PBT proxy from a TB (no tax split)
    net_worth = round(bs["equity"] + pbt, 2)
    amounts = {
        "profit_before_tax": pbt, "revenue": pl["income"], "total_assets": bs["assets"],
        "total_expenses": pl["expenses"], "net_worth": net_worth,
    }
    bases = {}
    for k, amt in amounts.items():
        bases[k] = {"amount": amt, "benchmark_pct": _BENCHMARK_PCT[k],
                    "provisional_materiality": round(abs(amt) * _BENCHMARK_PCT[k] / 100, 2)}

    # choose a base: PBT if healthy & positive, else revenue, else total assets
    # (spec E3b — no entity-type classification signal exists in this pipeline yet,
    # so this stays a flat waterfall; base_reason states why THIS waterfall's
    # outcome applies, and flags when the revenue fallback looks asset-heavy)
    total_assets_amt = bs["assets"]
    if pbt > 0 and pl["income"] and pbt > PBT_HEALTHY_PCT_OF_REVENUE * pl["income"]:
        chosen = "profit_before_tax"
        base_reason = (f"Profit before tax ({pbt:,.0f}) is positive and at least "
                       f"{PBT_HEALTHY_PCT_OF_REVENUE:.0%} of revenue — a profit-making, "
                       "stable-enough period to size materiality off profit.")
    elif pl["income"] > 0:
        chosen = "revenue"
        base_reason = ("Profit before tax is thin or negative, so revenue is used instead.")
        if total_assets_amt and total_assets_amt > ASSET_HEAVY_REVENUE_RATIO * pl["income"]:
            base_reason += (f" Note: total assets are more than {ASSET_HEAVY_REVENUE_RATIO:g}x "
                            "revenue for this entity — an asset-heavy/infrastructure profile "
                            "where 'total assets' may be the more defensible base; worth a "
                            "manual check.")
    else:
        chosen = "total_assets"
        base_reason = "Neither profit before tax nor revenue is usable as a base."
    cb = bases[chosen]
    return {
        "bases": bases,
        "chosen_base": chosen,
        "base_reason": base_reason,
        "provisional_overall_materiality": cb["provisional_materiality"],
        "calculation_basis": (f"{cb['benchmark_pct']:g}% of {chosen} {cb['amount']:,.0f} "
                              f"= {cb['provisional_materiality']:,.0f}"),
        "note": ("Provisional, TB-derived, for prioritisation only. It does not override "
                 "auditor-approved materiality or materiality by nature/context (grants, "
                 "deposits, write-offs, statutory dues, suspense, public money)."),
    }


def structure_ratios(roles: dict, role_coverage_pct: dict, statements: dict) -> dict:
    """Structural / intensity ratios (spec 8.3), gated per-pair on mapping coverage
    (spec QNT-04) and returned with the numerator/denominator working (spec
    QNT-06). Each ratio is ``{"value": number|None|"not computed...", "working":
    str|None}`` — null value where a denominator is absent; the gated string
    where either role's mapping coverage is too thin to trust."""
    def coverage_ok(*keys):
        return all(role_coverage_pct.get(k, 0.0) >= VALUE_COVERAGE_WARN_THRESHOLD for k in keys)

    def r(num, den, num_label, den_label):
        if not coverage_ok(num, den):
            cov = ", ".join(f"{lbl} mapping coverage {role_coverage_pct.get(k, 0.0):g}%"
                            for k, lbl in ((num, num_label), (den, den_label)))
            return {"value": "not computed — insufficient mapping coverage", "working": cov}
        num_v, den_v = abs(roles.get(num, 0.0)), abs(roles.get(den, 0.0))
        val = _fm_ratio(num_v, den_v, 3)
        working = (f"{num_label} {num_v:,.0f} / {den_label} {den_v:,.0f}"
                  if val is not None else None)
        return {"value": val, "working": working}

    bs, pl = statements["balance_sheet"], statements["profit_and_loss"]
    equity_base = bs["equity"] + pl["net_profit"]
    debt_to_equity = (_fm_ratio(bs["liabilities"], equity_base, 3) if equity_base else None)
    return {
        "debtor_intensity": r("receivables", "revenue", "receivables", "revenue"),
        "creditor_intensity": r("payables", "purchases", "payables", "purchases"),
        "inventory_intensity": r("inventory", "purchases", "inventory", "purchases"),
        "depreciation_proxy": r("depreciation", "ppe", "depreciation", "PPE"),
        "finance_cost_ratio": r("finance_cost", "borrowings", "finance cost", "borrowings"),
        "gst_output_to_revenue": r("gst_output", "revenue", "GST output", "revenue"),
        # statement-level (not role-based), so not gated on role coverage —
        # bs/pl subtotals are already the TB's own classified figures
        "debt_to_equity": {"value": debt_to_equity,
                           "working": (f"liabilities {bs['liabilities']:,.0f} / equity incl. "
                                      f"profit {equity_base:,.0f}") if debt_to_equity is not None else None},
        "notes": ["Role-based ratios use TB-mapped subtotals and are gated on mapping "
                  "coverage; a null value means the denominator is absent, and the gated "
                  "string means the underlying accounts aren't confidently enough mapped to "
                  "trust the ratio. These are structural indicators, not conclusions."],
    }


# ---------------------------------------------------------------------------
# Risk rating (spec 8.2) — value / nature / context / relationship / data quality
# ---------------------------------------------------------------------------

def rate_risk(*, amount: float = 0.0, materiality: float | None = None,
              sensitive: bool = False, relationship_gap: bool = False,
              data_quality_issue: bool = False, forced: str | None = None) -> tuple[str, list]:
    """Return (risk_rating, risk_basis[]) from the spec's five bases."""
    if forced:
        return forced, ["data_quality"] if data_quality_issue else ["nature"]
    basis = []
    material = materiality is not None and abs(amount) >= materiality
    if material:
        basis.append("value")
    if sensitive:
        basis.append("nature")
    if relationship_gap:
        basis.append("relationship")
    if data_quality_issue:
        basis.append("data_quality")
    # rating: high if material+sensitive, or material relationship gap, or severe data issue
    if data_quality_issue and not material and not sensitive:
        rating = "information_request"
    elif (material and (sensitive or relationship_gap)) or (relationship_gap and sensitive):
        rating = "high"
    elif material or sensitive or relationship_gap:
        rating = "medium"
    else:
        rating = "low"
    if not basis:
        basis = ["context"]
    return rating, basis
