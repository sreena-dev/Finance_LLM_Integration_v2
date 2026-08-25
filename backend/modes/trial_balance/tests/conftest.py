"""Shared pytest fixtures for the TB-v2 backend test suite.

Layout mirrors backend/tools/<package>/ one-for-one (see tests/tools/*). This
file provides the fixture-loading helpers every test module needs: a small
synthetic TB workbook builder, a canonical-TB Parquet builder, and a
DB-availability guard so tests that touch the real Postgres tables (LIVE
staging writes) skip cleanly rather than fail when no DB is reachable.
"""

import sys
from pathlib import Path

import openpyxl
import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modes.trial_balance.pipeline.tools import CANONICAL_TB_ALL_COLUMNS  # noqa: E402


@pytest.fixture
def make_tb_workbook(tmp_path):
    """Factory fixture: build a small Trial Balance .xlsx with a header row
    and given data rows. Returns the file path. `rows` is a list of
    (gl_code, gl_name, opening, debit, credit, closing) tuples. `formula_cell`
    optionally overwrites one cell (e.g. "F9") with a formula string, for
    Layer-4 formula-detection tests."""

    def _make(rows, formula_cell=None, formula_text=None, filename="tb.xlsx"):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "TB"
        ws.append(["GL Code", "GL Name", "Opening Balance", "Debit", "Credit", "Closing Balance"])
        for r in rows:
            ws.append(list(r))
        if formula_cell and formula_text:
            ws[formula_cell] = formula_text
        path = tmp_path / filename
        wb.save(path)
        return path

    return _make


# A default set of debit-normal/credit-normal anchor rows, balanced (debit
# total == credit total) so TB-005/009/010/011 all PASS -- tests that only
# care about TB-000/Layer-4/TB-025/026/029 can use this without also
# tripping unrelated arithmetic rules.
BALANCED_ANCHOR_ROWS = [
    ("1001", "Cash in Hand", 1000, 500, 0, 1500),
    ("1002", "Bank Account", 2000, 1000, 500, 2500),
    ("1003", "Trade Receivable", 3000, 200, 0, 3200),
    ("1004", "Inventory Stock", 4000, 0, 100, 3900),
    ("2001", "Trade Payable", -1000, 0, 500, -1500),
    ("2002", "Share Capital", -5000, 0, 0, -5000),
    ("2003", "Sales Revenue", 0, 0, 9000, -9000),
    ("2004", "General Reserve", -2000, 300, 0, -2300),
]


@pytest.fixture
def make_canonical_tb(tmp_path):
    """Factory fixture: build a canonical_tb.parquet with exactly
    CANONICAL_TB_ALL_COLUMNS. `rows` is a list of dicts with at least
    gl_code/gl_name/closing_balance; any column not supplied defaults to
    None (or 0.0 for the four numeric columns)."""

    def _make(rows, tb_doc_id="TEST_DOC", filename="canonical_tb.parquet"):
        numeric_cols = {"opening_balance", "debit", "credit", "closing_balance"}
        full_rows = []
        for r in rows:
            row = {c: r.get(c, 0.0 if c in numeric_cols else None) for c in CANONICAL_TB_ALL_COLUMNS}
            row["tb_doc_id"] = r.get("tb_doc_id", tb_doc_id)
            full_rows.append(row)
        df = pl.DataFrame(full_rows, schema={c: (pl.Float64 if c in numeric_cols else pl.Utf8) for c in CANONICAL_TB_ALL_COLUMNS})
        path = tmp_path / filename
        df.write_parquet(path)
        return path

    return _make


def _db_reachable() -> bool:
    try:
        from modes.trial_balance.pipeline.db import db_cursor

        with db_cursor() as cur:
            cur.execute("SELECT 1")
        return True
    except Exception:
        return False


@pytest.fixture
def balanced_anchor_rows():
    """Copy of BALANCED_ANCHOR_ROWS as a fixture, so test modules don't need
    a cross-module import of tests.conftest (fragile without tests/ being a
    real package) -- just request this fixture instead."""
    return list(BALANCED_ANCHOR_ROWS)


@pytest.fixture(scope="session")
def db_available():
    """True if the real Postgres DB (settings.DB_*) is reachable. Tests that
    need it should `pytest.skip(...)` early when this is False, rather than
    failing -- the DB is an external dependency, not something every dev/CI
    box is guaranteed to have running."""
    return _db_reachable()


# ── Phase-2 screen fixtures ───────────────────────────────────────────────────
# Defined in tests/conftest_phase2.py and re-exported here so pytest registers them
# globally. Kept in their own module so the Phase-2 additions stay reviewable as a
# unit rather than diffused through this file.
from modes.trial_balance.tests.conftest_phase2 import (  # noqa: E402,F401
    SCREEN_ROWS,
    minimal_tb,
    phase2_run,
    write_canonical,
)
