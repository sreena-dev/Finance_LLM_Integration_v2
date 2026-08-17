from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Callable

DAYS_IN_YEAR = 365          # sheet allows 365 or 360; 365 chosen (configurable)

# Reserved key `compute()` uses to hand a `trace_fn` the set of optional inputs it
# defaulted to zero. Not a canonical input; never appears in `Computation.inputs`.
_DEFAULTED = "__defaulted__"


# --------------------------------------------------------------------------- source trace
@dataclass(frozen=True)
class InputTrace:
    """Where ONE canonical input comes from — the sheet's Financial Statement /
    Major Head / Sub-Head / Line Item columns, kept once per key (not per ratio)."""
    key: str
    statement: str                  # Balance Sheet | Statement of Profit & Loss | Derived
                                     # | Notes to Accounts | Statement of Changes in Equity | External
    major_head: str = ""
    sub_head: str = ""
    line_item: str = ""             # the label/note as it appears on the filing

    def to_dict(self) -> dict:
        return asdict(self)


# One row per canonical key used by a CATALOG spec below, PLUS a clearly-marked tail
# block of P&L lines that `binding.py` binds for its own tie-out identities but that no
# ratio consumes. Sourced from the sheet's F(Schedule III Component)/G(Financial
# Statement)/H(Major Head)/I(Sub-Head)/J(Line Item) columns.
# `inventories` is the one key the sheet uses in a formula (Quick Ratio) without giving
# it its own trace row — sourced here consistent with the sheet's own pattern for
# sibling current-asset lines, not copied verbatim from a row that doesn't exist.
# (`marketable_securities` DOES have one: the sheet's "Current Investments" component
# row under Current Ratio, Balance Sheet -> Assets -> Current Assets.)
INPUT_TRACE: dict[str, InputTrace] = {
    # -- Balance Sheet : Assets --
    "current_assets": InputTrace("current_assets", "Balance Sheet", "Assets", "Current Assets (sub-total)"),
    "inventories": InputTrace("inventories", "Balance Sheet", "Assets", "Current Assets", "Inventories"),
    "trade_receivables": InputTrace("trade_receivables", "Balance Sheet", "Assets", "Current Assets", "Trade Receivables (net of allowance)"),
    "cash_and_cash_equivalents": InputTrace("cash_and_cash_equivalents", "Balance Sheet", "Assets", "Current Assets", "Cash and Cash Equivalents"),
    "marketable_securities": InputTrace("marketable_securities", "Balance Sheet", "Assets", "Current Assets", "Current Investments"),
    "prepaid_expenses": InputTrace("prepaid_expenses", "Balance Sheet", "Assets", "Current Assets", "Other Current Assets (Prepaid Expenses)"),
    "net_fixed_assets": InputTrace("net_fixed_assets", "Balance Sheet", "Assets", "Non-Current Assets", "Property, Plant and Equipment (Net Block per PPE Note/Schedule)"),
    "total_assets": InputTrace("total_assets", "Balance Sheet", "Assets (Total of Non-Current Assets + Current Assets)"),
    # -- Balance Sheet : Equity & Liabilities --
    "current_liabilities": InputTrace("current_liabilities", "Balance Sheet", "Equity & Liabilities", "Current Liabilities (sub-total)"),
    "short_term_borrowings": InputTrace("short_term_borrowings", "Balance Sheet", "Equity & Liabilities", "Current Liabilities", "Short-term Borrowings"),
    "long_term_borrowings": InputTrace("long_term_borrowings", "Balance Sheet", "Equity & Liabilities", "Non-Current Liabilities", "Long-term Borrowings"),
    "equity_share_capital": InputTrace("equity_share_capital", "Balance Sheet", "Equity & Liabilities", "Shareholders' Funds/Equity", "Equity Share Capital (Share Capital Note)"),
    "reserves_and_surplus": InputTrace("reserves_and_surplus", "Balance Sheet", "Equity & Liabilities", "Shareholders' Funds/Equity", "Other Equity (Ind AS) / Reserves and Surplus (AS)"),
    "total_equity": InputTrace("total_equity", "Balance Sheet", "Equity & Liabilities", "Shareholders' Funds/Equity (Equity Share Capital + Other Equity/Reserves & Surplus)"),
    "preference_share_capital": InputTrace("preference_share_capital", "Balance Sheet", "Equity & Liabilities", "Shareholders' Funds/Equity", "Share Capital Note (Preference) — or Borrowings if compulsorily redeemable (Ind AS)"),
    "debentures": InputTrace("debentures", "Balance Sheet", "Equity & Liabilities", "Non-Current/Current Liabilities", "Borrowings Note (Debentures)"),
    "other_borrowed_funds": InputTrace("other_borrowed_funds", "Balance Sheet", "Equity & Liabilities", "Borrowings Note"),
    "accumulated_losses": InputTrace("accumulated_losses", "Balance Sheet", "Equity & Liabilities", "Shareholders' Funds/Equity", "Deficit in Other Equity (if any)"),
    # -- Statement of Profit & Loss --
    "revenue": InputTrace("revenue", "Statement of Profit & Loss", "I. Revenue from Operations"),
    "cost_of_materials_consumed": InputTrace("cost_of_materials_consumed", "Statement of Profit & Loss", "Expenses", "Cost of Materials Consumed"),
    "purchases_of_stock_in_trade": InputTrace("purchases_of_stock_in_trade", "Statement of Profit & Loss", "Expenses", "Purchases of Stock-in-Trade"),
    "changes_in_inventories": InputTrace("changes_in_inventories", "Statement of Profit & Loss", "Expenses", "Changes in Inventories of Finished Goods/WIP/Stock-in-Trade"),
    "sga_expenses": InputTrace("sga_expenses", "Statement of Profit & Loss", "Expenses", "Other Expenses (Selling, Administrative & General)"),
    "depreciation": InputTrace("depreciation", "Statement of Profit & Loss", "Expenses", "Depreciation and Amortisation Expense"),
    "finance_costs": InputTrace("finance_costs", "Statement of Profit & Loss", "Expenses", "Finance Costs"),
    "pbt": InputTrace("pbt", "Statement of Profit & Loss", "Profit before Tax (V+VI)"),
    # ^ not a ratio input itself; the sheet's Note 5 names it as the source of EBIT,
    #   which `ratio_pipeline` derives as pbt + finance_costs (see the `ebit` row below).
    "pat": InputTrace("pat", "Statement of Profit & Loss", "Profit for the Period (after tax)"),
    "preference_dividend": InputTrace("preference_dividend", "Notes to Accounts", "Dividend Note (Preference)"),
    "non_cash_adjustments": InputTrace("non_cash_adjustments", "Statement of Profit & Loss / Cash Flow Statement", "Non-cash items (e.g. loss on sale of PPE)"),
    "instalments": InputTrace("instalments", "Notes to Accounts / Cash Flow Statement", "Borrowings Note (maturity schedule) or Financing Activities"),
    "loan_repayment": InputTrace("loan_repayment", "Notes to Accounts / Cash Flow Statement", "Borrowings Note (maturity schedule) or Financing Activities"),
    # -- Not individually traceable to one Schedule III line (sheet's own flag) --
    "credit_sales": InputTrace("credit_sales", "Statement of Profit & Loss",
                               "I. Revenue from Operations (credit portion — not separately disclosed; estimate/Notes)"),
    "credit_purchases": InputTrace("credit_purchases", "Statement of Profit & Loss",
                                   "Cost of Materials Consumed/Purchases Note (credit portion — not separately disclosed; estimate/Notes)"),
    "return_amount": InputTrace("return_amount", "Context-dependent", "Not a single traceable line — specify per analysis"),
    "investment": InputTrace("investment", "Context-dependent", "Total Assets / Net Assets / Capital Employed / Equity, as applicable"),
    # -- Notes to Accounts / Statement of Changes in Equity --
    "num_equity_shares": InputTrace("num_equity_shares", "Notes to Accounts", "Share Capital Note"),
    "total_dividend": InputTrace("total_dividend", "Statement of Changes in Equity / Notes on Other Equity", "Dividend distributed"),
    # -- Derived (computed from other bound figures, not a face line) --
    "ebit": InputTrace("ebit", "Derived", "Profit before Tax + Finance Costs"),
    "avg_total_assets": InputTrace("avg_total_assets", "Derived", "Average (Opening + Closing) Total Assets"),
    "avg_inventory": InputTrace("avg_inventory", "Derived", "Average (Opening + Closing) Inventories"),
    "avg_receivables": InputTrace("avg_receivables", "Derived", "Average (Opening + Closing) Trade Receivables"),
    "avg_payables": InputTrace("avg_payables", "Derived", "Average (Opening + Closing) Trade Payables"),
    "avg_equity": InputTrace("avg_equity", "Derived", "Average (Opening + Closing) Total Equity"),
    # -- Cash Flow Statement (Ind AS 7) — FDR Appendix G ratios 14 (Accruals), 15 (FCF) --
    "ocf": InputTrace("ocf", "Cash Flow Statement", "Operating Activities",
                      "Net cash generated by / (used in) operating activities"),
    "icf": InputTrace("icf", "Cash Flow Statement", "Investing Activities",
                      "Net cash generated by / (used in) investing activities"),
    "fcf_financing": InputTrace("fcf_financing", "Cash Flow Statement", "Financing Activities",
                                "Net cash generated by / (used in) financing activities"),
    "net_change_in_cash": InputTrace("net_change_in_cash", "Cash Flow Statement", "",
                                     "Net increase / (decrease) in cash and cash equivalents"),
    "capex_ppe": InputTrace("capex_ppe", "Cash Flow Statement", "Investing Activities",
                            "Payments for Property, Plant and Equipment"),
    "capex_intangibles": InputTrace("capex_intangibles", "Cash Flow Statement", "Investing Activities",
                                    "Purchase of Intangible Assets"),
    "capex_exploration": InputTrace("capex_exploration", "Cash Flow Statement", "Investing Activities",
                                    "Exploratory / development drilling or CWIP additions (sector-specific)"),
    "government_grant_cf": InputTrace("government_grant_cf", "Cash Flow Statement", "Operating Activities",
                                      "Government grant amortisation / receipt (ONE of the scattered "
                                      "components — see govt_support_dependency)"),
    # -- Balance Sheet : debt-definition components (FDR Appendix G ratio 3) --
    "lease_liabilities_nc": InputTrace("lease_liabilities_nc", "Balance Sheet", "Equity & Liabilities",
                                       "Non-Current Liabilities", "Lease Liabilities (Ind AS 116)"),
    "lease_liabilities_cl": InputTrace("lease_liabilities_cl", "Balance Sheet", "Equity & Liabilities",
                                       "Current Liabilities", "Lease Liabilities (Ind AS 116)"),
    "current_maturities_ltd": InputTrace("current_maturities_ltd", "Balance Sheet", "Equity & Liabilities",
                                         "Current Liabilities", "Current Maturities of Long-term Debt"),
    # -- External (needs a quoted market price; not in the annual report) --
    "market_price_per_share": InputTrace("market_price_per_share", "External", "Stock exchange quoted price (as at / average for the period)"),
    "market_value_equity": InputTrace("market_value_equity", "External", "Market capitalisation"),
    "market_value_debt": InputTrace("market_value_debt", "External", "Market value of debt/liabilities"),
    "replacement_cost_assets": InputTrace("replacement_cost_assets", "External", "Estimated replacement cost of assets"),
    # -- NOT an input to any ratio in the sheet. `binding.py` binds these four solely
    #    for its P&L tie-out identities (Revenue + Other Income = Total Income;
    #    PBT - Total Tax = PAT), and `ratio_pipeline` carries them into `inputs` so the
    #    report can show them. Their trace rows exist so those figures are attributable
    #    too — no CATALOG spec reads them, and none of the sheet's formulas needs them
    #    (the ROA/ROCE "EBIT(1-t)" alternate that would use Total Tax is deliberately
    #    not implemented; the primary EBIT/PAT form is). --
    "other_income": InputTrace("other_income", "Statement of Profit & Loss", "II. Other Income"),
    "total_income": InputTrace("total_income", "Statement of Profit & Loss", "III. Total Income (I+II)"),
    "total_expenses": InputTrace("total_expenses", "Statement of Profit & Loss", "Total Expenses (IV)"),
    "total_tax": InputTrace("total_tax", "Statement of Profit & Loss", "Total Tax Expense (VIII)"),
    # The EPS the entity PRINTS on the face of the P&L — carried for the report and for the
    # SRS §8 EPS-recompute tie-out, never as a substitute for the `eps` ratio this catalog
    # computes from Profit for the Period and the share count. Hence the distinct name.
    "disclosed_eps": InputTrace("disclosed_eps", "Statement of Profit & Loss",
                                "Earnings per Equity Share (Basic and Diluted), as disclosed"),
}


