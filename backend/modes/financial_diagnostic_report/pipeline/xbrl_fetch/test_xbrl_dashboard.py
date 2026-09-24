"""
Hermetic tests for `xbrl_dashboard.build_dashboard()` — no DB, no network. Fixture rows
are shaped exactly like `xbrl_conn.query()`'s output (a list of dicts with
`concept_name`, `fy_start`, `fy_end`, `value_numeric`, `unit`), per repo convention of
keeping DB access out of tests via a boundary that returns plain data.

Run from pipeline/: python -m xbrl_fetch.test_xbrl_dashboard
        or: python -m pytest xbrl_fetch/test_xbrl_dashboard.py -v
"""
from __future__ import annotations
from datetime import date

from .xbrl_dashboard import build_dashboard
from .xbrl_concepts import BY_ID


def _row(concept: str, fy_start, fy_end, value: float) -> dict:
    return {"concept_name": concept, "fy_start": fy_start, "fy_end": fy_end,
            "value_numeric": value, "unit": "INR"}


DOC = "test-doc.xml"

# ---------------------------------------------------------------------------------
# Golden fixture: NTPC BHEL Power Projects Pvt Ltd, FY2022-23 (doc_id
# 101_F92854371_20240229_ZA4X_U40102DL2008PTC177307_Instance_Ntpc BHEL_2022-23.xml).
# Values captured live from as_db and independently verified in
# documentation/logic_documentation/bs_logic_spec/ before this module existed — this
# is a regression fixture, not a value invented for the test.
# ---------------------------------------------------------------------------------
_D22, _D23 = date(2021, 4, 1), date(2022, 4, 1)
_I22, _I23 = date(2022, 3, 31), date(2023, 3, 31)

NBPPL_ROWS = [
    _row("RevenueFromOperations", _D22, _I22, 547355000),
    _row("RevenueFromOperations", _D23, _I23, 512681000),
    _row("ProfitLossForPeriod", _D22, _I22, -301279000),
    _row("ProfitLossForPeriod", _D23, _I23, -159720000),
    _row("ProfitBeforeTax", _D22, _I22, -429910000),
    _row("ProfitBeforeTax", _D23, _I23, -164423000),
    _row("TaxExpense", _D22, _I22, -128631000),
    _row("TaxExpense", _D23, _I23, -4703000),
    _row("CashFlowsFromUsedInOperatingActivities", _D22, _I22, 74106000),
    _row("CashFlowsFromUsedInOperatingActivities", _D23, _I23, 403424000),
    _row("DepreciationDepletionAndAmortisationExpense", _D22, _I22, 58066000),
    _row("DepreciationDepletionAndAmortisationExpense", _D23, _I23, 57876000),
    _row("FinanceCosts", _D22, _I22, 109697000),
    _row("FinanceCosts", _D23, _I23, 157260000),
    _row("Assets", None, _I22, 6148452000),
    _row("Assets", None, _I23, 5875726000),
    _row("Equity", None, _I22, -2025945000),
    _row("Equity", None, _I23, -2188311000),
    _row("Liabilities", None, _I22, 8174397000),
    _row("Liabilities", None, _I23, 8064037000),
    _row("CurrentAssets", None, _I22, 3909320000),
    _row("CurrentAssets", None, _I23, 3784864000),
    _row("CurrentLiabilities", None, _I22, 3730282000),
    _row("CurrentLiabilities", None, _I23, 3606744000),
    _row("TradeReceivablesCurrent", None, _I22, 2972876000),
    _row("TradeReceivablesCurrent", None, _I23, 2753132000),
    _row("TradePayablesCurrent", None, _I22, 3080589000),
    _row("TradePayablesCurrent", None, _I23, 3132174000),
    _row("CashAndCashEquivalents", None, _I22, 11721000),
    _row("CashAndCashEquivalents", None, _I23, 14208000),
    _row("PropertyPlantAndEquipment", None, _I22, 702566000),
    _row("PropertyPlantAndEquipment", None, _I23, 644738000),
    _row("CapitalWorkInProgress", None, _I22, 7914000),
    _row("CapitalWorkInProgress", None, _I23, 7914000),
    _row("BorrowingsCurrent", None, _I22, 0),
    _row("BorrowingsCurrent", None, _I23, 0),
    _row("BorrowingsNoncurrent", None, _I22, 0),
    _row("BorrowingsNoncurrent", None, _I23, 0),
]


