"""Two comparative-year figures merged into ONE cell, with the sibling
column left blank, must split apart -- deterministically, never guessing.

Real shape: a share-capital-reconciliation row prints "15,00,000" (as at 31
March 2023) and "1,50,000" (as at 31 March 2022) as two adjacent columns,
but TableFormer merges them into one cell ("15,00,000 1,50,000") and leaves
the neighbouring column blank. A different defect from a merged ROW (see
test_merged_rows.py) -- here the row is correct, one line item, but a
COLUMN boundary was lost.

Run with::

    cd ingestion
    venv/Scripts/python -m pytest tests/test_column_redistribution.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.tables import parse_markdown_tables  # noqa: E402


def test_two_merged_values_split_across_the_blank_sibling_column():
    # A second, ordinarily-populated row establishes column 2 as a genuine
    # value column (`_resolve_roles`'s ratio is computed across the WHOLE
    # table) -- exactly the realistic shape: most rows of a share-capital
    # reconciliation are fine, one row hit the merge defect.
    md = (
        "| Particulars | As at 31 March 2023 | As at 31 March 2022 |\n"
        "| --- | --- | --- |\n"
        "| Equity Shares Held by Governor of Odisha | 15,00,000 1,50,000 | |\n"
        "| Other Equity Shares | 3,00,000 | 3,00,000 |\n"
    )
    table = parse_markdown_tables(md, 4)[0]
    assert table.cell(0, 1).value == 1500000.0
    assert table.cell(0, 2).value == 150000.0


def test_the_blank_column_may_sit_to_the_left_of_the_merged_cell():
    """Tokens fill columns by INDEX order, not by which physical cell held
    the merged text -- so a blank slot to the LEFT of the merged cell must
    still get the first token."""
    md = (
        "| Particulars | As at 31 March 2023 | As at 31 March 2022 |\n"
        "| --- | --- | --- |\n"
        "| Equity Shares Held by Governor of Odisha | | 15,00,000 1,50,000 |\n"
        "| Other Equity Shares | 3,00,000 | 3,00,000 |\n"
    )
    table = parse_markdown_tables(md, 4)[0]
    assert table.cell(0, 1).value == 1500000.0
    assert table.cell(0, 2).value == 150000.0


def test_a_token_count_and_blank_count_mismatch_is_refused():
    """Two tokens but only ONE other value column at all (so zero possible
    blanks to redistribute into beyond the merged cell itself) -- ambiguous,
    must stay untouched rather than guessed at."""
    md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Equity Shares Held by Governor of Odisha | 15,00,000 1,50,000 |\n"
    )
    table = parse_markdown_tables(md, 4)[0]
    assert table.rows[0][1] == "15,00,000 1,50,000"


def test_three_tokens_but_only_one_blank_is_refused():
    md = (
        "| Particulars | A | B | C |\n"
        "| --- | --- | --- | --- |\n"
        "| Item | 1,00,000 2,00,000 3,00,000 | | 9,00,000 |\n"
    )
    table = parse_markdown_tables(md, 4)[0]
    # 3 tokens need 2 blanks; only 1 is available (column C is populated) --
    # refuse the whole redistribution, leave the merged cell exactly as it was.
    assert table.rows[0][1] == "1,00,000 2,00,000 3,00,000"
    assert table.cell(0, 3).value == 900000.0


def test_a_token_that_does_not_parse_cleanly_refuses_the_whole_cell():
    md = (
        "| Particulars | As at 31 March 2023 | As at 31 March 2022 |\n"
        "| --- | --- | --- |\n"
        "| Item | 15,00,000 8Z5'E | |\n"
    )
    table = parse_markdown_tables(md, 4)[0]
    assert table.rows[0][1] == "15,00,000 8Z5'E"


def test_an_already_populated_sibling_column_is_never_overwritten():
    """If the "blank" column is not actually blank, the merged cell must be
    left alone -- there is no safe slot to redistribute into."""
    md = (
        "| Particulars | A | B |\n"
        "| --- | --- | --- |\n"
        "| Item | 1,00,000 2,00,000 | 9,00,000 |\n"
    )
    table = parse_markdown_tables(md, 4)[0]
    assert table.rows[0][1] == "1,00,000 2,00,000"
    assert table.cell(0, 2).value == 900000.0


def test_an_ordinary_single_token_value_cell_is_unaffected():
    md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Share capital | 15,00,000.00 |\n"
    )
    table = parse_markdown_tables(md, 4)[0]
    assert table.cell(0, 1).value == 1500000.0
