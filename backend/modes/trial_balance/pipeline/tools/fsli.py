import datetime
import hashlib
import json
import time
from pathlib import Path

import polars as pl

from modes.trial_balance.pipeline.tools._shared import *  # noqa: F401,F403
from modes.trial_balance.pipeline.tools.canonical_schema import *  # noqa: F401,F403
from modes.trial_balance.pipeline.tools.pipeline_tool import *  # noqa: F401,F403

_COMPONENTS = {
    "revenue": ("revenue from operations", "revenue", "sales", "income from operations"),
    "trade_receivables": ("trade receivable", "sundry debtor", "debtors"),
    "trade_payables": ("trade payable", "sundry creditor", "creditors"),
    # Merged from the old, separate "purchases" component: a manufacturing/trading TB says
    # "Purchases"/"Cost of Materials"/"Cost of Goods Sold"; a services TB says "Cost of
    # Revenue"/"Cost of Services" for the same economic line. Treating these as two different
    # components (as the old "purchases"-only keyword list did) is why creditor/inventory
    # intensity came back null for every services-sector TB -- there is no "Purchases" line
    # for them to match, ever, by design, not because the data is incomplete.
    "cogs": ("cost of material", "cost of goods", "cost of sales", "cost of revenue",
             "cost of services", "purchase of stock", "purchases"),
    "inventory": ("inventory", "inventories", "stock-in-trade", "stock in trade"),
    "gross_ppe": ("property, plant and equipment", "property plant", "tangible asset", "gross block", "plant and machinery"),
    "depreciation": ("depreciation", "amortisation", "amortization"),
    "borrowings": ("borrowing", "term loan", "cash credit", "debenture"),
    "finance_cost": ("finance cost", "interest expense", "interest paid", "interest on borrowing"),
    "employee_cost": ("employee benefit", "salaries", "salary", "wages", "staff cost"),
}

# Keyword-fallback matching sums every ACCOUNT matching a keyword in absolute terms (so a
# debit/credit pair inside one group doesn't net to zero and read as "head absent" -- see
# gl_total_by_keywords). That is exactly wrong for a contra/return/provision account: it
# should REDUCE the component, not add to it in absolute terms. "accumulated"/"less:" was
# already excluded for gross_ppe/depreciation; the same problem exists for every component
# below and was silently inflating (or, for revenue, sometimes including balance-sheet
# items) the ratio's base.
_COMPONENT_EXCLUSIONS = {
    "gross_ppe": ("accumulated", "less:"),
    "depreciation": ("accumulated", "less:"),
    "revenue": ("deferred revenue", "unearned revenue", "sales return", "advance from customer"),
    "trade_receivables": ("provision for doubtful", "allowance for doubtful", "expected credit loss", "ecl", "impairment"),
    "cogs": ("purchase return", "purchase returns", "discount received"),
    "inventory": ("provision for", "obsolescence", "slow moving", "slow-moving"),
}

def _snapshot_component(out_dir) -> dict:
    """total_revenue/total_expenses from financial_snapshot_statistics.json -- classify_row-
    derived, so unlike every other component here it needs no keyword match at all and is
    available whenever build_financial_snapshot has run. Used both as its own ratio base
    (operating-expense ratio) and as the fallback denominator for creditor/inventory
    intensity when a TB has no distinct COGS/Purchases line (see the "cogs" component note)."""
    stats_path = Path(out_dir) / "financial_snapshot_statistics.json"
    if not stats_path.exists():
        return {"total_revenue": {"balance": None, "head": None, "basis": None},
                "total_expenses": {"balance": None, "head": None, "basis": None}}
    stats = safe_load_json(stats_path) or {}
    out = {}
    for key, label in (("total_revenue", "Total Revenue (financial snapshot)"),
                        ("total_expenses", "Total Expenses (financial snapshot)")):
        v = stats.get(key)
        out[key] = ({"balance": abs(float(v)), "head": label, "basis": "financial_snapshot"}
                    if v else {"balance": None, "head": None, "basis": None})
    return out

# Every ratio is a plain num/den pair, optionally with a `den_fallback` component tried when
# the primary denominator can't be located (documented and flagged on the output, never
# silent), and an optional `multiplier` for a "x365 days" restatement of the same components.
_RATIOS = [
    {"key": "debtor_intensity", "num": "trade_receivables", "den": "revenue",
     "formula": "Trade Receivables / Revenue from Operations",
     "audit_meaning": "High intensity is a recoverability and cut-off DIRECTION-SETTER, not proof of overstatement. Request ageing and subsequent receipts."},
    {"key": "debtor_days", "num": "trade_receivables", "den": "revenue", "multiplier": 365,
     "formula": "(Trade Receivables / Revenue from Operations) x 365",
     "audit_meaning": "Days Sales Outstanding, proxy basis (closing balance, not average). Rising days-outstanding period-on-period is the more useful signal than the absolute figure."},
    {"key": "creditor_intensity", "num": "trade_payables", "den": "cogs", "den_fallback": "total_expenses",
     "formula": "Trade Payables / Cost of Goods Sold (falls back to Total Expenses when this TB carries no distinct COGS/Purchases line -- e.g. a services entity)",
     "audit_meaning": "High intensity points to old or unsettled payables; low intensity raises the unrecorded-liability question. Request ageing and subsequent payments."},
    {"key": "creditor_days", "num": "trade_payables", "den": "cogs", "den_fallback": "total_expenses", "multiplier": 365,
     "formula": "(Trade Payables / Cost of Goods Sold [or Total Expenses]) x 365",
     "audit_meaning": "Days Payables Outstanding, proxy basis. A sudden lengthening can signal cash-flow strain or a completeness gap in recorded expenses -- corroborate either way before concluding."},
    {"key": "inventory_intensity", "num": "inventory", "den": "cogs", "den_fallback": "total_expenses",
     "formula": "Inventory / Cost of Goods Sold (falls back to Total Expenses when this TB carries no distinct COGS/Purchases line)",
     "audit_meaning": "High intensity suggests slow-moving stock or a valuation question. Request stock ageing and NRV assessment."},
    {"key": "inventory_days", "num": "inventory", "den": "cogs", "den_fallback": "total_expenses", "multiplier": 365,
     "formula": "(Inventory / Cost of Goods Sold [or Total Expenses]) x 365",
     "audit_meaning": "Days Inventory Outstanding, proxy basis. Request stock ageing where this has lengthened materially over the prior period."},
    {"key": "depreciation_proxy", "num": "depreciation", "den": "gross_ppe",
     "formula": "Depreciation Charge / Gross PPE",
     "audit_meaning": "A low proxy may reflect late additions, land, or fully-depreciated assets rather than under-charging. Request the FAR and depreciation working."},
    {"key": "finance_cost_ratio", "num": "finance_cost", "den": "borrowings",
     "formula": "Finance Cost / Closing Borrowings",
     "audit_meaning": "A proxy only: distorted by new loans, repayments and capitalisation. Request loan-wise interest working."},
    {"key": "employee_cost_ratio", "num": "employee_cost", "den": "revenue",
     "formula": "Employee Benefit Expense / Revenue from Operations",
     "audit_meaning": "A step change period-on-period warrants corroboration against headcount/payroll records -- this is a structure ratio, not a payroll-fraud test."},
    {"key": "operating_expense_ratio", "num": "total_expenses", "den": "revenue",
     "formula": "Total Expenses / Revenue from Operations",
     "audit_meaning": "A broad solvency/structure indicator (>100% means the entity ran at a loss this period from operations) -- corroborate any large period-on-period shift against the FSLI-level movement analysis."},
    {"key": "gross_margin_proxy", "num": "revenue_less_cogs", "den": "revenue",
     "formula": "(Revenue from Operations - Cost of Goods Sold) / Revenue from Operations",
     "audit_meaning": "Not computed with the Total Expenses fallback -- a margin proxy is only meaningful against a real COGS line. A margin swing period-on-period is a stronger risk signal here than the absolute level."},
]

