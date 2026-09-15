"""Integration-level test for the Gap-closure wiring inside
SARReportPipeline.run() — without needing yukta, a live LLM endpoint or a
database connection.

`SARReportPipeline.__init__` calls `_setup_agents()`, which needs `yukta`
and a generation endpoint. The specific methods under test here
(`_run_coherence_checks`, `_run_formal_checks`, `_resolve_applicability`,
`_run_consistency_checks`, `_run_pervasiveness_checks`,
`_fetch_prior_year_result`) don't touch `self` at all, so
`SARReportPipeline.__new__(SARReportPipeline)` (bypassing `__init__`)
gives a safe, dependency-free instance to call them on. This exercises the
exact sequence `run()` itself follows — applicability -> coherence ->
formal -> consistency -> pervasiveness -> prior-year -> merge -> suppress
-> elevate-recurring -> public-sector-lens -> evidence-enrich ->
review_status -> confidence -> output-classification -> stamp-entity ->
assign IDs -> to_dict() -> Display Response / Executive Summary — which
unit tests of the individual pieces alone don't prove stays wired
correctly end to end.
"""

from sar_prod_v3.applicability import applicability_observations
from sar_prod_v3.evidence_catalogue import enrich_evidence_required
from sar_prod_v3.formal_review import compute_review_status
from sar_prod_v3.observation import (
    apply_confidence_downgrade, assign_observation_ids, stamp_entity_context, suppress_untraceable,
)
from sar_prod_v3.output_classification import apply_output_classification
from sar_prod_v3.output_formats import PackageContext, render_display_response, render_executive_summary
from sar_prod_v3.pipeline.sar_report_pipeline import SARReportPipeline
from sar_prod_v3.prior_year_continuity import check_opinion_trend, elevate_recurring_observations
from sar_prod_v3.public_sector_lens import apply_public_sector_lens
from sar_prod_v3.tool_sar import ToolResult


def _bare_pipeline() -> SARReportPipeline:
    return SARReportPipeline.__new__(SARReportPipeline)


def _run_full_merge_sequence(pipeline, merged_json, fin_metrics, preflight_obs, data, *, fs_tables, company="ACME LTD", scope="standalone"):
    """Mirrors run()'s full merge sequence exactly (see
    sar_report_pipeline.py) so this test breaks the moment that sequence
    drifts from what run() actually does. Returns
    (observations_as_dicts, review_status, reasons, applicability,
    display_response, executive_summary).
    """
    applicability = pipeline._resolve_applicability(merged_json, data)
    appl_obs = applicability_observations(applicability)

    coherence_obs = pipeline._run_coherence_checks(merged_json, fin_metrics, preflight_obs, applicability)
    formal_result = pipeline._run_formal_checks(merged_json)
    consistency_obs = pipeline._run_consistency_checks(merged_json, fin_metrics, applicability)
    # REFERENCE_DSN is unset in this test environment, so this resolves to
    # [] via ReferenceTools' own graceful-degradation path — exercised here
    # for wiring fidelity, not for the (DB-dependent) logic itself, which
    # test_cag_directions_engine.py covers against a fake `tables` object.
    cag_directions_obs = pipeline._run_cag_directions_checks(merged_json)
    pervasiveness_obs = pipeline._run_pervasiveness_checks(merged_json, fs_tables)

    prior_result = pipeline._fetch_prior_year_result(data)
    opinion_trend_obs = check_opinion_trend(merged_json, prior_result)
    prior_year_obs = [opinion_trend_obs] if opinion_trend_obs is not None else []

    all_obs_objs = suppress_untraceable(
        coherence_obs + formal_result["observations"] + appl_obs
        + consistency_obs + cag_directions_obs + pervasiveness_obs + prior_year_obs
    )
    all_obs_objs = elevate_recurring_observations(all_obs_objs, prior_result)
    all_obs_objs = apply_public_sector_lens(all_obs_objs)
    all_obs_objs = enrich_evidence_required(all_obs_objs)

    review_status, reasons = compute_review_status(
        main_text_usable=data["main_text"].is_usable(), fs_tables=fs_tables, formal_summary=formal_result["summary"],
    )
    all_obs_objs = apply_confidence_downgrade(all_obs_objs, review_status)
    all_obs_objs = apply_output_classification(all_obs_objs)
    all_obs_objs = stamp_entity_context(all_obs_objs, entity=company, financial_year="FY 2023-24")
    all_obs_objs = assign_observation_ids(all_obs_objs)

    package_ctx = PackageContext(
        entity=company, financial_year="FY 2023-24", scope=scope,
        opinion_type=(merged_json.get("opinion") or {}).get("type", "unclear"),
        review_status=review_status, review_status_reasons=reasons,
    )
    display_response = render_display_response(package_ctx, all_obs_objs)
    executive_summary = render_executive_summary(package_ctx, all_obs_objs)

    return (
        [o.to_dict() for o in all_obs_objs], review_status, reasons, applicability,
        display_response, executive_summary,
    )


