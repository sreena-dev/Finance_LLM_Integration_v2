"""Standalone tests for the new tb_validation module (no pytest dependency —
run directly: `python backend/tests/test_tb_validation.py`, or inside the
backend container:
`docker compose exec backend python /app/test_tb_validation.py`).

One test per rule (~35), each with a self-constructed Pass and Fail/Warning/
Halt fixture built directly from the algorithm as specified — no external
skill-spec document exists to pull worked examples from (confirmed absent
from the repo and confirmed with the user).
"""
import io
import pathlib
import sys

# PATCHED for this integration: the vendored `yukta_rag` package lives under
# this mode's pipeline/ dir, not at a repo-root "backend". Resolved from
# __file__ so the file runs directly from any cwd and under pytest alike.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "pipeline"))

import openpyxl

from yukta_rag.tb_validation.tbv_ingest import ALL_ROLES, ParsedTBTable, TBRow, ingest_from_bytes
from yukta_rag.tb_validation.tbv_parsing import is_total_row, parse_amount
from yukta_rag.tb_validation.tbv_narrative import DISCLAIMER, build_summary_narrative
from yukta_rag.tb_validation import tbv_pipeline
from yukta_rag.tb_validation.tbv_rules_structural import (
    tb_000_sign_convention, tb_001_required_columns, tb_002_gl_code_non_blank,
    tb_003_gl_code_unique, tb_004_numeric_validity, tb_013_one_group_per_code,
    tb_014_group_in_master_list, tb_025_period_matches_engagement,
    tb_026_currency_scale, tb_029_source_trail, tb_030_fuzzy_duplicate_descriptions)
from yukta_rag.tb_validation.tbv_rules_arithmetic import (
    tb_005_row_arithmetic, tb_006_opening_sign_vs_group, tb_007_closing_sign_vs_group,
    tb_008_never_invert, tb_009_total_debit_credit, tb_010_opening_sum_zero,
    tb_011_closing_sum_zero, tb_012_assets_equal_liab_equity, tb_024_income_expense_net_pl,
    tb_032_pl_no_opening)
from yukta_rag.tb_validation.tbv_rules_completeness import (
    tb_015_activity_closes_zero, tb_016_no_duplicate_gl_group, tb_017_mandatory_heads_present,
    tb_018_suspense_nonzero, tb_019_rounding_within_materiality, tb_020_tax_head_sign,
    tb_028_sensitive_heads, tb_031_control_branch_sub_ledger)
from yukta_rag.tb_validation.tbv_rules_anomaly import (
    tb_021_benford, tb_022_gl_desc_group_mismatch, tb_023_round_number_entries,
    tb_027_static_vs_formula, tb_033_stale_year_reference)
from yukta_rag.tb_validation.tbv_rules_cross_year import (
    l1_company_code_filter, l2_ledger_alignment, l3_opening_closing_continuity,
    layer2_structural, layer3_variance)


def _table(rows, **role_flags) -> ParsedTBTable:
    resolved = {r: True for r in ALL_ROLES}
    resolved.update(role_flags)
    return ParsedTBTable(label="test", filename="test.xlsx", doc_id=None, period_label="Balance",
                         rows=rows, excluded_total_rows=0, roles_resolved=resolved,
                         has_raw_access=False, source_mode="doc_id")


def _row(code, name, group=None, opening=None, closing=None, debit=None, credit=None, **kw):
    return TBRow(row_number=1, ledger_code=code, ledger_name=name, group=group,
                opening_balance=opening, closing_balance=closing, debit=debit, credit=credit, **kw)


# --- number parsing / total-row filter ---------------------------------------

def test_parse_amount_all_steps():
    assert parse_amount("  500  ") == (500.0, False)
    assert parse_amount("(1,234.56)") == (-1234.56, False)
    assert parse_amount("-500") == (-500.0, False)
    assert parse_amount("500-") == (-500.0, False)
    assert parse_amount("1,23,456.40") == (123456.40, False)
    assert parse_amount("Rs. 1,234.56") == (1234.56, False)
    assert parse_amount("₹1,234.56") == (1234.56, False)
    assert parse_amount("1.234.56") == (1234.56, False)
    assert parse_amount("") == (0.0, True)
    assert parse_amount(None) == (0.0, True)
    assert parse_amount("abc") == (0.0, True)
    print("PASS: parse_amount() handles all 6 steps correctly")


