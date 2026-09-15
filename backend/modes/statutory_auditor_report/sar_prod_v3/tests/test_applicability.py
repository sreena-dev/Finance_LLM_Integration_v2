"""Tests for applicability.py — Gap-closure Phase 2, Gap #4.

Covers all three areas' three states (applicable / not_applicable /
uncertain) and the AUDIT_POINTER generation for "uncertain" only.
"""

from sar_prod_v3.applicability import (
    applicability_observations,
    resolve_applicability,
    resolve_caro_applicability,
    resolve_ifc_applicability,
    resolve_kam_applicability,
)


# ---------------------------------------------------------------------------
# CARO
# ---------------------------------------------------------------------------

def test_caro_applicable_when_text_found():
    r = resolve_caro_applicability({"caro_applicable": True}, caro_text_found=True, caro_raw_text="clause i ...")
    assert r["status"] == "applicable"


def test_caro_not_applicable_on_explicit_statement():
    text = "The provisions of the Companies (Auditor's Report) Order, 2020 (CARO) are not applicable to the Company."
    r = resolve_caro_applicability({}, caro_text_found=True, caro_raw_text=text)
    assert r["status"] == "not_applicable"


def test_caro_not_applicable_when_extractor_says_so():
    r = resolve_caro_applicability({"caro_applicable": False}, caro_text_found=True, caro_raw_text="")
    assert r["status"] == "not_applicable"


def test_caro_uncertain_when_nothing_found():
    r = resolve_caro_applicability({}, caro_text_found=False, caro_raw_text="")
    assert r["status"] == "uncertain"


# ---------------------------------------------------------------------------
# IFC
# ---------------------------------------------------------------------------

def test_ifc_applicable_when_opinion_resolved():
    r = resolve_ifc_applicability({"ifc_opinion": {"type": "unmodified"}}, ifc_text_found=True, ifc_raw_text="...")
    assert r["status"] == "applicable"


def test_ifc_not_applicable_on_explicit_statement():
    text = "The Company is not required to report under Section 143(3)(i) as it is a small company."
    r = resolve_ifc_applicability({}, ifc_text_found=False, ifc_raw_text=text)
    assert r["status"] == "not_applicable"


def test_ifc_uncertain_never_assumed_not_applicable():
    """IFC applies to nearly every company — absence alone must resolve to
    'uncertain', never silently to 'not_applicable'."""
    r = resolve_ifc_applicability({}, ifc_text_found=False, ifc_raw_text="")
    assert r["status"] == "uncertain"


# ---------------------------------------------------------------------------
# KAM
# ---------------------------------------------------------------------------

def test_kam_applicable_when_section_found():
    r = resolve_kam_applicability({"key_audit_matters": {"present": True}})
    assert r["status"] == "applicable"


def test_kam_never_resolves_to_not_applicable():
    """This module has no positive signal (listed status) that would ever
    justify concluding KAM is not applicable — absence must always be
    'uncertain', per source spec §19.1."""
    r = resolve_kam_applicability({"key_audit_matters": {"present": False}})
    assert r["status"] == "uncertain"
    r2 = resolve_kam_applicability({})
    assert r2["status"] == "uncertain"


# ---------------------------------------------------------------------------
# Combined resolution + observation generation
# ---------------------------------------------------------------------------

def test_resolve_applicability_combines_all_three():
    merged = {
        "CARO_2020": {"caro_applicable": True},
        "IFC_REPORT": {"ifc_opinion": {"type": "qualified"}},
        "key_audit_matters": {"present": True},
    }
    result = resolve_applicability(
        merged, caro_text_found=True, caro_raw_text="x", ifc_text_found=True, ifc_raw_text="y",
    )
    assert result["caro"]["status"] == "applicable"
    assert result["ifc"]["status"] == "applicable"
    assert result["kam"]["status"] == "applicable"


def test_applicability_observations_only_for_uncertain():
    applicability = {
        "caro": {"status": "applicable", "basis": "x"},
        "ifc": {"status": "not_applicable", "basis": "y"},
        "kam": {"status": "uncertain", "basis": "z"},
    }
    obs = applicability_observations(applicability)
    assert len(obs) == 1
    assert obs[0].check_id == "APPL-KAM-01"
    assert obs[0].tag == "AUDIT_POINTER"
    assert obs[0].risk_rating == "Information request only"


def test_applicability_observations_empty_when_all_resolved():
    applicability = {
        "caro": {"status": "applicable", "basis": "x"},
        "ifc": {"status": "not_applicable", "basis": "y"},
        "kam": {"status": "applicable", "basis": "z"},
    }
    assert applicability_observations(applicability) == []
