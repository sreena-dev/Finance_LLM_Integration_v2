"""The shapes this service produces.

Two audiences, and the split matters:

* ``TableRecord`` / ``TextRecord`` / ``DocumentRecord`` are shaped to the
  **existing corpus contract** -- the columns that
  ``backend/modes/financial_statement/pipeline/tools_fs.py`` already selects out
  of Postgres (``table_id``, ``table_title``, ``table_md``, ``page_ocr_start``,
  ``financial_stmt_type``, ``toc_section``, ``unit``, ``currency``,
  ``note_refs``). Matching those names exactly is what lets the 18 existing
  company tools -- ratios, tie-outs, Schedule III compliance, CARO, going
  concern -- run against an uploaded file with no change to their own code.

* ``PageQuality`` / ``DocumentQuality`` / ``CellFinding`` are new, and exist to
  answer the spec's section 4.3 (input-quality checks before analysis) and 15.3
  (*"If an amount cannot be read with confidence, classify the item as
  extraction issue, not financial issue"*).

Everything is a plain dataclass with an ``as_dict``. These cross a process
boundary as JSON and are then held in RAM by the gateway; nothing is persisted,
so there is no migration to keep in step and no ORM to satisfy.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


# Grades use docling's own vocabulary and thresholds (see
# docling/docling/datamodel/base_models.py, _score_to_grade) so that our
# preprocessing scores and docling's conversion scores can be reported on one
# scale without the reader having to hold two rubrics in mind.
GRADE_ORDER = ("poor", "fair", "good", "excellent")


def score_to_grade(score: float | None) -> str:
    if score is None:
        return "unspecified"
    if score < 0.5:
        return "poor"
    if score < 0.8:
        return "fair"
    if score < 0.9:
        return "good"
    return "excellent"


def worst_grade(grades) -> str:
    seen = [g for g in grades if g in GRADE_ORDER]
    if not seen:
        return "unspecified"
    return min(seen, key=GRADE_ORDER.index)


@dataclass
class PageDefect:
    """One named, human-readable thing wrong with a page.

    The spec asks the model to say *what* was unclear, not merely that quality
    was low, so a defect carries its own sentence rather than a code the caller
    has to translate.
    """

    code: str
    detail: str
    severity: str = "warn"  # "warn" | "error"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PageQuality:
    page_no: int  # 1-based, matches page_ocr_start
    width_px: int = 0
    height_px: int = 0
    native_dpi: float | None = None
    colour: str = "unknown"  # "bilevel" | "grey" | "colour"
    has_text_layer: bool = False
    rotation: int = 0
    skew_deg: float | None = None
    blur: float | None = None       # variance of Laplacian; higher is sharper
    contrast: float | None = None   # 5th-95th percentile spread, 0..1
    ink_coverage: float | None = None
    is_blank: bool = False
    duplicate_of: int | None = None
    defects: list[PageDefect] = field(default_factory=list)

    # Filled in after docling conversion.
    ocr_score: float | None = None
    layout_score: float | None = None
    table_score: float | None = None
    parse_score: float | None = None

    @property
    def preprocess_score(self) -> float:
        """A 0..1 legibility estimate from image statistics alone.

        Computed before OCR so a hopeless upload is rejected in seconds rather
        than after a ten-minute conversion. Deliberately conservative: it can
        say "this will be hard", never "this will be fine".
        """
        parts: list[float] = []
        if self.native_dpi is not None:
            # 150 DPI is roughly where 8pt table digits stop surviving
            # binarisation; 300 is the OCR training target.
            parts.append(max(0.0, min(1.0, (self.native_dpi - 100.0) / 200.0)))
        # Divisors are the measured floor of the clean corpus, so a typical good
        # page scores ~1.0 on these terms and only real degradation moves them.
        # See the calibration table in precheck.py.
        if self.contrast is not None:
            parts.append(max(0.0, min(1.0, self.contrast / 0.75)))
        if self.blur is not None:
            parts.append(max(0.0, min(1.0, self.blur / 2500.0)))
        if self.skew_deg is not None:
            # Half a degree over an A4 width already shifts a row by ~7px, which
            # is most of a text line's height.
            parts.append(max(0.0, min(1.0, 1.0 - abs(self.skew_deg) / 3.0)))
        if self.colour == "bilevel":
            # A hard-thresholded fax scan has thrown away the greys that OCR
            # uses to reconstruct thin strokes. It is not fatal, but it is never
            # "excellent" either.
            parts.append(0.55)
        return sum(parts) / len(parts) if parts else 0.5

    @property
    def grade(self) -> str:
        scores = [s for s in (self.ocr_score, self.layout_score, self.table_score) if s is not None]
        scores.append(self.preprocess_score)
        return score_to_grade(min(scores))

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["defects"] = [x.as_dict() for x in self.defects]
        d["preprocess_score"] = round(self.preprocess_score, 4)
        d["grade"] = self.grade
        return d


@dataclass
class CellFinding:
    """A cell whose value could not be established beyond doubt.

    This is the record that keeps a number out of the model's hands. ``value``
    is deliberately absent -- callers render ``marker`` instead, so there is no
    path by which an unverified figure reaches a prompt.
    """

    page_no: int
    table_id: str
    row_label: str
    column: str
    raw: str
    reasons: list[str] = field(default_factory=list)

    @property
    def marker(self) -> str:
        return (
            f'[unreadable: page {self.page_no}, table {self.table_id}, '
            f'row "{self.row_label}", col "{self.column}"]'
        )

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["marker"] = self.marker
        return d


@dataclass
class FootingCheck:
    """One arithmetic self-audit of a printed subtotal against its components."""

    table_id: str
    page_no: int
    subtotal_label: str
    printed: float | None
    recomputed: float | None
    difference: float | None
    passed: bool
    component_labels: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TableRecord:
    """One extracted table, in the shape ``public.table_chunks`` rows have.

    ``table_md`` is a GitHub-flavoured pipe table, which is what
    ``RatioExtractionEngine.parse_table_md`` and ``fs_db/md_parser.py`` both
    already parse. Unverified cells appear in it as their ``CellFinding.marker``
    rather than as a number.
    """

    table_id: str
    doc_id: str
    table_title: str | None
    table_md: str
    page_ocr_start: int
    page_ocr_end: int | None = None
    financial_stmt_type: str | None = None
    toc_section: str | None = None
    unit: str | None = None
    currency: str | None = None
    note_refs: list[str] = field(default_factory=list)
    is_financial: bool = False
    #: A searchable description of what the table contains, DERIVED from its own
    #: row labels rather than filed with the document.
    #: ``AuditRiskTools._match_note_tables`` matches ``table_title ILIKE`` OR
    #: ``table_description ILIKE``, and it is the single helper behind
    #: get_schedule_note, get_audit_report_highlights and review_account_area --
    #: three tools that find nothing without it whenever docling failed to
    #: recover a caption, which on a scan is often.
    table_description: str | None = None
    #: Which uploaded file this came from. See TextRecord.source_file.
    source_file: str | None = None

    # New, upload-only. The corpus has no equivalent, and the existing tools
    # ignore unknown keys.
    bbox: list[float] | None = None
    confidence: float | None = None
    vlm_agreement: float | None = None
    #: A JPEG of this table's own region of the corrected page, base64-encoded,
    #: so a citation can show the reader the scan the figure was taken from.
    #: The crop rather than the whole page: it is the evidence for this table
    #: specifically, and a 300 DPI A4 page is ~10x the bytes for a picture the
    #: reader then has to search. Absent when the table carried no bounding box.
    snippet_jpeg_b64: str | None = None
    findings: list[CellFinding] = field(default_factory=list)
    footings: list[FootingCheck] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["findings"] = [x.as_dict() for x in self.findings]
        d["footings"] = [x.as_dict() for x in self.footings]
        return d


@dataclass
class PageImage:
    """One page's corrected image, base64 JPEG, for the document pane's page view.

    Deliberately the WHOLE page docling and OCR actually read, not a crop --
    mirrors ``TableRecord.snippet_jpeg_b64`` but for the page rather than one
    figure's region, so a reader can page through a filing and check what was
    read against the scan itself. Captured once, during ingestion: the source
    PDF is dropped the moment the job ends (see ``jobs.py``), so there is no
    later point at which this bitmap could be re-derived.
    """

    page_no: int
    image_jpeg_b64: str | None = None
    width_px: int = 0
    height_px: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


#: The ``chunk_type`` values the Financial Statement tools actually filter on.
#: ``heading`` rows are the anchors for every precise narrative lookup, and
#: ``text``/``list`` rows are the passage bodies fetched after an anchor. Emitting
#: only ``text`` -- which this service did originally -- leaves the entire
#: heading-anchored retrieval family with nothing to anchor to.
CHUNK_HEADING = "heading"
CHUNK_TEXT = "text"
CHUNK_LIST = "list"


@dataclass
class TextRecord:
    """One narrative chunk, in the shape ``public.text_chunks`` rows have.

    Three fields here are load-bearing in ways that are easy to get wrong:

    ``page_ocr_start``
        **This**, not ``page_pdf_start``, is the page number every FS query
        selects, orders by and cites. ``SourceRef``'s own docstring is blunt
        about why: *"page_pdf_start is unusable -- its offset from
        page_ocr_start swings from -264 to +301 within a single document."*
        ``DocumentResolver._fetch_following_chunks`` orders on the tuple
        ``(page_ocr_start, chunk_id)``, so leaving it unset breaks passage
        assembly outright. ``page_pdf_start`` is kept alongside it only because
        the corpus column exists; nothing in this mode reads it.

    ``chunk_type``
        One of ``heading`` / ``text`` / ``list``. See the constants above.

    ``section_breadcrumb``
        The heading path, as a list. Two filters depend on its *content*:
        ``AccountingPolicyTools._find_heading_by_*`` require it to match
        ``%notes to%``, and every narrative query sorts standalone before
        consolidated on ``NOT ILIKE '%consolidated%'``. An empty breadcrumb
        silently excludes a document from policy-note lookup.
    """

    chunk_id: str
    doc_id: str
    content: str
    #: The page number the FS tools cite. Always populate this one.
    page_ocr_start: int
    #: Present for parity with the corpus column; nothing in this mode reads it.
    page_pdf_start: int | None = None
    section: str | None = None
    title: str | None = None
    chunk_type: str = CHUNK_TEXT
    section_breadcrumb: list[str] = field(default_factory=list)
    note_refs: list[str] = field(default_factory=list)
    bbox: list[float] | None = None
    #: Which uploaded file this came from. Carried so that a citation still
    #: names its own file once several uploads are merged into one package.
    source_file: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Identification:
    """What the document says it is -- inferred, with its evidence attached.

    Per spec section 5.1 this is reported as an inference and never as a
    certainty, and per section 4.2 it never comes from the filename: a file
    called ``..._2020-21_SFS_...pdf`` sitting in a ``2020-21/`` directory is
    metadata somebody typed, not something the auditor can rely on.
    """

    financial_year: str | None = None
    fy_confidence: str = "low"          # high | medium | low
    fy_evidence: list[str] = field(default_factory=list)
    entity_name: str | None = None
    entity_evidence: list[str] = field(default_factory=list)
    framework: str | None = None        # "Ind AS" | "AS" | "NBFC Ind AS" | None
    framework_division: str | None = None
    framework_confidence: str = "low"
    framework_signals: list[str] = field(default_factory=list)
    statement_flavour: str | None = None  # "standalone" | "consolidated"
    unresolved_conflicts: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DocumentQuality:
    pages: list[PageQuality] = field(default_factory=list)
    duplicate_pages: list[int] = field(default_factory=list)
    unreadable_cells: list[CellFinding] = field(default_factory=list)
    failed_footings: list[FootingCheck] = field(default_factory=list)
    vlm_used: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def mean_score(self) -> float | None:
        scores = [p.preprocess_score for p in self.pages]
        return sum(scores) / len(scores) if scores else None

    @property
    def low_score(self) -> float | None:
        scores = sorted(p.preprocess_score for p in self.pages)
        return scores[0] if scores else None

    @property
    def grade(self) -> str:
        return worst_grade([score_to_grade(self.mean_score)])

    @property
    def low_grade(self) -> str:
        return score_to_grade(self.low_score)

    def as_dict(self) -> dict[str, Any]:
        return {
            "pages": [p.as_dict() for p in self.pages],
            "duplicate_pages": self.duplicate_pages,
            "unreadable_cells": [c.as_dict() for c in self.unreadable_cells],
            "failed_footings": [f.as_dict() for f in self.failed_footings],
            "vlm_used": self.vlm_used,
            "notes": self.notes,
            "mean_score": self.mean_score,
            "low_score": self.low_score,
            "grade": self.grade,
            "low_grade": self.low_grade,
        }


@dataclass
class DocumentRecord:
    """One ingested document, in the shape ``public.documents`` rows have."""

    doc_id: str
    filename: str
    company: str | None = None
    fy_start: int | None = None
    fy_end: int | None = None
    total_pages_pdf: int = 0
    total_pages_ocr: int = 0
    total_tables: int = 0
    total_chunks: int = 0
    sha256: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IngestResult:
    document: DocumentRecord
    identification: Identification
    quality: DocumentQuality
    tables: list[TableRecord] = field(default_factory=list)
    texts: list[TextRecord] = field(default_factory=list)
    #: One entry per kept page (blank/duplicate pages are excluded, same as
    #: `tables`/`texts`), for the document pane's page-by-page view.
    pages: list[PageImage] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "document": self.document.as_dict(),
            "identification": self.identification.as_dict(),
            "quality": self.quality.as_dict(),
            "tables": [t.as_dict() for t in self.tables],
            "texts": [t.as_dict() for t in self.texts],
            "pages": [p.as_dict() for p in self.pages],
        }
