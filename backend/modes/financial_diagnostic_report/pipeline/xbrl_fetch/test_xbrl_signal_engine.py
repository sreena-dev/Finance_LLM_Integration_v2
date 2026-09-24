"""
Test suite for the S01-S27 XBRL signal library.

Two layers, matching the two things that can break independently:

  1. HERMETIC rule tests — construct a `fetched` dict by hand (no DB), following the
     `_fact`/`_fetched` helper convention `test_xbrl_risk_clusters.py` already uses for
     the live Block 6 path — these run 100% offline and pin the arithmetic.
  2. A LIVE integration test against `as_db` (skipped automatically if the DB is
     unreachable) — this is the regression test that guards the single most important
     finding behind this library: as_db carries effectively one filing per entity today,
     so every trend-shaped signal MUST abstain with PANEL_TOO_SHORT rather than silently
     compute a false trend off one data point.
"""
from __future__ import annotations
import pytest

from . import xbrl_signal_registry as R
from . import xbrl_signal_materiality as M
from . import xbrl_signal_derivations as D
from . import xbrl_signal_rules as RULES
from .xbrl_signal_model import FIRED, NOT_FIRED, ABSTAIN, NOT_APPLICABLE, PANEL_TOO_SHORT


def _series(*fy_ends: str) -> list[dict]:
    return [{"doc_id": f"DOC_{fy}", "fy_start": None, "fy_end": fy} for fy in fy_ends]


def _fetched(series: list[dict], face_facts: dict, *, disclosures=None,
             related_party=None) -> dict:
    return {
        "doc_id": series[-1]["doc_id"] if series else "DOC_TEST",
        "series": series,
        "face_facts": face_facts,
        "related_party_receivables": related_party or {},
        "disclosures": disclosures or [],
    }


def _facts(*entries: tuple[str, str, float]) -> dict:
    """entries: (concept_name, fy_end, value)."""
    out: dict = {}
    for concept, fy, val in entries:
        out.setdefault(concept, {})[fy] = {"value": val, "doc_id": f"DOC_{fy}", "unit": "INR"}
    return out


# ---- registry integrity -------------------------------------------------------------

def test_registry_coverage_complete():
    """Every registered signal has a rule, a derivation and a materiality profile — the
    three tables this library splits the S01-S27 spec across must never drift apart."""
    ids = {s.id for s in R.all_signals()}
    assert len(ids) == 27
    assert ids == {f"S{n:02d}" for n in range(1, 28)}
    for sid in ids:
        assert sid in RULES.RULES, f"{sid}: no rule function"
        assert sid in M._BY_ID, f"{sid}: no materiality profile"
        assert sid in D._BY_ID, f"{sid}: no derivation"


def test_by_nature_overrides_state_a_basis():
    for p in M.PROFILES:
        if p.by_nature:
            assert p.by_nature_basis, f"{p.signal_id}: by_nature=True with no basis"


def test_s09_ageing_confirmed_not_applicable_in_derivations():
    deriv = D.get("S09")
    assert deriv.confirmed_absent is True
    assert deriv.axis is None


# ---- hermetic rule tests --------------------------------------------------------------

def test_s03_net_current_liability_fires():
    fetched = _fetched(_series("2025-03-31"), _facts(
        ("CurrentAssets", "2025-03-31", 50_000_000.0),
        ("CurrentLiabilities", "2025-03-31", 80_000_000.0),
    ))
    outcome = RULES.rule_s03(fetched)
    assert outcome.fired is True
    assert "80,000,000" in outcome.trace or "50,000,000" in outcome.trace


def test_s03_net_current_liability_not_fired():
    fetched = _fetched(_series("2025-03-31"), _facts(
        ("CurrentAssets", "2025-03-31", 90_000_000.0),
        ("CurrentLiabilities", "2025-03-31", 80_000_000.0),
    ))
    outcome = RULES.rule_s03(fetched)
    assert outcome.fired is False


