"""Turning verified tables into records the existing FS tools already understand.

The column names here are not a new schema. They are the columns
``backend/modes/financial_statement/pipeline/tools_fs.py`` already selects out
of Postgres:

    SELECT table_id, table_title, table_md, page_ocr_start
    FROM public.table_chunks
    WHERE doc_id = %s AND financial_stmt_type = %s ...

Matching them exactly is the whole reuse strategy. ``RatioTools`` (33 ratios),
``TieOutTools``, ``MaterialityTools``, ``ComplianceTools``,
``AuditorReportTools``, ``GoingConcernTools``, ``TrendAnalysisTools`` and
``AccountAreaTools`` all reach their data through
``ComplianceTools._find_statement_tables`` and ``UnitResolver.resolve``, and both
of those care only about these field names. Get them right and the entire
existing check library runs against an uploaded scan without a line of change to
any of it.

Two fields are load-bearing in ways that are easy to miss:

``financial_stmt_type``
    Must be one of ``balance_sheet`` / ``profit_loss`` / ``cash_flow`` /
    ``statement_of_equity``. Anything else and ``_find_statement_tables``
    matches nothing, and every downstream tool reports the document as having
    no financial statements.

``unit`` / ``currency``
    ``UnitResolver`` takes a majority vote over these across the document's
    financial tables and prints a ``UNITS:`` line that prompt rule 20 requires
    the model to attach to every figure. Left empty, the resolver correctly
    reports that no scale was declared -- which is the honest outcome, and far
    better than guessing crore.
"""

from __future__ import annotations

import hashlib
import re

from .config import Config
from .models import (
    CHUNK_HEADING,
    CHUNK_LIST,
    CHUNK_TEXT,
    CellFinding,
    DocumentQuality,
    DocumentRecord,
    FootingCheck,
    Identification,
    IngestResult,
    PageImage,
    PageQuality,
    RecoveryStats,
    StageDuration,
    TableRecord,
    TextRecord,
)

# "Refer note 12", "(Note 2A)", "Notes 3 and 4"
_NOTE_REF_RE = re.compile(r"\bnotes?\.?\s*(?:no\.?)?\s*(\d{1,3}[A-Za-z]?)", re.I)


def doc_id_for(filename: str, data: bytes) -> str:
    """A stable id derived from the file's content, not its name.

    Content-addressed so that re-uploading the same file into the same
    conversation resolves to the same document rather than silently creating a
    second copy of every table -- which would then double any figure the model
    summed across documents.
    """
    digest = hashlib.sha256(data).hexdigest()
    return f"up_{digest[:16]}"


def _note_refs(text: str) -> list[str]:
    return sorted({m.group(1).upper() for m in _NOTE_REF_RE.finditer(text or "")})


def _fy_years(financial_year: str | None) -> tuple[int | None, int | None]:
    """``"2022-23"`` -> ``(2022, 2023)``, matching ``documents.fy_start/fy_end``."""
    if not financial_year:
        return None, None
    match = re.match(r"^(\d{4})-(\d{2,4})$", financial_year)
    if not match:
        return None, None
    start = int(match.group(1))
    return start, start + 1


def jpeg_b64(image: "np.ndarray", max_width: int = 1100, quality: int = 72) -> str | None:
    """A base64 JPEG of an image, for a reader to look at rather than re-OCR.

    Downscaled and compressed hard: this is evidence to look at, not to OCR
    again. A 300 DPI crop of a full-page table is ~1.5MB raw and ~60KB at the
    table-snippet defaults, and the gateway holds up to twelve documents per
    user in memory.
    """
    try:
        import base64
        import io

        from PIL import Image

        frame = Image.fromarray(image).convert("L")
        if frame.width > max_width:
            frame = frame.resize((max_width, int(frame.height * max_width / frame.width)), Image.LANCZOS)
        buffer = io.BytesIO()
        frame.save(buffer, format="JPEG", quality=quality, optimize=True)
        return base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception:
        return None