# --------------------------------------------------------------------------- input sourcing tier
# WHERE a canonical input is obtainable from, which is what decides whether a spec can
# EVER produce a number — as opposed to whether it happened to bind on one filing.
#
# This distinction was invisible before, and it mattered: 12 of the 39 enabled specs
# (including 4 of the 11 Schedule III MANDATORY ratios — DSCR, Receivables Turnover,
# Payables Turnover, ROI) abstain on EVERY entity, because no binder anywhere in the
# package ever produces their inputs. They abstained with the message "missing inputs:
# instalments", which reads like "this filing didn't disclose it" when the truth is
# "nothing in this system can ever supply it". Same failure mode the module's own
# any_of rule exists to prevent — an absence of capability must not be reported as an
# absence of data — so the capability is declared here as data, and `unreachable_specs()`
# derives the consequence instead of leaving it to be rediscovered per entity.
#
# Kept as a plain dict so this module stays stdlib-only and imports nothing from the rest
# of `fs_db`; `test_computations.py` cross-checks it against what `ratio_pipeline` really
# emits, so drift on either side fails the tests rather than silently returning here.
FACE = "face"          # printed on the face of the BS / P&L — bound automatically today
NOTE = "note"          # in the filing, but only in a Note / Cash Flow / SoCE table, none
                       # of which has a binder yet. Reachable by building one.
PARAM = "param"        # an analyst-specified parameter, not a reported line at all — the
                       # sheet leaves it to the engagement (ROI's Return / Investment)
NOT_DISCLOSED = "not_disclosed"   # genuinely absent from Schedule III, face AND notes;
                       # no binder can ever fix it (the credit portion of sales/purchases)
EXTERNAL = "external"  # not in the annual report at all (quoted price, replacement cost)

# Why a given input can't be had, in words a reviewer can act on. Attached to the abstain
# message so "we cannot compute this" is never mistaken for "the entity failed to disclose
# this" — the distinction that matters most when the ratio is a Schedule III mandate.
TIER_REASON = {
    NOTE: "not on the face of the BS/P&L — needs a Notes/Cash-Flow/SoCE binder",
    PARAM: "an analyst-specified parameter, not a reported line item",
    NOT_DISCLOSED: "not separately disclosed anywhere in Schedule III",
    EXTERNAL: "not in the annual report (external market data)",
}

INPUT_SOURCE: dict[str, str] = {
    # -- face of the Balance Sheet / Statement of Profit & Loss --
    **{k: FACE for k in (
        "current_assets", "inventories", "trade_receivables", "cash_and_cash_equivalents",
        "marketable_securities", "net_fixed_assets", "total_assets", "current_liabilities",
        "short_term_borrowings", "long_term_borrowings", "equity_share_capital",
        "reserves_and_surplus", "total_equity", "revenue", "cost_of_materials_consumed",
        "purchases_of_stock_in_trade", "changes_in_inventories", "sga_expenses",
        "depreciation", "finance_costs", "pbt", "pat", "other_income", "total_income",
        "total_expenses", "total_tax", "disclosed_eps",
        # derived by ratio_pipeline from face lines (EBIT = PBT + Finance Costs; averages
        # from the comparative column), so they are as available as the lines behind them
        "ebit", "avg_total_assets", "avg_inventory", "avg_receivables", "avg_payables",
        "avg_equity",
        # Face of the CASH FLOW STATEMENT, bound by the "CF" half of the registry since
        # the FDR Appendix G work. Same tier as the BS/P&L face lines — printed, and
        # bound automatically — so an abstain on these really does mean "this filing's
        # cash flow statement did not disclose it", not "no binder exists".
        "ocf", "icf", "fcf_financing", "net_change_in_cash",
        "capex_ppe", "capex_intangibles", "capex_exploration", "government_grant_cf",
        # Face of the BALANCE SHEET, in the liability sections.
        "lease_liabilities_nc", "lease_liabilities_cl", "current_maturities_ltd",
        # The four structural sub-totals printed on the face of every Schedule III
        # balance sheet. They are bound and tied out exactly like the others; they were
        # simply never listed as emittable, so they were computed and then discarded.
        # `total_non_current_assets` and `other_non_current_assets` are what the FDR's
        # S14 and S10 read, which is why both signals could never fire for any entity.
        "total_non_current_assets", "other_non_current_assets",
        "total_non_current_liabilities", "total_equity_and_liabilities")},
    # -- in the filing, but not on the face of the two statements we bind --
    "prepaid_expenses": NOTE,          # Other Current Assets note
    "preference_share_capital": NOTE,  # Share Capital note
    "debentures": NOTE,                # Borrowings note
    "other_borrowed_funds": NOTE,      # Borrowings note
    "accumulated_losses": NOTE,        # Other Equity note
    "preference_dividend": NOTE,       # Dividend note (absent entirely for most PSUs)
    "non_cash_adjustments": NOTE,      # Cash Flow Statement
    "instalments": NOTE,               # Borrowings maturity note / CFS financing activities
    "loan_repayment": NOTE,            # Borrowings maturity note / CFS financing activities
    "num_equity_shares": NOTE,         # Share Capital note / EPS note (weighted average)
    "total_dividend": NOTE,            # Statement of Changes in Equity / Other Equity note
    # The sheet's OWN flag: Schedule III does not split credit from cash sales/purchases
    # anywhere, on the face or in the notes. Not a binder gap — the figure is genuinely
    # not disclosed, so these stay unreachable by design rather than being proxied by
    # revenue (see "TWO TURNOVER RATIOS, NOT ONE" in the module docstring).
    "credit_sales": NOT_DISCLOSED,
    "credit_purchases": NOT_DISCLOSED,
    # Placeholders the sheet leaves for the analyst to pick per engagement, not lines.
    "return_amount": PARAM,
    "investment": PARAM,
    # -- outside the annual report --
    **{k: EXTERNAL for k in ("market_price_per_share", "market_value_equity",
                             "market_value_debt", "replacement_cost_assets")},
}


# --------------------------------------------------------------------------- result
@dataclass
class Computation:
    """One deterministically-computed ratio (or an ABSTAIN)."""
    key: str
    name: str
    category: str
    formula: str                       # human formula, verbatim from the sheet
    result: float | None
    unit: str                          # "x" | "%" | "days" | "Rs"
    inputs: dict = field(default_factory=dict)   # the exact figures used
    trace: str = ""                    # reproducible: figures + operation + result
    status: str = "OK"                 # OK | ABSTAIN
    ideal: str = ""                    # rule-of-thumb band (INTERPRETATION only)
    mandatory_sch3: bool = False       # Schedule III note-disclosure ratio
    notes: list[str] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)   # InputTrace.to_dict() per input consumed

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- spec
@dataclass(frozen=True)
class RatioSpec:
    key: str
    name: str
    category: str
    formula: str
    num: Callable[[dict], float]
    den: Callable[[dict], float] = lambda _x: 1.0
    scale: float = 1.0
    unit: str = "x"
    required: tuple[str, ...] = ()      # inputs that MUST be present (else ABSTAIN)
    optional: tuple[str, ...] = ()      # inputs defaulted to 0 with a caveat note
    any_of: tuple[tuple[str, ...], ...] = ()
    # ^ groups of alternative inputs where AT LEAST ONE per group must be present
    #   (checked on the RAW inputs, before optional-defaulting). Used for a value
    #   that Schedule III only reports as a sum of parts (e.g. COGS = Cost of
    #   Materials Consumed + Purchases of Stock-in-Trade + Changes in Inventories):
    #   if a filing discloses even one of the three, we sum what's there (missing
    #   ones default to 0 via `optional`); if NONE are found, defaulting all three
    #   to 0 would silently report a fabricated zero, so this group forces ABSTAIN
    #   instead — "we didn't find the figure" must never look like "the figure is 0".
    num_label: str = "numerator"
    den_label: str = "denominator"
    ideal: str = ""
    source: str = "sheet"
    # ^ WHERE this spec comes from. "sheet" = one of the 41 rows of `Final Ratio
    #   Calulation chart - final detailed.xlsx`, which was the catalog's single source of
    #   truth and must stay closed (the tests assert the sheet-sourced count exactly, so
    #   nothing can be smuggled in beside it). "srs-9.2" = deliberately added to satisfy
    #   the SRS §9.2 analytical-review families, which the sheet does not cover. Keeping
    #   the provenance per spec is what lets both invariants be enforced at once.
    mandatory_sch3: bool = False
    enabled: bool = True                # market ratios ship disabled (need ext. price)
    disabled_reason: str = ""
    guard: Callable[[dict], str | None] | None = None
    # ^ optional pre-flight plausibility test over the bound inputs. Returns an abstain
    #   reason, or None to proceed. It can only ever ABSTAIN — it never adjusts a figure
    #   — so a guard can make the engine quieter but never wrong.
    caveat: str = ""
    # ^ a standing limitation of the DIAGNOSTIC itself, attached to every successful
    #   result. Distinct from the per-run `notes` compute() generates for defaulted
    #   optional inputs: those describe what happened on one filing, this describes
    #   something true of the ratio on every filing. Used where a figure is publishable
    #   but must never be read at face value (Appendix G's "indicative" measures).
    trace_fn: Callable[[dict, float, float, float], str] | None = None
    # ^ optional richer trace for COMPOSITE ratios, called as (inputs, num, den, result)
    #   and returning the trace string in place of the default "num / den = result".
    #   Needed because the default line is only legible when the numerator is one
    #   figure: for DuPont ROE it would print the product's endpoints and hide the
    #   three factors that are the entire point of the diagnostic, and for Free Cash
    #   Flow it would print "/ 1.00". It can only ever change the WORDING — the number
    #   is computed by `num`/`den`/`scale` before this is consulted, so a trace_fn
    #   cannot alter a result, only explain it.
    den_must_be_positive: bool = False
    # ^ opt-in only — NOT a blanket d<=0 rule. Some denominators (e.g. Total Equity
    #   in debt_to_equity) can legitimately be negative for a distressed company,
    #   and that is a meaningful signal an audit tool must compute and show, not
    #   hide behind an abstain. Set this only where the underlying concept is
    #   physically incapable of being negative (e.g. Daily Operating Expenses),
    #   so a negative result there means the derivation doesn't apply to this
    #   filing's cost structure, not a real business fact.


