"""Splitting a row TableFormer merged from SEVERAL real line items via a
band-level second read -- a different defect from a single unreadable cell
(see test_rescue.py). The trigger detection, the multi-line crop, and the
self-consistency guard that stands in for a whole-table Alignment.

Run with::

    cd ingestion
    venv/Scripts/python -m pytest tests/test_band_rescue.py -q
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.convert import OcrLine, TableCellGeom  # noqa: E402
from app.tables import parse_markdown_tables  # noqa: E402
from app.vlm_read import (  # noqa: E402
    _looks_merged,
    multi_row_band,
    parse_band_rows,
    split_merged_rows,
)


# ---------------------------------------------------------------------------
# _looks_merged: the trigger
# ---------------------------------------------------------------------------

def test_two_enumerator_markers_in_one_label_looks_merged():
    assert _looks_merged("(a) Short-term borrowings (b) Trade payables", ["34,011.00"]) is True


def test_a_single_marker_does_not_look_merged():
    assert _looks_merged("(a) Share capital", ["15,00,000.00"]) is False


def test_two_numeric_tokens_in_one_value_cell_looks_merged_even_with_no_markers():
    """The un-enumerated case: a cash-flow line with no (a)/(b) markers at
    all, but a value cell that itself carries two real figures run together."""
    assert _looks_merged(
        "Interest Income Finance Costs Operating Profit before Working Capital Changes Dividend Income",
        ["43.62 2,780.82"],
    ) is True


def test_an_ordinary_row_does_not_look_merged():
    assert _looks_merged("Finance costs", ["6,276.63", "460.00"]) is False


# ---------------------------------------------------------------------------
# multi_row_band: finding the right multi-line span, without a Y-flip
# ---------------------------------------------------------------------------

def _converted_table_fixture():
    """Four printed lines that a merged-label row concatenates: 'Interest
    Income', 'Finance Costs', 'Operating Profit', 'Dividend Income'. All near
    the TOP of a 1000px-tall page; a Y-flipped implementation would instead
    read near the bottom, marked with a distinct value so a flip is caught
    even if the crop's shape alone would not reveal it.
    """
    header_cell = TableCellGeom(text="Particulars", row_start=0, row_end=1, col_start=0, col_end=1,
                                 bbox=(50.0, 10.0, 750.0, 30.0))
    ocr_lines = [
        OcrLine(text="Particulars", confidence=0.99, bbox=(50.0, 10.0, 400.0, 28.0)),
        OcrLine(text="Interest Income", confidence=0.95, bbox=(50.0, 100.0, 400.0, 118.0)),
        OcrLine(text="Finance Costs", confidence=0.95, bbox=(50.0, 120.0, 400.0, 138.0)),
        OcrLine(text="Operating Profit", confidence=0.95, bbox=(50.0, 140.0, 400.0, 158.0)),
        OcrLine(text="Dividend Income", confidence=0.95, bbox=(50.0, 160.0, 400.0, 178.0)),
    ]
    return SimpleNamespace(cells=[header_cell], ocr_lines=ocr_lines, page_height_pt=500.0)


def _marked_page_image():
    page_image = np.full((1000, 800), 255, dtype=np.uint8)
    page_image[0:60, 40:760] = 40                    # header band (correct, top)
    page_image[192:365, 40:760] = 80                  # the four-line band (correct, top)
    page_image[748:816, 40:760] = 160                  # where a Y-flip would look instead
    return page_image


def test_multi_row_band_finds_the_full_four_line_span():
    converted = _converted_table_fixture()
    page_image = _marked_page_image()
    merged_label = "Interest Income Finance Costs Operating Profit Dividend Income"

    image = multi_row_band(page_image, converted, merged_label, scale=1.0)
    assert image is not None
    assert 40 in image or 80 in image
    # Must not contain the marker that only appears where a Y-flipped
    # implementation would have looked instead.
    assert 160 not in image


def test_multi_row_band_returns_none_without_geometry():
    converted = SimpleNamespace(cells=[], ocr_lines=[], page_height_pt=None)
    page_image = _marked_page_image()
    assert multi_row_band(page_image, converted, "Interest Income Finance Costs") is None


def test_multi_row_band_returns_none_when_nothing_resembles_the_label():
    converted = _converted_table_fixture()
    page_image = _marked_page_image()
    assert multi_row_band(page_image, converted, "Something Entirely Unrelated To Any Line") is None


# ---------------------------------------------------------------------------
# parse_band_rows: every data row, not just the last
# ---------------------------------------------------------------------------

def test_parse_band_rows_returns_every_data_row():
    reply = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Interest Income | 43.62 |\n"
        "| Finance Costs | (6,276.63) |\n"
        "| Dividend Income | 2,780.82 |\n"
    )
    rows = parse_band_rows(reply)
    assert rows == [
        ["Interest Income", "43.62"],
        ["Finance Costs", "(6,276.63)"],
        ["Dividend Income", "2,780.82"],
    ]


def test_parse_band_rows_drops_a_header_echo():
    """A band reply may re-print the header strip as its own first row --
    `_body_rows` (shared with the whole-table pass) must drop it the same way."""
    reply = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Particulars | Amount |\n"
        "| Interest Income | 43.62 |\n"
    )
    rows = parse_band_rows(reply)
    assert rows == [["Interest Income", "43.62"]]


# ---------------------------------------------------------------------------
# split_merged_rows: the structural repair, end to end
# ---------------------------------------------------------------------------

def _merged_table():
    md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Finance costs | 460.00 |\n"
        "| Interest Income Finance Costs Operating Profit Dividend Income | 43.62 2780.82 |\n"
        "| Total | 3284.44 |\n"
    )
    return parse_markdown_tables(md, 1)[0]


def test_a_confirmed_split_replaces_one_row_with_several():
    table = _merged_table()
    converted = _converted_table_fixture()
    page_image = _marked_page_image()

    with patch("app.vlm_read.multi_row_band", return_value=np.zeros((10, 10), dtype=np.uint8)), \
         patch("app.vlm_read.transcribe_band", return_value=(
             "| Particulars | Amount |\n"
             "| --- | --- |\n"
             "| Interest Income | 43.62 |\n"
             "| Finance Costs | ILLEGIBLE |\n"
             "| Operating Profit | ILLEGIBLE |\n"
             "| Dividend Income | 2,780.82 |\n"
         )):
        new_rows, remap, notes, attempted = split_merged_rows(table, converted, page_image)

    assert len(new_rows) == 4
    assert notes
    # The merged row (old index 1) is gone; "Finance costs" (0) and "Total"
    # (2) survive, remapped.
    assert table.label(remap[0]) == "Finance costs"
    assert table.label(remap[2]) == "Total"
    labels = {table.label(r) for r in new_rows}
    assert labels == {"Interest Income", "Finance Costs", "Operating Profit", "Dividend Income"}
    assert 1 not in remap  # the old merged row has no single new identity


def test_max_attempts_of_zero_leaves_the_table_untouched_and_reports_zero_attempted():
    """The per-document budget cap: a caller passing max_attempts=0 (budget
    already spent by other tables) must get the table back completely
    unchanged, with `attempted` confirming no call was made -- so pipeline.py
    can decrement a shared running total accurately without over-spending it."""
    table = _merged_table()
    converted = _converted_table_fixture()
    page_image = _marked_page_image()
    before = [list(r) for r in table.rows]

    with patch("app.vlm_read.transcribe_band") as mock_transcribe:
        new_rows, remap, notes, attempted = split_merged_rows(
            table, converted, page_image, max_attempts=0,
        )

    mock_transcribe.assert_not_called()
    assert attempted == 0
    assert new_rows == set()
    assert notes == []
    assert table.rows == before


def test_a_single_row_reply_is_not_treated_as_a_split():
    """A band read that comes back with only ONE row is not a split -- the
    model did not find multiple line items either, so the row is left
    exactly as it was for the ordinary withhold path to handle."""
    table = _merged_table()
    converted = _converted_table_fixture()
    page_image = _marked_page_image()
    before = [list(r) for r in table.rows]

    with patch("app.vlm_read.multi_row_band", return_value=np.zeros((10, 10), dtype=np.uint8)), \
         patch("app.vlm_read.transcribe_band", return_value=(
             "| Particulars | Amount |\n| --- | --- |\n| Something | 1.00 |\n"
         )):
        new_rows, remap, notes, attempted = split_merged_rows(table, converted, page_image)

    assert new_rows == set()
    assert notes == []
    assert table.rows == before


def test_a_split_whose_labels_do_not_resemble_the_original_is_refused():
    """Proof of sight: the returned rows' concatenated labels must still
    resemble the ORIGINAL merged label. A read of some unrelated region must
    not be accepted just because it returned multiple rows."""
    table = _merged_table()
    converted = _converted_table_fixture()
    page_image = _marked_page_image()
    before = [list(r) for r in table.rows]

    with patch("app.vlm_read.multi_row_band", return_value=np.zeros((10, 10), dtype=np.uint8)), \
         patch("app.vlm_read.transcribe_band", return_value=(
             "| Particulars | Amount |\n| --- | --- |\n"
             "| Completely Unrelated Heading | 1.00 |\n"
             "| Another Unrelated Row | 2.00 |\n"
         )):
        new_rows, remap, notes, attempted = split_merged_rows(table, converted, page_image)

    assert new_rows == set()
    assert notes == []
    assert table.rows == before


def test_no_geometry_or_no_page_image_leaves_the_table_untouched():
    table = _merged_table()
    converted = SimpleNamespace(cells=[], ocr_lines=[], page_height_pt=None)
    new_rows, remap, notes, attempted = split_merged_rows(table, converted, None)
    assert new_rows == set()
    assert notes == []


def test_an_ordinary_document_with_no_merged_rows_is_untouched():
    md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Share capital | 15,00,000.00 |\n"
        "| Reserves | 4,00,000.00 |\n"
    )
    table = parse_markdown_tables(md, 1)[0]
    converted = _converted_table_fixture()
    page_image = _marked_page_image()
    new_rows, remap, notes, attempted = split_merged_rows(table, converted, page_image)
    assert new_rows == set()
    assert notes == []
    assert remap == {0: 0, 1: 1}
