"""Tests for observation.py — the §11 lineage rule, §38 confidence,
§39 observation shape and sequential ID assignment.

See GAP_CLOSURE_LOG.md, Gap #1, for what these enforce and why.
"""

import pytest

from sar_prod_v3.observation import (
    Observation,
    apply_confidence_downgrade,
    assign_observation_ids,
    elevate_risk_rating,
    from_check_result,
    from_preflight_dict,
    stamp_entity_context,
    suppress_untraceable,
)
from sar_prod_v3.tool_sar import CheckResult


# ---------------------------------------------------------------------------
# Construction / validation
# ---------------------------------------------------------------------------

def test_valid_observation_constructs():
    obs = Observation(
        check_id="X-1", component="Test", tag="FINDING",
        observation="something", evidence="a quote",
    )
    assert obs.confidence == "High"  # default
    assert obs.risk_rating == "Information request only"  # default


@pytest.mark.parametrize("field,bad_value", [
    ("tag", "MAYBE"),
    ("risk_rating", "Extreme"),
    ("confidence", "Certain"),
])
def test_invalid_enum_values_rejected(field, bad_value):
    kwargs = dict(check_id="X-1", component="Test", tag="FINDING", observation="x")
    kwargs[field] = bad_value
    with pytest.raises(ValueError):
        Observation(**kwargs)


# ---------------------------------------------------------------------------
# to_dict() — backward-compatible keys + new §39 keys
# ---------------------------------------------------------------------------

def test_to_dict_keeps_pre_phase1_keys_and_adds_new_ones():
    obs = Observation(
        check_id="CHK-COH-01", component="Opinion Coherence", tag="FINDING",
        observation="Unmodified opinion but adverse CARO clauses.",
        risk_rating="High", evidence="Opinion type: Unmodified | CARO adverse: 2",
    )
    d = obs.to_dict()
    # Pre-Phase-1 consumers (agent.build_writer_user_message, the frontend's
    # ReportDocument.jsx) read these exact keys — must not disappear.
    for key in ("check_id", "tag", "component", "observation", "risk_rating", "evidence", "obs_id"):
        assert key in d
    # New §39-contract keys.
    assert d["quoted_report_text"] == d["evidence"]
    assert d["confidence"] == "High"
    assert d["evidence_required"] == []
    assert d["caveats"] == []


# ---------------------------------------------------------------------------
# §11 lineage / suppression
# ---------------------------------------------------------------------------

def test_traceable_observation_is_untouched_by_suppression():
    obs = Observation(
        check_id="X-1", component="Test", tag="FINDING",
        observation="x", evidence="a real quoted extract",
    )
    out = suppress_untraceable([obs])
    assert out[0].tag == "FINDING"
    assert out[0].caveats == []


def test_untraceable_finding_is_downgraded_with_caveat():
    obs = Observation(check_id="X-1", component="Test", tag="FINDING", observation="x", evidence="")
    out = suppress_untraceable([obs])
    assert out[0].tag == "AUDIT_POINTER"
    assert out[0].caveats  # explains why


def test_untraceable_but_has_fs_reference_is_not_downgraded():
    """A financial-statement cross-reference is a real trace even with no
    quoted report text — e.g. a check anchored on 'Note 14' rather than a
    verbatim quote."""
    obs = Observation(
        check_id="X-1", component="Test", tag="RISK_FLAG", observation="x",
        evidence="", financial_statement_reference="Note 14 — Contingent Liabilities",
    )
    out = suppress_untraceable([obs])
    assert out[0].tag == "RISK_FLAG"


def test_audit_pointer_already_and_untraceable_is_a_no_op():
    obs = Observation(check_id="X-1", component="Test", tag="AUDIT_POINTER", observation="x", evidence="")
    out = suppress_untraceable([obs])
    assert out[0].tag == "AUDIT_POINTER"
    assert out[0].caveats == []  # nothing to explain — it was already an AUDIT_POINTER


# ---------------------------------------------------------------------------
# §29.1 / §38 confidence downgrade
# ---------------------------------------------------------------------------

def test_confidence_downgraded_only_when_provisional():
    obs = Observation(check_id="X-1", component="Test", tag="FINDING", observation="x", evidence="q")
    unchanged = apply_confidence_downgrade([obs], "complete")
    assert unchanged[0].confidence == "High"

    obs2 = Observation(check_id="X-1", component="Test", tag="FINDING", observation="x", evidence="q")
    downgraded = apply_confidence_downgrade([obs2], "provisional")
    assert downgraded[0].confidence == "Medium"


def test_confidence_downgrade_never_raises_it():
    obs = Observation(
        check_id="X-1", component="Test", tag="FINDING", observation="x",
        evidence="q", confidence="Low",
    )
    out = apply_confidence_downgrade([obs], "provisional")
    assert out[0].confidence == "Low"  # capped, not raised


