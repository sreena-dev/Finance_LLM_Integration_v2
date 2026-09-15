"""Tests for output_formats.py — Gap-closure Phase 4, Formats 1 and 2
(LLM_Output_Specification_CAG_Statutory_Auditor_Report_Review.md §2/§3).
"""

from sar_prod_v3.observation import Observation, assign_observation_ids
from sar_prod_v3.output_classification import apply_output_classification
from sar_prod_v3.output_formats import PackageContext, render_display_response, render_executive_summary

CTX = PackageContext(
    entity="ACME LTD", financial_year="FY 2023-24", scope="standalone",
    opinion_type="Unmodified", review_status="complete", review_status_reasons=[],
)


def _classified(*observations):
    obs = list(observations)
    assign_observation_ids(obs)
    apply_output_classification(obs)
    return obs


def test_display_response_header_and_zero_observation_case():
    """§48-equivalent for Format 1: a clean package must render cleanly,
    not crash or fabricate an observation."""
    out = render_display_response(CTX, [])
    assert out.startswith("ACME LTD / FY 2023-24 / STANDALONE")
    assert "No findings. No risk flags. No audit pointers." in out
    assert "CAVEAT" in out
    assert "AI-assisted review; subject to human audit judgement." in out


def test_display_response_lists_key_observations_most_material_first():
    obs = _classified(
        Observation(check_id="X1", component="Low issue", tag="RISK_FLAG", risk_rating="Low", observation="minor", evidence="q"),
        Observation(check_id="CHK-COH-01", component="High finding", tag="FINDING", risk_rating="High", observation="major", evidence="q"),
    )
    out = render_display_response(CTX, obs)
    # The High FINDING must appear before the Low RISK_FLAG.
    assert out.index("High finding") < out.index("Low issue")


def test_display_response_respects_max_observations_cap():
    obs = _classified(*[
        Observation(check_id=f"X{i}", component=f"Issue {i}", tag="RISK_FLAG", risk_rating="Medium", observation="x", evidence="q")
        for i in range(15)
    ])
    out = render_display_response(CTX, obs, max_observations=5)
    assert out.count("Recommended action:") == 5


def test_display_response_consistency_alerts_show_no_material_inconsistency_when_empty():
    out = render_display_response(CTX, [])
    assert out.count("No material inconsistency identified.") == 4  # all 4 combinations


def test_display_response_consistency_alerts_surface_a_matching_combination():
    obs = _classified(
        Observation(check_id="CHK-COH-01", component="AR/CARO issue", tag="FINDING", risk_rating="High", observation="x", evidence="q"),
    )
    out = render_display_response(CTX, obs)
    assert "AR/CARO issue" in out
    # only 3 of the 4 combinations should still say "no material inconsistency"
    assert out.count("No material inconsistency identified.") == 3


def test_executive_summary_sections_present():
    out = render_executive_summary(CTX, [])
    for heading in [
        "1. Report Identification and Scope", "2. Overall Review Conclusion",
        "3. Statutory Auditor Opinion Summary", "4. Top Findings and Risk Flags",
        "5. FS", "6. Material Silence", "7. Priority Audit Actions",
        "8. Information Limitations",
    ]:
        assert heading in out


def test_executive_summary_candidate_143_6_section_only_appears_when_populated():
    out_clean = render_executive_summary(CTX, [])
    assert "Candidate Section 143(6)" not in out_clean

    obs = _classified(
        Observation(check_id="CHK-COH-01", component="c", tag="FINDING", risk_rating="High", observation="x", evidence="q"),
    )
    out_with_candidate = render_executive_summary(CTX, obs)
    assert "Candidate Section 143(6)" in out_with_candidate


def test_executive_summary_consistency_table_matches_display_response_conclusion():
    """§17 Single Source of Truth: both formats must agree, because both
    are built from the identical observation list."""
    obs = _classified(
        Observation(check_id="CHK-COH-01", component="AR/CARO issue", tag="FINDING", risk_rating="High", observation="x", evidence="q"),
    )
    display = render_display_response(CTX, obs)
    summary = render_executive_summary(CTX, obs)
    assert "AR/CARO issue" in display
    assert "Follow-up required" in summary
    assert "SAR-01" in summary


def test_information_limitations_reflects_non_complete_review_status():
    ctx = PackageContext(
        entity="ACME LTD", financial_year="FY 2023-24", scope="standalone",
        opinion_type="Unmodified", review_status="provisional",
        review_status_reasons=["UDIN unresolved."],
    )
    display = render_display_response(ctx, [])
    summary = render_executive_summary(ctx, [])
    assert "UDIN unresolved." in display
    assert "UDIN unresolved." in summary
