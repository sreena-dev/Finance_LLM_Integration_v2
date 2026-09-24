"""The shapes that flow between the table-extraction stages.

    layout   -> TableRegion       where a table is on a page
    ocr      -> Token             every word OCR read inside it, with its box
    structure-> TablePlan         which tokens are labels, notes and figures, and
                                  which row/column each figure belongs to
    second_read / reconcile -> CellResult   how far each figure can be trusted

All coordinates are pixels of the preprocessed page image, origin top-left.
Nothing here computes anything; the stages that build these own the logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

Box = tuple[float, float, float, float]  # x0, y0, x1, y1

ROLE_LABEL = "label"
ROLE_NOTE = "note"
ROLE_VALUE = "value"
ROLE_IGNORE = "ignore"

KIND_HEADER = "header"
KIND_ITEM = "item"
KIND_SUBTOTAL = "subtotal"
KIND_TOTAL = "total"
KIND_HEADING = "heading"
KIND_BLANK = "blank"
ROW_KINDS = (KIND_HEADER, KIND_ITEM, KIND_SUBTOTAL, KIND_TOTAL, KIND_HEADING, KIND_BLANK)

# How far a figure can be trusted, weakest last. `reconcile.py` decides these;
# `export.py` decides what each looks like in the table.
STATUS_VERIFIED = "verified_dual_read"   # OCR and an independent second read agree
STATUS_OCR_ONLY = "ocr_only"             # no second read was possible; OCR alone
STATUS_FLAGGED = "flagged"               # the two reads disagree
STATUS_UNREADABLE = "unreadable"         # nothing trustworthy to show
STATUS_HANDWRITTEN = "handwritten"       # the page carries a handwritten figure here
STATUS_STRUCK = "struck"                 # the printed figure is struck through
STATUS_EMPTY = "empty"                   # nothing is printed in this cell


@dataclass
class Token:
    id: str
    text: str
    bbox: Box
    page_no: int
    conf: float | None = None

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def x1(self) -> float:
        return self.bbox[2]

    @property
    def y1(self) -> float:
        return self.bbox[3]

    @property
    def xc(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2.0

    @property
    def yc(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2.0

    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]


@dataclass
class TableRegion:
    """One table's place on a page, as layout analysis found it."""

    page_no: int
    bbox: Box
    #: The caption or heading printed just above the table, if any.
    title: str | None = None
    #: Order of this table among the page's tables, top to bottom.
    index_on_page: int = 0
    #: "layout" (docling marked it) or "unmarked" (found from a run of figures).
    source: str = "layout"


@dataclass
class Column:
    index: int
    role: str
    #: Horizontal band this column's figures sit in.
    x0: float
    x1: float
    #: Median right edge of the figures in it -- figures are right-aligned.
    right: float
    header: str = ""
    period: str | None = None
    #: The figure-shaped tokens whose right edges formed this column. Kept so a
    #: role proposed for the column can be checked against what is in it.
    members: list = field(default_factory=list)
    #: True when this column and its neighbour sit under one printed heading (an
    #: item column and its subtotal column), so a shared year is expected.
    shares_heading: bool = False


@dataclass
class Row:
    index: int
    kind: str
    y0: float
    y1: float
    label_tokens: list[Token] = field(default_factory=list)
    note_tokens: list[Token] = field(default_factory=list)
    #: column index -> the tokens OCR read in that column on this row.
    cells: dict[int, list[Token]] = field(default_factory=dict)
    #: Figure-shaped tokens that fell in no value column.
    stray: list[Token] = field(default_factory=list)

    @property
    def label(self) -> str:
        return " ".join(t.text for t in self.label_tokens).strip()

    @property
    def note(self) -> str:
        return " ".join(t.text for t in self.note_tokens).strip()


@dataclass
class TablePlan:
    region: TableRegion
    table_no: int
    tokens: list[Token]
    columns: list[Column]
    rows: list[Row]
    #: Text of every header row, top to bottom, for the record's title/description.
    header_lines: list[str] = field(default_factory=list)
    #: False when Gemma's structure could not be validated and the geometry-only
    #: grid was used instead.
    structure_confirmed: bool = True
    notes: list[str] = field(default_factory=list)
    #: Which candidate rows Gemma / geometry treated as this row's source, so a
    #: second read can crop the right band.
    crop_bbox: Box | None = None

    @property
    def value_columns(self) -> list[Column]:
        return [c for c in self.columns if c.role == ROLE_VALUE]


@dataclass
class CellResult:
    row: int
    col: int
    status: str
    #: What OCR read, verbatim. Never rewritten.
    ocr_text: str = ""
    #: What the independent second read reported for this position, if any.
    second_text: str | None = None
    ocr_conf: float | None = None
    reasons: list[str] = field(default_factory=list)
    tokens: list[Token] = field(default_factory=list)
