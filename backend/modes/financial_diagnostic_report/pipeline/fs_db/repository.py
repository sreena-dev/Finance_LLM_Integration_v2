"""
Read-only data access over finance_llm. Every SQL identifier comes from schema.py,
so a schema change is absorbed here + there only. Returns plain dicts / ParsedTables.
"""
from __future__ import annotations
import re
from dataclasses import replace
from . import db, schema as S
from .md_parser import parse_table_md
from .models import ParsedTable

_NOTE_TITLE_RE = re.compile(r"note[:\s#.\-]*\s*([0-9]+[A-Za-z]?)", re.I)

# Most filings do NOT write the word "note" above a note schedule. They print the schedule
# number and the caption: "8. Capital Work-in-Progress", "12. Trade receivables- Current",
# "10.1.1. Ageing schedule". Keyed on the word alone, `note_index` returned ZERO notes for
# ONGC FY2024-25 — a filing carrying 186 untyped financial tables — and every note-derived
# diagnostic abstained for want of a reader that was in fact working.
#
# The sub-numbering is deliberately kept: "10.1" (exploratory wells in progress) and "10.2"
# (intangible oil and gas assets in progress) are different schedules under one note, and
# collapsing them to "10" would merge two populations an auditor tests separately. The
# lookup in `find_note_table` matches on the leading component, so a request for note 10
# still finds both.
_NOTE_NUMBER_RE = re.compile(r"^\s*([0-9]{1,3}(?:\.[0-9]{1,3}){0,3})\.?\s+(?=[A-Za-z(])")

# A caption is a note only if it reads like an accounting caption. Without this the pattern
# above happily reads "2024. Something" or a page number as a schedule number.
_NOT_A_NOTE_CAPTION_RE = re.compile(r"^\s*(19|20)\d{2}\b")


def _page(row: dict) -> int | None:
    return row.get(S.TBL["page_pdf_start"]) or row.get(S.TBL["page_ocr_start"])


def flavor_of(row: dict) -> str:
    """standalone vs consolidated — keyed off section/title ('toc_section' is noisy)."""
    blob = " ".join(str(row.get(k) or "") for k in ("section", "title", "toc_section")).lower()
    if "consolidat" in blob:
        return "consolidated"
    return "standalone"          # default: the entity's own (standalone) statements


def _token_overlap(a: str, b: str) -> bool:
    """Do two labels share a meaningful noun? Guards note-to-face against wrong tables."""
    stop = {"total", "non", "current", "other", "the", "and", "for", "net", "financial",
            "assets", "liabilities", "of", "as", "at", "a", "b", "c", "i", "ii", "iii", "iv"}
    def toks(s):
        return {w for w in re.findall(r"[a-z]{4,}", (s or "").lower()) if w not in stop}
    return bool(toks(a) & toks(b))


def note_no_from_title(title: str | None, section: str | None = None) -> str | None:
    """The note/schedule number a table is printed under, or None.

    The explicit "Note 12" form is tried first on both fields, then the bare
    "12. Trade receivables" numbering, which is how most filings actually print it.
    """
    for s in (title, section):
        if s:
            m = _NOTE_TITLE_RE.search(s)
            if m:
                return m.group(1).upper()
    for s in (title, section):
        if not s or _NOT_A_NOTE_CAPTION_RE.match(s):
            continue
        m = _NOTE_NUMBER_RE.match(s)
        if m:
            return m.group(1).upper()
    return None


# ---- document / entity resolution -----------------------------------------
def get_document(doc_id: str) -> dict | None:
    d, t = S.DOC, S.DOCUMENTS
    return db.one(
        f"SELECT {d['id']} doc_id, {d['company']} company, {d['fy_start']} fy_start, "
        f"{d['fy_end']} fy_end, {d['name']} doc_name, {d['pages_ocr']} pages_ocr, "
        f"{d['pages_pdf']} pages_pdf, {d['total_tables']} total_tables, "
        f"{d['total_chunks']} total_chunks FROM {t} WHERE {d['id']}=%s", (doc_id,))


