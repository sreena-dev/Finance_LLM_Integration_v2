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