# Wave 2 Fix 1a: plausibility bounds for the ratios a live EPIL run showed reading
# implausible values with no gate at all (Creditor Days 550,566.9; Gross Margin 99.99%;
# Depreciation 133.64% of Gross PPE) -- (low, high, rationale), same shape as risk.py's
# _EXPECTED_BANDS. A value outside its band is still reported (never suppressed), just
# flagged "outside_expectation" instead of presented as if unremarkable.
_RATIO_BANDS = {
    "creditor_intensity": (0.0, 3.0, "Trade payables rarely exceed 3x the COGS/expense base they settle against."),
    "creditor_days": (0.0, 365.0, "A payables cycle beyond a full year is implausible for an operating entity."),
    "inventory_intensity": (0.0, 3.0, "Inventory rarely exceeds 3x the COGS/expense base it turns against."),
    "inventory_days": (0.0, 365.0, "An inventory cycle beyond a full year is implausible for an operating entity."),
    "depreciation_proxy": (0.0, 0.50, "A depreciation charge above 50% of Gross PPE in one period is implausible without asset disposals/impairment."),
    "gross_margin_proxy": (-0.5, 0.95, "A margin outside -50%..95% signals a denominator/component mismatch more often than genuine economics."),
}

# Wave 2 Fix 1a: creditor/inventory intensity's documented COGS->Total Expenses fallback
# (den_fallback above) only fired when the primary COGS component was absent/zero -- a
# residual, non-representative "materials" figure (e.g. EPIL's ~Rs 4.19cr, dwarfed by
# total expenses) still counted as "present" and silently defeated the fallback. Treat a
# primary match this small, relative to Total Expenses, as not a real COGS line either.
_COGS_FALLBACK_MATERIALITY_FRACTION = 0.05

def _resolve__build_audit_ratio_pack(fsli_df, tb_df, keywords, exclude=()) -> dict:
    node = find_fsli_component(fsli_df, keywords)
    if node and node["balance"] > 0:
        return {"balance": node["balance"], "head": node["node_name"], "basis": "fsli_hierarchy"}
    gl = gl_total_by_keywords(tb_df, keywords, exclude=exclude)
    if gl["balance"] > 0:
        names = [a["gl_name"] for a in gl["accounts"][:2] if a["gl_name"]]
        head = ", ".join(names) if names else f"{gl['account_count']} ledger match(es)"
        if gl["account_count"] > 2:
            head += f" and {gl['account_count'] - len(names)} other account(s)"
        return {"balance": gl["balance"], "head": head, "basis": "gl_name_fallback"}
    return {"balance": None, "head": None, "basis": None}

