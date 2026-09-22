"""A number followed by a unit-of-measure word must parse, not withhold.

Real shape, verified on OD-SPSU-SO-032's fixed-asset useful-life note: a cell
reading "3 Years" used to come back from `parse_cell` with `value=None` and
NO flags set at all -- not wrong, just silently unusable, and `verify.py`
then classified it `unreadable_text` purely because "Years" isn't a digit.
This is a general gap (`_NUM_RE` requires the WHOLE cleaned string to be
digits-only), not specific to that one document -- any useful-life or
amortisation-period schedule hits it.

Run with::

    cd ingestion
    venv/Scripts/python -m pytest tests/test_unit_suffixed_numbers.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.numbers import parse_cell  # noqa: E402


def test_years_parses_to_its_numeric_value():
    cell = parse_cell("3 Years")
    assert cell.value == 3.0
    assert cell.raw == "3 Years"  # untouched -- the printed text is unchanged
    assert cell.flags() == []
    assert not cell.suspect


def test_abbreviated_yrs_parses_too():
    cell = parse_cell("10 Yrs")
    assert cell.value == 10.0
    assert cell.flags() == []


def test_no_space_between_number_and_unit_still_parses():
    cell = parse_cell("5Years")
    assert cell.value == 5.0


def test_months_days_hours_all_recognised():
    assert parse_cell("6 Months").value == 6.0
    assert parse_cell("45 Days").value == 45.0
    assert parse_cell("2 Hours").value == 2.0
    assert parse_cell("1 Hr").value == 1.0


def test_a_decimal_useful_life_parses():
    cell = parse_cell("3.5 Years")
    assert cell.value == 3.5


def test_an_ocr_damaged_unit_word_does_not_false_positive():
    """"Yeas" (missing the middle "r") is not a recognised unit word -- this
    must stay unparseable, not silently coerced into matching "Years". A loose
    regex here would be exactly the kind of guess this module's docstring
    forbids."""
    cell = parse_cell("3 Yeas")
    assert cell.value is None


def test_a_negative_unit_suffixed_value_resolves_its_sign():
    """Clipped-parenthesis negative ("(3 Years" with the closing paren cropped
    by the table border) must still resolve through the SAME sign-uncertain
    path an ordinary negative number does."""
    cell = parse_cell("(3 Years")
    assert cell.value == -3.0
    assert cell.sign_uncertain is True


def test_an_explicit_sign_marker_with_a_unit_suffix_resolves_cleanly():
    cell = parse_cell("(-) 3 Years")
    assert cell.value == -3.0
    assert cell.sign_uncertain is False


def test_an_ordinary_plain_number_is_completely_unaffected():
    """Regression guard: the new fallback must never be reached for a normal
    cell, and must never change its result."""
    cell = parse_cell("15,00,000.00")
    assert cell.value == 1500000.0
    assert cell.flags() == []


def test_a_bare_unit_word_with_no_number_does_not_parse():
    cell = parse_cell("Years")
    assert cell.value is None
