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
#: "Note"/"Notes" is the Schedule III term; "Appendix" is what the Form-1/
#: Form-2 layout (Sikkim State Legal Services Authority and, per that layout's
#: own boilerplate, every body filing under the same prescribed format) calls
#: the identical column -- a bare reference number, not an amount. Verified as
#: a real gap, not a hypothetical one: on SK-SPSU-SSLSA-010's balance sheet,
#: the row-1 Appendix cell (whose LABEL is separately merged across four line
#: items -- a harder, different defect this does not fix) held garbled
#: multi-token content that also broke the positional fallback below --
#: `all(_NOTE_CELL_RE.fullmatch(v) ...)` requires EVERY cell in the column to
#: look like a clean bare number, and one merged cell was enough to fail that
#: for the whole column. Matching "Appendix" in the HEADER decides this from
#: text that OCR reads cleanly regardless of what happened to any one cell
#: beneath it, which is why it belongs ahead of the fallback rather than
#: patching the fallback's cell-shape check instead.
_NOTE_HDR_RE = re.compile(r"\bnotes?\b|\bappendix\b", re.I)
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


#: A parenthesized single letter (a-h), lowercase roman numeral, or bare 1-2
#: digit number -- Schedule III's own enumeration marks for the line items
#: inside a category, e.g. "(a) Short-term borrowings", "(ii) Intangible
#: assets", "(2) Current assets". Real filings print these WITHOUT a ruling
#: line between consecutive items -- only the category's own outer box -- and
#: TableFormer relies on ruling to find row boundaries. Two such markers
#: inside what docling reports as ONE row is the signature of exactly that:
#: two or more unruled line items merged into a single detected row. A
#: genuine single row does not print two of its own enumerators.
_ENUM_MARKER_RE = re.compile(r"\(\s*(?:[a-h]|[ivx]{1,4}|\d{1,2})\s*\)\s*", re.I)


def _split_merged_label(label: str) -> list[str] | None:
    """The label's own segments, each starting at its own enumerator marker.

    None when there are fewer than two markers -- nothing to split, and a
    single marker is exactly what a normal row looks like.
    """
    marks = list(_ENUM_MARKER_RE.finditer(label or ""))
    if len(marks) < 2:
        return None
    segments = []
    for i, m in enumerate(marks):
        start = m.start()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(label)
        segments.append(label[start:end].strip())
    return segments


def _split_merged_row(row: list[str], label_col: int) -> list[list[str]] | None:
    """One merged row split into several, or None if it must be left alone.

    Only the LAST segment keeps the row's own values; every earlier segment
    becomes its own row with nil cells. Safe specifically because Schedule
    III's unruled sub-items print in order, and when only ONE number exists
    across N merged labels, only one of those N line items was populated in
    the filing -- the one immediately before the numbers. Fabricating a value
    for an earlier, genuinely-blank item is never in play: they get nil, not
    a guess.

    Refuses outright -- returns None, leaving the row exactly as it was, to
    be withheld downstream the way it is today -- the moment ANY value cell
    shows a sign of holding more than one number: more than one
    whitespace-separated token, or digit grouping the parser already can't
    validate. Guessing how several real figures divide across several labels
    is precisely the class of error this function must never make.
    """
    if label_col >= len(row):
        return None
    segments = _split_merged_label(row[label_col])
    if segments is None:
        return None

    for c, cell in enumerate(row):
        if c == label_col:
            continue
        text = (cell or "").strip()
        if not text:
            continue
        if len(text.split()) > 1:
            return None
        parsed = parse_cell(text)
        if parsed.value is not None and parsed.grouping_odd:
            return None

    out: list[list[str]] = []
    for seg in segments[:-1]:
        blank = list(row)
        blank[label_col] = seg
        for c in range(len(blank)):
            if c != label_col:
                blank[c] = ""
        out.append(blank)
    last = list(row)
    last[label_col] = segments[-1]
    out.append(last)
    return out


def _split_merged_rows(rows: list[list[str]], label_col: int) -> list[list[str]]:
    out: list[list[str]] = []
    for row in rows:
        split = _split_merged_row(row, label_col)
        out.extend(split if split is not None else [row])
    return out


#: A wrapped/split column-header line -- "For the Year Ended" / "31st March
#: 2023" printed on two physical lines, or a units/caption line TableFormer
#: failed to keep inside its own header row. Extends vlm_read.py's own
#: `_is_header_ish` regex (which this deliberately does NOT edit -- that
#: function's scope is confined to vlm_read.py's compare/merge bookkeeping,
#: and 5 call sites there depend on its current narrow behaviour) with the
#: date/period terms that class of row is actually missing.
_HEADER_CONTINUATION_RE = re.compile(
    r"amount\s+in|rs\.?\s*in|in\s+'?000|\(₹|as\s+at|particulars"
    r"|for\s+the\s+year\s+ended|for\s+the\s+period\s+ended|year\s+ended"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+20\d{2}\b",
    re.I,
)


