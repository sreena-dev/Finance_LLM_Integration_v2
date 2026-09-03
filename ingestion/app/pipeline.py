"""The ingestion pipeline, start to finish.

    render -> precheck -> preprocess -> convert -> vlm second read
           -> verify -> identify -> emit

Each stage is a separate module and none of them import each other; this is the
only place that knows the order. That matters because the order is a design
decision rather than an accident -- preprocessing has to happen before docling
sees the page, and verification has to happen before anything is emitted -- and
keeping it in one readable function is what stops it drifting.

Progress is reported through a callback rather than returned, because the whole
run takes minutes on a scanned filing and the gateway streams these events to
the browser as they happen.
"""

from __future__ import annotations

import logging
from typing import Callable

from . import convert as convert_mod
from . import emit, identify as identify_mod, precheck, preprocess, render, verify, vlm_read
from .config import Config
from .models import IngestResult
from .tables import parse_markdown_tables

logger = logging.getLogger(__name__)

Progress = Callable[[str, str, float], None]

#: Stage weights for the progress bar, summing to 1.0. Taken from measured
#: proportions on a 25-page scan: OCR and table structure dominate everything
#: else by an order of magnitude, and a bar that gives each stage equal width
#: sits at 60% for four minutes and reads as a hang.
_STAGE_WEIGHTS = {
    "render": 0.05,
    "precheck": 0.05,
    "preprocess": 0.10,
    "convert": 0.55,
    "vlm": 0.15,
    "verify": 0.05,
    "identify": 0.05,
}


def _noop(stage: str, message: str, fraction: float) -> None:
    return None


