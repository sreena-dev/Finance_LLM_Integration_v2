"""Running docling over the corrected pages.

Docling is the structural engine here: layout detection, table structure
(TableFormer), reading order, per-item bounding boxes, and a per-page confidence
report. What it is *not* is a preprocessing stage -- see ``preprocess.py`` for
why that had to be built separately -- and it is not the last word on any
number, which is what ``verify.py`` is for.

**Why the corrected pages are reassembled into a PDF rather than fed as images.**
Docling accepts ``InputFormat.IMAGE``, but one image is one document: page
numbering restarts, reading order is per-page, and a table split across a page
break becomes two unrelated tables. Financial statements split tables across
pages constantly -- the existing corpus tooling has a whole
``_fetch_untitled_continuations`` path for exactly that case. Reassembling the
preprocessed page images into a single PDF keeps docling's own document-level
machinery working, and costs one in-memory re-encode.

**Why ``do_cell_matching`` stays on for scans.** It is tempting to turn it off
when there is no text layer -- there are no PDF text cells to match, after all.
That reasoning is wrong, and it was in this file until a run against two real
scanned pages proved it: TableFormer predicts the *grid*, and cell matching is
what puts text into it. On a scanned page the cells being matched are the OCR
output. With it off, OCR scored 0.99, the layout scored 0.93, both tables were
found with the right column counts and bounding boxes -- and every cell was
empty. See the comment on the option itself.
"""

from __future__ import annotations

import html
import io
import logging
import threading
from dataclasses import dataclass, field

from .config import Config
from .models import PageQuality

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

try:  # pragma: no cover
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

try:  # pragma: no cover
    import numpy as np
except ImportError:  # pragma: no cover
    np = None


class ConvertError(RuntimeError):
    pass


def _clean(text: str | None) -> str | None:
    """Undo docling's markdown HTML-escaping, once, at the source.

    docling's markdown export escapes ``&`` (and ``<``/``>``) the way any
    HTML-safe serializer does, so a scanned heading reading "Income &
    Expenditure Account" comes back as literally ``Income &amp; Expenditure
    Account``. Nothing downstream expects that: identify.py's statement
    classifier, tables.py's caption capture, and every label pattern in
    tools_fs.py all match against ordinary text, an ampersand included, and
    every one of them would need its own fix for the same underlying escaping
    otherwise -- proven concretely when a working `income\\s+(and|&)\\s+
    expenditure` pattern still didn't classify a real Section 8 company's
    Income & Expenditure Account, because the text it was matching against
    was ``&amp;expenditure``, not ``& expenditure``. Cleaned here, once, so
    every consumer of docling's markdown -- identify, tables, emit, verify,
    vlm_read -- sees the same plain text a human reading the page would.
    """
    if text is None:
        return None
    return html.unescape(text)


@dataclass
class TableCellGeom:
    """One TableFormer cell: its text, its place in the grid, and where it sits.

    ``bbox`` is ``(l, t, r, b)`` in page points with a TOP-LEFT origin, whatever
    origin docling reported it in -- normalised once here so nothing downstream
    has to remember that table cells arrive TOPLEFT while the table's own
    provenance box arrives BOTTOMLEFT.
    """

    text: str
    row_start: int
    row_end: int
    col_start: int
    col_end: int
    bbox: tuple[float, float, float, float] | None = None


@dataclass
class OcrLine:
    """One line of text as RapidOCR read it, inside a table's region.

    ``confidence`` is the OCR engine's own recognition score for this line --
    far sharper than the per-page ``ocr_score``, because it can single out the
    one badly read cell on an otherwise clean page. ``bbox`` as above: page
    points, TOP-LEFT origin.
    """

    text: str
    confidence: float | None
    bbox: tuple[float, float, float, float]


@dataclass
class ConvertedTable:
    """One table docling found, before any verification."""

    page_no: int
    markdown: str
    title: str | None = None
    bbox: list[float] | None = None
    confidence: float | None = None
    # Geometry docling computes and this module used to discard. Exporting a
    # table to markdown collapses every cell to pipes, and once
    # `tables.parse_markdown_tables` re-parses that text there is no way to see
    # that four printed labels share one cell while their values sit on the
    # rows beneath -- the defect behind most MISSING figures. Copied out as
    # plain tuples so the ConversionResult (page images, backends) is never
    # retained.
    cells: list[TableCellGeom] = field(default_factory=list)
    ocr_lines: list[OcrLine] = field(default_factory=list)
    page_height_pt: float | None = None


