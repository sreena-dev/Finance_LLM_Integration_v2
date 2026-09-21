"""Tests for backend/tools/canonical/validate_layer1_tb.py's new/modified
rules: TB-000's real sign-convention heuristic (replacing the old always-PASS
stub), the Layer-4 Formula Flag, and the new TB-025/026/029 rules."""

import pytest

from modes.trial_balance.pipeline.tools import _check_sign_convention, validate_layer1_tb

pl = pytest.importorskip("polars")


def _results_by_rule(output_dir):
    import json

    with open(output_dir / "layer1_results.json") as f:
        return {r["rule"]: r for r in json.load(f)}


class TestSignConventionHeuristic:
    """Unit-level tests directly on _check_sign_convention -- the function TB-000
    delegates to. Isolates the sign-detection logic from the rest of Layer 1."""

    def test_confirms_debit_positive_convention(self):
        df = pl.DataFrame(
            {
                "gl_name": ["Cash", "Bank Account", "Trade Receivable", "Inventory",
                            "Trade Payable", "Share Capital", "Sales Revenue", "General Reserve"],
                "closing_balance": [1500.0, 2500.0, 3200.0, 3900.0, -1500.0, -5000.0, -9000.0, -2300.0],
            }
        )
        status, message = _check_sign_convention(df)
        assert status == "PASS"
        assert "positive = debit" in message

    def test_flags_inverse_convention(self):
        # Debit-anchored accounts negative, credit-anchored accounts positive --
        # the file uses the OPPOSITE of what every downstream tool assumes.
        df = pl.DataFrame(
            {
                "gl_name": ["Cash A", "Cash B", "Cash C", "Bank D",
                            "Payable A", "Payable B", "Payable C", "Payable D"],
                "closing_balance": [-100.0, -100.0, -100.0, -100.0, 100.0, 100.0, 100.0, 100.0],
            }
        )
        status, message = _check_sign_convention(df)
        assert status == "WARNING"
        assert "opposite convention" in message

    def test_warns_when_too_few_anchors(self):
        df = pl.DataFrame({"gl_name": ["Misc A", "Misc B"], "closing_balance": [100.0, -50.0]})
        status, message = _check_sign_convention(df)
        assert status == "WARNING"
        assert "Not enough recognizable ledger names" in message

    def test_handles_unparseable_closing_balance_without_crashing(self):
        # closing_balance is still a raw/string value at the point TB-000 runs
        # (before TB-004's numeric coercion) -- this is a regression test for
        # a TypeError that occurred here before defensive float() parsing was added.
        df = pl.DataFrame(
            {
                "gl_name": ["Cash", "Bank Account", "Trade Receivable", "Payable A", "Payable B", "Payable C"],
                "closing_balance": ["1500", "2500", "not-a-number", "-1500", "-2000", "-2300"],
            }
        )
        status, _ = _check_sign_convention(df)
        assert status in ("PASS", "WARNING")  # must not raise

    def test_message_carries_basis_label(self):
        # TB-R31: three different sign-convention screens (this one, run_comparison_
        # sign_check, sign_convention_stats) each compute a genuinely different
        # percentage for the same TB -- every message must say which one produced it.
        df = pl.DataFrame(
            {
                "gl_name": ["Cash", "Bank Account", "Trade Receivable", "Inventory",
                            "Trade Payable", "Share Capital", "Sales Revenue", "General Reserve"],
                "closing_balance": [1500.0, 2500.0, 3200.0, 3900.0, -1500.0, -5000.0, -9000.0, -2300.0],
            }
        )
        _, message = _check_sign_convention(df)
        assert "single-TB, whole-file basis" in message


class TestOverallPipelineStatusNotDowngraded:
    # TB-R18: a genuinely HALTED rule (e.g. TB-009's Dr != Cr blocking check) must surface
    # as pipeline_status="HALTED"/can_continue=False -- this function used to silently
    # downgrade HALTED to WARNING "to force the pipeline to continue," which meant a TB
    # that doesn't foot never actually stopped anything downstream.
    def test_halted_rule_is_not_downgraded_to_warning(self, make_tb_workbook, tmp_path):
        # Debit total (1000) vs Credit total (0) is a 100% relative difference -- well past
        # TB-009's 5% HALTED threshold -- while opening+debit-credit still ties to closing,
        # so TB-005 stays PASS and this isolates TB-009 as the sole HALTED rule.
        rows = [("1001", "Cash", 0, 1000, 0, 1000)]
        wb_path = make_tb_workbook(rows)
        result = validate_layer1_tb(tb_excel_path=str(wb_path), output_dir=str(tmp_path))
        results = _results_by_rule(tmp_path)
        assert results["TB-009"]["status"] == "HALTED"
        assert result["pipeline_status"] == "HALTED"
        assert result["can_continue"] is False

    def test_passing_workbook_still_reports_success(self, make_tb_workbook, tmp_path):
        # NOTE: the shared `balanced_anchor_rows` fixture is turnover-balanced (debit
        # total == credit total) but its closing balances do NOT sum to zero (they sum to
        # 800) -- TB-010/TB-011 correctly HALT on it, and did so even before this fix; the
        # old downgrade bug just silently hid that HALT as a WARNING. A row set that is
        # genuinely balanced under the closing-balance identity is used here instead, to
        # isolate "does a clean TB still report success" from that separate, pre-existing
        # fixture-labelling issue.
        rows = [
            ("1001", "Cash", 0, 1000, 0, 1000),
            ("2001", "Trade Payable", 0, 0, 1000, -1000),
        ]
        wb_path = make_tb_workbook(rows)
        result = validate_layer1_tb(tb_excel_path=str(wb_path), output_dir=str(tmp_path))
        assert result["pipeline_status"] != "HALTED"
        assert result["can_continue"] is True


