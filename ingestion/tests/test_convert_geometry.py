"""The geometry docling computes must survive conversion, in one coordinate space.

Every structural MISSING figure in the accuracy baseline is a table whose ROW
GRID is wrong while its OCR text is right -- four printed labels sharing one
cell, one label wrapped across two rows. That is visible only in coordinates,
and `convert.py` used to export tables to markdown and let the docling result
fall out of scope, so every coordinate died there.

These tests use plain stand-in objects rather than docling itself: the test
venv does not carry docling, and the extraction is written to depend only on
the attribute names verified against docling-slim 2.123.1 (`TableCell.bbox` /
`start_row_offset_idx`..., `TextCell.to_bounding_box()` / `confidence`,
`SegmentedPdfPage.get_cells_in_bbox`).

Run with::

    cd ingestion
    venv/Scripts/python -m pytest tests/test_convert_geometry.py -q
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import convert  # noqa: E402

TOP = NS(name="TOPLEFT")
BOTTOM = NS(name="BOTTOMLEFT")
PAGE_HEIGHT = 842.0  # A4 in points


def _box(l, t, r, b, origin=TOP):
    return NS(l=l, t=t, r=r, b=b, coord_origin=origin)


def _cell(text, row, col, box):
    return NS(text=text, start_row_offset_idx=row, end_row_offset_idx=row + 1,
              start_col_offset_idx=col, end_col_offset_idx=col + 1, bbox=box)


class _Line:
    def __init__(self, text, confidence, box):
        self.text, self.confidence, self._box = text, confidence, box

    def to_bounding_box(self):
        return self._box


class _ParsedPage:
    def __init__(self, lines, raises=False):
        self.lines, self.raises, self.calls = lines, raises, []

    def get_cells_in_bbox(self, unit, bbox, ios=0.8):
        self.calls.append((unit, bbox, ios))
        if self.raises:
            raise RuntimeError("docling internals changed")
        return self.lines


# The SK-SPSU-SSLSA-010 shape: four printed labels in ONE TableFormer cell,
# while RapidOCR saw them as four separate lines at four heights.
SK_LABEL_BLOCK = _box(40.0, 100.0, 300.0, 160.0)
SK_CELLS = [
    _cell("Corpus/Capital Fund Reserve and Surplus Earmarked/Endownment Funds "
          "Secured Loans and Borrowings", 0, 0, SK_LABEL_BLOCK),
    _cell("1,51,85,562.98", 0, 2, _box(400.0, 100.0, 480.0, 112.0)),
    _cell("2,469.00", 1, 2, _box(400.0, 124.0, 480.0, 136.0)),
]
SK_LINES = [
    _Line("Corpus/Capital Fund", 0.98, _box(40.0, 100.0, 180.0, 112.0)),
    _Line("Reserve and Surplus", 0.97, _box(40.0, 112.0, 180.0, 124.0)),
    _Line("Earmarked/Endownment Funds", 0.95, _box(40.0, 124.0, 230.0, 136.0)),
    _Line("Secured Loans and Borrowings", 0.96, _box(40.0, 136.0, 240.0, 148.0)),
]


def _table_item(cells, table_box=None):
    return NS(data=NS(table_cells=cells),
              prov=[NS(page_no=1, bbox=table_box or _box(30.0, 812.0, 560.0, 600.0, BOTTOM))])


# --------------------------------------------------------------------------
# One coordinate space
# --------------------------------------------------------------------------

def test_a_topleft_box_passes_through_normalised():
    assert convert._topleft_box(10, 20, 30, 40, TOP, PAGE_HEIGHT) == (10.0, 20.0, 30.0, 40.0)


def test_a_bottomleft_box_is_flipped_into_topleft():
    """Docling's table provenance is BOTTOMLEFT: t=812 means 30pt from the top."""
    assert convert._topleft_box(30, 812, 560, 600, BOTTOM, PAGE_HEIGHT) == (30.0, 30.0, 560.0, 242.0)


def test_a_bottomleft_box_without_a_page_height_is_refused_not_guessed():
    """A box in the wrong space looks exactly like a real one -- which is how
    vlm_read.crop's own coordinate bug stayed invisible. Refuse instead."""
    assert convert._topleft_box(30, 812, 560, 600, BOTTOM, None) is None


def test_inverted_edges_are_normalised():
    assert convert._topleft_box(30, 40, 10, 20, TOP, PAGE_HEIGHT) == (10.0, 20.0, 30.0, 40.0)


# --------------------------------------------------------------------------
# What a table carries out of conversion
# --------------------------------------------------------------------------