@pipeline_tool("build_audit_ratio_pack", domain="fsli")
def build_audit_ratio_pack(
    canonical_tb_file: str,
    fsli_summary_file: str = None,
    materiality_file: str = None,
    output_dir: str = None,
    **kwargs,
) -> dict:
    """Compute the audit-analytical ratios sec 8.3 requires: debtor/creditor/inventory
    intensity AND their x365-day restatements, depreciation proxy, finance-cost ratio,
    employee-cost ratio, operating-expense ratio, and gross-margin proxy -- plus class
    concentration. Every ratio reports both components' head name, balance and match basis
    so a reviewer can trace it to mapped TB lines; a ratio with any component missing is null
    with the component named, never approximated.

    Creditor/inventory intensity fall back from COGS to Total Expenses (financial-snapshot
    totals, always available once build_financial_snapshot has run) when a TB carries no
    distinct COGS/Purchases line -- e.g. every services-sector entity -- rather than going
    null for an entire class of real trial balances; which basis was actually used is
    recorded on the ratio, never silently substituted. Writes audit_ratio_pack.json.

    Complements build_financial_ratios (liquidity/leverage/return) rather than
    replacing it."""
    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tb_path = Path(canonical_tb_file)
    if not tb_path.exists():
        raise PipelineFileError(str(tb_path), "Canonical TB (supply canonical_tb_file)")

    tb_df = load_canonical_tb(tb_path)
    if tb_df is None or tb_df.is_empty():
        return {
            "execution_status": "SUCCESS", "pipeline_status": "WARNING", "can_continue": True,
            "message": "Canonical TB is empty -- no ratios computed.",
            "artifacts": [], "errors": [],
        }
    tb_df = mapped_only(tb_df)

    warnings = []
    fsli_path = Path(fsli_summary_file) if fsli_summary_file else out_dir / "fsli_summary.parquet"
    fsli_df = safe_load_parquet(fsli_path) if fsli_path.exists() else None
    if fsli_df is None or fsli_df.is_empty():
        warnings.append(
            "fsli_summary.parquet not available -- components matched on ledger names, which "
            "cannot avoid double-counting a subtotal with its children. Ratios are marked "
            "gl_name_fallback and should be treated as indicative."
        )

    resolved = {
        key: _resolve__build_audit_ratio_pack(fsli_df, tb_df, kws, exclude=_COMPONENT_EXCLUSIONS.get(key, ()))
        for key, kws in _COMPONENTS.items()
    }
    # Wave 2 Fix 2a: revenue specifically defers to the single shared compute_total_revenue
    # helper (financial_snapshot's classify_row()-based figure, when available) instead of
    # this function's own independent keyword match above -- one of at least three
    # ratio/relationship engines that used to resolve revenue differently, which is exactly
    # why a live EPIL run showed revenue stated three different ways in the same report.
    _revenue = compute_total_revenue(out_dir, tb_df)
    if _revenue["balance"]:
        resolved["revenue"] = {
            "balance": _revenue["balance"],
            "head": "Total Revenue (financial snapshot)" if _revenue["basis"] == "financial_snapshot" else resolved["revenue"]["head"],
            "basis": _revenue["basis"],
        }
    resolved.update(_snapshot_component(out_dir))
    if resolved["revenue"]["balance"] and resolved["cogs"]["balance"] is not None:
        resolved["revenue_less_cogs"] = {
            "balance": resolved["revenue"]["balance"] - resolved["cogs"]["balance"],
            "head": f"{resolved['revenue']['head']} less {resolved['cogs']['head']}",
            "basis": "derived",
        }
    else:
        resolved["revenue_less_cogs"] = {"balance": None, "head": None, "basis": None}

    ratios = {}
    for spec in _RATIOS:
        key, num_key, den_key = spec["key"], spec["num"], spec["den"]
        formula, meaning = spec["formula"], spec["audit_meaning"]
        multiplier = spec.get("multiplier")
        num = resolved[num_key]

        den, den_key_used = resolved[den_key], den_key
        fallback_reason = None
        if spec.get("den_fallback"):
            fb = resolved[spec["den_fallback"]]
            total_expenses_bal = (resolved.get("total_expenses") or {}).get("balance")
            immaterial_partial_match = bool(
                den["basis"] == "gl_name_fallback" and den["balance"] and total_expenses_bal
                and den["balance"] < total_expenses_bal * _COGS_FALLBACK_MATERIALITY_FRACTION
            )
            if not den["balance"] and fb["balance"]:
                den, den_key_used, fallback_reason = fb, spec["den_fallback"], "absent"
            elif immaterial_partial_match and fb["balance"]:
                den, den_key_used, fallback_reason = fb, spec["den_fallback"], "immaterial_partial_match"

        missing = [k for k, c in ((num_key, num), (den_key_used, den)) if c["balance"] is None]

        # sec 6.2: no ratio on an absent, zero, or unmappable denominator.
        if missing or not den["balance"]:
            ratios[key] = {
                "value": None,
                "status": "not_computed",
                "formula": formula,
                "components_missing": missing or [den_key_used],
                "reason": (
                    f"Not computed -- {', '.join(missing or [den_key_used])} could not be located in "
                    "this trial balance. Approximating it would present an unvalidated figure "
                    "as a named metric."
                ),
                "audit_meaning": meaning,
            }
            continue

        value = (num["balance"] / den["balance"]) * (multiplier or 1)
        band = _RATIO_BANDS.get(key)
        if band:
            low, high, rationale = band
            status = "within_expectation" if low <= value <= high else "outside_expectation"
        else:
            status = "no_band_defined"
        ratios[key] = {
            "value": round(value, 6),
            "value_pct": f"{value:.2%}" if not multiplier else f"{value:,.1f} days",
            "status": status,
            "formula": formula,
            "components_missing": [],
            "numerator": {"component": num_key, "head": num["head"],
                          "balance": round(num["balance"], 2), "basis": num["basis"]},
            "denominator": {"component": den_key_used, "head": den["head"],
                            "balance": round(den["balance"], 2), "basis": den["basis"]},
            "used_fallback_denominator": den_key_used != den_key,
            "fallback_reason": fallback_reason,
            "provisional": num["basis"] == "gl_name_fallback" or den["basis"] == "gl_name_fallback",
            "audit_meaning": meaning,
        }
        if band:
            ratios[key]["expected_band"] = {"low": low, "high": high, "rationale": rationale}

    # Concentration: largest account as a share of its own class total (sec 8.4).
    concentration = []
    if "report_head" in tb_df.columns:
        for head in ("Assets", "Liabilities", "Equity", "Revenue", "Expenses"):
            block = tb_df.filter(tb_df["report_head"] == head)
            if block.is_empty():
                continue
            total = float(block["closing_balance"].abs().sum())
            if total <= 0:
                continue
            top = block.sort(block["closing_balance"].abs(), descending=True).row(0, named=True)
            top_bal = abs(float(top.get("closing_balance") or 0.0))
            concentration.append({
                "class": head,
                "class_total": round(total, 2),
                "largest_account": f"{top.get('gl_code')} - {top.get('gl_name')}",
                "largest_balance": round(top_bal, 2),
                "share_of_class": round(top_bal / total, 6),
                "share_pct": f"{top_bal / total:.2%}",
                "account_count": block.height,
            })

    computed = sum(1 for r in ratios.values() if r["value"] is not None)
    payload = {
        "methodology": (
            "Audit-analytical ratios per sec 8.3. Components are located in the FSLI "
            "hierarchy where available (shallowest matching node, to avoid double-counting a "
            "subtotal with its children) and fall back to ledger-name matching otherwise, "
            "with the basis recorded on every figure. A ratio with a missing or zero "
            "component is null with the component named -- sec 8.4 requires each numerator "
            "and denominator to be traceable to mapped TB lines, and an approximated ratio "
            "presented under a real metric's name is exactly what sec 1.2 forbids."
        ),
        "generated_at": datetime.datetime.now().isoformat(),
        "materiality_pack_version": load_pack("materiality")["_meta"]["version"],
        "summary": {
            "ratios_defined": len(_RATIOS),
            "ratios_computed": computed,
            "ratios_not_computable": len(_RATIOS) - computed,
            "classes_with_concentration": len(concentration),
        },
        "ratios": ratios,
        "concentration": concentration,
        "components_resolved": {
            k: {"head": v["head"], "balance": round(v["balance"], 2) if v["balance"] is not None else None,
                "basis": v["basis"]}
            for k, v in resolved.items()
        },
        "safe_limitation": SAFE_WORDING_DISCLAIMER,
    }

    out_path = out_dir / "audit_ratio_pack.json"
    with atomic_write(out_path) as tmp:
        Path(tmp).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "can_continue": True,
        "message": (
            f"Audit ratio pack: {computed}/{len(_RATIOS)} ratios computed, "
            f"{len(_RATIOS) - computed} not computable from this TB, "
            f"{len(concentration)} class concentration(s) measured."
        ),
        "artifacts": [str(out_path.resolve())],
        "errors": [],
        "warnings": warnings,
    }


