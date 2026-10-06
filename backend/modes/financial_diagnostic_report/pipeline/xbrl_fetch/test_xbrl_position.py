"""Negative / nil / missing equity must read the same, and truthfully, in every block.

Regression for: DuPont forcing the equity multiplier to 0.00x, a -1.0 D/E sentinel
reaching the LLM prompt, and RC04 reporting "conservative leverage" (or nothing) for
a filing with large borrowings against negative net worth. Pure synthetic data - no DB.
"""
from __future__ import annotations
import datetime as dt
import pytest

from . import xbrl_position as POS
from . import xbrl_trends as XT
from . import xbrl_business_profile as XBP
from . import xbrl_risk_clusters as XRC
from . import xbrl_risk_signals as XRS
from . import xbrl_health as XH

FY = dt.date(2025, 3, 31)
FY_PREV = dt.date(2024, 3, 31)
CR = 1e7


def _rows(equity, borr_cur=47.6 * CR, borr_nc=128.8 * CR, fy=FY):
    facts = {
        "Equity": equity, "BorrowingsCurrent": borr_cur, "BorrowingsNoncurrent": borr_nc,
        "Assets": 500 * CR, "RevenueFromOperations": 100 * CR, "ProfitLossForPeriod": -20 * CR,
        "CurrentAssets": 50 * CR, "CurrentLiabilities": 120 * CR,
    }
    return [{"concept_name": k, "fy_start": None, "fy_end": fy, "value_numeric": v}
            for k, v in facts.items() if v is not None]


# ---------------------------------------------------------------- shared helper
@pytest.mark.parametrize("equity,borr,status,de", [
    (100.0, 150.0, POS.OK, 1.5),
    (-5.0, 150.0, POS.NEGATIVE_NET_WORTH, None),
    (0.0, 150.0, POS.NIL_EQUITY, None),
    (None, 150.0, POS.MISSING, None),
    (100.0, None, POS.MISSING, None),
])
def test_classify_leverage(equity, borr, status, de):
    lev = POS.classify_leverage(equity, borr)
    assert lev.status == status and lev.de_ratio == de


def test_no_sentinel_or_zero_for_undefined_ratio():
    for equity in (-5.0, 0.0, None):
        lev = POS.classify_leverage(equity, 150.0)
        assert lev.de_ratio is None
        assert "-1.00" not in lev.de_display() and lev.de_display() != "0.00x"


def test_multiplier_is_signed_not_zeroed():
    assert POS.equity_multiplier(500.0, -250.0) == -2.0
    assert POS.equity_multiplier(500.0, 0.0) is None
    assert POS.equity_multiplier(None, 100.0) is None


# --------------------------------------------------------------------- DuPont
def _panel(equity_prev, equity_now):
    def p(d, eq):
        return {"period_date": d, "assets": 500.0, "equity": eq, "revenue": 100.0, "pat": 10.0}
    return {"panel": [p("FY2023-24", equity_prev), p("FY2024-25", equity_now)], "series_years": 2}


def test_dupont_negative_equity_shows_signed_multiplier_and_withholds_roe():
    out = XT.compute_dupont_decomposition(_panel(-200.0, -250.0))
    latest = out["periods"][-1]
    assert latest["multiplier_display"] == "-2.00×"
    assert "0.00×" not in latest["multiplier_display"]
    assert latest["roe"] is None and latest["roe_display"].startswith("n/m")
    assert out["attribution"] == {} and out["attribution_note"]
    assert out["leverage_dominated"] is False


def test_dupont_mixed_signs_withholds_attribution():
    out = XT.compute_dupont_decomposition(_panel(200.0, -250.0))
    assert out["periods"][0]["roe"] is not None and out["periods"][1]["roe"] is None
    assert out["attribution"] == {}


def test_dupont_positive_equity_unchanged():
    out = XT.compute_dupont_decomposition(_panel(200.0, 250.0))
    latest = out["periods"][-1]
    assert latest["multiplier_display"] == "2.00×"
    assert latest["roe"] == pytest.approx(0.10 * 0.2 * 2.0)
    assert set(out["attribution"]) == {"delta_roe", "margin_contrib", "turnover_contrib", "leverage_contrib"}


# ------------------------------------------------------- business profile (Sec 02)
def test_profile_never_emits_minus_one_for_negative_equity():
    figures, lookup = XBP.extract_grounded_figures(_rows(-351.5 * CR))
    p03 = next(f for f in figures if f.id == "P03")
    assert p03.value is None and "negative net worth" in p03.display
    assert lookup["debt_equity"] is None and lookup["leverage_status"] == POS.NEGATIVE_NET_WORTH
    assert all(f.value != -1.0 for f in figures)