def resolve(doc_id: str | None = None, company: str | None = None,
            fy_end: int | None = None) -> dict | None:
    if doc_id:
        return get_document(doc_id)
    d, t = S.DOC, S.DOCUMENTS
    if company and fy_end:
        return db.one(
            f"SELECT {d['id']} doc_id, {d['company']} company, {d['fy_end']} fy_end "
            f"FROM {t} WHERE {d['company']} ILIKE %s AND {d['fy_end']}=%s LIMIT 1",
            (company, fy_end))
    return None


def list_documents(company: str | None = None, limit: int = 25) -> list[dict]:
    d, t = S.DOC, S.DOCUMENTS
    where, params = "", []
    if company:
        where = f"WHERE {d['company']} ILIKE %s"
        params.append(f"%{company}%")
    return db.query(
        f"SELECT {d['id']} doc_id, {d['company']} company, {d['fy_start']} fy_start, "
        f"{d['fy_end']} fy_end FROM {t} {where} "
        f"ORDER BY {d['company']}, {d['fy_end']} DESC LIMIT {int(limit)}", tuple(params))


# ---- statement coverage (req 2 precheck) ----------------------------------
def statement_coverage(doc_id: str) -> dict:
    t, c = S.TABLE_CHUNKS, S.TBL
    rows = db.query(
        f"SELECT {c['stmt_type']} stmt, count(*) n FROM {t} "
        f"WHERE {c['doc']}=%s AND {c['stmt_type']} IS NOT NULL GROUP BY {c['stmt_type']}",
        (doc_id,))
    counts = {r["stmt"]: r["n"] for r in rows}
    return {s: counts.get(s, 0) for s in S.PRIMARY_STMTS}


# ---- primary statement tables ---------------------------------------------
def get_statement_tables(doc_id: str, stmt_type: str) -> list[dict]:
    t, c = S.TABLE_CHUNKS, S.TBL
    return db.query(
        f"SELECT {c['id']} table_id, {c['title']} title, {c['section']} section, "
        f"{c['toc_section']} toc_section, {c['md']} table_md, {c['unit']} unit, "
        f"{c['currency']} currency, {c['note_refs']} note_refs, "
        f"{c['page_pdf_start']} page_pdf_start, {c['page_ocr_start']} page_ocr_start "
        f"FROM {t} WHERE {c['doc']}=%s AND {c['stmt_type']}=%s "
        f"AND {c['md']} IS NOT NULL ORDER BY length({c['md']}) DESC", (doc_id, stmt_type))


def _bs_completeness(md: str) -> int:
    md = (md or "").lower()
    return int("total assets" in md) + int("total equity" in md)


# A cash-flow statement is complete when it carries all three activity sections. It is NOT
# measured by the word "total": a Schedule III cash-flow statement routinely says "Net cash
# generated from operating activities" and never prints "Total" at all, so the generic
# total-presence tiebreaker below scores the real statement ZERO and hands the selection to
# whichever note happens to contain the word. That is exactly how ONGC FY2023-24 lost its
# cash-flow statement to note 80.4's restatement reconciliation, taking `ocf` — and with it
# S04, S06 and S18 — down on a filing that parses perfectly.
def _cf_completeness(md: str) -> int:
    md = (md or "").lower()
    return sum(int(k in md) for k in ("operating activities", "investing activities",
                                      "financing activities"))


def _completeness(md: str, stmt: str) -> int:
    """Statement-aware completeness. Same 0-2/0-3 scale, different evidence per statement."""
    if stmt == S.STMT_CF:
        return _cf_completeness(md)
    return _bs_completeness(md)


_STMT_KW = {
    S.STMT_BS: ["balance sheet"],
    S.STMT_PL: ["profit and loss", "statement of profit", "profit & loss"],
    S.STMT_CF: ["cash flow"],
    S.STMT_SOCE: ["changes in equity"],
}
_SUMMARY_KW = ["five year", "ten year", "fifteen", "summarised", "summary", "profile",
               "ratio", "highlights", "five-year"]