_COMPONENT_KEYWORDS = {
    "current_assets": ("current asset",),
    "current_liabilities": ("current liab",),
    "inventory": ("inventory", "stock-in-trade", "stock in trade"),
    "trade_receivables": ("trade receivable", "sundry debtor", "debtors"),
    "trade_payables": ("trade payable", "sundry creditor", "creditors"),
    "interest_expense": ("interest expense", "finance cost", "interest on borrowing", "interest paid"),
    "total_debt": ("borrowing", "debt"),
    "cogs": ("cost of material", "cost of goods sold", "purchase of stock", "cost of sales"),
}

# Wave 2 Fix 1b: conventional textbook bounds for build_financial_ratios (liquidity/
# leverage/margin) -- separate from build_audit_ratio_pack's _RATIO_BANDS since these
# are two independent ratio engines by design (see Wave 2 plan's Fix 1 decision).
_RATIO_BANDS_SIMPLE = {
    "current": (0.2, 5.0, "A current ratio outside 0.2x-5x usually signals a component/classification issue rather than genuine liquidity."),
    "quick": (0.1, 4.0, "A quick ratio outside 0.1x-4x usually signals a component/classification issue."),
    "gross_margin": (-0.5, 0.95, "A margin outside -50%..95% more often signals a denominator/component mismatch than genuine economics."),
    "net_margin": (-1.0, 0.95, "A net margin outside -100%..95% more often signals a component mismatch than genuine economics."),
    "debt_equity": (0.0, 10.0, "A debt/equity ratio above 10x is implausible for most operating entities."),
}

def _safe_div(numerator, denominator):
    if numerator is None or denominator is None or denominator == 0:
        return None
    return round(numerator / denominator, 4)

@pipeline_tool("build_financial_ratios", domain="fsli")
def build_financial_ratios(canonical_tb_file: str = None, output_dir: str = None) -> dict:
    """Compute Current Ratio, Quick Ratio, Debt-Equity, Interest Coverage, Gross Margin,
    Net Margin, and ROCE from snapshot_drilldown.parquet + financial_snapshot_statistics.json
    (both already produced by build_financial_snapshot for this run). Writes
    financial_ratios.json. Requires build_financial_snapshot to have already run for this
    output_dir."""
    out_dir = resolve_output_dir(output_dir)
    drilldown_path = out_dir / "snapshot_drilldown.parquet"
    stats_path = out_dir / "financial_snapshot_statistics.json"

    if not drilldown_path.exists():
        raise PipelineFileError(str(drilldown_path), "Financial snapshot drilldown (run build_financial_snapshot first)")

    df = pl.read_parquet(drilldown_path)

    import json
    stats = {}
    if stats_path.exists():
        with open(stats_path, "r", encoding="utf-8") as f:
            stats = json.load(f)

    components = {name: find_fsli_component(df, keywords) for name, keywords in _COMPONENT_KEYWORDS.items()}
    missing = [name for name, val in components.items() if val is None]

    def bal(name):
        c = components.get(name)
        return c["balance"] if c else None

    # Wave 2 Fix 1b: total_revenue/total_expenses are stored credit-/debit-signed
    # respectively (debit-positive convention) -- total_equity is already negated to a
    # positive, real-world value at the point build_financial_snapshot writes it (Wave 1
    # Fix 4), but these two never were, so net_profit/ebit/revenue_to_assets below
    # silently summed a negative revenue with a positive expense instead of comparing
    # their magnitudes. Same abs() materiality.py:260 already applies correctly.
    total_revenue = abs(stats["total_revenue"]) if stats.get("total_revenue") is not None else None
    total_expenses = abs(stats["total_expenses"]) if stats.get("total_expenses") is not None else None
    total_equity = stats.get("total_equity")
    total_assets = stats.get("total_assets")

    net_profit = (total_revenue - total_expenses) if total_revenue is not None and total_expenses is not None else None
    ebit = (net_profit + bal("interest_expense")) if net_profit is not None and bal("interest_expense") is not None else None
    quick_assets = (bal("current_assets") - bal("inventory")) if bal("current_assets") is not None and bal("inventory") is not None else None
    capital_employed = (total_equity + bal("total_debt")) if total_equity is not None and bal("total_debt") is not None else None

    ratios = {
        "revenue_to_assets": {
            "value": _safe_div(total_revenue, total_assets),
            "formula": "Total Revenue / Total Assets",
            "components_missing": [c for c in ("total_revenue", "total_assets") if stats.get(c) is None],
        },
        "current": {
            "value": _safe_div(bal("current_assets"), bal("current_liabilities")),
            "formula": "Current Assets / Current Liabilities",
            "components_missing": [c for c in ("current_assets", "current_liabilities") if c in missing],
        },
        "quick": {
            "value": _safe_div(quick_assets, bal("current_liabilities")),
            "formula": "(Current Assets - Inventory) / Current Liabilities",
            "components_missing": [c for c in ("current_assets", "inventory", "current_liabilities") if c in missing],
        },
        "debt_equity": {
            "value": _safe_div(bal("total_debt"), total_equity),
            "formula": "Total Debt / Total Equity",
            "components_missing": (["total_debt"] if "total_debt" in missing else []) + (["total_equity"] if total_equity is None else []),
        },
        "interest_coverage": {
            "value": _safe_div(ebit, bal("interest_expense")),
            "formula": "EBIT / Interest Expense, EBIT = (Total Revenue - Total Expenses) + Interest Expense",
            "components_missing": (["interest_expense"] if "interest_expense" in missing else []) + (["total_revenue", "total_expenses"] if net_profit is None else []),
        },
        "gross_margin": {
            "value": _safe_div((total_revenue - bal("cogs")) if total_revenue is not None and bal("cogs") is not None else None, total_revenue),
            "formula": "(Total Revenue - Cost of Goods Sold) / Total Revenue",
            "components_missing": (["cogs"] if "cogs" in missing else []) + (["total_revenue"] if total_revenue is None else []),
        },
        "net_margin": {
            "value": _safe_div(net_profit, total_revenue),
            "formula": "(Total Revenue - Total Expenses) / Total Revenue",
            "components_missing": [c for c in ("total_revenue", "total_expenses") if stats.get(c) is None],
        },
        "roce": {
            "value": _safe_div(ebit, capital_employed),
            "formula": "EBIT / (Total Equity + Total Debt)",
            "components_missing": (["total_debt"] if "total_debt" in missing else []) + (["total_equity"] if total_equity is None else []) + (["interest_expense"] if "interest_expense" in missing else []),
        },
    }

    # Wave 2 Fix 1b: same plausibility-status pattern as build_audit_ratio_pack's
    # _RATIO_BANDS -- conventional textbook bounds, sufficient to catch e.g. a 99.99%
    # gross margin as implausible. Bounds are deliberately not defined for every ratio
    # here (revenue_to_assets, interest_coverage, roce) -- this is the simpler of the
    # two ratio engines and a band with no real evidence behind it would just be noise.
    for key, band in _RATIO_BANDS_SIMPLE.items():
        r = ratios[key]
        if r["value"] is None:
            r["status"] = "not_computed"
            continue
        low, high, rationale = band
        r["status"] = "within_expectation" if low <= r["value"] <= high else "outside_expectation"
        r["expected_band"] = {"low": low, "high": high, "rationale": rationale}
    for key, r in ratios.items():
        if "status" not in r:
            r["status"] = "not_computed" if r["value"] is None else "no_band_defined"

    out_path = out_dir / "financial_ratios.json"
    payload = {
        "methodology": "Ratio components identified by keyword match against the FSLI hierarchy "
        "(snapshot_drilldown.parquet), taking the shallowest matching node per component. Any "
        "ratio whose components could not be found is null with components_missing listed.",
        "components_found": {k: v for k, v in components.items() if v is not None},
        "components_not_found": missing,
        "ratios": ratios,
    }
    write_json_atomic(payload, out_path, indent=4, default=str)

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "artifacts": [str(out_path.resolve())],
        "message": f"Computed {len([r for r in ratios.values() if r['value'] is not None])}/{len(ratios)} ratios "
                    f"({len(missing)} FSLI component(s) not found: {', '.join(missing) if missing else 'none'}).",
        "ratios": ratios,
    }


