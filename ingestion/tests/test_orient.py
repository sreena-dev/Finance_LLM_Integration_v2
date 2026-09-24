"""Turning a sideways page upright, with the OCR calls replaced by fakes."""

from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import orient  # noqa: E402

UPRIGHT = [4.0, 3.5, 5.0, 2.8] * 6            # wide, short boxes: ordinary lines of text
SIDEWAYS = [0.3, 0.4, 0.5, 0.35] * 6          # tall, narrow boxes: lines standing up


def marked_page(h=200, w=140):
    """A page whose top-left corner is dark, so its orientation can be read off."""
    image = np.full((h, w), 255, np.uint8)
    image[:40, :40] = 0
    return image


def looks_upright(image) -> bool:
    return image[:30, :30].mean() < 50 and image[-30:, -30:].mean() > 200


def reads_best_upright(image) -> float:
    return 100.0 if looks_upright(image) else 10.0


def scanned_sideways(image, degrees_clockwise):
    """The same page as it would arrive if the paper had been turned."""
    back = {90: 270, 270: 90}[degrees_clockwise]
    return orient.turn(image, back)


def test_an_upright_page_has_no_tall_boxes():
    assert orient.tall_fraction(UPRIGHT) == 0.0
    assert not orient.is_sideways(UPRIGHT)


def test_a_page_of_standing_text_is_sideways():
    assert orient.tall_fraction(SIDEWAYS) == 1.0
    assert orient.is_sideways(SIDEWAYS)


def test_a_mostly_upright_page_with_a_few_vertical_labels_is_left_alone():
    mixed = UPRIGHT + [0.3, 0.3, 0.3]             # a stamp or a vertical margin note
    assert not orient.is_sideways(mixed)


def test_too_few_boxes_to_judge_leaves_the_page_alone():
    assert not orient.is_sideways([0.3, 0.3, 0.3])
    assert not orient.is_sideways([])


def test_an_upright_page_is_returned_untouched():
    page = marked_page()
    out, degrees = orient.correct_page(page, detect=lambda im: UPRIGHT, score=reads_best_upright)
    assert degrees == 0 and out is page


def test_a_page_scanned_a_quarter_turn_one_way_is_turned_back():
    original = marked_page()
    sideways = scanned_sideways(original, 90)
    out, degrees = orient.correct_page(sideways, detect=lambda im: SIDEWAYS, score=reads_best_upright)
    assert degrees == 90
    assert looks_upright(out) and out.shape == original.shape
    assert np.array_equal(out, original)


def test_a_page_scanned_a_quarter_turn_the_other_way_is_turned_back_the_other_way():
    original = marked_page()
    sideways = scanned_sideways(original, 270)
    out, degrees = orient.correct_page(sideways, detect=lambda im: SIDEWAYS, score=reads_best_upright)
    assert degrees == 270
    assert np.array_equal(out, original)


def test_the_direction_is_chosen_by_how_well_each_turn_reads_not_by_a_fixed_rule():
    page = marked_page()
    assert orient.choose_turn(page, score=lambda im: 1.0 if not looks_upright(im) else 0.0) in (90, 270)
    sideways = scanned_sideways(page, 270)
    assert orient.choose_turn(sideways, score=reads_best_upright) == 270


def test_a_failed_check_leaves_the_page_as_scanned():
    page = marked_page()

    def boom(image):
        raise RuntimeError("engine not available")

    out, degrees = orient.correct_page(page, detect=boom, score=reads_best_upright)
    assert degrees == 0 and out is page


def test_correct_pages_reports_which_pages_were_turned(monkeypatch):
    monkeypatch.setattr(orient.Config, "ORIENTATION_CORRECTION", True)
    upright, sideways = marked_page(), scanned_sideways(marked_page(), 90)
    detect = lambda im: SIDEWAYS if im is sideways else UPRIGHT
    images, turned = orient.correct_pages([upright, sideways, upright], detect, reads_best_upright)
    assert turned == {1: 90}
    assert images[0] is upright and images[2] is upright
    assert np.array_equal(images[1], marked_page())


def test_the_step_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(orient.Config, "ORIENTATION_CORRECTION", False)
    sideways = scanned_sideways(marked_page(), 90)
    images, turned = orient.correct_pages([sideways], lambda im: SIDEWAYS, reads_best_upright)
    assert turned == {} and images[0] is sideways


def test_it_is_on_by_default():
    assert orient.Config.ORIENTATION_CORRECTION is True
