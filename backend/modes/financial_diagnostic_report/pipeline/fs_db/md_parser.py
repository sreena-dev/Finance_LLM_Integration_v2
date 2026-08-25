"""
table_md (markdown pipe-table) → ParsedTable → CanonicalFact.

This is THE enabling transform for the arithmetic core: the DB stores every FS and
note table as clean pipe-markdown, but as *presentation*, not computable numbers.
This module turns each into typed rows (label / note / period → value) with the
column roles detected by profiling (not by position — the label is often not col 0,
there may be a Sr. No. column, and notes are alphanumeric like "2A").


Deterministic and dependency-free.
"""
from __future__ import annotations
import re
from .models import Row, ParsedTable, CanonicalFact

# nil / empty markers → None (absent), distinct from a real 0
_NIL = {"", "-", "–", "—", "nil", "na", "n/a", "--", "."}
_NUM_RE = re.compile(r"^[+-]?\d+(\.\d+)?$")

# CPSU / DPE-format statements print the sign as a MARKER standing OUTSIDE the figure —
# "(-)0.86", "(+) Rs. 18.66", "96.73 (-)" — which is neither the accountant's
# parenthesised negative "(0.86)" nor a bare "-0.86". The old parser matched NEITHER
# rule: such a cell does not end in ")", so the paren branch never fired, and "(-)0.86"
# fails `_NUM_RE` outright. The result was None, so the figure was DROPPED rather than
# mis-signed — the row read as empty, the line never bound, and every ratio depending on
# it abstained with no warning that a number had been thrown away.
_SIGN_PREFIX_RE = re.compile(r"^\(\s*([+-])\s*\)\s*")
_SIGN_SUFFIX_RE = re.compile(r"\s*\(\s*([+-])\s*\)$")
# Currency is stripped BEFORE the sign/paren rules, not after: it sits inside the sign
# marker ("(-) Rs. 96.73") and in front of the parenthesised negative ("₹ (1,234.50)"),
# and in both places it blocked the rule that had to fire.
_CURRENCY_RE = re.compile(r"[₹$]|\brs\.?|\binr\b", re.I)
# Typographic minus signs. Standing ALONE each is a nil marker (already in `_NIL`);
# in front of digits each is a real minus that `_NUM_RE` rejects.
_UNICODE_MINUS = "−–—"          # −  –  —
_NOTE_CELL_RE = re.compile(r"^\d+\s*[A-Za-z]?$")            # 2, 15, 2A, 11A
_SERIAL_HDR_RE = re.compile(r"^\s*(sr|sl|s)\.?\s*no\.?\s*$|^\s*#\s*$", re.I)
_NOTE_HDR_RE = re.compile(r"note", re.I)
_LABEL_HDR_RE = re.compile(r"particular|description|\bitem\b|head", re.I)
_PERIOD_HDR_RE = re.compile(r"as at|year ended|period ended|for the year|\b(19|20)\d{2}\b|31[\s.]*(st)?[\s.]*(march|dec)", re.I)


def parse_num(cell: str) -> float | None:
    """Parse an FS money cell. Handles Indian commas, ₹, (parens)=negative, the CPSU
    "(-)"/"(+)" sign markers, typographic minus signs, and nil→None."""
    if cell is None:
        return None
    s = str(cell).strip()
    if s.lower() in _NIL:
        return None
    # strip currency words/symbols first — they sit inside both the sign marker and the
    # parenthesised negative, and there they block the rule that has to fire
    s = _CURRENCY_RE.sub("", s).strip()
    if s.lower() in _NIL:                   # the cell was a bare currency symbol
        return None
    # An EXPLICIT "(-)"/"(+)" marker is AUTHORITATIVE and suppresses the parenthesised-
    # negative rule below. Filings that use the marker often ALSO print the figure in
    # parens — "(-) Payment during year | (119,947.74)" in the lease-liability movement
    # note — and that is the same sign stated twice, not a double negative. Reading both
    # would flip a figure that was already right.
    marked = None
    m = _SIGN_PREFIX_RE.match(s)
    if m:
        marked, s = m.group(1), s[m.end():].strip()
    else:
        m = _SIGN_SUFFIX_RE.search(s)
        if m:
            marked, s = m.group(1), s[:m.start()].strip()
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1].strip()
    # thousands separators (both , and Indian grouping)
    s = s.replace(",", "").replace(" ", "")
    if s and s[0] in _UNICODE_MINUS:        # −1,234 / –1,234 written with a dash glyph
        s = "-" + s[1:]
    if s.endswith("-") and len(s) > 1:      # trailing dash sometimes = 0 filler
        s = s[:-1]
    if not _NUM_RE.match(s):
        return None                         # incl. a bare "(-)"/"(+)": a sign, no figure
    v = float(s)
    if marked is not None:
        return -abs(v) if marked == "-" else abs(v)
    return -v if neg else v