EXEC_SNAPSHOT_MAX_LEVEL = 3

@pipeline_tool("build_financial_snapshot", domain="fsli")
def build_financial_snapshot(canonical_tb_file: str, metadata_file: str = None, output_dir: str = None) -> dict:
    """Enrich the FSLI rollup tree with percent-of-parent/percent-of-main-head and materiality rank."""
    start_time = time.time()
    errors = []
    warnings = []
    artifacts = []

    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    fsli_path = out_dir / "fsli_summary.parquet"
    if not fsli_path.exists() and canonical_tb_file:
        alt_path = Path(canonical_tb_file).parent / "fsli_summary.parquet"
        if alt_path.exists():
            fsli_path = alt_path
    if not fsli_path.exists():
        raise PipelineFileError(str(fsli_path), "FSLI Summary (run build_fsli_summary first)")

    df = pl.read_parquet(fsli_path)
    if df.is_empty():
        return {"execution_status": "FAILED", "errors": [{"type": "DataError", "error": "Empty FSLI Summary"}], "message": "FSLI Summary is empty."}

    req_cols = [
        "main_head", "hierarchy_level", "hierarchy_path", "node_name", "parent_node_name",
        "opening_balance", "debit", "credit", "closing_balance", "leaf_gl_count",
        "descendant_gl_count", "node_type",
    ]
    missing = [c for c in req_cols if c not in df.columns]
    if missing:
        return {"execution_status": "FAILED", "errors": [{"type": "SchemaError", "error": "MISSING_COLUMNS", "details": missing}], "message": f"FSLI Summary missing columns: {missing}"}

    if df.height - df["hierarchy_path"].n_unique() > 0:
        warnings.append({"type": "DataWarning", "message": "Duplicate hierarchy paths detected in FSLI Summary."})

    tree_df = df.with_columns(
        [
            pl.col("closing_balance").alias("balance"),
            pl.col("closing_balance").abs().alias("materiality"),
        ]
    )

    def _parent_path(path):
        parts = str(path).split(" > ")
        return " > ".join(parts[:-1]) if len(parts) > 1 else None

    tree_df = tree_df.with_columns(
        pl.col("hierarchy_path").map_elements(_parent_path, return_dtype=pl.Utf8).alias("parent_path")
    )

    # Percent of immediate parent's balance -- a self-join on hierarchy_path
    # is cleaner than pandas' set_index().to_dict() closure lookup. Dedup
    # keep="last" matches dict-overwrite semantics exactly (paths should
    # already be unique per node, but this stays safe if not).
    parent_lookup = (
        tree_df.select([pl.col("hierarchy_path").alias("_pkey"), pl.col("materiality").alias("_pmat")])
        .unique(subset=["_pkey"], keep="last")
    )
    tree_df = tree_df.join(parent_lookup, left_on="parent_path", right_on="_pkey", how="left")
    tree_df = tree_df.with_columns(
        pl.when(pl.col("_pmat").is_null())
        .then(pl.when(pl.col("materiality") > 0).then(100.0).otherwise(0.0))
        .when(pl.col("_pmat") == 0)
        .then(0.0)
        .otherwise(pl.col("materiality") / pl.col("_pmat") * 100.0)
        .alias("percent_of_parent")
    ).drop("_pmat")

    # Percent of the top-level main_head total (root of this node's tree)
    main_head_lookup = (
        tree_df.filter(pl.col("hierarchy_level") == 1)
        .select([pl.col("main_head").alias("_mhkey"), pl.col("materiality").alias("_mhmat")])
        .unique(subset=["_mhkey"], keep="last")
    )
    tree_df = tree_df.join(main_head_lookup, left_on="main_head", right_on="_mhkey", how="left")
    tree_df = tree_df.with_columns(
        pl.when(pl.col("_mhmat").is_null() | (pl.col("_mhmat") == 0))
        .then(0.0)
        .otherwise(pl.col("materiality") / pl.col("_mhmat") * 100.0)
        .alias("percent_of_main_head")
    ).drop("_mhmat")

    # Rank each node's materiality within its own main_head
    tree_df = tree_df.with_columns(
        pl.col("materiality").rank(method="dense", descending=True).over("main_head").alias("materiality_rank")
    )

    tree_df = tree_df.sort(["main_head", "hierarchy_path"])

    drilldown_path = out_dir / "snapshot_drilldown.parquet"
    write_parquet_atomic(tree_df, drilldown_path)
    artifacts.append(str(drilldown_path.resolve()))

    drilldown_json = out_dir / "snapshot_drilldown.json"
    write_json_atomic(tree_df.to_dicts(), drilldown_json, indent=4, default=str)
    artifacts.append(str(drilldown_json.resolve()))

    # Executive Financial Snapshot
    exec_df = tree_df.filter(pl.col("hierarchy_level") <= EXEC_SNAPSHOT_MAX_LEVEL)

    exec_cols = [
        "main_head", "hierarchy_level", "hierarchy_path", "display_path", "parent_path",
        "parent_node_name", "node_name", "opening_balance", "debit", "credit", "closing_balance",
        "balance", "leaf_gl_count", "descendant_gl_count", "percent_of_parent",
        "percent_of_main_head", "materiality_rank",
    ]
    exec_cols = [c for c in exec_cols if c in exec_df.columns]
    exec_df = exec_df.select(exec_cols)

    exec_path = out_dir / "financial_snapshot.parquet"
    write_parquet_atomic(exec_df, exec_path)
    artifacts.append(str(exec_path.resolve()))

    exec_json = out_dir / "financial_snapshot.json"
    write_json_atomic(exec_df.to_dicts(), exec_json, indent=4, default=str)
    artifacts.append(str(exec_json.resolve()))

    # Snapshot Statistics. main_head at hierarchy_level 1 is Schedule III style ("Current
    # assets", "Non-current liabilities", ...), not a literal "Assets"/"Liabilities" label,
    # so classify_row() (the same Assets/Liabilities/Equity/Revenue/Expenses normalization
    # every report builder already uses) does the head grouping instead of a literal-string
    # lookup against main_head.
    totals = {}
    for row in tree_df.filter(pl.col("hierarchy_level") == 1).to_dicts():
        head, _, _ = classify_row(row)
        totals[head] = totals.get(head, 0.0) + (row.get("balance") or 0.0)

    level1 = tree_df.filter(pl.col("hierarchy_level") == 1).sort("materiality", descending=True)
    largest_main_head = str(level1.row(0, named=True)["main_head"]) if not level1.is_empty() else "N/A"

    leaves = tree_df.filter(pl.col("node_type") == "LEAF").sort("materiality", descending=True)
    largest_leaf_name = str(leaves.row(0, named=True)["node_name"]) if not leaves.is_empty() else "N/A"

    stats = {
        "snapshot_nodes": len(exec_df),
        "drilldown_nodes": len(tree_df),
        "total_assets": float(totals.get("Assets", 0.0)),
        "total_liabilities": float(totals.get("Liabilities", 0.0)),
        # TB-R17 correction: closing_balance is debit-positive, so Equity (a credit-normal
        # head) carries a NEGATIVE raw sign -- risk.py's going-concern screen already
        # negates it (see its net_worth = -equity_raw, with the same comment). This value
        # was left un-negated here, so build_financial_ratios' Debt/Equity and ROCE (which
        # divide/add against it directly, against an already-positive total_debt from
        # find_fsli_component's abs()) were computing with an inverted sign.
        "total_equity": -float(totals.get("Equity", 0.0)),
        # Raw closing-balance sign (matches Assets/Liabilities/Revenue/Expenses above, none of
        # which are negated) -- for face-value display tables (Section 4's reconstruction) that
        # need Assets = Liabilities + Equity to tie out. "total_equity" above stays ratio/
        # reasoning-oriented (positive-equity convention, TB-R17) -- do not merge these two.
        "total_equity_natural_sign": float(totals.get("Equity", 0.0)),
        "total_revenue": float(totals.get("Revenue", 0.0)),
        "total_expenses": float(totals.get("Expenses", 0.0)),
        "maximum_depth": int(tree_df["hierarchy_level"].max()) if not tree_df.is_empty() else 0,
        "largest_fs_head": largest_main_head,
        "largest_line_item": largest_leaf_name,
        "processing_time_seconds": round(time.time() - start_time, 2),
    }

    stats_json = out_dir / "financial_snapshot_statistics.json"
    write_json_atomic(stats, stats_json, indent=4)
    artifacts.append(str(stats_json.resolve()))

    status = "SUCCESS" if not errors else "FAILED"
    pipeline_status = "WARNING" if warnings and not errors else status

    return {
        "execution_status": status,
        "pipeline_status": pipeline_status,
        "message": f"Hierarchy-driven Snapshot built. Generated {len(exec_df)} executive nodes and {len(tree_df)} drill-down nodes.",
        "artifacts": artifacts,
        "errors": errors,
        "warnings": warnings,
    }


