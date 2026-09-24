"""LayoutEngine.analyse against a fake docling document (no docling needed)."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace as NS

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import layout  # noqa: E402

W_PT, H_PT = 595.0, 842.0
W_PX, H_PX = 2479, 3508


def bbox(l, t, r, b, origin="BOTTOMLEFT"):
    return NS(l=l, t=t, r=r, b=b, coord_origin=NS(name=origin))


def item(text, label, l, t, r, b, level=1):
    return NS(text=text, label=NS(value=label), level=level, prov=[NS(page_no=1, bbox=bbox(l, t, r, b))])


def table(l, t, r, b):
    return NS(prov=[NS(page_no=1, bbox=bbox(l, t, r, b))])


class FakeConverter:
    def __init__(self, document, status="SUCCESS"):
        self.result = NS(document=document, status=NS(name=status))

    def convert(self, stream, raises_on_error=False):
        return self.result


def engine_with(document, monkeypatch, status="SUCCESS"):
    monkeypatch.setattr(layout, "DOCLING_AVAILABLE", True)
    monkeypatch.setattr(layout, "DocumentStream", lambda **kw: kw, raising=False)
    engine = layout.LayoutEngine()
    engine._converter = FakeConverter(document, status)
    return engine


def page_image():
    return np.full((H_PX, W_PX), 255, np.uint8)


def document(texts, tables):
    return NS(pages={1: NS(size=NS(width=W_PT, height=H_PT))}, texts=texts, tables=tables)


def test_headings_text_and_table_regions_come_back_in_top_left_pixels(monkeypatch):
    doc = document(
        texts=[
            item("Balance Sheet as at 31st March, 2024", "section_header", 50, 800, 500, 780),
            item("Running header", "page_header", 50, 830, 300, 820),
            item("Some narrative below the table.", "text", 50, 200, 500, 180),
        ],
        tables=[table(50, 760, 545, 300)],
    )
    page = engine_with(doc, monkeypatch).analyse(page_image(), 1)

    assert len(page.regions) == 1
    x0, y0, x1, y1 = page.regions[0].bbox
    assert abs(y0 - (842 - 760) * 4.1667) < 2 and abs(y1 - (842 - 300) * 4.1667) < 2
    assert page.regions[0].title == "Balance Sheet as at 31st March, 2024"
    assert page.regions[0].index_on_page == 0

    assert "## Balance Sheet as at 31st March, 2024" in page.markdown
    assert "Some narrative below the table." in page.markdown
    assert "Running header" not in page.markdown                   # page furniture is dropped


def test_text_inside_a_table_region_is_left_out_of_the_page_text(monkeypatch):
    doc = document(
        texts=[item("cell text that is really the table", "text", 100, 600, 300, 580)],
        tables=[table(50, 760, 545, 300)],
    )
    page = engine_with(doc, monkeypatch).analyse(page_image(), 1)
    assert "cell text" not in page.markdown and page.items == []


def test_regions_are_ordered_top_to_bottom_and_duplicates_dropped(monkeypatch):
    doc = document(
        texts=[],
        tables=[table(50, 300, 545, 100), table(50, 780, 545, 500), table(52, 778, 543, 502)],
    )
    page = engine_with(doc, monkeypatch).analyse(page_image(), 1)
    assert len(page.regions) == 2
    assert page.regions[0].bbox[1] < page.regions[1].bbox[1]
    assert [r.index_on_page for r in page.regions] == [0, 1]


def test_a_partial_conversion_is_reported_in_the_notes(monkeypatch):
    page = engine_with(document([], []), monkeypatch, status="PARTIAL_SUCCESS").analyse(page_image(), 1)
    assert any("PARTIAL_SUCCESS" in n for n in page.notes)


def test_without_docling_the_error_says_what_to_install(monkeypatch):
    monkeypatch.setattr(layout, "DOCLING_AVAILABLE", False)
    try:
        layout.LayoutEngine().analyse(page_image(), 1)
    except layout.LayoutError as exc:
        assert "requirements.txt" in str(exc)
    else:
        raise AssertionError("expected LayoutError")
