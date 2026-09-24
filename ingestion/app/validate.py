"""Checks on extracted tables. Nothing here changes a figure.

A table can be structurally tidy and still wrong -- a digit misread, a column
swapped, a row lost. The statements themselves carry their own consistency
rules, so the extraction is checked against them:

* **Footing.** A subtotal or total equals the rows it sums.
* **Balance Sheet identity.** Total assets equal total equity and liabilities.
* **Cross-table.** A note's total equals the statement line that cites it.

Arithmetic *validates* structure; it does not define it. A row's kind (item,
subtotal, total) comes from the structure stage and is taken as given here. If
a rule fails the failure is reported and the figures stay exactly as read --
this module has no way to edit a table, by design.

Only figures that were actually read are used. A cell that is flagged or
unreadable is *unknown*, and a check that would need it is skipped rather than
counted as either passing or failing: nothing can be proved about an amount
nobody could read.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .models import FootingCheck
from .normalize import tolerance
from .tabletypes import KIND_ITEM, KIND_SUBTOTAL, KIND_TOTAL

logger = logging.getLogger(__name__)

_DEFAULTS = {
    "total_labels": [r"\btotal\b", r"\bgrand\s+total\b"],
    "balance_sheet": {
        "assets": r"^\s*total\s+assets\b",
        "equity_and_liabilities": [
            r"^\s*total\s+(?:equity\s+and\s+liabilities|liabilities\s+and\s+equity)\b",
        ],
    },
    "cross_table": {"enabled": True},
    "cash_flow": {
        "opening": r"\bopening\s+balance\b",
        "closing": r"\bclosing\s+balance\b",
        "net_change": r"^\s*net\s+(?:increase|decrease)\b",
        "operating": r"\boperating\s+activities\s*\(?a\)?\s*$",
        "investing": r"\binvesting\s+activities\s*\(?b\)?\s*$",
        "financing": r"\bfinancing\s+activities\s*$",
    },
}


def _load_rules() -> dict:
    try:
        import yaml

        path = Path(__file__).with_name("rules.yaml")
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        merged = dict(_DEFAULTS)
        merged.update({k: v for k, v in loaded.items() if v})
        return merged
    except Exception:  # missing PyYAML or file: the built-in rules still apply
        logger.warning("rules.yaml could not be loaded; using built-in rules", exc_info=True)
        return dict(_DEFAULTS)


RULES = _load_rules()
_TOTAL_RES = [re.compile(p, re.I) for p in RULES["total_labels"]]
_ASSETS_RE = re.compile(RULES["balance_sheet"]["assets"], re.I)
_EQ_LIAB_RES = [re.compile(p, re.I) for p in RULES["balance_sheet"]["equity_and_liabilities"]]


def looks_like_total(label: str | None) -> bool:
    return any(rx.search(label or "") for rx in _TOTAL_RES)


@dataclass
class FootRow:
    label: str
    kind: str
    #: column index -> amount. A key with value None means "a figure is printed
    #: but was not read reliably"; a missing key means the cell is empty.
    values: dict[int, float | None]


def check_footings(table_id: str, page_no: int, rows: list[FootRow], value_cols: list[int]) -> list[FootingCheck]:
    checks: list[FootingCheck] = []
    for col in value_cols:
        stack: list[tuple[str, float | None]] = []
        for row in rows:
            if col not in row.values:
                continue
            value = row.values[col]
            if row.kind == KIND_ITEM:
                stack.append((row.label, value))
                continue
            if row.kind not in (KIND_SUBTOTAL, KIND_TOTAL):
                continue
            if value is None or any(v is None for _, v in stack):
                # Printed but unreadable, or built on something unreadable:
                # nothing can be proved either way.
                stack = [(row.label, value)]
                continue

            matched = None
            for k in range(len(stack), 0, -1):
                parts = stack[-k:]
                total = sum(v for _, v in parts)
                if abs(total - value) <= tolerance(total, value):
                    matched = (k, total)
                    break
            if matched is not None:
                k, total = matched
                checks.append(FootingCheck(
                    table_id=table_id, page_no=page_no, subtotal_label=row.label,
                    printed=value, recomputed=total, difference=round(value - total, 4),
                    passed=True, component_labels=[l for l, _ in stack[-k:]],
                ))
                del stack[-k:]
            elif stack:
                total = sum(v for _, v in stack)
                checks.append(FootingCheck(
                    table_id=table_id, page_no=page_no, subtotal_label=row.label,
                    printed=value, recomputed=total, difference=round(value - total, 4),
                    passed=False, component_labels=[l for l, _ in stack],
                ))
                stack = []
            stack.append((row.label, value))
    return checks


_CF = {k: re.compile(p, re.I) for k, p in RULES["cash_flow"].items()}


def check_cash_flow(table_id: str, page_no: int, rows: list[FootRow], value_cols: list[int]) -> list[FootingCheck]:
    """The identities a cash flow statement must satisfy, column by column.

    closing = opening + net change, and net change = operating + investing +
    financing. A check is made only for a column in which every figure it needs
    was read; an unreadable figure makes it unprovable, not failed.
    """
    def find(kind: str) -> FootRow | None:
        return next((r for r in rows if _CF[kind].search(r.label or "")), None)

    opening, closing, change = find("opening"), find("closing"), find("net_change")
    operating, investing, financing = find("operating"), find("investing"), find("financing")
    checks: list[FootingCheck] = []
    for col in value_cols:
        def val(row: FootRow | None) -> float | None:
            return None if row is None else row.values.get(col)

        o, c, n = val(opening), val(closing), val(change)
        if None not in (o, c, n):
            checks.append(FootingCheck(
                table_id=table_id, page_no=page_no,
                subtotal_label=f"{closing.label} = {opening.label} + {change.label}",
                printed=c, recomputed=o + n, difference=round(c - (o + n), 4),
                passed=abs(c - (o + n)) <= tolerance(c, o + n),
                component_labels=[opening.label, change.label],
            ))
        a, b, f = val(operating), val(investing), val(financing)
        if None not in (a, b, f, n):
            checks.append(FootingCheck(
                table_id=table_id, page_no=page_no,
                subtotal_label=f"{change.label} = operating + investing + financing",
                printed=n, recomputed=a + b + f, difference=round(n - (a + b + f), 4),
                passed=abs(n - (a + b + f)) <= tolerance(n, a + b + f),
                component_labels=[operating.label, investing.label, financing.label],
            ))
    return checks


def check_balance_sheet(table_id: str, page_no: int, rows: list[FootRow], value_cols: list[int]) -> list[FootingCheck]:
    """Total assets against total equity and liabilities, for each period column."""
    assets = next((r for r in rows if _ASSETS_RE.search(r.label or "")), None)
    other = next((r for r in rows if any(rx.search(r.label or "") for rx in _EQ_LIAB_RES)), None)
    if assets is None or other is None:
        return []
    checks: list[FootingCheck] = []
    for col in value_cols:
        a, b = assets.values.get(col), other.values.get(col)
        if a is None or b is None:
            continue
        checks.append(FootingCheck(
            table_id=table_id, page_no=page_no,
            subtotal_label=f"{assets.label} = {other.label}",
            printed=a, recomputed=b, difference=round(a - b, 4),
            passed=abs(a - b) <= tolerance(a, b),
            component_labels=[assets.label, other.label],
        ))
    return checks


@dataclass
class NoteLink:
    """One statement line that cites a note, and the note's own total."""
    statement_table_id: str
    page_no: int
    statement_label: str
    note_ref: str
    period: str | None
    statement_value: float
    note_table_id: str
    note_total: float


def check_cross_table(links: list[NoteLink]) -> list[FootingCheck]:
    checks = []
    for link in links:
        checks.append(FootingCheck(
            table_id=link.statement_table_id, page_no=link.page_no,
            subtotal_label=f"Note {link.note_ref} vs {link.statement_label}",
            printed=link.statement_value, recomputed=link.note_total,
            difference=round(link.statement_value - link.note_total, 4),
            passed=abs(link.statement_value - link.note_total) <= tolerance(link.statement_value, link.note_total),
            component_labels=[link.note_table_id],
        ))
    return checks