# --------------------------------------------------------------------------- evaluator
def _trace_for(spec: RatioSpec) -> list[dict]:
    keys, seen, out = list(spec.required) + list(spec.optional), set(), []
    for grp in spec.any_of:
        keys += list(grp)
    for k in keys:
        if k in seen:
            continue
        seen.add(k)
        t = INPUT_TRACE.get(k)
        if t:
            out.append(t.to_dict())
    return out


def compute(spec: RatioSpec, raw: dict) -> Computation:
    """Evaluate one spec deterministically. Never raises; abstains on any gap."""
    sources = _trace_for(spec)

    # any_of gate FIRST, on the raw (pre-default) inputs: a group that's entirely
    # absent means "we couldn't find this figure at all", which must abstain, not
    # silently compute as if every part of it were legitimately zero.
    for grp in spec.any_of:
        if not any(raw.get(k) is not None for k in grp):
            return _abstain(spec, f"none of: {', '.join(grp)} present", [], sources)

    inp = dict(raw)
    notes: list[str] = []
    defaulted: set[str] = set()
    for k in spec.optional:
        if inp.get(k) is None:
            inp[k] = 0.0
            defaulted.add(k)
            notes.append(f"{k} not disclosed — treated as 0")
    # Which optional inputs were defaulted, for any `trace_fn` that needs to tell a
    # DISCLOSED zero from an ABSENT one. Without it a trace reads "Purchase of
    # Intangibles 0.00" for a filing that never disclosed the line — the same
    # fabricated-zero misreading the `any_of` gate exists to prevent, one layer down in
    # the narration rather than the arithmetic. Reserved key, stripped from `used` below.
    inp[_DEFAULTED] = defaulted

    missing = [k for k in spec.required if inp.get(k) is None]
    if missing:
        # Say WHICH KIND of unavailable. "missing inputs: instalments" reads as "the entity
        # did not disclose this" — a serious misreading when the ratio is a Schedule III
        # mandate and the real cause is that this engine has no binder for the borrowings
        # maturity note. The tier turns a data-gap message into a capability-gap message
        # wherever that is the truth.
        return _abstain(spec, "missing inputs: " + ", ".join(
            f"{k} ({TIER_REASON[t]})" if (t := INPUT_SOURCE.get(k, FACE)) != FACE else k
            for k in missing), notes, sources)
    if spec.guard:
        reason = spec.guard(inp)
        if reason:
            return _abstain(spec, reason, notes, sources)
    try:
        n = float(spec.num(inp))
        d = float(spec.den(inp))
    except (TypeError, ValueError, KeyError) as e:
        return _abstain(spec, f"input not numeric ({e})", notes, sources)
    if d == 0:
        return _abstain(spec, f"{spec.den_label} is zero — division undefined", notes, sources)
    if spec.den_must_be_positive and d < 0:
        return _abstain(spec, f"{spec.den_label} is negative ({d:,.2f}) — the underlying "
                        f"derivation does not fit this filing's cost structure "
                        f"(e.g. cost of sales reported under an industry-specific line "
                        f"outside the standard Schedule III decomposition)", notes, sources)

    result = round(n / d * spec.scale, 4)
    used = {k: raw.get(k) if k in defaulted else inp.get(k)
            for k in (spec.required + spec.optional)}
    # ^ `raw.get` for a defaulted key, so `inputs` reports None ("not disclosed") rather
    #   than the 0.0 the arithmetic substituted. The note already says so in words; this
    #   keeps the machine-readable payload honest too.
    for grp in spec.any_of:                    # any_of members are real inputs too, and
        for k in grp:                          # a trace that omits them cannot be checked
            used.setdefault(k, raw.get(k))
    scale_txt = "" if spec.scale == 1 else f" x {spec.scale:g}"
    trace = (f"{spec.num_label} {n:,.2f} / {spec.den_label} {d:,.2f}{scale_txt} "
             f"= {result:,.4f} {spec.unit}")
    if spec.trace_fn:
        trace = spec.trace_fn(inp, n, d, result)
    if spec.caveat:
        notes = notes + [spec.caveat]
    return Computation(spec.key, spec.name, spec.category, spec.formula, result,
                       spec.unit, used, trace, "OK", spec.ideal,
                       spec.mandatory_sch3, notes, sources)


def _abstain(spec: RatioSpec, reason: str, notes: list[str], sources: list[dict]) -> Computation:
    return Computation(spec.key, spec.name, spec.category, spec.formula, None,
                       spec.unit, {}, f"ABSTAIN: {reason}", "ABSTAIN", spec.ideal,
                       spec.mandatory_sch3, notes + [reason], sources)


# --------------------------------------------------------------------------- derivations
# Named aggregates the sheet defines in prose, kept as small helpers so each
# ratio's num/den reads like the sheet (and so a reviewer sees the derivation).
def _quick_assets(x):        return x["current_assets"] - x["inventories"] - x.get("prepaid_expenses", 0.0)
def _cl_excl_stb(x):         return x["current_liabilities"] - x.get("short_term_borrowings", 0.0)
def _net_assets(x):
    """Total Assets - Current Liabilities, per the sheet's own name for this input
    ("Net Assets (Total Assets – Current Liabilities)") and its Derived TRACE row, which
    states the source as "Balance Sheet (Total Assets – Current Liabilities)" (sheet rows
    33 / 40 / 79).

    The sheet's Components PROSE also glosses it as "Property, Plant and Equipment (Net
    Block) + Net Current Assets", and this function used to compute that instead. The two
    agree only when PPE is the entire non-current side; for a real filing they diverge
    enormously — on ONGC 2024-25, TA-CL = 4,137,798.66 against PPE+NCA = 2,050,151.06, a
    50.5% gap that put `equity_ratio` at 1.5427 (an "equity ratio" above 1, against the
    sheet's own ideal of ~0.5+) and halved `debt_ratio` to 0.0410.

    Three things settle it for the trace-row reading: it is the ratio's own NAME, it is
    the machine-readable trace column the rest of this module is built from, and the sheet
    gives the identical Derived source for "Capital Employed" — which `_capital_employed`
    below already computes as TA - CL. Reading the same sheet concept two different ways
    in one file was the actual defect; the prose gloss is a textbook simplification that
    assumes away non-current assets other than PPE."""
    return x["total_assets"] - x["current_liabilities"]
def _working_capital(x):     return x["current_assets"] - x["current_liabilities"]
def _capital_employed(x):    return x["total_assets"] - x["current_liabilities"]

def _cogs(x):
    """COGS is not a Schedule III face line — it's the sum of the three P&L lines
    that ARE printed. `any_of` on the ratio spec guarantees at least one is present
    before this runs; missing ones are optional-defaulted to 0 by compute()."""
    return (x.get("cost_of_materials_consumed", 0.0) + x.get("purchases_of_stock_in_trade", 0.0)
            + x.get("changes_in_inventories", 0.0))

def _total_borrowings(x):
    """Same pattern as COGS: Total Borrowings = Long-term + Short-term Borrowings,
    each a separate BS line (non-current vs current liabilities section)."""
    return x.get("long_term_borrowings", 0.0) + x.get("short_term_borrowings", 0.0)

COGS_MATERIALITY = 0.10          # of Total Expenses; see _cogs_material below


def _cogs_material(x):
    """Guard for every COGS-derived ratio. `_cogs()` sums the three Schedule III lines
    the sheet decomposes cost of sales into, but some filings report their real cost of
    sales under an industry-specific head outside all three — confirmed live on ONGC,
    whose "Production, transportation, selling, distribution and other expenditure"
    (592,136.06) sits outside the decomposition, leaving _cogs() = 7,649.48 against
    Total Expenses of 1,015,659.06. Every COGS ratio then silently becomes nonsense:
    Gross Profit 99.45%, COGS Expense Ratio 0.55%, Inventory Turnover 0.07x — numbers
    that LOOK authoritative and are not.

    Where Total Expenses is bound we can test the decomposition instead of trusting it:
    below COGS_MATERIALITY of total expenses it is not this entity's cost of sales, so
    abstain. This can only silence a ratio, never change one. NOTE this threshold is
    ours, not the sheet's — the sheet has no plausibility column."""
    te = x.get("total_expenses")
    if te in (None, 0):
        return None                      # nothing to test against — proceed as before
    c = _cogs(x)
    if abs(c) >= COGS_MATERIALITY * abs(te):
        return None
    return (f"cost-of-sales decomposition (Cost of Materials Consumed + Purchases of "
            f"Stock-in-Trade + Changes in Inventories = {c:,.2f}) is only {abs(c)/abs(te):.2%} "
            f"of Total Expenses {te:,.2f} — this filing reports its cost of sales under an "
            f"industry-specific head outside the three Schedule III lines the sheet "
            f"decomposes COGS into, so every COGS-based ratio would be misleading")


def _procurement(x):
    """Amounts actually bought from suppliers in the period: Cost of Materials Consumed +
    Purchases of Stock-in-Trade. Deliberately EXCLUDES Changes in Inventories (unlike
    `_cogs`) — that line is a valuation adjustment converting purchases to cost of sales,
    not a purchase from a creditor, so it has no business in a payables-turnover
    numerator."""
    return x.get("cost_of_materials_consumed", 0.0) + x.get("purchases_of_stock_in_trade", 0.0)


def _procurement_material(x):
    """`_cogs_material` for the procurement lines. Same failure mode: where a filing books
    its input costs under an industry-specific head (ONGC), Cost of Materials Consumed is a
    rounding error against Total Expenses and a payables turnover built on it reports an
    absurd payment period with total confidence."""
    te = x.get("total_expenses")
    if te in (None, 0):
        return None
    p = _procurement(x)
    if abs(p) >= COGS_MATERIALITY * abs(te):
        return None
    return (f"procurement lines (Cost of Materials Consumed + Purchases of Stock-in-Trade "
            f"= {p:,.2f}) are only {abs(p)/abs(te):.2%} of Total Expenses {te:,.2f} — this "
            f"filing books its input costs under an industry-specific head, so a payables "
            f"turnover built on these lines would be misleading")


# --------------------------------------------------------------------------- Appendix G
# Helpers for the FDR Audit Planning Intelligence Specification v3, Appendix G — Formula
# library, as decomposed in `formulas/FDR ratios mapping.xlsx`. Everything below follows
# that sheet's component breakdown; where the sheet's Legend leaves a choice open, the
# choice made here is stated in the formula string so the trace never implies a
# definition the code did not use (the D3 failure mode, see `_net_assets`).

