"""
Hermetic regression over the signal rules. No DB, no model, no network.

    python -m fdr.test_rules

Every rule is a pure function of the panel, so every test here is a constructed panel and
an expected verdict. Three things are checked for each rule, and the third is the one that
matters most:

  FIRES       a panel built to exhibit the condition produces fired=True;
  DOES NOT    a clean panel produces fired=False — a real negative, not an abstain;
  ABSTAINS    a short or unbound panel produces fired=None with a reason, never a guess
              and never a zero substituted for an absence.

Beyond that, the derivation-specific properties: that S06 divides by an AVERAGE, that S18
divides by PBT rather than PAT, that a proxy is DISCLOSED and CAPS confidence, and that
S14 puts current maturities inside the short-debt share. Those are the corrections the
derivation table called for, and a passing rule that quietly reverts one of them would
otherwise look identical to a correct one.
"""
from __future__ import annotations

from . import rules as R
from . import thresholds as TH
from .panel import Panel, PanelCell, MIN_TREND_YEARS

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        _fails.append(msg)


# ---- fixtures ----------------------------------------------------------------------

def panel(series: dict[str, list[float]], years: tuple[str, ...] | None = None,
          *, verdict: str = "CONFIRMED", unit_conf: str = "HIGH") -> Panel:
    """A panel from plain lists, oldest year first.

    Everything is CONFIRMED at a HIGH-confidence scale by default, so a confidence that
    comes back below HIGH in a test is attributable to the rule's own cap and not to the
    fixture.
    """
    n = max(len(v) for v in series.values())
    ys = years or tuple(f"FY{2020 + i}-{str(2021 + i)[-2:]}" for i in range(n))
    cells: dict[tuple[str, str], PanelCell] = {}
    for key, values in series.items():
        for i, v in enumerate(values):
            y = ys[len(ys) - len(values) + i]
            cells[(key, y)] = PanelCell(
                value=float(v), fy_label=y, period_end=f"{y[2:6]}-03-31",
                unit_confidence=unit_conf, verify_verdict=verdict, unit_scale="lakh",
                source_label=key, doc_id="TEST")
    return Panel("TEST", "standalone", ys, cells)


def ran(o: R.Outcome, name: str) -> bool:
    if not o.ran:
        _fails.append(f"{name}: expected the rule to run, it abstained — {o.reason}")
        return False
    return True


# ---- coverage ----------------------------------------------------------------------

def test_every_rule_is_reachable() -> None:
    for sid, fn in R.RULES.items():
        check(callable(fn), f"{sid} maps to something that is not callable")
        check(R.run(sid, panel({"pat": [1, 2, 3]})) is not None,
              f"{sid} returned None from run(), which means no rule is registered")


def test_unknown_signal_returns_none_rather_than_raising() -> None:
    check(R.run("S99", panel({"pat": [1, 2, 3]})) is None,
          "run() on an unknown signal must return None, not raise")


def test_every_rule_abstains_on_an_empty_panel() -> None:
    """P4 — an absence is not a measurement. No rule may treat a missing figure as zero."""
    empty = Panel("TEST", "standalone")
    for sid in sorted(R.RULES):
        o = R.run(sid, empty)
        check(o is not None and not o.ran,
              f"{sid} produced a verdict from an empty panel")
        check(o is not None and bool(o.reason),
              f"{sid} abstained without a reason (§4.4)")


def test_every_rule_abstains_on_a_two_year_panel() -> None:
    """§9.5 — a two-point movement is never presented as a trend."""
    two = panel({k: [100.0, 110.0] for k in (
        "trade_receivables", "inventories", "trade_payables", "revenue",
        "cost_of_materials_consumed", "ocf", "pat", "pbt", "total_assets",
        "total_equity", "other_income", "total_income", "finance_costs",
        "long_term_borrowings", "short_term_borrowings", "depreciation",
        "net_fixed_assets", "other_non_current_assets", "total_non_current_assets")})
    for sid in sorted(R.RULES):
        from . import signals as SG
        if SG.BY_ID[sid].window < MIN_TREND_YEARS:
            continue
        o = R.run(sid, two)
        check(o is not None and not o.ran,
              f"{sid} is a {SG.BY_ID[sid].window}-year trend rule but produced a verdict "
              f"from two years")