def _tile(result, tile_id):
    return next(t for t in result["tiles"] if t["id"] == tile_id)


def test_nbppl_golden_fixture():
    result = build_dashboard(NBPPL_ROWS, DOC)
    assert result["total_count"] == 18
    assert result["computed_count"] == 17          # X18 correctly suppressed (PBT<=0 both years)
    assert result["flagged_ids"] == ["X06"]         # finance costs, only tile past its band

    rev = _tile(result, "X01")
    assert rev["display"] == "51.27"
    assert rev["movement_label"] == "-6.33% vs FY2021-22"

    eq = _tile(result, "X08")
    assert eq["display"] == "-218.83"
    assert "-8.01%" in eq["movement_label"]

    fin = _tile(result, "X06")
    assert fin["display"] == "15.73"
    assert fin["attention"] is True                 # +43.36% > 25% band, ADVERSE_UP, rose

    cwip = _tile(result, "X16")
    assert cwip["computed"] is True
    assert cwip["movement_label"] == "unchanged vs FY2021-22"

    borrow = _tile(result, "X17")
    assert borrow["computed"] is True
    assert borrow["display"] == "0.00"
    assert borrow["movement_label"] == ""           # prior value 0 -> movement withheld, not "+0%"

    tax_rate = _tile(result, "X18")
    assert tax_rate["computed"] is False
    assert "denominator" in tax_rate["reason"]


def test_single_period_shows_value_no_movement():
    rows = [_row("Assets", None, _I23, 1000)]
    result = build_dashboard(rows, DOC)
    t = _tile(result, "X07")
    assert t["computed"] is True
    assert t["value"] == 1000
    assert t["movement_label"] == ""
    assert "no comparable prior year" in t["reason"]


def test_missing_concept_reports_not_computed_with_reason():
    result = build_dashboard([], DOC)
    t = _tile(result, "X07")
    assert t["computed"] is False
    assert t["display"] == "—"
    assert t["reason"]


def test_capital_wip_absence_is_labelled_structural_not_a_gap():
    rows = [_row("Assets", None, _I23, 1000)]   # anything present, just not CWIP
    result = build_dashboard(rows, DOC)
    t = _tile(result, "X16")
    assert t["computed"] is False
    assert "structural absence" in t["reason"]


def test_summed_metric_drops_period_when_one_leg_missing_not_treated_as_zero():
    """Borrowings = current + non-current. If only current is bound for a period,
    that period must be dropped entirely — reporting it as 'total borrowings' would
    silently understate the real total by treating the missing leg as zero."""
    rows = [
        _row("BorrowingsCurrent", None, _I22, 500),
        _row("BorrowingsNoncurrent", None, _I22, 300),
        _row("BorrowingsCurrent", None, _I23, 900),
        # BorrowingsNoncurrent missing entirely for I23
    ]
    result = build_dashboard(rows, DOC)
    t = _tile(result, "X17")
    assert t["computed"] is True
    assert t["value"] == 800          # only the I22 period, where both legs are bound
    assert t["movement_label"] == ""  # no second period to compare against
    assert t["series_years"] == 1


def test_effective_tax_rate_suppressed_on_loss():
    rows = [
        _row("TaxExpense", _D23, _I23, 100),
        _row("ProfitBeforeTax", _D23, _I23, -1000),   # a loss year
    ]
    result = build_dashboard(rows, DOC)
    t = _tile(result, "X18")
    assert t["computed"] is False


def test_effective_tax_rate_computes_on_positive_pbt():
    rows = [
        _row("TaxExpense", _D23, _I23, 250),
        _row("ProfitBeforeTax", _D23, _I23, 1000),
    ]
    result = build_dashboard(rows, DOC)
    t = _tile(result, "X18")
    assert t["computed"] is True
    assert t["display"] == "25.00%"