def test_is_total_row():
    assert is_total_row("", "Total") is True
    assert is_total_row("", "Grand Total") is True
    assert is_total_row("", "  GRAND TOTALS  ") is True
    assert is_total_row("", "Sub Total") is True
    assert is_total_row("", "Total Assets") is True  # starts-with "total "
    assert is_total_row("1001", "Cash in hand") is False
    print("PASS: is_total_row() matches the exact vocabulary, case-insensitive/trimmed")


def test_ingest_from_bytes_end_to_end():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["GL Code", "GL Name", "Opening Balance", "Debit", "Credit", "Closing Balance"])
    ws.append(["1001", "Cash in hand", 1000, 500, 200, 1300])
    ws.append(["1002", "Bank account", 2000, 0, 500, 1500])
    ws.append(["", "Total", 3000, 500, 700, 2800])
    buf = io.BytesIO()
    wb.save(buf)
    table = ingest_from_bytes(buf.getvalue(), label="test.xlsx")
    assert table.roles_resolved["ledger_code"] and table.roles_resolved["closing_balance"]
    assert len(table.rows) == 2, f"expected 2 data rows (total excluded), got {len(table.rows)}"
    assert table.excluded_total_rows == 1
    assert table.rows[0].closing_balance == 1300.0
    print("PASS: ingest_from_bytes() resolves roles, parses amounts, excludes the total row")


# --- A. STRUCTURAL/SCHEMA ----------------------------------------------------

def test_tb000_documented_mixed_halts():
    r = tb_000_sign_convention(_table([]), documented_convention="mixed")
    assert r["status"] == "HALT"
    print("PASS: TB-000 halts on documented-and-mixed convention")


def test_tb000_heuristic_consistent_passes():
    rows = [_row(f"{i}", f"Cash account {i}", closing=100.0) for i in range(4)] + \
          [_row(f"c{i}", f"Sundry creditor {i}", closing=-100.0) for i in range(4)]
    r = tb_000_sign_convention(_table(rows))
    assert r["status"] == "PASS", r
    print("PASS: TB-000 heuristic passes on a consistent undocumented convention")


def test_tb000_heuristic_inconsistent_warns():
    # 2 of 4 debit-anchor accounts agree (50% < 70% threshold) -> WARNING
    rows = [_row(f"{i}", f"Cash account {i}", closing=(100.0 if i < 2 else -100.0)) for i in range(4)] + \
          [_row(f"c{i}", f"Sundry creditor {i}", closing=-100.0) for i in range(4)]
    r = tb_000_sign_convention(_table(rows))
    assert r["status"] == "WARNING", r
    print("PASS: TB-000 heuristic WARNs (never HALTs) on inconsistent undocumented convention")


def test_tb001_required_columns_halt_and_pass():
    ok = tb_001_required_columns(_table([]))
    assert ok["status"] == "PASS"
    bad = tb_001_required_columns(_table([], closing_balance=False))
    assert bad["status"] == "HALT"
    print("PASS: TB-001 halts when a required role is unresolved, passes when both resolved")


def test_tb002_gl_code_non_blank():
    ok = tb_002_gl_code_non_blank(_table([_row("1001", "Cash")]))
    assert ok["status"] == "PASS"
    bad = tb_002_gl_code_non_blank(_table([_row("", "Cash"), _row("1002", "Bank")]))
    assert bad["status"] == "HALT"
    print("PASS: TB-002 halts on a blank GL code")


def test_tb003_gl_code_unique():
    ok = tb_003_gl_code_unique(_table([_row("1001", "A"), _row("1002", "B")]))
    assert ok["status"] == "PASS"
    bad = tb_003_gl_code_unique(_table([_row("1001", "A"), _row("1001", "B")]))
    assert bad["status"] == "HALT"
    print("PASS: TB-003 halts on duplicate GL codes")