# The mapping sheet's Legend is explicit: "No single Schedule III line equals 'total
# debt'. Reviewers must fix a definition (e.g., whether to include lease liabilities and
# current maturities) and apply it consistently across all entities compared."
#
# DEFINITION FIXED HERE: borrowings + current maturities of long-term debt + lease
# liabilities (both halves). Ind AS 116 lease liabilities are contractual, interest-
# bearing, non-discretionary obligations; excluding them understates ONGC's leverage by
# 4.5x (0.0266x against 0.1196x), and the same omission on a lease-heavy entity is the
# kind of confidently-wrong figure §12.3 of RATIO_ENGINE_DESIGN.md exists to prevent.
# `debt_to_equity` (Schedule III mandatory) deliberately KEEPS the borrowings-only
# numerator, because that is the disclosure the statute asks for; this broader measure
# ships beside it under its own name, per the "TWO TURNOVER RATIOS, NOT ONE" precedent.
def _total_debt(x):
    return (x.get("long_term_borrowings", 0.0) + x.get("short_term_borrowings", 0.0)
            + x.get("current_maturities_ltd", 0.0)
            + x.get("lease_liabilities_nc", 0.0) + x.get("lease_liabilities_cl", 0.0))


def _capex(x):
    """Gross capital expenditure = the mapping sheet's items 2a + 2b + 2c.

    MAGNITUDES, not signed values. Ind AS 7 prints these as outflows (negative), but the
    sign that reaches us depends on the extraction, and Free Cash Flow must be
    `OCF - capex` either way. Taking absolute values makes the arithmetic independent of
    that convention; the direction is proved separately and much more strongly by the
    `cash_flow_reconciles` tie-out in binding.py, which fails if any sign was lost.

    Gross, never net of disposal proceeds: Appendix G says "Operating cash flow - Capital
    expenditure", and netting "Proceeds from disposal of PPE" into it would report a
    company that sold assets as having spent less on them."""
    return (abs(x.get("capex_ppe", 0.0)) + abs(x.get("capex_intangibles", 0.0))
            + abs(x.get("capex_exploration", 0.0)))


def _dupont_factors(x):
    """The three DuPont factors. Their product is algebraically PAT / Average Equity —
    the factors cancel — so `dupont_roe` computes that directly and reports the factors
    here rather than multiplying three rounded numbers together, which would drift."""
    net_margin = x["pat"] / x["revenue"]
    asset_turnover = x["revenue"] / x["avg_total_assets"]
    equity_multiplier = x["avg_total_assets"] / x["avg_equity"]
    return net_margin, asset_turnover, equity_multiplier


def _dupont_trace(x, n, d, result):
    nm, at, em = _dupont_factors(x)
    return (f"Net Margin {nm:.4f} (PAT {x['pat']:,.2f} / Revenue {x['revenue']:,.2f}) "
            f"x Asset Turnover {at:.4f} (Revenue {x['revenue']:,.2f} / Avg Total Assets "
            f"{x['avg_total_assets']:,.2f}) "
            f"x Equity Multiplier {em:.4f} (Avg Total Assets {x['avg_total_assets']:,.2f} "
            f"/ Avg Equity {x['avg_equity']:,.2f}) "
            f"= {nm * at * em * 100:,.4f} % "
            f"[computed directly as PAT {n:,.2f} / Avg Equity {d:,.2f} x 100 = "
            f"{result:,.4f} %; the revenue and asset terms cancel, so this is exact, "
            f"not a re-multiplication of rounded factors]")


def _ccc_parts(x):
    dso = x["avg_receivables"] / x["revenue"] * DAYS_IN_YEAR
    inv_days = x["avg_inventory"] / _cogs(x) * DAYS_IN_YEAR
    dpo = x["avg_payables"] / _procurement(x) * DAYS_IN_YEAR
    return dso, inv_days, dpo


def _ccc(x):
    dso, inv_days, dpo = _ccc_parts(x)
    return dso + inv_days - dpo


def _ccc_trace(x, n, d, result):
    dso, inv_days, dpo = _ccc_parts(x)
    return (f"Receivables Days {dso:,.2f} (Avg Trade Receivables {x['avg_receivables']:,.2f} "
            f"/ Revenue {x['revenue']:,.2f} x {DAYS_IN_YEAR}) "
            f"+ Inventory Days {inv_days:,.2f} (Avg Inventory {x['avg_inventory']:,.2f} "
            f"/ COGS {_cogs(x):,.2f} x {DAYS_IN_YEAR}) "
            f"- Payables Days {dpo:,.2f} (Avg Trade Payables {x['avg_payables']:,.2f} "
            f"/ Purchases {_procurement(x):,.2f} x {DAYS_IN_YEAR}) "
            f"= {result:,.4f} days")


def _fcf_trace(x, n, d, result):
    absent = x.get(_DEFAULTED) or set()
    parts = [f"{lbl} {abs(x.get(k, 0.0)):,.2f}"
             for k, lbl in (("capex_ppe", "Payments for PPE"),
                            ("capex_intangibles", "Purchase of Intangibles"),
                            ("capex_exploration", "Exploratory/Development Drilling"))
             if k not in absent and x.get(k) is not None]
    return (f"Operating Cash Flow {x['ocf']:,.2f} - Capital Expenditure {_capex(x):,.2f} "
            f"({' + '.join(parts)}) = {result:,.2f} Rs")


def _ccc_guard(x):
    """CCC needs BOTH the COGS and the procurement decompositions to be real, because it
    sums an inventory-days term built on one and a payables-days term built on the other.
    Reporting a cycle where two of its three legs are rounding errors would be worse than
    either component ratio alone, so this abstains if either guard fires."""
    return _cogs_material(x) or _procurement_material(x)


def _gross_profit(x):        return x["revenue"] - _cogs(x)
def _daily_opex(x):          return (_cogs(x) + x.get("sga_expenses", 0.0) - x.get("depreciation", 0.0)) / DAYS_IN_YEAR
def _eads(x):                return x["pat"] + x.get("depreciation", 0.0) + x.get("non_cash_adjustments", 0.0) + x["finance_costs"]
def _eps_calc(x):            return (x["pat"] - x.get("preference_dividend", 0.0)) / x["num_equity_shares"]
def _dps_calc(x):            return x["total_dividend"] / x["num_equity_shares"]


