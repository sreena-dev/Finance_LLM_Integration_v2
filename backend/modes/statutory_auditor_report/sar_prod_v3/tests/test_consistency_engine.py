"""Tests for consistency_engine.py — Gap-closure Phase 2, Gap #3 (partial).

Covers each of the 7 implemented rules firing / not firing, the
applicability gate on the CARO- and IFC-dependent rules, and that the
overall runner never crashes on missing keys.
"""

from sar_prod_v3.consistency_engine import run_consistency_checks

APPLICABLE_ALL = {
    "caro": {"status": "applicable"}, "ifc": {"status": "applicable"}, "kam": {"status": "applicable"},
}


def _ids(obs):
    return [o.check_id for o in obs]


# ---------------------------------------------------------------------------
# Rule 1 — CARO (ix)/(xix) vs going concern (EVAL-03)
# ---------------------------------------------------------------------------

def test_caro_default_with_no_going_concern_discussion_raises_high_risk_flag():
    merged = {
        "CARO_2020": {"high_priority_flags": {"loan_default_ix": True}, "clauses": []},
        "going_concern": {"murgc_paragraph_present": False, "discussed": False},
    }
    obs = run_consistency_checks(merged, {"distress_signals": []}, APPLICABLE_ALL)
    hits = [o for o in obs if o.check_id == "CONS-CARO-IX-01"]
    assert len(hits) == 1
    assert hits[0].tag == "RISK_FLAG" and hits[0].risk_rating == "High"


def test_caro_default_with_going_concern_discussed_does_not_fire():
    merged = {
        "CARO_2020": {"high_priority_flags": {"loan_default_ix": True}, "clauses": []},
        "going_concern": {"murgc_paragraph_present": True, "discussed": True},
    }
    obs = run_consistency_checks(merged, {"distress_signals": []}, APPLICABLE_ALL)
    assert "CONS-CARO-IX-01" not in _ids(obs)


def test_caro_default_gated_when_caro_applicability_uncertain():
    merged = {
        "CARO_2020": {"high_priority_flags": {"loan_default_ix": True}, "clauses": []},
        "going_concern": {"murgc_paragraph_present": False, "discussed": False},
    }
    applicability = {**APPLICABLE_ALL, "caro": {"status": "uncertain"}}
    obs = run_consistency_checks(merged, {"distress_signals": []}, applicability)
    assert "CONS-CARO-IX-01" not in _ids(obs)


# ---------------------------------------------------------------------------
# Rule 2 — fraud silence
# ---------------------------------------------------------------------------

def test_fraud_flag_with_no_echo_raises_risk_flag():
    merged = {"CARO_2020": {"high_priority_flags": {"fraud_xi": True}, "clauses": []}, "other_matter": {}}
    obs = run_consistency_checks(merged, {"distress_signals": []}, APPLICABLE_ALL)
    hits = [o for o in obs if o.check_id == "CONS-CARO-XI-01"]
    assert len(hits) == 1
    assert hits[0].tag == "RISK_FLAG"
    assert "does not conclude" not in hits[0].observation  # sanity: text exists
    assert "fraud occurred" in hits[0].observation.lower() or "not a finding" in hits[0].observation.lower()


def test_fraud_flag_with_echo_does_not_fire():
    merged = {
        "CARO_2020": {"high_priority_flags": {"fraud_xi": True}, "clauses": []},
        "other_matter": {"description": "A fraud matter was reported under section 143(12)."},
    }
    obs = run_consistency_checks(merged, {"distress_signals": []}, APPLICABLE_ALL)
    assert "CONS-CARO-XI-01" not in _ids(obs)


# ---------------------------------------------------------------------------
# Rule 3 / 4 — RPT / statutory dues silence
# ---------------------------------------------------------------------------

def test_rpt_flag_with_no_echo_raises_medium_risk_flag():
    merged = {"CARO_2020": {"high_priority_flags": {"rpt_xiii": True}, "clauses": []}}
    obs = run_consistency_checks(merged, {"distress_signals": []}, APPLICABLE_ALL)
    hits = [o for o in obs if o.check_id == "CONS-CARO-XIII-01"]
    assert len(hits) == 1 and hits[0].risk_rating == "Medium"


def test_rpt_flag_with_kam_echo_does_not_fire():
    merged = {
        "CARO_2020": {"high_priority_flags": {"rpt_xiii": True}, "clauses": []},
        "key_audit_matters": {"present": True, "items": [{"title": "Related party transactions"}]},
    }
    obs = run_consistency_checks(merged, {"distress_signals": []}, APPLICABLE_ALL)
    assert "CONS-CARO-XIII-01" not in _ids(obs)


def test_statutory_dues_flag_with_no_echo_raises():
    merged = {"CARO_2020": {"high_priority_flags": {"statutory_dues_vii": True}, "clauses": []}}
    obs = run_consistency_checks(merged, {"distress_signals": []}, APPLICABLE_ALL)
    assert "CONS-CARO-VII-01" in _ids(obs)