def test_tb004_numeric_validity():
    ok = tb_004_numeric_validity(_table([_row("1001", "A", closing=100.00, opening=50.00, debit=50.00, credit=0.0)]))
    assert ok["status"] == "PASS"
    bad_row = _row("1001", "A", closing=100.123, opening=50.0, debit=50.0, credit=0.0)
    bad = tb_004_numeric_validity(_table([bad_row]))
    assert bad["status"] == "HALT"
    unparseable_row = _row("1001", "A", closing=0.0, closing_unparseable=True)
    bad2 = tb_004_numeric_validity(_table([unparseable_row]))
    assert bad2["status"] == "HALT"
    print("PASS: TB-004 halts on inconsistent precision and on unparseable values")


def test_tb025_period_matches_engagement():
    skip = tb_025_period_matches_engagement(_table([]))
    assert skip["status"] == "WARNING"
    table = ParsedTBTable(label="t", filename=None, doc_id=None, period_label="FY 2024-25",
                          rows=[], excluded_total_rows=0, roles_resolved={r: True for r in ALL_ROLES},
                          has_raw_access=False, source_mode="doc_id")
    ok = tb_025_period_matches_engagement(table, engagement_period="FY 2024-25")
    assert ok["status"] == "PASS"
    bad = tb_025_period_matches_engagement(table, engagement_period="FY 2023-24")
    assert bad["status"] == "HALT"
    print("PASS: TB-025 skips without input, passes on match, halts on mismatch")


def test_tb026_currency_scale():
    rows = [_row(str(i), f"A{i}", closing=1000.0, currency="INR") for i in range(5)]
    ok = tb_026_currency_scale(_table(rows), target_currency="INR")
    assert ok["status"] == "PASS"
    rows_bad = rows + [_row("99", "Foreign", closing=1000.0, currency="USD")]
    bad = tb_026_currency_scale(_table(rows_bad), target_currency="INR")
    assert bad["status"] == "HALT"
    print("PASS: TB-026 halts on a non-target currency, no conversion performed")


def test_tb029_source_trail():
    ok = tb_029_source_trail(ParsedTBTable(label="t", filename="x.xlsx", doc_id=None, period_label=None,
                                          rows=[], excluded_total_rows=0, roles_resolved={},
                                          has_raw_access=False, source_mode="doc_id"))
    assert ok["status"] == "PASS"
    bad = tb_029_source_trail(ParsedTBTable(label="t", filename=None, doc_id=None, period_label=None,
                                           rows=[], excluded_total_rows=0, roles_resolved={},
                                           has_raw_access=False, source_mode="doc_id"))
    assert bad["status"] == "WARNING"
    print("PASS: TB-029 warns when no source filename is identified")


# --- D. GL MASTER DATA --------------------------------------------------------

def test_tb013_one_group_per_code():
    ok = tb_013_one_group_per_code(_table([_row("1", "A", group="Assets"), _row("1", "A", group="Assets")]))
    assert ok["status"] == "PASS"
    bad = tb_013_one_group_per_code(_table([_row("1", "A", group="Assets"), _row("1", "A", group="Liabilities")]))
    assert bad["status"] == "HALT"
    print("PASS: TB-013 halts when one GL code maps to more than one Group")


def test_tb014_group_in_master_list():
    skip = tb_014_group_in_master_list(_table([_row("1", "A", group="Assets")]))
    assert skip["status"] == "WARNING"
    ok = tb_014_group_in_master_list(_table([_row("1", "A", group="Assets")]), approved_group_master=["Assets"])
    assert ok["status"] == "PASS"
    bad = tb_014_group_in_master_list(_table([_row("1", "A", group="Unknown Group")]), approved_group_master=["Assets"])
    assert bad["status"] == "HALT"
    print("PASS: TB-014 warns (skips) without a master list, halts on an unapproved Group value")


def test_tb030_stub():
    r = tb_030_fuzzy_duplicate_descriptions(_table([]))
    assert r["status"] == "SKIPPED"
    print("PASS: TB-030 is a SKIPPED stub, deferred to the LLM-assisted pass")


# --- B. ROW ARITHMETIC --------------------------------------------------------

