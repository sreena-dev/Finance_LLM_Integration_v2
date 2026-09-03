"""The input-quality gate, run before any OCR.

Spec section 4.3 requires the model to check "PDF text extraction, scanned
pages, rotated pages, missing tables, cropped margins and OCR confidence" and to
request a better source when they fail. This module supplies the half of that
which can be measured from the page image alone, and it runs *first* so a
hopeless upload is refused in seconds instead of after a ten-minute conversion.

Every measurement here answers a defect actually present in the sample corpus:

``skew_deg``
    ``MH 2022-23 SFS`` page 3 (Statement of Changes in Equity) is rotated by
    roughly a degree. Over an A4 width that displaces a row by more than a text
    line's height, and the visible consequence on that page is row labels
    sitting beside the *wrong* numbers. A table extractor will bind them that
    way and report no error. This is the single most dangerous defect in the
    corpus because it produces confident, wrong figures.

``contrast``
    The Xerox VersaLink scans (``OD 2021-22``) have whole paragraphs rendered in
    washed-out grey. Below a certain spread, binarisation drops them entirely
    and the text is not so much misread as absent.

``colour == "bilevel"``
    ``MH 2020-21`` is CCITT G4 -- one bit per pixel, hard-thresholded at scan
    time. The greys that OCR uses to reconstruct thin strokes were discarded
    before we ever saw the file, and no amount of preprocessing brings them
    back.

``duplicate_of``
    The ``IARSFS`` files re-contain the ``SFS`` -- for the OD entity the two are
    byte-identical in size. Uploading both duplicates every table, and a
    duplicated statement silently doubles anything that sums across pages.

180-degree rotation is deliberately *not* guessed here. Distinguishing upright
from upside-down text from pixel statistics alone is unreliable, and a wrong
guess flips a page that was fine. It is left to be caught downstream, where OCR
confidence collapsing on an otherwise clean page is unambiguous evidence.
"""

from __future__ import annotations

import logging

from .config import Config
from .models import PageDefect, PageQuality
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


# Below this fraction of dark pixels the page carries no content worth OCR'ing.
_BLANK_INK_THRESHOLD = 0.002

# ---------------------------------------------------------------------------
# Thresholds below are CALIBRATED, not guessed. Measured over the sample corpus
# in data/ and against synthetic degradations of a known-good page
# (MH 2022-23 SFS p.4, Gaussian blur / downsample / alpha-fade):
#
#   metric      clean corpus pages     first visibly degraded    unusable
#   contrast    0.72 - 0.98            0.49  (50% ink fade)      0.15
#   blur        2,700 - 4,900          600   (3px Gaussian)      44
#
# The corpus spread is itself informative: the MH pages sit at 0.98 contrast and
# the washed-out Xerox VersaLink scans (OD 2021-22) at 0.72-0.80, which is the
# ranking those two sets deserve.
#
# Both are one-sided. A page can score well here and still OCR badly -- these
# say "this will be hard", never "this will be fine".
# ---------------------------------------------------------------------------
CONTRAST_WARN, CONTRAST_ERROR = 0.55, 0.30
BLUR_WARN, BLUR_ERROR = 1200.0, 400.0
# Hamming distance between two 64-bit dHashes below which two pages are the same
# scan. Not zero: re-scanning or re-compressing the same sheet perturbs a few
# bits, and the IARSFS/SFS pair are separate JPEG encodes of one original.
_DUPLICATE_HAMMING = 6


def _binarise(img: "np.ndarray") -> "np.ndarray":
    """Ink mask, True where there is ink.

    Otsu rather than a fixed threshold: the corpus spans a hard-thresholded
    bilevel fax and a washed-out grey Xerox scan, and no single cut serves both.
    """
    if cv2 is not None:
        _, mask = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        return mask > 0
    # Otsu by hand, so precheck still runs in an environment without OpenCV.
    hist = np.bincount(img.ravel(), minlength=256).astype(np.float64)
    total = hist.sum()
    omega = np.cumsum(hist) / total
    mu = np.cumsum(hist * np.arange(256)) / total
    mu_t = mu[-1]
    denom = omega * (1.0 - omega)
    denom[denom == 0] = np.nan
    sigma_b = (mu_t * omega - mu) ** 2 / denom
    cut = int(np.nanargmax(sigma_b))
    return img <= cut