def _split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_separator(cells: list[str]) -> bool:
    return all(re.fullmatch(r":?-{2,}:?", c or "-") or c == "" for c in cells) and any("-" in c for c in cells)


def _numeric_ratio(col_cells: list[str]) -> float:
    nonempty = [c for c in col_cells if c and c.strip().lower() not in _NIL]
    if not nonempty:
        return 0.0
    hits = sum(1 for c in nonempty if parse_num(c) is not None)
    return hits / len(nonempty)


def _value_count(col_cells: list[str]) -> int:
    return sum(1 for c in col_cells if parse_num(c) is not None)


# A money column is dense: most of a statement's rows carry a figure in it. `_numeric_ratio`
# alone cannot see this — it measures purity among NON-EMPTY cells, so a column holding two
# stray cells scores a perfect 1.0. ONGC's consolidated balance sheet has exactly that: an
# unheaded first column carrying the section markers "(1)"/"(2)" (read as -1.0/-2.0, since
# parentheses mean negative). It was admitted as a value column, became periods[0] — the
# column the resolver treats as THE current period — and, being empty on every real line,
# bound zero balance-sheet lines and silently killed every consolidated ratio.
_MIN_VALUE_DENSITY = 0.15
_MIN_VALUE_CELLS = 3


def _note_ratio(col_cells: list[str]) -> float:
    nonempty = [c for c in col_cells if c and c.strip().lower() not in _NIL]
    if not nonempty:
        return 0.0
    return sum(1 for c in nonempty if _NOTE_CELL_RE.match(c.strip())) / len(nonempty)


