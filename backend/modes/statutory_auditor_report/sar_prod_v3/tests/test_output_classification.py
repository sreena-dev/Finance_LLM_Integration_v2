"""Tests for output_classification.py — Gap-closure Phase 4 (output-spec §18)."""

from sar_prod_v3.observation import Observation
from sar_prod_v3.output_classification import apply_output_classification, is_candidate_143_6


def test_known_check_id_gets_consistency_type_and_sa_framework():
    obs = Observation(check_id="CHK-COH-01", component="c", tag="FINDING", risk_rating="High", observation="x", evidence="q")
    apply_output_classification([obs])
    assert obs.consistency_type == "AR_CARO"
    assert obs.sa_framework["standard"] == "SA 705"


def test_source_bucketing_matches_consistency_type():
    obs = Observation(check_id="CHK-COH-03", component="c", tag="RISK_FLAG", risk_rating="High", observation="x", evidence="signal")
    apply_output_classification([obs])
    assert obs.consistency_type == "FS_AR"
    assert obs.source["financial_statements"] == ["signal"]
    assert obs.source["audit_report"] == ["signal"]
    assert obs.source["caro"] == []


def test_within_ar_check_classified_other_not_a_cross_document_type():
    """CHK-COH-02 compares main opinion against IFC — both live inside the
    audit-report package, not a real FS/CARO cross-check."""
    obs = Observation(check_id="CHK-COH-02", component="c", tag="FINDING", risk_rating="High", observation="x", evidence="q")
    apply_output_classification([obs])
    assert obs.consistency_type == "OTHER"


def test_unknown_check_id_gets_safe_default():
    obs = Observation(check_id="NOT-A-REAL-CHECK", component="c", tag="AUDIT_POINTER", observation="x", evidence="")
    apply_output_classification([obs])
    assert obs.consistency_type == "OTHER"
    assert obs.recommended_audit_action  # still gets a fallback action


def test_applicability_and_preflight_share_default_classification():
    a = Observation(check_id="APPL-CARO-01", component="c", tag="AUDIT_POINTER", observation="x", evidence="basis")
    b = Observation(check_id="PRE-06", component="c", tag="AUDIT_POINTER", observation="x", evidence="")
    apply_output_classification([a, b])
    assert a.consistency_type == b.consistency_type == "OTHER"
    assert "applicability" in a.recommended_audit_action[0].lower() or "applicability" in a.recommended_audit_action[0]


def test_rule_specific_recommended_action_is_never_overwritten():
    obs = Observation(
        check_id="CHK-COH-01", component="c", tag="FINDING", risk_rating="High", observation="x", evidence="q",
        recommended_audit_action=["Already set by the rule itself."],
    )
    apply_output_classification([obs])
    assert obs.recommended_audit_action == ["Already set by the rule itself."]


# ---------------------------------------------------------------------------
# is_candidate_143_6
# ---------------------------------------------------------------------------

def test_candidate_143_6_requires_finding_high_and_traceable():
    obs = Observation(check_id="X", component="c", tag="FINDING", risk_rating="High", observation="x", evidence="quote")
    assert is_candidate_143_6(obs) is True


def test_candidate_143_6_false_for_risk_flag_even_if_high():
    obs = Observation(check_id="X", component="c", tag="RISK_FLAG", risk_rating="High", observation="x", evidence="quote")
    assert is_candidate_143_6(obs) is False


def test_candidate_143_6_false_for_finding_below_high():
    obs = Observation(check_id="X", component="c", tag="FINDING", risk_rating="Medium", observation="x", evidence="quote")
    assert is_candidate_143_6(obs) is False


def test_candidate_143_6_false_when_untraceable():
    obs = Observation(check_id="X", component="c", tag="FINDING", risk_rating="High", observation="x", evidence="")
    assert is_candidate_143_6(obs) is False


def test_apply_output_classification_sets_candidate_143_6_flag_on_the_observation():
    obs = Observation(check_id="CHK-COH-01", component="c", tag="FINDING", risk_rating="High", observation="x", evidence="q")
    apply_output_classification([obs])
    assert obs.candidate_143_6 is True
