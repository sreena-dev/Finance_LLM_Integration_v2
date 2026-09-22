"""``convert.py``'s HTML-unescape fix.

Real bug, found while investigating why a working `classify_statement`
pattern for "Income and Expenditure Account" still wasn't classifying a real
Section 8 company's face statement (data/OD-SPSU-SO-032/...): docling's
markdown export HTML-escapes ``&``, so the heading arrived as literally
``Income &amp; Expenditure Account`` -- not what any regex matching a plain
``&`` was ever going to match. Confirmed directly from that document's own
extracted narrative chunk content.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.convert import _clean                          # noqa: E402
from app.identify import classify_statement              # noqa: E402


def test_html_ampersand_escaping_is_undone():
    assert _clean("Income &amp; Expenditure Account") == "Income & Expenditure Account"
    assert _clean("For K Swain &amp; Co Chartered Accountants") == "For K Swain & Co Chartered Accountants"


def test_none_passes_through():
    assert _clean(None) is None


def test_unescaped_text_is_unaffected():
    """The common case -- no entities at all -- must be a no-op, not merely
    harmless: most headings in this corpus have nothing to unescape."""
    assert _clean("Balance Sheet as at 31st March, 2023") == "Balance Sheet as at 31st March, 2023"


def test_the_actual_bug_this_exists_to_fix():
    """Reproduces the real failure end to end: a correct classify_statement
    pattern, fed docling's actual (escaped) output, classifies nothing at
    all -- the same content, cleaned once at the source, classifies
    correctly. Both assertions matter: the first pins the bug as real, not
    hypothetical; the second proves the fix actually closes it."""
    raw_from_docling = "Income &amp; Expenditure Account for the year ended 31st March 2023"
    assert classify_statement(raw_from_docling) is None
    assert classify_statement(_clean(raw_from_docling)) == "profit_loss"


# ---------------------------------------------------------------------------
# A cut-short conversion must name the pages it lost. Real case: a 120-page
# annual report hit docling's 600 s limit; pages 80-120 came back empty and the
# user saw 43 identical "document timeout exceeded" notices and no page numbers.
# ---------------------------------------------------------------------------

def test_identical_errors_collapse_to_one_line_with_a_count():
    from app.convert import _collapse_errors

    assert _collapse_errors(["document timeout exceeded"] * 3 + ["other"]) == [
        "document timeout exceeded (x3)", "other"]


def test_page_ranges_are_compact():
    from app.convert import _page_ranges

    assert _page_ranges([80, 81, 82, 90, 100, 101]) == "80-82, 90, 100-101"


def test_pages_with_no_content_are_named_by_their_original_number():
    from types import SimpleNamespace as NS
    from app.convert import _unconverted_pages

    def item(page):
        return NS(prov=[NS(page_no=page)])

    document = NS(texts=[item(1), item(2)], tables=[item(2)], pages={1: 0, 2: 0, 3: 0, 4: 0})
    qualities = [NS(page_no=p, is_blank=False) for p in (1, 2, 5, 6)]  # pages 3,4 dropped upstream
    note = _unconverted_pages(document, qualities)
    assert "2 page(s)" in note and "5-6" in note
    assert _unconverted_pages(NS(texts=[item(i) for i in range(1, 5)], tables=[], pages={}), qualities) is None


def test_a_blank_page_is_not_reported_as_unconverted():
    from types import SimpleNamespace as NS
    from app.convert import _unconverted_pages

    document = NS(texts=[NS(prov=[NS(page_no=1)])], tables=[], pages={1: 0, 2: 0})
    qualities = [NS(page_no=1, is_blank=False), NS(page_no=2, is_blank=True)]
    assert _unconverted_pages(document, qualities) is None
