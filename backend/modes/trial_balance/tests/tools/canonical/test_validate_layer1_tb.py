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