# ---- S01 cash-conversion cycle -----------------------------------------------------

def test_s01_fires_on_a_lengthening_cycle() -> None:
    o = R.s01_cash_conversion_cycle(panel({
        "revenue": [1000.0, 1000.0, 1000.0],
        "cost_of_materials_consumed": [600.0, 600.0, 600.0],
        "trade_receivables": [100.0, 150.0, 220.0],     # DSO 36.5 -> 80.3 days
        "inventories": [100.0, 100.0, 100.0],
        "trade_payables": [100.0, 100.0, 100.0],
    }))
    if ran(o, "S01"):
        check(o.fired is True, "S01 did not fire on a receivables-driven lengthening")
        check("DSO" in o.trace and "DPO" in o.trace, "S01 trace omits the legs")


def test_s01_discloses_the_purchases_proxy_and_caps_confidence() -> None:
    """Purchases are not disclosed in an Indian annual report; the stand-in must be said."""
    o = R.s01_cash_conversion_cycle(panel({
        "revenue": [1000.0, 1000.0, 1000.0],
        "cost_of_materials_consumed": [600.0, 600.0, 600.0],
        "trade_receivables": [100.0, 100.0, 100.0],
        "inventories": [100.0, 100.0, 100.0],
        "trade_payables": [100.0, 100.0, 100.0],
    }))
    if ran(o, "S01"):
        check(bool(o.proxies), "S01 substituted a purchases proxy without disclosing it")
        check(o.confidence != R.HIGH,
              "S01 kept HIGH confidence while resting on a purchases proxy")


def test_s01_uses_disclosed_purchases_when_they_exist() -> None:
    o = R.s01_cash_conversion_cycle(panel({
        "revenue": [1000.0] * 3,
        "cost_of_materials_consumed": [600.0] * 3,
        "purchases_of_stock_in_trade": [650.0] * 3,
        "trade_receivables": [100.0] * 3,
        "inventories": [100.0] * 3,
        "trade_payables": [100.0] * 3,
    }))
    if ran(o, "S01"):
        check(not o.proxies,
              "S01 reported a proxy although purchases were disclosed")
        check("as disclosed" in o.trace, "S01 trace does not name the purchases basis")


# ---- S02 payables funding growth ---------------------------------------------------

def test_s02_fires_when_payables_outgrow_both_comparators() -> None:
    o = R.s02_payables_funding_growth(panel({
        "trade_payables": [100.0, 140.0, 200.0],        # +100%
        "revenue": [1000.0, 1020.0, 1050.0],            # +5%
        "trade_receivables": [100.0, 102.0, 105.0],
        "inventories": [100.0, 102.0, 105.0],           # funded WC +5%
        "cost_of_materials_consumed": [600.0, 600.0, 600.0],
    }))
    if ran(o, "S02"):
        check(o.fired is True, "S02 did not fire when payables doubled against flat revenue")


def test_s02_does_not_fire_when_the_whole_cycle_expands_together() -> None:
    """The point of the signal is supplier FUNDING, not a business that simply grew."""
    o = R.s02_payables_funding_growth(panel({
        "trade_payables": [100.0, 150.0, 200.0],
        "revenue": [1000.0, 1500.0, 2000.0],
        "trade_receivables": [100.0, 150.0, 200.0],
        "inventories": [100.0, 150.0, 200.0],
        "cost_of_materials_consumed": [600.0, 900.0, 1200.0],
    }))
    if ran(o, "S02"):
        check(o.fired is False,
              "S02 fired on proportional growth, which is expansion and not funding")


# ---- S03 net current liabilities ---------------------------------------------------

def test_s03_fires_on_a_net_current_liability_position() -> None:
    o = R.s03_net_current_liabilities(panel({
        "total_current_assets": [500.0], "total_current_liabilities": [900.0]}))
    if ran(o, "S03"):
        check(o.fired is True, "S03 did not fire at a current ratio of 0.56")


def test_s03_discloses_current_maturities_without_adding_them() -> None:
    """Under the Ind AS format they are already inside the printed total; adding double-counts."""
    o = R.s03_net_current_liabilities(panel({
        "total_current_assets": [500.0], "total_current_liabilities": [900.0],
        "current_maturities_ltd": [200.0]}))
    if ran(o, "S03"):
        check("not added" in o.trace,
              "S03 does not state that current maturities were left out of the arithmetic")
        check("0.56" in o.trace or "0.56" in o.observation,
              "S03 changed the printed ratio, which means it added the maturities")
        check(bool(o.evidence), "S03 raised the presentation question without an evidence ask")


