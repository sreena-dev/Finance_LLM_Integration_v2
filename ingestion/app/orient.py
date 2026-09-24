"""Turning a sideways page upright before layout and OCR see it.

Wide schedules (a fixed-asset roll-forward, a ten-column note) are often printed
landscape and scanned with the paper turned, so the page arrives with its text
running bottom-to-top while the PDF's own rotation flag says nothing. Layout
analysis and the geometry stage both assume horizontal text; on a sideways page
every "row" is a vertical strip and nothing reads.

The check is cheap and the decision is made on evidence, not a guess:

1. **Is the page sideways?** Text detection alone (no recognition) is run on a
   downscaled copy -- about half a second. Text lines are wide and short; on a
   sideways page most detected boxes are tall and narrow.
2. **Which way?** Only for a page that failed that test, the page is read in both
   turned orientations and the one OCR reads more confidently, and with more
   letters, wins. Measured on a real sideways page: 0.996 mean confidence turned
   the right way, 0.73 turned the wrong way, 0.72 as scanned.

Upside-down pages (a 180-degree turn) leave the text horizontal, so step 1 cannot
see them and they are not handled here.

This module does not touch preprocessing (render, precheck, preprocess); it runs
on the finished page images.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .config import Config

logger = logging.getLogger(__name__)

#: Fraction of detected text boxes that must be tall and narrow for a page to be
#: called sideways. An upright page has none; the sideways page measured had 93%.
TALL_FRACTION_THRESHOLD = 0.6
#: A box narrower than this (width / height) counts as tall.
TALL_ASPECT = 0.7
#: Longest side of the copy used for the checks -- enough to see text lines.
_CHECK_SIDE = 1400

DetectFn = Callable[["np.ndarray"], list[float]]      # image -> width/height of each text box
ScoreFn = Callable[["np.ndarray"], float]              # image -> how well it reads


def tall_fraction(aspects: list[float]) -> float:
    if not aspects:
        return 0.0
    return sum(1 for a in aspects if a < TALL_ASPECT) / len(aspects)


def is_sideways(aspects: list[float], minimum_boxes: int = 12) -> bool:
    """Most of the text is standing up. Too few boxes to judge means "not sure": leave it."""
    return len(aspects) >= minimum_boxes and tall_fraction(aspects) >= TALL_FRACTION_THRESHOLD


def _small(image, side: int = _CHECK_SIDE):
    import cv2

    h, w = image.shape[:2]
    scale = side / max(h, w)
    if scale >= 1:
        return image
    return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)


def detect_aspects(image) -> list[float]:
    """Width/height of every text box OCR's detector finds (detection only, no reading)."""
    from . import ocr

    out = ocr._engine()(_small(image), use_det=True, use_cls=False, use_rec=False)
    boxes = getattr(out, "boxes", None)
    if boxes is None:
        return []
    aspects: list[float] = []
    for box in boxes:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        if w > 4 and h > 4:
            aspects.append(w / h)
    return aspects


def read_score(image) -> float:
    """How well a page reads: mean recognition confidence times the letters recognised.

    Confidence alone can be high on a few lines; letters alone can be high on
    garbage. Their product is high only when a lot of text reads confidently.
    """
    from . import ocr

    out = ocr._engine()(_small(image), use_det=True, use_cls=False, use_rec=True)
    scores = list(getattr(out, "scores", None) or [])
    texts = list(getattr(out, "txts", None) or [])
    if not scores:
        return 0.0
    letters = sum(sum(c.isalpha() for c in t) for t in texts)
    return (sum(scores) / len(scores)) * letters


def choose_turn(image, score: ScoreFn) -> int:
    """Degrees clockwise (90 or 270) that make a sideways page read best."""
    import cv2

    clockwise = score(cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE))
    counter = score(cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE))
    return 90 if clockwise >= counter else 270


def turn(image, degrees_clockwise: int):
    import cv2

    code = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}[degrees_clockwise]
    return cv2.rotate(image, code)


def correct_page(image, detect: DetectFn = detect_aspects, score: ScoreFn = read_score):
    """``(upright image, degrees turned clockwise)``; degrees is 0 when left alone."""
    try:
        if not is_sideways(detect(image)):
            return image, 0
        degrees = choose_turn(image, score)
        return turn(image, degrees), degrees
    except Exception:
        # A page that cannot be checked is used as it is, exactly as before.
        logger.exception("orientation check failed; page left as scanned")
        return image, 0


def correct_pages(
    images: list, detect: DetectFn = detect_aspects, score: ScoreFn = read_score,
) -> tuple[list, dict[int, int]]:
    """Correct every page. Returns the images and ``{list index: degrees turned}``."""
    if not images or not Config.ORIENTATION_CORRECTION:
        return images, {}
    with ThreadPoolExecutor(max(1, Config.PAGE_WORKERS), thread_name_prefix="orient") as pool:
        results = list(pool.map(lambda im: correct_page(im, detect, score), images))
    turned = {i: deg for i, (_, deg) in enumerate(results) if deg}
    return [im for im, _ in results], turned