def build_page_image(page_no: int, image: "np.ndarray | None") -> PageImage:
    """One page's corrected bitmap, encoded for the document pane's page view.

    Sized separately from ``jpeg_b64``'s table-crop defaults
    (1100px/q72): a full page is roughly ten times a table crop's pixel area,
    and this is read to compare against the printed page, not fed back into
    OCR, so it can afford to be smaller and softer than a citation crop of one
    figure. ``None`` is accepted defensively, mirroring ``jpeg_b64``'s
    own contract, and returns an imageless record rather than raising -- the
    caller filters blank/duplicate pages out before this is reached, but a
    record that fails safe here is one fewer way ingestion can crash on a page
    that turned out to have nothing to encode.
    """
    if image is None:
        return PageImage(page_no=page_no)
    height, width = image.shape[:2]
    return PageImage(
        page_no=page_no,
        image_jpeg_b64=jpeg_b64(image, max_width=1400, quality=60),
        width_px=width,
        height_px=height,
    )


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
#: A block whose lines start with a bullet or an ordinal is a list, which the FS
#: tools treat as a passage body alongside prose (``chunk_type IN ('text','list')``).
_LIST_LINE_RE = re.compile(r"^\s*(?:[-*•]|\(?[a-z0-9]{1,3}[.)])\s+", re.I)
#: "Notes to the Standalone Financial Statements", "Notes forming part of ...".
#: Detecting this seeds the breadcrumb that
#: ``AccountingPolicyTools._find_heading_by_*`` filters on with ``%notes to%``.
_NOTES_ROOT_RE = re.compile(r"\bnotes?\s+(?:to|forming\s+part\s+of)\b", re.I)