BALANCE_EQUATION_TOLERANCE = 1.0

_UNMAPPED = "Unmapped"

def _blank_to_default(df: pl.DataFrame, columns, default: str) -> pl.DataFrame:
    """Coerce text columns to string, replacing null/blank/'nan' with *default*."""
    exprs = []
    for col in columns:
        c = pl.col(col).cast(pl.Utf8).fill_null(default)
        c = pl.when(c.str.strip_chars().str.to_lowercase().is_in(["", "nan", "none"])).then(pl.lit(default)).otherwise(c)
        exprs.append(c.alias(col))
    return df.with_columns(exprs)

def _make_node_id(value):
    """Deterministic short hash id for a hierarchy path; None for empty/unmapped."""
    if value is None or value == "" or value == _UNMAPPED:
        return None
    # Deterministic grouping id, not a security hash -- usedforsecurity=False
    # documents that for bandit/CWE-327 scans (reviewed 2026-09).
    return hashlib.md5(str(value).encode("utf-8"), usedforsecurity=False).hexdigest()[:8]

def _build_fsli_hierarchy_nodes(rows, row_parts, row_paths):
    """Steps 2-4 of the FSLI aggregation: construct every ancestor node from each row's
    grouping path, wire up the parent/children adjacency list, then aggregate each row's
    leaf GL balances onto its own full-path node (not yet rolled up to ancestors -- see
    _rollup_fsli_hierarchy for that). Returns (nodes, roots)."""
    # Step 2: construct every ancestor node from each row's grouping path.
    nodes = {}
    for row, parts in zip(rows, row_parts):
        bs_pl = row["bs_pl"]
        for i in range(1, len(parts) + 1):
            sub_parts = parts[:i]
            sub_path = " > ".join(sub_parts)
            if sub_path in nodes:
                continue
            main_head = sub_parts[0]
            sub_head_1 = sub_parts[1] if i > 1 else None
            sub_head_2 = sub_parts[2] if i > 2 else None
            parent_path = " > ".join(sub_parts[:-1]) if i > 1 else None
            nodes[sub_path] = {
                "node_id": _make_node_id(sub_path),
                "parent_id": _make_node_id(parent_path) if parent_path else None,
                "hierarchy_level": i,
                "hierarchy_path": sub_path,
                "display_path": hierarchy_path({
                    "bs_pl": bs_pl, "main_head": main_head, "sub_head_1": sub_head_1, "sub_head_2": sub_head_2,
                }),
                "bs_pl": bs_pl,
                "main_head": main_head,
                "sub_head_1": sub_head_1,
                "sub_head_2": sub_head_2,
                "node_name": sub_parts[-1],
                "parent_node_name": sub_parts[-2] if i > 1 else None,
                "opening_balance": 0.0,
                "debit": 0.0,
                "credit": 0.0,
                "closing_balance": 0.0,
                "leaf_gl_count": 0,
                "descendant_gl_count": 0,
                "children": [],
            }

    # Step 3: adjacency list + roots
    roots = []
    for path in nodes:
        parts = path.split(" > ")
        if len(parts) > 1:
            parent_path = " > ".join(parts[:-1])
            if parent_path in nodes:
                nodes[parent_path]["children"].append(path)
        else:
            roots.append(path)

    # Step 4: aggregate leaf GLs onto their full grouping-path node
    for row, path in zip(rows, row_paths):
        n = nodes[path]
        n["opening_balance"] += float(row["opening_balance"])
        n["debit"] += float(row["debit"])
        n["credit"] += float(row["credit"])
        n["closing_balance"] += float(row["closing_balance"])
        n["leaf_gl_count"] += 1

    return nodes, roots