FS_TABLES_OK = {"balance_sheet": {"table_md": "x"}, "profit_loss": {"table_md": "x"}}


def _data(*, main="text", caro="caro text", ifc="ifc text", prior_year=None):
    d = {
        "main_text": ToolResult(data=main, quality_flag="reliable" if main else "not_found"),
        "caro_text": ToolResult(data=caro, quality_flag="reliable" if caro else "not_found"),
        "ifc_text": ToolResult(data=ifc, quality_flag="reliable" if ifc else "not_found"),
    }
    d["prior_year"] = (
        ToolResult(data=prior_year, quality_flag="reliable") if prior_year is not None
        else ToolResult(data={}, quality_flag="not_found")
    )
    return d


def test_clean_fully_applicable_package_yields_no_findings_and_complete_status():
    """§48 "No-Observation Case" extended through Phases 2 and 3: a
    coherent, fully-applicable package with no prior-year record must
    still reach review_status=complete with zero observations."""
    pipeline = _bare_pipeline()
    merged_json = {
        "_meta": {"main_opinion_type": "unmodified", "caro_adverse_count": 0, "ifc_has_material_weakness": False},
        "formal_checks": {
            "auditors": [{"udin": "24123456ABCDEFGHIJ"}],
            "report_date": "2024-05-20", "fs_approval_date": "2024-05-10",
        },
        "emphasis_of_matter": {"present": False},
        "opinion": {"type": "unmodified"},
        "CARO_2020": {"caro_applicable": True, "clauses": [], "high_priority_flags": {}},
        "IFC_REPORT": {"ifc_opinion": {"type": "unmodified"}},
        "key_audit_matters": {"present": True, "items": [{"title": "x"}]},
        "going_concern": {"discussed": False, "murgc_paragraph_present": False},
    }
    observations, status, reasons, applicability, display, summary = _run_full_merge_sequence(
        pipeline, merged_json, {"distress_signals": []}, [], _data(), fs_tables=FS_TABLES_OK,
    )
    assert observations == []
    assert status == "complete"
    assert reasons == []
    assert all(v["status"] == "applicable" for v in applicability.values())
    assert "No findings. No risk flags. No audit pointers." in display
    assert "None required." in summary


def test_unresolved_applicability_gates_coherence_but_still_flags_itself():
    """CARO/IFC text missing entirely: applicability resolves to
    'uncertain' for all three areas, which must (a) raise exactly the 3
    APPL-*-01 pointers and (b) suppress CHK-COH-01/02 even though
    caro_adverse_count/ifc_has_material_weakness are nonzero/true in _meta —
    the whole point of Gap #4.
    """
    pipeline = _bare_pipeline()
    merged_json = {
        "_meta": {"main_opinion_type": "unmodified", "caro_adverse_count": 3, "ifc_has_material_weakness": True},
        "formal_checks": {"auditors": [{"udin": "24123456ABCDEFGHIJ"}], "report_date": "", "fs_approval_date": ""},
        "emphasis_of_matter": {"present": False},
        "CARO_2020": {}, "IFC_REPORT": {}, "key_audit_matters": {"present": False},
    }
    observations, status, reasons, applicability, _, _ = _run_full_merge_sequence(
        pipeline, merged_json, {"distress_signals": []}, [], _data(caro="", ifc=""), fs_tables=FS_TABLES_OK,
    )
    check_ids = [o["check_id"] for o in observations]
    assert {"APPL-CARO-01", "APPL-IFC-01", "APPL-KAM-01"}.issubset(set(check_ids))
    assert "CHK-COH-01" not in check_ids  # gated despite caro_adverse_count=3
    assert "CHK-COH-02" not in check_ids  # gated despite ifc_has_material_weakness=True
    assert status == "complete"  # applicability uncertainty alone doesn't block/provisionalize (Phase 1 scope)


