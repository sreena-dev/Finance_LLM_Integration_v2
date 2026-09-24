"""Turning an extracted table into the record the gateway and the UI consume.

The output shapes are a contract, not a design choice made here. The backend's
financial-statement tools, the document pane, the quality drawer and the cell
editor all read these fields, and some read them byte-for-byte:

* ``table_md`` is a GitHub pipe table: a header line, a ``---`` separator, then
  one line per row. The editor addresses a cell as line ``row_index + 2``, pipe
  column ``col_index`` -- so those indices are computed here from the text that
  is actually emitted, never from an internal grid that might differ.
* A cell that could not be established appears as its ``CellFinding.marker``
  (``[unreadable: ...]`` or ``[recovered X; second read, ...]``). Both are
  non-numeric as a whole on every parser in the stack, so a doubtful figure can
  never be summed or quoted as if it were sound.
* ``financial_stmt_type`` must be one of the four values the tools filter on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .identify import classify_statement, classify_statement_from_page, detect_units
from .models import (
    CONFIDENCE_LOW, CellFinding, FootingCheck, TableRecord,
)
from .normalize import parse_number
from .tabletypes import (
    KIND_ITEM, ROLE_NOTE, ROLE_VALUE, STATUS_EMPTY, STATUS_FLAGGED, STATUS_HANDWRITTEN,
    STATUS_OCR_ONLY, STATUS_STRUCK, STATUS_UNREADABLE, STATUS_VERIFIED, CellResult, TablePlan,
)
from .validate import FootRow, NoteLink, check_balance_sheet, check_cash_flow, check_footings

# "Refer note 12", "(Note 2A)", "Notes 3 and 4"
_NOTE_REF_RE = re.compile(r"\bnotes?\.?\s*(?:no\.?)?\s*(\d{1,3}[A-Za-z]?)", re.I)
_DESCRIPTION_LABELS = 12
_DESCRIPTION_CHARS = 400

_UNTRUSTED = {STATUS_FLAGGED, STATUS_UNREADABLE, STATUS_HANDWRITTEN, STATUS_STRUCK}


def note_refs(text: str) -> list[str]:
    return sorted({m.group(1).upper() for m in _NOTE_REF_RE.finditer(text or "")})


def _clean(text: str | None) -> str:
    """One line, no pipes: a stray '|' would shift every cell after it."""
    return re.sub(r"\s+", " ", (text or "").replace("|", "/")).strip()


def _finding(page_no: int, table_id: str, row_label: str, col_name: str, cell: CellResult,
             row_index: int, col_index: int) -> CellFinding:
    recovered = cell.status == STATUS_FLAGGED and cell.second_text not in (None, "", "?")
    return CellFinding(
        page_no=page_no,
        table_id=table_id,
        row_label=row_label,
        column=col_name,
        raw=cell.ocr_text,
        reasons=list(cell.reasons),
        row_index=row_index,
        col_index=col_index,
        recovered_text=cell.second_text if recovered else None,
        confidence=CONFIDENCE_LOW if recovered else None,
        recovery_origin="readers_disagree" if recovered else None,
    )


@dataclass
class TableInfo:
    """What the document-level rules need to know about one exported table."""

    table_id: str
    page_no: int
    title: str | None
    stmt_type: str | None
    #: (column position in `rows[..].values`, period year or None)
    columns: list[tuple[int, str | None]] = field(default_factory=list)
    #: (label, note ref, kind, {col: amount or None})
    rows: list[tuple[str, str, str, dict[int, float | None]]] = field(default_factory=list)


def foot_rows(plan: TablePlan, results: dict[int, dict[int, CellResult]]) -> list[FootRow]:
    out: list[FootRow] = []
    value_cols = [c.index for c in plan.value_columns]
    for row in plan.rows:
        values: dict[int, float | None] = {}
        for col in value_cols:
            cell = results.get(row.index, {}).get(col)
            if cell is None or cell.status == STATUS_EMPTY:
                continue
            if cell.status in _UNTRUSTED:
                values[col] = None
                continue
            parsed = parse_number(cell.ocr_text)
            values[col] = parsed.value if parsed.is_figure else None
        out.append(FootRow(row.label, row.kind, values))
    return out


def _confidence(footings: list[FootingCheck]) -> float | None:
    if not footings:
        return None
    return round(sum(1 for f in footings if f.passed) / len(footings), 4)


def add_footings(record: TableRecord, extra: list[FootingCheck]) -> None:
    """Attach document-level checks (cross-table) to the table they concern."""
    record.footings = list(record.footings) + list(extra)
    record.confidence = _confidence(record.footings)


def describe_table(plan: TablePlan) -> str | None:
    labels = [r.label for r in plan.rows if r.label and not r.label.startswith("[")][:_DESCRIPTION_LABELS]
    headers = [c.header for c in plan.columns if c.header]
    parts = headers + labels
    if not parts:
        return None
    return "; ".join(parts)[:_DESCRIPTION_CHARS]


def build_table_record(
    plan: TablePlan,
    results: dict[int, dict[int, CellResult]],
    doc_id: str,
    page_text: str,
    snippet_jpeg_b64: str | None,
    source_file: str | None,
) -> tuple[TableRecord, TableInfo]:
    short_id = f"t{plan.table_no}"
    page_no = plan.region.page_no
    title = plan.region.title or None

    value_cols = plan.value_columns
    note_col = next(
        (c for c in plan.columns if c.role == ROLE_NOTE and any(r.note_tokens or r.cells.get(c.index) for r in plan.rows)),
        None,
    )
    label_header = _clean(plan.columns[0].header) if plan.columns and plan.columns[0].header else "Particulars"

    header = [label_header]
    if note_col is not None:
        header.append("Note")
    col_names: dict[int, str] = {}
    for n, col in enumerate(value_cols, start=1):
        name = _clean(col.header) or f"Amount {n}"
        col_names[col.index] = name
        header.append(name)

    lines: list[list[str]] = []
    findings: list[CellFinding] = []
    info_rows: list[tuple[str, str, str, dict[int, float | None]]] = []
    agree = disagree = 0

    for row in plan.rows:
        body_index = len(lines)
        label = _clean(row.label)
        cells = [label]
        if note_col is not None:
            note_text = _clean(row.note) or _clean(" ".join(t.text for t in row.cells.get(note_col.index, [])))
            cells.append(note_text)
        values: dict[int, float | None] = {}
        for col in value_cols:
            cell = results.get(row.index, {}).get(col.index)
            if cell is None or cell.status == STATUS_EMPTY:
                cells.append("")
                continue
            if cell.status in (STATUS_VERIFIED, STATUS_OCR_ONLY):
                cells.append(_clean(cell.ocr_text))
                parsed = parse_number(cell.ocr_text)
                values[col.index] = parsed.value if parsed.is_figure else None
                if cell.status == STATUS_VERIFIED:
                    agree += 1
                continue
            finding = _finding(
                page_no, short_id, label or f"row {body_index + 1}", col_names[col.index],
                cell, body_index, len(cells),
            )
            findings.append(finding)
            cells.append(_clean(finding.marker))
            values[col.index] = None
            if cell.status == STATUS_FLAGGED and "readers_disagree" in cell.reasons:
                disagree += 1
        lines.append(cells)
        info_rows.append((label, _clean(row.note), row.kind, values))

    width = len(header)
    table_md = "\n".join(
        ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * width) + " |"]
        + ["| " + " | ".join(line + [""] * (width - len(line))) + " |" for line in lines]
    )

    statement_type = classify_statement(title) or classify_statement_from_page(page_text)
    scale, currency = detect_units("\n".join([title or "", page_text or "", *plan.header_lines]))

    foot = foot_rows(plan, results)
    cols = [c.index for c in value_cols]
    if statement_type == "cash_flow":
        # Sections are added across and each year prints an items column beside a
        # subtotal column, so "a total equals the rows above" does not apply.
        footings = check_cash_flow(short_id, page_no, foot, cols)
    else:
        footings = check_footings(short_id, page_no, foot, cols)
        if statement_type == "balance_sheet":
            footings += check_balance_sheet(short_id, page_no, foot, cols)
    confidence = _confidence(footings)
    compared = agree + disagree
    vlm_agreement = round(agree / compared, 4) if compared else None

    record = TableRecord(
        table_id=f"{doc_id}_{short_id}",
        doc_id=doc_id,
        table_title=title,
        table_md=table_md,
        page_ocr_start=page_no,
        page_ocr_end=page_no,
        financial_stmt_type=statement_type,
        toc_section=title,
        unit=scale,
        currency=currency,
        note_refs=note_refs(f"{title or ''} {page_text or ''}"),
        is_financial=statement_type is not None or bool(value_cols),
        bbox=[float(v) for v in plan.region.bbox],
        confidence=confidence,
        vlm_agreement=vlm_agreement,
        snippet_jpeg_b64=snippet_jpeg_b64,
        table_description=describe_table(plan),
        source_file=source_file,
        findings=findings,
        footings=footings,
        recovered=[],
    )
    info = TableInfo(
        table_id=f"{doc_id}_{short_id}", page_no=page_no, title=title, stmt_type=statement_type,
        columns=[(c.index, c.period) for c in value_cols], rows=info_rows,
    )
    return record, info


# ---------------------------------------------------------------------------
# cross-table: a note's total against the statement line that cites it
# ---------------------------------------------------------------------------

_NOTE_TITLE_RES = (
    re.compile(r"\bnote\s*[:.\-]?\s*(?:no\.?\s*)?(\d{1,3}[A-Za-z]?)\b", re.I),
    re.compile(r"^\s*(\d{1,3}[A-Za-z]?)\s*[.)]\s+\S"),
)
_STATEMENT_TYPES = ("balance_sheet", "profit_loss", "cash_flow", "statement_of_equity")


def _note_number(title: str | None) -> str | None:
    for rx in _NOTE_TITLE_RES:
        m = rx.search(title or "")
        if m:
            return m.group(1).upper()
    return None


def cross_table_links(infos: list[TableInfo]) -> list[NoteLink]:
    """Pair each statement line that cites a note with that note's total.

    Conservative on purpose. A pair is compared only when the note's table is
    identifiable by number, has a printed total, and a column whose period year
    matches the statement column's -- anything looser risks reporting a
    failure that is really two different columns being compared.
    """
    notes: dict[str, TableInfo] = {}
    for info in infos:
        if info.stmt_type in _STATEMENT_TYPES:
            continue
        number = _note_number(info.title)
        if number and number not in notes:
            notes[number] = info

    links: list[NoteLink] = []
    for info in infos:
        if info.stmt_type not in _STATEMENT_TYPES:
            continue
        for label, note, kind, values in info.rows:
            ref = (note or "").upper().strip()
            target = notes.get(ref)
            if not ref or target is None:
                continue
            total_row = next((r for r in reversed(target.rows) if r[2] in ("total", "subtotal")), None)
            if total_row is None:
                continue
            for col, period in info.columns:
                amount = values.get(col)
                if amount is None or period is None:
                    continue
                match = next(
                    (tc for tc, tp in target.columns if tp == period and total_row[3].get(tc) is not None),
                    None,
                )
                if match is None:
                    continue
                links.append(NoteLink(
                    statement_table_id=info.table_id, page_no=info.page_no, statement_label=label,
                    note_ref=ref, period=period, statement_value=amount,
                    note_table_id=target.table_id, note_total=total_row[3][match],
                ))
    return links