def _rollup_and_format_fsli_nodes(nodes, roots, df, warnings):
    """Step 5 (recursive parent rollup, post-order) then Step 6 (format output rows and
    run the balance-equation/root-vs-TB validations). Mutates `warnings` in place on a
    balance-equation mismatch. Returns (out_rows, validation_passed, total_root_closing)."""
    def rollup(path):
        node = nodes[path]
        node["descendant_gl_count"] = node["leaf_gl_count"]
        for child_path in node["children"]:
            child_node = rollup(child_path)
            node["opening_balance"] += child_node["opening_balance"]
            node["debit"] += child_node["debit"]
            node["credit"] += child_node["credit"]
            node["closing_balance"] += child_node["closing_balance"]
            node["descendant_gl_count"] += child_node["descendant_gl_count"]
        return node

    for root in roots:
        rollup(root)

    out_rows = []
    validation_passed = True
    total_root_closing = 0.0

    for path, n in nodes.items():
        n["node_type"] = "LEAF" if not n["children"] else "ROLLUP"

        diff = abs((n["opening_balance"] + n["debit"] - n["credit"]) - n["closing_balance"])
        if diff > BALANCE_EQUATION_TOLERANCE:
            warnings.append({"type": "ValidationWarning", "message": f"Balance equation failed for {path} (diff: {diff})"})
            validation_passed = False

        if n["hierarchy_level"] == 1:
            total_root_closing += n["closing_balance"]

        out_rows.append({k: v for k, v in n.items() if k != "children"})

    return out_rows, validation_passed, total_root_closing


@pipeline_tool("build_fsli_summary", domain="fsli")
def build_fsli_summary(canonical_tb_file: str, metadata_file: str = None, output_dir: str = None) -> dict:
    """Aggregate canonical TB GL rows into a main_head/sub_head_1/sub_head_2 rollup tree."""
    start_time = time.time()
    errors = []
    warnings = []
    artifacts = []

    tb_path = Path(canonical_tb_file) if canonical_tb_file else None
    if tb_path is None or not tb_path.exists():
        raise PipelineFileError(str(tb_path), "Canonical TB (supply canonical_tb_file from load_tb_from_db/ingest_tb_to_live)")

    df = pl.read_parquet(tb_path)
    if df.is_empty():
        return {
            "execution_status": "FAILED",
            "errors": [{"type": "DataError", "error": "Empty Canonical TB"}],
            "message": "Canonical TB is empty.",
        }

    # Rows with no resolvable GL code (e.g. a workbook's trailing synthetic grand-total/
    # tie-out row) are retained in canonical_tb.parquet for audit traceability but must
    # never be aggregated into FSLI totals -- they don't represent a real ledger account.
    no_gl_code_mask = pl.col("gl_code").is_null() | (pl.col("gl_code").cast(pl.Utf8).str.strip_chars() == "")
    excluded_no_gl_code_rows = df.filter(no_gl_code_mask).height
    if excluded_no_gl_code_rows:
        df = df.filter(~no_gl_code_mask)

    # Analysis-stage rollup -- unmapped/unmatched rows never enter FSLI totals (they can't be
    # placed in the hierarchy correctly anyway); they stay visible in Section 2/5's validation
    # tables via the unfiltered canonical_tb, just not rolled up here.
    df = mapped_only(df)

    required_cols = [
        "gl_code", "gl_name", "opening_balance", "debit", "credit", "closing_balance",
        "bs_pl", "main_head", "sub_head_1", "sub_head_2",
    ]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        return {
            "execution_status": "FAILED",
            "errors": [{"type": "SchemaError", "error": "MISSING_COLUMNS", "details": missing}],
            "message": f"Missing required columns: {missing}",
        }

    df = df.with_columns([pl.col(c).cast(pl.Float64, strict=False).fill_null(0.0) for c in CANONICAL_TB_NUMERIC_COLUMNS])

    df = _blank_to_default(df, ["bs_pl", "main_head", "sub_head_1", "sub_head_2"], _UNMAPPED)

    # Step 1: grouping path per row. main_head is always level 1; sub_head_1/sub_head_2
    # extend it only while actually populated (an "Unmapped" segment truncates the path).
    def grouping_parts(row):
        parts = [row["main_head"]]
        if row["sub_head_1"] != _UNMAPPED:
            parts.append(row["sub_head_1"])
            if row["sub_head_2"] != _UNMAPPED:
                parts.append(row["sub_head_2"])
        return parts

    # Fan-out tree aggregation (one GL row contributes to every ancestor level of its
    # hierarchy path simultaneously) has no vectorized groupby/join equivalent in either
    # pandas or polars -- stays a plain Python loop, materialized once here and reused
    # for both the node-construction pass (Step 2) and the leaf-aggregation pass (Step 4).
    rows = list(df.iter_rows(named=True))
    row_parts = [grouping_parts(r) for r in rows]
    row_paths = [" > ".join(p) for p in row_parts]

    nodes, roots = _build_fsli_hierarchy_nodes(rows, row_parts, row_paths)
    total_posting_gls = len(df)

    total_tb_closing = float(df["closing_balance"].sum())
    out_rows, validation_passed, total_root_closing = _rollup_and_format_fsli_nodes(nodes, roots, df, warnings)

    if abs(total_root_closing - total_tb_closing) > BALANCE_EQUATION_TOLERANCE:
        errors.append({
            "type": "DataError",
            "error": "Root Closing Balance mismatch",
            "message": f"Root Σ={total_root_closing} != TB Σ={total_tb_closing}",
        })
        validation_passed = False

    summary_df = pl.DataFrame(out_rows)
    summary_df = summary_df.sort(["hierarchy_level", "hierarchy_path"], descending=[False, False])

    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    summary_parquet = out_dir / "fsli_summary.parquet"
    write_parquet_atomic(summary_df, summary_parquet)
    artifacts.append(str(summary_parquet.resolve()))

    stats = {
        "total_nodes": len(nodes),
        "leaf_nodes": int(sum(1 for n in nodes.values() if not n["children"])),
        "root_nodes": len(roots),
        "posting_gl_accounts": total_posting_gls,
        "excluded_no_gl_code_rows": excluded_no_gl_code_rows,
        "max_hierarchy_depth": int(summary_df["hierarchy_level"].max()) if not summary_df.is_empty() else 0,
        "hierarchy_depth": int(summary_df["hierarchy_level"].max()) if not summary_df.is_empty() else 0,
        "aggregation_duration_ms": int((time.time() - start_time) * 1000),
        "validation_passed": validation_passed,
    }

    summary_json = out_dir / "fsli_summary.json"
    write_json_atomic(stats, summary_json, indent=4)
    artifacts.append(str(summary_json.resolve()))

    # `warnings` can carry one "Balance equation failed" entry per hierarchy node
    # (hundreds on a real TB) -- write the full list to disk and return only a
    # small preview + count inline to avoid blowing up the caller's context.
    if warnings:
        warnings_json = out_dir / "fsli_validation_warnings.json"
        write_json_atomic(warnings, warnings_json, indent=4)
        artifacts.append(str(warnings_json.resolve()))
    warnings_preview = warnings[:5]

    status = "SUCCESS" if not errors else "FAILED"
    pipeline_status = "WARNING" if warnings and not errors else status

    return {
        "execution_status": status,
        "pipeline_status": pipeline_status,
        "message": (
            "Canonical Financial Hierarchy Aggregation Engine completed successfully."
            + (f" {len(warnings)} balance-equation warning(s) — see fsli_validation_warnings.json." if warnings else "")
        ),
        "artifacts": artifacts,
        "errors": errors,
        "warnings": warnings_preview,
        "warnings_count": len(warnings),
    }