def merge_header_rows(table: Table) -> None:
    """Fold a wrapped header line back into ``table.header``, removing it
    from ``table.rows`` -- IN PLACE on both.

    Real shape: a cash-flow-statement header wraps as "For the Year Ended" /
    "31st March 2023" across two printed lines. When TableFormer does not
    recognise the second line as still part of the header, it becomes its
    own "data row" with a non-numeric "value" cell -- flagged
    `unreadable_text` downstream even though it was never a data row at all.

    Scoped to rows before the FIRST row that has ANY numeric value anywhere
    in it -- the natural boundary between the header/caption block and real
    data. That bound is what keeps this from ever touching a genuine data
    row deep in the table that happens to mention a month/year (e.g. an "as
    at" disclosure sentence): once real figures have started, this stops
    looking, for good, even if a later row's text would otherwise match.
    """
    width = max([len(table.header)] + [len(r) for r in table.rows], default=0)
    kept: list[list[str]] = []
    still_in_header_zone = True

    for row in table.rows:
        has_numeric = any(parse_cell(c).value is not None for c in row)
        if (
            still_in_header_zone and not has_numeric
            and _HEADER_CONTINUATION_RE.search(" ".join(row).lower())
        ):
            while len(table.header) < width:
                table.header.append("")
            for c in range(min(len(row), width)):
                text = (row[c] or "").strip()
                if not text:
                    continue
                existing = table.header[c].strip()
                table.header[c] = f"{existing} {text}".strip() if existing else text
            continue
        kept.append(row)
        if has_numeric:
            still_in_header_zone = False

    table.rows = kept


def redistribute_value_cells(table: Table) -> None:
    """Split a value cell holding N numeric tokens across N-1 BLANK sibling
    value columns in the same row, when the counts match exactly -- IN PLACE
    on ``table.rows``.

    The TableFormer defect this targets: two comparative-year figures
    printed as adjacent columns get merged into one cell (e.g.
    "15,00,000 1,50,000"), with the sibling column left blank.
    ``structure_repair.py``'s own module docstring documents this exact
    symptom on a different table ("18,600.00 28,65,46,137.60" in one cell,
    its neighbour blank). That module's fix rebuilds the whole row grid from
    OCR geometry; this is a narrower, geometry-free repair for the specific
    case where a single row's value cell plainly carries N numbers and
    exactly N-1 OTHER value columns in that same row are simply empty.

    Deterministic and refuses rather than guesses, exactly like
    ``_split_merged_row``'s own refusal discipline:

    - every whitespace-separated token must itself parse cleanly as a number
      (``parse_cell(token).value is not None``) -- a token that doesn't is
      left exactly as it was, falling through to the ordinary
      unreadable/rescue path;
    - the token count must equal EXACTLY 1 + the number of OTHER blank value
      columns in that row -- no ambiguity about how many slots there are;
    - tokens fill columns left-to-right by column INDEX order (not by which
      physical cell happened to hold the merged text), the same reading-order
      assumption every other part of this pipeline already makes.

    Never touches an already-populated cell -- a column with its own value
    is never treated as an available slot, whatever the merged cell says.
    """
    width = max([len(table.header)] + [len(r) for r in table.rows], default=0)
    for row in table.rows:
        while len(row) < width:
            row.append("")
        for c in table.value_cols:
            text = (row[c] or "").strip()
            if not text:
                continue
            tokens = text.split()
            if len(tokens) < 2:
                continue
            if any(parse_cell(t).value is None for t in tokens):
                continue

            blanks = [
                other for other in table.value_cols
                if other != c and not (row[other] or "").strip()
            ]
            if len(blanks) != len(tokens) - 1:
                continue

            targets = sorted([c] + blanks)
            for token, target_col in zip(tokens, targets):
                row[target_col] = token


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


def is_column_index_row(cells: list[str]) -> bool:
    """The Schedule III column-reference row: a literal ``1 | 2 | 3 | 4``
    printed under the header to number the columns for cross-reference, not a
    line of the statement.

    Verified as a real docling output shape, not a hypothetical one --
    OD-SPSU-SO-032's balance sheet reads it as an ordinary row with label
    ``"1"`` and values ``3``/``4`` in the money columns. Left in, it does
    double damage: ``verify._find_footings`` has no concept of a header row at
    all and walks ``table.rows`` directly, so ``3``/``4`` become candidate leaf
    values in subtotal discovery on every Schedule III filing that prints this
    row (which is most of them); and it produces a spurious
    ``readers_disagree`` in ``vlm_read.compare`` whenever a genuinely
    independent second reader -- correctly -- does not invent a matching row
    for it.

    Distinguished from a real row that happens to contain small numbers by
    requiring EVERY non-empty cell to be a positive integer in strict
    ascending consecutive order starting at 1 -- real money essentially never
    does that across a whole row, and a genuine one-two coincidence (two
    columns) is rare enough that three or more is required to fire.
    """
    values = [parse_cell(c).value for c in cells if (c or "").strip()]
    if len(values) < 3:
        return False
    if any(v is None or v != int(v) or v < 1 for v in values):
        return False
    ints = [int(v) for v in values]
    return ints == list(range(1, len(ints) + 1))


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
            header, rows = block[0], [
                r for r in block[1:]
                if not is_separator(r) and not is_column_index_row(r)
            ]
            t = Table(
                table_id=f"{prefix}{len(tables) + 1}",
                page_no=page_no,
                title=title,
                header=header,
                rows=rows,
            )
            _resolve_roles(t)
            # AFTER roles are resolved (label_col needs the original column
            # shape), BEFORE anything downstream sees the rows: verify.py's
            # footing checks and vlm_read.py's row alignment both operate on
            # `t.rows` directly, and both need the split, not the merge.
            t.rows = _split_merged_rows(t.rows, t.label_col)
            # Header-continuation rows next -- pulled out before anything
            # value-shaped is touched, since a header row has no numeric
            # tokens for redistribute_value_cells to act on anyway.
            merge_header_rows(t)
            # Then column-merged value cells, using the (now header-clean)
            # row list and the already-resolved value_cols.
            redistribute_value_cells(t)
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