# ---- S04 operating cash flow -------------------------------------------------------

def test_s04_fires_on_negative_operating_cash_flow() -> None:
    o = R.s04_weak_operating_cash_flow(panel({
        "ocf": [100.0, 50.0, -20.0], "pat": [100.0, 100.0, 100.0]}))
    if ran(o, "S04"):
        check(o.fired is True, "S04 did not fire on a negative operating cash flow")


def test_s04_reports_the_attribution_as_not_formed_when_the_subtotal_is_absent() -> None:
    o = R.s04_weak_operating_cash_flow(panel({
        "ocf": [100.0, 110.0, 120.0], "pat": [100.0, 100.0, 100.0]}))
    if ran(o, "S04"):
        check(o.fired is False, "S04 fired although cash covered profit throughout")
        check(bool(o.proxies),
              "S04 dropped the earnings-versus-working-capital split without saying so")


def test_s04_uses_the_pre_working_capital_subtotal_when_bound() -> None:
    o = R.s04_weak_operating_cash_flow(panel({
        "ocf": [100.0, 50.0, 10.0], "pat": [100.0, 100.0, 100.0],
        "ocf_before_wc": [140.0, 140.0, 140.0]}))
    if ran(o, "S04"):
        check("Before working-capital changes" in o.observation,
              "S04 ignored the pre-working-capital sub-total although it was bound")
        check(not o.proxies, "S04 reported a proxy although the sub-total was available")


# ---- S05 receivables vs revenue ----------------------------------------------------

def test_s05_fires_and_reports_the_dso_trend() -> None:
    o = R.s05_receivables_outpacing_revenue(panel({
        "trade_receivables": [100.0, 150.0, 200.0],
        "revenue": [1000.0, 1020.0, 1050.0]}))
    if ran(o, "S05"):
        check(o.fired is True, "S05 did not fire on receivables doubling against +5% revenue")
        check("DSO" in o.trace, "S05 reports a growth gap without the DSO level behind it")


# ---- S06 accruals ------------------------------------------------------------------

def test_s06_divides_by_average_total_assets() -> None:
    """The derivation says AVERAGE. A closing-balance denominator biases every grower."""
    p = panel({"pat": [100.0, 100.0, 100.0], "ocf": [0.0, 0.0, 0.0],
               "total_assets": [500.0, 700.0, 900.0]})
    o = R.s06_accruals_heavy_earnings(p)
    if ran(o, "S06"):
        # latest year: (100 - 0) / ((700 + 900) / 2) = 12.5%, not 100/900 = 11.1%
        check("12.5%" in o.trace, f"S06 did not use the average denominator — {o.trace[:200]}")
        check("average total assets" in o.trace, "S06 trace does not state the denominator")


def test_s06_drops_the_year_that_has_no_opening_balance() -> None:
    p = panel({"pat": [100.0] * 3, "ocf": [0.0] * 3, "total_assets": [500.0, 700.0, 900.0]})
    o = R.s06_accruals_heavy_earnings(p)
    if ran(o, "S06"):
        check("carries no opening balance" in o.observation,
              "S06 silently dropped a year rather than saying which and why")


def test_s06_abstains_rather_than_falling_back_to_a_closing_balance() -> None:
    p = panel({"pat": [100.0, 100.0, 100.0], "ocf": [0.0, 0.0, 0.0],
               "total_assets": [900.0]})
    o = R.s06_accruals_heavy_earnings(p)
    check(not o.ran, "S06 produced a ratio from a single total-assets year")


# ---- S10 non-current-other share ---------------------------------------------------

def test_s10_fires_on_a_rising_share() -> None:
    o = R.s10_non_current_other_share(panel({
        "other_non_current_assets": [50.0, 120.0, 200.0],
        "total_assets": [1000.0, 1000.0, 1000.0]}))     # 5% -> 20%
    if ran(o, "S10"):
        check(o.fired is True, "S10 did not fire on a 15-point rise in the residual share")
        check(bool(o.proxies),
              "S10 omitted other non-current FINANCIAL assets without saying the share is "
              "a lower bound")


