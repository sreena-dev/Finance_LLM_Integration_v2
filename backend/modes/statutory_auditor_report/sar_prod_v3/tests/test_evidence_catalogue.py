"""Tests for evidence_catalogue.py — Gap-closure Phase 3, Gap #9."""

from sar_prod_v3.evidence_catalogue import classify_issue_type, enrich_evidence_required, evidence_for
from sar_prod_v3.observation import Observation


def test_classify_issue_type_matches_going_concern():
    assert classify_issue_type("Going Concern — silence on distress") == "going_concern"


def test_classify_issue_type_matches_caro_statutory_dues():
    assert classify_issue_type("CARO clause (vii) statutory dues flag") == "caro_default_statutory_dues"


def test_classify_issue_type_does_not_false_positive_on_pre04_component_name():
    """PRE-04's own component name contains 'Regulatory' — must not match
    the regulatory_noncompliance-style category just from that word."""
    assert classify_issue_type("Report on Other Legal and Regulatory Requirements (Section 143)") is None


def test_classify_issue_type_returns_none_for_unmatched_text():
    assert classify_issue_type("UDIN is present and conforms to the ICAI format.") is None


def test_evidence_for_known_and_unknown_type():
    assert evidence_for("going_concern")
    assert evidence_for("not_a_real_type") == []


def test_enrich_fills_empty_evidence_required():
    obs = Observation(
        check_id="X", component="Going Concern issue", tag="RISK_FLAG",
        observation="silent on going concern", evidence="q",
    )
    enrich_evidence_required([obs])
    assert obs.evidence_required == evidence_for("going_concern")


def test_enrich_never_overwrites_existing_list():
    obs = Observation(
        check_id="Y", component="RPT", tag="RISK_FLAG", observation="related party silence",
        evidence="q", evidence_required=["already set by the rule itself"],
    )
    enrich_evidence_required([obs])
    assert obs.evidence_required == ["already set by the rule itself"]


def test_enrich_leaves_unmatched_observations_empty():
    obs = Observation(check_id="Z", component="UDIN", tag="AUDIT_POINTER", observation="UDIN missing.", evidence="")
    enrich_evidence_required([obs])
    assert obs.evidence_required == []
