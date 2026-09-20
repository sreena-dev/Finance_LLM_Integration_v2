"""Detecting a page rotated a full quadrant (90/180/270 degrees) before OCR
ever sees it -- a different, larger defect than ordinary +/-6 degree skew.

`_estimate_orientation` extends `precheck.py`'s own proven skew-detection
principle ("rotate an ink mask through candidate angles, keep whichever
makes the row-sum projection profile have the highest variance") one level
up: exactly as true comparing 0 against 90/180/270 as it is comparing -6
against +6 within the existing fine search.

Run with::

    cd ingestion
    venv/Scripts/python -m pytest tests/test_orientation.py -q
"""

from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.precheck import _estimate_orientation, _estimate_skew  # noqa: E402


def _lined_mask(n_lines: int = 8, line_height: int = 4, gap: int = 8,
                 width: int = 240, ink_cols: int = 170) -> "np.ndarray":
    """A synthetic ink mask with clean horizontal text-line bands: rows of
    ink separated by empty rows, the shape a row-sum projection profile
    reads as "text lines are level" when the mask is upright."""
    total_h = n_lines * (line_height + gap)
    mask = np.zeros((total_h, width), dtype=bool)
    rng = np.random.default_rng(42)
    y = 0
    for _ in range(n_lines):
        cols = rng.choice(width, size=ink_cols, replace=False)
        mask[y:y + line_height, cols] = True
        y += line_height + gap
    return mask


def test_an_already_upright_mask_needs_no_correction():
    quadrant, angle = _estimate_orientation(_lined_mask())
    assert quadrant == 0
    assert abs(angle) < 1.0


def test_a_90_degree_rotated_mask_is_detected_as_needing_an_axis_swap():
    """The row-variance signal reliably detects that an AXIS swap is needed
    (0/180 vs 90/270) -- for a roughly top-bottom-symmetric mask it CANNOT,
    by itself, reliably tell 90 apart from 270 (nor 0 from 180 in the sibling
    test below). That is the exact same limitation this module's own
    docstring already documents for plain 180-degree skew detection:
    "Distinguishing upright from upside-down text from pixel statistics
    alone is unreliable." This test pins the part that IS reliable: SOME
    correction in the {90, 270} class is chosen, not the wrong axis."""
    rotated = np.rot90(_lined_mask(), k=1)
    quadrant, _ = _estimate_orientation(rotated)
    assert quadrant in (90, 270)


def test_a_270_degree_rotated_mask_is_detected_as_needing_an_axis_swap():
    rotated = np.rot90(_lined_mask(), k=3)
    quadrant, _ = _estimate_orientation(rotated)
    assert quadrant in (90, 270)


def test_a_180_degree_rotated_mask_does_not_wrongly_trigger_an_axis_swap():
    """Same caveat as above, the other direction: 0 vs 180 cannot be told
    apart from row-variance alone for a top-bottom-symmetric mask. What
    matters is that horizontal banding is still recognised as horizontal
    banding -- i.e. the (unreliable) 90/270 axis is never wrongly chosen for
    input that only needed, at most, a 180-degree flip within its own axis."""
    rotated = np.rot90(_lined_mask(), k=2)
    quadrant, _ = _estimate_orientation(rotated)
    assert quadrant in (0, 180)


def test_correcting_the_detected_quadrant_produces_a_horizontally_banded_result():
    """Applying the detected correction must produce a mask that itself
    reads as upright (quadrant 0) when checked again -- self-consistency is
    what this algorithm actually guarantees, not necessarily pixel-exact
    equality to the original (the corrected result may be the 180-degree-
    flipped sibling of the original -- see the upright-vs-upside-down
    caveat above -- which is still "upright" in the sense that matters:
    horizontal, readable-direction text lines rather than sideways ones)."""
    original = _lined_mask()
    rotated = np.rot90(original, k=1)
    quadrant, _ = _estimate_orientation(rotated)
    corrected = np.rot90(rotated, k=quadrant // 90)
    assert corrected.shape == original.shape
    re_quadrant, _ = _estimate_orientation(corrected)
    assert re_quadrant == 0


def test_an_ambiguous_mask_defaults_to_zero_never_a_confident_wrong_guess():
    """A mask with no clear line structure at ANY quadrant -- scattered,
    uniform noise -- must never be "corrected": a wrongly rotated upright
    page is worse than a genuinely rotated page left alone."""
    rng = np.random.default_rng(7)
    mask = rng.random((200, 200)) < 0.05  # sparse, unstructured scatter
    quadrant, _ = _estimate_orientation(mask)
    assert quadrant == 0


def test_a_near_empty_mask_defaults_to_zero():
    mask = np.zeros((200, 200), dtype=bool)
    quadrant, angle = _estimate_orientation(mask)
    assert quadrant == 0
    assert angle == 0.0


def test_an_upright_mask_gets_the_same_fine_angle_as_plain_skew_detection():
    """Regression guard: for the overwhelming majority of pages -- ones that
    were never rotated a full quadrant -- `_estimate_orientation`'s fine
    angle must match what `_estimate_skew` alone already reports. Existing
    skew detection must be completely unaffected by this addition."""
    mask = _lined_mask()
    quadrant, angle = _estimate_orientation(mask)
    assert quadrant == 0
    assert angle == _estimate_skew(mask)