class TestRoundingToleranceAbsoluteFloor:
    # TB-R22: TB-009/010/011/019 previously escalated past PASS on ANY nonzero relative
    # residual, however microscopically small -- the exact "false precision" defect a
    # client review flagged directly (a residual of 0.000122 against a multi-thousand-
    # crore control total reported as an exception). Each rule must now clear an
    # absolute-currency-unit floor (_TB_ROUNDING_ABS_EPSILON = 1.0) before a nonzero
    # relative residual is treated as a genuine exception.

    def test_tiny_residual_within_absolute_epsilon_passes(self, make_tb_workbook, tmp_path):
        rows = [
            ("1001", "Cash", 0, 1000, 0, 1000.3),
            ("2001", "Trade Payable", 0, 0, 1000, -999.8),
        ]
        wb_path = make_tb_workbook(rows)
        validate_layer1_tb(tb_excel_path=str(wb_path), output_dir=str(tmp_path))
        results = _results_by_rule(tmp_path)
        assert results["TB-011"]["status"] == "PASS"
        assert results["TB-019"]["status"] == "PASS"
        assert results["TB-009"]["status"] == "PASS"
        assert results["TB-010"]["status"] == "PASS"

    def test_moderate_residual_beyond_epsilon_but_within_5pct_warns(self, make_tb_workbook, tmp_path):
        rows = [
            ("1001", "A", 0, 0, 0, 1050),
            ("2001", "B", 0, 0, 0, -1000),
        ]
        wb_path = make_tb_workbook(rows)
        validate_layer1_tb(tb_excel_path=str(wb_path), output_dir=str(tmp_path))
        results = _results_by_rule(tmp_path)
        assert results["TB-011"]["status"] == "WARNING"
        assert results["TB-019"]["status"] == "WARNING"

    def test_large_residual_beyond_5pct_halts(self, make_tb_workbook, tmp_path):
        rows = [("1001", "A", 0, 0, 0, 5000)]
        wb_path = make_tb_workbook(rows)
        validate_layer1_tb(tb_excel_path=str(wb_path), output_dir=str(tmp_path))
        results = _results_by_rule(tmp_path)
        assert results["TB-011"]["status"] == "HALTED"
        assert results["TB-019"]["status"] == "HALTED"

    def test_tb019_no_longer_permanently_skipped(self, make_tb_workbook, tmp_path):
        rows = [("1001", "Cash", 0, 1000, 0, 1000), ("2001", "Trade Payable", 0, 0, 1000, -1000)]
        wb_path = make_tb_workbook(rows)
        validate_layer1_tb(tb_excel_path=str(wb_path), output_dir=str(tmp_path))
        results = _results_by_rule(tmp_path)
        assert results["TB-019"]["status"] != "SKIPPED"


class TestTB005SeverityConsistency:
    # TB-R22: TB-005's PASS branch previously recorded severity "Blocking" while both its
    # WARNING branches record "Warning" for the identical rule -- inconsistent by
    # construction. Severity must not depend on which branch fired.
    def test_pass_and_warning_severities_match(self, make_tb_workbook, tmp_path):
        clean_rows = [("1001", "Cash", 0, 1000, 0, 1000)]
        wb_path = make_tb_workbook(clean_rows, filename="clean.xlsx")
        validate_layer1_tb(tb_excel_path=str(wb_path), output_dir=str(tmp_path / "clean"))
        pass_result = _results_by_rule(tmp_path / "clean")["TB-005"]
        assert pass_result["status"] == "PASS"

        mismatch_rows = [("1001", "Cash", 0, 1000, 0, 1.0)]
        wb_path2 = make_tb_workbook(mismatch_rows, filename="mismatch.xlsx")
        validate_layer1_tb(tb_excel_path=str(wb_path2), output_dir=str(tmp_path / "mismatch"))
        warn_result = _results_by_rule(tmp_path / "mismatch")["TB-005"]
        assert warn_result["status"] == "WARNING"

        assert pass_result["severity"] == warn_result["severity"] == "Warning"