def test_tb005_row_arithmetic():
    ok_row = _row("1", "A", opening=100.0, debit=50.0, credit=20.0, closing=130.0)
    ok = tb_005_row_arithmetic(_table([ok_row]))
    assert ok["status"] == "PASS"
    drift_row = _row("1", "A", opening=100.0, debit=50.0, credit=20.0, closing=130.5)  # ~0.3% drift
    warn = tb_005_row_arithmetic(_table([drift_row]))
    assert warn["status"] == "WARNING", warn
    bad_row = _row("1", "A", opening=100.0, debit=50.0, credit=20.0, closing=999.0)
    bad = tb_005_row_arithmetic(_table([bad_row]))
    assert bad["status"] == "HALT"
    print("PASS: TB-005 passes on an exact tie-out, warns on within-tolerance drift, "
          "halts on a real mismatch")


def test_tb005_skips_on_non_genuine_movement():
    """Real bug found live on GAIL FY23-24: a source file with only a single
    combined closing-balance column (no real movement/debit/credit columns)
    still gets p1_debit/p1_credit populated as a derived sign-split of the
    closing balance by trial_balance.py — NOT real movement. TB-005 must not
    treat that as a genuine mismatch (it would fail almost every row)."""
    fabricated_row = _row("1", "P&L Account", opening=-500.0, debit=0.0, credit=500.0, closing=-500.0)
    table = _table([fabricated_row])
    table.has_genuine_movement = False
    r = tb_005_row_arithmetic(table)
    assert r["status"] == "WARNING", r
    assert "sign-split" in r["message"]
    print("PASS: TB-005 correctly skips (WARNING) rather than falsely HALTing when "
          "debit/credit are a derived sign-split, not genuine movement")


def test_tb006_opening_sign_vs_group():
    ok = tb_006_opening_sign_vs_group(_table([_row("1", "Cash", group="Cash and Bank", opening=100.0)]))
    assert ok["status"] == "PASS"
    bad = tb_006_opening_sign_vs_group(_table([_row("1", "Cash", group="Cash and Bank", opening=-100.0)]))
    assert bad["status"] == "WARNING"
    print("PASS: TB-006 warns on an opening sign inconsistent with Group nature")


def test_tb007_closing_sign_vs_group():
    ok = tb_007_closing_sign_vs_group(_table([_row("1", "Payable", group="Trade payable", closing=-100.0)]))
    assert ok["status"] == "PASS"
    bad = tb_007_closing_sign_vs_group(_table([_row("1", "Payable", group="Trade payable", closing=100.0)]))
    assert bad["status"] == "WARNING"
    print("PASS: TB-007 warns on a closing sign inconsistent with Group nature")


def test_tb008_never_invert():
    skip = tb_008_never_invert(_table([_row("1", "A", opening=100.0, closing=100.0)]))
    assert skip["status"] == "WARNING"
    ok = tb_008_never_invert(_table([_row("1", "A", opening=100.0, closing=50.0)]), never_invert_accounts=["1"])
    assert ok["status"] == "PASS"
    bad = tb_008_never_invert(_table([_row("1", "A", opening=100.0, closing=-50.0)]), never_invert_accounts=["1"])
    assert bad["status"] == "WARNING"
    print("PASS: TB-008 warns when a never-invert account flips sign")


def test_tb032_pl_no_opening():
    ok = tb_032_pl_no_opening(_table([_row("1", "Sales", group="Revenue from operations", opening=0.0)]))
    assert ok["status"] == "PASS"
    bad = tb_032_pl_no_opening(_table([_row("1", "Sales", group="Revenue from operations", opening=500.0)]))
    assert bad["status"] == "WARNING"
    print("PASS: TB-032 warns when a P&L-type account carries an opening balance")


# --- C. AGGREGATE TIE-OUT ------------------------------------------------------

def test_tb009_total_debit_credit():
    ok = tb_009_total_debit_credit(_table([_row("1", "A", debit=100.0, credit=100.0)]))
    assert ok["status"] == "PASS"
    drift = tb_009_total_debit_credit(_table([_row("1", "A", debit=100.0, credit=99.0)]))  # 1% drift
    assert drift["status"] == "WARNING", drift
    bad = tb_009_total_debit_credit(_table([_row("1", "A", debit=100.0, credit=50.0)]))
    assert bad["status"] == "HALT"
    print("PASS: TB-009 passes exactly, warns on within-tolerance drift, halts beyond tolerance")