def _norm_label(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


# Schedule III MANDATES that trade payables be split into these two disclosure lines,
# so they appear in essentially every Ind AS balance sheet. Both begin with the word
# "Total", but they are LINE ITEMS under the Trade Payables head, not subtotals of
# anything. Classifying them as totals corrupts the row hierarchy (a total row closes
# its group), drops them out of section sums, and makes Trade Payables underivable —
# seen across the corpus, e.g. CPCL, where it left Total Current Liabilities unbindable.
_DUES_LINE_RE = re.compile(r"^total outstanding dues\b", re.I)


def _classify_role(label: str, has_vals: bool) -> str:
    lab = _norm_label(label).lower()
    # drop "a)", "(i)", "1." and the CPSU arithmetic markers "(+)" / "(-)" — an unstripped
    # "(-) Total outstanding dues ..." misses `_DUES_LINE_RE` and falls through to the
    # total branch, which closes the row group and re-creates the very defect that rule
    # exists to prevent
    lab_stripped = re.sub(r"^[\(\[]?\s*(?:[a-z0-9]{1,3}|[+-])\s*[\)\].]\s*", "", lab)
    lab_stripped = re.sub(r"^[-–—•*]\s*", "", lab_stripped)             # drop a leading bullet
    if _DUES_LINE_RE.match(lab_stripped):
        return "line" if has_vals else "header"
    if re.match(r"^(grand )?total\b|^sub[\s-]*total\b", lab_stripped) or re.search(r"\btotal\b", lab_stripped):
        if has_vals:
            return "total"
    if not has_vals:
        return "header"
    if not lab_stripped:
        return "sum"        # blank-label numeric row = an implicit subtotal
    return "line"


def _realign_short_row(cells: list[str], k: int) -> tuple[str, list[float | None] | None]:
    """Re-read a row that was extracted with FEWER cells than the table's profiled width.

    Column roles are profiled from the full-width rows, but a short row's content sits at
    the LEFT of the line, so those positions read the wrong cells. GAIL Gas 2024-25 is the
    reference case: its line rows carry 5 cells (serial | label | note | 2 figures) while
    every sub-total row carries 3 (label | 2 figures), so the profiled label_col=1 /
    value_cols=[3,4] read "Total Non Current Assets (A) | 5,157.55 | 4,720.98" as
    label="5,157.55", note="4,720.98" and NO values. All four grand totals vanished from a
    clean balance sheet and 0 of 20 balance-sheet keys bound. The same padding also blanked
    the labels of one-cell section headers ("| ASSETS |", "| Current Assets |"), so
    `_tag_sections` never fired and every section-scoped bind was filtered out — one defect,
    four dead ratio inputs.

    Read by CONTENT instead of position: the trailing `k` cells are the figures if they all
    parse as money or an explicit nil, and the label is the longest remaining text cell.
    Returns (label, values) with values=None when the row has no figure tail (a section
    header) — the label alone is still worth recovering, since section tagging needs it."""
    texts = [c for c in cells if (c or "").strip() and parse_num(c) is None]
    label = max(texts, key=len) if texts else ""
    if not k or len(cells) < k + 1:
        return label, None                    # no room for label + k figures
    tail = cells[-k:]
    nums = [parse_num(c) for c in tail]
    # every trailing cell must be a real figure or an explicit nil marker ("-", "n/a"),
    # so a row that merely happens to end in text is left alone rather than mangled
    if not all(n is not None or (c or "").strip().lower() in _NIL
               for n, c in zip(nums, tail)):
        return label, None
    return (label, nums) if any(n is not None for n in nums) else (label, None)


# Above this share of populated rows carrying TWO figures inside one candidate block, the
# block hypothesis is refuted: two columns that are routinely filled on the same row are two
# different measurements (quantity/value, gross/net), not one period printed across a rule.
_BLOCK_CONFLICT_TOLERANCE = 0.10


def _block_value(row: list[str], cols: list[int]) -> float | None:
    """The single figure a period-block carries on one row (leftmost wins on a tie)."""
    for ci in cols:
        v = parse_num(row[ci]) if ci < len(row) else None
        if v is not None:
            return v
    return None


def _period_column_blocks(value_cols: list[int], period_hdr_cols: list[int],
                          body: list[list[str]]) -> tuple[list[tuple[int, list[int]]], str]:
    """Group physical value columns into ONE GROUP PER REPORTING PERIOD.

    THE LAYOUT THIS EXISTS FOR
    --------------------------
    Indian annual reports routinely print each period across TWO physical columns: line
    items in an inner column, subtotals stepped out into an outer one. The header names the
    period once, so the header row is narrower than the body:

        | Particulars                        | Year ended 2024 | Year ended 2023 |
        | - Income tax expense               | 124,902.42 |            | 119,924.08 |        |
        | Net cash generated by operating "A" |           | 808,378.16 |            | 653,355.23 |

    Read as four independent periods — which is what column profiling does, since all four
    are numeric and dense — every SUBTOTAL is filed under the wrong year: ONGC FY2023-24's
    operating cash flow was being recorded as the prior year's figure. Nothing downstream
    can detect this, because the value is real, correctly parsed, and simply attached to the
    wrong period. `ocf` then fails the panel's every-year test and S04, S06 and S18 abstain
    on a filing that parses perfectly.

    WHY IT IS SAFE TO COLLAPSE THEM
    -------------------------------
    Four conditions must ALL hold, and the last is the one that does the real work:

      1. the header names at least one period, and fewer than there are value columns;
      2. the value columns divide exactly among the named periods (k >= 2 columns each);
      3. the value columns are contiguous — a gap means the grouping would be guesswork;
      4. THE DATA AGREES. Within a candidate block, a row carries at most one figure. A
         quantity/value or gross/net pair fills both cells on the same row and refutes the
         hypothesis, so the layout is left exactly as profiled.

    Condition 4 is evidence, not assumption: the rule fires only where the filing itself
    demonstrates that the columns are alternatives rather than distinct measurements. Where
    it does not fire, every column stays its own period and nothing changes.

    Returns (groups, warning) where each group is (column that NAMES the period, its
    physical columns). Blocks are named by the k-th period header IN ORDER, because a
    header narrower than its body is left-aligned over it.
    """
    trivial = [(c, [c]) for c in value_cols]
    n_named = len(period_hdr_cols)
    if n_named == 0 or len(value_cols) < 2 * n_named:
        return trivial, ""
    if value_cols != list(range(value_cols[0], value_cols[0] + len(value_cols))):
        return trivial, ""

    # The columns need not divide EXACTLY among the periods. A note schedule routinely
    # carries a trailing column the header never named — a sub-total rule, a footnote
    # marker column — leaving five value columns over two periods. Requiring exact
    # divisibility refused those outright, which is most of the note schedules in the
    # corpus (ONGC's exploratory-well movement is 5 over 2). So the remainder is folded
    # into the LAST block, and condition 4 below still has to accept the result: if the
    # trailing column is really a third period, rows will carry two figures inside that
    # block and the whole hypothesis is refused, exactly as before.
    k = len(value_cols) // n_named
    if k < 2:
        return trivial, ""
    blocks = [value_cols[i * k:(i + 1) * k] for i in range(n_named)]
    blocks[-1] = blocks[-1] + value_cols[n_named * k:]

    populated = conflicts = 0
    for r in body:
        for blk in blocks:
            n = sum(1 for ci in blk
                    if ci < len(r) and parse_num(r[ci]) is not None)
            if n:
                populated += 1
            if n > 1:
                conflicts += 1
    if populated == 0:
        return trivial, ""
    if conflicts / populated > _BLOCK_CONFLICT_TOLERANCE:
        return trivial, ""

    groups = [(period_hdr_cols[i], blocks[i]) for i in range(n_named)]
    return groups, (
        f"each reporting period is printed across {k} physical columns (line items and "
        f"stepped-out subtotals); columns grouped {[b for _n, b in groups]} so subtotals "
        f"are read into their own period rather than the next one")


def parse_table_md(md: str, table_id: str | None = None, statement: str | None = None,
                   unit: str | None = None, page: int | None = None) -> ParsedTable:
    warnings: list[str] = []
    grid = [_split_row(l) for l in (md or "").splitlines() if l.strip().startswith("|")]
    grid = [g for g in grid if not _is_separator(g)]
    if not grid:
        return ParsedTable(table_id, statement, [], [], unit=unit, page=page,
                           warnings=["empty or unparseable table_md"])

    header, body = grid[0], grid[1:]
    # The header is sometimes extracted SHORT — ONGC's consolidated P&L prints
    # "| Particulars | Note No. | Year ended ... |" over rows that actually carry an
    # unnamed leading column of section numerals ("| I | Revenue from operations | 37 |
    # ...").  Padding that header on the right (the old behaviour) shifts every column
    # role one place left: the label column resolves onto the numerals, the note column
    # onto the labels, and note numbers get read as money. Because the period columns sit
    # at the END of a financial table, a short header is missing LEADING columns, so pad
    # it on the left — using the most common body width, not the max, so one ragged row
    # cannot move the whole header.
    if body:
        widths = [len(g) for g in body]
        body_ncol = max(set(widths), key=widths.count)
        # Tightly guarded: exactly ONE column short, and the header must already name at
        # least one reporting period. Without both tests this misfires on headers that
        # are short for other reasons — BPCL's "| Particulars | Note No. | ₹ in crore |"
        # is a units banner naming no period at all, and left-padding it drove the label
        # column onto the figures and cost that filing every ratio.
        if (body_ncol - len(header) == 1
                and any(_PERIOD_HDR_RE.search(c or "") for c in header)):
            header = [""] + header
            warnings.append("header was one column short of the rows; assumed a missing "
                            "leading (unnamed) column")
    grid = [header] + body
    ncol = max(len(g) for g in grid)
    # Ragged BODY rows pad on the right. Blanket content-aware right-alignment was tried
    # here (treating every short row as missing LEADING cells) and measured WORSE across
    # the ONGC set — 249 ratios vs 294 — because chunk-level width differences are handled
    # at join time and re-aligning every row on top of that double-corrects. Keep the two
    # concerns separate: the join fixes chunk width, this only fills out short rows.
    #
    # `raw_body` keeps the PRE-PADDING widths, because a genuinely short row still has to
    # be rescued — see `_realign_short_row`, which is applied per row and ONLY where the
    # profiled columns yield nothing, so a row that already parses is never touched. That
    # narrowness is what distinguishes it from the blanket attempt above.
    raw_body = [list(g) for g in body]
    grid = [g + [""] * (ncol - len(g)) for g in grid]
    header, body = grid[0], grid[1:]

    cols = [[r[i] for r in body] for i in range(ncol)]
    numeric = [_numeric_ratio(c) for c in cols]
    noteish = [_note_ratio(c) for c in cols]

    # Header-driven roles FIRST — a serial/note column is numeric too, so it must be
    # claimed before value columns are chosen or it gets read as money.
    serial_col = next((i for i in range(ncol) if _SERIAL_HDR_RE.match(header[i] or "")), None)
    period_hdr_cols = [i for i in range(ncol)
                       if i != serial_col and _PERIOD_HDR_RE.search(header[i] or "")]
    note_col = next((i for i in range(ncol)
                     if i not in period_hdr_cols and i != serial_col
                     and _NOTE_HDR_RE.search(header[i] or "")), None)
    if note_col is None:                                    # no "Note" header → profile
        note_col = next((i for i in range(ncol)
                         if i not in period_hdr_cols and i != serial_col
                         and noteish[i] >= 0.6 and numeric[i] < 0.99), None)
    label_col = next((i for i in range(ncol)
                      if i not in period_hdr_cols and i not in (serial_col, note_col)
                      and _LABEL_HDR_RE.search(header[i] or "")), None)

    reserved = {c for c in (serial_col, note_col, label_col) if c is not None}
    nrows = len(body) or 1
    # A column named by a period header is trusted outright; one admitted purely on
    # numeric purity must ALSO be dense enough to be money rather than stray markers.
    inferred = {i for i in range(ncol)
                if i not in reserved and numeric[i] >= 0.5
                and _value_count(cols[i]) >= _MIN_VALUE_CELLS
                and _value_count(cols[i]) / nrows >= _MIN_VALUE_DENSITY}
    value_cols = sorted(set(period_hdr_cols) | inferred)
    dropped = [i for i in range(ncol)
               if i not in reserved and i not in value_cols and numeric[i] >= 0.5
               and _value_count(cols[i]) > 0]
    if dropped:
        warnings.append(f"ignored sparse numeric column(s) {dropped} — too few values to be "
                        f"a reporting period (likely section markers or footnote refs)")
    if not value_cols:                                     # fall back to most-numeric non-reserved
        cand = sorted((i for i in range(ncol) if i not in reserved),
                      key=lambda i: numeric[i], reverse=True)
        value_cols = [i for i in cand[:2] if numeric[i] > 0]
        warnings.append("value columns inferred by profiling (weak numeric headers)")

    if label_col is None:
        text_cols = [i for i in range(ncol)
                     if i not in value_cols and i != note_col and i != serial_col]
        if text_cols:
            label_col = max(text_cols, key=lambda i: sum(len(str(x)) for x in cols[i]))
        else:
            label_col = 0
            warnings.append("no clear label column; defaulted to col 0")

    # (naming column, physical columns that belong to that period). Normally one each.
    col_groups, block_note = _period_column_blocks(value_cols, period_hdr_cols, body)
    if block_note:
        warnings.append(block_note)

    periods = []
    for name_col, _cols in col_groups:
        h = _norm_label(header[name_col])
        periods.append(h if h else f"col_{name_col}")
    if len(set(periods)) != len(periods):                  # de-dupe blank/identical headers
        periods = [f"{p}#{i}" if periods.count(p) > 1 else p for i, p in enumerate(periods)]

    rows: list[Row] = []
    realigned = 0
    for j, r in enumerate(body):
        label = _norm_label(r[label_col]) if label_col is not None else ""
        note = None
        if note_col is not None:
            nv = _norm_label(r[note_col])
            note = nv if nv and nv.lower() not in _NIL else None
        vals = {periods[k]: _block_value(r, cols) for k, (_nc, cols) in enumerate(col_groups)}
        # Rescue ONLY a short row that the profiled columns read as empty. A row that
        # already yields a figure is never re-read, which is what keeps this from
        # double-correcting the chunk-width realignment done at join time.
        if len(raw_body[j]) < ncol and not any(v is not None for v in vals.values()):
            # `len(periods)`, not `len(value_cols)`: where periods were grouped into blocks
            # the two differ, and a short row still carries one figure PER PERIOD.
            alt_label, alt_vals = _realign_short_row(raw_body[j], len(periods))
            if alt_vals is not None:
                vals = {periods[k]: alt_vals[k] for k in range(len(periods))}
                label, note, realigned = _norm_label(alt_label), None, realigned + 1
            elif not label and alt_label:
                label, realigned = _norm_label(alt_label), realigned + 1
        role = _classify_role(label, any(v is not None for v in vals.values()))
        rows.append(Row(idx=j, label=label, note=note, role=role, values=vals, raw=r))
    if realigned:
        warnings.append(f"{realigned} short row(s) re-read by content — their cells sit "
                        f"left of the columns profiled from the full-width rows")

    return ParsedTable(table_id=table_id, statement=statement, periods=periods, rows=rows,
                       label_col=label_col, note_col=note_col, serial_col=serial_col,
                       value_cols=value_cols, unit=unit, page=page, warnings=warnings)


def to_facts(pt: ParsedTable, doc_id: str) -> list[CanonicalFact]:
    """Emit atomic CanonicalFacts (one per cell with a value) for the fact table."""
    out: list[CanonicalFact] = []
    for r in pt.rows:
        if r.role == "header":
            continue
        for period, v in r.values.items():
            if v is None:
                continue
            out.append(CanonicalFact(
                doc_id=doc_id, table_id=pt.table_id, statement=pt.statement,
                line_item=r.label or "(unlabelled)", note=r.note, period=period,
                value=v, role=r.role, unit=pt.unit, page=pt.page))
    return out
