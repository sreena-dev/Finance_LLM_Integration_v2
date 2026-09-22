"""Tests for backend/tools/_shared.py::resolve_artifact_path's wrong-format
correction. A real /audit run showed the LLM passing fsli_summary.json (the
JSON sibling build_fsli_summary also writes) into a param that needs
fsli_summary.parquet -- resolve_artifact_path only checked existence, not
format, so it trusted the wrong-format path and pl.read_parquet() crashed
with `polars.exceptions.ComputeError: parquet: File out of specification:
The file must end with PAR1`."""

import json

import polars as pl

from modes.trial_balance.pipeline.tools import (
    SIGN_CONVENTION_STATS_BASIS_LABEL,
    classify_row,
    concentration_rows_from_canonical,
    control_totals,
    mapped_only,
    resolve_artifact_path,
    resolve_severity,
    safe_fmt,
    sign_convention_stats,
)


def test_wrong_extension_corrected_when_canonical_file_exists(tmp_path):
    pl.DataFrame({"x": [1]}).write_parquet(tmp_path / "fsli_summary.parquet")
    with open(tmp_path / "fsli_summary.json", "w") as f:
        json.dump({"x": 1}, f)

    resolved = resolve_artifact_path(str(tmp_path / "fsli_summary.json"), tmp_path, "fsli_summary.parquet")
    assert resolved == tmp_path / "fsli_summary.parquet"


def test_wrong_extension_kept_when_canonical_file_absent(tmp_path):
    with open(tmp_path / "fsli_summary.json", "w") as f:
        json.dump({"x": 1}, f)

    resolved = resolve_artifact_path(str(tmp_path / "fsli_summary.json"), tmp_path, "fsli_summary.parquet")
    assert resolved == tmp_path / "fsli_summary.json"


def test_matching_extension_passed_through_unchanged(tmp_path):
    pl.DataFrame({"x": [1]}).write_parquet(tmp_path / "custom_name.parquet")

    resolved = resolve_artifact_path(str(tmp_path / "custom_name.parquet"), tmp_path, "fsli_summary.parquet")
    assert resolved == tmp_path / "custom_name.parquet"


def test_none_provided_path_uses_default(tmp_path):
    resolved = resolve_artifact_path(None, tmp_path, "fsli_summary.parquet")
    assert resolved == tmp_path / "fsli_summary.parquet"


def test_nonexistent_provided_path_uses_default(tmp_path):
    resolved = resolve_artifact_path(str(tmp_path / "does_not_exist.parquet"), tmp_path, "fsli_summary.parquet")
    assert resolved == tmp_path / "fsli_summary.parquet"


# TB-R05/R16/R17: resolve_severity must never rate an amount below clearly-trivial as
# MEDIUM+ regardless of how high the rule-trigger score is, and must be the single
# severity function every consumer defers to.

def test_resolve_severity_floors_trivial_amount_regardless_of_rule_score():
    # composite_score of 90 would be "Critical" on rule score alone -- but the amount
    # is below clearly_trivial, so it must floor to Low (never MEDIUM+ per TB-R05's AT).
    assert resolve_severity(rule_score=90, abs_amount=0.0, clearly_trivial=275081.14) == "Low"
    assert resolve_severity(rule_score=30, abs_amount=100.0, clearly_trivial=275081.14) == "Low"


def test_resolve_severity_zero_rule_score_and_trivial_amount_is_information_request():
    assert resolve_severity(rule_score=0, abs_amount=0.0, clearly_trivial=275081.14) == "Information Request"


def test_resolve_severity_normal_tiering_above_trivial_threshold():
    triv = 275081.14
    assert resolve_severity(rule_score=80, abs_amount=1_000_000, clearly_trivial=triv) == "Critical"
    assert resolve_severity(rule_score=60, abs_amount=1_000_000, clearly_trivial=triv) == "High"
    assert resolve_severity(rule_score=30, abs_amount=1_000_000, clearly_trivial=triv) == "Medium"
    assert resolve_severity(rule_score=10, abs_amount=1_000_000, clearly_trivial=triv) == "Low"
    assert resolve_severity(rule_score=0, abs_amount=1_000_000, clearly_trivial=triv) == "Information Request"