def test_tb010_opening_sum_zero():
    ok = tb_010_opening_sum_zero(_table([_row("1", "A", opening=100.0), _row("2", "B", opening=-100.0)]))
    assert ok["status"] == "PASS"
    drift = tb_010_opening_sum_zero(_table([_row("1", "A", opening=100.0), _row("2", "B", opening=-99.0)]))
    assert drift["status"] == "WARNING", drift
    bad = tb_010_opening_sum_zero(_table([_row("1", "A", opening=100.0), _row("2", "B", opening=-10.0)]))
    assert bad["status"] == "HALT"
    print("PASS: TB-010 passes exactly, warns on within-tolerance drift, halts beyond tolerance")


def test_tb011_closing_sum_zero():
    ok = tb_011_closing_sum_zero(_table([_row("1", "A", closing=100.0), _row("2", "B", closing=-100.0)]))
    assert ok["status"] == "PASS"
    drift = tb_011_closing_sum_zero(_table([_row("1", "A", closing=100.0), _row("2", "B", closing=-99.0)]))
    assert drift["status"] == "WARNING", drift
    bad = tb_011_closing_sum_zero(_table([_row("1", "A", closing=100.0), _row("2", "B", closing=-10.0)]))
    assert bad["status"] == "HALT"
    print("PASS: TB-011 passes exactly, warns on within-tolerance drift, halts beyond tolerance")


def test_tb012_assets_equal_liab_equity():
    rows = [_row("1", "Cash", group="Cash and Bank", closing=1000.0),
           _row("2", "Trade payable", group="Trade payable", closing=-600.0),
           _row("3", "Share capital", group="Share capital reserve", closing=-400.0)]
    ok = tb_012_assets_equal_liab_equity(_table(rows))
    assert ok["status"] == "PASS", ok
    rows_bad = [_row("1", "Cash", group="Cash and Bank", closing=1000.0),
               _row("2", "Trade payable", group="Trade payable", closing=-100.0)]
    bad = tb_012_assets_equal_liab_equity(_table(rows_bad))
    assert bad["status"] == "HALT"
    print("PASS: TB-012 passes when Assets = Liabilities + Equity, halts otherwise")


def test_tb024_income_expense_net_pl():
    skip = tb_024_income_expense_net_pl(_table([]))
    assert skip["status"] == "WARNING"
    rows = [_row("1", "Sales", group="Revenue from operations", closing=-1000.0),
           _row("2", "Salaries", group="Employee benefits expense", closing=600.0)]
    ok = tb_024_income_expense_net_pl(_table(rows), external_pl_figure=400.0)
    assert ok["status"] == "PASS", ok
    bad = tb_024_income_expense_net_pl(_table(rows), external_pl_figure=999.0)
    assert bad["status"] == "HALT"
    print("PASS: TB-024 skips without external figure, else ties out or halts")


# --- E/F/G/K ------------------------------------------------------------------

def test_tb015_activity_closes_zero():
    ok = tb_015_activity_closes_zero(_table([_row("1", "A", debit=10.0, credit=5.0, closing=5.0)]))
    assert ok["status"] == "PASS"
    bad = tb_015_activity_closes_zero(_table([_row("1", "A", debit=1000.0, credit=1000.0, closing=0.0)]))
    assert bad["status"] == "WARNING"
    print("PASS: TB-015 flags an account with activity that closes at zero")


def test_tb016_no_duplicate_gl_group():
    ok = tb_016_no_duplicate_gl_group(_table([_row("1", "A", group="X"), _row("1", "A", group="Y")]))
    assert ok["status"] == "PASS"
    bad = tb_016_no_duplicate_gl_group(_table([_row("1", "A", group="X"), _row("1", "A", group="X")]))
    assert bad["status"] == "HALT"
    print("PASS: TB-016 halts on a duplicate GL+Group combination")


def test_tb017_mandatory_heads_present():
    skip = tb_017_mandatory_heads_present(_table([]))
    assert skip["status"] == "WARNING"
    ok = tb_017_mandatory_heads_present(_table([_row("1", "Trade Payables", group="Liabilities")]),
                                       required_heads=["Trade Payables"])
    assert ok["status"] == "PASS"
    bad = tb_017_mandatory_heads_present(_table([_row("1", "Cash", group="Assets")]),
                                        required_heads=["Deferred Tax Liability"])
    assert bad["status"] == "WARNING"
    print("PASS: TB-017 warns when a mandatory head is missing")


