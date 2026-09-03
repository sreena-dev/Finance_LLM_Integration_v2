"""Tests for backend/tools/_shared.py::resolve_artifact_path's wrong-format
correction. A real /audit run showed the LLM passing fsli_summary.json (the
JSON sibling build_fsli_summary also writes) into a param that needs
fsli_summary.parquet -- resolve_artifact_path only checked existence, not
format, so it trusted the wrong-format path and pl.read_parquet() crashed
with `polars.exceptions.ComputeError: parquet: File out of specification:
The file must end with PAR1`."""

import json

import polars as pl
import pytest

from modes.trial_balance.pipeline.tools import (
    concentration_rows_from_canonical,
    resolve_artifact_path,
    resolve_severity,
    safe_fmt,
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
