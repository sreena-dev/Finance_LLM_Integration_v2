"""Regrouping OCR words into cell-sized tokens."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ocr import group_words  # noqa: E402


def w(text, x0, x1, y0=10, y1=44, conf=0.99):
    return (text, conf, (float(x0), float(y0), float(x1), float(y1)))


def test_label_words_close_together_stay_one_token():
    words = [w("Property,", 10, 130), w("Plant", 136, 200), w("and", 206, 250), w("Equipment", 258, 390)]
    tokens = group_words(words)
    assert [t[0] for t in tokens] == ["Property, Plant and Equipment"]


def test_a_note_number_is_not_swallowed_by_the_label_before_it():
    words = [w("Equipment", 259, 392), w("3", 415, 428)]
    assert [t[0] for t in group_words(words)] == ["Equipment", "3"]


def test_neighbouring_figures_in_different_columns_stay_separate():
    words = [w("68,67,416.61", 457, 614), w("3,10,253.00", 637, 782)]
    assert [t[0] for t in group_words(words)] == ["68,67,416.61", "3,10,253.00"]


def test_merged_token_takes_the_weakest_confidence_and_the_union_box():
    tokens = group_words([w("Total", 10, 70, conf=0.99), w("assets", 76, 150, conf=0.71)])
    text, conf, box = tokens[0]
    assert text == "Total assets" and conf == 0.71 and box[0] == 10 and box[2] == 150


def test_blank_words_are_dropped():
    assert group_words([w("  ", 10, 20), w("Total", 30, 90)])[0][0] == "Total"
