"""Wave 2 Fix 5: run_comparison_sign_check's anchor-keyword screen (scored per-period,
PY and CY separately) computes a genuinely different percentage than canonical.py's
TB-000 (same mechanism, single-TB scope) and _shared.py's sign_convention_stats
(population-wide, report_head-membership basis) -- every message/payload must say
which basis it used so a reader never mistakes one screen's number for the only one."""

import json

import polars as pl

from modes.trial_balance.pipeline.tools import run_comparison_sign_check


def _make_df(gl_names, balances):
    return pl.DataFrame({"gl_name": gl_names, "closing_balance": balances})


def test_flags_carry_basis_label(tmp_path):
    py_df = _make_df(
        ["Cash", "Bank Account", "Trade Receivable", "Trade Payable", "Share Capital", "Sales Revenue"],
        [1500.0, 2500.0, 3200.0, -1500.0, -5000.0, -9000.0],
    )
    cy_df = _make_df(
        ["Cash", "Bank Account", "Trade Receivable", "Trade Payable", "Share Capital", "Sales Revenue"],
        [1600.0, 2600.0, 3300.0, -1600.0, -5100.0, -9100.0],
    )
    py_path = tmp_path / "py.parquet"
    cy_path = tmp_path / "cy.parquet"
    py_df.write_parquet(py_path)
    cy_df.write_parquet(cy_path)

    result = run_comparison_sign_check(str(py_path), str(cy_path), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"

    flags = json.loads((tmp_path / "sign_convention_flags.json").read_text())["flags"]
    assert len(flags) == 2
    for flag in flags:
        assert flag["basis"] == "anchor-keyword screen (per-period basis, PY and CY scored separately)"
        assert "anchor-keyword screen (per-period basis" in flag["message"]


def test_too_few_anchors_message_also_carries_basis(tmp_path):
    py_df = _make_df(["Misc A", "Misc B"], [100.0, -50.0])
    cy_df = _make_df(["Misc A", "Misc B"], [100.0, -50.0])
    py_path = tmp_path / "py.parquet"
    cy_path = tmp_path / "cy.parquet"
    py_df.write_parquet(py_path)
    cy_df.write_parquet(cy_path)

    result = run_comparison_sign_check(str(py_path), str(cy_path), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"

    flags = json.loads((tmp_path / "sign_convention_flags.json").read_text())["flags"]
    assert all(f["basis"] == "anchor-keyword screen (per-period basis, PY and CY scored separately)" for f in flags)