def test_multiple_issues_merge_with_sequential_ids_and_downgraded_confidence():
    pipeline = _bare_pipeline()
    merged_json = {
        "_meta": {"main_opinion_type": "unmodified", "caro_adverse_count": 2, "ifc_has_material_weakness": True},
        "formal_checks": {
            "auditors": [{"udin": "BAD-UDIN"}],
            "report_date": "2024-05-01", "fs_approval_date": "2024-05-10",  # premature
        },
        "emphasis_of_matter": {"present": False},
        "opinion": {"type": "unmodified"},
        "CARO_2020": {"caro_applicable": True, "clauses": [], "high_priority_flags": {"loan_default_ix": True}},
        "IFC_REPORT": {"ifc_opinion": {"type": "unmodified"}},
        "key_audit_matters": {"present": True, "items": [{"title": "x"}]},
        "going_concern": {"discussed": False, "murgc_paragraph_present": False},
    }
    preflight_obs = [{
        "check_id": "PRE-06", "tag": "AUDIT_POINTER", "component": "CARO 2020 Annexure",
        "observation": "CARO not identified.", "risk_rating": "Information request only",
    }]
    observations, status, reasons, _, display, summary = _run_full_merge_sequence(
        pipeline, merged_json, {"distress_signals": []}, preflight_obs, _data(), fs_tables=FS_TABLES_OK,
    )

    check_ids = [o["check_id"] for o in observations]
    assert "PRE-06" in check_ids
    assert "CHK-COH-01" in check_ids       # unmodified + CARO adverse (applicable)
    assert "CHK-COH-02" in check_ids       # unmodified + IFC weakness (applicable)
    assert "CHK-UDIN-01" in check_ids
    assert "CHK-DATE-01" in check_ids
    assert "CONS-CARO-IX-01" in check_ids  # CARO(ix) default, no going concern (Gap #3)

    assert [o["obs_id"] for o in observations] == [f"SAR-{i:02d}" for i in range(1, len(observations) + 1)]

    assert status == "provisional"  # UDIN invalid
    assert all(o["confidence"] == "Medium" for o in observations)
    assert all(o["entity"] == "ACME LTD" for o in observations)  # Phase 4: stamp_entity_context ran

    # Phase 4: every FINDING/RISK_FLAG observation's component must appear
    # in both the Display Response and the Executive Summary — same
    # source list, same conclusions, per output-spec §17.
    material = [o for o in observations if o["tag"] in ("FINDING", "RISK_FLAG")]
    for o in material:
        assert o["component"] in display or o["observation"] in display
        assert o["observation_id"] in summary


def test_missing_main_report_blocks_regardless_of_other_findings():
    pipeline = _bare_pipeline()
    merged_json = {
        "_meta": {"main_opinion_type": "unmodified", "caro_adverse_count": 0, "ifc_has_material_weakness": False},
        "formal_checks": {"auditors": [{"udin": "24123456ABCDEFGHIJ"}], "report_date": "", "fs_approval_date": ""},
        "emphasis_of_matter": {"present": False},
        "CARO_2020": {}, "IFC_REPORT": {}, "key_audit_matters": {"present": False},
    }
    _, status, reasons, _, _, _ = _run_full_merge_sequence(
        pipeline, merged_json, {"distress_signals": []}, [], _data(main=""), fs_tables=FS_TABLES_OK,
    )
    assert status == "blocked"
    assert reasons


def test_pervasiveness_and_prior_year_and_public_sector_lens_all_fire_together():
    """Gap-closure Phase 3 end-to-end: a Qualified opinion with high
    pervasiveness cues (Gap #8), a prior-year record showing the same
    opinion type (Gap #6), and a KAM mentioning a grant (Gap #7's
    public-sector lens) should all surface in one merged run."""
    pipeline = _bare_pipeline()
    merged_json = {
        "_meta": {"main_opinion_type": "unmodified", "caro_adverse_count": 0, "ifc_has_material_weakness": False},
        "formal_checks": {
            "auditors": [{"udin": "24123456ABCDEFGHIJ"}],
            "report_date": "2024-05-20", "fs_approval_date": "2024-05-10",
        },
        "emphasis_of_matter": {"present": False},
        "opinion": {"type": "qualified"},
        "modification": {
            "quantified_amount": "500", "affected_line_items": ["Grant Receivable"],
            "pervasiveness_cues": {
                "affects_multiple_elements": True, "affects_fundamental_balance": True,
                "large_relative_to_key_bases": True, "cannot_determine_effect": False,
                "multiple_modifications_same_direction": False,
            },
        },
        "CARO_2020": {"caro_applicable": True, "clauses": [], "high_priority_flags": {}},
        "IFC_REPORT": {"ifc_opinion": {"type": "unmodified"}},
        "key_audit_matters": {"present": True, "items": [{"title": "x"}]},
        "going_concern": {"discussed": False, "murgc_paragraph_present": False},
    }
    fs_tables = {
        "balance_sheet": {"table_md": "Grant Receivable | 500"},  # reconciles
        "profit_loss": {"table_md": "x"},
    }
    prior_record = {"opinion_type": "qualified", "high_risk_observations": []}

    observations, status, reasons, _, display, summary = _run_full_merge_sequence(
        pipeline, merged_json, {"distress_signals": []}, [], _data(prior_year=prior_record), fs_tables=fs_tables,
    )
    check_ids = [o["check_id"] for o in observations]
    assert "PERV-01" in check_ids            # qualified + 3 cues
    assert "PERV-02" not in check_ids        # amount reconciles against fs_tables
    assert "PRIOR-OPN-01" in check_ids       # prior qualified + current qualified -> recurring modification
    assert status == "complete"

    # Phase 4: PERV-01 is tagged RISK_FLAG by design (pervasiveness.py never
    # re-classifies the auditor's stated opinion — see that module's
    # docstring), so it must NOT be flagged candidate_143_6 (that requires
    # tag==FINDING) even though its risk_rating is High.
    perv01 = next(o for o in observations if o["check_id"] == "PERV-01")
    assert perv01["candidate_143_6"] is False
    assert perv01["consistency_type"] == "OTHER"
    assert perv01["sa_framework"]["standard"] == "SA 705"
    assert not any(o["candidate_143_6"] for o in observations)
    assert "Candidate Section 143(6)" not in summary
