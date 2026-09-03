"""Golden tests over figures hand-read from the sample scans in ``data/``.

Every table in here was transcribed by eye from a rendered page, and every
expected total was checked against the printed statement. That is what makes
them golden: if the extraction changes and a total stops footing, the failure is
against the actual filing rather than against a previous run of this code.

Run with::

    ingestion/venv/Scripts/python -m pytest ingestion/tests -q

The four properties under test are the ones the whole pipeline exists to
guarantee:

1. Indian digit grouping and the OCR defects seen in this corpus parse correctly.
2. Subtotals are discovered even when the statement prints them unlabelled.
3. A clipped negative whose sign the arithmetic can confirm is recovered.
4. A clipped negative whose sign the arithmetic CANNOT confirm is withheld --
   never emitted as a positive. This is the safety property; if only one test in
   this file is kept, keep this one.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.numbers import parse_cell                      # noqa: E402
from app.tables import parse_markdown_tables            # noqa: E402
from app.verify import verify_table, redact             # noqa: E402


# --------------------------------------------------------------------------
# 1. Cell parsing
# --------------------------------------------------------------------------

def test_indian_grouping():
    assert parse_cell("20,17,448").value == 2017448.0
    assert parse_cell("1,11,647.03").value == 111647.03
    assert parse_cell("48,36,082").value == 4836082.0
    # Western grouping still works -- the corpus mixes both.
    assert parse_cell("4,516,527.58").value == 4516527.58


def test_nil_is_not_zero():
    """A dash means the line does not apply. Reading it as 0.0 would make an
    absent row foot correctly and hide that it was never there."""
    for raw in ("-", "", "  ", "nil", "N/A"):
        cell = parse_cell(raw)
        assert cell.value is None
        assert cell.is_nil


def test_clipped_paren_reads_negative_and_flags_itself():
    """`MH 2022-23 SFS` p.5 prints `(1,757` -- the closing paren is cropped by
    the table border."""
    cell = parse_cell("(1,757")
    assert cell.value == -1757.0
    assert cell.sign_uncertain
    assert "sign_uncertain" in cell.flags()


def test_ocr_separator_confusion():
    """The same page produced `18.00.000` for `18,00,000`."""
    cell = parse_cell("18.00.000")
    assert cell.value == 1800000.0
    assert cell.separators_repaired


def test_cpsu_sign_marker_beats_parens():
    """A filing that prints `(-) (119,947.74)` has stated one sign twice."""
    assert parse_cell("(-) 119,947.74").value == -119947.74
    assert parse_cell("(-) Rs. 96.73").value == -96.73


def test_percentages_are_not_money():
    assert parse_cell("54.70%").value is None


# --------------------------------------------------------------------------
# 2. Footing discovery
# --------------------------------------------------------------------------

# OD 2021-22 SFS page 10, Schedule III balance sheet. The two subtotals
# (14,74,540 and 25,000) are printed with NO label at all.
OD_BALANCE_SHEET = """Balance Sheet as at 31st March, 2022
| Partculars | Notes | As at 31st March, 2022 |
| --- | --- | --- |
| (a) Share Capital | 3 | 15,00,000 |
| (b) Surplus | 4 | (25,460) |
|  |  | 14,74,540 |
| (c) Other current liabilities | 5 | 25,000 |
|  |  | 25,000 |
| TOTAL |  | 14,99,540 |
"""


def test_column_roles_resolved_by_content():
    table = parse_markdown_tables(OD_BALANCE_SHEET, 10)[0]
    assert table.label_col == 0
    # The Notes column is numeric but is NOT money. Left in the value columns a
    # note reference of 5 becomes a figure of five and every footing fails.
    assert table.note_col == 1
    assert table.value_cols == [2]
    assert table.title == "Balance Sheet as at 31st March, 2022"


def test_unlabelled_subtotals_are_discovered():
    table = parse_markdown_tables(OD_BALANCE_SHEET, 10)[0]
    checks, _ = verify_table(table, 10)
    passed = {c.subtotal_label: c for c in checks if c.passed}
    assert any(c.printed == 1474540.0 for c in passed.values())
    grand = [c for c in checks if c.subtotal_label == "TOTAL"]
    assert len(grand) == 1
    assert grand[0].passed, "the balance sheet's own TOTAL must foot"
    assert grand[0].printed == 1499540.0


# MH 2022-23 SFS page 5, Note 1(a) PPE gross carrying amount. Both Disposals
# cells in the Total column are printed clipped: `(1,757` and `(10,378`.
PPE_ROLL_FORWARD = """Note 1 (a) - Property, plant and equipment
| Particulars | Air Conditioners | Computer Hardware | Office Equipments | Furniture & Fixtures | Freehold Land | Building | Motor Car | Total |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Opening balance as at April 1, 2021 | 3,439 | 12,898 | 5,572 | 9,534 | 943 | 2,759 | 1,757 | 36,903 |
| Additions | - | 1,419 | 12 | - | - | - | 1,739 | 3,170 |
| Disposals | - | - | - | - | - | - | (1,757) | (1,757 |
| Balance as at March 31, 2022 | 3,439 | 14,317 | 5,584 | 9,534 | 943 | 2,759 | 1,739 | 38,316 |
| Opening balance as at April 1, 2022 | 3,439 | 14,317 | 5,584 | 9,534 | 943 | 2,759 | 1,739 | 38,316 |
| Additions | 4,527 | 2,312 | 2,405 | 27,597 | - | - | - | 36,840 |
| Disposals | (3,280) | (41) | (1,881) | (5,176) | - | - | - | (10,378 |
| Balance as at March 31, 2023 | 4,686 | 16,588 | 6,108 | 31,955 | 943 | 2,759 | 1,739 | 64,777 |
"""


def test_ppe_roll_forward_foots_and_recovers_clipped_signs():
    table = parse_markdown_tables(PPE_ROLL_FORWARD, 5)[0]
    checks, findings = verify_table(table, 5)

    closing = [c for c in checks if c.subtotal_label == "Balance as at March 31, 2023"]
    assert closing, "the closing balance row must be recognised as a subtotal"
    assert all(c.passed for c in closing)
    # 38,316 + 36,840 - 10,378 = 64,778 against a printed 64,777: the filing is
    # in thousands and rounds. Tolerated, and the difference is reported.
    total_col = max(closing, key=lambda c: c.printed or 0)
    assert total_col.printed == 64777.0
    assert abs(total_col.difference) <= 1.0

    # Both clipped cells had their sign CONFIRMED by the column arithmetic, so
    # nothing is withheld.
    assert findings == [], [f.marker for f in findings]


def test_unconfirmable_sign_is_withheld_never_positive():
    """The safety property.

    Same clipped cell, but with a closing balance that makes the sign
    unconfirmable. The figure must leave the pipeline as a marker, not as a
    number -- and specifically not as +1,757.
    """
    md = """| Particulars | Motor Car | Total |
| --- | --- | --- |
| Opening balance as at April 1, 2021 | 1,757 | 36,903 |
| Additions | 1,739 | 3,170 |
| Disposals | (1,757) | (1,757 |
| Balance as at March 31, 2022 | 1,739 | 99,999 |
"""
    table = parse_markdown_tables(md, 5)[0]
    checks, findings = verify_table(table, 5)

    assert any(not c.passed for c in checks), "the corrupted total must fail"

    assert len(findings) == 1
    finding = findings[0]
    assert finding.row_label == "Disposals"
    assert finding.column == "Total"
    assert "sign_uncertain" in finding.reasons

    redact(table, findings)
    md_out = table.to_markdown()
    assert finding.marker in md_out
    # The critical assertion: the raw clipped figure is gone from the output
    # entirely, so there is nothing for a model to read as +1,757.
    assert "(1,757 " not in md_out.replace(finding.marker, "")
    total_column_cells = [r[2] for r in table.rows]
    assert "1,757" not in total_column_cells[2]


# --------------------------------------------------------------------------
# 3. The SOCIE column, which is where scan skew does its damage
# --------------------------------------------------------------------------

# MH 2022-23 SFS page 3, B. Other Equity, second block. This is the table whose
# rows were visibly misaligned by 1.7 degrees of scan skew before deskewing.
SOCIE = """B. Other Equity
| Particulars | Retained earnings | General Reserve | FVOCI Equity Investments | Total Other Equity |
| --- | --- | --- | --- | --- |
| Balance as at April 01, 2022 | 20,17,453 | 4,08,711 | 13,49,860 | 37,76,023 |
| Profit/(Loss) for the year | 4,56,004 | - | - | 4,56,004 |
| Other comprehensive income for the year | - | - | 8,60,447 | 8,60,447 |
| Total comprehensive income for the year | 4,56,004 | - | 8,60,447 | 13,16,451 |
| Dividends paid | (2,56,392) | - | - | (2,56,392) |
| Transfer to General Reserve | - | - | - | - |
| Balance as at March 31, 2023 | 22,17,065 | 4,08,711 | 22,10,307 | 48,36,082 |
"""


def test_socie_total_other_equity_foots():
    """48,36,082 is the figure printed on the page and cross-confirmed by the
    Note 11 Other Equity total on page 10 of the same filing."""
    table = parse_markdown_tables(SOCIE, 3)[0]
    checks, findings = verify_table(table, 3)
    assert findings == []
    closing = [
        c for c in checks
        if c.subtotal_label == "Balance as at March 31, 2023" and c.printed == 4836082.0
    ]
    assert closing and closing[0].passed


# --------------------------------------------------------------------------
# 4. Three-signal fusion
# --------------------------------------------------------------------------

TWO_COLUMNS = """| Particulars | Motor Car | Total |
| --- | --- | --- |
| Opening balance as at April 1, 2021 | 1,757 | 36,903 |
| Additions | 1,739 | 3,170 |
| Disposals | (1,757) | (1,757 |
| Balance as at March 31, 2022 | 1,739 | 99,999 |
"""


def test_footing_corroboration_is_scoped_to_its_own_column():
    """Regression.

    The Motor Car column foots; the Total column does not. An earlier version
    matched corroborated cells by ROW LABEL across every column, so the passing
    Motor Car check vouched for the identical 'Disposals' row in the Total
    column and un-withheld the clipped figure. A column may only vouch for
    itself.
    """
    table = parse_markdown_tables(TWO_COLUMNS, 5)[0]
    _, findings = verify_table(table, 5)
    assert [(f.row_label, f.column) for f in findings] == [("Disposals", "Total")]


def test_reader_disagreement_withholds_an_uncorroborated_cell():
    """A cell both readers can parse, but differently, and which no column
    arithmetic can settle, is not safe to quote.

    The table below deliberately has no total row, so nothing corroborates any
    cell and the disagreement is the only evidence available.
    """
    md = """Note 21 - Contingent liabilities
| Particulars | As at 31 March 2023 |
| --- | --- |
| Claims not acknowledged as debts | 12,45,000 |
| Bank guarantees outstanding | 3,10,500 |
"""
    table = parse_markdown_tables(md, 21)[0]
    _, findings = verify_table(table, 21, vlm_disagreements={(0, 1)})
    assert [(f.row_label, f.column) for f in findings] == [
        ("Claims not acknowledged as debts", "As at 31 March 2023")
    ]
    assert "readers_disagree" in findings[0].reasons


def test_arithmetic_outranks_a_reader_disagreement_on_a_footed_cell():
    """Two readers differing on a figure whose column adds up means one of them
    mis-transcribed something the arithmetic can settle independently. The
    column wins; the figure stands."""
    md = """| Particulars | Amount |
| --- | --- |
| (a) Share Capital | 15,00,000 |
| (b) Surplus | (25,460) |
| Total | 14,74,540 |
"""
    table = parse_markdown_tables(md, 1)[0]
    _, findings = verify_table(table, 1, vlm_disagreements={(0, 1)})
    assert findings == [], [f.marker for f in findings]


def test_low_ocr_confidence_alone_does_not_withhold_a_clean_cell():
    """docling grading its own OCR poorly is a reason to look harder, not a
    reason to discard a cell that parsed cleanly and foots. Withholding on the
    page score alone would blank most of a 200 DPI bilevel filing."""
    md = """| Particulars | Amount |
| --- | --- |
| (a) Share Capital | 15,00,000 |
| (b) Surplus | (25,460) |
| Total | 14,74,540 |
"""
    table = parse_markdown_tables(md, 1)[0]
    _, findings = verify_table(table, 1, ocr_score=0.2)
    assert findings == []
