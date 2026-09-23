"""Tests for public_sector_lens.py — Gap-closure Phase 3, Gap #7."""

from sar_prod_v3.observation import Observation
from sar_prod_v3.public_sector_lens import apply_public_sector_lens, classify_public_sector_dimension


def test_classify_matches_a_sensitive_category():
    lens = classify_public_sector_dimension("A grant was received under a Government scheme.")
    assert lens is not None
    assert lens["nature"] == "grants_subsidies"
    assert lens["regularity"] == "requires_review"
    assert lens["propriety"] == "requires_review"


def test_classify_does_not_false_positive_on_generic_regulatory_wording():
    assert classify_public_sector_dimension("Report on Other Legal and Regulatory Requirements") is None


def test_classify_returns_none_for_unrelated_text():
    assert classify_public_sector_dimension("UDIN is present and conforms to the ICAI format.") is None


def test_apply_tags_lens_and_elevates_risk():
    obs = Observation(
        check_id="Z", component="Grant utilisation", tag="RISK_FLAG", risk_rating="Medium",
        observation="grant unspent balance not disclosed", evidence="q",
    )
    apply_public_sector_lens([obs])
    assert obs.public_sector_lens is not None
    assert obs.risk_rating == "High"
    assert obs.caveats


def test_apply_does_not_elevate_information_request_only():
    obs = Observation(
        check_id="Z2", component="Grant matter", tag="AUDIT_POINTER", risk_rating="Information request only",
        observation="grant sanction letter needed", evidence="",
    )
    apply_public_sector_lens([obs])
    assert obs.public_sector_lens is not None  # still tagged
    assert obs.risk_rating == "Information request only"  # but not elevated


def test_apply_leaves_unmatched_observations_untouched():
    obs = Observation(check_id="Y", component="UDIN", tag="AUDIT_POINTER", observation="UDIN missing.", evidence="")
    apply_public_sector_lens([obs])
    assert obs.public_sector_lens is None


def test_apply_never_elevates_past_high():
    obs = Observation(
        check_id="W", component="Guarantee exposure", tag="FINDING", risk_rating="High",
        observation="an unrecorded guarantee was identified", evidence="q",
    )
    apply_public_sector_lens([obs])
    assert obs.risk_rating == "High"