def _estimate_skew(mask: "np.ndarray") -> float:
    """Skew angle in degrees, positive meaning the page leans clockwise.

    Projection-profile method: rotate a downsampled ink mask through candidate
    angles and keep the one whose row-sum profile has the highest variance.
    When the text lines are level, every row is either dense with ink or empty,
    which is exactly what maximum variance means.

    Chosen over a Hough transform because ruled financial tables are full of
    long horizontal rules, and Hough locks onto those rather than onto the text
    baselines. On a page whose *rules* are level but whose printed text is not
    -- a photocopy of a skewed original -- that is the wrong answer, and it is
    the text baselines that OCR cares about.
    """
    # Downsample hard: skew is a global property and the search is O(angles x
    # pixels). A 600px-wide proxy resolves well under a tenth of a degree.
    step = max(1, mask.shape[1] // 600)
    small = mask[::step, ::step].astype(np.float32)
    if small.sum() < 50:
        return 0.0

    h, w = small.shape
    ys, xs = np.nonzero(small)
    cy, cx = h / 2.0, w / 2.0
    ys = ys - cy
    xs = xs - cx

    best_angle, best_score = 0.0, -1.0
    # +/-6 degrees covers hand-fed flatbed and sheet-feeder skew. Anything worse
    # is a mis-oriented page, not skew, and is reported rather than corrected.
    for angle in np.arange(-6.0, 6.0001, 0.1):
        theta = np.deg2rad(angle)
        # Only the rotated y matters -- the profile is over rows.
        yr = xs * np.sin(theta) + ys * np.cos(theta)
        rows = np.bincount((yr - yr.min()).astype(np.int32), minlength=1).astype(np.float64)
        score = float(rows.var())
        if score > best_score:
            best_score, best_angle = score, float(angle)
    return round(best_angle, 2)


def _blur(img: "np.ndarray") -> float:
    """Variance of the Laplacian; higher is sharper."""
    if cv2 is not None:
        return float(cv2.Laplacian(img, cv2.CV_64F).var())
    a = img.astype(np.float64)
    lap = (
        -4 * a
        + np.roll(a, 1, 0) + np.roll(a, -1, 0)
        + np.roll(a, 1, 1) + np.roll(a, -1, 1)
    )[1:-1, 1:-1]
    return float(lap.var())


def _contrast(img: "np.ndarray", mask: "np.ndarray") -> float:
    """Separation between the ink and the paper it sits on, normalised to 0..1.

    This is deliberately measured *through the ink mask* rather than as a plain
    percentile spread over the whole page. A financial statement is typically
    95%+ paper, so a 5th-to-95th percentile spread has both ends landing in the
    background and reports a perfectly crisp page as having almost no contrast
    -- which is what it did for ``MH 2022-23 SFS`` page 10 on the first run
    here. The number that matters to OCR is how far a stroke sits from the sheet
    behind it, and that is independent of how much text the page carries.

    Paper is taken at the 90th percentile of non-ink pixels and ink at the 25th
    of ink pixels: both away from the extremes, so a dust speck or a blown
    highlight moves neither.
    """
    ink = img[mask]
    paper = img[~mask]
    if ink.size < 50 or paper.size < 50:
        return 0.0
    ink_level = float(np.percentile(ink, 25))
    paper_level = float(np.percentile(paper, 90))
    return float(max(0.0, (paper_level - ink_level) / 255.0))


def _dhash(img: "np.ndarray") -> int:
    """64-bit difference hash, for spotting the same sheet scanned twice."""
    if cv2 is not None:
        small = cv2.resize(img, (9, 8), interpolation=cv2.INTER_AREA)
    else:
        ys = np.linspace(0, img.shape[0] - 1, 8).astype(int)
        xs = np.linspace(0, img.shape[1] - 1, 9).astype(int)
        small = img[np.ix_(ys, xs)]
    diff = small[:, 1:] > small[:, :-1]
    bits = 0
    for bit in diff.ravel():
        bits = (bits << 1) | int(bit)
    return bits


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def check_page(page: RenderedPage) -> PageQuality:
    """Measure one page and attach every defect found to its ``PageQuality``."""
    q = page.quality
    img = page.image

    mask = _binarise(img)
    q.ink_coverage = round(float(mask.mean()), 5)
    q.contrast = round(_contrast(img, mask), 4)
    q.blur = round(_blur(img), 2)
    q.is_blank = q.ink_coverage < _BLANK_INK_THRESHOLD

    if q.is_blank:
        q.defects.append(PageDefect(
            "blank_page",
            f"Page {q.page_no} is blank or near-blank "
            f"({q.ink_coverage:.4%} ink coverage) and was not analysed.",
        ))
        q.skew_deg = 0.0
        return q

    q.skew_deg = _estimate_skew(mask)

    if q.native_dpi is not None and q.native_dpi < Config.MIN_ACCEPTABLE_DPI:
        q.defects.append(PageDefect(
            "low_resolution",
            f"Page {q.page_no} was scanned at about {q.native_dpi:.0f} DPI. "
            f"Below {Config.MIN_ACCEPTABLE_DPI} DPI, small table digits are "
            "frequently unrecoverable; figures read from this page need "
            "verification against the original.",
            severity="error",
        ))
    elif q.native_dpi is not None and q.native_dpi < 250:
        q.defects.append(PageDefect(
            "modest_resolution",
            f"Page {q.page_no} was scanned at about {q.native_dpi:.0f} DPI, "
            "below the 300 DPI that OCR is tuned for.",
        ))

    if q.colour == "bilevel":
        q.defects.append(PageDefect(
            "bilevel_scan",
            f"Page {q.page_no} is a 1-bit black-and-white scan. Thin strokes "
            "were discarded when it was scanned and cannot be recovered, so "
            "digits such as 3/8, 5/6 and 1/7 are more likely to be confused.",
        ))

    if abs(q.skew_deg or 0.0) > Config.DESKEW_MAX_ANGLE:
        q.defects.append(PageDefect(
            "severe_skew",
            f"Page {q.page_no} is rotated by about {q.skew_deg:.1f} degrees, "
            "which is past what deskewing corrects reliably. Table rows on this "
            "page may be matched to the wrong labels.",
            severity="error",
        ))
    elif abs(q.skew_deg or 0.0) >= Config.DESKEW_MIN_ANGLE:
        q.defects.append(PageDefect(
            "skew_corrected",
            f"Page {q.page_no} was rotated by about {q.skew_deg:.1f} degrees "
            "and has been straightened before reading.",
        ))

    if q.contrast is not None and q.contrast < CONTRAST_WARN:
        q.defects.append(PageDefect(
            "low_contrast",
            f"Page {q.page_no} is faint: its print sits only "
            f"{q.contrast:.0%} of the way from the paper behind it. Lighter "
            "lines may be dropped entirely rather than misread, so an absent "
            "row on this page is not evidence that the filing omitted it.",
            severity="error" if q.contrast < CONTRAST_ERROR else "warn",
        ))

    if q.blur is not None and q.blur < BLUR_WARN:
        q.defects.append(PageDefect(
            "blurred",
            f"Page {q.page_no} is soft or out of focus (sharpness {q.blur:.0f} "
            f"against {BLUR_WARN:.0f} for a sharp scan). Adjacent digits may "
            "merge.",
            severity="error" if q.blur < BLUR_ERROR else "warn",
        ))

    if q.height_px and q.width_px and q.width_px > q.height_px:
        q.defects.append(PageDefect(
            "landscape_page",
            f"Page {q.page_no} is landscape. If it holds a portrait table "
            "rotated sideways, column order may be misread.",
        ))

    return q


def check_document(pages: list[RenderedPage]) -> list[PageQuality]:
    """Run every per-page check, then the cross-page duplicate check."""
    qualities = [check_page(p) for p in pages]

    hashes: list[tuple[int, int]] = []
    for page, q in zip(pages, qualities):
        if q.is_blank:
            continue
        h = _dhash(page.image)
        for prev_no, prev_h in hashes:
            if _hamming(h, prev_h) <= _DUPLICATE_HAMMING:
                q.duplicate_of = prev_no
                q.defects.append(PageDefect(
                    "duplicate_page",
                    f"Page {q.page_no} is the same sheet as page {prev_no}. It "
                    "was read once; totals are not counted twice.",
                ))
                break
        else:
            hashes.append((q.page_no, h))

    return qualities
