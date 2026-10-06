"""
The catalogue of every auditor-tunable threshold in the FDR, with its default, its
UI bounds and how it should be presented.

DEFAULTS STAY IN CODE
---------------------
Nothing here replaces a hard-coded default. Signal defaults are read from
`xbrl_signal_thresholds`, trend defaults from `TrendThresholds`, and the numbers that
used to be typed inline in Blocks 4/5/6 are declared here as their default. An
auditor's override (see `xbrl_threshold_store`) only ever wins at read time, so
removing it restores exactly what the code shipped with.

UNITS
-----
`default` and stored overrides are in the units the CODE uses (0.25 for 25%, rupees for
a rupee amount). `factor` converts to the units a person reads: display = stored * factor
(100 for a fraction shown as %, 1e-7 for rupees shown as crore, -100 for a negative
"fall" tolerance shown as a positive %). The API speaks display units; this module owns
the conversion and the bounds check, so the UI cannot persist an out-of-range value.

DEAD THRESHOLDS ARE NOT EXPOSED
-------------------------------
`TrendThresholds` declares seven values that no code reads (PPE/INVESTMENTS/EQUITY drift,
TURNOVER_DROP, FINANCE_COST_DIVERGENCE, OCF_PAT_DIVERGENCE, DEPRECIATION_DRIFT). A slider
for a number nothing consults would mislead, so they are left out until they are wired.
`test_xbrl_thresholds.py` fails if a registered key is not actually read by the code.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import xbrl_signal_thresholds as _SIG
from . import xbrl_threshold_store as STORE
from .xbrl_trend_thresholds import TrendThresholds

SLIDER = "slider"
STEPPER = "stepper"


@dataclass(frozen=True)
class Entry:
    key: str
    label: str
    group: str
    used_in: str
    unit: str            # "%", "pp", "x", "days", "₹ cr"
    control: str         # SLIDER | STEPPER
    lo: float            # bounds and step are in DISPLAY units
    hi: float
    step: float
    factor: float
    default: float       # CODE units
    hint: str

    def to_display(self, stored: float) -> float:
        return round(stored * self.factor, 6)

    def to_stored(self, display: float) -> float:
        return display / self.factor


# --- groups (UI order) -----------------------------------------------------------------
G_WC = "Working capital & liquidity"
G_REV = "Receivables & revenue quality"
G_ASSET = "Assets & capitalisation"
G_FUND = "Funding & solvency"
G_EARN = "Earnings quality & estimates"
G_GOV = "Government, contingencies & investments"
GROUP_ORDER = (G_WC, G_REV, G_ASSET, G_FUND, G_EARN, G_GOV)

_ENTRIES: list[Entry] = []


def _pct(key, label, group, used, hint, *, hi=100.0, step=1.0, default=None, factor=100.0, unit="%"):
    _ENTRIES.append(Entry(key, label, group, used, unit, SLIDER, 0.0, hi, step, factor, default, hint))


def _pp(key, label, group, used, hint, *, hi=50.0, step=0.5, default=None, factor=1.0):
    _ENTRIES.append(Entry(key, label, group, used, "pp", SLIDER, 0.0, hi, step, factor, default, hint))


def _step(key, label, group, used, hint, *, unit, hi, step, default, factor=1.0, lo=0.0):
    _ENTRIES.append(Entry(key, label, group, used, unit, STEPPER, lo, hi, step, factor, default, hint))


def _sig(key: str) -> float:
    return _SIG.default_value(key)


_TD = TrendThresholds()   # dataclass defaults, untouched by overrides

# ===== Working capital & liquidity ======================================================
_step("signal.ccc_deterioration_days", "Cash-conversion cycle lengthening", G_WC, "S01",
      "Flag when the cash-conversion cycle lengthens by more than this many days across the window.",
      unit="days", hi=120, step=1, default=_sig("ccc_deterioration_days"))
_pp("signal.payables_growth_gap_pp", "Payables growth ahead of revenue", G_WC, "S02",
    "Trade payables growth minus revenue growth, in percentage points.",
    default=_sig("payables_growth_gap_pp"))
_pct("signal.payables_to_cogs_share", "Payables as a share of COGS", G_WC, "S02",
     "Trade payables above this share of cost of goods sold, combined with the growth gap.",
     default=_sig("payables_to_cogs_share"))
_step("signal.current_ratio_floor", "Current ratio floor", G_WC, "S03",
      "Flag a net current-liability position when the current ratio is below this.",
      unit="x", hi=5, step=0.1, default=_sig("current_ratio_floor"))
_pct("signal.ocf_to_pat_floor", "Operating cash flow vs profit", G_WC, "S04",
     "Flag when operating cash flow is below this share of profit after tax.",
     hi=150, default=_sig("ocf_to_pat_floor"))
_pct("signal.accruals_ratio_ceiling", "Accruals ratio ceiling", G_WC, "S06",
     "(Profit after tax - operating cash flow) / total assets above this.",
     hi=50, step=0.5, default=_sig("accruals_ratio_ceiling"))
_pct("trend.ACCRUALS_RATIO_ALERT", "Accruals alert (trend block)", G_WC, "Trends",
     "Accruals ratio above this in the trend block.", hi=50, step=0.5, default=_TD.ACCRUALS_RATIO_ALERT)
_pct("trend.PAYABLES_GROWTH_TOLERANCE", "Payables outpacing cost (trend block)", G_WC, "Trends",
     "Payables growth ahead of revenue/cost growth by more than this.", hi=100, default=_TD.PAYABLES_GROWTH_TOLERANCE)
_pct("trend.LIQUIDITY_MIX_SHIFT_THRESHOLD", "Liquidity mix shift", G_WC, "Trends",
     "Relative swing between other bank balances and other current financial assets.",
     hi=100, default=_TD.LIQUIDITY_MIX_SHIFT_THRESHOLD)

# ===== Receivables & revenue quality =====================================================
_pp("signal.receivables_growth_gap_pp", "Receivables growth ahead of revenue", G_REV, "S05",
    "Trade receivables growth minus revenue growth, in percentage points.",
    default=_sig("receivables_growth_gap_pp"))
_pct("signal.unbilled_contract_assets_share", "Unbilled contract assets", G_REV, "S07",
     "Unbilled contract assets above this share of annual revenue.", default=_sig("unbilled_contract_assets_share"))
_pct("signal.related_party_receivable_share", "Related-party receivables", G_REV, "S08",
     "Related-party trade debtors above this share of total trade receivables.",
     default=_sig("related_party_receivable_share"))
_pct("risk.rec_rev_flag", "Receivables / revenue (raise)", G_REV, "Risk clusters",
     "Receivables above this share of revenue raises the revenue-quality lead.", default=0.25)
_pct("risk.rec_rev_high", "Receivables / revenue (high severity)", G_REV, "Risk clusters",
     "Above this the lead is rated high severity instead of medium.", default=0.40)
_pct("trend.RECEIVABLE_PACING_TOLERANCE", "Receivables pacing tolerance", G_REV, "Trends",
     "Receivables growth outpacing revenue growth by more than this.", hi=100,
     default=_TD.RECEIVABLE_PACING_TOLERANCE)

# ===== Assets & capitalisation ===========================================================
_pct("signal.cwip_to_assets_share", "CWIP share of total assets", G_ASSET, "S09",
     "Capital work-in-progress above this share of total assets.", default=_sig("cwip_to_assets_share"))
_pp("signal.other_noncurrent_share_rise_pp", "Other non-current assets share rise", G_ASSET, "S10",
    "Rise in other non-current assets' share of total assets across the window.",
    hi=25, default=_sig("other_noncurrent_share_rise_pp"))
_pct("signal.depreciation_rate_fall_ratio", "Depreciation rate fall", G_ASSET, "S11",
     "Implied depreciation rate falling by more than this while the asset base expands.",
     default=_sig("depreciation_rate_fall_ratio"))
_pct("risk.cwip_flag", "CWIP of (PPE + CWIP) (raise)", G_ASSET, "Risk clusters",
     "CWIP above this share of PPE + CWIP raises the capitalisation lead.", default=0.20)
_pct("risk.cwip_high", "CWIP of (PPE + CWIP) (high severity)", G_ASSET, "Risk clusters",
     "Above this the lead is rated high severity.", default=0.40)
_step("risk.cwip_significant_amount", "CWIP amount treated as significant", G_ASSET, "Risk clusters",
      "CWIP above this amount makes the cluster a significant risk, ranked first. Absolute, so scale to the entity.",
      unit="₹ cr", hi=20000, step=50, default=1e10, factor=1e-7)
_pp("trend.NON_CURRENT_OTHER_DRIFT_THRESHOLD", "Other non-current drift", G_ASSET, "Trends",
    "Drift in other non-current assets / total assets between the first and last comparable periods.",
    hi=25, step=0.5, default=_TD.NON_CURRENT_OTHER_DRIFT_THRESHOLD, factor=100.0)
_pp("trend.CWIP_DRIFT_THRESHOLD", "CWIP drift (S09 tile)", G_ASSET, "Trends",
    "Drift in CWIP / total assets between the first and last comparable periods.",
    hi=25, step=0.5, default=_TD.CWIP_DRIFT_THRESHOLD, factor=100.0)
_pp("trend.S09_HIGH_PP", "CWIP drift (high severity)", G_ASSET, "Trends",
    "CWIP drift at or above this is rated high severity.", hi=50, step=0.5, default=10.0)
_pct("health.PRODUCING_PROPS_SHARE", "Producing properties share", G_ASSET, "Health summary",
     "Producing properties at or above this share of assets mark an operating asset base.",
     default=25.0, factor=1.0)
_pct("health.CWIP_HEAVY_SHARE", "CWIP-heavy asset base", G_ASSET, "Health summary",
     "CWIP at or above this share of assets (and above PPE) describes a build-phase entity.",
     default=35.0, factor=1.0)
_pct("health.CWIP_VS_ASSETS", "CWIP-heavy (alternative test)", G_ASSET, "Health summary",
     "Alternative: CWIP above this multiple of total assets.", default=0.4)
_pct("health.PPE_HEAVY_SHARE", "PPE-heavy asset base", G_ASSET, "Health summary",
     "PPE at or above this share of assets describes a fixed-asset-led entity.", default=20.0, factor=1.0)
_pct("health.INV_HEAVY_SHARE", "Investment-heavy asset base", G_ASSET, "Health summary",
     "Investments at or above this share of assets describe an investment-led entity.", default=40.0, factor=1.0)
_pct("health.CA_HEAVY_SHARE", "Current-asset-heavy asset base", G_ASSET, "Health summary",
     "Current assets at or above this share describe a working-capital-led entity.", default=50.0, factor=1.0)
_pct("health.INV_MENTION_SHARE", "Investments worth mentioning", G_ASSET, "Health summary",
     "Investments are named in the narrative only above this share of assets.",
     hi=20, step=0.5, default=1.0, factor=1.0)

# ===== Funding & solvency ================================================================
_step("signal.leverage_de_ceiling", "Debt-to-equity ceiling", G_FUND, "Risk clusters (RC04)",
      "Debt-to-equity above this raises the leverage lead. Undefined on negative equity, where borrowings raise a separate lead.",
      unit="x", hi=10, step=0.1, default=_sig("leverage_de_ceiling"))
_step("risk.de_high", "Debt-to-equity (high severity)", G_FUND, "Risk clusters",
      "Above this the leverage lead is rated high severity.", unit="x", hi=10, step=0.1, default=2.0)
_step("risk.de_significant", "Debt-to-equity (significant risk)", G_FUND, "Risk clusters",
      "Above this the funding cluster is marked a significant risk.", unit="x", hi=10, step=0.1, default=1.5)
_pct("signal.leverage_driven_roe_share", "ROE driven by leverage", G_FUND, "S13",
     "Share of ROE attributable to the equity multiplier in the DuPont split.", default=_sig("leverage_driven_roe_share"))
_pct("signal.short_term_debt_share", "Short-term borrowings share", G_FUND, "S14",
     "Short-term borrowings above this share of total borrowings.", default=_sig("short_term_debt_share"))
_pp("signal.finance_cost_borrowing_divergence_pp", "Finance cost vs borrowings divergence", G_FUND, "S15",
    "Growth of borrowings and of finance costs diverging by more than this.", hi=100, step=1,
    default=_sig("finance_cost_borrowing_divergence_pp"))
_pp("trend.BORROWINGS_DRIFT_THRESHOLD", "Borrowings share drift", G_FUND, "Trends",
    "Drift in borrowings / total assets across the window.", hi=25, step=0.5,
    default=_TD.BORROWINGS_DRIFT_THRESHOLD, factor=100.0)
_pct("trend.DUPONT_LEVERAGE_DOMINANCE", "Leverage dominance in ROE change", G_FUND, "Trends",
     "ROE change counts as leverage-driven when the equity multiplier explains more than this share.",
     default=_TD.DUPONT_LEVERAGE_DOMINANCE)
_pct("trend.MIN_MEANINGFUL_ROE_DELTA", "Smallest ROE change analysed", G_FUND, "Trends",
     "ROE attribution runs only when ROE moves by at least this much.", hi=10, step=0.25,
     default=_TD.MIN_MEANINGFUL_ROE_DELTA)
_pct("trend.COVERAGE_DRIFT_THRESHOLD", "Coverage ratio fall (DSCR / ICR)", G_FUND, "Trends",
     "Flag when debt-service or interest coverage falls by at least this much.", default=_TD.COVERAGE_DRIFT_THRESHOLD,
     factor=-100.0)
_pct("health.DEBT_IMMATERIAL_SHARE", "Borrowings treated as immaterial", G_FUND, "Health summary",
     "Debt below this share of capital is described as immaterial.", hi=50, step=0.5, default=5.0, factor=1.0)

# ===== Earnings quality & estimates ======================================================
_pct("signal.provision_movement_ratio", "Provisions year-on-year move", G_EARN, "S16",
     "Total provisions moving (up or down) by more than this.", default=_sig("provision_movement_ratio"))
_pct("signal.provision_reversal_share_of_pbt", "Provision write-backs vs PBT", G_EARN, "S16",
     "Provisions written back above this share of profit before tax.",
     default=_sig("provision_reversal_share_of_pbt"))
_pct("signal.other_income_to_pbt_share", "Other income vs PBT", G_EARN, "S18",
     "Other income above this share of profit before tax.", default=_sig("other_income_to_pbt_share"))
_pct("signal.other_income_to_revenue_share", "Other income vs revenue", G_EARN, "S19",
     "Other income above this share of revenue.", default=_sig("other_income_to_revenue_share"))
_pp("signal.other_income_growth_gap_pp", "Other income growth ahead of revenue", G_EARN, "S19",
    "Other income growth minus revenue growth, in percentage points.", hi=100, step=1,
    default=_sig("other_income_growth_gap_pp"))
_pct("signal.dividend_income_to_pbt_share", "Dividend income vs PBT", G_EARN, "S27",
     "Treasury / dividend income above this share of profit before tax.", default=_sig("dividend_income_to_pbt_share"))
_pct("risk.oi_flag", "Other income / PBT (raise)", G_EARN, "Risk clusters",
     "Other income above this share of PBT raises the estimate-quality lead.", default=0.30)
_pct("risk.oi_high", "Other income / PBT (high severity)", G_EARN, "Risk clusters",
     "Above this the lead is rated high severity.", default=0.50)
_pct("trend.PROVISION_VOLATILITY_THRESHOLD", "Provision volatility (trend block)", G_EARN, "Trends",
     "Year-on-year change in provisions / impairment above this.", default=_TD.PROVISION_VOLATILITY_THRESHOLD)

# ===== Government, contingencies & investments ===========================================
_pct("signal.government_support_share", "Government-support dependency", G_GOV, "S20",
     "Grants and subsidies above this share of total income.", default=_sig("government_support_share"))
_pct("signal.unspent_grant_growth_ratio", "Unspent-grant build-up", G_GOV, "S21",
     "Growth in unspent grants above this.", default=_sig("unspent_grant_growth_ratio"))
_pct("signal.contingent_liabilities_to_equity_share", "Contingent liabilities vs equity", G_GOV, "S23",
     "Contingent liabilities above this share of equity.", hi=200,
     default=_sig("contingent_liabilities_to_equity_share"))
_pct("signal.guarantees_to_equity_share", "Guarantees vs equity", G_GOV, "S24",
     "Corporate guarantees above this share of net worth.", hi=200, default=_sig("guarantees_to_equity_share"))
_pct("signal.investment_to_assets_share", "Investments share of assets", G_GOV, "S25",
     "Investments above this share of total assets.", default=_sig("investment_to_assets_share"))
_pct("signal.oci_to_equity_share", "OCI swing vs equity", G_GOV, "S26",
     "Other comprehensive income swinging more than this share of opening net worth.",
     default=_sig("oci_to_equity_share"))
_pct("risk.inv_flag", "Investments / assets (raise)", G_GOV, "Risk clusters",
     "Investments above this share of assets raises the concentration lead.", default=0.30)
_pct("risk.inv_high", "Investments / assets (high severity)", G_GOV, "Risk clusters",
     "Above this the lead is rated high severity.", default=0.50)
_step("risk.inv_min_nonoperating", "Non-operating investment floor", G_GOV, "Risk clusters",
      "Investments above this amount, with no PPE or CWIP, also raise the lead. Absolute, so scale to the entity.",
      unit="₹ cr", hi=5000, step=1, default=1e8, factor=1e-7)

# ---- freeze ------------------------------------------------------------------------------
REGISTRY: dict[str, Entry] = {}
for _e in _ENTRIES:
    if _e.key in REGISTRY:
        raise RuntimeError(f"duplicate threshold key {_e.key}")
    if not (_e.lo <= _e.to_display(_e.default) <= _e.hi):
        raise RuntimeError(f"{_e.key}: default {_e.to_display(_e.default)} outside UI bounds [{_e.lo}, {_e.hi}]")
    REGISTRY[_e.key] = _e
del _e


def entry(key: str) -> Entry:
    try:
        return REGISTRY[key]
    except KeyError:
        raise KeyError(f"no threshold registered for {key!r}") from None


def value(key: str) -> float:
    """Effective value in CODE units: the auditor's override if set, else the default.
    This is what every consumer calls; nothing else decides a threshold."""
    ov = STORE.override(key)
    return ov if ov is not None else entry(key).default


def snapshot() -> dict[str, Any]:
    """Everything the UI needs, in display units."""
    ovs = STORE.overrides()
    items = []
    for e in REGISTRY.values():
        cur = ovs.get(e.key, e.default)
        items.append({
            "key": e.key, "label": e.label, "group": e.group, "used_in": e.used_in,
            "unit": e.unit, "control": e.control, "min": e.lo, "max": e.hi, "step": e.step,
            "default": e.to_display(e.default), "value": e.to_display(cur),
            "overridden": e.key in ovs and abs(ovs[e.key] - e.default) > 1e-12, "hint": e.hint,
        })
    m = STORE.meta()
    return {"version": m["version"], "updated_at": m["updated_at"], "updated_by": m["updated_by"],
            "groups": list(GROUP_ORDER), "items": items,
            "overridden_count": sum(1 for i in items if i["overridden"])}


class ThresholdError(ValueError):
    pass


def apply_display_values(values: dict[str, float], actor: str) -> dict[str, Any]:
    """Validate then persist. `values` maps key -> DISPLAY-unit value. A value equal to
    the default removes the override. Unknown keys and out-of-range values are rejected
    for the whole request - nothing is partially applied."""
    problems = []
    cleaned: dict[str, float] = {}
    for key, raw in values.items():
        if key not in REGISTRY:
            problems.append(f"{key}: unknown threshold")
            continue
        e = REGISTRY[key]
        try:
            disp = float(raw)
        except (TypeError, ValueError):
            problems.append(f"{e.label}: not a number")
            continue
        if disp != disp or disp in (float("inf"), float("-inf")):
            problems.append(f"{e.label}: not a finite number")
        elif not (e.lo - 1e-9 <= disp <= e.hi + 1e-9):
            problems.append(f"{e.label}: {disp:g} {e.unit} is outside the allowed range {e.lo:g} to {e.hi:g}")
        else:
            cleaned[key] = e.to_stored(disp)
    if problems:
        raise ThresholdError("; ".join(problems))

    current = STORE.overrides()
    merged = dict(current)
    changes = []
    for key, stored in cleaned.items():
        e = REGISTRY[key]
        before = current.get(key, e.default)
        if abs(stored - e.default) <= 1e-12:
            merged.pop(key, None)
        else:
            merged[key] = stored
        if abs(stored - before) > 1e-12:
            changes.append({"key": key, "label": e.label, "unit": e.unit,
                            "from": e.to_display(before), "to": e.to_display(stored)})
    STORE.save(merged, actor, changes=changes)
    return snapshot()


def reset(keys: list[str] | None, actor: str) -> dict[str, Any]:
    current = STORE.overrides()
    targets = list(current) if not keys else [k for k in keys if k in current]
    changes = [{"key": k, "label": REGISTRY[k].label if k in REGISTRY else k, "unit": REGISTRY[k].unit if k in REGISTRY else "",
                "from": REGISTRY[k].to_display(current[k]) if k in REGISTRY else current[k],
                "to": REGISTRY[k].to_display(REGISTRY[k].default) if k in REGISTRY else None} for k in targets]
    STORE.save({k: v for k, v in current.items() if k not in targets}, actor, changes=changes)
    return snapshot()


def applied_overrides() -> list[dict[str, Any]]:
    """The overrides in force, for stamping onto a report (spec Sec 16: a report must
    say which thresholds produced it)."""
    out = []
    for key, stored in STORE.overrides().items():
        if key in REGISTRY and abs(stored - REGISTRY[key].default) > 1e-12:
            e = REGISTRY[key]
            out.append({"label": e.label, "used_in": e.used_in, "unit": e.unit,
                        "default": e.to_display(e.default), "value": e.to_display(stored)})
    return out
