"""Splitting rows TableFormer merged across an unruled Schedule III boundary.

Every fixture here is transcribed verbatim from the ACTUAL docling output on
data/OD-SPSU-SO-032/2022-23/..._SFS_....PDF (captured by running the real
ingestion pipeline against that document and reading the raw table markdown
before verification), not invented. That document's Schedule III line items
are printed with no ruling line between consecutive items inside a category --
only the category's own outer box -- and TableFormer relies on ruling to find
row boundaries, so two or more such items land in one detected row.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.tables import parse_markdown_tables               # noqa: E402


def _table(md: str):
    return parse_markdown_tables(md, 1)[0]


# --------------------------------------------------------------------------
# The safe case: one populated segment, one or more genuinely blank ones
# --------------------------------------------------------------------------

def test_a_blank_item_merged_with_a_populated_one_splits_safely():
    """'(a) Short-term borrowings (b) Trade payables' merged into one row,
    carrying ONE note number and ONE value -- the real filing's Short-term
    borrowings line is blank that year; Trade payables is Note 4, 34,011.00.
    The blank label must end up nil, not silently dropped and not guessed at."""
    md = (
        "| Particulars | Note No. | Figures as at 31st March, 2023 | Figures as at 31st March, 2022 |\n"
        "| --- | --- | --- | --- |\n"
        "| (a) Short-term borrowings (b) Trade payables | 4 | 34,011.00 |  |\n"
    )
    table = _table(md)
    assert len(table.rows) == 2
    assert table.label(0) == "(a) Short-term borrowings"
    assert table.rows[0][1:] == ["", "", ""]  # every non-label cell nil
    assert table.label(1) == "(b) Trade payables"
    assert table.cell(1, 1).value == 4
    assert table.cell(1, 2).value == 34011.0


def test_a_pure_subheading_merged_with_its_data_row_splits_safely():
    """'Type of Share: Equity Shares @ Rs.10/- per Share' carries no figure of
    its own; the data belongs to 'Held by Hon'ble Governor of Odisha (100%)'
    printed directly under it with no rule between them. This shape has no
    enumerator marker at all and is NOT expected to split -- pinning that this
    function only acts on the enumerator-marked pattern it can prove, not on
    every visually-merged row."""
    md = (
        "| Name of Shareholders | No. of Shares | Amount |\n"
        "| --- | --- | --- |\n"
        "| Type of Share: Equity Shares Held by Hon'ble Governor of Odisha (100%) | 1,50,000 | 15,00,000 |\n"
    )
    table = _table(md)
    assert len(table.rows) == 1  # unchanged: no enumerator marker to split on


def test_three_way_merge_all_blank_splits_into_three_nil_rows():
    """'(c) Long-term loans and advances (d) Other non-current assets (2)
    Current assets' -- three Schedule III line items, none populated, one of
    them a SECTION HEADING that got swallowed into the row text. Splitting
    this out also means '(2) Current assets' becomes its own row label, which
    is what the section-boundary regexes in tools_fs.py search for."""
    md = (
        "| Particulars | Note No. | col3 | col4 |\n"
        "| --- | --- | --- | --- |\n"
        "| (c) Long-term loans and advances (d) Other non-current assets (2) Current assets |  |  |  |\n"
    )
    table = _table(md)
    assert len(table.rows) == 3
    assert table.label(0) == "(c) Long-term loans and advances"
    assert table.label(1) == "(d) Other non-current assets"
    assert table.label(2) == "(2) Current assets"
    for r in range(3):
        assert table.cell(r, 1).value is None
        assert table.cell(r, 2).value is None


def test_roman_numeral_markers_split_too():
    md = (
        "| Particulars | Note No. | col3 | col4 |\n"
        "| --- | --- | --- | --- |\n"
        "| (i) Property, Plant and Equipment (ii) Intangible assets: (work-in-progress) |  |  |  |\n"
    )
    table = _table(md)
    assert len(table.rows) == 2
    assert table.label(0) == "(i) Property, Plant and Equipment"
    assert "Intangible assets" in table.label(1)


# --------------------------------------------------------------------------
# The refusal: never guess how several real numbers divide across labels
# --------------------------------------------------------------------------

def test_a_value_cell_with_two_numbers_refuses_to_split():
    """If a value cell holds more than one whitespace-separated token, more
    than one real figure may have been merged into it too -- there is no safe
    way to know which label each belongs to, so the row is left exactly as it
    was, for verify.py to withhold downstream the way it already does."""
    md = (
        "| Particulars | Note No. | col3 | col4 |\n"
        "| --- | --- | --- | --- |\n"
        "| (c) Other current liabilities (d) Short-term provisions | 5 | 22,69,36,581.10 5,11,11,345.00 |  |\n"
    )
    table = _table(md)
    assert len(table.rows) == 1
    assert table.label(0) == "(c) Other current liabilities (d) Short-term provisions"


def test_a_grouping_odd_value_refuses_to_split():
    md = (
        "| Particulars | Note No. | col3 | col4 |\n"
        "| --- | --- | --- | --- |\n"
        "| (a) Item one (b) Item two | 1 | 12,345,67 |  |\n"
    )
    table = _table(md)
    assert len(table.rows) == 1


# --------------------------------------------------------------------------
# Rows that must NOT be touched at all
# --------------------------------------------------------------------------

def test_a_single_marker_is_not_a_merge():
    md = (
        "| Particulars | Note No. | col3 | col4 |\n"
        "| --- | --- | --- | --- |\n"
        "| (a) Share capital | 1 | 15,00,000.00 | 15,00,000.00 |\n"
    )
    table = _table(md)
    assert len(table.rows) == 1
    assert table.label(0) == "(a) Share capital"


def test_no_marker_at_all_is_untouched():
    md = (
        "| Particulars | Note No. | col3 | col4 |\n"
        "| --- | --- | --- | --- |\n"
        "| Total | 1 | 43,34,97,783.82 | 14,99,540.00 |\n"
    )
    table = _table(md)
    assert len(table.rows) == 1


def test_a_percentage_in_parentheses_is_not_mistaken_for_a_marker():
    """'(100%)' must never match the enumerator pattern -- three digits, not
    the 1-2 the marker allows -- or an ordinary shareholding disclosure with
    only one real marker would be treated as a two-way merge."""
    md = (
        "| Particulars | Note No. | col3 | col4 |\n"
        "| --- | --- | --- | --- |\n"
        "| (a) Held by Hon'ble Governor of Odisha (100%) | 1 | 15,00,000 |  |\n"
    )
    table = _table(md)
    assert len(table.rows) == 1


# --------------------------------------------------------------------------
# The Schedule III column-reference row -- "1 | 2 | 3 | 4" is not a line item
#
# Real docling output, OD-SPSU-SO-032 2023-24 SFS page 1: this row caused a
# spurious readers_disagree once a genuinely independent second reader
# stopped inventing a matching row for it, AND fed fake leaf values (3, 4)
# into verify._find_footings's subtotal search on every filing that prints
# this convention -- most Schedule III documents.
# --------------------------------------------------------------------------

def test_the_column_reference_row_is_dropped_from_the_table():
    md = (
        "| Particulars | Note No. | Figures as at 31st March, 2024 | Figures as at 31st March, 2023 |\n"
        "| --- | --- | --- | --- |\n"
        "| 1 | 2 | 3 | 4 |\n"
        "| (a) Share capital | 1 | 15,00,000.00 | 15,00,000.00 |\n"
    )
    table = _table(md)
    assert len(table.rows) == 1
    assert table.label(0) == "(a) Share capital"


def test_a_row_that_is_only_partly_sequential_is_kept():
    """Two columns matching 1,2 by coincidence is not enough to discard a row
    -- real money can start "1, 2" far more plausibly than "1, 2, 3, 4"."""
    md = (
        "| Particulars | Note No. | Amount |\n"
        "| --- | --- | --- |\n"
        "| Some Reserve | 1 | 2 |\n"
    )
    table = _table(md)
    assert len(table.rows) == 1


def test_a_real_row_of_actual_money_is_never_mistaken_for_it():
    md = (
        "| Particulars | Note No. | 2024 | 2023 |\n"
        "| --- | --- | --- | --- |\n"
        "| Total | | 1 | 2,00,000 |\n"
    )
    table = _table(md)
    assert len(table.rows) == 1, "must not discard a real row over one small value"


def test_the_row_must_start_at_one_and_be_consecutive():
    md = (
        "| Particulars | Note No. | A | B |\n"
        "| --- | --- | --- | --- |\n"
        "| Item | 2 | 4 | 6 |\n"
    )
    table = _table(md)
    assert len(table.rows) == 1, "2,4,6 is real data, not a column-index row"