def test_resolve_severity_no_trivial_threshold_configured_skips_floor():
    # clearly_trivial=0 (materiality not computed) must not floor everything to Low.
    assert resolve_severity(rule_score=90, abs_amount=0.0, clearly_trivial=0.0) == "Critical"


# TB-R20: safe_fmt must show a genuine sub-rupee residual at full precision instead of
# blanket-rounding it to "0", while leaving normal large amounts unaffected.

def test_safe_fmt_shows_small_residual_at_precision():
    assert safe_fmt(3.05e-5) == "~0 (residual: 0.000030)"
    assert safe_fmt(-0.0001220703125) == "~0 (residual: -0.000122)"


def test_safe_fmt_large_amounts_unaffected():
    assert safe_fmt(5501622.81) == "5,501,623"
    assert safe_fmt(0) == "0"


# TB-R13/R14: concentration_rows_from_canonical must not double-count offsetting contra
# pairs as two findings, and must not surface a ~100% "concentration" artefact from a
# report_head with only one non-trivial account.

def _conc_df(rows):
    return pl.DataFrame(rows)


def test_concentration_collapses_offsetting_pair_into_one_finding():
    df = _conc_df([
        {"gl_code": "1", "gl_name": "Other Cont Payments", "report_head": "Expenses", "closing_balance": 12290000000.0},
        {"gl_code": "2", "gl_name": "Less alloc - Expense", "report_head": "Expenses", "closing_balance": -12220000000.0},
        {"gl_code": "3", "gl_name": "Salaries", "report_head": "Expenses", "closing_balance": 500000.0},
    ])
    rows = concentration_rows_from_canonical(df, threshold_pct=5.0)
    # Both large accounts individually exceed 5% of the Expenses head -- must collapse to
    # ONE annotated finding, not two.
    big = [r for r in rows if abs(r["closing"]) > 1_000_000]
    assert len(big) == 1
    assert big[0]["offsetting_pair"] is True
    assert "Offsetting pair" in big[0]["note"]


def test_concentration_denominator_degeneracy_guard_skips_single_account_head():
    df = _conc_df([
        {"gl_code": "1", "gl_name": "Sole Revenue Line", "report_head": "Revenue", "closing_balance": -550162281.0},
        {"gl_code": "2", "gl_name": "Some Asset", "report_head": "Assets", "closing_balance": 1000.0},
        {"gl_code": "3", "gl_name": "Other Asset", "report_head": "Assets", "closing_balance": 2000.0},
    ])
    rows = concentration_rows_from_canonical(df, threshold_pct=5.0, clearly_trivial=100.0)
    # Revenue has exactly one non-trivial account -- must not surface a ~100% artefact.
    assert not any(r["fs_head"] == "Revenue" for r in rows)


def test_concentration_without_clearly_trivial_keeps_legacy_behavior():
    df = _conc_df([
        {"gl_code": "1", "gl_name": "Sole Revenue Line", "report_head": "Revenue", "closing_balance": -550162281.0},
    ])
    # clearly_trivial defaults to 0.0 (guard disabled) -- unchanged from before TB-R14.
    rows = concentration_rows_from_canonical(df, threshold_pct=5.0)
    assert len(rows) == 1


# TB-R20: control_totals' footing identity ("total_debit"/"total_credit"/"difference")
# must be computed from signed CLOSING BALANCES, not from the raw debit/credit TURNOVER
# columns -- turnover ties by construction in a SAP period-extract and proves nothing
# about whether the TB itself foots. An EPIL live run showed turnover reported as
# "Total Debit"/"Total Credit" while the TB was independently flagged as not footing.

def _ctrl_df(rows):
    return pl.DataFrame(rows)


