"""Tests for the remaining Phase-2 tools -- materiality lens, audit ratio pack,
engagement context, normalisation note -- and the PII masking helper.

These live in one module because each is a single tool in a domain of its own; four
near-identical files would add navigation cost without adding clarity.
"""

import json
from pathlib import Path

import pytest

from modes.trial_balance.pipeline.tools import (
    build_audit_ratio_pack,
    build_engagement_context,
    build_materiality_lens,
    build_normalisation_note,
    find_identifiers,
    mask_structure,
    mask_text,
)

# write_canonical is a plain helper, not a fixture -- imported rather than requested.
from modes.trial_balance.tests.conftest_phase2 import write_canonical


def _load(run_dir, name):
    return json.loads((Path(run_dir) / name).read_text(encoding="utf-8"))


class TestMaterialityLens:
    def test_elevates_a_sub_trivial_write_off_by_context(self, phase2_run):
        """The headline case. A 40k write-off sits below the 50k clearly-trivial
        threshold, so value-only materiality drops it entirely -- but sec 2.2 makes
        it material by what it represents."""
        _, run_dir = phase2_run
        result = build_materiality_lens(output_dir=str(run_dir))
        assert result["execution_status"] == "SUCCESS"
        data = _load(run_dir, "materiality_lens.json")
        elevated = data["elevated_accounts"]
        assert len(elevated) == 1
        assert elevated[0]["gl_code"] == "4900"
        assert abs(elevated[0]["balance"]) < data["clearly_trivial_threshold"]
        assert elevated[0]["basis"] == "context"

        finding = data["finding_records"][0]
        assert finding["regularity_flag"] is True
        assert "context" in finding["risk_basis"]

    def test_audit_approved_materiality_overrides_the_computed_proxy(self, phase2_run):
        """sec 8.1 -- computed figures are for prioritisation only and never override
        auditor-set materiality."""
        _, run_dir = phase2_run
        build_materiality_lens(output_dir=str(run_dir), audit_approved_materiality=5_000_000.0)
        data = _load(run_dir, "materiality_lens.json")
        assert data["materiality_basis"] == "audit_approved"
        assert data["effective_overall_materiality"] == 5_000_000.0
        assert data["computed_overall_materiality"] == 1_000_000.0

    def test_marks_output_provisional_when_scale_unconfirmed(self, phase2_run):
        """TB-026 is WARNING in the fixture; sec 3.2 says materiality should not be
        relied on until scale is confirmed."""
        _, run_dir = phase2_run
        build_materiality_lens(output_dir=str(run_dir))
        data = _load(run_dir, "materiality_lens.json")
        assert data["provisional"] is True
        assert data["scale_confirmed"] is False

    def test_does_not_modify_the_source_materiality_artifact(self, phase2_run):
        _, run_dir = phase2_run
        before = (Path(run_dir) / "materiality.json").read_text(encoding="utf-8")
        build_materiality_lens(output_dir=str(run_dir))
        assert (Path(run_dir) / "materiality.json").read_text(encoding="utf-8") == before

    def test_fails_clearly_without_materiality(self, minimal_tb, tmp_path):
        out = tmp_path / "bare"
        out.mkdir()
        result = build_materiality_lens(output_dir=str(out))
        assert result["execution_status"] == "FAILED"
        assert "build_materiality" in result["message"]