_PL_FLAVORED_KEYWORDS = (
    "survey", "exploration", "amortisation", "amortization", "depletion",
    "impairment loss", "written off", "expense", "cost of",
)

def _normalized_singular(label: str) -> str:
    return str(label or "").strip().lower().rstrip("s")

@pipeline_tool("build_mapping_quality", domain="fsli")
def build_mapping_quality(canonical_tb_file: str, output_dir: str = None, **kwargs) -> dict:
    """Flags (a) near-duplicate main_head labels (e.g. "Expense" vs "Expenses" differing
    only by a trailing 's') and (b) FSLI<->account-type implausibility (a P&L-flavored
    account name classified under Equity/Balance Sheet). Writes mapping_quality.json."""
    errors = []
    artifacts = []

    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tb_path = Path(canonical_tb_file)
    if not tb_path.exists():
        raise PipelineFileError(str(tb_path), "canonical_tb.parquet")

    df = load_canonical_tb(tb_path)
    if df is None or df.is_empty():
        errors.append({"type": "EmptyDataError", "message": "canonical_tb.parquet loaded but is empty."})
        return {"execution_status": "FAILED", "errors": errors, "message": "Canonical TB is empty."}

    # (a) Near-duplicate main_head labels.
    raw_heads = [str(h) for h in df["main_head"].unique().to_list() if h is not None and str(h).strip()]
    by_norm: dict = {}
    for h in raw_heads:
        by_norm.setdefault(_normalized_singular(h), []).append(h)
    duplicate_label_groups = [
        {"normalized": norm, "labels": sorted(labels)}
        for norm, labels in by_norm.items()
        if len(labels) > 1
    ]

    # (b) FSLI<->account-type implausibility: a P&L-flavored account name classified
    # under Equity (per the classify_row-derived report_head column).
    gl_name_lower = pl.col("gl_name").cast(pl.Utf8).str.to_lowercase().fill_null("")
    pl_flavor_mask = pl.lit(False)
    for kw in _PL_FLAVORED_KEYWORDS:
        pl_flavor_mask = pl_flavor_mask | gl_name_lower.str.contains(kw, literal=True)
    implausible_df = df.filter((pl.col("report_head") == "Equity") & pl_flavor_mask)
    implausible_rows = [
        {
            "gl_code": r.get("gl_code", ""),
            "gl_name": r.get("gl_name", ""),
            "main_head": r.get("main_head", ""),
            "closing_balance": float(r.get("closing_balance", 0.0)),
            "concern": "P&L-flavored account name classified under Equity/Balance Sheet.",
        }
        for r in implausible_df.iter_rows(named=True)
    ]

    flags = []
    for grp in duplicate_label_groups:
        flags.append({
            "type": "duplicate_main_head_labels",
            "severity": "Medium",
            "detail": (
                f"Main heads {', '.join(repr(l) for l in grp['labels'])} normalize to the same "
                f"label and likely represent an inconsistent grouping-workbook taxonomy rather "
                f"than genuinely distinct FS categories."
            ),
            "labels": grp["labels"],
        })
    for row in implausible_rows:
        flags.append({
            "type": "implausible_fsli_account_type",
            "severity": "Medium",
            "detail": (
                f"{row['gl_name']} ({row['gl_code']}) is grouped under main_head "
                f"'{row['main_head']}' (Equity) but its name suggests a P&L/estimation-cost "
                f"nature -- confirm this grouping is intentional."
            ),
            "gl_code": row["gl_code"],
            "gl_name": row["gl_name"],
            "main_head": row["main_head"],
            "closing_balance": row["closing_balance"],
        })

    output_data = {
        "methodology": (
            "Deterministic screen: (a) near-duplicate main_head labels via normalized-singular "
            "comparison, (b) P&L-flavored account names classified under Equity. Surfaces "
            "inconsistencies in the client-supplied grouping workbook -- does not correct them; "
            "the grouping is trusted as supplied everywhere else in this pipeline."
        ),
        "summary": {
            "duplicate_label_groups": len(duplicate_label_groups),
            "implausible_fsli_accounts": len(implausible_rows),
            "total_flags": len(flags),
        },
        "flags": flags,
        "generated_at": datetime.datetime.now().isoformat(),
    }

    out_file = out_dir / "mapping_quality.json"
    write_json_atomic(output_data, out_file, indent=4)
    artifacts.append(str(out_file.resolve()))

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "message": f"Mapping-quality screen: {len(flags)} flag(s) ({len(duplicate_label_groups)} duplicate-label group(s), {len(implausible_rows)} implausible mapping(s)).",
        "artifacts": artifacts,
        "errors": errors,
        "data": output_data,
    }