def run(data: bytes, filename: str, progress: Progress | None = None) -> IngestResult:
    """Ingest one uploaded PDF and return everything known about it."""
    report = progress or _noop
    done = 0.0

    def advance(stage: str, message: str) -> None:
        nonlocal done
        report(stage, message, min(0.99, done))
        done += _STAGE_WEIGHTS.get(stage, 0.0)

    # ---- render ----------------------------------------------------------
    advance("render", "Reading the PDF")
    pages = render.render(data)
    doc_id = emit.doc_id_for(filename, data)
    notes: list[str] = []

    needs_ocr = not render.document_has_text_layer(pages)
    if needs_ocr:
        notes.append(
            "This document has no text layer on any page: it is a scan, and every "
            "figure below was read by OCR rather than taken from the file."
        )

    # ---- precheck --------------------------------------------------------
    advance("precheck", f"Checking the quality of {len(pages)} page(s)")
    qualities = precheck.check_document(pages)

    unusable = [q for q in qualities if q.grade == "poor"]
    if unusable and len(unusable) == len(qualities):
        notes.append(
            "Every page of this document graded poor. Figures read from it need "
            "checking against the original before use."
        )

    # ---- preprocess ------------------------------------------------------
    advance("preprocess", "Straightening and normalising pages")
    images = preprocess.preprocess(pages, qualities)

    # Skip blank and duplicate pages: a duplicate re-read is not merely wasted
    # OCR, it double-counts every table on the sheet.
    keep = [
        i for i, q in enumerate(qualities)
        if not q.is_blank and q.duplicate_of is None
    ]
    if len(keep) < len(images):
        skipped = len(images) - len(keep)
        notes.append(
            f"{skipped} page(s) were blank or repeated another page and were read once only."
        )

    kept_images = [images[i] for i in keep]
    kept_qualities = [qualities[i] for i in keep]

    # ---- convert ---------------------------------------------------------
    advance("convert", "Detecting layout and reading tables")
    converted = convert_mod.convert(kept_images, kept_qualities, needs_ocr)
    notes.extend(converted.errors)

    # ---- VLM second read -------------------------------------------------
    vlm_available = Config.vlm_configured() and vlm_read.probe()
    if not vlm_available:
        notes.append(
            "A second independent read by the vision model was not available, so "
            "figures here are corroborated by their own arithmetic only."
        )
    advance("vlm", "Re-reading tables with the vision model" if vlm_available else "Skipping the second read")

    # ---- verify ----------------------------------------------------------
    advance("verify", "Checking that the figures add up")
    records = []
    page_by_number = {q.page_no: q for q in kept_qualities}
    image_by_number = {q.page_no: img for q, img in zip(kept_qualities, kept_images)}

    # Titles, recovered from the page rather than from the table.
    #
    # docling supplies a caption only where the document marks one up, and a
    # scanned filing marks up nothing -- every table came back with title=None
    # on the real corpus. That matters more than it looks: `financial_stmt_type`
    # is derived from the title, and without it `_find_statement_tables` matches
    # nothing and every downstream tool reports the document as having no
    # financial statements at all.
    #
    # So each page's own markdown is parsed too, where `parse_markdown_tables`
    # captures the prose line immediately above each table as its title, and the
    # titles are matched to docling's tables by their order on the page.
    titles_by_page: dict[int, list[str | None]] = {}
    for page_no, page_md in converted.page_markdown.items():
        titles_by_page[page_no] = [t.title for t in parse_markdown_tables(page_md, page_no)]
    seen_on_page: dict[int, int] = {}

    for index, converted_table in enumerate(converted.tables, 1):
        parsed = parse_markdown_tables(
            converted_table.markdown, converted_table.page_no, prefix=f"t{index}_"
        )
        if not parsed:
            continue
        table = parsed[0]

        nth = seen_on_page.get(converted_table.page_no, 0)
        seen_on_page[converted_table.page_no] = nth + 1
        from_page = titles_by_page.get(converted_table.page_no) or []
        table.title = (
            converted_table.title
            or table.title
            or (from_page[nth] if nth < len(from_page) else None)
        )

        agreement = None
        snippet = None
        disagreements: set[tuple[int, int]] = set()
        page_image = image_by_number.get(converted_table.page_no)
        if page_image is not None:
            crop = vlm_read.crop(page_image, converted_table.bbox, None)
            # The citation snippet is produced whether or not the vision model
            # is reachable: showing the reader the scan a figure came from is
            # not contingent on a second model having read it.
            snippet = vlm_read.snippet(crop)
            if vlm_available and index <= Config.VLM_MAX_TABLES:
                second = vlm_read.transcribe(crop, table.to_markdown())
                if second:
                    agreement, disagreements = vlm_read.compare(table, second)

        page_quality = page_by_number.get(converted_table.page_no)
        footings, findings = verify.verify_table(
            table,
            converted_table.page_no,
            vlm_disagreements=disagreements,
            ocr_score=page_quality.ocr_score if page_quality else None,
        )
        # Redact BEFORE building the record: the record carries the markdown the
        # model will eventually read, and an unverified figure must not be in it.
        verify.redact(table, findings)

        records.append(emit.build_table_record(
            table=table,
            doc_id=doc_id,
            page_no=converted_table.page_no,
            surrounding_text=converted.page_markdown.get(converted_table.page_no, ""),
            footings=footings,
            findings=findings,
            bbox=converted_table.bbox,
            vlm_agreement=agreement,
            snippet_jpeg_b64=snippet,
            source_file=filename,
        ))

    # ---- page images -------------------------------------------------------
    #
    # One JPEG per kept page (blank/duplicate pages are already excluded from
    # `kept_qualities`), so the document pane can show the reader exactly what
    # OCR/docling saw. Built from `image_by_number` -- the already-corrected
    # `kept_images` bitmaps, in hand above at zero extra rendering cost --
    # rather than asking docling for its own `document.pages[n].image` (enabled
    # via `generate_page_images=True` in convert.py but read nowhere today).
    # `image_by_number` is exactly what OCR/docling actually looked at and is
    # keyed by the same `page_no` tables and texts already use; docling's own
    # page images would be a second, redundant re-rasterisation of that same
    # bitmap and would reintroduce the page-numbering ambiguity
    # `_absorb_confidence` above already has to work around.
    page_images = [
        emit.build_page_image(q.page_no, image_by_number.get(q.page_no))
        for q in kept_qualities
        if image_by_number.get(q.page_no) is not None
    ]

    # ---- identify --------------------------------------------------------
    #
    # Runs BEFORE the narrative chunks are built, not after: the chunk builder
    # needs the statement flavour to root each breadcrumb (standalone vs
    # consolidated is a sort key on every narrative query) and the entity name
    # to recognise the running page header and not mistake it for a section.
    advance("identify", "Working out the entity, year and framework")
    pages_text = [converted.page_markdown[p] for p in sorted(converted.page_markdown)]
    if not pages_text:
        pages_text = [converted.markdown]
    identification = identify_mod.identify(pages_text)

    texts = emit.build_text_records(
        doc_id,
        converted.page_markdown,
        flavour=identification.statement_flavour,
        entity_name=identification.entity_name,
        source_file=filename,
    )

    report("done", "Finished", 1.0)
    return emit.build_result(
        doc_id=doc_id,
        filename=filename,
        data=data,
        identification=identification,
        qualities=qualities,
        tables=records,
        texts=texts,
        vlm_used=vlm_available,
        notes=notes,
        page_images=page_images,
    )