class TestAuditRatioPack:
    def test_computes_debtor_intensity(self, phase2_run):
        """sec 8.4's own worked example, which the pipeline could not previously
        produce."""
        tb, run_dir = phase2_run
        result = build_audit_ratio_pack(canonical_tb_file=tb, output_dir=str(run_dir))
        assert result["execution_status"] == "SUCCESS"
        ratios = _load(run_dir, "audit_ratio_pack.json")["ratios"]
        assert ratios["debtor_intensity"]["value"] == pytest.approx(0.8, abs=0.01)

    def test_excludes_accumulated_depreciation_from_the_proxy(self, phase2_run):
        """'Accumulated Depreciation on Plant' matches both the PPE and depreciation
        keyword sets, so without exclusion it inflates both sides at once -- a live
        smoke test read 25% where the answer is 10%."""
        tb, run_dir = phase2_run
        build_audit_ratio_pack(canonical_tb_file=tb, output_dir=str(run_dir))
        ratios = _load(run_dir, "audit_ratio_pack.json")["ratios"]
        assert ratios["depreciation_proxy"]["value"] == pytest.approx(0.1, abs=0.01)

    def test_names_the_missing_component_instead_of_approximating(self, phase2_run):
        tb, run_dir = phase2_run
        build_audit_ratio_pack(canonical_tb_file=tb, output_dir=str(run_dir))
        inventory = _load(run_dir, "audit_ratio_pack.json")["ratios"]["inventory_intensity"]
        assert inventory["value"] is None
        assert inventory["components_missing"]
        assert "not computed" in inventory["reason"].lower()

    def test_every_computed_ratio_is_traceable_to_both_components(self, phase2_run):
        """sec 8.4 -- a reviewer must be able to trace each numerator and denominator
        back to a mapped trial-balance line."""
        tb, run_dir = phase2_run
        build_audit_ratio_pack(canonical_tb_file=tb, output_dir=str(run_dir))
        for key, r in _load(run_dir, "audit_ratio_pack.json")["ratios"].items():
            if r["value"] is None:
                continue
            for side in ("numerator", "denominator"):
                assert r[side]["head"], f"{key}.{side} has no head name"
                assert r[side]["balance"] is not None
                assert r[side]["basis"] in (
                    "fsli_hierarchy", "gl_name_fallback",
                    "financial_snapshot",  # total_revenue/total_expenses, classify_row-derived
                    "derived",  # e.g. revenue_less_cogs -- arithmetic over two already-traced components
                )

    def test_defines_more_than_five_ratios(self):
        """The old pack shipped exactly 5 ratios (debtor/creditor/inventory intensity,
        depreciation proxy, finance-cost ratio) -- this is the rebuild's headline ask."""
        from modes.trial_balance.pipeline.tools import _RATIOS

        assert len(_RATIOS) > 5
        keys = {spec["key"] for spec in _RATIOS}
        for expected in ("debtor_days", "creditor_days", "inventory_days",
                          "employee_cost_ratio", "operating_expense_ratio", "gross_margin_proxy"):
            assert expected in keys, f"{expected} missing from the rebuilt ratio set"

    def test_creditor_and_inventory_intensity_fall_back_to_total_expenses_for_a_services_tb(self, tmp_path):
        """The reported bug: a services-sector TB has no distinct Purchases/COGS line by
        design (it doesn't buy goods for resale), so creditor/inventory intensity came back
        null for every such TB under the old design. They should now compute against Total
        Expenses instead, with that substitution recorded rather than silent."""
        rows = [
            {"gl_code": "3000", "gl_name": "Revenue from Operations", "closing_balance": -50_000_000.0,
             "main_head": "Revenue", "sub_head_1": "Revenue from operations",
             "account_type": "Income", "mapped_status": "MAPPED"},
            {"gl_code": "6100", "gl_name": "Employee Cost", "closing_balance": 30_000_000.0,
             "main_head": "Expenses", "sub_head_1": "Employee benefit expenses",
             "account_type": "Expense", "mapped_status": "MAPPED"},
            {"gl_code": "6200", "gl_name": "Professional Fees", "closing_balance": 5_000_000.0,
             "main_head": "Expenses", "sub_head_1": "Other expenses",
             "account_type": "Expense", "mapped_status": "MAPPED"},
            {"gl_code": "2200", "gl_name": "Trade Payable - Vendor", "closing_balance": 8_000_000.0,
             "main_head": "Current liabilities", "sub_head_1": "Trade payables",
             "account_type": "Liability", "mapped_status": "MAPPED"},
        ]
        tb = write_canonical(tmp_path / "canonical_tb.parquet", rows)
        (tmp_path / "materiality.json").write_text(json.dumps({
            "selected_materiality": {"overall_materiality": 1_000_000.0},
            "thresholds": {"overall": 1_000_000.0, "performance": 750_000.0, "clearly_trivial": 50_000.0},
        }), encoding="utf-8")
        # Stands in for build_financial_snapshot's real output -- only the two keys
        # build_audit_ratio_pack actually reads.
        (tmp_path / "financial_snapshot_statistics.json").write_text(json.dumps({
            "total_revenue": 50_000_000.0, "total_expenses": 35_000_000.0,
        }), encoding="utf-8")

        result = build_audit_ratio_pack(canonical_tb_file=str(tb), output_dir=str(tmp_path))
        assert result["execution_status"] == "SUCCESS"
        ratios = _load(tmp_path, "audit_ratio_pack.json")["ratios"]

        creditor = ratios["creditor_intensity"]
        assert creditor["value"] is not None, "should not go null just because this TB has no COGS/Purchases line"
        assert creditor["used_fallback_denominator"] is True
        assert creditor["denominator"]["component"] == "total_expenses"
        assert creditor["value"] == pytest.approx(8_000_000.0 / 35_000_000.0, abs=1e-6)

        assert ratios["operating_expense_ratio"]["value"] == pytest.approx(35_000_000.0 / 50_000_000.0, abs=1e-6)
        # No real COGS line exists, so the margin proxy must stay null rather than
        # substitute Total Expenses under a "gross margin" label.
        assert ratios["gross_margin_proxy"]["value"] is None

    def test_days_ratio_is_365x_the_intensity_ratio(self, phase2_run):
        tb, run_dir = phase2_run
        build_audit_ratio_pack(canonical_tb_file=tb, output_dir=str(run_dir))
        ratios = _load(run_dir, "audit_ratio_pack.json")["ratios"]
        assert ratios["debtor_days"]["value"] == pytest.approx(ratios["debtor_intensity"]["value"] * 365, abs=0.01)

    def test_creditor_days_far_outside_band_is_flagged(self, phase2_run):
        # Wave 2 Fix 1a: a plausibility band now exists where none did before.
        tb, run_dir = phase2_run
        build_audit_ratio_pack(canonical_tb_file=tb, output_dir=str(run_dir))
        ratios = _load(run_dir, "audit_ratio_pack.json")["ratios"]
        assert ratios["creditor_days"]["status"] in ("within_expectation", "outside_expectation", "not_computed")

    def test_immaterial_residual_cogs_still_falls_back_to_total_expenses(self, tmp_path):
        """The EPIL bug: a tiny residual 'materials' line (~Rs 4.19cr, dwarfed by total
        expenses) counted as a "present" COGS match and silently defeated the documented
        COGS -> Total Expenses fallback, driving creditor/inventory intensity to an
        implausible reading off a near-zero denominator. A materiality-based trigger must
        still fall back to Total Expenses here, same as a fully-absent COGS line."""
        rows = [
            {"gl_code": "3000", "gl_name": "Revenue from Operations", "closing_balance": -50_000_000.0,
             "main_head": "Revenue", "sub_head_1": "Revenue from operations",
             "account_type": "Income", "mapped_status": "MAPPED"},
            {"gl_code": "5000", "gl_name": "Cost of Materials Consumed", "closing_balance": 419_000.0,
             "main_head": "Expenses", "sub_head_1": "Cost of materials consumed",
             "account_type": "Expense", "mapped_status": "MAPPED"},
            {"gl_code": "6100", "gl_name": "Employee Cost", "closing_balance": 30_000_000.0,
             "main_head": "Expenses", "sub_head_1": "Employee benefit expenses",
             "account_type": "Expense", "mapped_status": "MAPPED"},
            {"gl_code": "6200", "gl_name": "Professional Fees", "closing_balance": 5_000_000.0,
             "main_head": "Expenses", "sub_head_1": "Other expenses",
             "account_type": "Expense", "mapped_status": "MAPPED"},
            {"gl_code": "2200", "gl_name": "Trade Payable - Vendor", "closing_balance": 8_000_000.0,
             "main_head": "Current liabilities", "sub_head_1": "Trade payables",
             "account_type": "Liability", "mapped_status": "MAPPED"},
        ]
        tb = write_canonical(tmp_path / "canonical_tb.parquet", rows)
        (tmp_path / "materiality.json").write_text(json.dumps({
            "selected_materiality": {"overall_materiality": 1_000_000.0},
            "thresholds": {"overall": 1_000_000.0, "performance": 750_000.0, "clearly_trivial": 50_000.0},
        }), encoding="utf-8")
        (tmp_path / "financial_snapshot_statistics.json").write_text(json.dumps({
            "total_revenue": 50_000_000.0, "total_expenses": 35_419_000.0,
        }), encoding="utf-8")

        result = build_audit_ratio_pack(canonical_tb_file=str(tb), output_dir=str(tmp_path))
        assert result["execution_status"] == "SUCCESS"
        creditor = _load(tmp_path, "audit_ratio_pack.json")["ratios"]["creditor_intensity"]
        assert creditor["used_fallback_denominator"] is True
        assert creditor["fallback_reason"] == "immaterial_partial_match"
        assert creditor["denominator"]["component"] == "total_expenses"

    def test_excludes_purchase_returns_from_the_cogs_component(self, tmp_path):
        """A 'Purchase Returns' account is a contra entry that should REDUCE the
        purchases/COGS base, not be summed into it in absolute terms alongside real
        purchases -- gl_total_by_keywords sums matches in absolute terms by design, so
        without this exclusion a large returns account inflates the denominator and
        understates creditor/inventory intensity."""
        rows = [
            {"gl_code": "5000", "gl_name": "Purchases", "closing_balance": 20_000_000.0,
             "main_head": "Expenses", "sub_head_1": "Cost of materials consumed",
             "account_type": "Expense", "mapped_status": "MAPPED"},
            {"gl_code": "5010", "gl_name": "Purchase Returns", "closing_balance": -3_000_000.0,
             "main_head": "Expenses", "sub_head_1": "Cost of materials consumed",
             "account_type": "Expense", "mapped_status": "MAPPED"},
            {"gl_code": "2200", "gl_name": "Trade Payable - Vendor", "closing_balance": 4_000_000.0,
             "main_head": "Current liabilities", "sub_head_1": "Trade payables",
             "account_type": "Liability", "mapped_status": "MAPPED"},
        ]
        tb = write_canonical(tmp_path / "canonical_tb.parquet", rows)
        (tmp_path / "materiality.json").write_text(json.dumps({
            "selected_materiality": {"overall_materiality": 1_000_000.0},
            "thresholds": {"overall": 1_000_000.0, "performance": 750_000.0, "clearly_trivial": 50_000.0},
        }), encoding="utf-8")

        build_audit_ratio_pack(canonical_tb_file=str(tb), output_dir=str(tmp_path))
        ratios = _load(tmp_path, "audit_ratio_pack.json")["ratios"]
        # Denominator must be the 20M purchase line alone, not 23M (20M + the 3M return
        # summed in absolute terms).
        assert ratios["creditor_intensity"]["denominator"]["balance"] == pytest.approx(20_000_000.0, abs=1.0)


