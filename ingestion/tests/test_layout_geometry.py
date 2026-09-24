"""Layout helpers that need no docling: coordinates, titles, unmarked tables."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.layout import (  # noqa: E402
    TextItem, dedupe_regions, detect_unmarked, find_title, page_markdown, to_pixel_box,
)
from app.tabletypes import TableRegion  # noqa: E402

# A page 595 x 842 pt rendered at 300 DPI is 2479 x 3508 px (scale 4.1667).
W_PT, H_PT, W_PX, H_PX = 595.0, 842.0, 2479, 3508


def test_bottom_left_box_is_flipped_and_scaled():
    # A box near the TOP of the page: in BOTTOMLEFT terms, large y values.
    box = to_pixel_box(50, 800, 545, 700, "BOTTOMLEFT", W_PT, H_PT, W_PX, H_PX)
    assert box is not None
    x0, y0, x1, y1 = box
    assert abs(x0 - 50 * 4.1667) < 1 and abs(x1 - 545 * 4.1667) < 1
    assert abs(y0 - (842 - 800) * 4.1667) < 1     # near the top, not near the bottom
    assert abs(y1 - (842 - 700) * 4.1667) < 1


def test_top_left_box_is_only_scaled():
    x0, y0, x1, y1 = to_pixel_box(50, 100, 545, 300, "TOPLEFT", W_PT, H_PT, W_PX, H_PX)
    assert abs(y0 - 100 * 4.1667) < 1 and abs(y1 - 300 * 4.1667) < 1


def test_a_box_that_lands_outside_the_page_is_refused_not_clamped_into_a_guess():
    assert to_pixel_box(5000, 100, 5100, 200, "TOPLEFT", W_PT, H_PT, W_PX, H_PX) is None


def test_a_sliver_is_refused():
    assert to_pixel_box(10, 10, 12, 11, "TOPLEFT", W_PT, H_PT, W_PX, H_PX) is None


def test_duplicate_regions_keep_the_larger():
    big = TableRegion(1, (100, 100, 900, 700))
    dup = TableRegion(1, (110, 105, 890, 690))
    other_page = TableRegion(2, (110, 105, 890, 690))
    kept = dedupe_regions([dup, big, other_page])
    assert big in kept and dup not in kept and other_page in kept


def test_title_is_the_nearest_heading_above():
    items = [
        TextItem("Company Ltd", (100, 40, 500, 80), "text"),
        TextItem("Balance Sheet as at 31st March, 2024", (100, 150, 900, 190), "section_header"),
    ]
    assert find_title((100, 220, 900, 800), items) == "Balance Sheet as at 31st March, 2024"


def test_no_title_when_the_only_text_above_is_far_away_or_numeric():
    items = [TextItem("Far away", (100, 10, 300, 40), "text"), TextItem("12,345", (100, 200, 300, 230), "text")]
    assert find_title((100, 700, 900, 900), items) is None


def test_markdown_marks_headings_and_lists():
    items = [
        TextItem("Notes to the Financial Statements", (0, 0, 100, 30), "section_header"),
        TextItem("Some policy text.", (0, 50, 100, 80), "text"),
        TextItem("first point", (0, 100, 100, 130), "list_item"),
    ]
    md = page_markdown(items)
    assert md.splitlines()[0].startswith("## Notes to")
    assert "- first point" in md


def _figure_block(n_rows=5, x=1200):
    items = []
    for r in range(n_rows):
        y = 500 + r * 70
        items.append(TextItem(f"Item {r}", (200, y, 600, y + 34), "text"))
        items.append(TextItem("1,00,000", (x, y, x + 200, y + 34), "text"))
        items.append(TextItem("90,000", (x + 300, y, x + 460, y + 34), "text"))
    return items


def test_a_block_of_loose_figures_is_offered_as_a_table():
    found = detect_unmarked(_figure_block(), [], 1, W_PX)
    assert len(found) == 1 and found[0].source == "unmarked"
    x0, y0, x1, y1 = found[0].bbox
    assert x0 <= 200 and x1 >= 1660 and y0 < 500 and y1 > 500 + 4 * 70


def test_figures_inside_a_marked_region_are_not_offered_again():
    region = TableRegion(1, (100, 400, 2000, 1000))
    assert detect_unmarked(_figure_block(), [region], 1, W_PX) == []


def test_too_few_figures_offer_nothing():
    assert detect_unmarked(_figure_block(n_rows=2), [], 1, W_PX) == []


def _small_statement(n_rows=5, labelled=True, x=1500):
    items = []
    for r in range(n_rows):
        y = 700 + r * 90
        if labelled:
            items.append(TextItem(f"Finance cost line {r}", (200, y, 900, y + 34), "text"))
        items.append(TextItem(f"{25 + r},000", (x, y, x + 180, y + 34), "text"))
    return items


def test_a_small_labelled_statement_with_only_a_few_amounts_is_offered():
    # A tiny company's income statement: five amounts, each on a labelled line.
    found = detect_unmarked(_small_statement(5), [], 2, W_PX)
    assert len(found) == 1 and found[0].page_no == 2
    x0, y0, x1, y1 = found[0].bbox
    assert x0 <= 200 and x1 >= 1680


def test_four_labelled_amounts_are_enough():
    assert len(detect_unmarked(_small_statement(4), [], 1, W_PX)) == 1


def test_a_few_amounts_with_no_labels_are_not_a_table():
    assert detect_unmarked(_small_statement(5, labelled=False), [], 1, W_PX) == []


def test_three_labelled_amounts_are_not_enough():
    assert detect_unmarked(_small_statement(3), [], 1, W_PX) == []


def test_page_numbers_and_a_year_in_the_margin_are_not_a_table():
    items = [
        TextItem("Page 4 of 15", (200, 3400, 600, 3434), "text"),
        TextItem("4", (1500, 3400, 1530, 3434), "text"),
        TextItem("15", (1500, 3490, 1540, 3524), "text"),
        TextItem("2022", (1500, 3580, 1600, 3614), "text"),
    ]
    assert detect_unmarked(items, [], 1, W_PX) == []


def test_only_some_labelled_lines_is_not_enough_for_a_small_block():
    items = _small_statement(5)
    items = [i for i in items if not (i.text.startswith("Finance cost line") and int(i.text[-1]) >= 2)]
    assert detect_unmarked(items, [], 1, W_PX) == []
