"""Whole-table structural re-read: for when TableFormer's own row/column GRID
is wrong, not just one cell's value -- a real Cash Flow statement came back
with several distinct line items ("Interest Income", "Finance Costs",
"Operating Profit before Working Capital Changes", "Dividend Income") jammed
into ONE label cell, paired against an unrelated value. No existing check
(compare()/verify.py) can catch this: they all reason about VALUES within a
grid that is itself broken.

Two things are tested here, deliberately kept apart:

- `assess_structural_risk` -- is this table's grid worth distrusting at all?
  Never touches the VLM; purely a judgement from the table's own shape and
  its OCR geometry. Survives as a pure reporting signal; it no longer gates
  anything (see `select_structure`, which now runs for every table).
- `select_structure` -- given an independent VLM read, is it trustworthy
  enough to REPLACE the grid? Chooses by binding BOTH structures against the
  same OCR number ledger and comparing OCR coverage, not footing strength
  (see `structure_repair.coverage_score`'s own docstring for why footing was
  replaced -- it was measured, on a real document, to reward fragmentation).
  The refusal-path tests here are the safety-critical ones: this function is
  trusted with something no earlier VLM check in this pipeline is trusted
  with (replacing structure, not just a value), so every way it could
  rubber-stamp an invented table needs its own test, not just the
  acceptance path. The deeper binding/coverage mechanics have their own file,
  `test_number_binding.py`; this file only exercises the selection contract.

Run with::

    cd ingestion
    venv/Scripts/python -m pytest tests/test_structure_reread.py -q
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Config                             # noqa: E402
from app.convert import OcrLine, TableCellGeom            # noqa: E402
from app.tables import parse_markdown_tables              # noqa: E402
from app.vlm_read import assess_structural_risk, select_structure  # noqa: E402


def test_number_binding_is_off_by_default_because_it_is_unproven():
    """A wrong structural REPLACEMENT is a worse failure than today's
    per-cell withholding -- pinned so turning this on in production is a
    deliberate act with a failing test attached, not a one-character
    default nobody notices (same discipline as SHARPEN_ENABLED and
    ORIENTATION_DETECTION_ENABLED)."""
    assert Config.NUMBER_BINDING_ENABLED is False


def _line(text, x0, y0, x1, y1, confidence=0.99):
    return OcrLine(text=text, confidence=confidence, bbox=(x0, y0, x1, y1))


def _cell(text, row, col, x0, y0, x1, y1):
    return TableCellGeom(text=text, row_start=row, row_end=row + 1,
                         col_start=col, col_end=col + 1, bbox=(x0, y0, x1, y1))


def _table(markdown: str, page_no: int = 1):
    parsed = parse_markdown_tables(markdown, page_no, prefix="x")
    assert parsed, "fixture markdown did not parse into a table"
    return parsed[0]


def _ct(cells=None, ocr_lines=None, page_no=1):
    return SimpleNamespace(cells=cells or [], ocr_lines=ocr_lines or [], page_no=page_no)


# Two column bands: label x=0-200, value x=250-350 -- shared by every
# select_structure fixture below so col_bands is never empty (an empty
# col_bands means _map_table_cols_to_bands refuses, which is its OWN,
# separately-tested behaviour in test_number_binding.py, not what these
# tests are about).
_COLS = [
    _cell("Particulars", 0, 0, 0.0, 0.0, 200.0, 18.0),
    _cell("Amount", 0, 1, 250.0, 0.0, 350.0, 18.0),
]


# ---------------------------------------------------------------------------
# assess_structural_risk
# ---------------------------------------------------------------------------

def test_a_cash_flow_style_table_with_enough_merged_rows_is_flagged():
    """The real defect verbatim: several distinct line items jammed into one
    label cell, with no ruling line for TableFormer to have split on."""
    md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Interest Income Finance Costs Operating Profit before Working "
        "Capital Changes Dividend Income | 43.62 2,780.82 |\n"
        "| Trade Receivables-(increase) | (165.42) |\n"
        "| Inventories-increase | - |\n"
        "| Trade Payables-Increase | 607.62 |\n"
    )
    table = _table(md)
    risk = assess_structural_risk(table, _ct())
    assert risk.unreliable is True
    assert any("merged line items" in r for r in risk.reasons)


def test_one_merged_row_among_many_stays_below_the_threshold():
    rows = "\n".join(f"| Line {i} | {i}.00 |" for i in range(9))
    md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| (a) First thing (b) Second thing | 1.00 2.00 |\n"
        + rows + "\n"
    )
    table = _table(md)
    risk = assess_structural_risk(table, _ct())
    assert risk.unreliable is False
    assert risk.reasons == []


def test_a_tiny_table_is_never_flagged_regardless_of_merge_ratio():
    """Below _MIN_ROWS_FOR_STRUCTURAL_RISK, a fraction is too noisy to mean
    anything -- even a table that is ENTIRELY merged-looking rows must not
    trigger a whole-table replacement over 2-3 rows."""
    md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| (a) One (b) Two | 1.00 2.00 |\n"
        "| (c) Three (d) Four | 3.00 4.00 |\n"
    )
    table = _table(md)
    risk = assess_structural_risk(table, _ct())
    assert risk.unreliable is False


def test_ocr_geometry_row_count_mismatch_flags_even_with_no_merged_rows():
    """No row looks merged by content alone -- the grid is flagged purely
    because raw OCR text clusters into a materially different number of rows
    than TableFormer's own grid claims, the same primitive
    structure_repair.repair_from_geometry already trusts to REBUILD a grid,
    reused here only as a diagnostic."""
    md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Row A | 10.00 |\n"
        "| Row B | 20.00 |\n"
        "| Row C | 30.00 |\n"
        "| Row D | 40.00 |\n"
        "| Row E | 50.00 |\n"
        "| Row F | 60.00 |\n"
    )
    table = _table(md)

    cells = [
        TableCellGeom(text="Particulars", row_start=0, row_end=1, col_start=0, col_end=1,
                      bbox=(0.0, 0.0, 200.0, 20.0)),
        TableCellGeom(text="Amount", row_start=0, row_end=1, col_start=1, col_end=2,
                      bbox=(220.0, 0.0, 400.0, 20.0)),
    ]
    # Only 4 well-separated row-bands exist in the raw OCR geometry, against
    # a 6-row grid -- exactly the shape of the real defect (TableFormer
    # merged two of the real printed rows into others).
    ocr_lines = [
        _line("Row A", 10.0, 30.0, 190.0, 48.0), _line("10.00", 230.0, 30.0, 390.0, 48.0),
        _line("Row B", 10.0, 60.0, 190.0, 78.0), _line("20.00", 230.0, 60.0, 390.0, 78.0),
        _line("Row C", 10.0, 90.0, 190.0, 108.0), _line("30.00", 230.0, 90.0, 390.0, 108.0),
        _line("Row D", 10.0, 120.0, 190.0, 138.0), _line("40.00", 230.0, 120.0, 390.0, 138.0),
    ]
    risk = assess_structural_risk(table, _ct(cells=cells, ocr_lines=ocr_lines))
    assert risk.unreliable is True
    assert any("clusters into" in r for r in risk.reasons)


# ---------------------------------------------------------------------------
# select_structure -- the selection contract (bind_table/coverage_score's own
# mechanics are tested in test_number_binding.py)
# ---------------------------------------------------------------------------

_ORIGINAL_MD = (
    "| Particulars | Amount |\n"
    "| --- | --- |\n"
    "| Revenue | 100.00 |\n"
    "| Other Income | 20.00 |\n"
    "| Total Income | 120.00 |\n"
)

# Label and value each printed as their OWN OCR detection region, at the
# same Y, in the two column bands _COLS defines -- the realistic shape
# (see test_structure_repair.py's OD_TRADE_PAYABLES_REGION_LINES), and what
# lets every value token take its line's own bbox directly rather than
# being interpolated.
_REAL_OCR_LINES = [
    _line("Particulars", 10.0, 0.0, 190.0, 18.0), _line("Amount", 260.0, 0.0, 340.0, 18.0),
    _line("Revenue", 10.0, 20.0, 190.0, 38.0), _line("100.00", 260.0, 20.0, 340.0, 38.0),
    _line("Other Income", 10.0, 40.0, 190.0, 58.0), _line("20.00", 260.0, 40.0, 340.0, 58.0),
    _line("Total Income", 10.0, 60.0, 190.0, 78.0), _line("120.00", 260.0, 60.0, 340.0, 78.0),
]


def test_a_candidate_grounded_in_the_pages_own_ocr_text_is_chosen():
    original = _table(_ORIGINAL_MD)
    chosen, binding, replaced, reason = select_structure(
        _ORIGINAL_MD, _ct(cells=_COLS, ocr_lines=_REAL_OCR_LINES), original, prefix="x",
    )
    # Identical structure on both sides -- coverage ties, and a tie keeps
    # docling (the status-quo bias). The important assertion is that a
    # genuinely well-grounded, well-covered candidate is never REFUSED
    # outright by the gates that come before coverage is even compared.
    assert chosen is not None
    assert "coverage" in reason


def test_a_reply_with_no_supporting_text_anywhere_on_the_page_is_refused():
    """An invented table: none of its labels appear anywhere in the page's
    own OCR text. Must be refused before figures ever matter."""
    original = _table(_ORIGINAL_MD)
    invented_md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Made Up Line Item | 999.00 |\n"
        "| Another Fabricated Row | 888.00 |\n"
    )
    chosen, binding, replaced, reason = select_structure(
        invented_md, _ct(cells=_COLS, ocr_lines=_REAL_OCR_LINES), original, prefix="x",
    )
    assert replaced is False
    assert chosen is original
    assert "supporting text" in reason


def test_a_split_that_accounts_for_more_figures_is_chosen_over_a_merged_original():
    """The real defect: two genuinely distinct printed lines TableFormer
    merged into one row. Coverage, unlike footing, rewards the split
    directly: the merged original's row can bind at most ONE of the two
    printed figures (its single cell holds one parsed value), so the
    other is a leftover; the split candidate binds both."""
    merged_md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Interest Income Finance Costs | 30.00 |\n"
        "| Depreciation | 40.00 |\n"
        "| Amortisation | 50.00 |\n"
    )
    split_md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Interest Income | 10.00 |\n"
        "| Finance Costs | 30.00 |\n"
        "| Depreciation | 40.00 |\n"
        "| Amortisation | 50.00 |\n"
    )
    ocr_lines = [
        _line("Particulars", 10.0, 0.0, 190.0, 18.0), _line("Amount", 260.0, 0.0, 340.0, 18.0),
        _line("Interest Income", 10.0, 20.0, 190.0, 38.0), _line("10.00", 260.0, 20.0, 340.0, 38.0),
        _line("Finance Costs", 10.0, 40.0, 190.0, 58.0), _line("30.00", 260.0, 40.0, 340.0, 58.0),
        _line("Depreciation", 10.0, 60.0, 190.0, 78.0), _line("40.00", 260.0, 60.0, 340.0, 78.0),
        _line("Amortisation", 10.0, 80.0, 190.0, 98.0), _line("50.00", 260.0, 80.0, 340.0, 98.0),
    ]
    original = _table(merged_md)
    chosen, binding, replaced, reason = select_structure(
        split_md, _ct(cells=_COLS, ocr_lines=ocr_lines), original, prefix="x",
    )
    assert replaced is True
    assert chosen is not original
    assert chosen.cell(0, 1).value == 10.00
    assert chosen.cell(1, 1).value == 30.00


def test_a_single_row_reply_is_refused():
    original = _table(_ORIGINAL_MD)
    one_row_md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Revenue | 100.00 |\n"
    )
    chosen, binding, replaced, reason = select_structure(
        one_row_md, _ct(cells=_COLS, ocr_lines=_REAL_OCR_LINES), original, prefix="x",
    )
    assert replaced is False
    assert chosen is original
    assert "too few rows" in reason


def test_an_unparseable_reply_is_refused():
    original = _table(_ORIGINAL_MD)
    chosen, binding, replaced, reason = select_structure(
        "I could not read this table clearly.", _ct(cells=_COLS, ocr_lines=_REAL_OCR_LINES),
        original, prefix="x",
    )
    assert replaced is False
    assert chosen is original
    assert "did not contain a parseable table" in reason


def test_no_independent_read_at_all_is_refused_not_crashed():
    original = _table(_ORIGINAL_MD)
    chosen, binding, replaced, reason = select_structure(
        "", _ct(cells=_COLS, ocr_lines=_REAL_OCR_LINES), original, prefix="x",
    )
    assert replaced is False
    assert chosen is original
    assert "no independent read" in reason


def test_no_ocr_text_available_at_all_is_refused():
    """No `ocr_lines` at all means there is nothing to check labels against
    -- must refuse rather than accept on the strength of nothing. With no
    OCR lines, docling's OWN binding also has nothing to bind against, so
    this exercises the base-binding-refused path directly."""
    original = _table(_ORIGINAL_MD)
    chosen, binding, replaced, reason = select_structure(
        _ORIGINAL_MD, _ct(cells=_COLS, ocr_lines=[]), original, prefix="x",
    )
    assert replaced is False
    assert chosen is original


def test_a_refusal_never_mutates_the_original_table():
    """The 'never worse than status quo' guarantee: after ANY refusal, the
    table that would otherwise be kept is untouched."""
    original = _table(_ORIGINAL_MD)
    before = original.to_markdown()
    select_structure(
        "nonsense", _ct(cells=_COLS, ocr_lines=_REAL_OCR_LINES), original, prefix="x",
    )
    assert original.to_markdown() == before