# ---------------------------------------------------------------------------
# ID assignment
# ---------------------------------------------------------------------------

def test_ids_assigned_sequentially_across_whole_list():
    obs_list = [
        Observation(check_id="A", component="c", tag="FINDING", observation="x", evidence="q"),
        Observation(check_id="B", component="c", tag="RISK_FLAG", observation="x", evidence="q"),
        Observation(check_id="C", component="c", tag="AUDIT_POINTER", observation="x"),
    ]
    out = assign_observation_ids(obs_list)
    assert [o.observation_id for o in out] == ["SAR-01", "SAR-02", "SAR-03"]


# ---------------------------------------------------------------------------
# Adapters from the pre-existing CheckResult / preflight dict shapes
# ---------------------------------------------------------------------------

def test_from_check_result_carries_all_fields():
    cr = CheckResult(
        check_id="CHK-UDIN-01", tag="AUDIT_POINTER", component="Formal Checks — UDIN",
        passed=False, observation="UDIN missing.", evidence="",
        risk_rating="Information request only",
    )
    obs = from_check_result(cr)
    assert obs.check_id == "CHK-UDIN-01"
    assert obs.tag == "AUDIT_POINTER"
    assert obs.risk_rating == "Information request only"


def test_from_preflight_dict_is_always_traceable_or_already_audit_pointer():
    pf = {
        "check_id": "PRE-06", "tag": "AUDIT_POINTER", "component": "CARO 2020 Annexure",
        "observation": "CARO 2020 Annexure was not identified.",
        "risk_rating": "Information request only",
    }
    obs = from_preflight_dict(pf)
    # Pre-flight observations report an absence — there is nothing to quote
    # by construction — so suppression must be a no-op on them.
    out = suppress_untraceable([obs])
    assert out[0].tag == "AUDIT_POINTER"


# ---------------------------------------------------------------------------
# elevate_risk_rating (Gap-closure Phase 3 — shared by prior_year_continuity.py
# and public_sector_lens.py)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("before,after", [
    ("Low", "Medium"),
    ("Medium", "High"),
    ("High", "High"),  # capped
])
def test_elevate_risk_rating_steps_up_and_caps_at_high(before, after):
    assert elevate_risk_rating(before) == after


def test_elevate_risk_rating_never_touches_information_request_only():
    """Information request only means 'no risk conclusion is possible',
    not 'one step below Low' — elevating it would manufacture a severity
    judgement with no basis."""
    assert elevate_risk_rating("Information request only") == "Information request only"


def test_elevate_risk_rating_unknown_value_passthrough():
    assert elevate_risk_rating("Not A Real Rating") == "Not A Real Rating"


# ---------------------------------------------------------------------------
# Gap-closure Phase 4 additions (output-spec §18)
# ---------------------------------------------------------------------------

def test_new_fields_have_sensible_defaults():
    obs = Observation(check_id="X", component="c", tag="FINDING", observation="x", evidence="q")
    assert obs.entity == "" and obs.financial_year == ""
    assert obs.source == {"financial_statements": [], "audit_report": [], "caro": []}
    assert obs.consistency_type is None
    assert obs.sa_framework is None
    assert obs.recommended_audit_action == []
    assert obs.candidate_143_6 is False
    assert obs.reviewer_status == "Pending"
    assert obs.reviewer_comments == ""


def test_invalid_consistency_type_rejected():
    with pytest.raises(ValueError):
        Observation(check_id="X", component="c", tag="FINDING", observation="x", consistency_type="NOT_A_TYPE")


def test_invalid_reviewer_status_rejected():
    with pytest.raises(ValueError):
        Observation(check_id="X", component="c", tag="FINDING", observation="x", reviewer_status="Ignored")


def test_to_dict_quoted_text_is_a_list_wrapping_evidence():
    obs = Observation(check_id="X", component="c", tag="FINDING", observation="x", evidence="the quote")
    assert obs.to_dict()["quoted_text"] == ["the quote"]

    empty = Observation(check_id="X", component="c", tag="AUDIT_POINTER", observation="x", evidence="")
    assert empty.to_dict()["quoted_text"] == []


def test_stamp_entity_context_fills_every_observation():
    obs = [
        Observation(check_id="A", component="c", tag="FINDING", observation="x", evidence="q"),
        Observation(check_id="B", component="c", tag="RISK_FLAG", observation="x", evidence="q"),
    ]
    stamp_entity_context(obs, entity="ACME LTD", financial_year="FY 2023-24")
    assert all(o.entity == "ACME LTD" and o.financial_year == "FY 2023-24" for o in obs)
