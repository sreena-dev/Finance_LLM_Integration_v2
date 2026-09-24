"""Reading a printed figure: verdicts and comparisons, never repairs."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.normalize import (  # noqa: E402
    KIND_DASH, KIND_EMPTY, KIND_MALFORMED, KIND_NUMBER, KIND_TEXT, looks_numeric, parse_number,
    same_figure,
)


def test_indian_grouping_is_clean():
    p = parse_number("1,20,000")
    assert p.kind == KIND_NUMBER and p.value == 120000.0 and p.clean


def test_western_grouping_is_clean():
    p = parse_number("120,000.50")
    assert p.value == 120000.5 and p.clean


def test_brackets_mean_negative():
    p = parse_number("(95,000)")
    assert p.value == -95000.0 and p.negative


def test_leading_minus_is_negative():
    assert parse_number("-5,000").value == -5000.0


def test_dash_and_nil_are_zero_statements_not_missing():
    for text in ("-", "\u2014", "Nil", "nil"):
        p = parse_number(text)
        assert p.kind == KIND_DASH and p.value == 0.0 and p.is_figure


def test_empty_and_text():
    assert parse_number("").kind == KIND_EMPTY
    assert parse_number("Refer note").kind == KIND_TEXT


def test_a_letter_o_for_a_zero_is_malformed_not_repaired():
    p = parse_number("1,2O0")
    assert p.kind == KIND_MALFORMED and not p.clean and p.value is None


def test_odd_grouping_is_flagged_but_still_a_number():
    p = parse_number("1,23,45")
    assert p.kind == KIND_NUMBER and not p.clean and "grouping_odd" in p.flags


def test_clipped_bracket_is_sign_uncertain():
    p = parse_number("(95,000")
    assert "sign_uncertain" in p.flags and not p.clean


def test_same_figure_ignores_grouping_style_but_not_sign():
    assert same_figure("1,20,000", "120,000")
    assert not same_figure("120,000", "(120,000)")
    assert not same_figure("1,20,000", "1,20,001")


def test_garbled_reads_never_corroborate_each_other():
    assert not same_figure("1,2O0", "1,2O0")
    assert not same_figure("abc", "abc")


def test_dashes_agree_with_dashes():
    assert same_figure("-", "\u2014")


def test_looks_numeric_is_generous_but_not_for_words():
    assert looks_numeric("1,20,000") and looks_numeric("(95)") and looks_numeric("1,2O0")
    assert not looks_numeric("Total") and not looks_numeric("")
