"""Docling for text and layout: what is on the page, and where the tables are.

Docling's job here is deliberately narrow. It reads the *text* (headings,
paragraphs, lists -- what identification and the notes are built from) and it
*locates* tables. It does not read table cells: TableFormer is off. Table
contents come from OCR word boxes (`ocr.py`) organised by `structure.py`, and
a table's digits never pass through docling's grid.

Pages are analysed one at a time by a single engine guarded by a lock. Two
docling conversions at once run the machine out of memory (measured:
``bad_alloc``, and worse, valid-looking but corrupted output), so layout stays
single-file; the pipeline overlaps it with OCR and model calls for pages
already past it.

Coordinates: docling reports PDF points, and boxes for the synthesised PDF
are BOTTOM-LEFT origin. Everything leaving this module is in **pixels of the
preprocessed page image, origin top-left** -- the space OCR, structure and
second read all work in. `to_pixel_box` is the one place that conversion
happens, and it is tested, because a wrong conversion once produced a crop
covering 21% of the page with none of the table in it.
"""

from __future__ import annotations

import html
import io
import logging
import re
import threading
from dataclasses import dataclass, field

from .config import Config
from .normalize import looks_numeric
from .tabletypes import Box, TableRegion

logger = logging.getLogger(__name__)

try:  # pragma: no cover - import guard
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.datamodel.base_models import DocumentStream, InputFormat
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions,
        RapidOcrOptions,
        TableFormerMode,
        TableStructureOptions,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption
    DOCLING_AVAILABLE = True
except ImportError:  # pragma: no cover
    DOCLING_AVAILABLE = False


class LayoutError(RuntimeError):
    pass


@dataclass
class TextItem:
    text: str
    bbox: Box            # page pixels, top-left origin
    label: str = "text"  # docling's label: text, section_header, list_item, ...
    level: int = 1