def test_s10_does_not_fire_on_a_flat_share() -> None:
    o = R.s10_non_current_other_share(panel({
        "other_non_current_assets": [50.0, 52.0, 55.0],
        "total_assets": [1000.0, 1040.0, 1100.0]}))
    if ran(o, "S10"):
        check(o.fired is False, "S10 fired on a share that did not drift")


# ---- S11 depreciation vs the asset base --------------------------------------------

def test_s11_fires_on_a_falling_implied_rate() -> None:
    o = R.s11_depreciation_vs_asset_base(panel({
        "depreciation": [100.0, 100.0, 100.0],
        "net_fixed_assets": [1000.0, 1500.0, 2500.0]}))
    if ran(o, "S11"):
        check(o.fired is True, "S11 did not fire on a charge flat against a growing base")


def test_s11_does_not_fire_on_a_rising_rate() -> None:
    """Only a FALL is the concern; a net-block denominator drifts a rate up mechanically."""
    o = R.s11_depreciation_vs_asset_base(panel({
        "depreciation": [100.0, 140.0, 200.0],
        "net_fixed_assets": [2000.0, 2000.0, 2000.0]}))
    if ran(o, "S11"):
        check(o.fired is False, "S11 fired on a RISING implied rate")


def test_s11_caps_confidence_and_says_the_level_is_not_comparable() -> None:
    o = R.s11_depreciation_vs_asset_base(panel({
        "depreciation": [100.0, 100.0, 100.0],
        "net_fixed_assets": [1000.0, 1500.0, 2500.0]}))
    if ran(o, "S11"):
        check(o.confidence != R.HIGH,
              "S11 kept HIGH confidence on a net-block proxy for gross block")
        check("NOT comparable" in o.observation,
              "S11 did not warn that the level cannot be held against a disclosed life")


def test_s11_excludes_cwip_from_the_asset_base() -> None:
    with_cwip = R.s11_depreciation_vs_asset_base(panel({
        "depreciation": [100.0, 100.0, 100.0],
        "net_fixed_assets": [1000.0, 1100.0, 1200.0],
        "cwip": [500.0, 500.0, 500.0]}))
    without = R.s11_depreciation_vs_asset_base(panel({
        "depreciation": [100.0, 100.0, 100.0],
        "net_fixed_assets": [1000.0, 1100.0, 1200.0]}))
    if ran(with_cwip, "S11+cwip") and ran(without, "S11-cwip"):
        check(with_cwip.trace != without.trace,
              "S11 produced the same rate with and without CWIP — it is not being excluded")


# ---- S13 leverage-driven ROE -------------------------------------------------------

def test_s13_fires_when_leverage_carries_the_improvement() -> None:
    o = R.s13_leverage_driven_roe(panel({
        "pat": [100.0, 100.0, 100.0],
        "revenue": [1000.0, 1000.0, 1000.0],
        "total_assets": [1000.0, 1000.0, 1000.0],
        "total_equity": [500.0, 350.0, 250.0]}))        # ROE 20% -> 40%, operating flat
    if ran(o, "S13"):
        check(o.fired is True, "S13 did not fire on an ROE gain driven purely by leverage")


def test_s13_does_not_fire_when_margin_carries_it() -> None:
    o = R.s13_leverage_driven_roe(panel({
        "pat": [100.0, 150.0, 200.0],
        "revenue": [1000.0, 1000.0, 1000.0],
        "total_assets": [1000.0, 1000.0, 1000.0],
        "total_equity": [500.0, 500.0, 500.0]}))
    if ran(o, "S13"):
        check(o.fired is False, "S13 fired on a margin-driven ROE improvement")


def test_s13_traces_each_factor_across_every_year() -> None:
    """§8.2 — endpoints cannot tell a steady build from a spike that has unwound."""
    p = panel({"pat": [100.0, 100.0, 100.0], "revenue": [1000.0] * 3,
               "total_assets": [1000.0] * 3, "total_equity": [500.0, 350.0, 250.0]})
    o = R.s13_leverage_driven_roe(p)
    if ran(o, "S13"):
        for y in p.years:
            check(y in o.trace, f"S13 trace omits {y}; it is comparing endpoints only")


