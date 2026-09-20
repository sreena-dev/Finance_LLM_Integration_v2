"""Targeted per-row rescue: the primitives a caller uses to recover a single
unreadable cell via a narrowly cropped, second-chance VLM call, and the two
anti-hallucination controls that gate whether its answer is trusted at all --
`ILLEGIBLE` (an explicitly correct, expected answer) and anchor-matching (a
rescue must reproduce the row's OTHER already-trusted figures, never shown to
it, before its answer for the CELL of interest is accepted).

Run with::

    cd ingestion
    venv/Scripts/python -m pytest tests/test_rescue.py -q
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.convert import OcrLine, TableCellGeom  # noqa: E402
from app.tables import parse_markdown_tables  # noqa: E402
from app.vlm_read import (  # noqa: E402
    check_rescue_anchors,
    parse_rescue_row,
    rescue_cell_text,
    row_band,
)


def _table():
    md = (
        "| Particulars | Amount | Prior Year |\n"
        "| --- | --- | --- |\n"
        "| Share Capital | 15,00,000 | 15,00,000 |\n"
        "| Investment Properties | 8Z5'E | 4,00,000 |\n"
        "| Surplus | (25,460) | (10,000) |\n"
    )
    return parse_markdown_tables(md, 1)[0]


# ---------------------------------------------------------------------------
# row_band: geometry must never be Y-flipped
# ---------------------------------------------------------------------------

def _converted_table_fixture():
    """A synthetic page 1000px tall, 800px wide, page_height_pt=500 (so the
    points->pixels scale is exactly 2.0). The header sits at the TOP of the
    page (y in points 10-30) and the target row sits just below it (y in
    points 92-126) -- both near the top of the image. A Y-flipped
    implementation would instead read from near the BOTTOM of the image
    (pixel rows ~750-816), which this fixture marks with a third, distinct
    value so a flip is caught even if the crop's shape alone would not
    reveal it.
    """
    page_image = np.full((1000, 800), 255, dtype=np.uint8)
    page_image[0:60, 40:760] = 40                      # correct header band
    page_image[184:252, 40:760] = 80                    # correct row band
    page_image[748:816, 40:760] = 160                    # where a Y-flip would look instead

    header_cell = TableCellGeom(text="Particulars", row_start=0, row_end=1, col_start=0, col_end=1,
                                 bbox=(50.0, 10.0, 400.0, 30.0))
    value_cell = TableCellGeom(text="Amount", row_start=0, row_end=1, col_start=1, col_end=2,
                                bbox=(400.0, 10.0, 750.0, 30.0))
    ocr_lines = [
        OcrLine(text="Particulars", confidence=0.99, bbox=(50.0, 10.0, 400.0, 28.0)),
        OcrLine(text="Investment Properties", confidence=0.95, bbox=(50.0, 100.0, 400.0, 118.0)),
        OcrLine(text="Amount", confidence=0.95, bbox=(400.0, 100.0, 750.0, 118.0)),
    ]
    return SimpleNamespace(
        cells=[header_cell, value_cell],
        ocr_lines=ocr_lines,
        page_height_pt=500.0,
    )


def test_row_band_reads_from_the_top_not_a_y_flipped_position():
    converted = _converted_table_fixture()
    page_image = np.full((1000, 800), 255, dtype=np.uint8)
    page_image[0:60, 40:760] = 40
    page_image[184:252, 40:760] = 80
    page_image[748:816, 40:760] = 160

    image = row_band(page_image, converted, "Investment Properties", scale=1.0)
    assert image is not None
    # The crop must contain the correct (top-of-page) markers...
    assert 40 in image or 80 in image
    # ...and must NOT contain the value that only appears where a Y-flipped
    # implementation would have looked instead.
    assert 160 not in image


def test_row_band_returns_none_without_geometry():
    converted = SimpleNamespace(cells=[], ocr_lines=[], page_height_pt=None)
    page_image = np.full((100, 100), 255, dtype=np.uint8)
    assert row_band(page_image, converted, "Investment Properties") is None


def test_row_band_returns_none_when_no_line_matches_the_label():
    converted = _converted_table_fixture()
    page_image = np.full((1000, 800), 255, dtype=np.uint8)
    assert row_band(page_image, converted, "Something Entirely Unrelated") is None


# ---------------------------------------------------------------------------
# parse_rescue_row: takes the last real data row, ignores stray formatting
# ---------------------------------------------------------------------------

def test_parse_rescue_row_takes_the_data_line_not_a_separator():
    reply = (
        "| Row | Amount | Prior Year |\n"
        "| --- | --- | --- |\n"
        "| Investment Properties | 5,00,000 | 4,00,000 |\n"
    )
    cells = parse_rescue_row(reply)
    assert cells == ["Investment Properties", "5,00,000", "4,00,000"]


def test_parse_rescue_row_returns_none_for_a_non_table_reply():
    assert parse_rescue_row("I cannot determine this value.") is None


# ---------------------------------------------------------------------------
# check_rescue_anchors / rescue_cell_text: the real anti-hallucination gate
# ---------------------------------------------------------------------------

def test_a_rescue_that_reproduces_its_anchor_is_accepted():
    table = _table()
    row = next(r for r in range(len(table.rows)) if table.label(r) == "Investment Properties")
    # Column 0 (Amount) is the unreadable target; column 1 (Prior Year) is
    # docling's own confident 4,00,000 -- the anchor. The rescue reproduces
    # BOTH: its own candidate for Amount, and the anchor for Prior Year.
    rescued = ["Investment Properties", "5,00,000", "4,00,000"]
    assert check_rescue_anchors(table, row, target_col=1, rescued_cells=rescued) is True
    assert rescue_cell_text(table, row, target_col=1, rescued_cells=rescued) == "5,00,000"


def test_a_rescue_that_misreads_its_anchor_is_discarded():
    """One wrong anchor fails the WHOLE read -- a model that misread a figure
    it could see is not evidence about one it could not."""
    table = _table()
    row = next(r for r in range(len(table.rows)) if table.label(r) == "Investment Properties")
    rescued = ["Investment Properties", "5,00,000", "9,99,999"]  # wrong anchor
    assert check_rescue_anchors(table, row, target_col=1, rescued_cells=rescued) is False


def test_a_rescue_answering_illegible_for_the_target_yields_no_candidate():
    table = _table()
    row = next(r for r in range(len(table.rows)) if table.label(r) == "Investment Properties")
    rescued = ["Investment Properties", "ILLEGIBLE", "4,00,000"]
    assert rescue_cell_text(table, row, target_col=1, rescued_cells=rescued) is None


def test_a_rescue_answering_illegible_for_an_anchor_is_discarded():
    """ILLEGIBLE on an ANCHOR (not the target) is still a failure to
    reproduce it -- it must not be silently skipped as "no data either way"."""
    table = _table()
    row = next(r for r in range(len(table.rows)) if table.label(r) == "Investment Properties")
    rescued = ["Investment Properties", "5,00,000", "ILLEGIBLE"]
    assert check_rescue_anchors(table, row, target_col=1, rescued_cells=rescued) is False


def test_a_rescue_with_no_anchor_available_is_not_accepted():
    """A single-value-column table has nothing to check the rescue against
    at all -- honest degradation, not blind trust."""
    md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Investment Properties | 8Z5'E |\n"
    )
    table = parse_markdown_tables(md, 1)[0]
    row = 0
    rescued = ["Investment Properties", "5,00,000"]
    assert check_rescue_anchors(table, row, target_col=1, rescued_cells=rescued) is False