@dataclass
class Converted:
    markdown: str
    tables: list[ConvertedTable] = field(default_factory=list)
    page_markdown: dict[int, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    #: Caveats about how tables were found (rebuilt from positions, or figures
    #: read but never assembled). Reach `quality["notes"]` via pipeline.
    notes: list[str] = field(default_factory=list)


def pages_to_pdf(images: list["np.ndarray"]) -> bytes:
    """Reassemble corrected page images into a single-file PDF.

    JPEG at quality 95 rather than lossless: these are already lossy scans, the
    re-encode is visually transparent at that setting, and a lossless PDF of 35
    A4 pages at 300 DPI runs to hundreds of megabytes, which then has to be held
    in memory alongside the conversion.
    """
    if Image is None:
        raise ConvertError("Pillow is required to reassemble pages. Install ingestion/requirements.txt.")
    if not images:
        raise ConvertError("No pages to convert.")

    frames = [Image.fromarray(img).convert("L") for img in images]
    buffer = io.BytesIO()
    frames[0].save(
        buffer,
        format="PDF",
        save_all=True,
        append_images=frames[1:],
        resolution=float(Config.RENDER_DPI),
        quality=95,
    )
    return buffer.getvalue()


def _pipeline_options() -> "PdfPipelineOptions":
    options = PdfPipelineOptions()
    # ALWAYS on, regardless of whether the SOURCE pdf had a text layer.
    #
    # This used to be conditional on a per-document `needs_ocr` flag, which
    # trusted `render.document_has_text_layer` to say when OCR could safely be
    # skipped. That trust was misplaced: `pages_to_pdf()` above reassembles
    # every page from its RASTERISED bitmap into a JPEG-in-PDF, unconditionally
    # -- there is no code path in this service that ever hands docling the
    # original PDF bytes. The reconstructed file has zero text cells on every
    # page, born-digital source or not, so `do_ocr=False` was never "skip OCR
    # because the text survived" -- it was "skip OCR, and there is nothing else
    # to read". Measured: a 22-page fully born-digital filing
    # (OD-SPSU-SO-032_2023-24_SFS, text layer on 22/22 pages) went through this
    # branch and extracted 0 tables, 0 chunks, 0 notes -- a silent, complete
    # loss presenting as a clean conversion. The same false economy loses
    # whichever pages of a mostly-digital filing happen to be scanned (measured
    # on a 78-page annual report: 4 of 78 pages, silently blank).
    #
    # A rasterised born-digital page still OCRs reliably -- it is a crisp,
    # noise-free 300 DPI render of computer-set type, easier than any real scan
    # in this corpus -- so the cost of running OCR unconditionally is wasted
    # time on pages that did not need it, never wrong output. That is the
    # right trade until a later phase reads native PDF text directly and skips
    # rasterisation for the pages that have it, which needs its own coordinate-
    # space reconciliation (docling's bbox space would no longer match the
    # RENDER_DPI bitmap space the VLM crop and every downstream consumer
    # assume) and is deliberately not bundled into this fix.
    options.do_ocr = True
    options.do_table_structure = True

    options.table_structure_options = TableStructureOptions(
        mode=TableFormerMode.ACCURATE if Config.TABLEFORMER_ACCURATE else TableFormerMode.FAST,
        # ALWAYS on, including for scans. This was originally set to
        # `not needs_ocr`, reasoning that a scan has no PDF text cells to match
        # -- which is true of the *file* and entirely wrong about the pipeline.
        # TableFormer predicts the grid; cell matching is what puts text INTO
        # that grid, and on a scanned page the cells it matches are the OCR
        # output. Measured on `MH 2022-23 SFS` pages 3 and 5 with it off:
        # OCR scored 0.99, layout 0.93, both tables were found with the correct
        # column counts (5 and 9) and correct bounding boxes -- and every single
        # cell came back empty. A confident, well-structured, completely blank
        # table is the worst possible output, because nothing downstream can
        # tell it apart from a filing that discloses nothing.
        do_cell_matching=True,
    )

    # RapidOCR rather than Tesseract: it runs through onnxruntime and needs no
    # system binary, which matters both for the container and because neither
    # tesseract nor poppler is present on the development host.
    #
    # `force_full_page_ocr=True` unconditionally, same reasoning as `do_ocr`
    # above: the reconstructed PDF has no text cells anywhere, born-digital
    # source or not, so `force_full_page_ocr=False` -- "OCR only the regions
    # without a text cell" -- would still OCR every region on every page. It
    # is not a real per-page routing lever in this pipeline; leaving it True
    # says so rather than implying a selectivity that cannot happen.
    options.ocr_options = RapidOcrOptions(
        lang=Config.OCR_LANG,
        force_full_page_ocr=True,
        # OCR is ~95% of conversion time (docling's own profiler on OD pages),
        # and the angle classifier runs on every text box of it. See
        # Config.OCR_USE_ANGLE_CLASSIFIER for the measurement.
        rapidocr_params={"Global.use_cls": Config.OCR_USE_ANGLE_CLASSIFIER},
    )

    # Needed for the VLM second read and for the citation viewer's page snippet.
    options.generate_page_images = True
    # Docling disables this after assembly unless it is asked for explicitly,
    # and it is what preserves cell-level provenance.
    options.generate_parsed_pages = True
    options.images_scale = 2.0

    artifacts = Config.artifacts_dir()
    if artifacts is not None:
        # Points at weights baked into the image. Without it docling reaches
        # Hugging Face on first use: a multi-minute stall, and an outright
        # failure on an air-gapped host.
        options.artifacts_path = str(artifacts)

    # Set explicitly rather than left at docling's `auto`, so the log line says
    # what was asked for and not only what was chosen. `auto` silently falls
    # back to CPU when torch cannot see a GPU -- which is correct behaviour and
    # also exactly how a GPU machine ends up doing layout on the CPU unnoticed.
    options.accelerator_options = AcceleratorOptions(
        device=Config.ACCELERATOR_DEVICE,
        num_threads=Config.NUM_THREADS,
    )

    options.document_timeout = Config.DOCUMENT_TIMEOUT
    return options


#: One converter for the process, built on first use.
#:
#: `DocumentConverter` owns the layout and TableFormer models. Constructing it
#: per call re-instantiated those models for every document -- pure repeated
#: setup, since the options never vary between jobs. Built lazily rather than
#: at import so the service still starts (and `/health` still answers) when
#: docling's models are missing or slow to load; the cost lands on the first
#: conversion, which is already a minutes-long operation.
#:
#: Guarded by a lock because two uploads can arrive together --
#: MAX_CONCURRENT_JOBS is 2 -- and building this twice concurrently would load
#: two copies of the models into a container sized for one.
_CONVERTER = None
_CONVERTER_LOCK = threading.Lock()


def _converter() -> "DocumentConverter":
    global _CONVERTER
    if _CONVERTER is not None:
        return _CONVERTER
    with _CONVERTER_LOCK:
        if _CONVERTER is None:
            _CONVERTER = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(
                        pipeline_options=_pipeline_options()
                    )
                }
            )
    return _CONVERTER