class TestEngagementContext:
    def test_emits_the_mandatory_statement_when_context_is_missing(self, phase2_run):
        """sec 2.1 requires this statement verbatim rather than proceeding as if
        context were known."""
        tb, run_dir = phase2_run
        result = build_engagement_context(canonical_tb_file=tb, output_dir=str(run_dir))
        assert result["execution_status"] == "SUCCESS"
        data = _load(run_dir, "engagement_context.json")
        assert data["engagement_context"] == "unknown"
        assert "Engagement context has not been provided" in data["engagement_context_statement"]
        assert "C&AG supplementary audit" in data["engagement_context_statement"]

    def test_records_supplied_context_without_a_statement(self, phase2_run):
        tb, run_dir = phase2_run
        build_engagement_context(canonical_tb_file=tb, output_dir=str(run_dir),
                                 engagement_context="CAG_supplementary_audit")
        data = _load(run_dir, "engagement_context.json")
        assert data["engagement_context"] == "CAG_supplementary_audit"
        assert data["engagement_context_statement"] is None

    def test_rejects_an_invalid_context_rather_than_storing_it(self, phase2_run):
        tb, run_dir = phase2_run
        result = build_engagement_context(canonical_tb_file=tb, output_dir=str(run_dir),
                                          engagement_context="vibes_based_review")
        assert _load(run_dir, "engagement_context.json")["engagement_context"] == "unknown"
        assert any("not one of" in w for w in result["warnings"])

    def test_framework_inference_is_marked_to_confirm(self, tmp_path):
        """sec 1.2 -- never assume Ind AS or AS. An inference is reported as an
        inference, for the audit team to confirm."""
        tb = write_canonical(tmp_path / "canonical_tb.parquet", [
            {"gl_code": "1001", "gl_name": "Right of Use Asset - Building", "closing_balance": 1000.0,
             "main_head": "Non-current assets", "account_type": "Asset", "mapped_status": "MAPPED"},
            {"gl_code": "2001", "gl_name": "Lease Liability", "closing_balance": -900.0,
             "main_head": "Non-current liabilities", "account_type": "Liability", "mapped_status": "MAPPED"},
        ])
        build_engagement_context(canonical_tb_file=str(tb), output_dir=str(tmp_path))
        data = _load(tmp_path, "engagement_context.json")
        assert data["framework"] == "Ind AS"
        assert data["framework_basis"] == "inferred_to_confirm"
        assert "right of use" in data["framework_evidence"]["ind_as_markers_found"]

    def test_supplied_framework_beats_inference(self, phase2_run):
        tb, run_dir = phase2_run
        build_engagement_context(canonical_tb_file=tb, output_dir=str(run_dir),
                                 accounting_framework="AS")
        data = _load(run_dir, "engagement_context.json")
        assert data["framework"] == "AS"
        assert data["framework_basis"] == "supplied"

    def test_public_funding_markers_do_not_assert_government_status(self, phase2_run):
        tb, run_dir = phase2_run
        build_engagement_context(canonical_tb_file=tb, output_dir=str(run_dir))
        gate = _load(run_dir, "engagement_context.json")["applicability_gates"]["public_sector_lens"]
        assert gate["applicable"] is None
        assert gate["trial_balance_indicators"]
        assert "do NOT establish" in gate["note"] or "does NOT establish" in gate["note"]


