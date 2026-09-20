"""Which reporting periods an uploaded document's DATA actually carries.

This exists because "what years can I answer about?" was being answered from
the wrong place. A document carries ONE ``financial_year`` in its
identification -- the year the filing is *for* -- and every surface that told
the model what was in scope reported only that. So a single filing looked like
a single year of data.

It is not. Every Indian statutory filing prints its comparative alongside the
current year: ``| Particulars | Note No. | Figures as at 31st March, 2024 |
Figures as at 31st March, 2023 |``. Both columns are extracted, both are in
``table_md``, and both are rendered to the user in the document pane. Only the
*metadata* said one year -- and on that basis the model told a user it could
not compute a year-on-year movement because "the previous year's financial
statements were not provided", while the previous year's figure sat in the
next column of the very table it was reading.

So periods are derived HERE from the extracted column headers themselves,
never from the filename, never from the identification block, and never from a
list of metrics anyone thought to enumerate. Whatever period columns the
document actually has are the periods it can be asked about -- that is the
whole design, and it is why this generalises to a document shape nobody
anticipated.

Two header shapes occur in the real corpus and both are handled:

*Absolute* -- the year is printed: "Figures as at 31st March, 2024",
"31st March 2025", "Year ended 31.03.2023", "FY 2023-24". The closing year is
what identifies the column, so a span ("2023-24") resolves to its later year.

*Relative* -- the column says only where it sits: "Current Year" / "Previous
Year", as SK-SPSU-SSLSA-010's balance sheet prints them. These are resolved
against the document's own identified financial year when there is one, and
reported as relative when there is not. A relative column is still a REAL
period of data; refusing to report it because it lacks a printed year is the
same silent-loss failure this module exists to end.

Nothing here infers a period that is not printed. A header that carries
neither a year nor a relative-period phrase is not a period column, and a
document whose headers yield nothing is reported as exactly that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

#: A four-digit year in a plausible reporting range. Bounded so a note
#: reference, an amount, or a CIN ("U91990OR2021NPL036045") cannot be read as
#: a year -- the CIN case is real and appears on the very document that
#: prompted this module.
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")

#: A financial-year span as filings print it: 2023-24, 2023-2024, 2023/24.
#: The CLOSING year identifies the column, matching how every other part of
#: this system names a year.
_SPAN_RE = re.compile(r"\b(19\d{2}|20\d{2})\s*[-/]\s*(\d{2}|\d{4})\b")

#: Columns that name their position rather than their date.
_RELATIVE_RE = re.compile(
    r"\b(current|previous|prior|preceding|corresponding)\s+(year|period)\b"
    r"|\bthis\s+year\b|\blast\s+year\b",
    re.I,
)
_IS_PRIOR_RE = re.compile(r"\b(previous|prior|preceding|corresponding|last)\b", re.I)

#: Header text that is structurally a period column even though it is neither
#: -- kept OUT deliberately. These are the label/reference columns, and
#: matching them as periods is how a note number becomes a year.
_NOT_A_PERIOD_RE = re.compile(
    r"^\s*(particulars?|description|items?|heads?|notes?\s*(no\.?)?|appendix|"
    r"sr\.?\s*no\.?|sl\.?\s*no\.?|s\.?\s*no\.?|#|schedule)\s*$",
    re.I,
)


@dataclass(frozen=True)
class Period:
    """One reporting period a document's tables actually carry."""

    #: The header exactly as printed, so a citation can be checked against the
    #: page rather than against this module's idea of a period.
    label: str
    #: The calendar year the column closes in, where it could be read.
    year: int | None
    #: "absolute" when the header printed a year, "relative" when it printed
    #: only a position ("Previous Year") -- which may still have been resolved
    #: to a year below, in which case `year` is set and this stays "relative"
    #: so the reader knows how it was arrived at.
    kind: str
    #: True when the column is the document's own comparative rather than its
    #: primary year. Only meaningful once `year` is known.
    is_comparative: bool = False

    def describe(self) -> str:
        if self.year and self.kind == "absolute":
            return f"{self.label} (year {self.year})"
        if self.year:
            return f"{self.label} (resolved to year {self.year})"
        return f"{self.label} (no year printed on the column)"


@dataclass
class PeriodCoverage:
    """A period, and how much of the document actually carries it."""

    period: Period
    tables: int = 0
    sample_table_ids: list[str] = field(default_factory=list)