def test_control_totals_foots_on_closing_balances_even_when_turnover_does_not_tie():
    # Turnover columns are deliberately unequal (debit turnover >> credit turnover, as in
    # a SAP period-extract with heavy in-year activity), but closing balances DO foot:
    # one debit-balance account (+100) exactly offset by one credit-balance account (-100).
    df = _ctrl_df([
        {"gl_code": "1", "debit": 900.0, "credit": 100.0, "closing_balance": 100.0, "opening_balance": 0.0},
        {"gl_code": "2", "debit": 50.0, "credit": 800.0, "closing_balance": -100.0, "opening_balance": 0.0},
    ])
    ctrl = control_totals(df)
    assert ctrl["total_debit"] == 100.0
    assert ctrl["total_credit"] == 100.0
    assert ctrl["difference"] == 0.0
    # Turnover is reported separately and is NOT forced to zero -- it's a different,
    # legitimately unequal figure that must not be conflated with the footing check.
    assert ctrl["turnover_total_debit"] == 950.0
    assert ctrl["turnover_total_credit"] == 900.0
    assert ctrl["turnover_total_debit"] != ctrl["turnover_total_credit"]


def test_control_totals_flags_genuine_closing_balance_mismatch_even_when_turnover_ties():
    # Turnover columns tie exactly (as most ERP exports do by construction), but closing
    # balances genuinely do not foot -- this is the real defect the check exists to catch,
    # and it must not be masked by a coincidentally-balanced turnover column.
    df = _ctrl_df([
        {"gl_code": "1", "debit": 500.0, "credit": 500.0, "closing_balance": 100.0, "opening_balance": 0.0},
        {"gl_code": "2", "debit": 500.0, "credit": 500.0, "closing_balance": -40.0, "opening_balance": 0.0},
    ])
    ctrl = control_totals(df)
    assert ctrl["turnover_total_debit"] == ctrl["turnover_total_credit"] == 1000.0
    assert ctrl["total_debit"] == 100.0
    assert ctrl["total_credit"] == 40.0
    assert ctrl["difference"] == 60.0
    assert ctrl["sum_closing"] == 60.0


def test_sign_convention_stats_carries_basis_label():
    # Wave 2 Fix 5: this is a THIRD, genuinely different sign-convention basis from
    # canonical.py's TB-000 and run_comparison_sign_check (both gl_name keyword-anchor
    # screens) -- it filters by resolved report_head membership instead, a much larger
    # population. Every consumer must know which basis a percentage came from.
    df = pl.DataFrame({
        "report_head": ["Assets", "Assets", "Liabilities"],
        "closing_balance": [100.0, -50.0, -30.0],
    })
    stats = sign_convention_stats(df)
    assert stats["basis"] == SIGN_CONVENTION_STATS_BASIS_LABEL
    assert stats["basis"] == "population-wide, mapped-row basis (report_head classification)"


# classify_row() must normalize short Schedule III abbreviations (a client-supplied grouping
# workbook's own account_type/main_head text, not this pipeline's own taxonomy, which only ever
# emits full words) the same way it normalizes full words -- a real OVL FY2024-25 dataset shipped
# account_type as "AST"/"LEQ"/"EXP"/"INC", which fell through classify_row's substring matching
# into the raw-code-verbatim fallback and silently broke every report_head-keyed screen
# (sign convention, going-concern, concentration all read 0/0 or wrong totals as a result).

def _mapped_row(account_type, main_head=None):
    return {
        "mapped_status": "MAPPED",
        "account_type": account_type,
        "main_head": main_head,
        "sub_head_1": None,
        "sub_head_2": None,
        "bs_pl": None,
    }


def test_classify_row_normalizes_known_abbreviations():
    assert classify_row(_mapped_row("AST"))[0] == "Assets"
    assert classify_row(_mapped_row("LEQ"))[0] == "Liabilities"
    assert classify_row(_mapped_row("EXP"))[0] == "Expenses"
    assert classify_row(_mapped_row("INC"))[0] == "Revenue"
    # Case-insensitive, and via main_head when account_type is absent.
    assert classify_row(_mapped_row(None, main_head="ast"))[0] == "Assets"


def test_classify_row_still_falls_through_unrecognized_codes_verbatim():
    # A genuinely unrecognized code must still render (never crash the report) --
    # only the KNOWN abbreviations get normalized, not an arbitrary passthrough.
    assert classify_row(_mapped_row("ZZQ"))[0] == "ZZQ"


