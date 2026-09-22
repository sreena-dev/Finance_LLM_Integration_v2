"""A wrapped column header split onto its own row must fold back into the
header, not be treated as a data row.

Real shape: a cash-flow-statement header prints "For the Year Ended" and
"31st March 2023" on two physical lines. When TableFormer's row-boundary
detection doesn't recognise the second line as still part of the header, it
becomes its own "data row" -- e.g. label "A: CASH FLOW FROM OPERATING
ACTIVITIES", value cell "For the Year Ended 31st" -- which then gets flagged
`unreadable_text` downstream since it isn't a number, when it was never a
value cell at all.

Run with::

    cd ingestion
    venv/Scripts/python -m pytest tests/test_header_continuation.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.tables import parse_markdown_tables  # noqa: E402


def test_a_wrapped_year_ended_header_row_is_folded_into_the_header():
    md = (
        "| Particulars | Note | For the Year Ended |\n"
        "| --- | --- | --- |\n"
        "| A: CASH FLOW FROM OPERATING ACTIVITIES | | 31st March 2023 |\n"
        "| Net Profit before tax | | 43.62 |\n"
    )
    tables = parse_markdown_tables(md, 3)
    table = tables[0]

    # The continuation row is gone; only the real data row remains.
    assert len(table.rows) == 1
    assert table.label(0) == "Net Profit before tax"

    # Its text is folded into the header, not lost.
    assert "31st March 2023" in table.header[2]


def test_the_amount_in_thousands_caption_row_is_folded_too():
    """The pre-existing header/unit patterns (amount in, as at, particulars)
    still work through the new, broadened regex."""
    md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| (Amount In Rs.00000) | |\n"
        "| Interest Income | 43.62 |\n"
    )
    table = parse_markdown_tables(md, 3)[0]
    assert len(table.rows) == 1
    assert table.label(0) == "Interest Income"


def test_a_genuine_data_row_mentioning_a_month_and_year_is_not_touched():
    """A row that HAS a numeric value must never be folded into the header,
    even if its text also happens to mention a month/year -- e.g. a
    dividend-declared-on disclosure. Having a number is what makes it real
    data, not header text."""
    md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Dividend declared on 15 March 2023 | 5,00,000 |\n"
    )
    table = parse_markdown_tables(md, 3)[0]
    assert len(table.rows) == 1
    assert table.label(0) == "Dividend declared on 15 March 2023"
    assert table.cell(0, 1).value == 500000.0


def test_scoping_stops_at_the_first_numeric_row():
    """Once a real, numeric data row has appeared, nothing AFTER it gets
    folded into the header even if it matches the same pattern -- e.g. a
    caption reappearing mid-table for a sub-section must stay a row, not
    vanish into the header silently."""
    md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Net Profit before tax | 43.62 |\n"
        "| For the Year Ended 31st March 2022 | |\n"
        "| Finance Costs | 6,276.63 |\n"
    )
    table = parse_markdown_tables(md, 3)[0]
    # All three rows survive -- the mid-table caption-like row is left alone
    # because a numeric row already appeared before it.
    assert len(table.rows) == 3
    assert table.label(1) == "For the Year Ended 31st March 2022"


def test_a_table_with_no_header_continuation_row_is_unaffected():
    md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Share capital | 15,00,000.00 |\n"
        "| Reserves | 4,00,000.00 |\n"
    )
    table = parse_markdown_tables(md, 3)[0]
    assert len(table.rows) == 2
