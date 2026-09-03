"""
Every threshold the signal rules use, in one place, each with its basis.

WHY A SEPARATE MODULE
---------------------
A threshold is an audit-methodology decision wearing a number's clothes. Buried in a rule
it looks like arithmetic and nobody reviews it; gathered here it is a table the audit side
can read, challenge and version — which is what §16 (explainability) and §18.3
(reproducibility) actually require.

Three properties every entry must have:

  SOURCED     `basis` names the specification sentence or the reason the number exists.
              Where the spec states no number, `origin=PROPOSED` says so plainly rather
              than implying the document set it.
  VERSIONED   `VERSION` travels on every FDR run manifest. A threshold change changes what
              the system flags across every entity, so a run must record which set it used.
  RELATIVE    thresholds are ratios, shares and relative movements — never absolute rupee
              amounts. An absolute cut-off would mean one thing for a 500-crore entity and
              something else entirely for ONGC.

WHAT A THRESHOLD IS NOT
-----------------------
It is not a materiality level, and it does not decide whether something matters. It
decides whether a DIAGNOSTIC FIRES. Materiality, severity and priority are applied later,
by the layers built for them (§10.6, §11).
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any

VERSION = "fdr-thresholds-1.0.0"

SPEC = "SPEC"           # the specification states this number
PROPOSED = "PROPOSED"   # drafted here, pending audit sign-off


@dataclass(frozen=True)
class Threshold:
    key: str
    value: float
    unit: str            # "ratio" | "share" | "pct_points" | "days" | "x"
    basis: str
    origin: str = PROPOSED

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_T: tuple[Threshold, ...] = (
    # ---- §9.5 / general movement ----------------------------------------------
    Threshold("material_movement", 0.25, "ratio",
              "§9.5 and the FSA spec's >25% movement trigger: a year-on-year movement "
              "above a quarter is the point at which a change stops being noise and is "
              "asked to explain itself.", SPEC),

    # ---- S01 cash-conversion cycle ---------------------------------------------
    Threshold("ccc_deterioration_days", 15.0, "days",
              "A cash-conversion cycle lengthening by more than a fortnight across the "
              "series is a working-capital change of audit interest. §9.2 requires "
              "working capital be read as an interacting system, not line by line."),

    # ---- S02 payables funding growth --------------------------------------------
    Threshold("payables_growth_gap", 0.15, "ratio",
              "Trade payables growing more than 15 percentage points faster than BOTH "
              "revenue and the receivables-plus-inventory they fund. Requiring both is "
              "what separates supplier funding from a general expansion of the cycle."),
    Threshold("payables_days_rise", 15.0, "days",
              "Payables days lengthening by more than a fortnight across the window. Set "
              "level with the cash-conversion-cycle band so the two signals cannot "
              "disagree about what counts as a material movement in days."),
    Threshold("working_capital_days_flat", 10.0, "days",
              "DSO and inventory days are treated as flat when each moves less than ten "
              "days. Without a flatness band the second S02 test could never fire, since "
              "no real working-capital leg is ever exactly still."),

    # ---- S03 net current liabilities -------------------------------------------
    Threshold("current_ratio_floor", 1.0, "x",
              "§7 liquidity structure: a net current-liability position exists when "
              "current liabilities exceed current assets. The threshold IS the "
              "definition, not a tuning parameter.", SPEC),

    # ---- S04 operating cash flow ------------------------------------------------
    Threshold("ocf_to_pat_weak", 0.80, "ratio",
              "Operating cash flow persistently below four-fifths of reported profit is "
              "the §9.1 cash-conversion concern. Read against the business model, never "
              "mechanically — which is why the signal is suppressed for EPC."),

    # ---- S05 receivables vs revenue ---------------------------------------------
    Threshold("receivables_growth_gap", 0.15, "ratio",
              "Receivables growing more than 15 percentage points faster than revenue "
              "over the window. App D names the signal; the gap is proposed."),

    # ---- S06 accruals ------------------------------------------------------------
    Threshold("accruals_ratio_high", 0.10, "ratio",
              "(PAT - OCF) / average total assets above 10% marks accruals-heavy "
              "earnings. §9.1 names the accruals ratio and its trend."),

    # ---- S10 non-current-other share ---------------------------------------------
    Threshold("non_current_other_drift", 0.03, "share",
              "The residual non-current-asset bucket gaining more than three percentage "
              "points of the balance sheet across the window. §7 names the rising share; "
              "the band is proposed. Expressed in balance-sheet share, not in rupees, so "
              "it means the same thing for a small body and for a major CPSU."),

    # ---- S11 depreciation vs the asset base ---------------------------------------
    Threshold("implied_depreciation_drift", 0.20, "ratio",
              "The implied depreciation rate FALLING by more than a fifth in relative "
              "terms across the window. Only a fall fires: a rising rate is not the "
              "concern App D names, and would be produced mechanically by the net-block "
              "proxy as an asset base ages."),

    # ---- S13 leverage-driven ROE -------------------------------------------------
    Threshold("roe_improvement", 0.15, "ratio",
              "ROE must actually have improved by a clear margin before asking what "
              "drove it; below this the decomposition has nothing to explain."),
    Threshold("equity_multiplier_share", 0.60, "share",
              "§8.2's test: where more than 60% of the ROE improvement is attributable "
              "to the equity multiplier rather than margin or turnover, the movement is "
              "a solvency story, not a performance one."),
    Threshold("operating_drift_tolerance", 0.05, "ratio",
              "Margin and asset turnover are treated as flat when they move less than "
              "5%. Without a flatness band, trivial operating drift would mask a clear "
              "leverage effect."),

    # ---- S14 short-term funding of long-term assets --------------------------------
    Threshold("long_term_funding_shortfall", 0.25, "share",
              "More than a quarter of the increase in non-current assets not carried by "
              "the movement in equity plus non-current borrowings. Expressed against the "
              "asset movement rather than as an amount, so a large entity funding a small "
              "proportion short does not fire ahead of a small entity funding most of it "
              "short."),
    Threshold("short_debt_share", 0.40, "share",
              "Current borrowings, including current maturities of long-term debt, above "
              "two-fifths of total debt. §7 names the reliance on rolling short-term "
              "finance; the band is proposed."),

    # ---- S15 finance cost vs borrowings ------------------------------------------
    Threshold("finance_cost_divergence", 0.20, "ratio",
              "Borrowings and finance cost diverging by more than 20% over the window. "
              "App D names the divergence; the band is proposed."),
    Threshold("borrowings_floor_share", 0.01, "share",
              "Borrowings below 1% of total assets make the ratio meaningless — a tiny "
              "denominator turns rounding into a signal. The rule abstains instead."),
    Threshold("implied_rate_ceiling", 0.30, "ratio",
              "An implied borrowing rate above 30% is not a diagnostic, it is an integrity "
              "failure: no entity borrows at that price, so the finance-cost numerator is "
              "not interest on the borrowings denominator. §4.3 — a failed integrity check "
              "bars a conclusion resting on the affected figure, so the rule abstains and "
              "asks for the finance-cost decomposition rather than reporting the number. "
              "The band is deliberately generous; it is a bound on the impossible, not a "
              "view on what rate is high.", SPEC),
    Threshold("lease_to_borrowings_ratio", 1.00, "x",
              "Where lease liabilities exceed borrowings, the implied-rate denominator is "
              "definitionally wrong: Ind AS 116 interest sits inside finance cost while the "
              "lease liability sits outside the borrowings base. Naming the contamination "
              "in prose is not enough when it is the larger balance."),

    # ---- S18 non-cash gains ------------------------------------------------------
    Threshold("other_income_share_of_pbt", 0.25, "share",
              "Other income exceeding a quarter of profit BEFORE TAX, while operating cash "
              "flow does not follow, is the §9.1 recurring-versus-one-off concern. The "
              "denominator is pre-tax because the numerator is: putting a pre-tax income "
              "item over a post-tax profit mixes the two."),

    # ---- S19 other-income sustainability ------------------------------------------
    Threshold("other_income_share_of_income", 0.10, "share",
              "Other income above a tenth of total income is material enough for its "
              "sustainability to matter (§9.1)."),
    Threshold("other_income_growth", 0.25, "ratio",
              "Other income growing more than 25% faster than total income across the "
              "window — the sustainability question §9.1 asks."),

    # ---- S27 investment income dependency (EXTENSION) -------------------------------
    Threshold("investment_income_share_of_pbt", 0.30, "share",
              "Other income above 30% of profit before tax means a substantial part of the "
              "reported result does not come from the operating business. PROPOSED: the "
              "specification sets no threshold here because it names no such signal."),
    Threshold("investment_income_persistence_years", 2, "count",
              "The share must hold in at least two years of the window before it is raised. "
              "One year above the line is a one-off — the §9.1 distinction the signal exists "
              "to respect — and a single year is exactly what a disposal gain produces."),

    # ---- S20 government-support dependency ----------------------------------------
    Threshold("dependency_index_high", 0.15, "share",
              "Measured government support above a seventh of total income. Appendix G "
              "defines the index; the band is proposed. Note that the index this fires on "
              "is PARTIAL — one of its three components is measurable today — so it "
              "understates, and a non-firing result is not a low-dependency finding."),
    Threshold("dependency_index_rise", 0.05, "share",
              "A five-percentage-point rise in the dependency index across the window. "
              "The lead is a change in the DEGREE of dependency: a grant-funded body is "
              "dependent by design and its existence is not the signal."),
)

BY_KEY: dict[str, Threshold] = {t.key: t for t in _T}
ALL: tuple[Threshold, ...] = _T


def get(key: str) -> float:
    """The value. Raises on an unknown key — a typo must fail loudly, not read as 0.0."""
    try:
        return BY_KEY[key].value
    except KeyError:
        raise KeyError(f"unknown threshold {key!r}; known: {', '.join(sorted(BY_KEY))}") from None


def basis(key: str) -> str:
    return BY_KEY[key].basis


def manifest() -> dict[str, Any]:
    """What travels on the run manifest (§18.3 reproducibility)."""
    return {"version": VERSION,
            "thresholds": {t.key: {"value": t.value, "unit": t.unit, "origin": t.origin}
                           for t in _T}}


def proposed() -> tuple[str, ...]:
    """Thresholds still awaiting audit sign-off — surfaced by `python -m fdr contract --gaps`."""
    return tuple(t.key for t in _T if t.origin == PROPOSED)
