"""Working out a table's structure: geometry proposes, Gemma organises, and
geometry validates what Gemma said.

Where the digits come from is settled before this module runs: OCR read every
word in the table and gave each a box (`Token`). This module never reads a
figure. It decides which tokens are the label of a row, which is a note
reference, which belong to which figure column, which rows are headers or
totals or section headings, and which two printed lines are really one wrapped
label.

The order matters:

1. **Geometry first.** Tokens are clustered into candidate rows by vertical
   position and candidate columns by the right edge of their figures (amounts
   are right-aligned). This alone produces a usable grid for a clean table.
2. **Gemma organises.** The model sees the table image and the candidate rows
   and columns and answers only structural questions -- what role does each
   column play, what kind is each row, which rows continue each other. Its
   answer is a small JSON of ids and enum values; it writes no words or digits
   of its own, so there is nothing in it that could be mistaken for a figure.
3. **Geometry validates.** Every id must exist, be used once, and be
   consistent with where the token actually sits. A reply that fails any check
   is rejected, retried once with the failures listed, and if it fails again
   the geometry-only grid is used and the table is marked unconfirmed.

Arithmetic (footing) is not used to choose a structure here. It is a later
check *on* a structure, never the thing that picks one.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from statistics import median

from .config import Config
from .llm_client import CLIENT, KIND_STRUCTURE, parse_json
from .normalize import looks_numeric, parse_number
from .tabletypes import (
    KIND_BLANK, KIND_HEADER, KIND_HEADING, KIND_ITEM, KIND_SUBTOTAL, KIND_TOTAL, ROLE_IGNORE,
    ROLE_LABEL, ROLE_NOTE, ROLE_VALUE, Column, Row, TablePlan, TableRegion, Token,
)

logger = logging.getLogger(__name__)

_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_NOTE_TEXT_RE = re.compile(r"^(?:[A-Za-z]{1,2}[-.]?)?\d{1,3}[A-Za-z]?$")
#: Serial numbers that number the LINES of a statement: "1.", "II." (not
#: numeric), "(1)". They are part of the label, never a column of figures.
_ENUM_DOT_RE = re.compile(r"^\d{1,2}\.$")
_ENUM_PAREN_RE = re.compile(r"^\(\d{1,2}\)$")
_SMALL_INT_RE = re.compile(r"^\d{1,3}$")
#: What a column-heading line says. A section title above the first figures
#: ("EQUITY AND LIABILITIES") says none of it.
_HEADER_WORDS_RE = re.compile(
    r"particulars|notes?|amount|rs|rupees|₹|in\s+'?000|as\s+at|year\s+ended|schedule"
    r"|(?:19|20)\d{2}|march",
    re.I,
)


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------

@dataclass
class Geometry:
    rows: list[list[Token]]                    # candidate rows, top to bottom
    columns: list[Column]                      # [0] is the label column
    median_height: float
    margin: float
    notes: list[str] = field(default_factory=list)
    #: token id -> column, for the few tokens Gemma said sit under a different
    #: column than their position suggests (and geometry allowed).
    forced: dict[str, int] = field(default_factory=dict)
    #: Line-numbering tokens ("1.", "(2)"): label text, never figures.
    enumerators: set[str] = field(default_factory=set)

    @property
    def first_band_x0(self) -> float:
        return min((c.x0 for c in self.columns[1:]), default=float("inf"))


def find_enumerators(rows: list[list[Token]]) -> set[str]:
    ids: set[str] = set()
    for row in rows:
        for i, tok in enumerate(row):
            text = tok.text.strip()
            if _ENUM_DOT_RE.match(text):
                ids.add(tok.id)
            elif _ENUM_PAREN_RE.match(text) and i == 0 and len(row) > 1 and not looks_numeric(row[1].text):
                ids.add(tok.id)             # "(1) Basic (in rupees)" -- leftmost, followed by a label
    return ids


def _cluster_rows(tokens: list[Token], median_h: float) -> list[list[Token]]:
    ordered = sorted(tokens, key=lambda t: (t.yc, t.x0))
    rows: list[list[Token]] = []
    means: list[float] = []
    for tok in ordered:
        if rows and abs(tok.yc - means[-1]) <= 0.55 * median_h:
            rows[-1].append(tok)
            means[-1] = sum(t.yc for t in rows[-1]) / len(rows[-1])
        else:
            rows.append([tok])
            means.append(tok.yc)
    return [sorted(r, key=lambda t: t.x0) for r in rows]


def _amount_ratio(tokens: list[Token]) -> float:
    """Share of tokens that look like an amount rather than a small reference."""
    if not tokens:
        return 0.0
    hits = 0
    for t in tokens:
        p = parse_number(t.text)
        if p.kind == "dash" or (p.kind in ("number", "malformed") and (
            "," in t.text or "." in t.text or "(" in t.text or sum(c.isdigit() for c in t.text) >= 4
        )):
            hits += 1
    return hits / len(tokens)


_YEAR_ONLY_RE = re.compile(r"^(?:19|20)\d{2}$")


def is_amount(token: Token) -> bool:
    """A token that is plainly an amount, not a reference number or a year.

    Grouped or decimal figures, or four-plus digits that are not a bare year.
    A dash counts: it is a printed nil in a figure column.
    """
    text = token.text.strip()
    if not looks_numeric(text) or _ENUM_DOT_RE.match(text):
        return False          # "31st March, 2024" has digits and a comma and is a date, not an amount
    parsed = parse_number(text)
    if parsed.kind == "dash":
        return True
    if parsed.kind not in ("number", "malformed"):
        return False
    if _YEAR_ONLY_RE.match(text):
        return False
    return "," in text or "." in text or "(" in text or sum(c.isdigit() for c in text) >= 4


def is_column_number_row(row: list[Token]) -> bool:
    """A row like ``1  2  3  4`` printed under the headings to number the columns."""
    figures = [t for t in row if looks_numeric(t.text)]
    if len(figures) < 2 or len(figures) != len(row):
        return False
    return all(_SMALL_INT_RE.match(t.text.strip()) for t in figures)


def is_data_row(row: list[Token]) -> bool:
    """A line that carries an amount, including a small whole-number one.

    An amount is not always grouped or decimal: "460" is a real figure. A row
    counts as data if it holds an amount-shaped token, or if it holds a label
    (words) together with a figure that is not a year, an enumerator or a
    column number. Headings hold neither: their figures are years, and a
    row of column numbers ("1 2 3 4") has no words.
    """
    if is_column_number_row(row):
        return False
    if any(is_amount(t) for t in row):
        return True
    ordered = sorted(row, key=lambda t: t.x0)

    glued: set[int] = set()
    for i in range(1, len(ordered)):
        prev, tok = ordered[i - 1], ordered[i]
        close = (tok.x0 - prev.x1) < max(tok.height, prev.height)
        # A number printed right up against the words before it -- or against a number
        # that is itself part of that phrase -- belongs to the phrase ("Mumbai - 400 001").
        # An amount sits a whole column away from its description.
        if close and (not looks_numeric(prev.text) or (i - 1) in glued):
            glued.add(i)

    def glued_to_text(i: int) -> bool:
        return i in glued

    figures = [
        t for i, t in enumerate(ordered)
        if looks_numeric(t.text) and not _YEAR_ONLY_RE.match(t.text.strip())
        and not _ENUM_DOT_RE.match(t.text.strip()) and not glued_to_text(i)
    ]
    words = [t for t in row if not looks_numeric(t.text) and sum(c.isalpha() for c in t.text) >= 3]
    long_text = [t for t in words if sum(c.isalpha() for c in t.text) >= 8]
    if len(long_text) >= 2:
        # A line item has ONE description. Two long pieces of text on a line are a
        # letterhead or an address ("Universal Insurance Building ... Mumbai - 400 001"),
        # whose pin code is not an amount.
        return False
    return bool(figures) and bool(words)


def data_start(rows: list[list[Token]]) -> int:
    """Index of the first row that carries a real amount; everything before it is heading.

    Heading rows hold years, column numbers and titles. Letting them into column
    detection invents columns (a "2024" heading and a "3" column number each
    become a band of their own). If no row carries an amount -- a table of small
    whole numbers -- the whole table is data.
    """
    for i, row in enumerate(rows):
        if is_data_row(row):
            return i
    return 0


def build_geometry(tokens: list[Token], region_bbox) -> Geometry:
    if not tokens:
        return Geometry([], [Column(0, ROLE_LABEL, region_bbox[0], region_bbox[2], region_bbox[0])], 0.0, 0.0)

    heights = [t.height for t in tokens if t.height > 0]
    median_h = median(heights) if heights else 30.0
    margin = 0.8 * median_h
    rows = _cluster_rows(tokens, median_h)

    enumerators = find_enumerators(rows)
    start = data_start(rows)
    heading_ids = {t.id for row in rows[:start] for t in row}
    numeric = [
        t for t in tokens
        if looks_numeric(t.text) and t.id not in heading_ids and t.id not in enumerators
    ]
    numeric.sort(key=lambda t: t.x1)
    clusters: list[list[Token]] = []
    tol = 2.0 * median_h
    for tok in numeric:
        if clusters and tok.x1 - clusters[-1][-1].x1 <= tol:
            clusters[-1].append(tok)
        else:
            clusters.append([tok])

    def support(cluster: list[Token]) -> int:
        return len({round(t.yc / max(1.0, median_h)) for t in cluster})

    strong = [c for c in clusters if len(c) >= 2 and support(c) >= 2]
    chosen = strong or [c for c in clusters if c]

    notes: list[str] = []
    bands: list[tuple[list[Token], str]] = []
    for cluster in chosen:
        ratio = _amount_ratio(cluster)
        pure_small = all(_NOTE_TEXT_RE.match(t.text.strip()) for t in cluster)
        if ratio < 0.2 and not pure_small:
            continue  # enumerators like "1." -- part of the label, not a column
        bands.append((cluster, ROLE_NOTE if (ratio < 0.2 and pure_small) else ROLE_VALUE))

    # A leftmost small-integer band is only a note column if real amount
    # columns exist to its right; alone it is just the table's only figures.
    if bands and not any(role == ROLE_VALUE for _, role in bands):
        bands = [(c, ROLE_VALUE) for c, _ in bands]
    bands.sort(key=lambda b: min(t.x0 for t in b[0]))

    # A note column with a single reference in it never clusters (one token is
    # not a column). It is still recognisable: a small integer that is the LAST
    # thing printed before the figure columns on a row that also carries a
    # label. Offered as a note band only if it is not already inside one.
    value_x0s = [min(t.x0 for t in c) for c, role in bands if role == ROLE_VALUE]
    if value_x0s:
        first_x0 = min(value_x0s)
        in_bands = {t.id for c, _ in bands for t in c}
        lone = []
        for row in rows[start:]:
            before = [t for t in row if t.xc < first_x0 - margin]
            if len(before) >= 2 and before[-1].id not in in_bands and _NOTE_TEXT_RE.match(before[-1].text.strip()):
                lone.append(before[-1])
        if lone:
            spread = max(t.x1 for t in lone) - min(t.x1 for t in lone)
            existing = [b for b in bands if b[1] == ROLE_NOTE]
            if spread <= 1.5 * tol and not existing:
                bands.append((lone, ROLE_NOTE))
                bands.sort(key=lambda b: min(t.x0 for t in b[0]))

    columns: list[Column] = []
    label_right = min((min(t.x0 for t in c) for c, _ in bands), default=region_bbox[2]) - margin
    columns.append(Column(0, ROLE_LABEL, region_bbox[0], max(region_bbox[0], label_right), label_right))
    for i, (cluster, role) in enumerate(bands, start=1):
        columns.append(Column(
            index=i, role=role,
            x0=min(t.x0 for t in cluster), x1=max(t.x1 for t in cluster),
            right=median(t.x1 for t in cluster), members=list(cluster),
        ))
    return Geometry(rows, columns, median_h, margin, notes, enumerators=enumerators)


def _column_for(tok: Token, geometry: Geometry) -> int | None:
    """The figure column a token belongs to, or None (label / no column)."""
    bands = [c for c in geometry.columns[1:] if c.role != ROLE_IGNORE]
    if tok.id in geometry.enumerators:
        return None
    forced = geometry.forced.get(tok.id)
    if forced is not None and any(c.index == forced for c in bands):
        return forced
    if not bands or tok.xc < geometry.first_band_x0 - geometry.margin:
        return None
    if looks_numeric(tok.text):
        best = min(bands, key=lambda c: abs(tok.x1 - c.right))
        if abs(tok.x1 - best.right) <= 3.0 * geometry.median_height:
            return best.index
    for c in bands:
        if c.x0 - geometry.margin <= tok.xc <= c.x1 + geometry.margin:
            return c.index
    return -1  # sits in the figure area but in no column


def split_row(row_tokens: list[Token], geometry: Geometry):
    """(label tokens, note tokens, {col: tokens}, stray tokens) for one candidate row."""
    label: list[Token] = []
    note: list[Token] = []
    cells: dict[int, list[Token]] = {}
    stray: list[Token] = []
    role_of = {c.index: c.role for c in geometry.columns}
    for tok in row_tokens:
        col = _column_for(tok, geometry)
        if col is None:
            label.append(tok)
        elif col == -1:
            if looks_numeric(tok.text):
                stray.append(tok)
            else:
                label.append(tok)
        elif role_of.get(col) == ROLE_NOTE:
            note.append(tok)
        else:
            cells.setdefault(col, []).append(tok)
    return label, note, cells, stray


# ---------------------------------------------------------------------------
# default row kinds (used when Gemma is unavailable or did not classify a row)
# ---------------------------------------------------------------------------

def _default_kinds(geometry: Geometry) -> dict[int, str]:
    from .validate import looks_like_total

    kinds: dict[int, str] = {}
    # The first line that really is data. Decided by the same rule that keeps letterhead
    # lines and column-number rows out of column detection, so the two never disagree.
    first_data = next((i for i, row in enumerate(geometry.rows) if is_data_row(row)), None)
    if first_data is None:
        for i, row in enumerate(geometry.rows):
            if is_column_number_row(row):
                continue
            _, _, cells, _ = split_row(row, geometry)
            real = [
                t for toks in cells.values() for t in toks
                if parse_number(t.text).is_figure and not _YEAR_RE.fullmatch(t.text.strip())
            ]
            if real:
                first_data = i
                break
    for i, row in enumerate(geometry.rows):
        label, _, cells, stray = split_row(row, geometry)
        text = " ".join(t.text for t in label)
        has_fig = any(parse_number(t.text).is_figure for toks in cells.values() for t in toks)
        if is_column_number_row(row):
            kinds[i] = KIND_HEADER
        elif first_data is None or i < first_data:
            reads_like_header = bool(cells) or bool(stray) or bool(_HEADER_WORDS_RE.search(text))
            kinds[i] = KIND_HEADER if reads_like_header else (KIND_HEADING if text else KIND_BLANK)
        elif looks_like_total(text) and has_fig:
            kinds[i] = KIND_TOTAL
        elif not has_fig and not stray:
            kinds[i] = KIND_HEADING if text else KIND_BLANK
        else:
            kinds[i] = KIND_ITEM
    return kinds


# ---------------------------------------------------------------------------
# Gemma's structural answer
# ---------------------------------------------------------------------------

@dataclass
class Decisions:
    col_roles: dict[int, str] = field(default_factory=dict)
    row_kind: dict[int, str] = field(default_factory=dict)
    row_join: dict[int, str] = field(default_factory=dict)      # "next" | "prev"
    moves: dict[int, int] = field(default_factory=dict)         # token number -> column
    #: Suggestions that were ignored rather than acted on (a join whose row has
    #: figures of its own, a move to a non-figure column). Not fatal: the rest of
    #: the reply is sound, and ignoring one suggestion emits nothing wrong.
    dropped: list[str] = field(default_factory=list)


_ROW_KINDS = {KIND_HEADER, KIND_ITEM, KIND_SUBTOTAL, KIND_TOTAL, KIND_HEADING, KIND_BLANK}


#: Words a printed label cannot sensibly end on: if a line stops on one of these
#: the label carries on below.
_DANGLING_WORDS = {
    "and", "or", "of", "to", "in", "the", "for", "on", "from", "with", "at", "by", "over",
    "under", "against", "into", "between", "through", "as", "than", "a", "an", "&", "/",
    "before", "after", "less", "plus", "net", "excess", "total",
}
_ENUMERATOR_RE = re.compile(r"^\(?(?:[a-z]|[ivxlc]{1,4}|\d{1,2})[.)]\s", re.I)


def wrap_evidence(first: str, second: str) -> bool:
    """Does the printed text itself say `first` carries on into `second`?

    Gemma proposes which lines are one wrapped label, and it is not reliable at
    it: on a real filing it merged two complete line items ("Income from
    Sales/Services", note 12, into "Grants/Subsidies"). A merge changes a row's
    label, and labels are what everything downstream finds rows by, so a
    proposed merge is only acted on when the text supports it: the first line
    stops mid-phrase (an open bracket, a trailing hyphen or comma, a word like
    "and" or "of"), or the second line starts in lower case. A line that reads
    as a finished item on its own is never joined to the next.
    """
    first, second = first.strip(), second.strip()
    if not first or not second:
        return False
    if _ENUMERATOR_RE.match(second):
        return False                                   # "(b) ..." opens a new item
    if first.count("(") > first.count(")") or first.count("[") > first.count("]"):
        return True
    if first[-1] in "-,\u2013\u2014":
        return True
    if re.split(r"\s+", first)[-1].lower().strip(".,") in _DANGLING_WORDS:
        return True
    return second[0].islower() or second[0] in "[("


def reconcile_roles(
    geometry: Geometry, proposed: dict[int, str], shifted: bool,
) -> tuple[dict[int, str], list[str]]:
    """Gemma's column roles, checked against what is actually in each column.

    Position and content decide a column's role wherever they are clear, and
    Gemma is only believed where the page is ambiguous (a column of serial
    numbers versus one of note references, say). Two rules:

    * A column that is mostly amounts is a value column, whatever Gemma said. On
      a real balance sheet Gemma numbered its columns 1-4 when only 1-3 were
      shown; taking its roles at face value made the 2024 figures a "note"
      column and left only the 2023 figures, filed under the wrong year.
    * A column of small reference numbers stays a note column even if Gemma
      called it a value column (else the references would be summed as amounts).

    If Gemma listed a column past the last one shown, its numbering cannot be
    matched to the page at all, so none of its column roles are used.
    """
    if shifted:
        return {}, [
            "Gemma numbered its columns past the last one shown, so its column roles could not "
            "be matched to the page; positions decided them"
        ]
    final: dict[int, str] = {}
    notes: list[str] = []
    for col in geometry.columns[1:]:
        role = proposed.get(col.index)
        if role is None:
            continue
        members = col.members
        ratio = _amount_ratio(members) if members else 0.0
        amounts_column = bool(members) and ratio >= 0.5
        reference_column = (
            bool(members) and ratio < 0.2
            and all(_NOTE_TEXT_RE.match(t.text.strip()) for t in members)
        )
        if amounts_column and role != ROLE_VALUE:
            notes.append(f"column {col.index} is full of amounts, so it stays a value column (Gemma said {role})")
            final[col.index] = ROLE_VALUE
        elif reference_column and col.role == ROLE_NOTE and role == ROLE_VALUE:
            notes.append(f"column {col.index} holds note references, so it stays a note column (Gemma said value)")
            final[col.index] = ROLE_NOTE
        else:
            final[col.index] = role
    return final, notes


def _tok_num(token: Token) -> int:
    return int(token.id.rsplit(":", 1)[-1])


def _row_line(idx: int, row: list[Token]) -> str:
    return f"r{idx}: " + " ".join(f"[{_tok_num(t)}]{t.text}" for t in row)


_PROMPT = """You are shown an image of ONE table from a scanned financial statement, plus the OCR text found in it, already grouped by position into candidate ROWS and candidate COLUMNS. Describe the table's STRUCTURE only. Do not write, correct or guess any number or word.

COLUMNS (column 0 is always the label column and is already decided):
{columns}

ROWS (each token is shown as [token number]text):
{rows}

Reply with JSON only, in exactly this shape:
{{"columns":[{{"id":1,"role":"note|value|ignore"}}],"rows":[{{"id":0,"kind":"header|item|subtotal|total|heading|blank","join":null}}],"moves":[]}}

Rules:
- "columns": give one entry for each of columns {column_ids}. "note" = a column of small note/schedule reference numbers, "value" = a column of amounts, "ignore" = anything else (serial numbers, stray marks).
- "rows": give one entry for each of rows {row_ids}. kind "header" = column headings or the unit line; "item" = a line with its own figures; "subtotal" or "total" = a sum line; "heading" = a section title with no figures; "blank" = nothing useful.
- "join": use "next" when the row holds only the START of a label whose figures are on the row below; use "prev" when it holds only the CONTINUATION of the row above's label. Otherwise null. A joined row must have no figures of its own.
- "moves": normally empty. Only when a figure token is clearly printed under a different column than the one listed, give {{"token":<token number>,"column":<column id>}}.
"""

_RETRY_SUFFIX = "\n\nYour previous reply was rejected for these reasons; answer again with them fixed:\n{errors}\n"

_FIXED_COLUMNS_NOTE = "\nThe column roles are already decided ({roles}); answer with an empty \"columns\" list.\n"


def _column_text(geometry: Geometry, ids: list[int]) -> str:
    lines = []
    by_index = {c.index: c for c in geometry.columns}
    for cid in ids:
        col = by_index[cid]
        sample = []
        for row in geometry.rows:
            for tok in row:
                if _column_for(tok, geometry) == cid:
                    sample.append(tok.text)
                    break
            if len(sample) >= 4:
                break
        lines.append(f"{cid}: x={col.x0:.0f}-{col.x1:.0f}, first entries: {' | '.join(sample) or '(none)'}")
    return "\n".join(lines) or "(none)"


def validate_decisions(
    data, geometry: Geometry, row_ids: list[int], column_ids: list[int], token_nums: set[int],
    need_columns: bool,
) -> tuple[Decisions, list[str]]:
    """Check Gemma's reply against what is actually on the page.

    Everything it may say is an id or an enum, so the checks are: ids exist,
    each is used once, enums are in range, joins point at a real neighbour that
    carries no figures, and a moved token lands in a real figure column.
    """
    errors: list[str] = []
    out = Decisions()
    if not isinstance(data, dict):
        return out, ["the reply was not a JSON object"]

    if need_columns:
        cols = data.get("columns")
        if not isinstance(cols, list):
            errors.append('"columns" must be a list')
            cols = []
        seen: set[int] = set()
        for entry in cols:
            cid = entry.get("id") if isinstance(entry, dict) else None
            role = entry.get("role") if isinstance(entry, dict) else None
            if cid == 0:
                continue  # the label column is fixed; an entry for it changes nothing
            if cid not in column_ids:
                out.dropped.append(f"column id {cid!r} ignored: not one of the columns shown")
            elif cid in seen:
                out.dropped.append(f"second entry for column {cid} ignored")
            elif role not in (ROLE_NOTE, ROLE_VALUE, ROLE_IGNORE):
                # Keep the geometry's own role for this column and say so.
                seen.add(cid)
                out.dropped.append(f"column {cid} role {role!r} is not note, value or ignore; positions decide")
            else:
                seen.add(cid)
                out.col_roles[cid] = role
        shifted = any(
            isinstance(e, dict) and isinstance(e.get("id"), int) and e["id"] > max(column_ids, default=0)
            for e in cols
        )
        out.col_roles, role_notes = reconcile_roles(geometry, out.col_roles, shifted)
        out.dropped.extend(role_notes)
        # A column Gemma did not mention keeps the role positions gave it.
        geometry_values = [c.index for c in geometry.columns[1:] if c.role == ROLE_VALUE and c.index not in out.col_roles]
        if column_ids and not (
            any(r == ROLE_VALUE for r in out.col_roles.values()) or geometry_values
        ):
            errors.append("no column was classified as a value column")

    rows = data.get("rows")
    if not isinstance(rows, list):
        errors.append('"rows" must be a list')
        rows = []
    seen_rows: set[int] = set()
    for entry in rows:
        if not isinstance(entry, dict):
            out.dropped.append("a row entry that was not an object was ignored")
            continue
        rid, kind, join = entry.get("id"), entry.get("kind"), entry.get("join")
        if rid not in row_ids:
            out.dropped.append(f"row id {rid!r} ignored: not one of the rows shown")
            continue
        if rid in seen_rows:
            out.dropped.append(f"second entry for row {rid} ignored")
            continue
        seen_rows.add(rid)
        if kind not in _ROW_KINDS:
            # e.g. a join word put in the kind field: keep the geometry's kind.
            out.dropped.append(f"row {rid} kind {kind!r} is not recognised; positions decide")
        else:
            out.row_kind[rid] = kind
        if join in ("next", "prev"):
            out.row_join[rid] = join
        elif join not in (None, "null", ""):
            out.dropped.append(f"row {rid} join {join!r} ignored")
    # A row Gemma did not classify keeps the kind positions gave it.

    row_pos = {rid: i for i, rid in enumerate(row_ids)}

    def label_of(rid: int) -> str:
        return " ".join(t.text for t in split_row(geometry.rows[rid], geometry)[0])

    for rid, join in list(out.row_join.items()):
        row = geometry.rows[rid]
        _, note, cells, stray = split_row(row, geometry)
        pos = row_pos[rid]
        reason = None
        if cells or stray:
            # A note number on a wrapped label's last line is normal; figures are not.
            reason = "it has figures of its own"
        elif join == "next" and pos + 1 >= len(row_ids):
            reason = "no row follows in this reply"
        elif join == "prev" and pos == 0:
            reason = "no row precedes it in this reply"
        elif join == "next":
            if note:
                reason = "it carries its own note number, so it is a complete line item"
            elif not wrap_evidence(label_of(rid), label_of(row_ids[pos + 1])):
                reason = "its text does not read as continuing into the next line"
        elif not wrap_evidence(label_of(row_ids[pos - 1]), label_of(rid)):
            reason = "the previous line does not read as continuing into it"
        if reason:
            out.dropped.append(f"row {rid}: not joined ({join}) because {reason}")
            del out.row_join[rid]

    moves = data.get("moves") or []
    if not isinstance(moves, list):
        errors.append('"moves" must be a list')
        moves = []
    roles_now = {c.index: c.role for c in geometry.columns}
    roles_now.update(out.col_roles)
    tok_by_num = {_tok_num(t): t for row in geometry.rows for t in row}
    col_by_id = {c.index: c for c in geometry.columns}
    for entry in moves:
        tok = entry.get("token") if isinstance(entry, dict) else None
        col = entry.get("column") if isinstance(entry, dict) else None
        placed = tok_by_num.get(tok)
        target = col_by_id.get(col)
        if placed is not None and target is not None and _column_for(placed, geometry) not in (None, -1):
            # Figures are right-aligned to their column, so where a figure is printed is
            # strong evidence. A move is only honoured when the figure has no column of
            # its own, or already sits close to the one it is being moved to.
            if abs(placed.x1 - target.right) > 1.5 * geometry.median_height:
                out.dropped.append(
                    f"move of {placed.text!r} to column {col} ignored: it is printed under a different column"
                )
                continue
        if tok not in token_nums:
            out.dropped.append(f"move of token {tok!r} ignored: no such token")
        elif tok in out.moves:
            out.dropped.append(f"second move of token {tok} ignored")
        elif roles_now.get(col) not in (ROLE_VALUE, ROLE_NOTE):
            out.dropped.append(f"move of token {tok} to column {col!r} ignored: not a figure column")
        else:
            out.moves[tok] = col
    return out, errors


def _ask(prompt: str, image, doc_id: str | None, label: str):
    reply = CLIENT.chat(KIND_STRUCTURE, prompt, [image] if image is not None else None,
                        doc_id=doc_id, label=label)
    return reply, parse_json(reply.content) if reply.ok else None


def _decide_chunk(
    geometry: Geometry, row_ids: list[int], image, doc_id: str | None, label: str,
    fixed_roles: dict[int, str] | None,
) -> tuple[Decisions | None, list[str]]:
    column_ids = [c.index for c in geometry.columns[1:]]
    token_nums = {_tok_num(t) for row in geometry.rows for t in row}
    prompt = _PROMPT.format(
        columns=_column_text(geometry, column_ids),
        rows="\n".join(_row_line(i, geometry.rows[i]) for i in row_ids),
        column_ids=column_ids or "(none)",
        row_ids=f"{row_ids[0]}..{row_ids[-1]}" if row_ids else "(none)",
    )
    if fixed_roles is not None:
        prompt += _FIXED_COLUMNS_NOTE.format(roles=", ".join(f"{k}={v}" for k, v in sorted(fixed_roles.items())))

    errors: list[str] = []
    for attempt in range(2):
        text = prompt if attempt == 0 else prompt + _RETRY_SUFFIX.format(errors="\n".join(f"- {e}" for e in errors))
        reply, data = _ask(text, image, doc_id, f"{label}_a{attempt + 1}")
        if not reply.ok:
            return None, [f"model call failed: {reply.error}"]
        decisions, errors = validate_decisions(
            data, geometry, row_ids, column_ids, token_nums, need_columns=fixed_roles is None,
        )
        if not errors:
            if fixed_roles is not None:
                decisions.col_roles = dict(fixed_roles)
            return decisions, []
    return None, errors


# ---------------------------------------------------------------------------
# assembling the plan
# ---------------------------------------------------------------------------

def _apply(geometry: Geometry, decisions: Decisions | None, table_no: int, region: TableRegion,
           tokens: list[Token], crop_bbox, confirmed: bool, notes: list[str]) -> TablePlan:
    kinds = _default_kinds(geometry)
    joins: dict[int, str] = {}
    if decisions is not None:
        for cid, role in decisions.col_roles.items():
            for col in geometry.columns:
                if col.index == cid:
                    col.role = role
        kinds.update(decisions.row_kind)
        joins = decisions.row_join
        by_num = {_tok_num(t): t for t in tokens}
        for num, col_id in decisions.moves.items():
            tok = by_num.get(num)
            if tok is not None and any(c.index == col_id for c in geometry.columns):
                geometry.forced[tok.id] = col_id
                notes.append(
                    f"Table {table_no}: figure {tok.text!r} was placed under column {col_id} "
                    "on Gemma's structure read."
                )

    def has_amount(cand: list[Token]) -> bool:
        _, _, c, _ = split_row(cand, geometry)
        return any(is_amount(t) for toks in c.values() for t in toks)

    first_amount = next(
        (
            i for i, cand in enumerate(geometry.rows)
            if not is_column_number_row(cand) and (has_amount(cand) or is_data_row(cand))
        ),
        None,
    )

    column_headings_seen = False
    header_rows: list[list[Token]] = []
    kept: list[Row] = []
    pending_label: list[Token] = []
    pending_note: list[Token] = []
    prev_row: Row | None = None

    for i, cand in enumerate(geometry.rows):
        kind = kinds.get(i, KIND_ITEM)
        label, note, cells, stray = split_row(cand, geometry)
        join = joins.get(i)

        # Above the first row that carries an amount, a line with words sitting in a
        # figure column ("Figures as at 31st March,", a units line printed over a
        # column) is a column heading, whatever it was called.
        if (
            first_amount is not None and i < first_amount and kind != KIND_HEADER
            and cells and not any(is_amount(t) for toks in cells.values() for t in toks)
        ):
            kind = KIND_HEADER

        if kind == KIND_HEADER and any(
            is_amount(t) for toks in cells.values() for t in toks
        ):
            # Column headings do not carry amounts. A row that does is a line item
            # the model mislabelled; treating it as a heading would drop the row
            # and put its figures into the column names.
            kind = KIND_ITEM
            notes.append(
                f"Table {table_no}: a row Gemma called a heading carries amounts, so it was kept as a line item."
            )

        if kind == KIND_HEADER and any(
            is_amount(t) for r in kept for toks in r.cells.values() for t in toks
        ) and not is_column_number_row(cand) and (label or note):
            # A "header" after the figures have started is not a column heading
            # (those sit above the data); dropping it would lose a label line.
            kind = KIND_HEADING
        if kind == KIND_HEADER and column_headings_seen and not cells and not is_column_number_row(cand) and label:
            # Words in the label area only, printed AFTER the column headings: a section
            # title ("Tangibles -"), not part of the headings.
            kind = KIND_HEADING
        if kind == KIND_HEADER:
            if not is_column_number_row(cand):
                header_rows.append(cand)
                worded_columns = sum(
                    1 for toks in cells.values()
                    if any(not looks_numeric(t.text) and sum(ch.isalpha() for ch in t.text) >= 3 for t in toks)
                )
                if worded_columns >= 2:
                    # Words over TWO OR MORE figure columns: real column headings. A title or a
                    # letterhead line has words over at most one.
                    column_headings_seen = True
            continue
        if kind == KIND_BLANK and not join:
            if not (label or note or cells or stray):
                continue
            # "Blank" for a line that has printed text loses that text. Keep it as
            # a heading line instead: label preserved, no figures invented.
            kind = KIND_HEADING
        if join == "next":
            pending_label.extend(label)
            pending_note.extend(note)
            continue
        # Gemma often marks BOTH halves of one wrapped label ("next" on the first
        # line, "prev" on the second). Once the first line has been carried
        # forward the merge is already done, and the second line is the ordinary row.
        if join == "prev" and prev_row is not None and not pending_label:
            prev_row.label_tokens.extend(label)
            if not prev_row.note_tokens:
                prev_row.note_tokens = list(note)
            prev_row.y1 = max(prev_row.y1, max((t.y1 for t in cand), default=prev_row.y1))
            continue

        row = Row(
            index=len(kept), kind=kind if kind != KIND_BLANK else KIND_ITEM,
            y0=min(t.y0 for t in cand), y1=max(t.y1 for t in cand),
            label_tokens=pending_label + label, note_tokens=note or pending_note,
            cells=cells, stray=stray,
        )
        if pending_label:
            row.y0 = min(row.y0, min(t.y0 for t in pending_label))
        pending_label, pending_note = [], []
        kept.append(row)
        prev_row = row

    # column headers from the header rows. A heading is often wider than the
    # figures beneath it (a long date over short numbers), so a token is given
    # to the figure column it overlaps most, not the one whose band contains its
    # centre.
    columns = [c for c in geometry.columns if c.role != ROLE_IGNORE]
    figure_cols = [c for c in columns if c.role != ROLE_LABEL]
    first_x0 = min((c.x0 for c in figure_cols), default=float("inf"))
    parts_by_col: dict[int, list[Token]] = {c.index: [] for c in columns}
    def alignment(tok: Token, col: Column) -> float:
        """How far a heading is from lining up with a column, on its best edge.

        Headings are right-aligned, centred or left-aligned over their figures,
        and are often wider than them; whichever edge matches is the one that
        counts.
        """
        return min(
            abs(tok.x1 - col.x1),
            abs(tok.xc - (col.x0 + col.x1) / 2),
            abs(tok.x0 - col.x0),
        )

    def covers(tok: Token, col: Column) -> bool:
        """The heading sits over this column: at least half its width, or -- for a heading
        clearly wider than one column -- a fifth of it (a long "Year ended 31.03.2024"
        printed over two columns rarely lies evenly across both)."""
        overlap = min(tok.x1, col.x1) - max(tok.x0, col.x0)
        width = max(1.0, col.x1 - col.x0)
        if overlap >= 0.5 * width:
            return True
        return (tok.x1 - tok.x0) >= 1.5 * width and overlap >= 0.2 * width

    spanned_ids: set[int] = set()
    recent_header_rows = header_rows[-3:]
    for hrow in header_rows:
        cover = {id(tok): [c for c in figure_cols if covers(tok, c)] for tok in hrow}
        for tok in hrow:
            if figure_cols and tok.x0 < first_x0 - 2 * geometry.margin and tok.x1 > first_x0:
                # Starts out in the label area and runs on over the figures. That is a
                # statement title or a letterhead line centred across the table -- unless
                # a sibling heading sits beside it on the same line, in which case it is
                # simply a wide heading right-aligned over its own column.
                siblings = [
                    o for o in hrow
                    if o is not tok and o.xc >= first_x0 - geometry.margin
                    and not looks_numeric(o.text) and sum(ch.isalpha() for ch in o.text) >= 3
                ]
                if not siblings:
                    continue
            if looks_numeric(tok.text) and not _YEAR_RE.fullmatch(tok.text.strip()):
                continue        # a stray number (a pin code) is not a heading word
            spanned = cover[id(tok)]
            # A heading spans columns only if no OTHER heading on the same line also
            # sits over them. A wide heading right-aligned over its own column can
            # overlap the neighbour too, and that neighbour has a heading of its own.
            alone = all(
                not ({c.index for c in cover[id(other)]} & {c.index for c in spanned})
                for other in hrow if other is not tok
            )
            if len(spanned) >= 2 and alone:
                # "Year ended 31.03.2024" printed once over an item column and a
                # subtotal column: both columns are that year.
                for c in spanned:
                    parts_by_col[c.index].append(tok)
                    spanned_ids.add(c.index)
                continue
            reach = [
                c for c in figure_cols
                if min(tok.x1, c.x1 + geometry.margin) - max(tok.x0, c.x0 - geometry.margin) > 0
            ]
            if not reach and figure_cols and tok.xc >= first_x0 - geometry.margin:
                reach = figure_cols
            if reach:
                parts_by_col[min(reach, key=lambda c: alignment(tok, c)).index].append(tok)
            elif columns and hrow in recent_header_rows:
                # Text in the label area: the label column's heading is the line(s)
                # just above the data, not a logo or company name further up.
                parts_by_col[columns[0].index].append(tok)
    for col in columns:
        parts = sorted(parts_by_col[col.index],
                       key=lambda t: (round(t.yc / max(1.0, geometry.median_height)), t.x0))
        col.header = " ".join(t.text for t in parts).strip()
        years = _YEAR_RE.findall(col.header)
        col.period = years[0] if len(set(years)) == 1 else None

    # Two neighbouring columns under one heading (items and their subtotals) would carry
    # the same name; tell them apart without changing which year each belongs to.
    value_cols = [c for c in columns if c.role == ROLE_VALUE]
    for left, right in zip(value_cols, value_cols[1:]):
        if (
            left.header and left.header == right.header and "(column " not in left.header
            and left.index in spanned_ids and right.index in spanned_ids
        ):
            left.header, right.header = f"{left.header} (column 1 of 2)", f"{right.header} (column 2 of 2)"
            left.shares_heading = right.shares_heading = True

    header_lines = [" ".join(t.text for t in hrow) for hrow in header_rows]
    plan = TablePlan(
        region=region, table_no=table_no, tokens=tokens, columns=columns, rows=kept,
        header_lines=header_lines, structure_confirmed=confirmed, notes=notes, crop_bbox=crop_bbox,
    )
    return plan


def year_order_note(plan: TablePlan) -> str | None:
    """A note when two value columns print years in ascending order.

    Current year before previous year is how these filings print; ascending
    order is the signature of a current/previous swap. A signal only -- nothing
    is reordered here.
    """
    years = [(c.index, int(c.period)) for c in plan.value_columns if c.period]
    for (i1, y1), (i2, y2) in zip(years, years[1:]):
        if y1 < y2:
            return (
                f"Table {plan.table_no}: value columns print years {y1} then {y2} left to "
                "right (ascending); these filings print the current year first, so the "
                "year columns may be swapped. Figures are shown as read; check them against the scan."
            )
    return None


def header_notes(plan: TablePlan) -> list[str]:
    """Signals about a table's figure-column headings. Never changes anything.

    * years in ascending order (a current/previous swap signature);
    * two figure columns printing the same year;
    * some figure columns carrying a year and others not, so the year columns
      cannot be told apart from their headings.
    """
    out: list[str] = []
    if len(plan.value_columns) > 4:
        # A wide schedule (opening balance, additions, deletions, closing balance ...)
        # dates its columns in ascending order and repeats them across groups on purpose.
        return out
    swap = year_order_note(plan)
    if swap:
        out.append(swap)
    periods = [c.period for c in plan.value_columns]
    known = [p for p in periods if p]
    shared = {c.period for c in plan.value_columns if getattr(c, "shares_heading", False)}
    known_for_dupes = [p for p in known if p not in shared]
    if len(known_for_dupes) != len(set(known_for_dupes)):
        out.append(
            f"Table {plan.table_no}: two figure columns print the same year in their headings; "
            "check which is which against the scan."
        )
    if len(periods) >= 2 and known and len(known) < len(periods):
        out.append(
            f"Table {plan.table_no}: only some figure columns carry a year in their headings, so "
            "the year columns could not be checked against each other."
        )
    return out


def _dump_tokens(doc_id: str | None, table_no: int, region: TableRegion, tokens: list[Token]) -> None:
    """Write the OCR tokens beside the model logs so a bad table can be replayed offline."""
    base = Config.LLM_LOG_DIR
    if not base or not doc_id:
        return
    try:
        import json
        from pathlib import Path

        folder = Path(base) / doc_id / "tables"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"t{table_no}.json").write_text(json.dumps({
            "table_no": table_no,
            "page_no": region.page_no,
            "bbox": list(region.bbox),
            "title": region.title,
            "tokens": [
                {"id": t.id, "text": t.text, "bbox": list(t.bbox), "page_no": t.page_no, "conf": t.conf}
                for t in tokens
            ],
        }), encoding="utf-8")
    except Exception:  # an audit trail must never be the reason a table is lost
        logger.debug("could not write token dump", exc_info=True)


def load_dump(path) -> tuple[TableRegion, list[Token]]:
    """Read a token dump back: the region and tokens of one table, for replay."""
    import json

    data = json.loads(open(path, encoding="utf-8").read())
    region = TableRegion(page_no=data["page_no"], bbox=tuple(data["bbox"]), title=data.get("title"))
    tokens = [
        Token(id=t["id"], text=t["text"], bbox=tuple(t["bbox"]), page_no=t["page_no"], conf=t.get("conf"))
        for t in data["tokens"]
    ]
    return region, tokens


def build_plan(
    tokens: list[Token], region: TableRegion, table_no: int, crop_image, crop_bbox,
    *, use_llm: bool, doc_id: str | None = None,
) -> TablePlan:
    """The full structure step for one table."""
    _dump_tokens(doc_id, table_no, region, tokens)
    geometry = build_geometry(tokens, region.bbox)
    notes: list[str] = list(geometry.notes)
    if not geometry.rows or len(geometry.columns) < 2:
        return _apply(geometry, None, table_no, region, tokens, crop_bbox, False,
                      notes + [f"Table {table_no}: no figure columns were found; only its text was read."])

    if not use_llm:
        return _apply(geometry, None, table_no, region, tokens, crop_bbox, False,
                      notes + [f"Table {table_no}: structure was worked out from positions alone."])

    per_call = max(5, Config.STRUCTURE_ROWS_PER_CALL)
    all_ids = list(range(len(geometry.rows)))
    chunks = [all_ids[i:i + per_call] for i in range(0, len(all_ids), per_call)]

    merged = Decisions()
    roles: dict[int, str] | None = None
    confirmed = True
    for n, chunk in enumerate(chunks):
        image = crop_image
        if len(chunks) > 1 and crop_image is not None:
            image = _crop_rows(crop_image, geometry, chunk, crop_bbox)
        decisions, errors = _decide_chunk(
            geometry, chunk, image, doc_id, f"t{table_no}_c{n + 1}", roles,
        )
        if decisions is None:
            confirmed = False
            notes.append(
                f"Table {table_no}: Gemma's structure read could not be validated "
                f"({'; '.join(errors[:3])}); the layout was worked out from positions alone."
            )
            break
        roles = decisions.col_roles
        notes.extend(f"Table {table_no}: Gemma suggestion ignored - {d}." for d in decisions.dropped)
        merged.col_roles = roles
        merged.row_kind.update(decisions.row_kind)
        merged.row_join.update(decisions.row_join)
        merged.moves.update(decisions.moves)

    if not confirmed:
        geometry = build_geometry(tokens, region.bbox)   # discard any partial mutation
        return _apply(geometry, None, table_no, region, tokens, crop_bbox, False, notes)
    return _apply(geometry, merged, table_no, region, tokens, crop_bbox, True, notes)


def _crop_rows(crop_image, geometry: Geometry, chunk: list[int], crop_bbox):
    """The slice of a table crop covering the chunk's rows (plus header rows)."""
    if crop_image is None or crop_bbox is None:
        return crop_image
    y0 = min(t.y0 for i in chunk for t in geometry.rows[i]) - geometry.margin
    y1 = max(t.y1 for i in chunk for t in geometry.rows[i]) + geometry.margin
    top = max(0, int(y0 - crop_bbox[1]))
    bottom = min(crop_image.shape[0], int(y1 - crop_bbox[1]))
    return crop_image[top:bottom] if bottom > top else crop_image
