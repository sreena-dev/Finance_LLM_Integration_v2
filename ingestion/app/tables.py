"""A parsed markdown pipe-table, and the column roles inside it.

The markdown pipe table is the interchange format for this whole system: it is
what ``table_chunks.table_md`` stores in the existing corpus, what
``RatioExtractionEngine.parse_table_md`` and ``fs_db/md_parser.py`` both read,
and what docling's ``export_to_markdown`` emits. Producing and consuming that
one shape is what lets an uploaded document reach the existing tool library
unchanged.

Column roles are decided by **content, not position**, following the approach
already proven in ``fs_db/md_parser.py``. Real filed statements will not
cooperate with a positional rule: the OD balance sheet has an unnumbered roman
level, a separate arabic level, a label, a ``Notes`` column and one value
column; the MH PPE schedule has a label and eight value columns; and either may
carry a leading ``Sr. No.`` that looks numeric but is not money.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .numbers import Cell, parse_cell

_SEPARATOR_RE = re.compile(r"^:?-{2,}:?$")
_NOTE_HDR_RE = re.compile(r"\bnotes?\b", re.I)
_SERIAL_HDR_RE = re.compile(r"^\s*(sr|sl|s)\.?\s*no\.?\s*$|^\s*#\s*$", re.I)
_LABEL_HDR_RE = re.compile(r"particular|description|\bitem\b|head", re.I)
# A note reference is a small integer with an optional letter suffix: 2, 15, 2A.
_NOTE_CELL_RE = re.compile(r"^\d{1,3}\s*[A-Za-z]?$")

#: Row labels that announce a subtotal. Used to *catch a total that does not
#: foot*; discovering subtotals themselves does not depend on this list, because
#: real statements print unlabelled subtotal rows (observed: the OD balance
#: sheet's Shareholder's Funds subtotal has no label at all).
_TOTAL_LABEL_RE = re.compile(
    r"\btotal\b|\bsub-?total\b|\bbalance as at\b|\bclosing\b|"
    r"\bnet carrying\b|\bnet block\b|\bgrand total\b",
    re.I,
)


def split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(
        _SEPARATOR_RE.fullmatch(c or "-") or c == "" for c in cells
    ) and any("-" in c for c in cells)


@dataclass
class Table:
    """One markdown table, with its column roles resolved."""

    table_id: str
    page_no: int
    title: str | None
    header: list[str]
    rows: list[list[str]]
    label_col: int = 0
    note_col: int | None = None
    value_cols: list[int] = field(default_factory=list)

    def label(self, r: int) -> str:
        row = self.rows[r]
        return row[self.label_col] if self.label_col < len(row) else ""

    def column_name(self, c: int) -> str:
        if c < len(self.header) and self.header[c]:
            return self.header[c]
        return f"column {c + 1}"

    def cell(self, r: int, c: int) -> Cell:
        row = self.rows[r]
        return parse_cell(row[c] if c < len(row) else "")

    def to_markdown(self) -> str:
        width = max([len(self.header)] + [len(r) for r in self.rows]) if self.rows else len(self.header)

        def pad(cells: list[str]) -> str:
            padded = list(cells) + [""] * (width - len(cells))
            return "| " + " | ".join(padded) + " |"

        out = [pad(self.header), "| " + " | ".join(["---"] * width) + " |"]
        out.extend(pad(r) for r in self.rows)
        return "\n".join(out)


def _numeric_ratio(values: list[str]) -> float:
    populated = [v for v in values if v and v.strip()]
    if not populated:
        return 0.0
    hits = sum(1 for v in populated if parse_cell(v).value is not None)
    return hits / len(populated)


def _resolve_roles(table: Table) -> None:
    """Decide which column is the label, which is Notes, which carry money.

    The ``Notes`` column is singled out and excluded from the value columns
    because it is numeric but is not an amount. Left in, a note reference of
    ``5`` becomes a figure of five and every footing check on the table fails
    for a reason that has nothing to do with the figures.
    """
    width = max([len(table.header)] + [len(r) for r in table.rows], default=0)

    def column(c: int) -> list[str]:
        return [r[c] if c < len(r) else "" for r in table.rows]

    ratios = [_numeric_ratio(column(c)) for c in range(width)]

    # Label column: the leftmost column that is mostly text. Leftmost rather
    # than "least numeric" because Schedule III statements put roman/arabic
    # level markers in their own narrow columns to the left of the caption, and
    # those are also non-numeric.
    label_col = 0
    best_len = -1.0
    for c in range(width):
        if ratios[c] > 0.5:
            continue
        cells = [v for v in column(c) if v.strip()]
        if not cells:
            continue
        mean_len = sum(len(v) for v in cells) / len(cells)
        header = table.header[c] if c < len(table.header) else ""
        # A header saying "Particulars"/"Description" settles it outright.
        if _LABEL_HDR_RE.search(header or ""):
            label_col = c
            break
        if mean_len > best_len:
            best_len, label_col = mean_len, c
    table.label_col = label_col

    note_col = None
    for c in range(width):
        header = table.header[c] if c < len(table.header) else ""
        if _NOTE_HDR_RE.search(header or "") and not _TOTAL_LABEL_RE.search(header or ""):
            note_col = c
            break
        if c != label_col and ratios[c] > 0.5:
            cells = [v for v in column(c) if v.strip()]
            # An untitled column of small integers sitting immediately right of
            # the label is the note column in every Schedule III layout seen.
            if cells and all(_NOTE_CELL_RE.fullmatch(v) for v in cells) and c <= label_col + 2:
                note_col = c
                break
    table.note_col = note_col

    value_cols = []
    for c in range(width):
        if c in (label_col, note_col):
            continue
        header = table.header[c] if c < len(table.header) else ""
        if _SERIAL_HDR_RE.search(header or ""):
            continue
        if ratios[c] >= 0.4:
            value_cols.append(c)
    table.value_cols = value_cols


def parse_markdown_tables(md: str, page_no: int, prefix: str = "t") -> list[Table]:
    """Split a markdown document into its pipe tables, roles resolved.

    A blank line, a non-pipe line, or a second header separator all end the
    current table. Docling emits one table per block with a separator row after
    the header, so the separator is the reliable boundary marker.
    """
    tables: list[Table] = []
    block: list[list[str]] = []
    title: str | None = None
    pending_title: str | None = None

    def flush() -> None:
        nonlocal block, title
        if len(block) >= 2:
            header, rows = block[0], [r for r in block[1:] if not is_separator(r)]
            t = Table(
                table_id=f"{prefix}{len(tables) + 1}",
                page_no=page_no,
                title=title,
                header=header,
                rows=rows,
            )
            _resolve_roles(t)
            tables.append(t)
        block = []
        title = None

    for line in md.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = split_row(stripped)
            if is_separator(cells):
                # A separator arriving when we already have body rows starts a
                # new table that happens to abut the previous one.
                if len(block) > 1:
                    header = block.pop()
                    flush()
                    block = [header]
                continue
            if not block and pending_title:
                title, pending_title = pending_title, None
            block.append(cells)
        else:
            flush()
            if stripped and not stripped.startswith("<"):
                candidate = stripped.lstrip("#").strip()
                # Remember the nearest preceding prose line as the table's
                # caption -- but only if it could BE a caption. On a scanned
                # filing the line directly above a table is as often a stray
                # figure carried over from the table before it ("64,777"), a
                # unit note ("(Amount in '000)") or the running page header, and
                # taking those blindly gives every table a meaningless title and
                # loses the real heading a line or two further up.
                if _is_caption_like(candidate):
                    pending_title = candidate

    flush()
    return tables


def looks_like_total(label: str) -> bool:
    return bool(_TOTAL_LABEL_RE.search(label or ""))


#: A unit note, not a caption: "(Amount in '000)", "(Rs. in lakh)".
_UNIT_LINE_RE = re.compile(r"^\(?\s*(amount|figures|rs\.?|inr|₹)\b", re.I)


def _is_caption_like(line: str) -> bool:
    """Could this line be a table's heading?

    Rejects the three things that actually sit directly above a table on a
    scanned filing and are not its title: a bare figure orphaned from the table
    above, a units note, and a line with no letters in it at all.
    """
    if not line or len(line) > 200:
        return False
    if _UNIT_LINE_RE.match(line):
        return False
    letters = sum(1 for c in line if c.isalpha())
    if letters < 3:
        return False
    # Mostly digits and separators -> a stray row, not a heading.
    digits = sum(1 for c in line if c.isdigit())
    return digits <= letters
