"""Orchestration: run every Layer-1 rule for a table (run-all-report-all,
never stop early), the L-1..3 cross-year checks in comparison mode, the
halt-rule (phase_reached), Layer 2/3 (gated on no halt), Layer 4 (always
attempted), and the final Output Shape assembly + narrative.
"""

from __future__ import annotations

from yukta_rag.tb_validation import tbv_formula
from yukta_rag.tb_validation.tbv_config import NEGLIGENCE_TOLERANCE_PCT
from yukta_rag.tb_validation.tbv_ingest import ParsedTBTable, ingest_from_bytes, ingest_from_doc_id
from yukta_rag.tb_validation.tbv_narrative import build_summary_narrative
from yukta_rag.tb_validation.tbv_rules_anomaly import (tb_021_benford,
                                                       tb_022_gl_desc_group_mismatch,
                                                       tb_023_round_number_entries,
                                                       tb_027_static_vs_formula,
                                                       tb_033_stale_year_reference)
from yukta_rag.tb_validation.tbv_rules_arithmetic import (build_single_table_rows,
                                                          tb_005_row_arithmetic,
                                                          tb_006_opening_sign_vs_group,
                                                          tb_007_closing_sign_vs_group,
                                                          tb_008_never_invert,
                                                          tb_009_total_debit_credit,
                                                          tb_010_opening_sum_zero,
                                                          tb_011_closing_sum_zero,
                                                          tb_012_assets_equal_liab_equity,
                                                          tb_024_income_expense_net_pl,
                                                          tb_032_pl_no_opening)
from yukta_rag.tb_validation.tbv_rules_completeness import (tb_015_activity_closes_zero,
                                                            tb_016_no_duplicate_gl_group,
                                                            tb_017_mandatory_heads_present,
                                                            tb_018_suspense_nonzero,
                                                            tb_019_rounding_within_materiality,
                                                            tb_020_tax_head_sign,
                                                            tb_028_sensitive_heads,
                                                            tb_031_control_branch_sub_ledger)
from yukta_rag.tb_validation.tbv_rules_cross_year import (build_full_table_rows,
                                                          l1_company_code_filter,
                                                          l2_ledger_alignment,
                                                          l3_opening_closing_continuity,
                                                          layer2_structural, layer3_variance)
from yukta_rag.tb_validation.tbv_rules_structural import (tb_000_sign_convention,
                                                          tb_001_required_columns,
                                                          tb_002_gl_code_non_blank,
                                                          tb_003_gl_code_unique,
                                                          tb_004_numeric_validity,
                                                          tb_013_one_group_per_code,
                                                          tb_014_group_in_master_list,
                                                          tb_025_period_matches_engagement,
                                                          tb_026_currency_scale,
                                                          tb_029_source_trail,
                                                          tb_030_fuzzy_duplicate_descriptions)


def _run_layer1_for_table(table: ParsedTBTable, formula_flags: list[dict] | None,
                          params: dict) -> list[dict]:
    """Every Layer-1 rule, in the exact A/B/C/D/E/F/G/H/J/K category order —
    run-all-report-all, never stop early."""
    tol = params.get("tolerance_pct", NEGLIGENCE_TOLERANCE_PCT)
    results = [
        # A. STRUCTURAL/SCHEMA
        tb_000_sign_convention(table, params.get("documented_convention")),
        tb_001_required_columns(table),
        tb_002_gl_code_non_blank(table),
        tb_003_gl_code_unique(table),
        tb_004_numeric_validity(table),
        tb_025_period_matches_engagement(table, params.get("engagement_period")),
        tb_026_currency_scale(table, params.get("target_currency")),
        tb_029_source_trail(table),
        # B. ROW ARITHMETIC
        tb_005_row_arithmetic(table, tol),
        tb_006_opening_sign_vs_group(table),
        tb_007_closing_sign_vs_group(table),
        tb_008_never_invert(table, params.get("never_invert_accounts")),
        tb_032_pl_no_opening(table),
        # C. AGGREGATE TIE-OUT
        tb_009_total_debit_credit(table, tol),
        tb_010_opening_sum_zero(table, tol),
        tb_011_closing_sum_zero(table, tol),
        tb_012_assets_equal_liab_equity(table, tol),
        tb_024_income_expense_net_pl(table, params.get("external_pl_figure"), tol),
        # D. GL MASTER DATA
        tb_013_one_group_per_code(table),
        tb_014_group_in_master_list(table, params.get("approved_group_master")),
        tb_030_fuzzy_duplicate_descriptions(table),
        # E. CONTINUITY
        tb_015_activity_closes_zero(table),
        # F. COMPLETENESS
        tb_016_no_duplicate_gl_group(table),
        tb_017_mandatory_heads_present(table, params.get("required_heads")),
        tb_018_suspense_nonzero(table),
        tb_019_rounding_within_materiality(table, params.get("rounding_account_threshold")),
        tb_028_sensitive_heads(table),
        # G. STATUTORY MAPPING
        tb_020_tax_head_sign(table, params.get("tax_head_sign_map")),
        # H. ANOMALY DETECTION
        tb_021_benford(table),
        tb_022_gl_desc_group_mismatch(table),
        tb_023_round_number_entries(table),
        tb_033_stale_year_reference(table, params.get("tb_period_year")),
        # J. PRESENTATION
        tb_027_static_vs_formula(table, formula_flags),
        # K. CROSS-DOCUMENT
        tb_031_control_branch_sub_ledger(table, params.get("sub_ledger_ref")),
    ]
    return results