def test_the_merged_cell_and_the_separate_ocr_lines_both_survive():
    """THE point: one cell holding four labels, and four OCR lines at four
    heights. Both are needed to see the merge -- neither alone shows it."""
    page = _ParsedPage(SK_LINES)
    cells, lines, height = convert._table_geometry(
        _table_item(SK_CELLS), 1, {1: (page, PAGE_HEIGHT)})

    assert height == PAGE_HEIGHT
    assert len(cells) == 3
    assert cells[0].row_start == 0 and cells[0].col_start == 0
    assert "Earmarked/Endownment Funds" in cells[0].text
    assert cells[0].bbox == (40.0, 100.0, 300.0, 160.0)

    assert [l.text for l in lines] == [
        "Corpus/Capital Fund", "Reserve and Surplus",
        "Earmarked/Endownment Funds", "Secured Loans and Borrowings",
    ]
    assert [l.bbox[1] for l in lines] == [100.0, 112.0, 124.0, 136.0], "one height per printed line"
    assert lines[2].confidence == pytest.approx(0.95)


def test_ocr_lines_are_looked_up_within_the_tables_own_box_at_line_granularity():
    page = _ParsedPage(SK_LINES)
    item = _table_item(SK_CELLS)
    convert._table_geometry(item, 1, {1: (page, PAGE_HEIGHT)})

    unit, bbox, ios = page.calls[0]
    assert bbox is item.prov[0].bbox
    assert "LINE" in str(unit)
    assert ios == 0.8


def test_a_cell_in_bottomleft_space_is_kept_even_when_its_box_cannot_be_flipped():
    cells, _, _ = convert._table_geometry(
        _table_item([_cell("Total", 3, 0, _box(40, 700, 90, 690, BOTTOM))]), 1, {})

    assert len(cells) == 1
    assert cells[0].text == "Total"
    assert cells[0].bbox is None


def test_docling_internals_changing_degrades_to_no_lines_not_a_failed_conversion():
    cells, lines, _ = convert._table_geometry(
        _table_item(SK_CELLS), 1, {1: (_ParsedPage(SK_LINES, raises=True), PAGE_HEIGHT)})

    assert lines == []
    assert len(cells) == 3, "a lines failure must not also cost the cell grid"


def test_a_table_on_a_page_with_no_parsed_geometry_still_carries_its_cells():
    cells, lines, height = convert._table_geometry(_table_item(SK_CELLS), 1, {})

    assert len(cells) == 3
    assert lines == []
    assert height is None


def test_a_table_with_no_data_carries_nothing_and_does_not_raise():
    cells, lines, _ = convert._table_geometry(NS(data=None, prov=[]), 1, {})
    assert cells == [] and lines == []


# --------------------------------------------------------------------------
# Page geometry, and the whole path through _extract_tables
# --------------------------------------------------------------------------

def test_page_height_comes_from_the_page_size():
    parsed = _ParsedPage([])
    result = NS(pages=[NS(page_no=1, size=NS(height=842.0), parsed_page=parsed)])

    assert convert._page_geometry(result) == {1: (parsed, 842.0)}


def test_page_height_falls_back_to_the_parsed_page_dimension():
    parsed = _ParsedPage([])
    parsed.dimension = NS(height=595.0)
    result = NS(pages=[NS(page_no=2, size=None, parsed_page=parsed)])

    assert convert._page_geometry(result)[2] == (parsed, 595.0)


def test_extract_tables_attaches_geometry_to_each_table():
    parsed = _ParsedPage(SK_LINES)
    item = _table_item(SK_CELLS)
    item.export_to_markdown = lambda document=None: "| a | b |\n| --- | --- |\n| 1 | 2 |"
    item.caption_text = lambda document: "Balance Sheet"
    document = NS(tables=[item])

    tables = convert._extract_tables(document, {1: (parsed, PAGE_HEIGHT)})

    assert len(tables) == 1
    assert len(tables[0].cells) == 3
    assert len(tables[0].ocr_lines) == 4
    assert tables[0].page_height_pt == PAGE_HEIGHT


def test_extract_tables_without_geometry_behaves_exactly_as_before():
    """Backwards compatible: the old single-argument call still works, and the
    markdown/title/bbox it always produced are unchanged."""
    item = _table_item(SK_CELLS)
    item.export_to_markdown = lambda document=None: "| a | b |\n| --- | --- |\n| 1 | 2 |"
    item.caption_text = lambda document: "Balance Sheet"

    tables = convert._extract_tables(NS(tables=[item]))

    assert tables[0].markdown.startswith("| a | b |")
    assert tables[0].title == "Balance Sheet"
    assert tables[0].bbox == [30.0, 812.0, 560.0, 600.0]
    assert tables[0].ocr_lines == []
