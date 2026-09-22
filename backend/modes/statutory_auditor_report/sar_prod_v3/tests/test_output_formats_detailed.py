"""Tests for output_formats.render_detailed_report() — Gap-closure Phase 5,
Format 3 (LLM_Output_Specification_CAG_Statutory_Auditor_Report_Review.md §4/§5).
"""

from sar_prod_v3.observation import Observation, assign_observation_ids
from sar_prod_v3.output_classification import apply_output_classification
from sar_prod_v3.output_formats import PackageContext, render_detailed_report

CTX = PackageContext(
    entity="ACME LTD", financial_year="FY 2023-24", scope="standalone",
    opinion_type="Unmodified", review_status="complete", review_status_reasons=[],
)

APPLICABLE_ALL = {
    "caro": {"status": "applicable", "basis": "found"},
    "ifc": {"status": "applicable", "basis": "found"},
    "kam": {"status": "applicable", "basis": "found"},
}


def _classified(*observations):
    obs = list(observations)
    assign_observation_ids(obs)
    apply_output_classification(obs)
    return obs


def _render(obs=(), **overrides):
    kwargs = dict(
        merged_json={"CARO_2020": {}}, applicability=APPLICABLE_ALL,
        quality_flags={"main_text": "reliable"}, doc_meta={"doc_id": "X", "doc_name": "X Annual Report", "total_pages": 100},
        existing_report_md="## PART 1\nsummary\n## PART 2 — DETAILED MEMORANDUM\nnarrative body here",
    )
    kwargs.update(overrides)
    return render_detailed_report(CTX, list(obs), **kwargs)


def test_all_16_sections_present_on_a_clean_package():
    """Every one of the 16 numbered headings must appear, in order, with no
    gaps — §10 and §11 included, even on a standalone package with no
    prior-year matters (the case that used to skip both headings entirely
    and jump straight from "9." to "12.")."""
    out = _render()
    for heading in [
        "1. Report Control Sheet", "2. Executive Summary", "3. Input Package and Review Status",
        "4. Scope and Assignment Context", "5-6. Statutory Auditor's Opinion Analysis",
        "7. FS – Audit Report – CARO Consistency Analysis", "8. Silence and Material Gap Analysis",
        "9. Public-Sector / High-Sensitivity Matters", "10. Consolidated / Group Matters",
        "11. Prior-Year / Continuing Matters", "12. Observations, Audit Pointers and Evidence Required",
        "13. Candidate Matters for Consideration under Section 143(6)", "14. Limitations and Caveats",
        "15. Reviewer Action Summary", "16. Annexures", "Annexure A", "Annexure B", "Annexure C", "Annexure E",
    ]:
        assert heading in out, f"missing: {heading}"


def test_section_10_heading_always_present_not_applicable_for_standalone_scope():
    """§10's heading must stay in the TOC even when it doesn't apply — the
    old behaviour (omitting the heading entirely for standalone scope) made
    the numbering jump straight from "9." to "12.", silently skipping 10
    and 11, which read as broken numbering rather than "not applicable"."""
    out = _render()
    assert "10. Consolidated / Group Matters" in out
    assert "Not applicable — this review's scope is standalone, not consolidated." in out


def test_section_10_included_for_consolidated_scope():
    ctx = PackageContext(
        entity="ACME LTD", financial_year="FY 2023-24", scope="consolidated",
        opinion_type="Unmodified", review_status="complete", review_status_reasons=[],
    )
    out = render_detailed_report(
        ctx, [], merged_json={"CARO_2020": {}}, applicability=APPLICABLE_ALL,
        quality_flags={}, doc_meta={}, existing_report_md="",
    )
    assert "10. Consolidated / Group Matters" in out
    assert "Group-audit-specific checks are not yet implemented" in out


def test_section_11_heading_always_present_none_identified_without_prior_year_observation():
    out = _render()
    assert "11. Prior-Year / Continuing Matters" in out
    assert "No prior-year or continuing matters were identified from the supplied package." in out


def test_section_11_included_with_prior_year_observation():
    obs = _classified(Observation(
        check_id="PRIOR-OPN-01", component="c", tag="RISK_FLAG", risk_rating="Medium",
        observation="Prior-year opinion was qualified; current also qualified.", evidence="q",
    ))
    out = _render(obs)
    assert "11. Prior-Year / Continuing Matters" in out