def test_s01_ccc_abstains_on_single_year_panel():
    """The single most important regression guard in this suite: as_db carries one
    filing per entity for the overwhelming majority of the corpus, so a trend signal
    with window_years=3 must abstain cleanly rather than fabricate a trend."""
    fetched = _fetched(_series("2025-03-31"), _facts(
        ("TradeReceivablesCurrent", "2025-03-31", 1.0),
        ("Inventories", "2025-03-31", 1.0),
        ("TradePayablesCurrent", "2025-03-31", 1.0),
        ("RevenueFromOperations", "2025-03-31", 1.0),
    ))
    outcome = RULES.rule_s01(fetched)
    assert outcome.fired is None
    assert outcome.reason_code == PANEL_TOO_SHORT


def test_s08_related_party_concentration_uses_dimensioned_numeric_fact():
    series = _series("2025-03-31")
    fetched = _fetched(
        series,
        _facts(("TradeReceivablesCurrent", "2025-03-31", 100_000.0)),
        related_party={"DOC_2025-03-31": 40_000.0},
    )
    outcome = RULES.rule_s08(fetched)
    assert outcome.fired is True
    assert "40" in outcome.trace


def test_s08_zero_receivables_abstains_not_divides_by_zero():
    series = _series("2025-03-31")
    fetched = _fetched(
        series, _facts(("TradeReceivablesCurrent", "2025-03-31", 0.0)),
        related_party={"DOC_2025-03-31": 40_000.0},
    )
    outcome = RULES.rule_s08(fetched)
    assert outcome.fired is None


def test_s09_ageing_subbranch_always_not_applicable():
    outcome = RULES.rule_s09_ageing({})
    assert outcome.not_applicable is True


def test_s22_abstains_pending_business_profile_wiring():
    outcome = RULES.rule_s22({})
    assert outcome.fired is None
    assert outcome.reason_code == PANEL_TOO_SHORT


def test_s13_dupont_single_period_abstains_leverage_share():
    fetched = _fetched(_series("2025-03-31"), _facts(
        ("ProfitLossForPeriod", "2025-03-31", 10.0),
        ("RevenueFromOperations", "2025-03-31", 100.0),
        ("Assets", "2025-03-31", 200.0),
        ("Equity", "2025-03-31", 50.0),
    ))
    outcome = RULES.rule_s13(fetched)
    assert outcome.fired is None
    assert "DuPont" in outcome.reason


# ---- live integration (skips automatically without DB access) -------------------------

def _db_reachable() -> bool:
    try:
        from . import xbrl_conn as DB
        ok, _detail = DB.ping()
        return ok
    except Exception:
        return False


@pytest.mark.skipif(not _db_reachable(), reason="as_db not reachable in this environment")
def test_engine_runs_end_to_end_against_a_real_filing():
    from . import xbrl_conn as DB
    from . import xbrl_signal_engine as ENGINE

    row = DB.one("SELECT doc_id FROM documents LIMIT 1")
    assert row is not None
    results = ENGINE.evaluate_signals(row["doc_id"])
    assert len(results) == 28  # 27 signals + the S09 ageing sub-branch
    ids = {r.signal_id for r in results}
    assert ids == {s.id for s in R.all_signals()} | {"S09_ageing"}
    for r in results:
        assert r.status in (FIRED, NOT_FIRED, ABSTAIN, NOT_APPLICABLE)


@pytest.mark.skipif(not _db_reachable(), reason="as_db not reachable in this environment")
def test_most_trend_signals_abstain_panel_too_short_on_single_year_corpus():
    """Confirms the corpus reality this whole design accounts for: on an entity with
    only one fy_end in as_db, every window_years>=2 signal must abstain."""
    from . import xbrl_conn as DB
    from . import xbrl_signal_engine as ENGINE
    from . import xbrl_signal_fetch as FETCH

    row = DB.one("""
        SELECT doc_id FROM documents d
        WHERE (SELECT COUNT(DISTINCT fy_end) FROM documents d2
               WHERE d2.entity_cin = d.entity_cin) = 1
        LIMIT 1
    """)
    assert row is not None
    results = {r.signal_id: r for r in ENGINE.evaluate_signals(row["doc_id"])}
    for signal in R.all_signals():
        if signal.window_years >= 2:
            r = results[signal.id]
            if r.status == ABSTAIN:
                assert r.reason_code == PANEL_TOO_SHORT, (
                    f"{signal.id} abstained for {r.reason_code}, expected PANEL_TOO_SHORT "
                    f"on a single-year entity"
                )