# ---- S14 short-term funding of long-term assets ------------------------------------

def test_s14_fires_when_long_term_funding_does_not_carry_the_asset_growth() -> None:
    o = R.s14_short_term_funding_of_long_term_assets(panel({
        "total_non_current_assets": [1000.0, 2000.0],
        "total_equity": [500.0, 520.0],
        "long_term_borrowings": [500.0, 520.0],
        "short_term_borrowings": [100.0, 1000.0]}))
    if ran(o, "S14"):
        check(o.fired is True,
              "S14 did not fire when non-current assets doubled on short borrowings")


def test_s14_includes_current_maturities_in_the_short_debt_share() -> None:
    base = {"total_non_current_assets": [1000.0, 1050.0],
            "total_equity": [500.0, 550.0],
            "long_term_borrowings": [900.0, 900.0],
            "short_term_borrowings": [100.0, 100.0]}
    without = R.s14_short_term_funding_of_long_term_assets(panel(base))
    # 900 of (900 + 900) = 50% short, against 100 of 1000 = 10% without the maturities.
    with_cm = R.s14_short_term_funding_of_long_term_assets(
        panel({**base, "current_maturities_ltd": [0.0, 800.0]}))
    if ran(without, "S14-cm") and ran(with_cm, "S14+cm"):
        check(with_cm.fired is True and without.fired is False,
              "S14 gave the same verdict with and without current maturities — they are "
              "not inside the short-debt share the derivation names")
        check("included in current borrowings" in with_cm.observation,
              "S14 did not state that current maturities were included")


def test_s14_caps_confidence_when_current_maturities_are_unbound() -> None:
    o = R.s14_short_term_funding_of_long_term_assets(panel({
        "total_non_current_assets": [1000.0, 1050.0], "total_equity": [500.0, 550.0],
        "long_term_borrowings": [900.0, 900.0], "short_term_borrowings": [100.0, 100.0]}))
    if ran(o, "S14"):
        check(o.confidence != R.HIGH,
              "S14 kept HIGH confidence while the short-debt share was a lower bound")


# ---- S15 finance cost vs borrowings ------------------------------------------------

def test_s15_reports_the_implied_rate() -> None:
    o = R.s15_finance_cost_vs_borrowings(panel({
        "finance_costs": [50.0, 80.0, 120.0],
        "long_term_borrowings": [800.0, 800.0, 800.0],
        "short_term_borrowings": [200.0, 200.0, 200.0],
        "total_assets": [5000.0, 5000.0, 5000.0]}))
    if ran(o, "S15"):
        check(o.fired is True, "S15 did not fire on cost rising against flat debt")
        check("Implied borrowing rate" in o.observation,
              "S15 reported only the growth divergence, not the implied rate")
        # 120 / ((1000 + 1000) / 2) = 12.0%
        check("12.0%" in o.observation or "12.0%" in o.trace,
              f"S15 implied rate is wrong — {o.trace[:200]}")


def test_s15_abstains_on_a_trivial_borrowing_base() -> None:
    o = R.s15_finance_cost_vs_borrowings(panel({
        "finance_costs": [1.0, 2.0, 4.0],
        "long_term_borrowings": [1.0, 1.0, 1.0],
        "short_term_borrowings": [1.0, 1.0, 1.0],
        "total_assets": [100000.0] * 3}))
    check(not o.ran, "S15 produced a divergence on borrowings below 1% of total assets")


def test_s15_abstains_on_an_economically_impossible_implied_rate() -> None:
    """§4.3 — a failed integrity check bars a conclusion resting on the affected figure.

    The ONGC regression. Finance cost of 460,397 against average borrowings of 725,846
    implies a 63% borrowing rate. No entity borrows at that price, so the numerator is not
    interest on that denominator, and the divergence rests on the same contaminated
    figure. Both must be withheld — the first version of this rule named the contamination
    in prose and published the number anyway, and it reached rank 1.
    """
    o = R.s15_finance_cost_vs_borrowings(panel({
        "finance_costs": [269960.1, 408131.2, 460397.0],
        "long_term_borrowings": [394993.2, 398824.8, 355979.4],
        "short_term_borrowings": [326894.7, 212100.0, 484788.7],
        "total_assets": [36703709.3, 44602089.0, 45165275.8]}))
    check(not o.ran, "S15 published an implied borrowing rate above 60% as a diagnostic")
    check("Integrity check failed" in o.reason,
          "S15 abstained without naming this as an integrity failure")
    check("decommissioning" in o.reason and "lease" in o.reason,
          "S15's abstain does not name the usual causes of the contamination")
    check("divergence is NOT reported" in o.reason,
          "S15 did not state that the growth divergence is withheld too")