def test_section_9_default_sentence_when_no_public_sector_matters():
    out = _render()
    assert "No separately reportable public-sector sensitivity was identified from the supplied package." in out


def test_narrative_sections_embed_existing_report_part_2():
    out = _render()
    assert "narrative body here" in out


def test_candidate_143_6_section_lists_only_flagged_observations():
    obs = _classified(
        Observation(check_id="CHK-COH-01", component="Opinion issue", tag="FINDING", risk_rating="High", observation="major", evidence="q"),
        Observation(check_id="X2", component="Minor issue", tag="RISK_FLAG", risk_rating="Low", observation="minor", evidence="q"),
    )
    out = _render(obs)
    assert "Candidate Matter SAR-01" in out
    assert "Candidate Matter SAR-02" not in out
    assert "STATUS: DRAFT" not in out  # exact casing check below instead
    assert "DRAFT — HUMAN REVIEW REQUIRED" in out


def test_section_12_register_has_one_row_per_observation():
    obs = _classified(
        Observation(check_id="A", component="c1", tag="FINDING", risk_rating="High", observation="x1", evidence="q"),
        Observation(check_id="B", component="c2", tag="RISK_FLAG", risk_rating="Medium", observation="x2", evidence="q"),
    )
    out = _render(obs)
    assert out.count("| SAR-0") >= 2  # both appear in the summary table rows


def test_annexure_c_reflects_caro_clauses_from_merged_json():
    merged = {"CARO_2020": {"clauses": [
        {"clause_no": "i", "answer": "clean"},
        {"clause_no": "vii", "answer": "partial", "verbatim_quote": "Some GST dues remained unpaid."},
    ]}}
    out = _render(merged_json=merged)
    assert "(i)" in out and "No material inconsistency identified." in out
    assert "(vii)" in out and "Some GST dues remained unpaid." in out


def test_annexure_d_only_appears_with_direction_observations():
    out_without = _render()
    assert "Annexure D" not in out_without

    obs = _classified(Observation(
        check_id="DIR-I-01", component="C&AG Direction I", tag="RISK_FLAG", risk_rating="Medium",
        observation="not evidenced", evidence="q",
    ))
    out_with = _render(obs)
    assert "Annexure D" in out_with


def test_renders_without_crashing_on_a_fully_empty_package():
    out = render_detailed_report(
        CTX, [], merged_json={}, applicability={}, quality_flags={}, doc_meta={}, existing_report_md="",
    )
    assert "Report Control Sheet" in out


def test_section_2_does_not_embed_full_executive_summary():
    """§2 used to embed render_executive_summary()'s complete output
    verbatim — its own H1 title plus a nested "1."-"8." numbering that
    duplicated Top Findings (§4), the CARO consistency table (§7) and
    Silence matters (§8) later in this same document. §2 must stay a short
    synopsis, not a second copy of Format 2."""
    obs = _classified(Observation(
        check_id="DIR-I-01", component="C&AG Direction I", tag="RISK_FLAG", risk_rating="Medium",
        observation="A standing C&AG direction is in effect for this report's date.", evidence="q",
    ))
    out = _render(obs)
    assert "# Executive Summary" not in out  # the embedded format's own H1
    assert "3. Statutory Auditor Opinion Summary" not in out  # its nested numbering
    # The observation's full text must appear exactly where §9/§12 put it —
    # not a third time inside §2's old nested "4. Top Findings" list.
    assert out.count("A standing C&AG direction is in effect for this report's date.") == 2


def test_section_12_does_not_repeat_observation_as_why_it_matters():
    """When neither `audit_risk` nor `gap` is set (the common case for
    AUDIT_POINTER/RISK_FLAG checks), §12 used to print the identical
    observation sentence a second time as "Why It Matters"/"Why it
    matters" — once in the table cell, once again in the narrative block
    right below it, immediately next to the sentence it was repeating."""
    obs = _classified(Observation(
        check_id="PRE-05", component="Rule 11 Sub-clauses", tag="AUDIT_POINTER",
        risk_rating="Information request only",
        observation="Rule 11 sub-clauses were not identified.",
    ))
    out = _render(obs)
    assert out.count("Rule 11 sub-clauses were not identified.") == 2  # table + "- Issue:" only
    assert "Why it matters: Rule 11 sub-clauses were not identified." not in out
    # The table's "Why It Matters" column renders "—" instead of repeating
    # the observation text that's already in the same row's "Observation" column.
    assert "| — | — | Pending |" in out