@dataclass
class PageLayout:
    page_no: int
    markdown: str
    regions: list[TableRegion] = field(default_factory=list)
    items: list[TextItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# coordinates
# ---------------------------------------------------------------------------

def to_pixel_box(l: float, t: float, r: float, b: float, origin: str,
                 page_w_pt: float, page_h_pt: float, img_w: int, img_h: int) -> Box | None:
    """A docling box (points) as page pixels, top-left origin, or None.

    Both conversions are mandatory: points -> pixels (each axis scaled by the
    image's size over the page's size in points) and, for a BOTTOM-LEFT origin,
    the Y flip. Returns None for a box that does not land inside the image with
    a usable size, rather than a clamped guess at what it meant.
    """
    if not page_w_pt or not page_h_pt or not img_w or not img_h:
        return None
    sx, sy = img_w / page_w_pt, img_h / page_h_pt
    if "BOTTOM" in (origin or "").upper():
        y0, y1 = (page_h_pt - max(t, b)) * sy, (page_h_pt - min(t, b)) * sy
    else:
        y0, y1 = min(t, b) * sy, max(t, b) * sy
    x0, x1 = min(l, r) * sx, max(l, r) * sx
    x0, y0 = max(0.0, x0), max(0.0, y0)
    x1, y1 = min(float(img_w), x1), min(float(img_h), y1)
    if x1 - x0 < 20 or y1 - y0 < 15:
        return None
    return (x0, y0, x1, y1)


def _iou(a: Box, b: Box) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _inside(inner: Box, outer: Box, frac: float = 0.8) -> bool:
    ix = max(0.0, min(inner[2], outer[2]) - max(inner[0], outer[0]))
    iy = max(0.0, min(inner[3], outer[3]) - max(inner[1], outer[1]))
    area = max(1e-6, (inner[2] - inner[0]) * (inner[3] - inner[1]))
    return (ix * iy) / area >= frac


def dedupe_regions(regions: list[TableRegion]) -> list[TableRegion]:
    """Drop a region that mostly repeats a larger one on the same page."""
    kept: list[TableRegion] = []
    for region in sorted(regions, key=lambda r: -((r.bbox[2] - r.bbox[0]) * (r.bbox[3] - r.bbox[1]))):
        if any(k.page_no == region.page_no and (_iou(k.bbox, region.bbox) > 0.6 or _inside(region.bbox, k.bbox, 0.9))
               for k in kept):
            continue
        kept.append(region)
    return kept


# ---------------------------------------------------------------------------
# text, titles, unmarked tables (pure functions over TextItems)
# ---------------------------------------------------------------------------

_CAPTION_LABELS = {"section_header", "title", "caption", "text"}


def page_markdown(items: list[TextItem]) -> str:
    """Reading-order markdown: headings as ``##``, list items as ``-``, prose as paragraphs."""
    blocks: list[str] = []
    for it in sorted(items, key=lambda i: (i.bbox[1], i.bbox[0])):
        text = it.text.strip()
        if not text:
            continue
        if it.label in ("section_header", "title"):
            blocks.append("#" * min(6, max(2, it.level + 1)) + " " + text)
        elif it.label == "list_item":
            blocks.append("- " + text)
        else:
            blocks.append(text)
    return "\n\n".join(blocks)


def find_title(region: Box, items: list[TextItem], max_gap_px: float = 260.0) -> str | None:
    """The caption or heading printed just above a table, if there is one."""
    best = None
    for it in items:
        if it.label not in _CAPTION_LABELS or not it.text.strip() or len(it.text) > 160:
            continue
        if it.bbox[3] > region[1] + 4 or region[1] - it.bbox[3] > max_gap_px:
            continue
        if it.bbox[2] < region[0] or it.bbox[0] > region[2]:
            continue
        if looks_numeric(it.text):
            continue
        if best is None or it.bbox[3] > best.bbox[3]:
            best = it
    return best.text.strip() if best is not None else None


_MIN_FIGURES = 6            # enough on their own
_MIN_LABELLED_FIGURES = 4   # enough only if the lines are labelled


def _labelled_rows(block: list[TextItem], loose: list[TextItem], median_h: float) -> int:
    """How many of a block's lines have a text label printed to the left of the figures."""
    by_row: dict[int, list[TextItem]] = {}
    for it in block:
        by_row.setdefault(round(((it.bbox[1] + it.bbox[3]) / 2) / median_h), []).append(it)
    count = 0
    for row, figs in by_row.items():
        left = min(f.bbox[0] for f in figs)
        centre = sum((f.bbox[1] + f.bbox[3]) / 2 for f in figs) / len(figs)
        if any(
            not looks_numeric(t.text) and t.bbox[2] <= left
            and abs((t.bbox[1] + t.bbox[3]) / 2 - centre) <= 0.6 * median_h
            for t in loose
        ):
            count += 1
    return count


def detect_unmarked(items: list[TextItem], regions: list[TableRegion], page_no: int,
                    img_w: int) -> list[TableRegion]:
    """Blocks of figures the layout model did not mark as a table.

    A statement with almost no ruling lines gets no table box; its figures
    still come out of OCR as loose text. A run of six or more figure-shaped
    items on three or more distinct lines, outside every marked region, is
    offered as a table candidate. A small company's statement can have only a
    handful of amounts, so four or five are enough when at least three of the
    lines also carry a text label to the left of the figures -- which is what
    tells a statement from a page number and a couple of years in the margin.
    The box covers the figures and the labels printed to their left.
    """
    loose = [
        it for it in items
        if not any(_inside(it.bbox, r.bbox, 0.5) for r in regions)
    ]
    figures = [it for it in loose if looks_numeric(it.text) and not re.fullmatch(r"(?:19|20)\d{2}", it.text.strip())]
    if len(figures) < _MIN_LABELLED_FIGURES:
        return []
    heights = sorted(it.bbox[3] - it.bbox[1] for it in figures)
    median_h = heights[len(heights) // 2] or 30.0
    figures.sort(key=lambda it: it.bbox[1])

    blocks: list[list[TextItem]] = [[figures[0]]]
    for it in figures[1:]:
        if it.bbox[1] - blocks[-1][-1].bbox[3] > 6.0 * median_h:
            blocks.append([it])
        else:
            blocks[-1].append(it)

    found: list[TableRegion] = []
    for block in blocks:
        rows = {round(((it.bbox[1] + it.bbox[3]) / 2) / median_h) for it in block}
        if len(rows) < 3:
            continue
        if len(block) < _MIN_FIGURES and _labelled_rows(block, loose, median_h) < 3:
            continue
        if len(block) < _MIN_LABELLED_FIGURES:
            continue
        top = min(it.bbox[1] for it in block) - 3.0 * median_h
        bottom = max(it.bbox[3] for it in block) + 0.5 * median_h
        labels = [
            it for it in loose
            if it.bbox[1] >= top and it.bbox[3] <= bottom and it not in block
        ]
        x0 = min([it.bbox[0] for it in block] + [it.bbox[0] for it in labels])
        x1 = max(it.bbox[2] for it in block)
        found.append(TableRegion(
            page_no=page_no, bbox=(max(0.0, x0 - 10), max(0.0, top), min(float(img_w), x1 + 10), bottom),
            source="unmarked",
        ))
    return found


# ---------------------------------------------------------------------------
# docling
# ---------------------------------------------------------------------------

def _page_pdf(image) -> bytes:
    from PIL import Image

    frame = Image.fromarray(image).convert("L")
    buffer = io.BytesIO()
    frame.save(buffer, format="PDF", resolution=float(Config.RENDER_DPI), quality=95)
    return buffer.getvalue()


def _pipeline_options() -> "PdfPipelineOptions":
    options = PdfPipelineOptions()
    options.do_ocr = True
    # TableFormer is off: table cells are read from OCR word boxes instead.
    options.do_table_structure = Config.LAYOUT_TABLE_STRUCTURE
    if Config.LAYOUT_TABLE_STRUCTURE:
        options.table_structure_options = TableStructureOptions(
            mode=TableFormerMode.FAST, do_cell_matching=True,
        )
    options.ocr_options = RapidOcrOptions(
        lang=Config.OCR_LANG,
        force_full_page_ocr=True,
        rapidocr_params={"Global.use_cls": Config.OCR_USE_ANGLE_CLASSIFIER},
    )
    options.generate_page_images = False
    options.generate_parsed_pages = False
    artifacts = Config.artifacts_dir()
    if artifacts is not None:
        options.artifacts_path = str(artifacts)
    options.accelerator_options = AcceleratorOptions(
        device=Config.ACCELERATOR_DEVICE, num_threads=Config.NUM_THREADS,
    )
    options.document_timeout = Config.DOCUMENT_TIMEOUT
    return options


def _label_name(item) -> str:
    label = getattr(item, "label", None)
    return str(getattr(label, "value", label) or "text")


def _px_box(item, page_w_pt, page_h_pt, img_w, img_h) -> Box | None:
    prov = (getattr(item, "prov", None) or [None])[0]
    bbox = getattr(prov, "bbox", None) if prov is not None else None
    if bbox is None:
        return None
    origin = str(getattr(getattr(bbox, "coord_origin", None), "name", getattr(bbox, "coord_origin", "")) or "")
    return to_pixel_box(bbox.l, bbox.t, bbox.r, bbox.b, origin, page_w_pt, page_h_pt, img_w, img_h)


class LayoutEngine:
    """One docling converter for the process, used one page at a time."""

    def __init__(self) -> None:
        self._converter = None
        self._build_lock = threading.Lock()
        self._run_lock = threading.Lock()

    def _get(self):
        if self._converter is None:
            with self._build_lock:
                if self._converter is None:
                    self._converter = DocumentConverter(
                        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=_pipeline_options())}
                    )
        return self._converter

    def analyse(self, image, page_no: int) -> PageLayout:
        if not DOCLING_AVAILABLE:
            raise LayoutError(
                "docling is not installed in this service. Install ingestion/requirements.txt."
            )
        img_h, img_w = image.shape[:2]
        pdf = _page_pdf(image)
        with self._run_lock:                       # one conversion at a time, always
            result = self._get().convert(
                DocumentStream(name=f"page{page_no}.pdf", stream=io.BytesIO(pdf)),
                raises_on_error=False,
            )
        status = getattr(getattr(result, "status", None), "name", "")
        document = result.document
        notes: list[str] = []
        if status and status not in ("SUCCESS", ""):
            notes.append(f"Page {page_no}: layout analysis reported {status}; text or tables on it may be missing.")

        pages = getattr(document, "pages", None) or {}
        page = next(iter(pages.values()), None)
        size = getattr(page, "size", None)
        w_pt, h_pt = getattr(size, "width", None), getattr(size, "height", None)
        if not w_pt or not h_pt:
            w_pt, h_pt = img_w * 72.0 / Config.RENDER_DPI, img_h * 72.0 / Config.RENDER_DPI

        items: list[TextItem] = []
        for item in getattr(document, "texts", None) or []:
            label = _label_name(item)
            if label in ("page_header", "page_footer"):
                continue
            box = _px_box(item, w_pt, h_pt, img_w, img_h)
            text = html.unescape(getattr(item, "text", "") or "").strip()
            if box is None or not text:
                continue
            items.append(TextItem(text, box, label, int(getattr(item, "level", 1) or 1)))

        regions: list[TableRegion] = []
        for item in getattr(document, "tables", None) or []:
            box = _px_box(item, w_pt, h_pt, img_w, img_h)
            if box is not None:
                regions.append(TableRegion(page_no=page_no, bbox=box, source="layout"))
        regions = dedupe_regions(regions)

        if Config.DETECT_UNMARKED_TABLES:
            extra = detect_unmarked(items, regions, page_no, img_w)
            if extra:
                notes.append(
                    f"Page {page_no}: {len(extra)} table(s) had no ruling lines for layout analysis to "
                    "mark; they were located from their figures' positions."
                )
            regions = dedupe_regions(regions + extra)

        regions.sort(key=lambda r: (r.bbox[1], r.bbox[0]))
        for n, region in enumerate(regions):
            region.index_on_page = n
            region.title = find_title(region.bbox, items)

        body = [it for it in items if not any(_inside(it.bbox, r.bbox, 0.8) for r in regions)]
        return PageLayout(page_no=page_no, markdown=page_markdown(body), regions=regions, items=body, notes=notes)


ENGINE = LayoutEngine()
