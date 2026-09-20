"""Corrections that check their own work.

Every preprocessing step here is lossy, and until now each one was applied on
the strength of a measurement saying it was WARRANTED -- never a measurement
saying it WORKED. A deskew that left the page no straighter, or a sharpen that
amplified halftone rather than print, was applied, recorded in the quality
report as an improvement, and its damage travelled downstream as fact.

The deskew case is the one that matters most. `preprocess`'s own docstring
explains why: rotating a page displaces the end of every row, and a table
extractor clustering cells by vertical position then binds a label to the
values from the row above or below and reports no error doing so -- "the worst
possible failure: confident and wrong".

Run with::

    cd ingestion
    venv/Scripts/python -m pytest tests/test_preprocess_feedback.py -q
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import preprocess                              # noqa: E402
from app.config import Config                           # noqa: E402
from app.models import PageQuality                      # noqa: E402
from app.precheck import BLUR_WARN, _blur               # noqa: E402
from app.render import RenderedPage                     # noqa: E402

cv2 = pytest.importorskip("cv2")


def _page_with_text(width=1200, height=900, skew_deg=0.0, soft=False):
    """A synthetic page of ruled text lines -- enough structure for the skew
    estimator and the Laplacian to have something real to measure."""
    img = np.full((height, width), 245, dtype=np.uint8)
    for y in range(80, height - 60, 40):
        img[y:y + 6, 80:width - 80] = 30
    if skew_deg:
        matrix = cv2.getRotationMatrix2D((width / 2.0, height / 2.0), skew_deg, 1.0)
        img = cv2.warpAffine(img, matrix, (width, height),
                             flags=cv2.INTER_CUBIC,
                             borderMode=cv2.BORDER_CONSTANT, borderValue=245)
    if soft:
        img = cv2.GaussianBlur(img, (0, 0), sigmaX=1.6)
    return img


def _quality(image, **kw):
    q = PageQuality(page_no=1, width_px=image.shape[1], height_px=image.shape[0])
    q.blur = round(_blur(image), 2)
    q.contrast = 0.9          # high, so CLAHE stays out of the way
    q.colour = "greyscale"    # not bilevel, so despeckle stays out of the way
    for key, value in kw.items():
        setattr(q, key, value)
    return q


def _run(image, quality):
    return preprocess.preprocess_page(
        RenderedPage(page_no=1, image=image, quality=quality), quality
    )


def _defect_codes(quality):
    return {d.code for d in quality.defects}


def _defect_text(quality):
    return " ".join(d.detail for d in quality.defects)


@pytest.fixture
def sharpening_on(monkeypatch):
    """Turn sharpening on for tests that exercise the mechanism.

    It ships OFF -- measured against the accuracy harness, it made pages
    sharper and extraction worse (see `Config.SHARPEN_ENABLED`). The mechanism
    is still tested because it is still there and still tunable; what must not
    be tested by accident is that it runs, which is what
    `test_sharpening_is_off_by_default_because_it_was_measured_and_lost`
    pins instead.
    """
    monkeypatch.setattr(Config, "SHARPEN_ENABLED", True)


# --------------------------------------------------------------------------
# Sharpening -- acting on a measurement that was previously ignored
# --------------------------------------------------------------------------

def test_sharpening_is_off_by_default_because_it_was_measured_and_lost():
    """Enabled, it cost 5 correct figures across the three accuracy cases
    (58 -> 53 CORRECT, 10 -> 12 MISSING) while making every qualifying page
    measurably sharper. Sharper pixels are not the goal; correct figures are.

    Pinned as a test so re-enabling it is a deliberate act with a failing
    test attached, not a one-character default nobody notices.
    """
    assert Config.SHARPEN_ENABLED is False


def test_a_soft_page_is_untouched_while_sharpening_is_off():
    image = _page_with_text(soft=True)
    quality = _quality(image)
    assert quality.blur < BLUR_WARN

    out = _run(image, quality)

    assert np.array_equal(out, image)

def test_a_soft_page_is_sharpened(sharpening_on):
    """`blur` had been measured since the first version of this pipeline and
    nothing ever acted on it."""
    image = _page_with_text(soft=True)
    quality = _quality(image)
    assert quality.blur < BLUR_WARN, "fixture is not soft enough to trigger"

    out = _run(image, quality)

    assert _blur(out) > _blur(image), "the page came back no sharper"
    assert "sharpened" in _defect_text(quality)


def test_a_page_that_is_already_sharp_is_left_alone():
    """Nothing is applied 'just in case': every step here is lossy."""
    image = _page_with_text(soft=False)
    quality = _quality(image, blur=BLUR_WARN + 500.0)

    out = _run(image, quality)

    assert np.array_equal(out, image)
    assert "sharpened" not in _defect_text(quality)


def test_sharpening_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(Config, "SHARPEN_ENABLED", False)
    image = _page_with_text(soft=True)
    quality = _quality(image)

    out = _run(image, quality)

    assert np.array_equal(out, image)


def test_a_sharpen_that_does_not_help_is_discarded_and_reported(sharpening_on):
    """The measurement decides, not the intent. Reported rather than silent --
    a correction that had to be thrown away means a page the pipeline found
    hard, which is worth an auditor knowing when they weigh a figure from it."""
    image = _page_with_text(soft=True)
    quality = _quality(image)
    # A sharpen that returns the input unchanged cannot improve the score.
    preprocess_sharpen = preprocess._sharpen
    try:
        preprocess._sharpen = lambda img: img
        out = _run(image, quality)
    finally:
        preprocess._sharpen = preprocess_sharpen

    assert np.array_equal(out, image)
    assert "correction_rejected" in _defect_codes(quality)
    assert "discarded" in _defect_text(quality)


def test_the_recorded_sharpness_reflects_the_image_that_was_kept(sharpening_on):
    """quality.blur is what downstream withholding rules read. Leaving it at
    the pre-sharpen value would describe an image that no longer exists."""
    image = _page_with_text(soft=True)
    quality = _quality(image)
    before = quality.blur

    out = _run(image, quality)

    assert quality.blur > before
    assert quality.blur == pytest.approx(_blur(out), rel=0.01)


# --------------------------------------------------------------------------
# Deskew -- the correction with the worst failure mode
# --------------------------------------------------------------------------

def test_a_real_skew_is_corrected():
    image = _page_with_text(skew_deg=1.7)
    quality = _quality(image, skew_deg=1.7)

    out = _run(image, quality)

    assert not np.array_equal(out, image)
    assert "deskewed" in _defect_text(quality)


def test_a_deskew_that_does_not_straighten_the_page_is_discarded():
    """THE regression this file exists for. A level page carrying a bogus
    skew measurement must come back untouched, not rotated by 3 degrees and
    reported as 'corrected'."""
    image = _page_with_text(skew_deg=0.0)
    # A measurement that is simply wrong -- the page is level.
    quality = _quality(image, skew_deg=3.0)

    out = _run(image, quality)

    assert np.array_equal(out, image), "a level page was rotated anyway"
    assert "deskewed" not in _defect_text(quality)
    assert "correction_rejected" in _defect_codes(quality)


def test_a_rejected_deskew_says_what_it_measured():
    image = _page_with_text(skew_deg=0.0)
    quality = _quality(image, skew_deg=3.0)

    _run(image, quality)

    text = _defect_text(quality)
    assert "discarded" in text
    assert "no straighter" in text


def test_a_skew_below_the_floor_is_not_touched():
    image = _page_with_text(skew_deg=0.0)
    quality = _quality(image, skew_deg=0.05)

    out = _run(image, quality)

    assert np.array_equal(out, image)
    assert _defect_codes(quality) == set()


def test_a_skew_above_the_ceiling_is_refused_not_corrected():
    """Beyond the ceiling it is a mis-oriented page, not skew."""
    image = _page_with_text(skew_deg=0.0)
    quality = _quality(image, skew_deg=45.0)

    out = _run(image, quality)

    assert np.array_equal(out, image)


# --------------------------------------------------------------------------
# Rotation -- verifying what does NOT need doing
# --------------------------------------------------------------------------

def test_page_rotation_is_not_reapplied_here():
    """pdfium's own render() already applies the PDF's /Rotate, so the bitmap
    arriving here is upright and PageQuality.rotation is informational.
    Re-applying it would rotate a correct page into being wrong -- pinned
    because the plan for this phase originally called for exactly that."""
    image = _page_with_text()
    quality = _quality(image, rotation=90)

    out = _run(image, quality)

    assert out.shape == image.shape, "the page was rotated a second time"
    assert np.array_equal(out, image)


# --------------------------------------------------------------------------
# Orientation -- gross quadrant correction, applied before fine deskew
# --------------------------------------------------------------------------

def test_a_90_degree_quadrant_is_corrected_before_deskew_runs():
    """precheck sets orientation_quadrant when content was scanned sideways
    with no /Rotate to declare it -- distinct from `rotation`, which is
    PDF-metadata-only and (per the test above) never reapplied here."""
    image = _page_with_text(width=1200, height=900)
    # np.rot90(x, k=1) and np.rot90(x, k=3) undo each other -- a page scanned
    # so its content ended up rotated this way needs the OTHER k applied to
    # come back upright, which is exactly what `_estimate_orientation` (and
    # therefore `orientation_quadrant`) reports: apply np.rot90(sideways,
    # k=quadrant//90) and get `image` back.
    sideways = np.rot90(image, k=3)
    quality = _quality(sideways, orientation_quadrant=90, skew_deg=0.0)

    out = _run(sideways, quality)

    assert out.shape == image.shape
    assert np.array_equal(out, image)
    assert "rotated 90 degrees" in _defect_text(quality)


def test_orientation_quadrant_zero_is_a_no_op():
    image = _page_with_text()
    quality = _quality(image, orientation_quadrant=0, skew_deg=0.0)

    out = _run(image, quality)

    assert np.array_equal(out, image)
    assert "rotated" not in _defect_text(quality)


def test_quadrant_correction_lands_before_the_fine_deskew_search():
    """Deskew's own +/-6 degree search assumes a roughly-upright starting
    point. A page that needed BOTH a quadrant swap and a small residual
    skew must have the quadrant correction applied first -- pinned by
    checking the applied-corrections note lists 'rotated' before
    'deskewed', and that the final result is both axis-correct and
    straighter than the naive 90-degree rotation alone."""
    image = _page_with_text(width=1200, height=900, skew_deg=1.7)
    sideways = np.rot90(image, k=3)
    quality = _quality(sideways, orientation_quadrant=90, skew_deg=1.7)

    out = _run(sideways, quality)

    text = _defect_text(quality)
    assert text.index("rotated 90 degrees") < text.index("deskewed")
    assert out.shape == image.shape


# --------------------------------------------------------------------------
# The blank-page short circuit still holds
# --------------------------------------------------------------------------

def test_a_blank_page_is_returned_untouched():
    image = _page_with_text(soft=True)
    quality = _quality(image, is_blank=True)

    out = _run(image, quality)

    assert np.array_equal(out, image)
    assert _defect_codes(quality) == set()