class TestNormalisationNote:
    def test_records_the_transformations_applied(self, phase2_run):
        tb, run_dir = phase2_run
        result = build_normalisation_note(canonical_tb_file=tb, output_dir=str(run_dir))
        assert result["execution_status"] == "SUCCESS"
        note = _load(run_dir, "normalisation_note.json")
        steps = {t["step"] for t in note["transformations_applied"]}
        assert "Sign standardisation" in steps
        assert "Rescaling" in steps
        rescale = next(t for t in note["transformations_applied"] if t["step"] == "Rescaling")
        assert rescale["detail"] == "None applied"

    def test_carries_the_scale_caveat_when_tb026_did_not_pass(self, phase2_run):
        tb, run_dir = phase2_run
        build_normalisation_note(canonical_tb_file=tb, output_dir=str(run_dir))
        note = _load(run_dir, "normalisation_note.json")
        assert note["currency_and_scale"]["status"] == "WARNING"
        assert any("scale-sensitive" in c or "1,000x" in c for c in note["caveats"])

    def test_states_what_is_not_evidenced_rather_than_leaving_a_gap(self, minimal_tb, tmp_path):
        """A silent gap reads as 'nothing to report', which is the opposite of the
        truth when no Layer-1 results exist."""
        out = tmp_path / "bare"
        out.mkdir()
        result = build_normalisation_note(canonical_tb_file=minimal_tb, output_dir=str(out))
        assert result["execution_status"] == "SUCCESS"
        note = _load(out, "normalisation_note.json")
        assert "Not evidenced" in note["sign_convention"]["finding"]
        assert any("layer1_results.json not available" in w for w in result["warnings"])

    def test_reports_mapping_coverage_for_re_performance(self, phase2_run):
        tb, run_dir = phase2_run
        build_normalisation_note(canonical_tb_file=tb, output_dir=str(run_dir))
        note = _load(run_dir, "normalisation_note.json")
        pop = note["row_population"]
        assert pop["rows_in_canonical_tb"] == 17
        assert pop["unmapped_or_unmatched_accounts"] == 1
        assert "re_performance_test" in note


