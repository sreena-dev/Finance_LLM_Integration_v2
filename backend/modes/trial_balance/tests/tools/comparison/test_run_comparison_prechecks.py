"""Comparative-analysis QA fix: run_comparison_prechecks.py's opening_closing_continuity
check must exclude ordinary P&L resets (bs_pl == "PL" accounts with a nil CY opening
balance) from break_count/breaks, using the canonical bs_pl column -- not a pattern-match
against the raw main_head display label, which never matches real sub-category labels
("Wages", "Rent", "Auditor fees", ...) and previously let 29 of 30 ordinary resets in a
real PY->CY run be reported as genuine continuity breaks."""

import polars as pl

from modes.trial_balance.pipeline.tools import run_comparison_prechecks


def test_pl_resets_excluded_from_genuine_breaks(tmp_path):
    # PY: two P&L accounts with a closing balance (ordinary year-end position) and one
    # Equity account with a closing balance. CY: all three reset to a DIFFERENT opening
    # balance than PY's closing -- the two P&L accounts reset to 0 (expected), the Equity
    # account carries a genuine, unexplained discontinuity.
    py_df = pl.DataFrame({
        "gl_code": ["100", "101", "200"],
        "closing_balance": [50000.0, 30000.0, 900000.0],
        "debit": [50000.0, 30000.0, 0.0],
        "credit": [0.0, 0.0, 900000.0],
    })
    cy_df = pl.DataFrame({
        "gl_code": ["100", "101", "200"],
        "opening_balance": [0.0, 0.0, 750000.0],  # 100/101 reset to nil (expected); 200 is a real break
        "bs_pl": ["PL", "PL", "BS"],
        "debit": [0.0, 0.0, 0.0],
        "credit": [0.0, 0.0, 0.0],
    })
    py_path = tmp_path / "py.parquet"
    cy_path = tmp_path / "cy.parquet"
    py_df.write_parquet(py_path)
    cy_df.write_parquet(cy_path)

    result = run_comparison_prechecks(str(py_path), str(cy_path), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"

    import json
    report = json.loads((tmp_path / "precheck_results.json").read_text())
    check = next(c for c in report["checks"] if c["check"] == "opening_closing_continuity")

    assert check["break_count"] == 1, "Only the Equity account (gl_code 200) should count as a genuine break"
    assert check["expected_pl_reset_count"] == 2
    assert check["total_raw_break_count"] == 3
    assert [b["gl_code"] for b in check["breaks"]] == ["200"]


def test_pl_account_with_nonzero_cy_opening_is_still_genuine(tmp_path):
    # A P&L account that carries a NONZERO CY opening balance is not an ordinary reset --
    # something is actually wrong (P&L accounts should never carry an opening balance) --
    # so it must NOT be excluded.
    py_df = pl.DataFrame({
        "gl_code": ["100"],
        "closing_balance": [50000.0],
        "debit": [50000.0],
        "credit": [0.0],
    })
    cy_df = pl.DataFrame({
        "gl_code": ["100"],
        "opening_balance": [12000.0],  # nonzero -- not an ordinary reset
        "bs_pl": ["PL"],
        "debit": [0.0],
        "credit": [0.0],
    })
    py_path = tmp_path / "py.parquet"
    cy_path = tmp_path / "cy.parquet"
    py_df.write_parquet(py_path)
    cy_df.write_parquet(cy_path)

    result = run_comparison_prechecks(str(py_path), str(cy_path), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"

    import json
    report = json.loads((tmp_path / "precheck_results.json").read_text())
    check = next(c for c in report["checks"] if c["check"] == "opening_closing_continuity")

    assert check["break_count"] == 1
    assert check["expected_pl_reset_count"] == 0


def test_removed_ledger_not_counted_as_continuity_break(tmp_path):
    # A gl_code present in PY with a real closing balance but entirely absent from CY is a
    # removed ledger (a structural change already reported elsewhere), not a continuity
    # break -- it must not appear in break_count, expected_pl_reset_count, or
    # total_raw_break_count under either bucket.
    py_df = pl.DataFrame({
        "gl_code": ["100", "999"],
        "closing_balance": [50000.0, 75000.0],  # 999 only exists in PY
        "debit": [0.0, 0.0],
        "credit": [0.0, 0.0],
    })
    cy_df = pl.DataFrame({
        "gl_code": ["100"],
        "opening_balance": [50000.0],  # ties out exactly -- no break at all
        "bs_pl": ["BS"],
        "debit": [0.0],
        "credit": [0.0],
    })
    py_path = tmp_path / "py.parquet"
    cy_path = tmp_path / "cy.parquet"
    py_df.write_parquet(py_path)
    cy_df.write_parquet(cy_path)

    result = run_comparison_prechecks(str(py_path), str(cy_path), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"

    import json
    report = json.loads((tmp_path / "precheck_results.json").read_text())
    check = next(c for c in report["checks"] if c["check"] == "opening_closing_continuity")

    assert check["break_count"] == 0
    assert check["expected_pl_reset_count"] == 0
    assert check["total_raw_break_count"] == 0


def test_missing_bs_pl_column_degrades_to_unfiltered_breaks(tmp_path):
    # No bs_pl column at all (e.g. an older canonical TB) -- must not crash, and must not
    # silently drop real breaks just because it can't classify them.
    py_df = pl.DataFrame({"gl_code": ["100"], "closing_balance": [50000.0], "debit": [0.0], "credit": [0.0]})
    cy_df = pl.DataFrame({"gl_code": ["100"], "opening_balance": [0.0], "debit": [0.0], "credit": [0.0]})
    py_path = tmp_path / "py.parquet"
    cy_path = tmp_path / "cy.parquet"
    py_df.write_parquet(py_path)
    cy_df.write_parquet(cy_path)

    result = run_comparison_prechecks(str(py_path), str(cy_path), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"

    import json
    report = json.loads((tmp_path / "precheck_results.json").read_text())
    check = next(c for c in report["checks"] if c["check"] == "opening_closing_continuity")

    assert check["break_count"] == 1
    assert check["expected_pl_reset_count"] == 0


# TB-R18/R20: the CY/PY debit==credit control-total check must foot on signed CLOSING
# BALANCES, not raw debit/credit TURNOVER (which ties by construction and would mask a
# genuine imbalance), and a genuinely HALTED check must surface as pipeline_status=
# "HALTED"/can_continue=False rather than being downgraded to WARNING "so the pipeline
# can continue" -- the same defect fixed in validate_layer1_tb/validate_layer2_tb,
# mirrored here for the comparative precheck.

def test_cy_control_total_halts_on_closing_balance_mismatch_despite_tied_turnover(tmp_path):
    py_df = pl.DataFrame({
        "gl_code": ["1", "2"],
        "closing_balance": [50000.0, -50000.0],
        "debit": [500.0, 500.0], "credit": [500.0, 500.0],
    })
    cy_df = pl.DataFrame({
        "gl_code": ["1", "2"],
        "opening_balance": [50000.0, -50000.0],
        # Turnover ties exactly (debit == credit, both 1000), but closing balances leave
        # an 80,000 residual instead of netting to zero -- the genuine footing defect.
        "closing_balance": [100000.0, -20000.0],
        "debit": [500.0, 500.0], "credit": [500.0, 500.0],
    })
    py_path = tmp_path / "py.parquet"
    cy_path = tmp_path / "cy.parquet"
    py_df.write_parquet(py_path)
    cy_df.write_parquet(cy_path)

    result = run_comparison_prechecks(str(py_path), str(cy_path), output_dir=str(tmp_path))

    import json
    report = json.loads((tmp_path / "precheck_results.json").read_text())
    cy_check = next(c for c in report["checks"] if c["check"] == "cy_debit_credit_control_totals")
    py_check = next(c for c in report["checks"] if c["check"] == "py_debit_credit_control_totals")

    assert cy_check["status"] == "HALTED"
    assert cy_check["diff"] == 80000.0
    assert cy_check["turnover_total_debit"] == cy_check["turnover_total_credit"] == 1000.0
    assert py_check["status"] == "PASS"

    # The downgrade must be gone: a genuinely HALTED check propagates all the way to the
    # function's own return value, not just precheck_results.json.
    assert result["pipeline_status"] == "HALTED"
    assert result["can_continue"] is False
    assert "cy_debit_credit_control_totals" in result["halted_checks"]
