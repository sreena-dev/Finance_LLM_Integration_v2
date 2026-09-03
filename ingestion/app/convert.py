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

import io
import logging
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


@dataclass
class ConvertedTable:
    """One table docling found, before any verification."""

    page_no: int
    markdown: str
    title: str | None = None
    bbox: list[float] | None = None
    confidence: float | None = None


@dataclass
class Converted:
    markdown: str
    tables: list[ConvertedTable] = field(default_factory=list)
    page_markdown: dict[int, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


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


def _pipeline_options(needs_ocr: bool) -> "PdfPipelineOptions":
    options = PdfPipelineOptions()
    options.do_ocr = needs_ocr
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

    if needs_ocr:
        # RapidOCR rather than Tesseract: it runs through onnxruntime and needs
        # no system binary, which matters both for the container and because
        # neither tesseract nor poppler is present on the development host.
        options.ocr_options = RapidOcrOptions(
            lang=Config.OCR_LANG,
            # The pages arriving here are full-page scans with no text layer, so
            # there is nothing for layout-region OCR to be selective about.
            force_full_page_ocr=True,
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


def convert(images: list["np.ndarray"], qualities: list[PageQuality], needs_ocr: bool) -> Converted:
    """Convert the corrected pages and fold docling's confidence into ``qualities``."""
    if not DOCLING_AVAILABLE:
        raise ConvertError(
            "docling is not installed in this service. Install ingestion/requirements.txt "
            "(docling==2.123.1 with the format-pdf, models-local and feat-ocr-rapidocr extras)."
        )

    pdf_bytes = pages_to_pdf(images)
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=_pipeline_options(needs_ocr))}
    )

    stream = DocumentStream(name="upload.pdf", stream=io.BytesIO(pdf_bytes))
    result = converter.convert(stream, raises_on_error=False)

    errors = [str(getattr(e, "error_message", e)) for e in (getattr(result, "errors", None) or [])]

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
    converted = Converted(markdown=document.export_to_markdown(), errors=errors)

    for page_no, page_doc in _iter_pages(document):
        try:
            converted.page_markdown[page_no] = page_doc
        except Exception:  # pragma: no cover - defensive
            continue

    converted.tables = _extract_tables(document)
    return converted


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
    by_number = {q.page_no: q for q in qualities}

    for page_no, scores in pages.items():
        quality = by_number.get(int(page_no)) or by_number.get(int(page_no) + 1)
        if quality is None:
            continue
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
            yield int(page_no), document.export_to_markdown(page_no=int(page_no))
        except TypeError:
            return
        except Exception:
            continue


def _extract_tables(document) -> list[ConvertedTable]:
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
        tables.append(ConvertedTable(
            page_no=page_no or 1,
            markdown=markdown,
            title=_caption(item, document),
            bbox=bbox,
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
    return (text or "").strip() or None