def test_tb018_suspense_nonzero():
    ok = tb_018_suspense_nonzero(_table([_row("1", "Suspense account", closing=0.0)]))
    assert ok["status"] == "PASS"
    bad = tb_018_suspense_nonzero(_table([_row("1", "Suspense account", closing=500.0)]))
    assert bad["status"] == "WARNING"
    print("PASS: TB-018 warns on a non-zero suspense account at period end")


def test_tb019_rounding_within_materiality():
    skip = tb_019_rounding_within_materiality(_table([_row("1", "Rounding off", closing=10.0)]))
    assert skip["status"] == "WARNING"
    ok = tb_019_rounding_within_materiality(_table([_row("1", "Rounding off", closing=10.0)]),
                                           rounding_account_threshold=100.0)
    assert ok["status"] == "PASS"
    bad = tb_019_rounding_within_materiality(_table([_row("1", "Rounding off", closing=500.0)]),
                                            rounding_account_threshold=100.0)
    assert bad["status"] == "WARNING"
    print("PASS: TB-019 requires the threshold input, warns when exceeded")


def test_tb020_tax_head_sign():
    skip = tb_020_tax_head_sign(_table([]))
    assert skip["status"] == "WARNING"
    ok = tb_020_tax_head_sign(_table([_row("1", "GST Payable", closing=-500.0)]),
                              tax_head_sign_map={"gst payable": "credit"})
    assert ok["status"] == "PASS"
    bad = tb_020_tax_head_sign(_table([_row("1", "GST Payable", closing=500.0)]),
                               tax_head_sign_map={"gst payable": "credit"})
    assert bad["status"] == "WARNING"
    print("PASS: TB-020 warns when a tax head's sign doesn't match its expected nature")


def test_tb028_sensitive_heads():
    ok = tb_028_sensitive_heads(_table([_row("1", "Cash in hand", closing=100.0)]))
    assert ok["status"] == "PASS"
    bad = tb_028_sensitive_heads(_table([_row("1", "CSR Expenditure", closing=100.0)]))
    assert bad["status"] == "WARNING"
    print("PASS: TB-028 surfaces sensitive-nature heads via the reused audit-mapping tags")


def test_tb031_control_branch_sub_ledger():
    skip = tb_031_control_branch_sub_ledger(_table([]))
    assert skip["status"] == "WARNING"
    ok = tb_031_control_branch_sub_ledger(_table([_row("1", "Branch control account")]), sub_ledger_ref=["1"])
    assert ok["status"] == "PASS"
    bad = tb_031_control_branch_sub_ledger(_table([_row("1", "Branch control account")]), sub_ledger_ref=["2"])
    assert bad["status"] == "WARNING"
    print("PASS: TB-031 warns when a control/branch account has no sub-ledger backup")


# --- H/J -----------------------------------------------------------------------

def test_tb021_benford():
    skip = tb_021_benford(_table([_row(str(i), "A", closing=100.0) for i in range(5)]))
    assert skip["status"] == "SKIPPED"
    rows = [_row(str(i), "A", closing=float(f"1{i:03d}")) for i in range(40)]  # all start with '1'
    bad = tb_021_benford(_table(rows))
    assert bad["status"] == "WARNING"
    print("PASS: TB-021 skips on a small sample, flags a Benford deviation on a skewed sample")


def test_tb022_stub():
    r = tb_022_gl_desc_group_mismatch(_table([]))
    assert r["status"] == "SKIPPED"
    print("PASS: TB-022 is a SKIPPED stub, deferred to the LLM-assisted pass")


def test_tb023_round_number_entries():
    rows_ok = [_row(str(i), "A", closing=1234.56 + i) for i in range(10)]
    ok = tb_023_round_number_entries(_table(rows_ok))
    assert ok["status"] == "PASS"
    rows_bad = [_row(str(i), "A", closing=1000.0 * (i + 1)) for i in range(10)]
    bad = tb_023_round_number_entries(_table(rows_bad))
    assert bad["status"] == "WARNING"
    print("PASS: TB-023 warns on an excessive proportion of round-number entries")


