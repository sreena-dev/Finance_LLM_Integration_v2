"""Rasterising a PDF and reading what kind of PDF it is.

Docling would happily rasterise these pages itself. This stage exists anyway,
ahead of it, because ``preprocess.py`` has to deskew and normalise the page
*before* any OCR sees it, and docling exposes no hook between "load the page"
and "run OCR on it". So the page image is produced here, corrected there, and
handed to docling as an image rather than as a PDF.

The second job is establishing what the file actually is. Measured across the
sample corpus, every standalone financial statement is a pure scan:

    MH 2020-21 SFS     17pp   CCITT G4, 1-bit bilevel, 200 DPI, 0 fonts
    MH 2021-25 SFS     21-31pp JPEG, 8-bit RGB, 200 DPI, 0 fonts
    OD 2021-23 SFS     24-26pp Xerox VersaLink JPEG, 200 DPI
    OD 2023-24 SFS     35pp   JPEG 200 DPI, two landscape-rotated pages

so ``has_text_layer`` is false throughout in the sample corpus.

The pipeline still checks rather than assumes, but not to skip OCR: it never
does, on any document -- see the comment on ``do_ocr`` in
``convert.py::_pipeline_options`` for why a rasterise-then-reassemble pipeline
has no path that ever benefits from skipping it, and how a born-digital PDF
that reached this fork once (silently) lost every figure it had. What this
still buys is an honest note ("this document's own text layer went unused")
and a signal a later phase could act on by reading the source PDF's text
directly instead of rasterising it away, which this stage does not attempt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import Config
from .models import PageQuality

logger = logging.getLogger(__name__)

try:  # pragma: no cover - import guard
    import pypdfium2 as pdfium
except ImportError:  # pragma: no cover
    pdfium = None

try:  # pragma: no cover
    import numpy as np
except ImportError:  # pragma: no cover
    np = None


class RenderError(RuntimeError):
    """Raised when the file cannot be opened as a PDF at all."""


@dataclass
class RenderedPage:
    page_no: int          # 1-based
    image: "np.ndarray"   # greyscale uint8, H x W, at Config.RENDER_DPI
    quality: PageQuality


def _require_deps() -> None:
    missing = [n for n, m in (("pypdfium2", pdfium), ("numpy", np)) if m is None]
    if missing:
        raise RenderError(
            "The ingestion service is missing " + ", ".join(missing) + ". "
            "Install ingestion/requirements.txt."
        )


def _native_image_facts(page) -> tuple[float | None, str]:
    """Native DPI and colour depth of the page's own embedded scan.

    A scanned page is one full-page image object, so its pixel dimensions
    divided by the page's size in inches *is* the scan resolution -- which is
    the number that decides whether OCR has anything to work with. The rendered
    bitmap cannot tell you this: render a 200 DPI scan at 300 and you get a 300
    DPI bitmap of 200 DPI information.

    Returns ``(None, "unknown")`` for a page with no single dominant image,
    which is the born-digital case.
    """
    try:
        width_pt, height_pt = page.get_size()
    except Exception:
        return None, "unknown"
    if not width_pt:
        return None, "unknown"

    best = None
    for obj in page.get_objects():
        # FPDF_PAGEOBJ_IMAGE == 3. Compared numerically because the constant's
        # import path has moved between pypdfium2 majors.
        if getattr(obj, "type", None) != 3:
            continue
        try:
            meta = obj.get_metadata()
        except Exception:
            continue
        area = getattr(meta, "width", 0) * getattr(meta, "height", 0)
        if best is None or area > best[0]:
            best = (area, meta)

    if best is None:
        return None, "unknown"

    meta = best[1]
    px_w = getattr(meta, "width", 0)
    bpp = getattr(meta, "bits_per_pixel", 0)
    dpi = (px_w / (width_pt / 72.0)) if px_w and width_pt else None

    if bpp <= 1:
        colour = "bilevel"
    elif bpp <= 8:
        colour = "grey"
    else:
        colour = "colour"
    return dpi, colour


def render(data: bytes) -> list[RenderedPage]:
    """Rasterise every page to greyscale at ``Config.RENDER_DPI``.

    Greyscale rather than colour because every downstream consumer -- skew
    estimation, contrast measurement, binarisation, OCR -- works on luminance,
    and carrying three identical channels through the preprocessing chain
    triples the memory for no information. The one thing colour would buy is
    distinguishing a blue signature stamp from black print, and that is handled
    later by layout, not by hue.
    """
    _require_deps()

    try:
        pdf = pdfium.PdfDocument(data)
    except Exception as exc:
        raise RenderError(f"Not a readable PDF: {exc}") from exc

    n_pages = len(pdf)
    if n_pages == 0:
        raise RenderError("The PDF contains no pages.")
    if n_pages > Config.MAX_PAGES:
        raise RenderError(
            f"The PDF has {n_pages} pages; this service accepts at most "
            f"{Config.MAX_PAGES}. Split it and upload the financial statements alone."
        )

    scale = Config.RENDER_DPI / 72.0
    out: list[RenderedPage] = []

    for i in range(n_pages):
        page = pdf[i]
        page_no = i + 1

        # A text layer is what makes OCR skippable. count_chars() is cheap and
        # exact; a heuristic over font objects is neither (the OD 2023-24 file
        # carries 66 font objects and still has no extractable page text,
        # because the fonts belong to a merged digital cover sheet).
        try:
            n_chars = page.get_textpage().count_chars()
        except Exception:
            n_chars = 0

        try:
            rotation = int(page.get_rotation() or 0)
        except Exception:
            rotation = 0

        native_dpi, colour = _native_image_facts(page)

        bitmap = page.render(scale=scale, grayscale=True)
        arr = bitmap.to_numpy()
        if arr.ndim == 3:
            arr = arr[:, :, 0]
        arr = np.ascontiguousarray(arr, dtype=np.uint8)

        quality = PageQuality(
            page_no=page_no,
            width_px=int(arr.shape[1]),
            height_px=int(arr.shape[0]),
            native_dpi=round(native_dpi, 1) if native_dpi else None,
            colour=colour,
            has_text_layer=n_chars > 20,
            rotation=rotation,
        )
        out.append(RenderedPage(page_no=page_no, image=arr, quality=quality))

    return out


def document_has_text_layer(pages: list[RenderedPage]) -> bool:
    """True only if a real majority of pages carry extractable text.

    Not ``any()``: a scanned filing with one born-digital cover page would then
    route down the no-OCR path and come back almost empty.
    """
    if not pages:
        return False
    with_text = sum(1 for p in pages if p.quality.has_text_layer)
    return with_text >= max(1, int(0.6 * len(pages)))