def test_s15_abstains_when_leases_exceed_the_borrowings_denominator() -> None:
    """Naming the contamination in prose is not enough when it is the larger balance."""
    o = R.s15_finance_cost_vs_borrowings(panel({
        "finance_costs": [50.0, 80.0, 120.0],
        "long_term_borrowings": [800.0, 800.0, 800.0],
        "short_term_borrowings": [200.0, 200.0, 200.0],
        "lease_liabilities_nc": [2000.0, 2500.0, 3000.0],
        "lease_liabilities_cl": [400.0, 450.0, 500.0],
        "total_assets": [50000.0] * 3}))
    check(not o.ran, "S15 reported a rate although leases were 3.5x the borrowings base")
    check("Ind AS 116" in o.reason, "S15's abstain does not explain the lease contamination")


def test_s15_still_runs_on_a_credible_rate() -> None:
    """The gate is a bound on the impossible, not a view on what rate is high."""
    o = R.s15_finance_cost_vs_borrowings(panel({
        "finance_costs": [50.0, 80.0, 120.0],
        "long_term_borrowings": [800.0, 800.0, 800.0],
        "short_term_borrowings": [200.0, 200.0, 200.0],
        "total_assets": [5000.0] * 3}))
    check(o.ran, f"S15 abstained on a 12% implied rate, which is credible — {o.reason}")


def test_divergences_are_labelled_in_percentage_points() -> None:
    """Subtracting one growth rate from another gives points, not a rate (§16)."""
    o = R.s05_receivables_outpacing_revenue(panel({
        "trade_receivables": [100.0, 150.0, 200.0],
        "revenue": [1000.0, 1020.0, 1050.0]}))
    if ran(o, "S05"):
        check("pp" in o.observation,
              "S05 prints a gap between two growth rates with a % sign, inviting it to be "
              "read as a rate")


def test_s15_carries_the_reconciling_items_to_eliminate_first() -> None:
    from . import derivations as DV
    d = DV.for_signal("S15")
    check(d is not None and len(d.reconciling_items) >= 4,
          "S15 does not list the reconciling items that can produce the divergence alone")
    joined = " ".join(d.reconciling_items).lower() if d else ""
    for item in ("capitalis", "unwinding", "exchange"):
        check(item in joined, f"S15 reconciling items omit {item!r}")


# ---- S18 non-cash gains ------------------------------------------------------------

def test_s18_divides_by_pbt_not_pat() -> None:
    p = panel({"other_income": [100.0, 100.0, 100.0],
               "pbt": [200.0, 200.0, 200.0],
               "pat": [100.0, 100.0, 100.0],
               "ocf": [50.0, 50.0, 50.0]})
    o = R.s18_non_cash_gains(p)
    if ran(o, "S18"):
        # 100/200 = 50% against PBT; 100/100 = 100% had it used PAT
        check("50.0%" in o.trace, f"S18 did not divide by PBT — {o.trace[:200]}")
        check("PBT" in o.trace, "S18 trace does not name the denominator")


def test_s18_is_labelled_an_upper_bound_and_capped() -> None:
    o = R.s18_non_cash_gains(panel({
        "other_income": [100.0] * 3, "pbt": [200.0] * 3, "ocf": [50.0] * 3}))
    if ran(o, "S18"):
        check("UPPER BOUND" in o.observation,
              "S18 presents a proxy for non-cash gains without saying it overstates")
        check(o.confidence != R.HIGH, "S18 kept HIGH confidence on the other-income proxy")
        check(bool(o.proxies), "S18 did not record the substitution it made")


def test_s18_does_not_fire_when_cash_follows_the_result() -> None:
    o = R.s18_non_cash_gains(panel({
        "other_income": [100.0] * 3, "pbt": [200.0] * 3, "ocf": [300.0] * 3}))
    if ran(o, "S18"):
        check(o.fired is False,
              "S18 fired although operating cash flow exceeded the reported profit")