# Tables that TALK ABOUT a statement without BEING it. Each of these carries the statement's
# own keyword in its section heading and so scores the full +3 relevance — a reconciliation
# of restated cash flows is titled "…of Cash Flows", a hedge disclosure is titled "Cash Flow
# Hedge Accounting". They are notes, and a note must never be selected as the face statement.
#
# Kept deliberately narrow and phrase-anchored. Bare "reconciliation" is NOT here: a real
# cash-flow statement often ends with a reconciliation of cash and cash equivalents, and
# demoting on that word alone would throw away the statement it is part of.
_NOT_THE_STATEMENT_KW = [
    "reconciliation of restated", "restated items", "reconciliation of statement",
    "reconciliation of the statement", "reconciliation of profit and loss",
    "hedge accounting", "disclosures of effects", "effects of cash flow hedge",
]


def _stmt_relevance(row: dict, stmt: str) -> int:
    # section+title only — toc_section is noisy/mis-attributed in this DB and would
    # wrongly demote real statements whose toc got tagged 'FIVE YEAR PROFILE' etc.
    blob = " ".join(str(row.get(k) or "") for k in ("section", "title")).lower()
    score = sum(3 for kw in _STMT_KW.get(stmt, []) if kw in blob)
    score -= sum(4 for kw in _SUMMARY_KW if kw in blob)   # demote five-year/summary tables
    score -= sum(5 for kw in _NOT_THE_STATEMENT_KW if kw in blob)   # notes ABOUT a statement
    return score


def _norm_section(row: dict) -> str:
    return re.sub(r"\s+", " ", str(row.get("section") or "").strip().lower())


_SYNTHETIC_COL = re.compile(r"^col_\d+$", re.I)


def _named_periods(p: ParsedTable) -> list[str]:
    """Period columns the filing actually NAMED.

    `parse_table_md` invents `col_N` placeholders for columns whose header it could not
    read — a ragged trailing column, a merged cell, an OCR gap. Those placeholders are an
    artefact of the chunk, not of the statement, and they differ between two halves of one
    statement (0133 ends with col_3/col_4, 0134 with col_5). Comparing them would refuse
    every genuine continuation, so the comparison is over the named columns only, which is
    what actually establishes that two chunks measure the same periods.
    """
    return [c for c in p.periods if not _SYNTHETIC_COL.match(str(c))]


def _doc_order(row: dict) -> tuple:
    return (_page(row) if _page(row) is not None else 10**9, str(row.get("table_id") or ""))