# --------------------------------------------------------------------------- THE CATALOG
# Row S.No in comments ties each spec back to the Excel sheet.
CATALOG: list[RatioSpec] = [
    # -- 1. Liquidity -------------------------------------------------------
    RatioSpec("current_ratio", "Current Ratio", "Liquidity",
              "Current Assets / Current Liabilities",
              num=lambda x: x["current_assets"], den=lambda x: x["current_liabilities"],
              required=("current_assets", "current_liabilities"),
              num_label="Current Assets", den_label="Current Liabilities",
              ideal="~ 2 : 1", mandatory_sch3=True),
    RatioSpec("quick_ratio", "Quick Ratio (Acid-Test)", "Liquidity",
              "(Current Assets - Inventories - Other Current Assets (Prepaid Expenses)) / Current Liabilities",
              num=_quick_assets, den=lambda x: x["current_liabilities"],
              required=("current_assets", "inventories", "current_liabilities"),
              optional=("prepaid_expenses",),
              num_label="Quick Assets", den_label="Current Liabilities", ideal="~ 1 : 1"),
    RatioSpec("cash_ratio", "Cash Ratio (Absolute Liquidity)", "Liquidity",
              "(Cash and Cash Equivalents + Marketable Securities/Current Investments) / Current Liabilities",
              num=lambda x: x["cash_and_cash_equivalents"] + x.get("marketable_securities", 0.0),
              den=lambda x: x["current_liabilities"],
              required=("cash_and_cash_equivalents", "current_liabilities"), optional=("marketable_securities",),
              num_label="Cash and Cash Equivalents + Marketable Securities", den_label="Current Liabilities",
              ideal="~ 0.5-1"),
    RatioSpec("basic_defense_interval", "Basic Defense Interval", "Liquidity",
              "(Cash and Cash Equivalents + Trade Receivables (net) + Marketable Securities) / Daily Operating Expenses",
              num=lambda x: x["cash_and_cash_equivalents"] + x["trade_receivables"] + x.get("marketable_securities", 0.0),
              den=_daily_opex, unit="days",
              required=("cash_and_cash_equivalents", "trade_receivables"),
              optional=("marketable_securities", "sga_expenses", "depreciation",
                        "cost_of_materials_consumed", "purchases_of_stock_in_trade", "changes_in_inventories"),
              any_of=(("cost_of_materials_consumed", "purchases_of_stock_in_trade", "changes_in_inventories"),),
              guard=_cogs_material,     # same root cause as the negative-denominator case
                                        # below, but reported with the actual reason
              num_label="Liquid Assets", den_label="Daily Operating Expenses",
              ideal="higher; often vs 30-90 days",
              # Daily Operating Expenses cannot legitimately be negative for an
              # operating company — a negative result means the COGS-decomposition
              # proxy understates true cost (see the "KNOWN LIMITATION" note in the
              # module docstring), not a real negative expense. Abstain rather than
              # report a nonsensical negative day-count (confirmed live on ONGC: -159 days).
              den_must_be_positive=True),
    RatioSpec("net_working_capital", "Net Working Capital", "Liquidity",
              "Current Assets - Current Liabilities (excluding Short-term Borrowings)",
              num=lambda x: x["current_assets"] - _cl_excl_stb(x), unit="Rs",
              required=("current_assets", "current_liabilities"),
              optional=("short_term_borrowings",),
              num_label="CA - CL(excl Short-term Borrowings)", den_label="1", ideal="must be positive"),

    # -- 2. Leverage / Capital Structure -----------------------------------
    RatioSpec("equity_ratio", "Equity Ratio", "Leverage",
              "Total Equity (Equity Share Capital + Other Equity) / Net Assets "
              "(Total Assets - Current Liabilities)",
              num=lambda x: x["total_equity"], den=_net_assets,
              required=("total_equity", "total_assets", "current_liabilities"),
              num_label="Total Equity", den_label="Net Assets", ideal="higher safer (~0.5+)"),
    RatioSpec("debt_ratio", "Debt Ratio", "Leverage",
              "Total Borrowings / Net Assets (Total Assets - Current Liabilities)",
              num=_total_borrowings, den=_net_assets,
              required=("total_assets", "current_liabilities"),
              optional=("long_term_borrowings", "short_term_borrowings"),
              any_of=(("long_term_borrowings", "short_term_borrowings"),),
              num_label="Total Borrowings", den_label="Net Assets", ideal="< 1"),
    RatioSpec("debt_to_equity", "Debt-to-Equity Ratio", "Leverage",
              "Total Borrowings / Total Equity (Equity Share Capital + Other Equity)",
              num=_total_borrowings, den=lambda x: x["total_equity"],
              required=("total_equity",),
              optional=("long_term_borrowings", "short_term_borrowings"),
              any_of=(("long_term_borrowings", "short_term_borrowings"),),
              num_label="Total Borrowings", den_label="Total Equity",
              ideal="~ 2:1 (1:1 conservative)", mandatory_sch3=True),
    RatioSpec("debt_to_total_assets", "Debt to Total Assets", "Leverage",
              "Total Borrowings / Total Assets",
              num=_total_borrowings, den=lambda x: x["total_assets"],
              required=("total_assets",),
              optional=("long_term_borrowings", "short_term_borrowings"),
              any_of=(("long_term_borrowings", "short_term_borrowings"),),
              num_label="Total Borrowings", den_label="Total Assets", ideal="< 0.5-0.6"),
    RatioSpec("capital_gearing", "Capital Gearing Ratio", "Leverage",
              "(Preference Share Capital + Debentures + Other Borrowings) / (Equity Share Capital + Other Equity - Losses)",
              num=lambda x: x.get("preference_share_capital", 0.0) + x.get("debentures", 0.0) + x.get("other_borrowed_funds", 0.0),
              den=lambda x: x["equity_share_capital"] + x["reserves_and_surplus"] - x.get("accumulated_losses", 0.0),
              required=("equity_share_capital", "reserves_and_surplus"),
              optional=("preference_share_capital", "debentures", "other_borrowed_funds", "accumulated_losses"),
              # Without this guard all three numerator parts optional-default to 0 and the
              # ratio reports a confident "0.0000 x" — i.e. "this company has no fixed-cost
              # funds" — when the truth is that none of the three was found (all are Notes-
              # level, so that is the NORMAL case from BS+P&L alone). Observed live on ONGC.
              any_of=(("preference_share_capital", "debentures", "other_borrowed_funds"),),
              num_label="Fixed-cost Funds", den_label="Equity Funds", ideal="lower (low-geared) safer"),
    RatioSpec("proprietary_ratio", "Proprietary Ratio", "Leverage",
              "Total Equity (Equity Share Capital + Preference Share Capital + Other Equity) / Total Assets",
              num=lambda x: x["equity_share_capital"] + x.get("preference_share_capital", 0.0) + x["reserves_and_surplus"],
              den=lambda x: x["total_assets"],
              required=("equity_share_capital", "reserves_and_surplus", "total_assets"),
              optional=("preference_share_capital",),
              num_label="Proprietary Fund", den_label="Total Assets", ideal=">= 0.5"),

    # -- 3. Coverage --------------------------------------------------------
    RatioSpec("dscr", "Debt Service Coverage Ratio", "Coverage",
              "Earnings Available for Debt Service / (Finance Costs + Instalments)",
              num=_eads, den=lambda x: x["finance_costs"] + x["instalments"],
              required=("pat", "finance_costs", "instalments"), optional=("depreciation", "non_cash_adjustments"),
              num_label="Earnings for Debt Service", den_label="Finance Costs + Instalments",
              ideal="~ 1.5 - 2", mandatory_sch3=True),
    RatioSpec("interest_coverage", "Interest Coverage (Times Interest Earned)", "Coverage",
              "EBIT / Finance Costs",
              num=lambda x: x["ebit"], den=lambda x: x["finance_costs"],
              required=("ebit", "finance_costs"),
              num_label="EBIT", den_label="Finance Costs", ideal="3-5x comfortable"),
    RatioSpec("preference_dividend_coverage", "Preference Dividend Coverage", "Coverage",
              "Profit for the Period / Preference Dividend",
              num=lambda x: x["pat"], den=lambda x: x["preference_dividend"],
              required=("pat", "preference_dividend"),
              num_label="Profit for the Period", den_label="Preference Dividend", ideal="> 1"),
    RatioSpec("fixed_charges_coverage", "Fixed Charges Coverage", "Coverage",
              "(EBIT + Depreciation and Amortisation Expense) / (Finance Costs + Repayment of Loan)",
              num=lambda x: x["ebit"] + x.get("depreciation", 0.0),
              den=lambda x: x["finance_costs"] + x["loan_repayment"],
              required=("ebit", "finance_costs", "loan_repayment"), optional=("depreciation",),
              num_label="EBIT + Depreciation", den_label="Finance Costs + Loan Repayment", ideal="> 1"),

    # -- 4. Activity / Efficiency / Turnover -------------------------------
    # Numerator kept as Revenue from Operations (the primary/standard convention and
    # the one Schedule III's own mandatory "Net Capital Turnover Ratio" uses); the
    # sheet's "(or COGS)" is a documented alternate convention, not a required path.
    RatioSpec("total_assets_turnover", "Total Assets Turnover", "Turnover",
              "Revenue from Operations / Average Total Assets",
              num=lambda x: x["revenue"], den=lambda x: x["avg_total_assets"],
              required=("revenue", "avg_total_assets"),
              num_label="Revenue from Operations", den_label="Avg Total Assets", ideal="higher is better"),
    RatioSpec("fixed_assets_turnover", "Fixed Assets Turnover", "Turnover",
              "Revenue from Operations / Property, Plant and Equipment (Net Block)",
              num=lambda x: x["revenue"], den=lambda x: x["net_fixed_assets"],
              required=("revenue", "net_fixed_assets"),
              num_label="Revenue from Operations", den_label="Net Fixed Assets", ideal="higher is better"),
    RatioSpec("capital_turnover", "Capital / Net Assets Turnover", "Turnover",
              "Revenue from Operations / Capital Employed (Total Assets – Current Liabilities)",
              num=lambda x: x["revenue"], den=_capital_employed,
              required=("revenue", "total_assets", "current_liabilities"),
              num_label="Revenue from Operations", den_label="Capital Employed", ideal="higher is better",
              mandatory_sch3=True),   # Schedule III "Net Capital Turnover Ratio"
    RatioSpec("current_assets_turnover", "Current Assets Turnover", "Turnover",
              "Revenue from Operations / Current Assets",
              num=lambda x: x["revenue"], den=lambda x: x["current_assets"],
              required=("revenue", "current_assets"),
              num_label="Revenue from Operations", den_label="Current Assets", ideal="higher is better"),
    RatioSpec("working_capital_turnover", "Working Capital Turnover", "Turnover",
              "Revenue from Operations / Working Capital (Current Assets – Current Liabilities)",
              num=lambda x: x["revenue"], den=_working_capital,
              required=("revenue", "current_assets", "current_liabilities"),
              num_label="Revenue from Operations", den_label="Working Capital", ideal="higher, not excessive"),
    RatioSpec("inventory_turnover", "Inventory Turnover", "Turnover",
              "(Cost of Materials Consumed + Purchases of Stock-in-Trade + Changes in Inventories) / Average Inventory",
              num=_cogs, den=lambda x: x["avg_inventory"],
              required=("avg_inventory",),
              optional=("cost_of_materials_consumed", "purchases_of_stock_in_trade", "changes_in_inventories"),
              any_of=(("cost_of_materials_consumed", "purchases_of_stock_in_trade", "changes_in_inventories"),),
              guard=_cogs_material,
              num_label="Cost of Materials + Purchases + Inventory Change", den_label="Avg Inventory",
              ideal="higher is better", mandatory_sch3=True),
    RatioSpec("receivables_turnover", "Receivables Turnover", "Turnover",
              "Revenue from Operations (Credit Sales portion) / Average Trade Receivables",
              num=lambda x: x["credit_sales"], den=lambda x: x["avg_receivables"],
              required=("credit_sales", "avg_receivables"),
              num_label="Credit Sales", den_label="Avg Trade Receivables", ideal="higher is better",
              mandatory_sch3=True),
    RatioSpec("receivables_collection_period", "Receivables Collection Period", "Turnover",
              "365 / Receivables Turnover  (= Avg Trade Receivables x 365 / Credit Sales)",
              num=lambda x: x["avg_receivables"], den=lambda x: x["credit_sales"],
              scale=DAYS_IN_YEAR, unit="days",
              required=("avg_receivables", "credit_sales"),
              num_label="Avg Trade Receivables", den_label="Credit Sales", ideal="~ credit period allowed"),
    RatioSpec("payables_turnover", "Payables Turnover", "Turnover",
              "Purchases (Credit portion) / Average Trade Payables",
              num=lambda x: x["credit_purchases"], den=lambda x: x["avg_payables"],
              required=("credit_purchases", "avg_payables"),
              num_label="Credit Purchases", den_label="Avg Trade Payables", ideal="vs firm's own credit period",
              mandatory_sch3=True),
    RatioSpec("payables_velocity", "Payables Velocity / Avg Payment Period", "Turnover",
              "365 / Payables Turnover  (= Avg Trade Payables x 365 / Credit Purchases)",
              num=lambda x: x["avg_payables"], den=lambda x: x["credit_purchases"],
              scale=DAYS_IN_YEAR, unit="days",
              required=("avg_payables", "credit_purchases"),
              num_label="Avg Trade Payables", den_label="Credit Purchases", ideal="vs credit offered"),

    # -- 4b. Turnover on TOTAL flows (source: SRS §9.2, NOT the sheet) -------------
    # The four specs above are the Schedule III mandates and use Net Credit Sales /
    # Purchases, which Schedule III never discloses — so they abstain on every filing.
    # SRS §9.2 "Efficiency | Receivables, inventory, payables, asset turnover" asks for
    # these as ANALYTICAL indicators and says nothing about a credit split, so they are
    # computed here on the total flows, under names and formula strings that say exactly
    # that. They are NOT flagged mandatory_sch3: reporting a total-revenue turnover as
    # though it were the entity's Schedule III disclosure would be a fabricated figure
    # under a regulatory label. The credit-based specs keep abstaining until a Notes
    # binder can read the entity's own disclosed numerator/denominator, at which point
    # these become the cross-check against it rather than a substitute for it.
    RatioSpec("receivables_turnover_revenue",
              "Trade Receivables Turnover (on total Revenue from Operations)", "Turnover",
              "Revenue from Operations / Average Trade Receivables  "
              "(total revenue, NOT the Schedule III net-credit-sales numerator)",
              num=lambda x: x["revenue"], den=lambda x: x["avg_receivables"],
              required=("revenue", "avg_receivables"),
              num_label="Revenue from Operations", den_label="Avg Trade Receivables",
              ideal="higher is better; compare with the entity's stated credit terms",
              source="srs-9.2"),
    RatioSpec("days_sales_outstanding", "Days Sales Outstanding (DSO)", "Turnover",
              "Average Trade Receivables x 365 / Revenue from Operations  "
              "(total revenue, NOT the Schedule III net-credit-sales numerator)",
              num=lambda x: x["avg_receivables"], den=lambda x: x["revenue"],
              scale=DAYS_IN_YEAR, unit="days",
              required=("avg_receivables", "revenue"),
              num_label="Avg Trade Receivables", den_label="Revenue from Operations",
              ideal="~ credit period allowed; rising DSO signals collectability/cut-off risk",
              source="srs-9.2"),
    RatioSpec("payables_turnover_purchases",
              "Trade Payables Turnover (on Purchases)", "Turnover",
              "(Cost of Materials Consumed + Purchases of Stock-in-Trade) / Average Trade "
              "Payables  (total procurement, NOT the Schedule III net-credit-purchases numerator)",
              num=_procurement, den=lambda x: x["avg_payables"],
              required=("avg_payables",),
              optional=("cost_of_materials_consumed", "purchases_of_stock_in_trade"),
              any_of=(("cost_of_materials_consumed", "purchases_of_stock_in_trade"),),
              guard=_procurement_material,
              num_label="Cost of Materials + Purchases", den_label="Avg Trade Payables",
              ideal="compare with the credit period suppliers allow",
              source="srs-9.2"),
    RatioSpec("days_payable_outstanding", "Days Payable Outstanding (DPO)", "Turnover",
              "Average Trade Payables x 365 / (Cost of Materials Consumed + Purchases of "
              "Stock-in-Trade)  (total procurement, NOT the Schedule III numerator)",
              num=lambda x: x["avg_payables"], den=_procurement,
              scale=DAYS_IN_YEAR, unit="days",
              required=("avg_payables",),
              optional=("cost_of_materials_consumed", "purchases_of_stock_in_trade"),
              any_of=(("cost_of_materials_consumed", "purchases_of_stock_in_trade"),),
              guard=_procurement_material, den_must_be_positive=True,
              num_label="Avg Trade Payables", den_label="Purchases",
              ideal="vs supplier credit terms; a stretching DPO signals liquidity stress",
              source="srs-9.2"),

    # -- 5. Profitability (based on sales) ---------------------------------
    RatioSpec("gross_profit_margin", "Gross Profit Ratio", "Profitability-Sales",
              "(Gross Profit / Revenue from Operations) x 100,  "
              "Gross Profit = Revenue from Operations - (Cost of Materials Consumed + "
              "Purchases of Stock-in-Trade + Changes in Inventories)",
              num=_gross_profit, den=lambda x: x["revenue"], scale=100.0, unit="%",
              required=("revenue",),
              optional=("cost_of_materials_consumed", "purchases_of_stock_in_trade", "changes_in_inventories"),
              any_of=(("cost_of_materials_consumed", "purchases_of_stock_in_trade", "changes_in_inventories"),),
              guard=_cogs_material,
              num_label="Gross Profit", den_label="Revenue from Operations", ideal="industry-specific; higher better"),
    RatioSpec("net_profit_margin", "Net Profit Ratio", "Profitability-Sales",
              "(Profit for the Period / Revenue from Operations) x 100",
              num=lambda x: x["pat"], den=lambda x: x["revenue"], scale=100.0, unit="%",
              required=("pat", "revenue"),
              num_label="Profit for the Period", den_label="Revenue from Operations", ideal="higher is favourable",
              mandatory_sch3=True),
    RatioSpec("operating_profit_margin", "Operating Profit Ratio", "Profitability-Sales",
              "(Operating Profit / Revenue from Operations) x 100,  Operating Profit = EBIT",
              num=lambda x: x["ebit"], den=lambda x: x["revenue"], scale=100.0, unit="%",
              required=("ebit", "revenue"),
              num_label="EBIT", den_label="Revenue from Operations", ideal="higher is favourable"),

    # -- S.No 29 "Expense Ratios": one template row -> one spec per expense head the
    #    sheet names. Formula cell: "Respective Expense / Revenue from Operations x 100";
    #    ideal/implication columns are shared across all four (see module docstring).
    RatioSpec("cogs_ratio", "COGS Expense Ratio", "Profitability-Sales",
              "((Cost of Materials Consumed + Purchases of Stock-in-Trade + Changes in "
              "Inventories) / Revenue from Operations) x 100",
              num=_cogs, den=lambda x: x["revenue"], scale=100.0, unit="%",
              required=("revenue",),
              optional=("cost_of_materials_consumed", "purchases_of_stock_in_trade", "changes_in_inventories"),
              any_of=(("cost_of_materials_consumed", "purchases_of_stock_in_trade", "changes_in_inventories"),),
              guard=_cogs_material,
              num_label="Cost of Materials + Purchases + Inventory Change",
              den_label="Revenue from Operations",
              ideal="lower is generally favourable; compare with industry norm"),
    RatioSpec("operating_expenses_ratio", "Operating Expenses Ratio", "Profitability-Sales",
              "(Other Expenses (Administrative + Selling & Distribution) / Revenue from "
              "Operations) x 100",
              num=lambda x: x["sga_expenses"], den=lambda x: x["revenue"], scale=100.0, unit="%",
              required=("sga_expenses", "revenue"),
              num_label="Other Expenses (Selling, Administrative & General)",
              den_label="Revenue from Operations",
              ideal="lower is generally favourable; compare with industry norm"),
    RatioSpec("operating_ratio", "Operating Ratio", "Profitability-Sales",
              "((Cost of Materials Consumed + Purchases of Stock-in-Trade + Changes in "
              "Inventories + Other Expenses (Administrative + Selling & Distribution)) / "
              "Revenue from Operations) x 100",
              num=lambda x: _cogs(x) + x["sga_expenses"], den=lambda x: x["revenue"],
              scale=100.0, unit="%",
              required=("sga_expenses", "revenue"),
              optional=("cost_of_materials_consumed", "purchases_of_stock_in_trade", "changes_in_inventories"),
              any_of=(("cost_of_materials_consumed", "purchases_of_stock_in_trade", "changes_in_inventories"),),
              guard=_cogs_material,
              num_label="COGS + Other Expenses", den_label="Revenue from Operations",
              ideal="lower is generally favourable; compare with industry norm"),
    RatioSpec("financial_expenses_ratio", "Financial Expenses Ratio", "Profitability-Sales",
              "(Finance Costs / Revenue from Operations) x 100",
              num=lambda x: x["finance_costs"], den=lambda x: x["revenue"], scale=100.0, unit="%",
              required=("finance_costs", "revenue"),
              num_label="Finance Costs", den_label="Revenue from Operations",
              ideal="lower is generally favourable; compare with industry norm"),

    # -- 6. Profitability (return on investment) ---------------------------
    RatioSpec("roi", "Return on Investment (ROI)", "Profitability-Return",
              "(Return / Investment) x 100",
              num=lambda x: x["return_amount"], den=lambda x: x["investment"], scale=100.0, unit="%",
              required=("return_amount", "investment"),
              num_label="Return", den_label="Investment", ideal="> cost of capital",
              mandatory_sch3=True),
    RatioSpec("roa", "Return on Assets (ROA)", "Profitability-Return",
              "[Profit for the Period (+ Finance Costs)] / Average Total Assets x 100",
              num=lambda x: x["pat"] + x.get("finance_costs", 0.0), den=lambda x: x["avg_total_assets"],
              scale=100.0, unit="%",
              required=("pat", "avg_total_assets"), optional=("finance_costs",),
              num_label="Profit for the Period + Finance Costs", den_label="Avg Total Assets", ideal="higher is better"),
    RatioSpec("roce", "Return on Capital Employed (ROCE)", "Profitability-Return",
              "(EBIT / Capital Employed) x 100,  Capital Employed = Total Assets - Current Liabilities "
              "= Property, Plant and Equipment + Working Capital = Equity + Long-term Borrowings",
              num=lambda x: x["ebit"], den=_capital_employed, scale=100.0, unit="%",
              required=("ebit", "total_assets", "current_liabilities"),
              num_label="EBIT", den_label="Capital Employed", ideal="> cost of borrowing",
              mandatory_sch3=True),
    RatioSpec("roe", "Return on Equity (ROE)", "Profitability-Return",
              "[(Profit for the Period - Preference Dividend) / Total Equity (Equity Share Capital + Other Equity)] x 100",
              num=lambda x: x["pat"] - x.get("preference_dividend", 0.0), den=lambda x: x["total_equity"],
              scale=100.0, unit="%",
              required=("pat", "total_equity"), optional=("preference_dividend",),
              num_label="Profit for the Period - Pref Dividend", den_label="Total Equity", ideal="higher is favourable",
              mandatory_sch3=True),

    # -- 7. Profitability (owner's point of view) --------------------------
    RatioSpec("eps", "Earnings Per Share (EPS)", "Profitability-Owner",
              "Profit for the Period Available to Equity Shareholders / Number of Equity Shares Outstanding",
              num=lambda x: x["pat"] - x.get("preference_dividend", 0.0),
              den=lambda x: x["num_equity_shares"], unit="Rs",
              required=("pat", "num_equity_shares"), optional=("preference_dividend",),
              num_label="Profit for the Period to Equity", den_label="No. Equity Shares", ideal="higher; track trend"),
    RatioSpec("dps", "Dividend Per Share (DPS)", "Profitability-Owner",
              "Total Dividend Paid to Equity Shareholders / Number of Equity Shares Outstanding",
              num=lambda x: x["total_dividend"], den=lambda x: x["num_equity_shares"], unit="Rs",
              required=("total_dividend", "num_equity_shares"),
              num_label="Total Dividend Paid", den_label="No. Equity Shares", ideal="per dividend policy"),
    RatioSpec("dividend_payout", "Dividend Payout Ratio", "Profitability-Owner",
              "Dividend Per Share (DPS) / Earnings Per Share (EPS)",
              num=_dps_calc, den=_eps_calc,
              required=("total_dividend", "num_equity_shares", "pat"), optional=("preference_dividend",),
              num_label="DPS", den_label="EPS", ideal="depends on policy"),

    # -- 8. Market / Valuation (SHIP DISABLED: needs external market price;
    #       the sheet itself flags these "Not individually traceable" to a
    #       Schedule III line — quoted price comes from the exchange, not the
    #       annual report) --
    RatioSpec("pe_ratio", "Price-Earnings (P/E)", "Market",
              "Market Price per Share / EPS",
              num=lambda x: x["market_price_per_share"], den=_eps_calc,
              required=("market_price_per_share", "pat", "num_equity_shares"), optional=("preference_dividend",),
              num_label="Market Price/Share", den_label="EPS", ideal="vs sector avg",
              enabled=False, disabled_reason="needs quoted market price (external to annual report)"),
    RatioSpec("dividend_yield", "Dividend Yield", "Market",
              "(DPS / Market Price per Share) x 100",
              num=_dps_calc, den=lambda x: x["market_price_per_share"], scale=100.0, unit="%",
              required=("total_dividend", "num_equity_shares", "market_price_per_share"),
              num_label="DPS", den_label="Market Price/Share", ideal="vs FD/peer yields",
              enabled=False, disabled_reason="needs quoted market price"),
    RatioSpec("earnings_yield", "Earnings Yield", "Market",
              "(EPS / Market Price per Share) x 100",
              num=_eps_calc, den=lambda x: x["market_price_per_share"], scale=100.0, unit="%",
              required=("pat", "num_equity_shares", "market_price_per_share"), optional=("preference_dividend",),
              num_label="EPS", den_label="Market Price/Share", ideal="vs market/bond yields",
              enabled=False, disabled_reason="needs quoted market price"),
    RatioSpec("mv_bv", "Market Value / Book Value", "Market",
              "Market Price per Share / Book Value per Share (Total Equity / No. of Equity Shares)",
              num=lambda x: x["market_price_per_share"], den=lambda x: x["total_equity"] / x["num_equity_shares"],
              required=("market_price_per_share", "total_equity", "num_equity_shares"),
              num_label="Market Price/Share", den_label="Book Value/Share", ideal="> 1",
              enabled=False, disabled_reason="needs quoted market price"),
    RatioSpec("tobins_q", "Q Ratio (Tobin's Q)", "Market",
              "(Market Value of Equity + Liabilities) / Estimated Replacement Cost of Assets",
              num=lambda x: x["market_value_equity"] + x["market_value_debt"],
              den=lambda x: x["replacement_cost_assets"],
              required=("market_value_equity", "market_value_debt", "replacement_cost_assets"),
              num_label="MV Equity + Debt", den_label="Replacement Cost", ideal="~ 1",
              enabled=False, disabled_reason="needs market values + replacement-cost estimate"),

    # -- 9. FDR Appendix G ---------------------------------------------------
    # The FDR Audit Planning Intelligence Specification v3, Appendix G — Formula library,
    # component-mapped in `formulas/FDR ratios mapping.xlsx`. Nine of its sixteen
    # diagnostics were already satisfied by sheet-sourced specs above and are NOT
    # duplicated here (Appendix G no. -> catalog key):
    #     1 current_ratio · 2 quick_ratio · 3 debt_to_equity · 4 interest_coverage
    #     5 net_profit_margin · 8 roce · 9 total_assets_turnover
    #     10 days_sales_outstanding · 12 days_payable_outstanding
    # `source="appendix-g"` keeps the Excel-sourced catalog CLOSED (test_computations
    # asserts its count exactly), exactly as `source="srs-9.2"` does.
    #
    # Appendix G 6 — Return on equity. Appendix G and the mapping sheet's Legend both
    # define ROE on AVERAGE equity ("require BOTH the current year and prior year Balance
    # Sheet"); the sheet-sourced `roe` above uses CLOSING equity and is the Schedule III
    # mandatory disclosure, so both ship, under different names and formula strings, per
    # the "TWO TURNOVER RATIOS, NOT ONE" precedent. No preference-dividend term: Appendix
    # G does not carry one, and `roe` already covers that variant.
    RatioSpec("roe_avg_equity", "Return on Equity (ROE, average equity basis)",
              "Profitability-Return",
              "(Profit after Tax / Average Equity) x 100   [FDR Appendix G no. 6]",
              num=lambda x: x["pat"], den=lambda x: x["avg_equity"],
              scale=100.0, unit="%", required=("pat", "avg_equity"),
              num_label="Profit after Tax", den_label="Average Equity",
              ideal="higher is favourable", source="appendix-g"),

    # Appendix G 7 — DuPont ROE. Reconciles to `roe_avg_equity` by construction (see
    # `_dupont_factors`); the diagnostic value is the three factors, which the trace_fn
    # exposes, and which is what lets Layer 3 say WHY a return moved rather than that it did.
    RatioSpec("dupont_roe", "DuPont ROE (decomposed)", "Profitability-Return",
              "Net Margin x Asset Turnover x Equity Multiplier   [FDR Appendix G no. 7]",
              num=lambda x: x["pat"], den=lambda x: x["avg_equity"],
              scale=100.0, unit="%",
              required=("pat", "revenue", "avg_total_assets", "avg_equity"),
              num_label="Profit after Tax", den_label="Average Equity",
              ideal="decompose before interpreting: a leverage-driven rise is not a "
                    "profitability gain", trace_fn=_dupont_trace, source="appendix-g"),

    # Appendix G 3 (broad reading) — Debt-Equity on the FIXED total-debt definition.
    # `debt_to_equity` above keeps the borrowings-only numerator because that is the
    # Schedule III mandatory disclosure. The mapping sheet's component breakdown for
    # Appendix G no. 3 lists four numerator items, of which that covers two, so this
    # ships the full definition beside it rather than silently redefining a statutory
    # ratio. Measured on ONGC 2024-25: 0.0266x borrowings-only against 0.1196x here.
    RatioSpec("debt_to_equity_total_debt", "Debt-to-Equity (total debt basis)", "Leverage",
              "Total Debt (Long-term + Short-term Borrowings + Current Maturities of "
              "Long-term Debt + Lease Liabilities) / Total Equity   [FDR Appendix G no. 3]",
              num=_total_debt, den=lambda x: x["total_equity"],
              required=("total_equity",),
              optional=("long_term_borrowings", "short_term_borrowings",
                        "current_maturities_ltd", "lease_liabilities_nc",
                        "lease_liabilities_cl"),
              any_of=(("long_term_borrowings", "short_term_borrowings",
                       "current_maturities_ltd", "lease_liabilities_nc",
                       "lease_liabilities_cl"),),
              num_label="Total Debt", den_label="Total Equity",
              ideal="compare only against entities measured on the same definition",
              source="appendix-g"),

    # Appendix G 11 — Inventory days. The day-count companion to the existing
    # `inventory_turnover`, carrying the same COGS-decomposition guard: where a filing
    # books cost of sales under an industry-specific head, this abstains rather than
    # report a day-count built on a rounding error.
    RatioSpec("inventory_days", "Inventory Days", "Turnover",
              "Average Inventory / Cost of Goods Sold x 365   [FDR Appendix G no. 11]",
              num=lambda x: x["avg_inventory"], den=_cogs, scale=float(DAYS_IN_YEAR),
              unit="days", required=("avg_inventory",),
              optional=("cost_of_materials_consumed", "purchases_of_stock_in_trade",
                        "changes_in_inventories"),
              any_of=(("cost_of_materials_consumed", "purchases_of_stock_in_trade",
                       "changes_in_inventories"),),
              guard=_cogs_material, den_must_be_positive=True,
              num_label="Avg Inventory", den_label="Cost of Goods Sold",
              ideal="lower is better", source="appendix-g"),

    # Appendix G 13 — Cash-conversion cycle.
    RatioSpec("cash_conversion_cycle", "Cash Conversion Cycle", "Turnover",
              "Receivables Days + Inventory Days - Payables Days   [FDR Appendix G no. 13]",
              num=_ccc, den=lambda _x: 1.0, scale=1.0, unit="days",
              required=("avg_receivables", "revenue", "avg_inventory", "avg_payables"),
              optional=("cost_of_materials_consumed", "purchases_of_stock_in_trade",
                        "changes_in_inventories"),
              any_of=(("cost_of_materials_consumed", "purchases_of_stock_in_trade"),),
              guard=_ccc_guard, trace_fn=_ccc_trace,
              num_label="Cash Conversion Cycle", den_label="",
              ideal="lower/negative is better", source="appendix-g"),

    # Appendix G 14 — Accruals ratio. The first ratio in this catalog to depend on the
    # Cash Flow Statement; `ocf` is trusted only once `cash_flow_reconciles` passes.
    RatioSpec("accruals_ratio", "Accruals Ratio", "Quality",
              "((Profit after Tax - Operating Cash Flow) / Average Total Assets) x 100"
              "   [FDR Appendix G no. 14]",
              num=lambda x: x["pat"] - x["ocf"], den=lambda x: x["avg_total_assets"],
              scale=100.0, unit="%", required=("pat", "ocf", "avg_total_assets"),
              num_label="PAT - Operating Cash Flow", den_label="Avg Total Assets",
              ideal="lower is better; a high or rising figure means profit is not "
                    "cash-backed", source="appendix-g"),

    # Appendix G 15 — Free cash flow. An absolute figure, not a ratio (same shape as the
    # existing `net_working_capital`), so den=1 and the trace_fn replaces the "/ 1.00" line.
    RatioSpec("free_cash_flow", "Free Cash Flow", "Quality",
              "Operating Cash Flow - Capital Expenditure (Payments for PPE + Intangibles "
              "+ sector-specific drilling/CWIP additions)   [FDR Appendix G no. 15]",
              num=lambda x: x["ocf"] - _capex(x), den=lambda _x: 1.0, unit="Rs",
              required=("ocf",),
              optional=("capex_ppe", "capex_intangibles", "capex_exploration"),
              # Without this every capex component optional-defaults to 0 and FCF is
              # reported as equal to OCF — a company that spent nothing on capital. Same
              # fabricated-zero failure the `capital_gearing` guard exists to prevent.
              any_of=(("capex_ppe", "capex_intangibles", "capex_exploration"),),
              trace_fn=_fcf_trace,
              num_label="Operating Cash Flow - Capex", den_label="",
              ideal="positive; negative means investment is funded by borrowing or "
                    "supplier credit", source="appendix-g"),

    # Appendix G 16 — Government-support dependency. The spec itself marks this
    # "indicative", and the mapping sheet's Legend warns the three numerator components
    # are "frequently scattered across different notes ... each must be individually
    # confirmed rather than assumed nil by omission."
    #
    # So: `any_of` forces an ABSTAIN when no grant line binds — reporting 0% would assert
    # "this entity receives no government support", which is exactly the assumption the
    # Legend forbids — and the `caveat` attaches a mandatory partial-coverage warning
    # naming what was and was not confirmed whenever it DOES compute.
    RatioSpec("govt_support_dependency", "Government-Support Dependency (indicative)",
              "Public-Sector Lens",
              "((Grants + Subsidies + Budgetary Support) / Total Income) x 100"
              "   [FDR Appendix G no. 16 — INDICATIVE]",
              num=lambda x: abs(x.get("government_grant_cf", 0.0)),
              den=lambda x: x["total_income"], scale=100.0, unit="%",
              required=("total_income",), optional=("government_grant_cf",),
              any_of=(("government_grant_cf",),),
              num_label="Government Grants (confirmed components)", den_label="Total Income",
              ideal="interpret against the business model, never as a conclusion about "
                    "impropriety", source="appendix-g",
              caveat="PARTIAL COVERAGE — the numerator includes only the government-grant "
                     "components this engine could confirm from the face of the statements "
                     "(see `government_grant_cf` in the inputs). Appendix G's three "
                     "components (grants recognised in P&L, sector subsidies, budgetary "
                     "support / grants-in-aid) are typically disclosed across several "
                     "notes, and no Notes binder exists yet, so this is a FLOOR on "
                     "dependency, not a measure of it. Confirm each component against the "
                     "notes before drawing any planning inference."),
]

