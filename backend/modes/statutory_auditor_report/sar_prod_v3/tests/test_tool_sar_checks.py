"""Tests for the deterministic CheckTools methods touched or added in
Gap-closure Phase 1: check_report_date_sequence (new), the refactored
shared date parser, and check_eom_closing_sentence's evidence fix.
"""

from sar_prod_v3.tool_sar import CheckTools, ComputeTools, _parse_flexible_date


# ---------------------------------------------------------------------------
# _parse_flexible_date — shared helper
# ---------------------------------------------------------------------------

def test_parse_flexible_date_accepts_multiple_formats():
    assert _parse_flexible_date("2024-05-20").isoformat() == "2024-05-20"
    assert _parse_flexible_date("20-05-2024").isoformat() == "2024-05-20"
    assert _parse_flexible_date("20/05/2024").isoformat() == "2024-05-20"
    assert _parse_flexible_date("20 May 2024").isoformat() == "2024-05-20"


def test_parse_flexible_date_returns_none_on_garbage():
    assert _parse_flexible_date("not a date") is None
    assert _parse_flexible_date("") is None


# ---------------------------------------------------------------------------
# check_report_date_sequence — new deterministic §30 check
# ---------------------------------------------------------------------------

def test_report_before_approval_is_a_high_finding():
    r = CheckTools.check_report_date_sequence(
        fs_approval_date="2024-05-20", report_date="2024-05-10",
    )
    assert r.tag == "FINDING"
    assert r.passed is False
    assert r.risk_rating == "High"
    assert "2024-05-10" in r.evidence and "2024-05-20" in r.evidence


def test_report_on_or_after_approval_passes():
    r = CheckTools.check_report_date_sequence(
        fs_approval_date="2024-05-20", report_date="2024-05-20",
    )
    assert r.tag == "FINDING"
    assert r.passed is True

    r2 = CheckTools.check_report_date_sequence(
        fs_approval_date="2024-05-10", report_date="2024-05-20",
    )
    assert r2.passed is True


def test_unparseable_dates_become_audit_pointer_not_a_silent_pass():
    r = CheckTools.check_report_date_sequence(fs_approval_date="", report_date="garbage")
    assert r.tag == "AUDIT_POINTER"
    assert r.passed is False
    assert r.risk_rating == "Information request only"


def test_no_fixed_day_range_benchmark_applied_here():
    """§30: SA 700 sets no numeric day-range threshold for this check —
    a report dated 400 days after approval must still just pass, unlike
    ComputeTools.compute_report_date_gap's separate (informational-only,
    FY-end-based) 90/180-day metric."""
    r = CheckTools.check_report_date_sequence(
        fs_approval_date="2024-03-31", report_date="2025-06-01",
    )
    assert r.tag == "FINDING"
    assert r.passed is True


# ---------------------------------------------------------------------------
# check_eom_closing_sentence — evidence fix (Gap-closure Phase 1)
# ---------------------------------------------------------------------------

def test_eom_closing_sentence_present_carries_quote():
    text = (
        "i. Note 12 discloses a contingent liability.\n"
        "Our opinion on the Financial Statements is not modified in respect of the above matters."
    )
    r = CheckTools.check_eom_closing_sentence(text)
    assert r.passed is True
    assert r.evidence  # a real quote


def test_eom_closing_sentence_absent_still_carries_searched_text_as_evidence():
    """Before Gap-closure Phase 1 this branch returned evidence="", which
    made the lineage rule (observation.suppress_untraceable) downgrade a
    legitimate, directly-verifiable FINDING to an AUDIT_POINTER. It must
    carry the searched text so the finding stays traceable."""
    text = "i. Note 12 discloses a contingent liability with no further comment."
    r = CheckTools.check_eom_closing_sentence(text)
    assert r.passed is False
    assert r.tag == "FINDING"
    assert r.risk_rating == "High"
    assert r.evidence.strip() != ""
    assert "Note 12" in r.evidence


def test_eom_closing_sentence_empty_input_is_audit_pointer():
    r = CheckTools.check_eom_closing_sentence("")
    assert r.tag == "AUDIT_POINTER"
    assert r.passed is False


# ---------------------------------------------------------------------------
# compute_report_date_gap — unchanged behaviour after the _parse refactor
# ---------------------------------------------------------------------------

def test_compute_report_date_gap_unchanged_after_refactor():
    g = ComputeTools.compute_report_date_gap("2024-03-31", "2024-05-15")
    assert g["assessment"] == "normal"
    assert g["gap_days"] == 45

    g2 = ComputeTools.compute_report_date_gap("2024-03-31", "2024-03-01")
    assert g2["assessment"] == "premature"
    assert g2["flag"] is True