def primary_statement(doc_id: str, stmt_type: str, flavor: str = "standalone") -> ParsedTable | None:
    """Representative parsed table for a statement, coherent within one flavor.

    Ranks by: statement relevance (avoid five-year/summary tables and notes ABOUT the
    statement) → statement-aware completeness → presence of a total → size, then MERGES
    the selected table with its continuations.

    WHY THE MERGE EXISTS
    --------------------
    A statement longer than a page is stored as several `table_chunks` under one section
    heading. Returning the single best-ranked chunk therefore returns HALF A STATEMENT and
    silently loses every line in the other half. ONGC FY2023-24's cash flow is split at the
    page break: chunk 0133 carries operating activities, chunk 0134 carries investing and
    financing. Selecting either one loses real lines — picking 0134 is what dropped `ocf`.

    THE SAFETY GATE IS THE PERIOD COLUMNS
    -------------------------------------
    A continuation is only merged when it parses to the SAME period columns as the primary.
    That is what separates a page-break continuation from a different table that happens to
    sit under the same heading: a restated-comparatives note or a sub-schedule carries
    different columns and is refused. Combined with the relevance floor (a continuation must
    score as high as the primary, so anything demoted by `_NOT_THE_STATEMENT_KW` can never be
    merged in), a merged statement cannot pick up rows measured on a different basis.

    Merged rows are deduplicated on (label, values) — the same line appearing in two chunks
    is one line — and the merge is recorded in `warnings` so it is visible in provenance
    rather than being an invisible rewrite of what the filing said.
    """
    tables = get_statement_tables(doc_id, stmt_type)
    if not tables:
        return None
    same = [t for t in tables if flavor_of(t) == flavor] or tables

    def rank(t: dict) -> tuple:
        return (_stmt_relevance(t, stmt_type),
                _completeness(t["table_md"], stmt_type),
                1 if re.search(r"total", t["table_md"] or "", re.I) else 0,
                len(t["table_md"] or ""))

    best = max(same, key=rank)
    parsed = parse_table_md(best["table_md"], table_id=best["table_id"], statement=stmt_type,
                            unit=best["unit"], page=_page(best))

    best_rel = _stmt_relevance(best, stmt_type)
    section = _norm_section(best)
    if not section:
        return parsed

    merged: list[ParsedTable] = []
    for t in same:
        if t["table_id"] == best["table_id"]:
            continue
        if _norm_section(t) != section or _stmt_relevance(t, stmt_type) < best_rel:
            continue
        cand = parse_table_md(t["table_md"], table_id=t["table_id"], statement=stmt_type,
                              unit=t["unit"], page=_page(t))
        if cand.rows and _named_periods(cand) and _named_periods(cand) == _named_periods(parsed):
            merged.append(cand)
    if not merged:
        return parsed

    order = {best["table_id"]: _doc_order(best)}
    for t in same:
        order.setdefault(t["table_id"], _doc_order(t))
    parts = sorted([parsed] + merged, key=lambda p: order.get(p.table_id, (10**9, "")))

    # Every row, in order, nothing dropped. Schedule III repeats blank and section rows, and
    # `binding.py` reads parent/child depth from the row SEQUENCE — collapsing repeats merges
    # sibling blocks and breaks the sums (`trade_payables`, `net_fixed_assets`). Chunks
    # cannot overlap: each is parsed with its own header consumed as a header.
    rows: list = []
    for part in parts:
        for r in part.rows:
            rows.append(replace(r, idx=len(rows)))

    parsed.rows = rows
    parsed.warnings = list(parsed.warnings) + [
        "merged continuation chunks under one section heading: "
        + ", ".join(str(p.table_id) for p in parts)]
    return parsed


# ---- note schedules (req 3a note-to-face, "associated notes") --------------
def note_index(doc_id: str, flavor: str = "standalone") -> dict[str, list[dict]]:
    """Map note_no -> candidate numeric note tables within ONE flavor
    (is_financial, untyped, note no. in title). Flavor coherence is what stops a
    standalone face line being tied to a consolidated note schedule."""
    t, c = S.TABLE_CHUNKS, S.TBL
    rows = db.query(
        f"SELECT {c['id']} table_id, {c['title']} title, {c['section']} section, "
        f"{c['toc_section']} toc_section, {c['md']} table_md, {c['unit']} unit, "
        f"{c['note_refs']} note_refs, {c['page_pdf_start']} page_pdf_start, "
        f"{c['page_ocr_start']} page_ocr_start "
        f"FROM {t} WHERE {c['doc']}=%s AND {c['is_financial']} IS TRUE "
        f"AND {c['stmt_type']} IS NULL AND {c['md']} IS NOT NULL", (doc_id,))
    idx: dict[str, list[dict]] = {}
    for r in rows:
        if flavor_of(r) != flavor:
            continue
        n = note_no_from_title(r["title"], r["section"])
        if n:
            idx.setdefault(n, []).append(r)
    return idx


def parse_note(row: dict) -> ParsedTable:
    return parse_table_md(row["table_md"], table_id=row["table_id"], statement="note",
                          unit=row["unit"], page=_page(row))


def find_note_table(doc_id: str, note_no: str, _idx: dict | None = None,
                    flavor: str = "standalone") -> ParsedTable | None:
    idx = _idx if _idx is not None else note_index(doc_id, flavor)
    cands = idx.get(str(note_no).upper())
    if not cands:
        return None
    return parse_note(max(cands, key=lambda r: len(r["table_md"] or "")))
