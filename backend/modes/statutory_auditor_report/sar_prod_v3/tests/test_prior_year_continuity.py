"""Tests for prior_year_continuity.py — Gap-closure Phase 3, Gap #6."""

from sar_prod_v3.observation import Observation
from sar_prod_v3.prior_year_continuity import (
    build_prior_year_summary,
    check_opinion_trend,
    elevate_recurring_observations,
)


def test_build_prior_year_summary_keeps_only_findings_and_high_risk():
    obs = [
        Observation(check_id="A", component="c", tag="FINDING", risk_rating="Medium", observation="x", evidence="q"),
        Observation(check_id="B", component="c", tag="RISK_FLAG", risk_rating="High", observation="x", evidence="q"),
        Observation(check_id="C", component="c", tag="RISK_FLAG", risk_rating="Low", observation="x", evidence="q"),
    ]
    summary = build_prior_year_summary({"opinion": {"type": "qualified"}}, obs, "complete")
    assert summary["opinion_type"] == "qualified"
    check_ids = {r["check_id"] for r in summary["high_risk_observations"]}
    assert check_ids == {"A", "B"}  # FINDING (any risk) + High risk_rating; Low RISK_FLAG excluded


def test_check_opinion_trend_none_without_prior_result():
    assert check_opinion_trend({"opinion": {"type": "qualified"}}, None) is None


def test_check_opinion_trend_recurring_modification():
    obs = check_opinion_trend({"opinion": {"type": "qualified"}}, {"opinion_type": "adverse"})
    assert obs.tag == "RISK_FLAG" and obs.risk_rating == "Medium"


def test_check_opinion_trend_resolved_modification():
    obs = check_opinion_trend({"opinion": {"type": "unmodified"}}, {"opinion_type": "adverse"})
    assert obs.tag == "AUDIT_POINTER"


def test_check_opinion_trend_both_unmodified_is_none():
    assert check_opinion_trend({"opinion": {"type": "unmodified"}}, {"opinion_type": "unmodified"}) is None


def test_check_opinion_trend_new_modification_this_year_is_none():
    """Prior unmodified, current modified — not this rule's concern (it's
    a fresh matter, not a continuity question)."""
    assert check_opinion_trend({"opinion": {"type": "qualified"}}, {"opinion_type": "unmodified"}) is None


def test_elevate_recurring_observations_elevates_matching_check_id():
    obs = [Observation(check_id="CONS-CARO-IX-01", component="c", tag="RISK_FLAG", risk_rating="Medium", observation="x", evidence="q")]
    elevate_recurring_observations(obs, {"high_risk_observations": [{"check_id": "CONS-CARO-IX-01"}]})
    assert obs[0].risk_rating == "High"
    assert obs[0].caveats


def test_elevate_recurring_observations_no_op_without_prior_result():
    obs = [Observation(check_id="X", component="c", tag="RISK_FLAG", risk_rating="Medium", observation="x", evidence="q")]
    elevate_recurring_observations(obs, None)
    assert obs[0].risk_rating == "Medium"
    assert obs[0].caveats == []


def test_elevate_recurring_observations_no_match_leaves_untouched():
    obs = [Observation(check_id="X", component="c", tag="RISK_FLAG", risk_rating="Medium", observation="x", evidence="q")]
    elevate_recurring_observations(obs, {"high_risk_observations": [{"check_id": "OTHER-ID"}]})
    assert obs[0].risk_rating == "Medium"
    assert obs[0].caveats == []


def test_elevate_recurring_observations_already_high_adds_caveat_without_change():
    obs = [Observation(check_id="X", component="c", tag="FINDING", risk_rating="High", observation="x", evidence="q")]
    elevate_recurring_observations(obs, {"high_risk_observations": [{"check_id": "X"}]})
    assert obs[0].risk_rating == "High"
    assert obs[0].caveats  # still notes the recurrence