CATALOG_BY_KEY = {s.key: s for s in CATALOG}

# Appendix G no. -> the catalog key that satisfies it. Exposed so a caller can render the
# sixteen-row Appendix G set as a closed set (like `schedule3.analyse` does for the eleven
# statutory ratios), and so the tests can assert the mapping stays complete.
APPENDIX_G: tuple[tuple[int, str, str], ...] = (
    (1,  "Current ratio",                    "current_ratio"),
    (2,  "Quick ratio",                      "quick_ratio"),
    (3,  "Debt-equity",                      "debt_to_equity"),
    (4,  "Interest coverage",                "interest_coverage"),
    (5,  "Net profit ratio",                 "net_profit_margin"),
    (6,  "Return on equity (ROE)",           "roe_avg_equity"),
    (7,  "DuPont ROE",                       "dupont_roe"),
    (8,  "Return on capital employed",       "roce"),
    (9,  "Asset turnover",                   "total_assets_turnover"),
    (10, "Receivables days (DSO)",           "days_sales_outstanding"),
    (11, "Inventory days",                   "inventory_days"),
    (12, "Payables days",                    "days_payable_outstanding"),
    (13, "Cash-conversion cycle",            "cash_conversion_cycle"),
    (14, "Accruals ratio",                   "accruals_ratio"),
    (15, "Free cash flow",                   "free_cash_flow"),
    (16, "Government-support dependency",    "govt_support_dependency"),
)