def convert(images: list["np.ndarray"], qualities: list[PageQuality]) -> Converted:
    """Convert the corrected pages and fold docling's confidence into ``qualities``.

    Always OCRs -- see the comment on ``do_ocr`` in ``_pipeline_options`` for
    why there is no longer a per-document choice here. A caller that measured
    a source PDF's own text layer (``render.document_has_text_layer``) may
    still use that for what it is honestly good for: describing the document
    in a user-facing note. It plays no part in this function.
    """
    if not DOCLING_AVAILABLE:
        raise ConvertError(
            "docling is not installed in this service. Install ingestion/requirements.txt "
            "(docling==2.123.1 with the format-pdf, models-local and feat-ocr-rapidocr extras)."
        )

    pdf_bytes = pages_to_pdf(images)
    converter = _converter()

    stream = DocumentStream(name="upload.pdf", stream=io.BytesIO(pdf_bytes))
    result = converter.convert(stream, raises_on_error=False)

    errors = _collapse_errors(
        [str(getattr(e, "error_message", e)) for e in (getattr(result, "errors", None) or [])]
    )

    # A partial conversion still returns a document, and that document is
    # missing pages. Reporting it as a clean result would let the model treat an
    # absent disclosure as evidence that the entity did not disclose it -- which
    # is precisely the inference prompt rule 16 forbids. So the status is
    # surfaced as an error line rather than being swallowed.
    status = getattr(result, "status", None)
    status_name = getattr(status, "name", str(status) if status else "")
    if status_name and status_name not in ("SUCCESS", ""):
        errors.append(
            f"Docling reported {status_name} for this document. Some pages were "
            "not converted, so an absent table or note here is not evidence "
            "that the filing omitted it."
        )
    if status_name in ("FAILURE", "SKIPPED"):
        raise ConvertError(
            "The document could not be converted"
            + (f": {errors[0]}" if errors else ".")
        )

    _absorb_confidence(result, qualities)

    document = result.document
    missing = _unconverted_pages(document, qualities)
    if missing:
        errors.append(missing)
    converted = Converted(markdown=_clean(document.export_to_markdown()), errors=errors)

    for page_no, page_doc in _iter_pages(document):
        try:
            converted.page_markdown[page_no] = page_doc
        except Exception:  # pragma: no cover - defensive
            continue

    tables = _extract_tables(document, _page_geometry(result))
    # Repair each table's row grid from OCR geometry where TableFormer's own
    # grid disagrees with itself across columns -- see structure_repair.py.
    # A no-op whenever there is nothing to repair or the repair does not
    # foot better than the original; every table not touched comes back
    # byte-identical.
    from . import structure_repair
    converted.tables = [structure_repair.repair_from_geometry(t) for t in tables]
    if Config.SYNTHESIZE_MISSED_TABLES:
        extra, notes = _missed_tables(document, _page_geometry(result), converted.tables)
        converted.tables.extend(extra)
        converted.notes.extend(notes)
    return converted


