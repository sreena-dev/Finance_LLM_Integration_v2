"""
Every numeric cutoff the S01-S27 XBRL signal rules use, in one place, each sourced.

Kept independent from `pipeline/fdr/thresholds.py` by design (see `xbrl_signal_model.py`'s
module docstring for why the two FDR paths stay decoupled) — this is the as_db path's own
threshold table, not an import of the fs_db one, even though several values happen to
match because both are reading the same FDR specification.

WHAT A THRESHOLD IS NOT (same discipline as the fs_db path)
-------------------------------------------------------------------------------------
It decides whether a rule FIRES. It never decides materiality (that is
`xbrl_signal_materiality.py`, a separate file per the user's explicit requirement) and it
never decides severity or priority.

Every entry is RELATIVE (a ratio, a share, a percentage-point gap, a day-count) — never
an absolute rupee figure, which would mean one thing for a small entity and another for
a large PSU.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any

VERSION = "xbrl-signal-thresholds-1.0.0"

SPEC = "SPEC"           # the FDR specification states this number
PROPOSED = "PROPOSED"   # drafted from audit practice / the user's supplied plan, pending
                        # audit sign-off — never presented as spec-mandated


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
    # ---- S01 cash-conversion cycle -----------------------------------------------
    Threshold("ccc_deterioration_days", 15.0, "days",
              "CCC lengthening by more than a fortnight across the available window is "
              "the point a working-capital change stops being noise (spec Sec 9.2: working "
              "capital read as an interacting system, not line by line)."),

    # ---- S02 payables funding growth ----------------------------------------------
    Threshold("payables_growth_gap_pp", 15.0, "pct_points",
              "Trade payables growth outpacing revenue growth by more than 15 "
              "percentage points is the point supplier funding of growth becomes "
              "distinguishable from a general expansion of the cycle."),
    Threshold("payables_to_cogs_share", 0.30, "ratio",
              "Trade payables exceeding 30% of COGS, combined with the growth gap above, "
              "is what separates a funding-structure signal from an ordinary credit term."),

    # ---- S03 net current-liability position ----------------------------------------
    Threshold("current_ratio_floor", 1.0, "x",
              "Current ratio below 1.0x is the textbook net current-liability position "
              "(spec Sec 7 structural-liquidity reading)."),

    # ---- S04 weak / negative OCF ---------------------------------------------------
    Threshold("ocf_to_pat_floor", 0.80, "ratio",
              "OCF below 80% of PAT is a cash-conversion shortfall material enough to "
              "warrant a lead, distinct from ordinary working-capital timing noise."),

    # ---- S05 receivables outpacing revenue ------------------------------------------
    Threshold("receivables_growth_gap_pp", 15.0, "pct_points",
              "Receivables growth outpacing revenue growth by more than 15 percentage "
              "points across the window — same gap convention as S02 for consistency."),

    # ---- S06 accruals-heavy earnings ------------------------------------------------
    Threshold("accruals_ratio_ceiling", 0.10, "ratio",
              "(PAT - OCF) / Assets exceeding 10% flags earnings not yet converted to "
              "cash at a scale worth an accruals-quality lead."),

    # ---- S07 period-end / unbilled concentration ------------------------------------
    Threshold("unbilled_contract_assets_share", 0.45, "ratio",
              "Unbilled contract assets exceeding 45% of annual revenue — proxy branch "
              "only; the quarterly-revenue-concentration branch is out of reach from "
              "annual MCA XBRL filings (see xbrl_signal_derivations.py)."),

    # ---- S08 related-party receivable concentration ---------------------------------
    Threshold("related_party_receivable_share", 0.25, "ratio",
              "Related-party trade debtors exceeding 25% of total trade receivables."),

    # ---- S09 CWIP concentration (face ratio only — no ageing schedule in as_db) -----
    Threshold("cwip_to_assets_share", 0.35, "ratio",
              "Capital work-in-progress exceeding 35% of total assets."),

    # ---- S10 rising non-current-other share -----------------------------------------
    Threshold("other_noncurrent_share_rise_pp", 3.0, "pct_points",
              "Other non-current assets' share of total assets rising by more than 3 "
              "percentage points across the window."),

    # ---- S11 depreciation-asset divergence ------------------------------------------
    Threshold("depreciation_rate_fall_ratio", 0.20, "ratio",
              "Implied depreciation rate (depreciation / PPE) falling by more than 20% "
              "while the PPE base itself is expanding."),

    # ---- S13 leverage-driven ROE decomposition ---------------------------------------
    Threshold("leverage_driven_roe_share", 0.60, "ratio",
              "More than 60% of a period's ROE attributable to the equity-multiplier "
              "term of the 3-stage DuPont decomposition, rather than margin or turnover."),

    # ---- S14 short-term funding of long-term assets ----------------------------------
    Threshold("short_term_debt_share", 0.40, "ratio",
              "Short-term borrowings exceeding 40% of total borrowings."),

    # ---- S15 finance-cost / borrowings divergence ------------------------------------
    Threshold("finance_cost_borrowing_divergence_pp", 20.0, "pct_points",
              "Growth rate of total borrowings and of finance costs diverging by more "
              "than 20 percentage points across the window."),

    # ---- S16 provision volatility / reversals ----------------------------------------
    Threshold("provision_movement_ratio", 0.25, "ratio",
              "Total provisions moving (up or down) by more than 25% year on year."),
    Threshold("provision_reversal_share_of_pbt", 0.20, "ratio",
              "Provisions written back exceeding 20% of the magnitude of profit before "
              "tax — a buffering-of-losses read."),

    # ---- S18 non-cash gains dominance -------------------------------------------------
    Threshold("other_income_to_pbt_share", 0.25, "ratio",
              "Other income exceeding 25% of profit before tax while OCF lags PBT."),

    # ---- S19 other-income sustainability ----------------------------------------------
    Threshold("other_income_to_revenue_share", 0.10, "ratio",
              "Other income exceeding 10% of operating revenue."),
    Threshold("other_income_growth_gap_pp", 25.0, "pct_points",
              "Other income growing more than 25 percentage points faster than "
              "operating revenue across the window."),

    # ---- S20 government-support dependency --------------------------------------------
    Threshold("government_support_share", 0.15, "ratio",
              "(Grants + subsidies) exceeding 15% of total income (revenue + other "
              "income) — spec Sec 3.3/9.3 dependency lens."),

    # ---- S21 unspent-grant build-up ----------------------------------------------------
    Threshold("unspent_grant_growth_ratio", 0.20, "ratio",
              "Deferred/unspent government grants growing more than 20% year on year."),

    # ---- S23 contingent liabilities vs net worth ---------------------------------------
    Threshold("contingent_liabilities_to_equity_share", 0.50, "ratio",
              "Total contingent claims exceeding 50% of total equity."),

    # ---- S24 financial guarantees for group entities -------------------------------------
    Threshold("guarantees_to_equity_share", 0.20, "ratio",
              "Corporate guarantees issued exceeding 20% of net worth."),

    # ---- S25 investment concentration in assets ------------------------------------------
    Threshold("investment_to_assets_share", 0.25, "ratio",
              "Non-current and current investments exceeding 25% of total assets."),

    # ---- S26 fair-value OCI volatility -----------------------------------------------------
    Threshold("oci_to_equity_share", 0.15, "ratio",
              "Other comprehensive income swinging more than 15% of opening net worth."),

    # ---- S27 investment income dependency ---------------------------------------------------
    Threshold("dividend_income_to_pbt_share", 0.30, "ratio",
              "Treasury/dividend income exceeding 30% of profit before tax."),
)

_BY_KEY: dict[str, Threshold] = {t.key: t for t in _T}
if len(_BY_KEY) != len(_T):
    raise RuntimeError("duplicate threshold key in xbrl_signal_thresholds")


def get(key: str) -> Threshold:
    try:
        return _BY_KEY[key]
    except KeyError:
        raise KeyError(f"no threshold registered for {key!r}") from None


def all_thresholds() -> tuple[Threshold, ...]:
    return _T