# The eleven Schedule III MANDATORY note-disclosure ratios (Notes sheet, item 1),
# which also require an explanation for any >=25% YoY variance.
MANDATORY_SCH3 = [s.key for s in CATALOG if s.mandatory_sch3]
SCH3_VARIANCE_THRESHOLD = 0.25


# --------------------------------------------------------------------------- public API
def run_ratios(inputs: dict, keys: list[str] | None = None,
               include_disabled: bool = False) -> list[Computation]:
    """Compute a set of ratios from canonical `inputs`. Default = every enabled
    spec (skips ABSTAINs? no — abstains are RETURNED, they are the audit trail)."""
    specs = [CATALOG_BY_KEY[k] for k in keys] if keys else CATALOG
    return [compute(s, inputs) for s in specs
            if include_disabled or s.enabled]


def blocking_inputs(spec: RatioSpec) -> list[str]:
    """The inputs that stop `spec` from EVER computing: any required key, or any whole
    `any_of` group, that no binder can currently supply (INPUT_SOURCE != FACE). Empty
    list = the spec can fire whenever the filing actually discloses its lines.

    Optional keys are excluded on purpose — they default to 0 with a caveat note, so a
    missing one degrades the figure but never blocks it."""
    out = [k for k in spec.required if INPUT_SOURCE.get(k, NOTE) != FACE]
    for grp in spec.any_of:
        if not any(INPUT_SOURCE.get(k, NOTE) == FACE for k in grp):
            out.append("any_of(" + " | ".join(grp) + ")")
    return out