# ---- S19 other-income sustainability -----------------------------------------------

def test_s19_reports_the_share_of_pbt_when_bound() -> None:
    o = R.s19_other_income_sustainability(panel({
        "other_income": [50.0, 150.0, 400.0],
        "total_income": [1000.0, 1100.0, 1300.0],
        "pbt": [200.0, 220.0, 260.0]}))
    if ran(o, "S19"):
        check("profit before tax" in o.observation,
              "S19 omitted the other-income-over-PBT ratio the derivation names")
        check("split" in o.observation,
              "S19 did not state that the recurring / non-recurring split was not formed")


def test_s19_states_the_missing_split_even_without_pbt() -> None:
    o = R.s19_other_income_sustainability(panel({
        "other_income": [50.0, 150.0, 400.0],
        "total_income": [1000.0, 1100.0, 1300.0]}))
    if ran(o, "S19"):
        check("not formed" in o.trace,
              "S19 did not say that other income over PBT could not be formed")
        check(bool(o.proxies), "S19 dropped the recurring split without recording it")


# ---- S20 government dependency -----------------------------------------------------

def test_s20_fires_on_a_high_partial_index() -> None:
    o = R.s20_government_dependency(panel({
        "total_income": [1000.0, 1000.0, 1000.0],
        "government_grant_cf": [-50.0, -150.0, -300.0]}))
    if ran(o, "S20"):
        check(o.fired is True, "S20 did not fire at 30% of total income")
        check("PARTIAL" in o.observation,
              "S20 presented a one-component index without saying it is partial")
        check(o.confidence == R.LOW,
              "S20 must cap at LOW: one of three components is measured, and it is a cash "
              "receipt rather than the amount recognised in income")


def test_s20_abstains_when_no_component_is_bound() -> None:
    o = R.s20_government_dependency(panel({"total_income": [1000.0] * 3}))
    check(not o.ran, "S20 reported an index with none of its components bound")
    check("Nothing is substituted" in o.reason,
          "S20 abstained without stating that nothing was substituted")


# ---- cross-cutting -----------------------------------------------------------------

def test_no_rule_reports_confidence_without_a_basis() -> None:
    p = panel({k: [100.0, 120.0, 150.0] for k in (
        "trade_receivables", "inventories", "trade_payables", "revenue",
        "cost_of_materials_consumed", "ocf", "pat", "pbt", "total_assets",
        "total_equity", "other_income", "total_income", "finance_costs",
        "long_term_borrowings", "short_term_borrowings", "depreciation",
        "net_fixed_assets", "other_non_current_assets", "total_non_current_assets",
        "total_current_assets", "total_current_liabilities", "government_grant_cf")})
    for sid in sorted(R.RULES):
        o = R.run(sid, p)
        if o is not None and o.ran:
            check(o.confidence is not None, f"{sid} ran without forming a confidence")
            check(bool(o.confidence_basis), f"{sid} gave a confidence with no basis (App H)")
            check(bool(o.observation), f"{sid} ran without an observation (§14.2)")
            check(bool(o.trace), f"{sid} ran without a trace a reviewer can check (§16)")


def test_a_contradicted_input_forces_low_confidence() -> None:
    """A figure from a statement that failed its own identity cannot support a diagnostic."""
    p = panel({"total_current_assets": [500.0], "total_current_liabilities": [900.0]},
              verdict="CONTRADICTED")
    o = R.s03_net_current_liabilities(p)
    if ran(o, "S03"):
        check(o.confidence == R.LOW,
              "a CONTRADICTED input did not drive the diagnostic to LOW confidence")


def test_every_threshold_a_rule_reads_exists() -> None:
    """A typo in a threshold key must fail loudly, not read as 0.0."""
    p = panel({k: [100.0, 120.0, 150.0] for k in (
        "trade_receivables", "inventories", "trade_payables", "revenue",
        "cost_of_materials_consumed", "ocf", "pat", "pbt", "total_assets",
        "total_equity", "other_income", "total_income", "finance_costs",
        "long_term_borrowings", "short_term_borrowings", "depreciation",
        "net_fixed_assets", "other_non_current_assets", "total_non_current_assets",
        "total_current_assets", "total_current_liabilities", "government_grant_cf")})
    for sid in sorted(R.RULES):
        try:
            R.run(sid, p)
        except KeyError as e:
            _fails.append(f"{sid} reads an unknown threshold: {e}")