def _collapse_errors(errors: list[str]) -> list[str]:
    """One line per distinct message, with its count.

    Docling reports a timeout once per unit of work it abandoned: 43 identical
    "document timeout exceeded" lines on one 120-page filing, each shown to the
    user as its own notice. Order of first appearance is kept.
    """
    counts: dict[str, int] = {}
    for message in errors:
        counts[message] = counts.get(message, 0) + 1
    return [m if n == 1 else f"{m} (x{n})" for m, n in counts.items()]


def _page_ranges(numbers: list[int]) -> str:
    out, start, prev = [], None, None
    for n in sorted(numbers):
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append((start, prev))
            start = prev = n
    if start is not None:
        out.append((start, prev))
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in out)


def _unconverted_pages(document, qualities) -> str | None:
    """Name the pages docling handed back with nothing at all on them.

    A partial conversion (timeout, a crashed page) otherwise shows only as
    generic docling errors, and every table and note on the missing pages is
    silently absent. Positional, like `_absorb_confidence`: docling's n-th page
    is qualities[n-1], and only the original page number means anything to the
    reader.
    """
    if not qualities:
        return None
    seen: set[int] = set()
    for item in list(getattr(document, "texts", None) or []) + list(getattr(document, "tables", None) or []):
        for prov in getattr(item, "prov", None) or []:
            page_no = getattr(prov, "page_no", None)
            if page_no:
                seen.add(int(page_no))
    pages = getattr(document, "pages", None) or {}
    base = min((int(p) for p in pages), default=1)
    empty = [
        qualities[i].page_no for i in range(len(qualities))
        if (i + base) not in seen and not qualities[i].is_blank
    ]
    if not empty:
        return None
    return (
        f"{len(empty)} page(s) produced nothing and were NOT converted (page(s) "
        f"{_page_ranges(empty)}). The document was cut short or those pages failed; "
        "tables, notes and figures on them are absent from this extraction, which is "
        "not evidence that the filing omitted them. Re-upload those pages on their own."
    )