def unreachable_specs(include_disabled: bool = False) -> dict[str, list[str]]:
    """key -> blocking inputs, for every spec that cannot compute for ANY entity.

    This is a property of the SYSTEM, not of a filing: it says which catalog entries are
    inert until a note / cash-flow / SoCE binder exists (or, for `credit_sales` and
    `credit_purchases`, until someone accepts a proxy the sheet itself declines to make).
    Call it to caveat a report honestly, and see `MANDATORY_SCH3_UNREACHABLE` for the
    subset that Schedule III requires an entity to disclose."""
    return {s.key: b for s in CATALOG
            if (include_disabled or s.enabled) and (b := blocking_inputs(s))}


# The Schedule III ratios an entity is REQUIRED to disclose that this engine nonetheless
# cannot compute — the highest-priority gap in the catalog, since these are exactly the
# figures a reviewer will look for. DSCR and Fixed Charges Coverage need the borrowings
# maturity / cash-flow financing lines; Receivables and Payables Turnover need the credit
# split Schedule III never discloses; ROI needs the analyst to name Return and Investment.
MANDATORY_SCH3_UNREACHABLE = [k for k in MANDATORY_SCH3 if k in unreachable_specs()]


def run_mandatory(inputs: dict) -> list[Computation]:
    """The 11 Schedule III mandatory ratios only."""
    return run_ratios(inputs, keys=MANDATORY_SCH3)


def variance_flag(current: Computation, prior_value: float | None,
                  threshold: float = SCH3_VARIANCE_THRESHOLD) -> dict | None:
    """Schedule III rule: a mandatory ratio moving >= `threshold` (25%) YoY needs
    a disclosed explanation. Pure arithmetic; returns a flag dict or None."""
    if current.result is None or prior_value in (None, 0):
        return None
    change = (current.result - prior_value) / abs(prior_value)
    if abs(change) < threshold:
        return None
    return {"key": current.key, "name": current.name,
            "prior": prior_value, "current": current.result,
            "change_pct": round(change * 100, 2),
            "trace": f"({current.result} - {prior_value}) / |{prior_value}| = {change:+.1%}",
            "requires": "Schedule III explanation for >=25% movement"}
    
    

"""
Deterministic financial-ratio catalog — sourced from
`formulas/Final Ratio Calulation chart - final detailed.xlsx` (the precision pass
over the original `Financial_Ratio_Analysis_Reference_Table.xlsx`).

THE FORMULAS ARE PYTHON, NOT THE LLM. Every ratio here is a pure function over a
dict of *canonical inputs*: same inputs -> same number, forever. Each result is a
`Computation` carrying its formula string, the exact input figures, a per-input
SOURCE TRACE (which statement / major head / sub-head / line item each figure
comes from), and the arithmetic trace — so a reviewer can see EXACTLY which line
item was taken and how it ends up in the calculation, without rerunning a model.
A formula whose inputs are missing / ambiguous / zero-denominator returns
status="ABSTAIN" (queued for a human) — it never guesses (finance-core rule).

WHY A SECOND EXCEL PASS
-----------------------
The original sheet used generic finance-textbook wording ("Sales", "Interest",
"Net Profit", "Sundry Debtors"). The new sheet re-labels every input to the
EXACT term a Schedule III / Ind AS filing actually prints ("Revenue from
Operations", "Finance Costs", "Profit for the Period", "Trade Receivables") and
adds an explicit per-input trace (Financial Statement / Major Head / Sub-Head /
Line Item). Two things fall out of that precision pass, not cosmetic renames:

  1. COGS is not a face line in Schedule III format. The new sheet decomposes
     it into the three lines that DO exist: Cost of Materials Consumed +
     Purchases of Stock-in-Trade + Changes in Inventories. Every ratio that used
     a monolithic `cogs` input now derives it via `_cogs()` from those three
     (at least one must be present, via `any_of` — see `compute()`).
  2. "Total Debt" is renamed "Total Borrowings" and decomposed the same way:
     Long-term Borrowings (non-current liabilities) + Short-term Borrowings
     (current liabilities), via `_total_borrowings()`.

  Two ratios (credit-sales-based Receivables/Payables Turnover) are explicitly
  flagged by the sheet as "not separately disclosed — estimate/Notes": Schedule
  III doesn't split credit vs cash sales/purchases on the face, so those inputs
  stay unbound and those ratios correctly keep abstaining — this is NOT a gap to
  fix, it is the sheet's own honesty, kept as-is rather than papered over with a
  fragile revenue-as-proxy guess.

ONE SHEET ROW THAT IS A TEMPLATE, NOT A SINGLE RATIO (S.No 29)
--------------------------------------------------------------
Every other sheet row is one ratio -> one spec. S.No 29 "Expense Ratios (COGS /
Operating Expenses / Operating / Financial Expenses Ratio)" is a template: its
formula cell is "Respective Expense / Revenue from Operations x 100" and its own
name enumerates FOUR expense heads, with the Components column naming each one
(COGS decomposition, Other Expenses (Administrative), Other Expenses (Selling &
Distribution), Finance Costs, Revenue from Operations). It therefore expands to
the four specs `cogs_ratio` / `operating_expenses_ratio` / `operating_ratio` /
`financial_expenses_ratio` — the heads the sheet itself lists, nothing invented.
The sheet traces BOTH "Other Expenses (Administrative)" and "Other Expenses
(Selling & Distribution)" to the same printed line (Statement of Profit & Loss ->
Expenses -> Other Expenses), because Schedule III prints only that one aggregate
and the admin/selling split is note-level; so `operating_expenses_ratio` uses the
single combined `sga_expenses` line rather than two keys no filing can bind
separately. (`operating_expenses_ratio` is the "Operating Expenses Ratio" head;
`operating_ratio` is the "Operating" head = COGS + Other Expenses over revenue.)

KNOWN LIMITATION — the COGS-decomposition proxy can understate true cost
--------------------------------------------------------------------------
`_cogs()` sums only three P&L lines (Cost of Materials Consumed / Purchases of
Stock-in-Trade / Changes in Inventories). Some industries (extraction, utilities,
services) report their real cost of sales under an industry-specific line the
decomposition doesn't capture — confirmed live on ONGC, where the true cost line
is "Production, transportation, selling, distribution and other expenditure",
outside all three canonical lines. This silently understates `_cogs()`, which:
  - can push `basic_defense_interval`'s denominator negative — GUARDED (see
    `den_must_be_positive` on that spec): it abstains rather than report a
    nonsensical negative day-count.
  - inflates `gross_profit_margin` toward 100% (observed 99.45% on ONGC) — NOT
    currently guarded. A broader plausibility check (comparing summed COGS
    against `total_expenses`) was considered and deliberately deferred: it needs
    a threshold, and risks false-abstaining genuinely low-COGS companies
    (services-heavy PSUs) where a small COGS-to-total-expense ratio is normal,
    not a data gap. Flagging here so this doesn't get rediscovered as a surprise.

WHERE THE SHEET CONTRADICTS ITSELF — "Net Assets" (S.No 6, 7)
--------------------------------------------------------------
The sheet names this input "Net Assets (Total Assets – Current Liabilities)" and its
Derived trace row gives the source as "Balance Sheet (Total Assets – Current Liabilities)",
but the Components PROSE for the same two ratios glosses it as "PPE (Net Block) + Net
Current Assets". Those agree only if PPE is the whole non-current side. `_net_assets()`
follows the NAME and the TRACE ROW (TA - CL) — see its docstring for why, and note that
`_capital_employed()` computes the same sheet concept the same way, which it did not
before. The prose reading put ONGC's Equity Ratio at 1.5427, i.e. above 1 against the
sheet's own "~0.5+" ideal.

TWO TURNOVER RATIOS, NOT ONE (receivables / payables)
------------------------------------------------------
Schedule III's mandatory Trade Receivables / Payables Turnover ratios are defined on NET
CREDIT sales and purchases, and Schedule III discloses the credit split nowhere — face or
notes. Proxying it with total revenue is not a rounding convenience: it asserts "every
sale was on credit", which for a PSU selling to government offtakers may be nearly true or
wildly false, with nothing in the statements to say which. Reporting that under a
MANDATORY-disclosure label would put a fabricated number where a reviewer reasonably reads
the entity's own.

So the catalog carries BOTH, and keeps them apart by name, by formula string and by flag:
  - `receivables_turnover` / `payables_turnover` (+ their day-count companions) are the
    Schedule III mandates, `mandatory_sch3=True`, and ABSTAIN until a Notes binder can read
    the entity's own disclosed numerator and denominator.
  - `receivables_turnover_revenue` / `payables_turnover_purchases` (+ DSO / DPO) are the
    SRS §9.2 ANALYTICAL indicators — §9.2 asks for "Receivables, inventory, payables, asset
    turnover" and says nothing about a credit split — computed on total flows, named and
    formula-stringed as exactly that, `source="srs-9.2"`, never `mandatory_sch3`.
When the Notes binder lands these become the cross-check against the disclosed ratio rather
than a substitute for it, which is the more valuable audit artefact anyway.

ROI IS LEFT ABSTAINING ON PURPOSE (S.No 30)
--------------------------------------------
The sheet defines ROI as "(Return / Investment) x 100" with Investment = "Total Assets /
Net Assets / Capital Employed / Equity, as applicable" — i.e. it hands the choice to the
engagement. Binding it to EBIT / Capital Employed would make it byte-identical to `roce`,
and two identical numbers under different names in an audit memo is worse than one blank,
because the reader assumes they measure different things. `return_amount` / `investment`
are therefore tiered PARAM, and the abstain says "an analyst-specified parameter, not a
reported line item" instead of implying the figures were missing from the filing.

RATIOS THAT CAN NEVER FIRE, AND SAYING SO OUT LOUD
---------------------------------------------------
A spec abstains when an input is missing, but "missing" covers two very different things:
this filing didn't disclose the figure, or nothing in this package can ever supply it.
12 of the 39 enabled specs are the second kind — their inputs live in Notes, the Cash
Flow Statement or the Statement of Changes in Equity, none of which has a binder — and
that includes 4 of the 11 Schedule III MANDATORY ratios. `INPUT_SOURCE` declares the tier
of every canonical input and `unreachable_specs()` / `MANDATORY_SCH3_UNREACHABLE` derive
the consequence, so a report can caveat the gap instead of implying a disclosure failure
by the entity. Reporting a capability gap as a data gap is the same error the `any_of`
rule already guards against, one level up.

HOW THE EXCEL WAS INTERPRETED FOR PYTHON
-----------------------------------------
Each ratio row maps to: CANONICAL INPUTS (stable machine keys the binding layer
fills) -> num(inputs)/den(inputs) (derivations like Gross Profit = Revenue -
COGS live inside num/den) -> a scale (1 = x/times, 100 = %, 365 = days). Each
canonical input's SOURCE TRACE (statement/major head/sub head/line item) is kept
once in `INPUT_TRACE`, not repeated per ratio, and attached to every `Computation`
(success or abstain) so the UI can show exactly what was being looked for.

This module is self-contained (stdlib only) and imports nothing from `fs_db` or
`rag`, so both the fs_db flow and rag/company_qa can call it.
"""