def _any_halt(*result_lists: list[dict]) -> bool:
    return any(r["status"] == "HALT" for results in result_lists for r in results)


def _ingest(data: bytes | None, doc_id: str | None, label: str | None) -> ParsedTBTable:
    if data is not None:
        return ingest_from_bytes(data, label=label)
    if doc_id is not None:
        return ingest_from_doc_id(doc_id)
    raise ValueError("either data or doc_id must be supplied")


def run_single(data: bytes | None = None, doc_id: str | None = None,
              label: str | None = None, **params) -> dict:
    """Single-table validation mode — Layer 1 only (no cross-year checks)."""
    table = _ingest(data, doc_id, label)
    formula_flags = tbv_formula.scan_formulas(data, table)  # None if unavailable
    results = _run_layer1_for_table(table, formula_flags, params)
    halted = _any_halt(results)
    phase_reached = "HALTED" if halted else "LAYER_1"

    full_table_rows = None
    if not halted:
        full_table_rows = build_single_table_rows(table, params.get("variance_materiality_pct"))

    layer1_results = {"single": results}
    narrative = build_summary_narrative(
        table_label=table.label, prior_label=None, layer1_results=layer1_results,
        structural_result=None, variance_rows=None, formula_flags=formula_flags,
        phase_reached=phase_reached)

    return {
        "phase_reached": phase_reached,
        "layer1_results": layer1_results,
        "structural_result": None,
        "variance_rows": None,
        "full_table_rows": full_table_rows,
        "formula_flags": formula_flags if formula_flags is not None else [],
        "summary_narrative": narrative,
    }


def run_comparison(py_data: bytes | None = None, py_doc_id: str | None = None,
                   cy_data: bytes | None = None, cy_doc_id: str | None = None,
                   py_label: str | None = None, cy_label: str | None = None,
                   **params) -> dict:
    """Comparison mode — Layer 1 for both years, L-1..3, then (if no HALT
    anywhere) Layer 2 structural diff and Layer 3 variance analysis."""
    py_table = _ingest(py_data, py_doc_id, py_label)
    cy_table = _ingest(cy_data, cy_doc_id, cy_label)

    py_formula_flags = tbv_formula.scan_formulas(py_data, py_table)
    cy_formula_flags = tbv_formula.scan_formulas(cy_data, cy_table)

    py_results = _run_layer1_for_table(py_table, py_formula_flags, params)
    cy_results = _run_layer1_for_table(cy_table, cy_formula_flags, params)

    l1_py = l1_company_code_filter(py_table, params.get("target_company_code"))
    l1_cy = l1_company_code_filter(cy_table, params.get("target_company_code"))
    l1_py["details"] = {**l1_py["details"], "table": "py"}
    l1_cy["details"] = {**l1_cy["details"], "table": "cy"}
    l2 = l2_ledger_alignment(py_table, cy_table)
    l3 = l3_opening_closing_continuity(py_table, cy_table)
    cross_year_results = [l1_py, l1_cy, l2, l3]

    halted = _any_halt(py_results, cy_results, cross_year_results)
    layer1_results = {"py": py_results, "cy": cy_results, "cross_year_results": cross_year_results}
    formula_flags = [*(py_formula_flags or []), *(cy_formula_flags or [])]

    if halted:
        return {
            "phase_reached": "HALTED",
            "layer1_results": layer1_results,
            "structural_result": None,
            "variance_rows": None,
            "full_table_rows": None,
            "formula_flags": formula_flags,
            "summary_narrative": build_summary_narrative(
                table_label=cy_table.label, prior_label=py_table.label,
                layer1_results=layer1_results, structural_result=None, variance_rows=None,
                formula_flags=formula_flags, phase_reached="HALTED"),
        }

    structural_result = layer2_structural(py_table, cy_table)
    phase_reached = "STRUCTURAL"

    variance_rows = layer3_variance(py_table, cy_table, params.get("variance_materiality_pct"))
    full_table_rows = build_full_table_rows(py_table, cy_table, variance_rows)
    phase_reached = "VARIANCE"

    narrative = build_summary_narrative(
        table_label=cy_table.label, prior_label=py_table.label,
        layer1_results=layer1_results, structural_result=structural_result,
        variance_rows=variance_rows, formula_flags=formula_flags, phase_reached=phase_reached)

    return {
        "phase_reached": phase_reached,
        "layer1_results": layer1_results,
        "structural_result": structural_result,
        "variance_rows": variance_rows,
        "full_table_rows": full_table_rows,
        "formula_flags": formula_flags,
        "summary_narrative": narrative,
    }