def test_context_over_reads_operating_cash_flow_multiple_of_profit():
    rows = [
        _row("CashFlowsFromUsedInOperatingActivities", _D23, _I23, 200),
        _row("ProfitLossForPeriod", _D23, _I23, 100),
    ]
    result = build_dashboard(rows, DOC)
    t = _tile(result, "X04")
    assert t["computed"] is True
    assert "2.00" in t["context_display"]


def test_zero_prior_value_withholds_movement_not_infinite_percent():
    rows = [
        _row("CashAndCashEquivalents", None, _I22, 0),
        _row("CashAndCashEquivalents", None, _I23, 500),
    ]
    result = build_dashboard(rows, DOC)
    t = _tile(result, "X14")
    assert t["computed"] is True
    assert t["movement_label"] == ""
    assert "zero" in t["reason"]


def test_tiny_nonzero_prior_value_withholds_extreme_percentage():
    """A prior-year base that is small but not exactly zero produces a percentage
    that is arithmetically correct but not meaningful (e.g. -8628%) — the same
    distortion as the exact-zero case, just less obviously. The level and the
    absolute change must still be shown; only the misleading percentage withheld."""
    rows = [
        _row("CashAndCashEquivalents", None, _I22, 1000),        # 0.0001 cr — a tiny base
        _row("CashAndCashEquivalents", None, _I23, 200_000_000), # 20 cr — a real swing
    ]
    result = build_dashboard(rows, DOC)
    t = _tile(result, "X14")
    assert t["computed"] is True
    assert t["value"] == 200_000_000
    assert t["movement_label"] == ""       # the misleading percentage is withheld
    assert "too small" in t["reason"]
    assert "0.00" in t["reason"]           # the actual (tiny) prior-year figure is named
    assert "+20.00" in t["reason"]         # the absolute change is still shown


def test_ordinary_large_percentage_within_ceiling_still_shows_movement():
    """A genuinely large swing against a normal-sized base (not a tiny one) must
    still show as a percentage — the ceiling targets distorted math, not every
    big number."""
    rows = [
        _row("CashAndCashEquivalents", None, _I22, 100_000_000),  # 10 cr
        _row("CashAndCashEquivalents", None, _I23, 800_000_000),  # 80 cr, +700%
    ]
    result = build_dashboard(rows, DOC)
    t = _tile(result, "X14")
    assert t["computed"] is True
    assert "+700.00%" in t["movement_label"]
    assert "reason" not in t or not t.get("reason")


def test_every_metric_id_in_registry_appears_exactly_once():
    result = build_dashboard(NBPPL_ROWS, DOC)
    ids = [t["id"] for t in result["tiles"]]
    assert len(ids) == len(set(ids)) == len(BY_ID)


def test_sub_crore_entity_scales_in_lakhs():
    """An entity with all figures below 1 crore (e.g., a startup or SPV) must
    be presented in 'INR lakh' so its balances do not round down to 0.00 INR crore."""
    rows = [
        _row("RevenueFromOperations", _D22, _I22, 4_000_000),  # 40 lakhs
        _row("RevenueFromOperations", _D23, _I23, 5_000_000),  # 50 lakhs (+25%)
        _row("Assets", None, _I22, 7_000_000),                 # 70 lakhs
        _row("Assets", None, _I23, 8_500_000),                 # 85 lakhs
    ]
    result = build_dashboard(rows, DOC)
    assert result["scale_label"] == "INR lakh"

    rev = _tile(result, "X01")
    assert rev["unit_label"] == "INR lakh"
    assert rev["display"] == "50.00"
    assert rev["movement_label"] == "+25.00% vs FY2021-22"
    assert rev["attention"] is False

    ast = _tile(result, "X07")
    assert ast["unit_label"] == "INR lakh"
    assert ast["display"] == "85.00"

    # Explicit scale override to crore
    result_cr = build_dashboard(rows, DOC, scale="crore")
    assert result_cr["scale_label"] == "INR crore"
    rev_cr = _tile(result_cr, "X01")
    assert rev_cr["unit_label"] == "INR crore"
    assert rev_cr["display"] == "0.50"


if __name__ == "__main__":
    import sys
    fns = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failures = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failures}/{len(fns)} passed")
    sys.exit(1 if failures else 0)
