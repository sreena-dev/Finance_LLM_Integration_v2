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
4. **Despeckle**, so it removes noise rather than the artefacts the earlier
   steps introduced.
5. **Sharpen last**, on the cleanest image available -- correcting both the
   scan's own softness and the definition the three resampling steps above
   cost.

Every step is conditional on a measurement from ``precheck``. Nothing is applied
"just in case": each of these is lossy, and applying CLAHE to an already-crisp
page or despeckling one with fine print costs accuracy rather than buying it.

**And every step that can be checked, is.** Deskew and sharpening re-measure
the page afterwards and discard their own result if it did not improve --
because "conditional on a measurement" only ever tested whether a correction
was *warranted*, never whether it *worked*. A deskew that left a page no
straighter, or a sharpen that amplified halftone rather than print, used to be
invisible: the correction was applied, noted as an improvement, and the damage
travelled downstream as fact. A rejected correction is now reported as its own
defect, because a page the pipeline found hard is worth an auditor knowing
about when they weigh a figure taken from it.

PDF-declared page rotation is deliberately not handled here: ``page.render()``
in pdfium already applies the PDF's own ``/Rotate``, so the bitmap arriving in
this module is upright with respect to that metadata. ``PageQuality.rotation``
records what was applied and is informational only.

What metadata cannot tell you is content that was scanned sideways with no
``/Rotate`` to declare it -- a page fed into the scanner rotated a quarter or
half turn, which is a property of the ink on the page, not of the PDF. That
case IS handled, one step before deskew: ``precheck._estimate_orientation``
extends its own proven skew-detection signal one level up (0/90/180/270
instead of +/-6 degrees) and reports the result on ``PageQuality
.orientation_quadrant``; this module applies it with an exact, lossless
``np.rot90`` before the fine deskew search runs, because that search assumes a
roughly-upright starting point. Distinguishing upright from upside-down within
one axis from pixel statistics alone remains unreliable (see ``precheck``'s
own note on 180-degree ambiguity) -- an axis correction can land a page the
right way up 180 degrees out, and that residual is not chased here.
"""

from __future__ import annotations

import logging

from .config import Config
from .models import PageDefect, PageQuality
from .precheck import BLUR_WARN, CONTRAST_WARN, _binarise, _blur, _estimate_skew
from .render import RenderedPage

logger = logging.getLogger(__name__)

#: Unsharp-mask strength and radius. Low on purpose -- see `_sharpen`.
_SHARPEN_AMOUNT = 0.6
_SHARPEN_SIGMA = 1.0

#: A correction has to improve its own measurement by at least this much to be
#: kept. A change smaller than this is noise in the measurement rather than a
#: real gain, and every one of these steps is lossy -- so "no measurable
#: improvement" means "do not pay the cost".
_MIN_GAIN = 1.02

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


def _sharpen(img: "np.ndarray") -> "np.ndarray":
    """Unsharp mask, for a page `precheck` measured as soft.

    **Disabled by default -- it was measured against the accuracy harness and
    it lost.** See ``Config.SHARPEN_ENABLED`` for the numbers. The short
    version: it made every qualifying page measurably sharper and made the
    EXTRACTION measurably worse, because a mask that crispens glyphs also
    rings around table rules, and row-boundary detection is more fragile than
    glyph legibility. Sharper pixels are not the goal; correct figures are.

    Left in place because the idea is sound and only the tuning is disproven.

    ``blur`` has been measured since the first version of this pipeline and
    nothing ever acted on it: it fed the quality score and raised a "page is
    soft or out of focus" note, and then the soft page was sent to OCR exactly
    as it arrived. This is the step that was missing -- and the lesson is that
    the missing step was missing for a reason nobody had written down.

    Deliberately gentle. The defect being corrected is a soft scan, not a
    defocused photograph, and the failure mode of over-sharpening here is
    specific and bad: a strong mask rings around every glyph edge, and on the
    thin CCITT strokes in this corpus the ringing closes the counters of ``6``,
    ``8`` and ``9`` into each other. ``AMOUNT`` is therefore low and the
    Gaussian radius small -- enough to recover stroke definition lost to
    scanning and to the cubic upscale above, not enough to invent edges.

    Whether it actually helped is not assumed: ``preprocess_page`` re-measures
    and discards the result if it did not.
    """
    blurred = cv2.GaussianBlur(img, (0, 0), sigmaX=_SHARPEN_SIGMA)
    return cv2.addWeighted(img, 1.0 + _SHARPEN_AMOUNT, blurred, -_SHARPEN_AMOUNT, 0)


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
    rejected: list[str] = []

    # Gross quadrant correction lands FIRST and is exact (np.rot90, no
    # interpolation) -- deskew's own +/-6 degree search below assumes a
    # roughly-upright starting point, so a page that is actually sideways
    # must be swung onto its axis before the fine search ever runs on it.
    # `orientation_quadrant` is content-inferred (precheck._estimate_orientation),
    # never read from PDF metadata -- see this module's own docstring on why
    # rotation from `/Rotate` is not handled here at all.
    if quality.orientation_quadrant:
        img = np.ascontiguousarray(np.rot90(img, k=quality.orientation_quadrant // 90))
        applied.append(f"rotated {quality.orientation_quadrant} degrees (content was sideways)")

    angle = quality.skew_deg or 0.0
    if (
        Config.DESKEW_ENABLED
        and Config.DESKEW_MIN_ANGLE <= abs(angle) <= Config.DESKEW_MAX_ANGLE
    ):
        candidate = _deskew(img, angle)
        # Re-measure, and keep the rotation only if the page is actually
        # straighter for it. Nothing checked this before, so a deskew that made
        # a page WORSE was invisible -- and this is the correction with the
        # worst failure mode in the module: rotating a level page displaces the
        # end of every row, and a table extractor clustering cells by vertical
        # position then binds a label to the row above or below it and reports
        # no error doing so. Cheap to verify: `_estimate_skew` works on a
        # 600px-wide proxy, and only pages that were actually deskewed pay for it.
        residual = _estimate_skew(_binarise(candidate))
        if abs(residual) < abs(angle):
            img = candidate
            applied.append(f"deskewed by {angle:+.2f} degrees")
        else:
            rejected.append(
                f"a {angle:+.2f} degree deskew was computed but discarded: it "
                f"left the page at {residual:+.2f} degrees, no straighter than "
                "it started"
            )

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

    # Sharpening runs LAST, on the cleanest image available. Every step above
    # is lossy in the same direction -- the cubic deskew and upscale both
    # interpolate, and the median despeckle rounds stroke ends -- so this both
    # corrects the scan's own softness and recovers definition those steps
    # cost. Running it earlier would mean sharpening noise that despeckle then
    # removes, and then softening the result again.
    if (
        Config.SHARPEN_ENABLED
        and quality.blur is not None
        and quality.blur < BLUR_WARN
    ):
        # Measured on the image AS IT ENTERS THIS STEP, not on `quality.blur`.
        # That distinction is the whole correctness of the check: `quality.blur`
        # describes the RAW page, and by this point the image has been through
        # a cubic deskew, a cubic upscale and a median despeckle, each of which
        # lowers Laplacian variance. Comparing a sharpened, fully-processed
        # image against the raw page's score made sharpening unable to win even
        # when it worked -- measured across the corpus, it rejected 23 of 23
        # attempts, with "improvements" reading as 545 -> 84. The gate above
        # still uses quality.blur, because whether the PAGE is soft enough to
        # warrant sharpening is a question about the page; whether the sharpen
        # HELPED is a question about this step.
        before = _blur(img)
        candidate = _sharpen(img)
        after = _blur(candidate)
        # Same discipline as the deskew above: measured, not assumed. An
        # unsharp mask on a page that was soft for a reason other than focus --
        # a halftone background, JPEG texture -- amplifies the texture rather
        # than the print, and the measurement says so.
        if after >= before * _MIN_GAIN:
            img = candidate
            applied.append(f"sharpened (sharpness {before:.0f} to {after:.0f})")
            quality.blur = round(after, 2)
        else:
            rejected.append(
                f"sharpening was tried and discarded: it moved sharpness from "
                f"{before:.0f} to {after:.0f}, which is not an improvement"
            )

    if applied:
        quality.defects.append(PageDefect(
            "preprocessed",
            f"Page {quality.page_no} was corrected before reading: "
            + ", ".join(applied) + ".",
            severity="warn",
        ))
    if rejected:
        # Reported, not silent. A correction that was computed and thrown away
        # is a page the pipeline found hard, and that is worth an auditor
        # knowing when they weigh a figure taken from it.
        quality.defects.append(PageDefect(
            "correction_rejected",
            f"Page {quality.page_no}: " + "; ".join(rejected) + ".",
            severity="warn",
        ))

    return img


def preprocess(pages: list[RenderedPage], qualities: list[PageQuality]) -> list["np.ndarray"]:
    # Independent per page -- each corrects its own bitmap and records onto
    # its own PageQuality -- so run across pages at once. `map` preserves page
    # order, which every caller relies on to zip images back to qualities.
    workers = max(1, min(Config.PAGE_WORKERS, len(pages)))
    if workers == 1:
        return [preprocess_page(p, q) for p, q in zip(pages, qualities)]

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(preprocess_page, pages, qualities))