class TestValidateLayer1TbEndToEnd:
    def test_tb000_pass_on_balanced_anchor_workbook(self, make_tb_workbook, balanced_anchor_rows, tmp_path):
        wb_path = make_tb_workbook(balanced_anchor_rows)
        result = validate_layer1_tb(tb_excel_path=str(wb_path), output_dir=str(tmp_path))
        assert result["execution_status"] == "SUCCESS"
        results = _results_by_rule(tmp_path)
        assert results["TB-000"]["status"] == "PASS"

    def test_layer4_detects_formula_cell(self, make_tb_workbook, balanced_anchor_rows, tmp_path):
        wb_path = make_tb_workbook(
            balanced_anchor_rows, formula_cell="F9", formula_text="=C9+D9-E9"
        )
        result = validate_layer1_tb(tb_excel_path=str(wb_path), output_dir=str(tmp_path))
        layer4 = result["layer4_result"]
        assert layer4["has_formulas"] is True
        assert layer4["total_formula_cells"] == 1
        assert layer4["cells_by_sheet"]["TB"][0]["cell"] == "F9"
        assert layer4["cells_by_sheet"]["TB"][0]["formula"] == "=C9+D9-E9"

    def test_layer4_reports_no_formulas_on_plain_workbook(self, make_tb_workbook, balanced_anchor_rows, tmp_path):
        wb_path = make_tb_workbook(balanced_anchor_rows)
        result = validate_layer1_tb(tb_excel_path=str(wb_path), output_dir=str(tmp_path))
        layer4 = result["layer4_result"]
        assert layer4["has_formulas"] is False
        assert layer4["total_formula_cells"] == 0

    def test_layer4_empty_when_sourced_from_canonical_parquet(self, make_canonical_tb, tmp_path):
        # A canonical TB has no formulas by construction -- Layer 4 must not
        # attempt formula detection (and must not crash) when there's no raw
        # workbook to read.
        canonical_tb_file = make_canonical_tb(
            [{"gl_code": "1001", "gl_name": "Cash", "closing_balance": 1500.0}]
        )
        result = validate_layer1_tb(canonical_tb_file=str(canonical_tb_file), output_dir=str(tmp_path))
        assert result["layer4_result"] == {"has_formulas": False, "total_formula_cells": 0, "cells_by_sheet": {}}

    def test_tb025_warning_when_period_not_supplied(self, make_tb_workbook, balanced_anchor_rows, tmp_path):
        wb_path = make_tb_workbook(balanced_anchor_rows)
        validate_layer1_tb(tb_excel_path=str(wb_path), output_dir=str(tmp_path))
        results = _results_by_rule(tmp_path)
        assert results["TB-025"]["status"] == "WARNING"

    def test_tb025_pass_when_period_supplied(self, make_tb_workbook, balanced_anchor_rows, tmp_path):
        wb_path = make_tb_workbook(balanced_anchor_rows)
        validate_layer1_tb(
            tb_excel_path=str(wb_path), output_dir=str(tmp_path), expected_financial_year="FY2025-26"
        )
        results = _results_by_rule(tmp_path)
        assert results["TB-025"]["status"] == "PASS"
        assert "FY2025-26" in results["TB-025"]["message"]

    def test_tb026_flags_scale_outlier(self, make_tb_workbook, balanced_anchor_rows, tmp_path):
        rows = list(balanced_anchor_rows) + [("3001", "Miscellaneous Outlier Account", 0, 0, 0, 50_000_000_000)]
        wb_path = make_tb_workbook(rows)
        validate_layer1_tb(tb_excel_path=str(wb_path), output_dir=str(tmp_path))
        results = _results_by_rule(tmp_path)
        assert results["TB-026"]["status"] == "WARNING"
        assert "possible wrong-scale entry" in results["TB-026"]["message"]

    def test_tb029_pass_when_source_system_supplied(self, make_tb_workbook, balanced_anchor_rows, tmp_path):
        wb_path = make_tb_workbook(balanced_anchor_rows)
        validate_layer1_tb(
            tb_excel_path=str(wb_path), output_dir=str(tmp_path),
            source_system="SAP", extraction_date="2026-08-19",
        )
        results = _results_by_rule(tmp_path)
        assert results["TB-029"]["status"] == "PASS"

    def test_tb029_warning_when_not_supplied(self, make_tb_workbook, balanced_anchor_rows, tmp_path):
        wb_path = make_tb_workbook(balanced_anchor_rows)
        validate_layer1_tb(tb_excel_path=str(wb_path), output_dir=str(tmp_path))
        results = _results_by_rule(tmp_path)
        assert results["TB-029"]["status"] == "WARNING"