def _absorb_confidence(result, qualities: list[PageQuality]) -> None:
    """Copy docling's per-page scores onto our own page records.

    Reported alongside the preprocessing scores rather than instead of them:
    docling grades how well *it* did, and the preprocessing scores grade what it
    had to work with. A page can score well on one and badly on the other, and
    which of the two is low tells the auditor whether to ask for a better scan
    or to check the extraction.
    """
    report = getattr(result, "confidence", None)
    if report is None:
        return
    pages = getattr(report, "pages", None) or {}
    if not pages or not qualities:
        return

    # Mapped POSITIONALLY, not by page number. `qualities` is the exact
    # sequence of pages handed to docling -- pipeline.run drops blank and
    # duplicate pages before conversion while these records keep their original
    # page_no -- so docling's n-th page is qualities[n], and the two numbering
    # schemes need not agree at all. The previous code guessed with
    # `by_number.get(page_no) or by_number.get(page_no + 1)`, which silently
    # attributed one page's OCR confidence to another as soon as any page was
    # dropped: the wrong-but-present key always won.
    ordered = sorted(pages.items(), key=lambda kv: int(kv[0]))
    base = int(ordered[0][0])          # docling is 0-based on some builds, 1-based on others
    for page_no, scores in ordered:
        index = int(page_no) - base
        if not 0 <= index < len(qualities):
            continue
        quality = qualities[index]
        for attr in ("ocr_score", "layout_score", "table_score", "parse_score"):
            value = getattr(scores, attr, None)
            # docling uses NaN, not None, for "not measured".
            if value is not None and value == value:
                setattr(quality, attr, round(float(value), 4))


def _iter_pages(document):
    """``(page_no, markdown)`` per page, where docling can give it.

    Page-scoped export moved between docling 2.x minors, so this tries the
    current keyword and degrades to nothing rather than failing the conversion:
    the per-page markdown is a convenience for citation display, and the
    document-level markdown and the table list are what the pipeline needs.
    """
    pages = getattr(document, "pages", None) or {}
    for page_no in sorted(pages):
        try:
            yield int(page_no), _clean(document.export_to_markdown(page_no=int(page_no)))
        except TypeError:
            return
        except Exception:
            continue


def _topleft_box(l, t, r, b, origin, page_height) -> tuple[float, float, float, float] | None:
    """``(l, t, r, b)`` in TOP-LEFT page points, whatever origin it arrived in.

    Docling reports table cells and OCR lines TOPLEFT but a table's provenance
    box BOTTOMLEFT (see `_provenance`). A BOTTOMLEFT box cannot be flipped
    without the page height, so it comes back ``None`` rather than as a box in
    the wrong space -- the same refusal `vlm_read.crop` makes for the same
    reason.
    """
    name = str(getattr(origin, "name", origin) or "").upper()
    if "BOTTOM" in name:
        if not page_height:
            return None
        t, b = page_height - t, page_height - b
    return (float(min(l, r)), float(min(t, b)), float(max(l, r)), float(max(t, b)))


def _line_unit():
    """docling's LINE cell unit, or a stand-in when docling is not installed."""
    try:
        from docling_core.types.doc.page import TextCellUnit
        return TextCellUnit.LINE
    except Exception:
        return "LINE"


def _page_geometry(result) -> dict[int, tuple]:
    """``page_no -> (parsed_page, page_height_pt)`` for every converted page.

    ``parsed_page`` survives conversion only because `_pipeline_options` sets
    `generate_parsed_pages=True`; its `textline_cells` are the RapidOCR lines.
    """
    out: dict[int, tuple] = {}
    for page in getattr(result, "pages", None) or []:
        try:
            parsed = getattr(page, "parsed_page", None)
            size = getattr(page, "size", None)
            height = getattr(size, "height", None) if size is not None else None
            if height is None and parsed is not None:
                dimension = getattr(parsed, "dimension", None)
                height = getattr(dimension, "height", None) if dimension is not None else None
            out[int(page.page_no)] = (parsed, float(height) if height else None)
        except Exception:
            continue
    return out


def _table_geometry(item, page_no, page_geometry) -> tuple[list[TableCellGeom], list[OcrLine], float | None]:
    """Cell grid and OCR lines for one table, or empty lists on any failure.

    Degrades to nothing rather than failing the conversion: geometry is an
    input to repair, and a table without it is exactly the table this pipeline
    produced before geometry was carried at all.
    """
    parsed, height = (page_geometry or {}).get(page_no, (None, None))

    cells: list[TableCellGeom] = []
    try:
        for cell in getattr(getattr(item, "data", None), "table_cells", None) or []:
            box = None
            bbox = getattr(cell, "bbox", None)
            if bbox is not None:
                box = _topleft_box(bbox.l, bbox.t, bbox.r, bbox.b,
                                   getattr(bbox, "coord_origin", None), height)
            cells.append(TableCellGeom(
                text=_clean(getattr(cell, "text", "")) or "",
                row_start=int(cell.start_row_offset_idx),
                row_end=int(cell.end_row_offset_idx),
                col_start=int(cell.start_col_offset_idx),
                col_end=int(cell.end_col_offset_idx),
                bbox=box,
            ))
    except Exception:
        cells = []

    lines: list[OcrLine] = []
    provenance = (getattr(item, "prov", None) or [None])[0]
    table_bbox = getattr(provenance, "bbox", None) if provenance is not None else None
    if parsed is not None and table_bbox is not None:
        try:
            # get_cells_in_bbox reconciles the line and table origins itself.
            for line in parsed.get_cells_in_bbox(_line_unit(), table_bbox, ios=0.8):
                bb = line.to_bounding_box()
                box = _topleft_box(bb.l, bb.t, bb.r, bb.b, getattr(bb, "coord_origin", None), height)
                if box is None:
                    continue
                confidence = getattr(line, "confidence", None)
                lines.append(OcrLine(
                    text=_clean(getattr(line, "text", "")) or "",
                    confidence=float(confidence) if confidence is not None else None,
                    bbox=box,
                ))
        except Exception:
            lines = []

    return cells, lines, height


