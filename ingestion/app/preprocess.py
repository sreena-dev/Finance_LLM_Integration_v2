"""Correcting the page image before OCR sees it.

**This is the stage docling does not have**, and the reason the plan treats
docling as the structural engine rather than the whole pipeline. Docling's PDF
pipeline is built around a text layer plus cell matching; where there is no text
layer it delegates to an OCR engine and passes the page through untouched. It
performs no deskew, no resolution normalisation and no contrast correction, and
it will not tell you that any of those were needed.

For this corpus that gap is not academic. ``MH 2022-23 SFS`` page 3 is skewed by
1.7 degrees (measured, not estimated -- see ``precheck``). Over a 1654px page
width that displaces the right-hand end of a row by roughly 49 pixels, which is
more than a text line's height at 200 DPI. A table extractor clustering cells
into rows by vertical position will therefore bind a label to the values from
the row above or below it, and report no error while doing so. That is the
worst possible failure: confident and wrong.

Order matters and is not arbitrary:

1. **Deskew first**, on the original pixels. Rotation resamples, and resampling
   an already-upscaled image compounds the interpolation blur over more pixels
   for no gain.
2. **Then upscale**, so the interpolation works on straightened strokes.
3. **Then normalise contrast**, because CLAHE's tile statistics are meaningful
   only once the geometry is settled.
4. **Despeckle last**, so it removes noise rather than the artefacts the earlier
   steps introduced.

Every step is conditional on a measurement from ``precheck``. Nothing is applied
"just in case": each of these is lossy, and applying CLAHE to an already-crisp
page or despeckling one with fine print costs accuracy rather than buying it.
"""

from __future__ import annotations

import logging

from .config import Config
from .models import PageDefect, PageQuality
from .precheck import CONTRAST_WARN
from .render import RenderedPage

logger = logging.getLogger(__name__)

try:  # pragma: no cover
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

try:  # pragma: no cover
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


def _deskew(img: "np.ndarray", angle: float) -> "np.ndarray":
    """Rotate by ``-angle`` about the page centre, padding with paper white.

    ``BORDER_REPLICATE`` would smear the edge pixels into the corners; a page
    corner is usually blank paper, so an explicit white constant is both more
    honest and easier for the following binarisation to handle. The border
    value is sampled from the page's own paper level rather than hardcoded to
    255, because a grey-cast scan padded with pure white gains a false edge that
    layout detection reads as a rule.
    """
    h, w = img.shape[:2]
    paper = int(np.percentile(img, 90))
    matrix = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), -angle, 1.0)
    return cv2.warpAffine(
        img, matrix, (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=paper,
    )


def _upscale_to(img: "np.ndarray", native_dpi: float | None, target_dpi: int) -> "np.ndarray":
    """Resample a low-resolution scan up to the OCR engine's training scale.

    This adds no information -- the detail was lost at scan time and cannot be
    recovered. What it does buy is real: OCR models are trained at roughly 300
    DPI and their receptive fields are sized for glyphs of that stroke width, so
    feeding them a 200 DPI page makes the text smaller than anything they saw in
    training. Cubic interpolation over a straightened image is a better input
    than the raw bitmap, which is why this runs after deskew rather than before.
    """
    if not native_dpi or native_dpi >= target_dpi:
        return img
    factor = target_dpi / native_dpi
    # Above 2x the interpolation is inventing more than it is preserving, and
    # the memory cost grows with the square.
    factor = min(factor, 2.0)
    return cv2.resize(img, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)


def _normalise_contrast(img: "np.ndarray") -> "np.ndarray":
    """CLAHE -- local, tile-wise histogram equalisation.

    Local rather than global because the defect it addresses is local. On the
    Xerox VersaLink scans (``OD 2021-22``) some paragraphs are crisp and others
    on the same sheet are washed out, which is a scanner exposure artefact
    across the platen. A global stretch normalises the page by its darkest
    region and leaves the faded block exactly as faint as it was.

    ``clipLimit`` is kept low: this is print on paper, and an aggressive limit
    amplifies JPEG blocking in the background into texture that binarisation
    then reads as speckle.
    """
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(img)


def _despeckle(img: "np.ndarray") -> "np.ndarray":
    """Remove isolated scanner noise without eroding thin strokes.

    A 3x3 median is the largest kernel that is safe here. The corpus includes
    1-bit CCITT fax scans whose glyph strokes are already only 1-2 pixels wide
    at 200 DPI; anything wider removes the stroke along with the noise, and a
    thinned ``3`` becomes an ``8``-shaped smudge or disappears.
    """
    return cv2.medianBlur(img, 3)


def preprocess_page(page: RenderedPage, quality: PageQuality) -> "np.ndarray":
    """Return the corrected image for one page, recording what was done to it.

    The returned array is what docling and the VLM both read. ``quality`` is
    mutated in place with a note for each correction, so the quality report can
    tell an auditor that a figure came off a page which had been rotated and
    re-exposed -- which is relevant to how much weight to put on it.
    """
    img = page.image
    if cv2 is None or np is None:
        return img
    if quality.is_blank:
        return img

    applied: list[str] = []

    angle = quality.skew_deg or 0.0
    if (
        Config.DESKEW_ENABLED
        and Config.DESKEW_MIN_ANGLE <= abs(angle) <= Config.DESKEW_MAX_ANGLE
    ):
        img = _deskew(img, angle)
        applied.append(f"deskewed by {angle:+.2f} degrees")

    before_shape = img.shape
    img = _upscale_to(img, quality.native_dpi, Config.RENDER_DPI)
    if img.shape != before_shape:
        applied.append(
            f"upscaled from {quality.native_dpi:.0f} to about {Config.RENDER_DPI} DPI"
        )

    if Config.CLAHE_ENABLED and (quality.contrast or 1.0) < CONTRAST_WARN:
        img = _normalise_contrast(img)
        applied.append("contrast normalised")

    # Only on bilevel scans. A greyscale page's "speckle" is usually halftone or
    # JPEG texture that binarisation handles better than a median filter does,
    # and running this everywhere measurably softens fine print.
    if Config.DESPECKLE_ENABLED and quality.colour == "bilevel":
        img = _despeckle(img)
        applied.append("despeckled")

    if applied:
        quality.defects.append(PageDefect(
            "preprocessed",
            f"Page {quality.page_no} was corrected before reading: "
            + ", ".join(applied) + ".",
            severity="warn",
        ))

    return img


def preprocess(pages: list[RenderedPage], qualities: list[PageQuality]) -> list["np.ndarray"]:
    return [preprocess_page(p, q) for p, q in zip(pages, qualities)]