class TestMasking:
    @pytest.mark.parametrize("text", [
        "Amount 100000000.0 and 251400000.0",   # unformatted currency
        "Balance 100,000,000.00 vs 80,000,000.00",
        "Run at 2026-08-24T15:22:19.816 for FY 2024-25",
        "Materiality 750000.0 performance",
    ])
    def test_never_masks_figures_or_dates(self, text):
        """A bare digit-run pattern matched currency amounts -- a live scan flagged
        100000000.0 as a bank account, which would have masked it to XXXXX0000 in
        every report, corrupting the figure while protecting nothing."""
        assert mask_text(text) == text
        assert find_identifiers(text) == {}

    @pytest.mark.parametrize("text,expect_gone", [
        ("Bank A/c No: 123456789012", "123456789012"),
        ("PAN ABCDE1234F", "ABCDE1234F"),
        ("GSTIN 27ABCDE1234F1Z5", "ABCDE1234F"),
        ("Aadhaar 1234 5678 9012", "1234 5678"),
        ("IFSC HDFC0001234", "HDFC0001234"),
        ("Employee EMP00421 advance", "EMP00421"),
        ("Reference 987654321098765 held", "987654321098765"),
    ])
    def test_masks_real_identifiers(self, text, expect_gone):
        out = mask_text(text)
        assert expect_gone not in out, out

    def test_gstin_is_masked_before_pan_so_neither_is_mangled(self):
        out = mask_text("GSTIN 27ABCDE1234F1Z5")
        assert out.startswith("GSTIN 27") and out.endswith("1Z5")

    def test_mask_structure_leaves_numbers_and_keys_alone(self):
        obj = {"pan_field": "ABCDE1234F", "amount": 100000000.0, "count": 12, "ok": True}
        out = mask_structure(obj)
        assert "pan_field" in out, "dict keys must not be masked"
        assert out["amount"] == 100000000.0
        assert out["count"] == 12 and out["ok"] is True
        assert out["pan_field"] != "ABCDE1234F"

    def test_documentation_flag_returns_the_original(self):
        """sec 16.2 allows the full identifier where audit documentation needs it."""
        assert mask_text("PAN ABCDE1234F", unmask_for_documentation=True) == "PAN ABCDE1234F"