def test_thresholds_are_relative_never_absolute_rupees() -> None:
    """An absolute cut-off means one thing for a small body and another for ONGC."""
    for t in TH.ALL:
        # "count" is a COUNT OF YEARS, not a quantity of money — a persistence requirement
        # ("the share must hold in at least 2 years") is scale-free in exactly the way this
        # test is protecting. The rule being enforced is that no threshold is denominated in
        # rupees, and a year count never is.
        check(t.unit in ("ratio", "share", "pct_points", "days", "x", "count"),
              f"threshold {t.key} has unit {t.unit!r}, which looks like an amount")
        check(bool(t.basis.strip()), f"threshold {t.key} states no basis")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    if _fails:
        print(f"FAIL - {len(_fails)} problem(s) in {len(tests)} checks:")
        for f in _fails:
            print(f"  - {f}")
        return 1
    print(f"OK - {len(tests)} checks passed over {len(R.RULES)} implemented rules")
    return 0




# ---------------------------------------------------------------- reason codes
# A rule that cannot run must say WHICH condition failed, name the key and the years, and
# carry a closed code. The disjunction these replaced ("fewer than 3 comparable years, OR
# the inputs are not bound in every year") covered both branches because that was cheaper
# than asking which one happened — and on a five-year panel its first branch is FALSE at
# the moment it is emitted. It is also unqueryable: ranking binding fixes by coverage was
# a regular expression over English.

_Y5 = tuple(f"FY{2020 + i}-{str(2021 + i)[-2:]}" for i in range(5))


def test_a_key_bound_in_no_year_is_named_as_binder_work() -> None:
    p = panel({"revenue": [1.0] * 5})
    o = R._needs(p, ("revenue", "trade_payables"))
    check(o is not None and o.code == R.INPUT_NEVER_BOUND,
          f"a key bound in no year must give INPUT_NEVER_BOUND, got {o and o.code}")
    check("trade_payables" in (o.reason if o else ""),
          "the abstain must name the failing key")


def test_a_hole_in_the_middle_is_a_gap_not_a_shortage_of_years() -> None:
    """The ONGC case: `ocf` present either side of FY2023-24 and absent in it. The old
    message blamed the panel length, which was five years and plainly sufficient."""
    p = panel({"revenue": [1.0] * 5}, _Y5)
    for y in ("FY2021-22", "FY2022-23", "FY2024-25"):
        p.cells[("ocf", y)] = PanelCell(value=1.0, fy_label=y, period_end=f"{y[2:6]}-03-31",
                                        unit_confidence="HIGH", verify_verdict="CONFIRMED")
    o = R._needs(p, ("ocf",))
    check(o is not None and o.code == R.INPUT_SERIES_BROKEN,
          f"a mid-series hole must give INPUT_SERIES_BROKEN, got {o and o.code}")
    check("FY2023-24" in (o.reason if o else ""),
          "the abstain must name the year that is missing")


def test_a_short_panel_is_a_panel_fact_not_a_key_failure() -> None:
    p = panel({"revenue": [1.0, 1.0]})
    o = R._needs(p, ("revenue",))
    check(o is not None and o.code == R.PANEL_TOO_SHORT,
          f"a two-year panel must give PANEL_TOO_SHORT, got {o and o.code}")


def test_a_complete_window_does_not_abstain() -> None:
    check(R._needs(panel({"revenue": [1.0] * 5}), ("revenue",)) is None,
          "a key with a full window must not abstain")


def test_no_disjunctive_abstain_reason_survives() -> None:
    import inspect
    src = inspect.getsource(R)
    check("are not bound in every year" not in src,
          "a disjunctive abstain reason survives in rules.py")


def test_scope_codes_are_exactly_the_two_that_are_not_data_failures() -> None:
    check(R.SCOPE_CODES == frozenset({R.RULE_NOT_IMPLEMENTED, R.NEEDS_NOTE_EXTRACTION}),
          "SCOPE_CODES must hold exactly the two scope reasons")


if __name__ == "__main__":
    raise SystemExit(main())