# ------------------------------------------------------------------- RC04
def test_rc04_fires_on_borrowings_against_negative_net_worth():
    rows = _rows(-351.5 * CR)
    signals, lookup = XRS.detect_signals_and_metrics(rows, [])
    assert any(s["id"] == "SIG_NEGATIVE_NET_WORTH" and s["cluster_id"] == "RC04" for s in signals)
    out = XRC.build_risk_clusters("d", "X", "C", "FY", rows, [], use_llm=False)
    rc04 = next(c for c in out["risk_clusters"] if c["id"] == "RC04")
    assert rc04["raised"] and rc04["significant_risk"] is True
    metrics = {m["label"]: m["value"] for m in rc04["metrics"]}
    assert "176.40 cr" in metrics["Total borrowings"] and "negative net worth" in metrics["Debt-to-equity ratio"]


def test_rc04_not_raised_wording_is_conditional_not_boilerplate():
    ok = XRC.build_risk_clusters("d", "X", "C", "FY", _rows(500 * CR, 10 * CR, 10 * CR), [], use_llm=False)
    reason = next(c for c in ok["risk_clusters"] if c["id"] == "RC04")["reason"]
    assert "0.04x" in reason and "nil borrowings" not in reason

    rows = [r for r in _rows(None) if r["concept_name"] != "Equity"]
    miss = XRC.build_risk_clusters("d", "X", "C", "FY", rows, [], use_llm=False)
    rc = next(c for c in miss["risk_clusters"] if c["id"] == "RC04")
    assert rc["reason"].startswith("Not assessed") and rc["diagnostic_confidence"] == "low"


# ------------------------------------------- one borrowings figure across blocks
def test_borrowings_identical_across_profile_risk_and_health():
    rows = _rows(-351.5 * CR)
    expected = 47.6 * CR + 128.8 * CR
    _, prof = XBP.extract_grounded_figures(rows)
    _, risk = XRS.detect_signals_and_metrics(rows, [])
    assert prof["borrowings"] == pytest.approx(expected)
    assert risk["borrowings"] == pytest.approx(expected)
    assert risk["leverage_status"] == prof["leverage_status"] == POS.NEGATIVE_NET_WORTH


def test_dupont_missing_assets_is_not_a_zero_multiplier():
    series = {"panel": [{"period_date": "FY2022-23", "assets": 0.0, "equity": 350.0, "revenue": 0.0, "pat": 0.0},
                        {"period_date": "FY2023-24", "assets": 500.0, "equity": 250.0, "revenue": 100.0, "pat": 10.0}],
              "series_years": 2}
    out = XT.compute_dupont_decomposition(series)
    assert out["periods"][0]["multiplier_display"] == "n/m"
    assert out["periods"][0]["roe"] is None and out["attribution"] == {}


# ---- safe-language linters must not rewrite the inside of ordinary words ------------------
def test_linters_do_not_corrupt_words_that_contain_a_prohibited_phrase():
    from . import xbrl_trends as T, xbrl_risk_text as RTX, xbrl_business_profile as BP, xbrl_health as H
    text = "ROE improved and margins improves; the board approves; proved, not fraudulently."
    assert T._clean_str(text).startswith("ROE improved and margins improves; the board approves;")
    assert H._clean_str(text).startswith("ROE improved and margins improves; the board approves;")
    out = RTX.lint_risk_clusters({"risk_clusters": [{"theme": "Margins improves", "evidence_request": "approves", "alt_explanations": ["improved"]}]})
    c = out["risk_clusters"][0]
    assert c["theme"] == "Margins improves" and c["evidence_request"] == "approves" and c["alt_explanations"] == ["improved"]
    assert BP.lint_business_profile({"x": "Profit improves"}, [])["x"] == "Profit improves"


def test_linters_still_replace_the_prohibited_words_themselves():
    from . import xbrl_trends as T, xbrl_risk_text as RTX
    assert "proves" not in T._clean_str("This proves the point").lower()
    out = RTX.lint_risk_clusters({"risk_clusters": [{"theme": "It confirms and proves it", "alt_explanations": [], "recommended_response": {}}]})
    t = out["risk_clusters"][0]["theme"]
    assert "proves" not in t and "confirms" not in t