def _header_cells(table_md: str) -> list[str]:
    """The header row's cells, or [] if this is not a pipe table.

    The first pipe line IS the header: docling and this pipeline both emit
    header-then-separator, and `tables.parse_markdown_tables` builds every
    stored table that way.
    """
    for line in (table_md or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        body = stripped.strip("|")
        return [c.strip() for c in body.split("|")]
    return []


def _closing_year(text: str) -> int | None:
    """The year a column closes in, from its header text.

    A span is resolved to its later year ("2023-24" -> 2024) because that is
    the year the column's figures are *as at*, and it is how the rest of this
    system names a year. A two-digit tail is expanded against its own century
    rather than assumed to be 20xx.
    """
    span = _SPAN_RE.search(text)
    if span:
        start = int(span.group(1))
        tail = span.group(2)
        if len(tail) == 4:
            return int(tail)
        # 2023-24 -> 2024; 1999-00 -> 2000.
        candidate = (start // 100) * 100 + int(tail)
        if candidate < start:
            candidate += 100
        return candidate

    years = [int(y) for y in _YEAR_RE.findall(text)]
    if not years:
        return None
    # A header naming two dates ("for the year ended 31.03.2024 and 2023")
    # closes on the later one.
    return max(years)


def period_for_header(header: str, document_year: int | None = None) -> Period | None:
    """The period a single column header denotes, or None if it denotes none.

    ``document_year`` is the closing year of the document's own identified
    financial year, used ONLY to resolve a relative column ("Previous Year")
    to a number. It never overrides a year the column printed itself, and it
    never invents one for a header that is not a period at all.
    """
    text = (header or "").strip()
    if not text or _NOT_A_PERIOD_RE.match(text):
        return None

    year = _closing_year(text)
    if year is not None:
        return Period(label=text, year=year, kind="absolute")

    relative = _RELATIVE_RE.search(text)
    if not relative:
        return None

    prior = bool(_IS_PRIOR_RE.search(text))
    resolved = None
    if document_year is not None:
        resolved = document_year - 1 if prior else document_year
    return Period(label=text, year=resolved, kind="relative", is_comparative=prior)


def _document_year(document: Any) -> int | None:
    """The closing year of the document's own identified financial year."""
    raw = getattr(document, "financial_year", None)
    if raw:
        year = _closing_year(str(raw))
        if year is not None:
            return year
    fy_end = getattr(document, "fy_end", None)
    try:
        return int(fy_end) if fy_end else None
    except (TypeError, ValueError):
        return None


def coverage(document: Any) -> list[PeriodCoverage]:
    """Every period the document's extracted tables carry, most recent first.

    Counted across tables rather than reported from the first one found: a
    period that appears on one note of forty is a much weaker basis for a
    year-on-year answer than one that appears on the face of every statement,
    and the model is told which it is looking at.
    """
    document_year = _document_year(document)
    found: dict[tuple[str, int | None], PeriodCoverage] = {}

    for table in getattr(document, "tables", None) or []:
        header_cells = _header_cells(table.get("table_md") or "")
        table_id = str(table.get("table_id") or "")
        seen_in_table: set[tuple[str, int | None]] = set()
        for cell in header_cells:
            period = period_for_header(cell, document_year)
            if period is None:
                continue
            # Key on the year where there is one, so "31st March, 2024" and
            # "Figures as at 31st March, 2024" are ONE period rather than two
            # spellings of it. Fall back to the label when no year was read.
            key = ("", period.year) if period.year is not None else (period.label.lower(), None)
            if key in seen_in_table:
                continue
            seen_in_table.add(key)
            entry = found.get(key)
            if entry is None:
                entry = PeriodCoverage(period=period)
                found[key] = entry
            entry.tables += 1
            if len(entry.sample_table_ids) < 3 and table_id:
                entry.sample_table_ids.append(table_id)

    entries = list(found.values())
    # Most recent first; undated columns last, since they cannot be placed.
    entries.sort(key=lambda e: (e.period.year is None, -(e.period.year or 0), e.period.label))

    # The comparative flag is only decidable once the full set is known: the
    # newest dated column is the document's own year and every older dated
    # column is a comparative it prints alongside.
    dated = [e for e in entries if e.period.year is not None]
    if len(dated) > 1:
        newest = dated[0].period.year
        for entry in dated:
            if entry.period.year != newest and not entry.period.is_comparative:
                entry.period = Period(
                    label=entry.period.label,
                    year=entry.period.year,
                    kind=entry.period.kind,
                    is_comparative=True,
                )
    return entries


def years_available(document: Any) -> list[int]:
    """The distinct closing years the document's data can answer about."""
    return sorted({e.period.year for e in coverage(document) if e.period.year}, reverse=True)


def report(document: Any, indent: str = "") -> str:
    """The period-coverage lines the upload tools show the model.

    Written to be acted on, not just read: the closing sentence tells the
    model plainly that a comparison across these periods needs no second
    upload, because the failure this module fixes was the model *declining* a
    question it had the data for.
    """
    entries = coverage(document)
    if not entries:
        return (
            f"{indent}Reporting periods in the extracted data: NONE could be read "
            "from the table column headers. Do not assume this document covers "
            "only one period -- ask the user, or read the statement headings, "
            "before answering anything period-specific."
        )

    lines = [f"{indent}Reporting periods present in this document's own tables:"]
    for entry in entries:
        role = ""
        if entry.period.is_comparative:
            role = " — comparative column"
        elif entry.period.year is not None and entry.period.year == (
            max((e.period.year for e in entries if e.period.year), default=None)
        ):
            role = " — the document's own reporting year"
        lines.append(
            f"{indent}  - {entry.period.describe()}{role}; on {entry.tables} table(s)"
            + (f" e.g. {', '.join(entry.sample_table_ids)}" if entry.sample_table_ids else "")
        )

    years = [e.period.year for e in entries if e.period.year]
    if len(set(years)) > 1:
        lines.append(
            f"{indent}This document carries MORE THAN ONE period side by side, so a "
            "year-on-year movement can be computed from THIS FILE ALONE. A second "
            "upload is NOT required, and saying the earlier year was not provided "
            "would be wrong: read both columns off the same row of the same table "
            "and quote each with its column heading."
        )
    elif len(entries) > 1:
        lines.append(
            f"{indent}More than one period column is present but not all of them "
            "print a year. Quote each figure with its column heading exactly as "
            "printed, and ask the user to confirm which years they denote before "
            "calling a movement a year-on-year change."
        )
    return "\n".join(lines)


def periods_sentence(documents: Iterable[Any]) -> str:
    """One line summarising in-document period coverage across all uploads."""
    per_doc = [(d, years_available(d)) for d in documents]
    multi = [(d, ys) for d, ys in per_doc if len(ys) > 1]
    if not multi:
        return ""
    parts = [
        f"{d.filename} carries {', '.join(str(y) for y in ys)}"
        for d, ys in multi
    ]
    return (
        "In-document comparatives available (no second upload needed): "
        + "; ".join(parts) + "."
    )
