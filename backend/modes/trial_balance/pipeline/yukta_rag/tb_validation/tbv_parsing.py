"""Shared number-parsing and total-row-filter primitives for the TB
validation module. Pure functions, no I/O, no dependency on any other
package in this repo.
"""

from __future__ import annotations

import re

from yukta_rag.tb_validation.tbv_config import TOTAL_ROW_EXACT, TOTAL_ROW_STARTS_WITH

_NON_DIGIT_DOT_RE = re.compile(r"[^0-9.]")


def parse_amount(raw: object) -> tuple[float, bool]:
    """Parse a raw TB cell value into a signed float, per the exact 6-step
    algorithm (in order):

    1. Trim whitespace.
    2. Parenthesized amount -> negative: "(1,234.56)" -> -1234.56.
    3. Leading or trailing minus -> negative: "-500" and "500-" both -> -500.0.
    4. Strip everything that isn't a digit or decimal point (commas including
       Indian lakh/crore grouping, currency symbols, "INR"/"Rs." prefixes,
       whitespace).
    5. If more than one "." remains, keep only the LAST one as the decimal
       point, remove the rest.
    6. Empty/None/unparseable -> 0.0, flagged unparseable.

    Returns (value, unparseable). ``unparseable=True`` means the value could
    not be confidently parsed and was treated as 0.0 in sums — the CALLER
    (TB-004) is responsible for recording this as a failure for that row;
    this function never silently drops anything, it only reports.
    """
    if raw is None:
        return 0.0, True
    s = str(raw).strip()
    if not s:
        return 0.0, True

    negative = False
    if s.startswith("(") and s.endswith(")") and len(s) >= 2:
        negative = True
        s = s[1:-1].strip()
    if s.startswith("-"):
        negative = True
        s = s[1:].strip()
    elif s.endswith("-"):
        negative = True
        s = s[:-1].strip()

    s = _NON_DIGIT_DOT_RE.sub("", s)
    if not s:
        return 0.0, True

    if s.count(".") > 1:
        parts = s.split(".")
        s = "".join(parts[:-1]) + "." + parts[-1]
    if s == "." or s == "":
        return 0.0, True

    try:
        value = float(s)
    except ValueError:
        return 0.0, True

    return (-value if negative else value), False


def is_total_row(ledger_code: object, ledger_name: object) -> bool:
    """Case-insensitive, trimmed match on EITHER cell against the total-row
    vocabulary (exact match or starts-with 'grand total'/'total '). Run
    independently per table, before every other check; excluded rows are
    informational only (never halts) and must never feed any aggregate
    check downstream."""
    for cell in (ledger_code, ledger_name):
        if cell is None:
            continue
        low = str(cell).strip().lower()
        if not low:
            continue
        if low in TOTAL_ROW_EXACT:
            return True
        if any(low.startswith(p) for p in TOTAL_ROW_STARTS_WITH):
            return True
    return False