def _looks_like_list(block: str) -> bool:
    lines = [ln for ln in block.splitlines() if ln.strip()]
    if len(lines) < 2:
        return False
    return sum(1 for ln in lines if _LIST_LINE_RE.match(ln)) >= max(2, len(lines) // 2)


def build_text_records(
    doc_id: str,
    page_markdown: dict[int, str],
    *,
    flavour: str | None = None,
    entity_name: str | None = None,
    source_file: str | None = None,
) -> list[TextRecord]:
    """Narrative chunks in the shape the FS narrative retrieval expects.

    Not a flat list of paragraphs. The precise-retrieval family in ``tools_fs``
    is *heading-anchored*: it finds a ``chunk_type='heading'`` row, then fetches
    the four following ``text``/``list`` rows in document order. So headings are
    emitted as their own records, and the heading stack becomes each record's
    ``section_breadcrumb``.

    Three details are what make the corpus queries match:

    * **Heading numbering is preserved.** ``AccountingPolicyTools`` separates a
      level-2 policy sub-note from a level-1 disclosure note with the regex
      ``^[0-9]+\\.[0-9]+\\.?\\s`` against heading *content*. Docling gives us
      ``## 2.11 TAXATION:``, which matches -- as long as nothing strips it.
    * **The breadcrumb is rooted at the statement flavour** and, once a "Notes
      to ..." heading is seen, at that too. Policy lookup filters the breadcrumb
      on ``%notes to%``, and every narrative query sorts standalone before
      consolidated by testing it for ``consolidated``.
    * **The running page header is not a heading.** Docling exports the entity
      name as ``## IDBI Trusteeship Services Ltd`` at the top of nearly every
      page; treated as a section it would overwrite the real one on every chunk
      and reset the breadcrumb on every page.
    """
    records: list[TextRecord] = []

    root: list[str] = []
    if flavour:
        root.append(f"{flavour.capitalize()} Financial Statements")
    entity_key = (entity_name or "").strip().lower()

    # (level, text) stack of open headings, reset per document rather than per
    # page: a note's body routinely continues onto the next sheet, and resetting
    # per page would orphan the continuation from its heading.
    stack: list[tuple[int, str]] = []

    def breadcrumb() -> list[str]:
        return root + [t for _, t in stack]

    def emit(content: str, page_no: int, chunk_type: str) -> None:
        crumbs = breadcrumb()
        records.append(TextRecord(
            chunk_id=f"{doc_id}_p{page_no:04d}_c{len(records) + 1:04d}",
            doc_id=doc_id,
            content=content,
            page_ocr_start=page_no,
            page_pdf_start=page_no,
            section=stack[-1][1] if stack else None,
            title=stack[-2][1] if len(stack) > 1 else (stack[-1][1] if stack else None),
            chunk_type=chunk_type,
            section_breadcrumb=crumbs,
            note_refs=_note_refs(content),
            source_file=source_file,
        ))

    for page_no in sorted(page_markdown):
        for block in re.split(r"\n\s*\n", page_markdown[page_no] or ""):
            block = block.strip()
            if not block or block.startswith("|") or block.startswith("<!--"):
                continue

            lines = block.splitlines()
            match = _HEADING_RE.match(lines[0])
            if match:
                # A heading and the prose under it often arrive in ONE block --
                # docling emits no blank line between a running page header and
                # the address beneath it. Split rather than requiring a
                # single-line block, or the heading is swallowed into a text
                # chunk and never becomes an anchor.
                level, text = len(match.group(1)), match.group(2).strip()
                remainder = "\n".join(lines[1:]).strip()

                if text and not (entity_key and text.lower() == entity_key):
                    if _NOTES_ROOT_RE.search(text) and text not in root:
                        # Seed the root so every chunk beneath it carries
                        # "Notes to" in its breadcrumb, which is exactly what
                        # policy lookup filters on.
                        root.append(text)
                        stack.clear()
                    else:
                        while stack and stack[-1][0] >= level:
                            stack.pop()
                        stack.append((level, text))
                        # chunk_ids must sort in document order within a page:
                        # _fetch_following_chunks orders on
                        # (page_ocr_start, chunk_id) and takes everything
                        # strictly after the anchor.
                        emit(text, page_no, CHUNK_HEADING)

                if len(remainder) >= 25:
                    emit(remainder, page_no,
                         CHUNK_LIST if _looks_like_list(remainder) else CHUNK_TEXT)
                continue

            # The notes root is often NOT a heading. Measured on the real
            # scans: docling emits "## 2.11 TAXATION:" but no
            # "## Notes to the Standalone Financial Statements" anywhere on the
            # page -- the marker sits in body text or on an earlier sheet. It is
            # picked up from prose too, because the policy-note lookup filters
            # breadcrumbs on "notes to" and without it that lookup finds
            # nothing at all.
            if not any(_NOTES_ROOT_RE.search(r) for r in root):
                first = block.splitlines()[0].strip()
                if len(first) <= 120 and _NOTES_ROOT_RE.search(first):
                    root.append(first)

            if len(block) < 25:
                continue
            emit(block, page_no, CHUNK_LIST if _looks_like_list(block) else CHUNK_TEXT)

    return records


def build_result(
    doc_id: str,
    filename: str,
    data: bytes,
    identification: Identification,
    qualities: list[PageQuality],
    tables: list[TableRecord],
    texts: list[TextRecord],
    vlm_used: bool,
    notes: list[str],
    page_images: list[PageImage] | None = None,
    stage_durations: list[StageDuration] | None = None,
    rescue_stats: RecoveryStats | None = None,
    disagreement_stats: RecoveryStats | None = None,
    gap_fill_stats: RecoveryStats | None = None,
) -> IngestResult:
    fy_start, fy_end = _fy_years(identification.financial_year)

    unreadable: list[CellFinding] = []
    failed: list[FootingCheck] = []
    # Merged reporting view: every arithmetic-PROMOTED cell (tagged
    # promoted=True) plus every display-only recovered CellFinding (tagged
    # promoted=False) -- the latter is deliberately ALSO still counted in
    # `unreadable` above via table.findings, so a consumer that only knows
    # the pre-recovery `unreadable_cells` count never under-warns: its figure
    # is still not usable, whatever this merged view additionally says about it.
    recovered: list[dict] = []
    for table in tables:
        unreadable.extend(table.findings)
        failed.extend(f for f in table.footings if not f.passed)
        for rc in table.recovered:
            recovered.append({**rc.as_dict(), "promoted": True})
        for f in table.findings:
            if f.recovered_text is not None:
                recovered.append({**f.as_dict(), "promoted": False})

    quality = DocumentQuality(
        pages=qualities,
        duplicate_pages=[q.page_no for q in qualities if q.duplicate_of],
        unreadable_cells=unreadable,
        failed_footings=failed,
        recovered_cells=recovered,
        vlm_used=vlm_used,
        notes=notes,
        stage_durations=stage_durations or [],
        rescue_stats=rescue_stats or RecoveryStats(),
        disagreement_stats=disagreement_stats or RecoveryStats(),
        gap_fill_stats=gap_fill_stats or RecoveryStats(),
    )

    document = DocumentRecord(
        doc_id=doc_id,
        filename=filename,
        company=identification.entity_name,
        fy_start=fy_start,
        fy_end=fy_end,
        total_pages_pdf=len(qualities),
        total_pages_ocr=sum(1 for q in qualities if not q.is_blank),
        total_tables=len(tables),
        total_chunks=len(texts),
        sha256=hashlib.sha256(data).hexdigest(),
        ingest_version=Config.PIPELINE_VERSION,
    )

    return IngestResult(
        document=document,
        identification=identification,
        quality=quality,
        tables=tables,
        texts=texts,
        pages=page_images or [],
    )