def _label_name(item) -> str:
    label = getattr(item, "label", None)
    return str(getattr(label, "value", label) or "text")


def _item_box(item, page_geometry):
    """``(page_no, (l, t, r, b) TOP-LEFT)`` for one docling item, or ``None``."""
    prov = (getattr(item, "prov", None) or [None])[0]
    bbox = getattr(prov, "bbox", None) if prov is not None else None
    if bbox is None:
        return None
    page_no = getattr(prov, "page_no", None)
    _, height = (page_geometry or {}).get(page_no, (None, None))
    box = _topleft_box(bbox.l, bbox.t, bbox.r, bbox.b, getattr(bbox, "coord_origin", None), height)
    return None if box is None else (page_no, box)


def _missed_tables(document, page_geometry, detected) -> tuple[list[ConvertedTable], list[str]]:
    """Tables docling's layout model missed, rebuilt from text positions.

    Also the caveat for the case where figures were read but no table could be
    assembled: without it those figures vanish (short text blocks are dropped
    when narrative chunks are built) and nothing says so.
    """
    from . import structure_repair as sr

    frags: dict[int, list] = {}
    tables_at: dict[int, list] = {}
    pictures_at: dict[int, list] = {}
    for item in getattr(document, "texts", None) or []:
        placed = _item_box(item, page_geometry)
        if placed is not None:
            frags.setdefault(placed[0], []).append(
                sr.TextFragment(text=_clean(getattr(item, "text", "")) or "", bbox=placed[1],
                                label=_label_name(item)))
    for item in getattr(document, "tables", None) or []:
        placed = _item_box(item, page_geometry)
        if placed is not None:
            tables_at.setdefault(placed[0], []).append(placed[1])
    for item in getattr(document, "pictures", None) or []:
        placed = _item_box(item, page_geometry)
        if placed is not None:
            pictures_at.setdefault(placed[0], []).append(placed[1])

    extra: list[ConvertedTable] = []
    notes: list[str] = []
    for page_no in sorted(frags):
        table_boxes = tables_at.get(page_no, [])
        orphans = sr.orphan_figures(frags[page_no], table_boxes, pictures_at.get(page_no, []))
        # First: rows a detected table's box stopped short of, printed in its
        # own columns just below it. These borrow that table's header.
        for ct in detected:
            if ct.page_no != page_no or not ct.bbox or not ct.markdown or not orphans:
                continue
            h = (page_geometry.get(page_no) or (None, None))[1]
            if not h:
                continue
            box_tl = (ct.bbox[0], h - ct.bbox[1], ct.bbox[2], h - ct.bbox[3])
            try:
                cont = sr.synthesize_continuation(frags[page_no], orphans, ct.markdown,
                                                  ct.ocr_lines, box_tl)
            except Exception:  # noqa: BLE001
                logger.exception("continuation rebuild failed on page %s", page_no)
                cont = None
            if cont is None:
                continue
            built_cont, consumed = cont
            l, t, r, b = built_cont.bbox
            box_bl = [l, h - t, r, h - b]
            _, lines, _ = _table_geometry(_GeomItem(page_no, box_bl), page_no, page_geometry)
            extra.append(ConvertedTable(
                page_no=page_no, markdown=built_cont.markdown,
                title=f"{ct.title} (continued)" if ct.title else None,
                bbox=box_bl, cells=[], ocr_lines=lines, page_height_pt=h,
            ))
            notes.append(
                f"Page {page_no}: {built_cont.figures} figure(s) printed below the detected "
                "table, in its columns, were outside the box layout analysis drew. They were "
                "rebuilt as a continuation of that table under its column headings and are "
                "verified like any other table."
            )
            gone = {id(f) for f in consumed}
            orphans = [f for f in orphans if id(f) not in gone]
        if len(orphans) < 3:
            continue
        built = None
        try:
            built = sr.synthesize_table(frags[page_no], orphans, table_boxes)
        except Exception:  # noqa: BLE001 - a rebuild failure must never fail the document
            logger.exception("table rebuild failed on page %s", page_no)
        height = (page_geometry.get(page_no) or (None, None))[1]
        if built is None or not height:
            notes.append(
                f"Page {page_no}: {len(orphans)} figure(s) were read from the scan but the "
                "layout analysis found no table for them and none could be assembled from "
                "their positions. They are NOT in any table or extracted text -- check "
                "this page against the original before relying on its figures."
            )
            continue
        l, t, r, b = built.bbox
        box_bl = [l, height - t, r, height - b]  # docling's BOTTOMLEFT box, as _provenance
        geom_item = _GeomItem(page_no, box_bl)
        cells, lines, _ = _table_geometry(geom_item, page_no, page_geometry)
        extra.append(ConvertedTable(
            page_no=page_no, markdown=built.markdown, title=built.title, bbox=box_bl,
            cells=[], ocr_lines=lines, page_height_pt=height,
        ))
        notes.append(
            f"Page {page_no}: the layout analysis did not detect a table here (few or no "
            f"ruling lines); a table of {built.figures} figure(s) was rebuilt from the OCR "
            "text positions and is verified like any other table."
        )
    return extra, notes