def test_statutory_dues_flag_with_rule11_echo_does_not_fire():
    merged = {
        "CARO_2020": {"high_priority_flags": {"statutory_dues_vii": True}, "clauses": []},
        "rule_11": {"sub_clauses": [{"topic": "statutory dues", "text": "GST dues disputed."}]},
    }
    obs = run_consistency_checks(merged, {"distress_signals": []}, APPLICABLE_ALL)
    assert "CONS-CARO-VII-01" not in _ids(obs)


# ---------------------------------------------------------------------------
# Rule 5 — Rule 11(g) vs IFC (EVAL-06)
# ---------------------------------------------------------------------------

def test_rule11g_adverse_with_unmodified_ifc_raises_high_risk_flag():
    merged = {
        "rule_11": {"sub_clauses": [{"topic": "audit trail", "adverse": True}]},
        "IFC_REPORT": {"ifc_opinion": {"type": "unmodified"}, "material_weaknesses": {"present": False}, "it_controls_mentioned": False},
    }
    obs = run_consistency_checks(merged, {"distress_signals": []}, APPLICABLE_ALL)
    hits = [o for o in obs if o.check_id == "CONS-R11G-01"]
    assert len(hits) == 1 and hits[0].tag == "RISK_FLAG" and hits[0].risk_rating == "High"


def test_rule11g_adverse_with_ifc_weakness_already_noted_does_not_fire():
    merged = {
        "rule_11": {"sub_clauses": [{"topic": "audit trail", "adverse": True}]},
        "IFC_REPORT": {"ifc_opinion": {"type": "unmodified"}, "material_weaknesses": {"present": True}},
    }
    obs = run_consistency_checks(merged, {"distress_signals": []}, APPLICABLE_ALL)
    assert "CONS-R11G-01" not in _ids(obs)


def test_rule11g_gated_when_ifc_applicability_uncertain():
    merged = {
        "rule_11": {"sub_clauses": [{"topic": "audit trail", "adverse": True}]},
        "IFC_REPORT": {"ifc_opinion": {"type": "unmodified"}},
    }
    applicability = {**APPLICABLE_ALL, "ifc": {"status": "uncertain"}}
    obs = run_consistency_checks(merged, {"distress_signals": []}, applicability)
    assert "CONS-R11G-01" not in _ids(obs)


# ---------------------------------------------------------------------------
# Rule 6 — C&AG directions unquantified
# ---------------------------------------------------------------------------

def test_cag_directions_pending_with_no_text_raises_audit_pointer():
    merged = {"cag_directions": {"present": True, "pending_count": 2, "text": ""}}
    obs = run_consistency_checks(merged, {"distress_signals": []}, APPLICABLE_ALL)
    hits = [o for o in obs if o.check_id == "CONS-CAGDIR-01"]
    assert len(hits) == 1 and hits[0].tag == "AUDIT_POINTER"


def test_cag_directions_with_text_does_not_fire():
    merged = {"cag_directions": {"present": True, "pending_count": 2, "text": "All directions addressed; no impact."}}
    obs = run_consistency_checks(merged, {"distress_signals": []}, APPLICABLE_ALL)
    assert "CONS-CAGDIR-01" not in _ids(obs)


# ---------------------------------------------------------------------------
# Rule 7 — KAM vs distress signals
# ---------------------------------------------------------------------------

def test_kam_present_without_liquidity_coverage_raises_medium_risk_flag():
    merged = {"key_audit_matters": {"present": True, "items": [{"title": "Revenue recognition"}]}}
    obs = run_consistency_checks(merged, {"distress_signals": ["Negative net worth detected"]}, APPLICABLE_ALL)
    hits = [o for o in obs if o.check_id == "CONS-KAM-01"]
    assert len(hits) == 1 and hits[0].risk_rating == "Medium"


def test_kam_covering_going_concern_does_not_fire():
    merged = {"key_audit_matters": {"present": True, "items": [{"title": "Going concern assessment"}]}}
    obs = run_consistency_checks(merged, {"distress_signals": ["Negative net worth detected"]}, APPLICABLE_ALL)
    assert "CONS-KAM-01" not in _ids(obs)


def test_kam_absent_is_not_this_rules_concern():
    """Absence of KAM entirely is applicability.py's job (APPL-KAM-01), not
    this rule's — it must not also fire here."""
    merged = {"key_audit_matters": {"present": False}}
    obs = run_consistency_checks(merged, {"distress_signals": ["Negative net worth detected"]}, APPLICABLE_ALL)
    assert "CONS-KAM-01" not in _ids(obs)


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------

def test_runner_never_crashes_on_empty_input():
    assert run_consistency_checks({}, {}, APPLICABLE_ALL) == []


def test_runner_never_crashes_on_missing_applicability_keys():
    assert run_consistency_checks({"CARO_2020": {"high_priority_flags": {"loan_default_ix": True}}}, {}, {}) == []
