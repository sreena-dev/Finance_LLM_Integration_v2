"""
Deterministic checks for the ratio catalog (run: python fs_db/test_computations.py).
Hand-computed expected values — if the Python ever drifts from the sheet's math,
these fail. No DB, no LLM, no network.
"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))   # import computations without the pkg __init__
import computations as C

# A synthetic but internally-consistent input set (round numbers for hand-checking).
# COGS (600) is split across the three Schedule III lines it's actually reported as
# (400 + 100 + 100); Total Borrowings (300) is split across LT/ST (200 + 100) — same
# arithmetic as before the sheet's precision pass, just decomposed the way a real
# filing prints it.
INP = {
    "current_assets": 300, "current_liabilities": 150, "inventories": 100,
    "prepaid_expenses": 10, "cash_and_cash_equivalents": 50, "marketable_securities": 20,
    "trade_receivables": 80, "short_term_borrowings": 30,
    "revenue": 1000,
    "cost_of_materials_consumed": 400, "purchases_of_stock_in_trade": 100, "changes_in_inventories": 100,
    "ebit": 200, "pat": 120, "finance_costs": 40,
    "depreciation": 50, "instalments": 20, "loan_repayment": 20, "sga_expenses": 100,
    # long_term_borrowings + short_term_borrowings(30, below) = 300 total borrowings
    "total_assets": 900, "long_term_borrowings": 270, "net_fixed_assets": 500,
    "equity_share_capital": 100, "reserves_and_surplus": 300, "preference_share_capital": 50,
    "debentures": 100, "other_borrowed_funds": 50, "accumulated_losses": 0,
    "total_equity": 400,
    "avg_total_assets": 850, "avg_inventory": 90, "credit_sales": 900,
    "avg_receivables": 80, "credit_purchases": 500, "avg_payables": 60,
    "num_equity_shares": 10, "total_dividend": 30, "preference_dividend": 5,
    "return_amount": 200, "investment": 800,
    # -- FDR Appendix G additions --
    # Average equity 380 = (opening 360 + closing 400) / 2.
    "avg_equity": 380,
    # Total income 1100 = revenue 1000 + other income 100.
    "other_income": 100, "total_income": 1100, "total_expenses": 800,
    # Cash Flow Statement. Capex 100 = 60 PPE + 10 intangibles + 30 exploration, printed
    # as Ind AS 7 outflows (negative) so the magnitude-normalisation in `_capex` is
    # exercised by the fixture rather than assumed.
    "ocf": 200, "icf": -150, "fcf_financing": -40, "net_change_in_cash": 10,
    "capex_ppe": -60, "capex_intangibles": -10, "capex_exploration": -30,
    "government_grant_cf": -25,
    # Total debt 400 = 270 LT + 30 ST + 20 current maturities + 60 + 20 lease liabilities.
    "current_maturities_ltd": 20, "lease_liabilities_nc": 60, "lease_liabilities_cl": 20,
}

EXPECT = {
    "current_ratio": 2.0,
    # Net Assets = Total Assets - Current Liabilities, per the sheet's own name for the
    # input and its Derived trace row (NOT the Components prose's "PPE + Net Current
    # Assets" gloss, which double-counts nothing but silently drops every non-current
    # asset other than PPE — 50.5% of the denominator on ONGC).
    "equity_ratio": round(400 / (900 - 150), 4),              # 0.5333
    "debt_ratio": round(300 / (900 - 150), 4),                # 0.4
    "quick_ratio": round((300 - 100 - 10) / 150, 4),          # 1.2667
    "cash_ratio": round((50 + 20) / 150, 4),                  # 0.4667
    "net_working_capital": (300 - (150 - 30)),                # 180.0
    "debt_to_equity": round(300 / 400, 4),                    # 0.75  (200+100 LT+ST / 400)
    "debt_to_total_assets": round(300 / 900, 4),              # 0.3333
    "proprietary_ratio": round((100 + 50 + 300) / 900, 4),    # 0.5
    "interest_coverage": 5.0,
    "dscr": round((120 + 50 + 40) / (40 + 20), 4),            # 3.5
    "inventory_turnover": round(600 / 90, 4),                 # 6.6667  (400+100+100 COGS)
    "receivables_collection_period": round(80 * 365 / 900, 4),# 32.44
    # SRS §9.2 analytical turnovers — total flows, NOT the Schedule III credit numerators.
    "receivables_turnover_revenue": round(1000 / 80, 4),      # 12.5   (revenue / avg recv)
    "days_sales_outstanding": round(80 * 365 / 1000, 4),      # 29.2
    "payables_turnover_purchases": round(500 / 60, 4),        # 8.3333 (400+100 procurement,
                                                              #   changes_in_inventories excluded)
    "days_payable_outstanding": round(60 * 365 / 500, 4),     # 43.8
    "gross_profit_margin": 40.0,                              # (1000 - 600) / 1000 * 100
    "net_profit_margin": 12.0,
    "operating_profit_margin": 20.0,
    # S.No 29 expense ratios (one sheet template row -> four heads it names)
    "cogs_ratio": 60.0,                                       # 600 / 1000 * 100
    "operating_expenses_ratio": 10.0,                         # 100 / 1000 * 100
    "operating_ratio": 70.0,                                  # (600 + 100) / 1000 * 100
    "financial_expenses_ratio": 4.0,                          # 40 / 1000 * 100
    "roce": round(200 / (900 - 150) * 100, 4),                # 26.6667
    "roe": round((120 - 5) / 400 * 100, 4),                   # 28.75
    "eps": round((120 - 5) / 10, 4),                          # 11.5
    "dividend_payout": round((30 / 10) / ((120 - 5) / 10), 4),# 0.2609
    # -- FDR Appendix G — every value hand-computed from INP above --
    "roe_avg_equity": round(120 / 380 * 100, 4),              # 31.5789 (PAT / AVERAGE equity)
    # DuPont must land on exactly `roe_avg_equity`: net margin x asset turnover x equity
    # multiplier = (120/1000) x (1000/850) x (850/380) = 120/380. Asserting the same
    # number twice is the point — it is the decomposition identity, and if the factors
    # were ever multiplied as rounded values instead of cancelled, this drifts.
    "dupont_roe": round(120 / 380 * 100, 4),                  # 31.5789
    "debt_to_equity_total_debt": round(400 / 400, 4),         # 1.0 (270+30+20+60+20 / 400)
    "inventory_days": round(90 / 600 * 365, 4),               # 54.75  (avg inv / COGS x 365)
    # 29.2 DSO + 54.75 inventory days - 43.8 payables days
    "cash_conversion_cycle": round(80 / 1000 * 365 + 90 / 600 * 365 - 60 / 500 * 365, 4),
    "accruals_ratio": round((120 - 200) / 850 * 100, 4),      # -9.4118 (PAT below OCF)
    "free_cash_flow": round(200 - (60 + 10 + 30), 4),         # 100.0 (capex magnitudes)
    "govt_support_dependency": round(25 / 1100 * 100, 4),     # 2.2727
}


def main() -> int:
    results = {c.key: c for c in C.run_ratios(INP)}
    fails = []

    for key, want in EXPECT.items():
        got = results.get(key)
        if got is None:
            fails.append(f"{key}: not produced"); continue
        if got.status != "OK":
            fails.append(f"{key}: status={got.status} ({got.trace})"); continue
        if abs(got.result - want) > 0.01:
            fails.append(f"{key}: got {got.result} want {want}")
        if not got.sources:
            fails.append(f"{key}: no source trace attached")

    # ABSTAIN behaviour: zero denominator and missing input must NOT compute.
    zero = C.compute(C.CATALOG_BY_KEY["interest_coverage"], {**INP, "finance_costs": 0})
    if zero.status != "ABSTAIN":
        fails.append("interest_coverage with finance_costs=0 should ABSTAIN")
    miss = C.compute(C.CATALOG_BY_KEY["roe"], {"pat": 120})   # total_equity missing
    if miss.status != "ABSTAIN":
        fails.append("roe without total_equity should ABSTAIN")

    # any_of: COGS decomposition — partial disclosure computes (missing parts -> 0),
    # but ALL THREE absent must abstain rather than silently report COGS=0.
    partial = C.compute(C.CATALOG_BY_KEY["inventory_turnover"],
                        {**INP, "purchases_of_stock_in_trade": None, "changes_in_inventories": None})
    if partial.status != "OK" or abs(partial.result - round(400 / 90, 4)) > 0.01:
        fails.append(f"inventory_turnover with partial COGS should compute from what's present, got {partial}")
    none_present = {k: v for k, v in INP.items()
                    if k not in ("cost_of_materials_consumed", "purchases_of_stock_in_trade", "changes_in_inventories")}
    absent = C.compute(C.CATALOG_BY_KEY["inventory_turnover"], none_present)
    if absent.status != "ABSTAIN":
        fails.append("inventory_turnover with NO COGS component present should ABSTAIN, not report COGS=0")

    # any_of: Total Borrowings decomposition — same partial/absent behaviour.
    no_borrowings = {k: v for k, v in INP.items()
                     if k not in ("long_term_borrowings", "short_term_borrowings")}
    absent_debt = C.compute(C.CATALOG_BY_KEY["debt_to_equity"], no_borrowings)
    if absent_debt.status != "ABSTAIN":
        fails.append("debt_to_equity with NO borrowings line present should ABSTAIN, not report debt=0")

    # capital_gearing: all three numerator parts are Notes-level and optional, so with
    # none of them present it must ABSTAIN, not report a confident 0.0000 ("no fixed-cost
    # funds") built from three silent zero-defaults. Observed live on ONGC.
    no_fixed = {k: v for k, v in INP.items()
                if k not in ("preference_share_capital", "debentures", "other_borrowed_funds")}
    if C.compute(C.CATALOG_BY_KEY["capital_gearing"], no_fixed).status != "ABSTAIN":
        fails.append("capital_gearing with no fixed-cost fund line present should ABSTAIN, "
                     "not report 0.0000")
    if C.compute(C.CATALOG_BY_KEY["capital_gearing"], INP).status != "OK":
        fails.append("capital_gearing should still compute when the parts ARE present")

    # COGS materiality guard: when the three-line decomposition is a token share of Total
    # Expenses, the filing books its cost of sales elsewhere and every COGS ratio is
    # misleading -> abstain. Must NOT fire when the decomposition is the real cost base.
    for key in ("gross_profit_margin", "cogs_ratio", "inventory_turnover"):
        thin = C.compute(C.CATALOG_BY_KEY[key], {**INP, "total_expenses": 20000})  # 600/20000 = 3%
        if thin.status != "ABSTAIN":
            fails.append(f"{key} should ABSTAIN when COGS is 3% of total expenses, got {thin.result}")
        fat = C.compute(C.CATALOG_BY_KEY[key], {**INP, "total_expenses": 700})     # 600/700 = 86%
        if fat.status != "OK":
            fails.append(f"{key} should compute when COGS is 86% of total expenses ({fat.trace})")
    if C.compute(C.CATALOG_BY_KEY["gross_profit_margin"], INP).status != "OK":
        fails.append("COGS guard must be a no-op when total_expenses is not bound")

    # Same guard for the procurement lines behind the SRS §9.2 payables turnovers: on a
    # filing that books input costs under an industry-specific head, 500/20000 = 2.5% is
    # not this entity's procurement and DPO would be absurd (43.8 days -> 1,095 days).
    for key in ("payables_turnover_purchases", "days_payable_outstanding"):
        thin = C.compute(C.CATALOG_BY_KEY[key], {**INP, "total_expenses": 20000})
        if thin.status != "ABSTAIN":
            fails.append(f"{key} should ABSTAIN when procurement is 2.5% of total expenses")
        if C.compute(C.CATALOG_BY_KEY[key], {**INP, "total_expenses": 700}).status != "OK":
            fails.append(f"{key} should compute when procurement is a real cost base")
    # Changes in Inventories is a valuation adjustment, not a purchase from a creditor, so
    # it must NOT reach the payables numerator (which would give 600/60 = 10.0, not 8.3333).
    if abs(C.CATALOG_BY_KEY["payables_turnover_purchases"].num(INP) - 500) > 0.01:
        fails.append("payables turnover numerator must exclude changes_in_inventories")

    # An input that no binder can supply must abstain with the REASON TYPE, not a bare
    # "missing inputs" that reads as a disclosure failure by the entity.
    roi = C.compute(C.CATALOG_BY_KEY["roi"], {k: v for k, v in INP.items()
                                              if k not in ("return_amount", "investment")})
    if "analyst-specified parameter" not in roi.trace:
        fails.append(f"roi abstain should name the PARAM tier, got: {roi.trace}")
    rec = C.compute(C.CATALOG_BY_KEY["receivables_turnover"],
                    {k: v for k, v in INP.items() if k != "credit_sales"})
    if "not separately disclosed anywhere in Schedule III" not in rec.trace:
        fails.append(f"receivables_turnover abstain should name the tier, got: {rec.trace}")

    # Market ratios must be disabled by default (need external price).
    default_keys = {c.key for c in C.run_ratios(INP)}
    if "pe_ratio" in default_keys:
        fails.append("pe_ratio should be disabled by default")

    # Catalog coverage vs the source sheet: 41 ratio rows in
    # `formulas/Final Ratio Calulation chart - final detailed.xlsx`, of which S.No 29
    # ("Expense Ratios") is a template row naming four expense heads -> 40 + 4 = 44.
    # The sheet stays CLOSED — nothing may be smuggled in beside it — so this is an
    # equality on the sheet-sourced specs, checked separately from the deliberate SRS §9.2
    # additions below rather than as one loose total that would hide either drifting.
    from_sheet = [s for s in C.CATALOG if s.source == "sheet"]
    if len(from_sheet) != 44:
        fails.append(f"catalog should hold exactly 44 sheet-sourced specs (41 rows, S.No 29 "
                     f"-> 4 heads); found {len(from_sheet)}")
    srs = {s.key for s in C.CATALOG if s.source == "srs-9.2"}
    if srs != {"receivables_turnover_revenue", "days_sales_outstanding",
               "payables_turnover_purchases", "days_payable_outstanding"}:
        fails.append(f"SRS §9.2 additions drifted: {sorted(srs)}")
    # The analytical turnovers must NEVER carry the Schedule III flag — that flag is what
    # tells a reader the number is the entity's mandated disclosure, and these are computed
    # on total flows instead of the net-credit numerators Schedule III prescribes.
    mislabelled = [k for k in srs if C.CATALOG_BY_KEY[k].mandatory_sch3]
    if mislabelled:
        fails.append(f"SRS §9.2 turnovers must not be flagged mandatory_sch3: {mislabelled}")
    if len(C.MANDATORY_SCH3) != 11:
        fails.append(f"Schedule III mandates exactly 11 note-disclosure ratios; "
                     f"catalog flags {len(C.MANDATORY_SCH3)}")

    # "Net Assets" must be the SAME quantity the sheet's Capital Employed trace row gives,
    # since the sheet derives both from "Balance Sheet (Total Assets – Current
    # Liabilities)". equity_ratio and roce disagreeing on it was the original defect.
    # Compared through the spec's own den() so the check is exact rather than fighting the
    # 4-dp rounding on `result`, and so it fails if EITHER ratio's denominator drifts.
    na = C.CATALOG_BY_KEY["equity_ratio"].den(INP)
    ce = C.CATALOG_BY_KEY["roce"].den(INP)
    if na != 900 - 150 or na != ce:
        fails.append(f"Net Assets ({na}) must be Total Assets - Current Liabilities "
                     f"({900 - 150}) and equal Capital Employed ({ce})")
    if "net_fixed_assets" in C.CATALOG_BY_KEY["equity_ratio"].required:
        fails.append("equity_ratio should no longer require PPE — Net Assets is TA - CL")

    # Reachability: a spec whose inputs no binder can supply must be DECLARED, not
    # discovered per entity. These counts are the measured state of the package; changing
    # them means a binder was added or a spec's inputs changed, and the report/SRS
    # coverage claims have to move with it — so they are asserted, not merely printed.
    unreachable = C.unreachable_specs()
    expect_unreachable = {
        "capital_gearing", "dscr", "preference_dividend_coverage", "fixed_charges_coverage",
        "receivables_turnover", "receivables_collection_period", "payables_turnover",
        "payables_velocity", "roi", "eps", "dps", "dividend_payout",
    }
    if set(unreachable) != expect_unreachable:
        fails.append(f"unreachable set drifted: +{set(unreachable) - expect_unreachable} "
                     f"-{expect_unreachable - set(unreachable)}")
    if set(C.MANDATORY_SCH3_UNREACHABLE) != {"dscr", "receivables_turnover",
                                             "payables_turnover", "roi"}:
        fails.append(f"mandatory-unreachable drifted: {C.MANDATORY_SCH3_UNREACHABLE}")
    # Every canonical input must declare a tier, or blocking_inputs() silently guesses NOTE
    # and a reachable spec starts reporting itself as dead.
    untiered = sorted(set(C.INPUT_TRACE) - set(C.INPUT_SOURCE))
    if untiered:
        fails.append(f"canonical inputs with no INPUT_SOURCE tier: {untiered}")
    # A spec declared reachable must actually compute on the synthetic set above (which
    # supplies every input), otherwise "reachable" means nothing.
    for s in C.CATALOG:
        if s.enabled and s.key not in unreachable and results[s.key].status != "OK":
            fails.append(f"{s.key} is declared reachable but abstained: {results[s.key].trace}")

    # The FACE tier is a claim about another module — that `ratio_pipeline` really does
    # emit these. Cross-check it against that module's own declaration so adding a binder
    # there without retiering here (or vice versa) fails, instead of leaving a spec
    # declared dead while its inputs are in fact available.
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from fs_db.ratio_pipeline import EMITTED_INPUTS
    except Exception as e:                      # pragma: no cover - optional dependency
        print(f"  (skipped ratio_pipeline cross-check: {type(e).__name__}: {e})")
    else:
        face = {k for k, v in C.INPUT_SOURCE.items() if v == C.FACE}
        if face - EMITTED_INPUTS:
            fails.append(f"INPUT_SOURCE marks these FACE but ratio_pipeline never emits "
                         f"them: {sorted(face - EMITTED_INPUTS)}")
        # trade_payables is emitted only to derive avg_payables; it is not itself a ratio
        # input, so it legitimately has no INPUT_TRACE/INPUT_SOURCE row.
        extra = EMITTED_INPUTS - face - {"trade_payables"}
        if extra:
            fails.append(f"ratio_pipeline emits inputs not tiered FACE: {sorted(extra)}")

    # FDR Appendix G must stay a CLOSED, COMPLETE set of sixteen: every entry maps to a
    # real enabled spec, and all sixteen compute on the synthetic set. A formula silently
    # dropping out of the catalog is the one failure this whole module exists to prevent.
    if len(C.APPENDIX_G) != 16:
        fails.append(f"APPENDIX_G should hold 16 diagnostics; found {len(C.APPENDIX_G)}")
    if len({k for _, _, k in C.APPENDIX_G}) != len(C.APPENDIX_G):
        fails.append("APPENDIX_G maps two diagnostics to the same catalog key")
    for no, name, key in C.APPENDIX_G:
        spec = C.CATALOG_BY_KEY.get(key)
        if spec is None:
            fails.append(f"Appendix G {no} ({name}) -> '{key}' is not in the catalog")
        elif not spec.enabled:
            fails.append(f"Appendix G {no} ({name}) -> '{key}' is disabled")
        elif results[key].status != "OK":
            fails.append(f"Appendix G {no} ({name}) did not compute on the synthetic "
                         f"inputs: {results[key].trace}")
    # DuPont's factors must multiply back to the ROE it decomposes, or the decomposition
    # is decorative. Checked on the factors themselves, not just the headline result.
    nm, at, em = C._dupont_factors(INP)
    if round(nm * at * em * 100, 4) != results["roe_avg_equity"].result:
        fails.append(f"DuPont factors {nm}x{at}x{em} do not reconcile to "
                     f"roe_avg_equity {results['roe_avg_equity'].result}")

    # Schedule III >=25% variance flag fires; <25% does not.
    cr = results["current_ratio"]
    if C.variance_flag(cr, prior_value=1.4) is None:      # 2.0 vs 1.4 = +42.9%
        fails.append("variance_flag should fire for >=25% move")
    if C.variance_flag(cr, prior_value=1.9) is not None:  # 2.0 vs 1.9 = +5.3%
        fails.append("variance_flag should NOT fire for <25% move")

    print(f"catalog: {len(C.CATALOG)} specs "
          f"({sum(s.enabled for s in C.CATALOG)} enabled, "
          f"{len(C.MANDATORY_SCH3)} Schedule III mandatory)")
    print(f"checked: {len(EXPECT)} computed values + abstain/any_of/disabled/variance guards")
    if fails:
        print("\nFAILURES:")
        for f in fails:
            print("  -", f)
        return 1
    print("\nALL PASS")
    # show a couple of traces (incl. source trace) so the reproducible output is visible
    for k in ("current_ratio", "roce", "inventory_turnover"):
        c = results[k]
        print(f"  [{k}] {c.trace}")
        print(f"      sources: {[s['line_item'] or s['major_head'] for s in c.sources][:4]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