class _GeomItem:
    """Just enough of a docling table item for `_table_geometry`."""

    def __init__(self, page_no, box_bl):
        from docling_core.types.doc import BoundingBox, CoordOrigin

        l, t, r, b = box_bl
        bbox = BoundingBox(l=l, t=t, r=r, b=b, coord_origin=CoordOrigin.BOTTOMLEFT)

        class _P:
            pass

        prov = _P()
        prov.bbox, prov.page_no = bbox, page_no
        self.prov, self.data = [prov], None


def _extract_tables(document, page_geometry=None) -> list[ConvertedTable]:
    """Pull each table out with its page number and bounding box.

    The bbox is what the citation viewer highlights and what the VLM second read
    crops to, so a table without one is still usable but loses both.
    """
    tables: list[ConvertedTable] = []
    for item in getattr(document, "tables", None) or []:
        page_no, bbox = _provenance(item)
        # Pass the document. Called without it, docling 2.x logs
        # "Usage of TableItem.export_to_markdown() without `doc` argument is
        # deprecated" on every table and takes a hand-rolled path instead of
        # MarkdownDocSerializer. Both forms produce byte-identical pipe tables
        # on 2.123.1, so this is purely about staying on the supported path;
        # the no-argument fallback covers builds whose signature predates it.
        try:
            markdown = item.export_to_markdown(document)
        except TypeError:
            try:
                markdown = item.export_to_markdown()
            except Exception:
                continue
        except Exception:
            continue
        cells, ocr_lines, page_height = _table_geometry(item, page_no, page_geometry)
        tables.append(ConvertedTable(
            page_no=page_no or 1,
            markdown=_clean(markdown),
            title=_caption(item, document),
            bbox=bbox,
            cells=cells,
            ocr_lines=ocr_lines,
            page_height_pt=page_height,
        ))
    return tables


def _provenance(item) -> tuple[int | None, list[float] | None]:
    provenance = getattr(item, "prov", None) or []
    if not provenance:
        return None, None
    first = provenance[0]
    page_no = getattr(first, "page_no", None)
    bbox = getattr(first, "bbox", None)
    if bbox is None:
        return page_no, None
    try:
        # NOTE for consumers: docling PDF bboxes use coord_origin=BOTTOMLEFT.
        # The frontend overlay must flip Y before drawing over a rasterised page.
        return page_no, [float(bbox.l), float(bbox.t), float(bbox.r), float(bbox.b)]
    except Exception:
        return page_no, None


def _caption(item, document) -> str | None:
    try:
        text = item.caption_text(document)
    except Exception:
        return None
    return _clean((text or "").strip()) or None