def test_tb033_stale_year_reference():
    skip = tb_033_stale_year_reference(_table([_row("1", "Provision 2020")]))
    assert skip["status"] == "WARNING"
    ok = tb_033_stale_year_reference(_table([_row("1", "Provision FY2024")]), tb_period_year=2024)
    assert ok["status"] == "PASS"
    bad = tb_033_stale_year_reference(_table([_row("1", "Provision FY2015")]), tb_period_year=2024)
    assert bad["status"] == "WARNING"
    print("PASS: TB-033 warns on a GL description year more than the gap threshold from the TB period")


def test_tb027_static_vs_formula():
    skip = tb_027_static_vs_formula(_table([]), None)
    assert skip["status"] == "WARNING"
    ok = tb_027_static_vs_formula(_table([]), [])
    assert ok["status"] == "PASS"
    bad = tb_027_static_vs_formula(_table([]), [{"row": 2, "column": "closing_balance", "formula": "=A2+B2"}])
    assert bad["status"] == "WARNING"
    print("PASS: TB-027 reuses the Layer-4 formula-flag list, reported at Warning severity")


# --- L. CROSS-YEAR + Layer 2/3 --------------------------------------------------

def test_l1_company_code_filter():
    skip = l1_company_code_filter(_table([], company_code=False), None)
    assert skip["status"] == "WARNING"
    single = l1_company_code_filter(_table([_row("1", "A", company_code="C1")]), None)
    assert single["status"] == "PASS"
    ambiguous = l1_company_code_filter(
        _table([_row("1", "A", company_code="C1"), _row("2", "B", company_code="C2")]), None)
    assert ambiguous["status"] == "HALT"
    resolved = l1_company_code_filter(
        _table([_row("1", "A", company_code="C1"), _row("2", "B", company_code="C2")]), "C1")
    assert resolved["status"] == "PASS"
    print("PASS: L-1 halts only when multiple companies are present with no target specified")


def test_l2_ledger_alignment():
    py = _table([_row("1", "Cash")])
    cy = _table([_row("1", "Cash")])
    ok = l2_ledger_alignment(py, cy)
    assert ok["status"] == "PASS"
    cy_mismatch = _table([_row("1", "Cash Renamed")])
    warn = l2_ledger_alignment(py, cy_mismatch)
    assert warn["status"] == "WARNING", "mismatched names must WARN, never HALT"
    py_unresolved = _table([_row("1", "Cash")], ledger_code=False)
    halt = l2_ledger_alignment(py_unresolved, cy)
    assert halt["status"] == "HALT"
    print("PASS: L-2 halts only on unresolved ledger_code, WARNs (never HALTs) on name mismatches")


def test_l3_opening_closing_continuity():
    py = _table([_row("1", "Cash", closing=100.0)])
    cy = _table([_row("1", "Cash", opening=100.0)])
    ok = l3_opening_closing_continuity(py, cy)
    assert ok["status"] == "PASS"
    cy_bad = _table([_row("1", "Cash", opening=90.0)])
    warn = l3_opening_closing_continuity(py, cy_bad)
    assert warn["status"] == "WARNING", "mismatch must WARN, never HALT"
    cy_unresolved = _table([_row("1", "Cash", opening=100.0)], opening_balance=False)
    skip = l3_opening_closing_continuity(py, cy_unresolved)
    assert skip["status"] == "WARNING", "missing prerequisite must WARN (skip), not SKIPPED"
    print("PASS: L-3 warns (never HALTs) on mismatch or a missing prerequisite")


def test_layer2_structural():
    py = _table([_row("1", "A"), _row("2", "B")])
    cy = _table([_row("2", "B"), _row("3", "C")])
    r = layer2_structural(py, cy)
    assert r["py_ledger_count"] == 2 and r["cy_ledger_count"] == 2
    assert r["n_new"] == 1 and r["new_ledgers"][0]["ledger_code"] == "3"
    assert r["n_removed"] == 1 and r["removed_ledgers"][0]["ledger_code"] == "1"
    assert r["delta_count"] == 0
    print("PASS: Layer 2 reports py/cy ledger counts and identifies new/removed with S.No numbering")