def test_classify_row_full_words_still_work_unchanged():
    assert classify_row(_mapped_row("Asset"))[0] == "Assets"
    assert classify_row(_mapped_row("Liability"))[0] == "Liabilities"
    assert classify_row(_mapped_row("Equity"))[0] == "Equity"
    assert classify_row(_mapped_row("Income"))[0] == "Revenue"
    assert classify_row(_mapped_row("Expense"))[0] == "Expenses"


def test_classify_row_unmapped_status_always_wins_over_account_type():
    row = _mapped_row("AST")
    row["mapped_status"] = "UNMATCHED"
    assert classify_row(row)[0] == "Unmapped"


def test_classify_row_disambiguates_ambiguous_leq_via_main_head():
    # A real OVL FY2024-25 dataset used account_type "LEQ" indiscriminately for
    # Liabilities AND Equity rows (even a couple of Asset rows) -- resolving LEQ straight
    # to "Liabilities" silently merged genuine Equity rows into Liabilities, which is
    # exactly what broke going-concern's net-worth calc (_head_total(df, "Equity") found
    # nothing). main_head's own text must get a real chance to disambiguate first.
    assert classify_row(_mapped_row("LEQ", main_head="Equity Share Capital"))[0] == "Equity"
    assert classify_row(_mapped_row("LEQ", main_head="Other Equity"))[0] == "Equity"
    assert classify_row(_mapped_row("LEQ", main_head="Current Liabilities"))[0] == "Liabilities"
    assert classify_row(_mapped_row("LEQ", main_head="Non-Current Liabilities"))[0] == "Liabilities"
    assert classify_row(_mapped_row("LEQ", main_head="Current Assets"))[0] == "Assets"


def test_classify_row_leq_falls_back_to_liabilities_when_main_head_unhelpful():
    # No main_head text to disambiguate with -- LEQ's ambiguous default (Liabilities,
    # never silently Equity) still applies so the report never crashes or shows a raw code.
    assert classify_row(_mapped_row("LEQ"))[0] == "Liabilities"


# mapped_only() -- the shared filter analysis-stage functions apply so unmapped/unmatched rows
# never enter ratios/concentration/materiality/risk screens, while validation-stage functions
# (control_totals, data-sufficiency grading, Layer 1/2 validation) intentionally never call it.

def test_mapped_only_excludes_unmapped_and_unmatched():
    df = pl.DataFrame({
        "gl_code": ["1", "2", "3"],
        "mapped_status": ["MAPPED", "UNMAPPED", "UNMATCHED"],
        "closing_balance": [100.0, 200.0, 300.0],
    })
    filtered = mapped_only(df)
    assert filtered.height == 1
    assert filtered["gl_code"].to_list() == ["1"]


def test_mapped_only_passthrough_when_no_mapped_status_column():
    df = pl.DataFrame({"gl_code": ["1"], "closing_balance": [100.0]})
    assert mapped_only(df).height == 1


def test_sign_convention_stats_excludes_unmapped_rows():
    # Same figures as test_sign_convention_stats_carries_basis_label, plus an unmapped row
    # that must not inflate either denominator.
    df = pl.DataFrame({
        "report_head": ["Assets", "Assets", "Liabilities", "Unmapped"],
        "mapped_status": ["MAPPED", "MAPPED", "MAPPED", "UNMATCHED"],
        "closing_balance": [100.0, -50.0, -30.0, 9999.0],
    })
    stats = sign_convention_stats(df)
    assert stats["debit_total"] == 2
    assert stats["credit_total"] == 1


def test_concentration_excludes_unmapped_rows_from_denominator():
    df = pl.DataFrame({
        "gl_code": ["1", "2", "3"],
        "gl_name": ["Sole Revenue Line", "Other Asset", "Unmapped Suspense"],
        "report_head": ["Revenue", "Assets", "Unmapped"],
        "mapped_status": ["MAPPED", "MAPPED", "UNMATCHED"],
        "closing_balance": [-550162281.0, 1000.0, 999999999.0],
    })
    rows = concentration_rows_from_canonical(df, threshold_pct=5.0)
    # The unmapped row's huge balance must never appear as (or skew) a finding.
    assert all(r["gl_code"] != "3" for r in rows)