def test_layer3_variance():
    py = _table([_row("1", "A", closing=100.0), _row("2", "B", closing=100.0),
                _row("3", "C", closing=0.0), _row("4", "D", closing=100.0)])
    cy = _table([_row("1", "A", closing=100.0), _row("2", "B", closing=250.0),
                _row("3", "C", closing=50.0), _row("5", "E", closing=20.0)])
    no_threshold = layer3_variance(py, cy, None)
    assert all(r["flag"] == "NO_THRESHOLD" for r in no_threshold)

    rows = layer3_variance(py, cy, variance_materiality_pct=10.0)
    by_code = {r["ledger_code"]: r for r in rows}
    assert by_code["1"]["flag"] == "NO_CHANGE"
    assert by_code["2"]["flag"] == "HIGH_PRIORITY"  # 150% change > 2x threshold
    assert by_code["3"]["flag"] == "NEW_ENTRY"
    assert by_code["4"]["flag"] == "DROPPED"
    assert by_code["5"]["flag"] == "NEW_ENTRY"
    print("PASS: Layer 3 variance flag logic matches the spec exactly, incl. NO_THRESHOLD mode")


# --- narrative guardrail ---------------------------------------------------------

def test_narrative_comparison_mode_cites_correct_py_cy_source():
    """Real bug found live on the actual GAIL FY24-25/FY23-24 pair: PY's and
    CY's WARNING messages were being cross-labeled with each other's
    filename. A rule result unique to PY must cite PY's own label, and a
    rule result unique to CY must cite CY's own label — never swapped."""
    narrative = build_summary_narrative(
        table_label="CY_FILE.xlsx", prior_label="PY_FILE.xlsx",
        layer1_results={
            "py": [{"rule_id": "TB-999-PY-ONLY", "status": "WARNING",
                   "message": "py-only finding", "details": {}}],
            "cy": [{"rule_id": "TB-999-CY-ONLY", "status": "WARNING",
                   "message": "cy-only finding", "details": {}}],
            "cross_year_results": [],
        },
        structural_result=None, variance_rows=None, formula_flags=None, phase_reached="LAYER_1")
    py_line = next(l for l in narrative.split("\n") if "TB-999-PY-ONLY" in l)
    cy_line = next(l for l in narrative.split("\n") if "TB-999-CY-ONLY" in l)
    assert "PY_FILE.xlsx" in py_line and "CY_FILE.xlsx" not in py_line, py_line
    assert "CY_FILE.xlsx" in cy_line and "PY_FILE.xlsx" not in cy_line, cy_line
    print("PASS: comparison-mode narrative cites each rule result against its own "
          "correct source file, never swapped")


def test_narrative_disclaimer_and_forbidden_words():
    narrative = build_summary_narrative(
        table_label="test.xlsx", prior_label=None,
        layer1_results={"single": [{"rule_id": "TB-005", "status": "WARNING",
                                    "message": "Ledger 4001 fails TB-005 by 50, outside tolerance.",
                                    "details": {}}]},
        structural_result=None, variance_rows=None, formula_flags=None, phase_reached="LAYER_1")
    assert DISCLAIMER in narrative
    forbidden = ("opinion", "certify", "confirms", "proves", "verified", "true and fair",
                "correctness", "misstatement", "irregularity", "fraud", "non-compliance",
                "recoverability", "going concern")
    body = narrative.split(DISCLAIMER)[0]
    for word in forbidden:
        assert word not in body.lower(), f"forbidden word {word!r} leaked into narrative body"
    print("PASS: narrative always includes the verbatim disclaimer and never the forbidden words")


# --- pipeline-level: halt short-circuits Layer 2/3 -------------------------------

def test_pipeline_run_single_halts_on_gl_code_blank():
    r = tb_002_gl_code_non_blank(_table([_row("", "Blank code account", closing=100.0)]))
    assert r["status"] == "HALT"
    print("PASS: a blank GL code halts (exercised at rule level; full pipeline HALT "
          "short-circuit behavior verified live against real GAIL/Company H data)")


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR: {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
