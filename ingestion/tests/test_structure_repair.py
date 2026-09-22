"""Rebuilding a table's row grid from where the ink actually sits.

Every coordinate below is transcribed verbatim from a real docling conversion
of OD-SPSU-SO-032 2023-24 SFS's balance sheet, page 1 -- captured by running
the pipeline against the real PDF and reading the raw geometry before this
module ever touched it, the same discipline `tests/fixtures/README.md` sets
out for every other fixture in this suite.

THE regression this file exists for. RapidOCR read every field in this region
cleanly, each at its own Y-position, 99%+ confidence:

    y=423-442  "(b) Trade payables"            "4"  "18,600.00"       "34,011.00"
    y=443-460  "(c) Other current liabilities" "5"  "28,65,46,137.60" "22,69,36,581.10"
    y=459-478  "(d) Short-term provisions"     "6"  "5,22,78,867.00"  "5,11,11,345.00"

TableFormer's own row grid drew the label column's boundary and the value
columns' boundary in DIFFERENT places for the same vertical region, producing:

    "(b) Trade payables" | 4 | (blank)                     | (blank)
    (blank)              | 5 | "18,600.00 28,65,46,137.60"  | "34,011.00 22,69,36,581.10"
    "(c) Other current liabilities (d) Short-term provisions" | 6 | "5,22,78,867.00" | "5,11,11,345.00"

-- one row's worth of disagreement, in opposite directions, between the label
column and the value columns. The accuracy harness scores this exact document
today: `Trade payables` and `Other current liabilities` MISSING, both years.

Run with::

    cd ingestion
    venv/Scripts/python -m pytest tests/test_structure_repair.py -q
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import structure_repair as sr                  # noqa: E402
from app.convert import ConvertedTable, OcrLine, TableCellGeom  # noqa: E402


def _line(text, x0, y0, x1, y1, confidence=0.99):
    return OcrLine(text=text, confidence=confidence, bbox=(x0, y0, x1, y1))


def _cell(text, row, col, x0, y0, x1, y1):
    return TableCellGeom(text=text, row_start=row, row_end=row + 1,
                         col_start=col, col_end=col + 1, bbox=(x0, y0, x1, y1))


# The real region, real coordinates, four columns: Particulars / Note No. /
# 2024 / 2023.
OD_HEADER = ("| Particulars | Note No. | Figures as at 31st March, 2024 | "
             "Figures as at 31st March, 2023 |\n| --- | --- | --- | --- |\n")

OD_TRADE_PAYABLES_REGION_LINES = [
    _line("(b) Trade payables", 48.7, 423.4, 166.3, 442.4),
    _line("4", 376.2, 425.9, 389.5, 442.4),
    _line("18,600.00", 586.8, 425.9, 652.6, 443.1),
    _line("34,011.00", 787.9, 425.9, 853.7, 443.1),
    _line("(c) Other current liabilities", 50.0, 443.1, 215.0, 460.2),
    _line("5", 376.2, 443.1, 388.9, 460.2),
    _line("28,65,46,137.60", 548.9, 443.7, 651.9, 460.8),
    _line("22,69,36,581.10", 752.5, 443.7, 855.6, 460.8),
    _line("(d) Short-term provisions", 48.7, 458.9, 208.0, 478.0),
    _line("6", 375.6, 461.5, 388.9, 478.0),
    _line("5,22,78,867.00", 555.2, 462.1, 651.3, 479.2),
    _line("5,11,11,345.00", 758.2, 460.8, 854.9, 478.0),
    # The real Total row's own real coordinates, immediately below -- without
    # its own OCR lines, a whole-table rebuild would lose it entirely, which
    # is a real risk of rebuilding from geometry rather than a fixture
    # artefact: the live pipeline captures every row's lines from the table's
    # own bbox, but a test that only supplies SOME of a table's lines would
    # wrongly show data loss that the real path does not have.
    _line("Total", 61.97, 476.06, 103.07, 493.83),
    _line("64,88,82,047.93", 531.80, 478.60, 648.78, 495.74),
    _line("43,34,97,783.82", 737.31, 479.87, 855.55, 496.37),
]

# TableFormer's own grid for the SAME region: label boundary and value
# boundary disagree by one row, in opposite directions -- transcribed from the
# real docling markdown export.
OD_TRADE_PAYABLES_DOCLING_MD = (
    OD_HEADER
    + "| (b) Trade payables | 4 |  |  |\n"
    + "|  | 5 | 18,600.00 28,65,46,137.60 | 34,011.00 22,69,36,581.10 |\n"
    + "| (c) Other current liabilities (d) Short-term provisions | 6 | "
      "5,22,78,867.00 | 5,11,11,345.00 |\n"
)

# The same region with the REST of the real balance sheet's equity+liabilities
# side around it -- Share capital, Reserves and surplus, Refundable Grant, and
# the printed Total -- so the footing gate has real arithmetic to judge the
# repair against, exactly as it will on the live document. Every component
# figure and the Total itself are transcribed verbatim from the real filing;
# 15,00,000 + 1,04,38,195.33 + 29,81,00,248 + 18,600 + 28,65,46,137.60 +
# 5,22,78,867 = 64,88,82,047.93, the printed Total, to the paisa.
OD_FULL_REGION_DOCLING_MD = (
    OD_HEADER
    + "| (a) Share capital | 1 | 15,00,000.00 | 15,00,000.00 |\n"
    + "| (b) Reserves and surplus | 2 | 1,04,38,195.33 | 38,81,031.72 |\n"
    + "| (b) Refundable Grant | 3 | 29,81,00,248.00 | 15,00,34,815.00 |\n"
    + "| (b) Trade payables | 4 |  |  |\n"
    + "|  | 5 | 18,600.00 28,65,46,137.60 | 34,011.00 22,69,36,581.10 |\n"
    + "| (c) Other current liabilities (d) Short-term provisions | 6 | "
      "5,22,78,867.00 | 5,11,11,345.00 |\n"
    + "| Total |  | 64,88,82,047.93 | 43,34,97,783.82 |\n"
)

# Column X-ranges as TableFormer itself reported them (from the surrounding,
# CORRECTLY-gridded rows of the same table -- Share capital, Refundable
# Grant, etc.) -- this is what makes column assignment reliable even though
# the ROW grid is not.
OD_COLUMN_CELLS = [
    _cell("(a) Share capital", 2, 0, 49.9, 260.0, 187.0, 277.0),
    _cell("1", 2, 1, 376.2, 262.0, 389.5, 278.0),
    _cell("15,00,000.00", 2, 2, 546.9, 262.0, 650.0, 278.0),
    _cell("15,00,000.00", 2, 3, 753.1, 262.0, 856.2, 278.0),
    _cell("(b) Refundable Grant", 8, 0, 49.9, 352.3, 183.4, 369.4),
    _cell("3", 8, 1, 376.2, 352.9, 389.5, 370.7),
    _cell("29,81,00,248.00", 8, 2, 546.9, 353.6, 650.0, 370.7),
    _cell("15,00,34,815.00", 8, 3, 753.1, 353.6, 856.2, 370.7),
]


# The three extra context rows' own OCR lines and cells -- consistent,
# already-correctly-gridded content docling read cleanly, standing in for the
# rest of the real page (whose exact coordinates weren't captured for rows
# outside the disputed region, only the values, which are real).
_CONTEXT_ROWS = [
    ("(a) Share capital", "1", "15,00,000.00", "15,00,000.00", 260.0, 277.0),
    ("(b) Reserves and surplus", "2", "1,04,38,195.33", "38,81,031.72", 280.0, 297.0),
    ("(b) Refundable Grant", "3", "29,81,00,248.00", "15,00,34,815.00", 352.3, 369.4),
]


def _context_lines():
    out = []
    for label, note, v2024, v2023, y0, y1 in _CONTEXT_ROWS:
        out.append(_line(label, 49.9, y0, 187.0, y1))
        out.append(_line(note, 376.2, y0 + 2, 389.5, y1))
        out.append(_line(v2024, 546.9, y0 + 2, 650.0, y1))
        out.append(_line(v2023, 753.1, y0 + 2, 856.2, y1))
    return out


def _context_cells():
    # row_start offset well clear of 0: in real docling output row_start==0
    # is the table's OWN HEADER (see MH_2324_HEADER_CELLS below), and these
    # are ordinary body rows -- starting them at 0 would make
    # `_header_bottom` mistake a real line item for the header, exactly the
    # class of bug this fixture exists to avoid reintroducing.
    out = []
    for row_index, (label, note, v2024, v2023, y0, y1) in enumerate(_CONTEXT_ROWS, start=20):
        out.append(_cell(label, row_index, 0, 49.9, y0, 187.0, y1))
        out.append(_cell(note, row_index, 1, 376.2, y0, 389.5, y1))
        out.append(_cell(v2024, row_index, 2, 546.9, y0, 650.0, y1))
        out.append(_cell(v2023, row_index, 3, 753.1, y0, 856.2, y1))
    return out


def _od_table(with_cells=True, with_lines=True, full_region=False):
    return ConvertedTable(
        page_no=1,
        markdown=OD_FULL_REGION_DOCLING_MD if full_region else OD_TRADE_PAYABLES_DOCLING_MD,
        cells=(list(OD_COLUMN_CELLS) + (_context_cells() if full_region else [])) if with_cells else [],
        ocr_lines=(list(OD_TRADE_PAYABLES_REGION_LINES) + (_context_lines() if full_region else []))
                  if with_lines else [],
        page_height_pt=842.0,
    )


# --------------------------------------------------------------------------
# The real regression, end to end
# --------------------------------------------------------------------------

def test_the_real_od_region_is_rebuilt_correctly():
    """The end-to-end regression: with the real surrounding context present
    (Share capital, Reserves and surplus, Refundable Grant, Total), the
    repaired structure foots -- 15,00,000 + 1,04,38,195.33 + 29,81,00,248 +
    18,600 + 28,65,46,137.60 + 5,22,78,867 = 64,88,82,047.93 exactly -- and
    the original, with Trade payables and Other current liabilities blank or
    crushed, cannot. That is what makes the acceptance gate choose it."""
    repaired = sr.repair_from_geometry(_od_table(full_region=True))

    assert repaired.markdown != OD_FULL_REGION_DOCLING_MD, "nothing was repaired"

    from app.tables import parse_markdown_tables
    table = parse_markdown_tables(repaired.markdown, 1)[0]
    by_label = {table.label(r): r for r in range(len(table.rows))}

    tp = next(r for lbl, r in by_label.items() if "Trade payables" in lbl)
    assert table.cell(tp, table.value_cols[0]).value == 18600.0
    assert table.cell(tp, table.value_cols[1]).value == 34011.0

    ocl = next(r for lbl, r in by_label.items() if "Other current liabilities" in lbl
               and "Short-term" not in lbl)
    assert table.cell(ocl, table.value_cols[0]).value == 286546137.60
    assert table.cell(ocl, table.value_cols[1]).value == 226936581.10

    stp = next(r for lbl, r in by_label.items() if "Short-term provisions" in lbl)
    assert table.cell(stp, table.value_cols[0]).value == 52278867.0
    assert table.cell(stp, table.value_cols[1]).value == 51111345.0

    # The Total, and every other real row, survived the whole-table rebuild.
    total = next(r for lbl, r in by_label.items() if lbl == "Total")
    assert table.cell(total, table.value_cols[0]).value == 648882047.93
    assert "Share capital" in " ".join(by_label)
    assert "Reserves and surplus" in " ".join(by_label)
    assert "Refundable Grant" in " ".join(by_label)


def test_the_repair_is_visible_it_does_not_silently_replace_the_row_count():
    """Three real printed lines must become three rows, not collapse back to
    the two-and-a-fragment shape TableFormer produced."""
    repaired = sr.repair_from_geometry(_od_table(full_region=True))
    from app.tables import parse_markdown_tables
    table = parse_markdown_tables(repaired.markdown, 1)[0]
    labels = " | ".join(table.label(r) for r in range(len(table.rows)))
    assert "Trade payables" in labels
    assert "Other current liabilities" in labels
    assert "Short-term provisions" in labels


# --------------------------------------------------------------------------
# Refusal -- the acceptance gate, and missing geometry
# --------------------------------------------------------------------------

def test_no_geometry_is_a_pure_noop():
    original = _od_table(with_cells=False, with_lines=False)
    assert sr.repair_from_geometry(original) is original


def test_cells_without_lines_is_a_noop():
    original = _od_table(with_lines=False)
    assert sr.repair_from_geometry(original) is original


def test_lines_without_cells_is_a_noop():
    original = _od_table(with_cells=False)
    assert sr.repair_from_geometry(original) is original


def test_a_table_that_already_foots_is_left_alone():
    """A clean, correctly-gridded table must never be rewritten -- rebuilding
    it from geometry could only be a lateral move at best, and the arithmetic
    gate must refuse a candidate that does not IMPROVE on the original."""
    md = (
        "| Particulars | Amount |\n| --- | --- |\n"
        "| (a) Share Capital | 15,00,000 |\n"
        "| (b) Surplus | 25,460 |\n"
        "| Total | 15,25,460 |\n"
    )
    table = ConvertedTable(
        page_no=1, markdown=md,
        cells=[
            _cell("(a) Share Capital", 0, 0, 40.0, 100.0, 150.0, 116.0),
            _cell("15,00,000", 0, 1, 300.0, 100.0, 380.0, 116.0),
            _cell("(b) Surplus", 1, 0, 40.0, 118.0, 150.0, 134.0),
            _cell("25,460", 1, 1, 300.0, 118.0, 380.0, 134.0),
            _cell("Total", 2, 0, 40.0, 136.0, 150.0, 152.0),
            _cell("15,25,460", 2, 1, 300.0, 136.0, 380.0, 152.0),
        ],
        ocr_lines=[
            _line("(a) Share Capital", 40.0, 100.0, 150.0, 116.0),
            _line("15,00,000", 300.0, 100.0, 380.0, 116.0),
            _line("(b) Surplus", 40.0, 118.0, 150.0, 134.0),
            _line("25,460", 300.0, 118.0, 380.0, 134.0),
            _line("Total", 40.0, 136.0, 150.0, 152.0),
            _line("15,25,460", 300.0, 136.0, 380.0, 152.0),
        ],
        page_height_pt=842.0,
    )

    repaired = sr.repair_from_geometry(table)
    assert repaired is table


def test_too_few_lines_to_cluster_is_a_noop():
    table = ConvertedTable(
        page_no=1, markdown="| A | B |\n| --- | --- |\n| x | y |\n",
        cells=[_cell("x", 0, 0, 40.0, 100.0, 60.0, 116.0)],
        ocr_lines=[_line("x", 40.0, 100.0, 60.0, 116.0),
                   _line("y", 200.0, 100.0, 220.0, 116.0)],
    )
    assert sr.repair_from_geometry(table) is table


def test_a_single_column_of_geometry_is_a_noop():
    """Column assignment needs at least two distinct columns to mean
    anything; one column is not enough evidence to rebuild rows from."""
    table = ConvertedTable(
        page_no=1, markdown=OD_TRADE_PAYABLES_DOCLING_MD,
        cells=[_cell("(a) Share capital", 2, 0, 49.9, 260.0, 187.0, 277.0)],
        ocr_lines=list(OD_TRADE_PAYABLES_REGION_LINES),
    )
    assert sr.repair_from_geometry(table) is table


def test_repair_never_mutates_the_input():
    original = _od_table()
    original_md = original.markdown
    sr.repair_from_geometry(original)
    assert original.markdown == original_md


# --------------------------------------------------------------------------
# Column assignment and row clustering, unit-level
# --------------------------------------------------------------------------

def test_column_ranges_ignore_spanning_cells():
    """A caption cell spanning every column must not smear two real columns
    into one wide band."""
    cells = [
        _cell("Balance Sheet as at 31 March", 0, 0, 40.0, 50.0, 800.0, 66.0),
    ]
    cells[0].col_end = 4  # spans columns 0-3
    ranges = sr._column_ranges(cells, header_count=4)
    assert ranges == {}


def test_nearest_column_picks_the_containing_range():
    ranges = {0: (40.0, 200.0), 1: (370.0, 390.0), 2: (540.0, 660.0), 3: (750.0, 860.0)}
    assert sr._nearest_column(100.0, ranges) == 0
    assert sr._nearest_column(380.0, ranges) == 1
    assert sr._nearest_column(600.0, ranges) == 2
    assert sr._nearest_column(800.0, ranges) == 3


def test_nearest_column_falls_back_to_the_closest_edge_outside_every_range():
    ranges = {0: (40.0, 200.0), 2: (540.0, 660.0)}
    assert sr._nearest_column(210.0, ranges) == 0
    assert sr._nearest_column(530.0, ranges) == 2


def test_row_clustering_separates_three_real_lines_correctly():
    ranges = sr._column_ranges(OD_COLUMN_CELLS, header_count=4)
    rows = sr._cluster_rows(OD_TRADE_PAYABLES_REGION_LINES, ranges, header_count=4)

    assert rows is not None
    assert len(rows) == 4, "3 real line items plus the Total row beneath them"
    assert rows[0][0] == "(b) Trade payables"
    assert rows[0][2] == "18,600.00" and rows[0][3] == "34,011.00"
    assert rows[1][0] == "(c) Other current liabilities"
    assert rows[1][2] == "28,65,46,137.60" and rows[1][3] == "22,69,36,581.10"
    assert rows[2][0] == "(d) Short-term provisions"
    assert rows[2][2] == "5,22,78,867.00" and rows[2][3] == "5,11,11,345.00"
    assert rows[3][0] == "Total"
    assert rows[3][2] == "64,88,82,047.93" and rows[3][3] == "43,34,97,783.82"


def test_the_tables_own_header_read_again_is_not_duplicated_as_a_body_row():
    """THE regression, real docling output on MH-CPSU-ITSL-048 2024-25: a
    table's header sits inside the same bbox its OCR lines were queried
    against, so a rebuild with no row concept at all reproduced it VERBATIM
    as a spurious first row -- 'Particulars | Note No | 31st March 2025 |
    31st March 2024' appearing twice, once as the real header and once as
    fabricated body data."""
    header = ["Particulars", "Note No", "31st March 2025", "31st March 2024"]
    ranges = {0: (48.0, 400.0), 1: (400.0, 480.0), 2: (600.0, 660.0), 3: (730.0, 780.0)}
    lines = [
        # The header, read again by OCR -- same text, a plausible Y-band.
        _line("Particulars", 50.0, 100.0, 150.0, 116.0),
        _line("Note No", 420.0, 100.0, 470.0, 116.0),
        _line("31st March 2025", 610.0, 100.0, 655.0, 116.0),
        _line("31st March 2024", 735.0, 100.0, 775.0, 116.0),
        # Three genuine body rows beneath it.
        _line("Revenue from Operations", 50.0, 130.0, 220.0, 146.0),
        _line("7,69,561", 610.0, 130.0, 650.0, 146.0),
        _line("7,40,888", 735.0, 130.0, 775.0, 146.0),
        _line("Employee Benefit Expense", 50.0, 150.0, 220.0, 166.0),
        _line("1,49,321", 610.0, 150.0, 650.0, 166.0),
        _line("1,27,330", 735.0, 150.0, 775.0, 166.0),
        _line("Total Expenses", 50.0, 170.0, 220.0, 186.0),
        _line("3,49,065", 610.0, 170.0, 650.0, 186.0),
        _line("3,13,350", 735.0, 170.0, 775.0, 186.0),
    ]

    rows = sr._cluster_rows(lines, ranges, header_count=4, header=header)

    assert rows is not None
    labels = [r[0] for r in rows]
    assert labels.count("Particulars") == 0, f"header duplicated as a body row: {rows}"
    assert labels == ["Revenue from Operations", "Employee Benefit Expense", "Total Expenses"]


def test_a_body_row_that_only_partly_resembles_the_header_is_kept():
    """The dedup must match the header EXACTLY, not merely overlap with it --
    a real row is not discarded just because one of its cells happens to say
    'Particulars' or repeat a column title."""
    header = ["Particulars", "Note No", "2025", "2024"]
    ranges = {0: (48.0, 400.0), 1: (400.0, 480.0), 2: (600.0, 660.0), 3: (730.0, 780.0)}

    def _row_at(y0):
        return [
            _line("Particulars regarding Note No 2025 policy", 50.0, y0, 350.0, y0 + 16),
            _line("1", 420.0, y0, 470.0, y0 + 16),
            _line("500", 610.0, y0, 650.0, y0 + 16),
            _line("400", 735.0, y0, 775.0, y0 + 16),
        ]

    lines = _row_at(100.0) + _row_at(120.0) + _row_at(140.0)
    rows = sr._cluster_rows(lines, ranges, header_count=4, header=header)

    assert rows is not None
    assert len(rows) == 3
    assert all("Particulars regarding" in r[0] for r in rows)


# --------------------------------------------------------------------------
# A wrapped, two-line header AND a caption bleeding into the table's region
#
# Real coordinates, MH-CPSU-ITSL-048 2023-24 SFS, balance sheet page 1 --
# reported directly by a user: after the exact-text header dedup above, TWO
# spurious rows still survived, both with blank labels and digit-bearing text
# in a value column -- "31st March 2024" / "31st March.2023" (fragments of a
# column header that wraps across two PHYSICAL printed lines, which docling's
# own header text already joins into one string but the OCR-line
# reconstruction sees as two separate Y-bands) and "(Amount in '000)" (a
# document caption sitting just above the header, inside the same region).
# Neither band's text equals the header string, so exact matching missed
# both; this is fixed geometrically instead, by excluding anything above
# where the header's OWN cells (row_start == 0) end on the page.
# --------------------------------------------------------------------------

MH_2324_HEADER = ["S.N. Particulars", "Note No.",
                  "For the year ended 31st March 2024",
                  "For the year ended 31st March.2023"]

MH_2324_HEADER_CELLS = [
    _cell("S.N. Particulars", 0, 0, 200.7, 124.1, 351.3, 138.7),
    _cell("Note No.", 0, 1, 431.3, 126.0, 471.3, 138.7),
    _cell("For the year ended 31st March 2024", 0, 2, 489.1, 118.4, 571.0, 144.5),
    _cell("For the year ended 31st March.2023", 0, 3, 601.5, 119.6, 679.6, 145.1),
]

MH_2324_CAPTION_AND_HEADER_LINES = [
    # The caption, above the header entirely.
    _line("Balance Sheet as at 31st March.2024", 372.9, 99.9, 523.4, 110.7),
    # The header's own text, wrapped across two physical lines.
    _line("For the year ended", 489.1, 118.4, 571.0, 133.0),
    _line("For the year ended", 601.5, 119.6, 679.6, 132.4),
    _line("Particulars", 303.0, 124.1, 351.3, 138.7),
    _line("S.N.", 200.7, 124.7, 222.9, 138.7),
    _line("Note No.", 431.3, 126.0, 471.3, 138.7),
    _line("31st March 2024", 494.2, 131.7, 565.3, 144.5),
    _line("31st March.2023", 604.1, 132.4, 674.6, 145.1),
]


def test_a_wrapped_header_line_is_excluded_not_reconstructed_as_a_row():
    header_bottom = sr._header_bottom(MH_2324_HEADER_CELLS)
    assert header_bottom is not None

    ranges = {0: (200.0, 355.0), 1: (430.0, 472.0), 2: (489.0, 572.0), 3: (601.0, 680.0)}
    body = [
        _line("Property, Plant & Equipment", 223.0, 212.6, 340.5, 225.3),
        _line("35,391", 551.3, 211.3, 582.5, 225.3),
        _line("39,639", 660.0, 211.9, 691.1, 225.3),
        _line("Investment Properties", 224.2, 225.9, 314.4, 236.8),
        _line("3,701", 555.1, 224.0, 584.4, 238.0),
        _line("3,831", 663.8, 223.4, 691.7, 239.9),
        _line("Other Intangible Assets", 224.2, 238.0, 314.4, 249.0),
        _line("1,774", 555.1, 238.0, 584.4, 250.0),
        _line("1,984", 663.8, 238.0, 691.7, 250.0),
    ]
    lines = MH_2324_CAPTION_AND_HEADER_LINES + body

    rows = sr._cluster_rows(lines, ranges, header_count=4,
                            header=MH_2324_HEADER, header_bottom=header_bottom)

    assert rows is not None
    labels = [r[0] for r in rows]
    assert labels == ["Property, Plant & Equipment", "Investment Properties",
                       "Other Intangible Assets"], (
        f"caption and wrapped-header fragments survived as rows: {rows}"
    )


def test_without_header_geometry_the_exact_match_dedup_still_catches_a_plain_duplicate():
    """Defensive fallback: a docling build that does not expose row_start==0
    header cells must still catch the simple case this already handled."""
    header = ["Particulars", "Note No", "31st March 2025", "31st March 2024"]
    ranges = {0: (48.0, 400.0), 1: (400.0, 480.0), 2: (600.0, 660.0), 3: (730.0, 780.0)}
    lines = [
        _line("Particulars", 50.0, 100.0, 150.0, 116.0),
        _line("Note No", 420.0, 100.0, 470.0, 116.0),
        _line("31st March 2025", 610.0, 100.0, 655.0, 116.0),
        _line("31st March 2024", 735.0, 100.0, 775.0, 116.0),
        _line("Revenue from Operations", 50.0, 130.0, 220.0, 146.0),
        _line("7,69,561", 610.0, 130.0, 650.0, 146.0),
        _line("7,40,888", 735.0, 130.0, 775.0, 146.0),
        _line("Employee Benefit Expense", 50.0, 150.0, 220.0, 166.0),
        _line("1,49,321", 610.0, 150.0, 650.0, 166.0),
        _line("1,27,330", 735.0, 150.0, 775.0, 166.0),
        _line("Total Expenses", 50.0, 170.0, 220.0, 186.0),
        _line("3,49,065", 610.0, 170.0, 650.0, 186.0),
        _line("3,13,350", 735.0, 170.0, 775.0, 186.0),
    ]
    rows = sr._cluster_rows(lines, ranges, header_count=4, header=header, header_bottom=None)
    assert rows is not None
    assert [r[0] for r in rows].count("Particulars") == 0


# ---------------------------------------------------------------------------
# Two-line anchors: a wrapped caption must anchor to BOTH printed lines
# ---------------------------------------------------------------------------

def _wrapped_caption_table():
    from app.tables import parse_markdown_tables
    md = ("| Particulars | Note | 2024 | 2023 |\n| --- | --- | --- | --- |\n"
          "| (a) Property, Plant and Equipment [and Intangible assets] | 3 | 68,67,416.61 | 3,10,253.00 |\n"
          "| (b) Capital work in progress | 4 | 1,000.00 | 2,000.00 |\n")
    return parse_markdown_tables(md, 1)[0]


def _wrapped_caption_lines():
    return [
        _line("(a) Property, Plant and Equipment", 48.0, 100.0, 230.0, 112.0),
        _line("[and Intangible assets]", 60.0, 116.0, 190.0, 128.0),
        _line("(b) Capital work in progress", 48.0, 140.0, 215.0, 152.0),
    ]


def test_a_wrapped_caption_anchors_to_both_of_its_printed_lines():
    """Neither fragment alone reaches the anchor threshold, so without a
    two-line anchor the row a wrapped-label join produces would be
    un-anchorable -- its figures unbound and withheld."""
    lines = _wrapped_caption_lines()
    anchors = sr._anchor_lines(_wrapped_caption_table(), lines)
    assert anchors[0] == (0, 1)
    assert anchors[1] == (2, 2)


def test_a_two_line_anchor_widens_the_band_over_both_lines():
    bands = sr.row_bands_from_labels(_wrapped_caption_table(), _wrapped_caption_lines())
    top, bottom = bands[0]
    assert top <= 100.0 and bottom >= 128.0
    # ... but midpoint clipping still keeps it off the next row's band.
    assert bottom <= bands[1][0] + 1e-6


def test_a_row_that_matches_one_line_cleanly_is_never_widened_onto_its_neighbour():
    """The swallow guard: a clean single-line match (>= 0.95) must stay a
    single-line anchor even when the next printed line is also close by."""
    from app.tables import parse_markdown_tables
    table = parse_markdown_tables(
        "| Particulars | Amount |\n| --- | --- |\n"
        "| Trade payables | 100.00 |\n| Other current liabilities | 200.00 |\n", 1)[0]
    lines = [
        _line("Trade payables", 48.0, 100.0, 160.0, 112.0),
        _line("Other current liabilities", 48.0, 118.0, 200.0, 130.0),
    ]
    assert sr._anchor_lines(table, lines) == {0: (0, 0), 1: (1, 1)}


# ---------------------------------------------------------------------------
# is_continuation_fragment: the sibling guard shared by every wrapped-label join
# ---------------------------------------------------------------------------

def test_a_bracketed_lowercase_tail_printed_at_the_same_indent_is_a_continuation():
    assert sr.is_continuation_fragment(
        "[and Intangible assets]", "(a) Property, Plant and Equipment", 60.0, 48.0) is True


def test_a_tail_printed_slightly_deeper_is_still_a_continuation():
    assert sr.is_continuation_fragment(
        "and intangible assets", "(a) Property, Plant and Equipment", 90.0, 48.0) is True


def test_a_sibling_line_item_with_an_enumerator_is_never_a_continuation():
    """The primary guard: a printed enumerator says a NEW item starts here."""
    for lower in ("(b) Trade payables", "(ii) Other current liabilities", "2. Provisions"):
        assert sr.is_continuation_fragment(lower, "(a) Borrowings", 60.0, 48.0) is False


def test_a_capitalised_caption_is_not_a_continuation():
    assert sr.is_continuation_fragment(
        "Other current liabilities", "Trade payables", 60.0, 48.0) is False


def test_a_tail_printed_outbound_of_its_caption_is_not_a_continuation():
    assert sr.is_continuation_fragment(
        "[and Intangible assets]", "(a) Property, Plant and Equipment", 30.0, 48.0) is False


def test_a_total_line_is_never_a_continuation_even_when_lowercase():
    assert sr.is_continuation_fragment("total", "Borrowings", 60.0, 48.0) is False


def test_without_x_positions_the_predicate_refuses_rather_than_trusting_text_alone():
    assert sr.is_continuation_fragment(
        "[and Intangible assets]", "(a) Property, Plant and Equipment") is False
    assert sr.is_continuation_fragment(
        "[and Intangible assets]", "(a) Property, Plant and Equipment", 60.0, None) is False


def test_an_empty_upper_caption_is_never_continued():
    assert sr.is_continuation_fragment("[and Intangible assets]", "", 60.0, 48.0) is False


# ---------------------------------------------------------------------------
# The rebuild gate: OCR coverage, never footing strength
# ---------------------------------------------------------------------------

def _scripted_coverage(monkeypatch, results):
    """Make `_coverage_of` return the given results in call order (original
    first, then candidate) so the gate's decision rule can be tested on its
    own, without depending on how a fixture happens to bind."""
    calls = iter(results)
    monkeypatch.setattr(sr, "_coverage_of", lambda *a, **k: next(calls))


def test_the_gate_accepts_a_rebuild_that_covers_more_and_loses_nothing(monkeypatch):
    ct = _od_table(full_region=True)
    _scripted_coverage(monkeypatch, [(5, 4), (9, 4)])
    assert sr.repair_from_geometry(ct) is not ct


def test_the_gate_refuses_a_rebuild_that_binds_fewer_figures_even_at_higher_coverage(monkeypatch):
    """Coverage is a NET score. A rebuild binding fewer figures while leaving
    more leftovers unpenalised can outscore the original while actually
    dropping data -- the second condition is what stops that."""
    ct = _od_table(full_region=True)
    _scripted_coverage(monkeypatch, [(5, 6), (9, 3)])
    assert sr.repair_from_geometry(ct) is ct


def test_the_gate_refuses_a_tie(monkeypatch):
    ct = _od_table(full_region=True)
    _scripted_coverage(monkeypatch, [(5, 4), (5, 4)])
    assert sr.repair_from_geometry(ct) is ct


def test_the_gate_refuses_when_either_binding_could_not_be_established(monkeypatch):
    """None means "no measurement", never "zero" -- a plumbing failure must
    leave the table alone, not read as a poor score."""
    ct = _od_table(full_region=True)
    _scripted_coverage(monkeypatch, [None, (9, 4)])
    assert sr.repair_from_geometry(ct) is ct
    _scripted_coverage(monkeypatch, [(5, 4), None])
    assert sr.repair_from_geometry(ct) is ct


def test_the_geometry_rebuild_is_skipped_entirely_when_its_switch_is_off(monkeypatch):
    from app.config import Config
    monkeypatch.setattr(Config, "GEOMETRY_REPAIR_COVERAGE_GATE", False)
    ct = _od_table(full_region=True)
    assert sr.repair_from_geometry(ct) is ct


def test_the_discredited_footing_comparator_is_gone():
    assert not hasattr(sr, "_footing_strength")


# ---------------------------------------------------------------------------
# join_wrapped_labels: a caption that wrapped onto a second printed line
# ---------------------------------------------------------------------------

def _table(md: str):
    from app.tables import parse_markdown_tables
    return parse_markdown_tables(md, 1)[0]


_WRAP_HEADER = "| Particulars | Note | 2024 | 2023 |\n| --- | --- | --- | --- |\n"


def _wrap_table(upper="(a) Property, Plant and Equipment", lower="[and Intangible assets]",
                upper_cells="|  |  |  |", lower_cells="| 3 | 68,67,416.61 | 3,10,253.00 |"):
    return _table(
        _WRAP_HEADER
        + f"| {upper} {upper_cells}\n"
        + f"| {lower} {lower_cells}\n"
        + "| (b) Capital work in progress | 4 | 1,000.00 | 2,000.00 |\n"
    )


def _wrap_lines(upper_x=48.0, lower_x=60.0, lower_y=116.0, include_lower=True):
    lines = [
        _line("(a) Property, Plant and Equipment", upper_x, 100.0, 230.0, 112.0),
        _line("(b) Capital work in progress", 48.0, 140.0, 215.0, 152.0),
    ]
    if include_lower:
        lines.insert(1, _line("[and Intangible assets]", lower_x, lower_y, 190.0, lower_y + 12.0))
    return lines


def test_a_wrapped_caption_is_rejoined_with_the_row_carrying_its_figures():
    table = _wrap_table()
    merged, remap = sr.join_wrapped_labels(table, _wrap_lines())
    assert merged == [0]
    assert len(table.rows) == 2
    assert table.label(0) == "(a) Property, Plant and Equipment [and Intangible assets]"
    assert remap == {0: 0, 1: 0, 2: 1}


def test_joining_a_wrapped_caption_never_changes_a_figure():
    """The invariant that makes this safe: the merged row is the VALUE row's
    cells verbatim -- only the label changes."""
    table = _wrap_table()
    before = list(table.rows[1])
    sr.join_wrapped_labels(table, _wrap_lines())
    after = table.rows[0]
    assert after[1:] == before[1:]


def test_two_sibling_line_items_are_never_joined():
    table = _wrap_table(upper="(a) Borrowings", lower="(b) Trade payables")
    lines = [
        _line("(a) Borrowings", 48.0, 100.0, 200.0, 112.0),
        _line("(b) Trade payables", 60.0, 116.0, 200.0, 128.0),
        _line("(b) Capital work in progress", 48.0, 140.0, 215.0, 152.0),
    ]
    assert sr.join_wrapped_labels(table, lines)[0] == []
    assert len(table.rows) == 3


def test_a_capitalised_lower_caption_is_never_joined():
    table = _wrap_table(upper="Trade payables", lower="Other current liabilities")
    lines = [
        _line("Trade payables", 48.0, 100.0, 200.0, 112.0),
        _line("Other current liabilities", 60.0, 116.0, 220.0, 128.0),
        _line("(b) Capital work in progress", 48.0, 140.0, 215.0, 152.0),
    ]
    assert sr.join_wrapped_labels(table, lines)[0] == []


def test_a_lower_caption_printed_shallower_than_the_upper_is_never_joined():
    assert sr.join_wrapped_labels(_wrap_table(), _wrap_lines(lower_x=30.0))[0] == []


def test_a_row_with_no_printed_line_to_anchor_to_is_never_joined():
    """No anchor means no X position, and without X the predicate refuses."""
    assert sr.join_wrapped_labels(_wrap_table(), _wrap_lines(include_lower=False))[0] == []


def test_two_lines_separated_by_more_than_one_line_height_are_never_joined():
    """A gap wider than one line means a blank row or a rule sits between
    them -- a wrapped caption never has one."""
    assert sr.join_wrapped_labels(_wrap_table(), _wrap_lines(lower_y=132.0))[0] == []


def test_a_section_heading_never_absorbs_the_line_item_beneath_it():
    table = _wrap_table(upper="EQUITY AND LIABILITIES", lower="[and other items]")
    lines = [
        _line("EQUITY AND LIABILITIES", 48.0, 100.0, 230.0, 112.0),
        _line("[and other items]", 60.0, 116.0, 190.0, 128.0),
        _line("(b) Capital work in progress", 48.0, 140.0, 215.0, 152.0),
    ]
    assert sr.join_wrapped_labels(table, lines)[0] == []


def test_an_upper_row_that_already_carries_figures_is_never_joined():
    """Direction-locked: captions wrap DOWNWARD. An upper row with figures is
    a different defect, left to the VLM-corroborated path."""
    table = _wrap_table(upper_cells="| 3 | 1.00 | 2.00 |")
    assert sr.join_wrapped_labels(table, _wrap_lines())[0] == []


def test_an_upper_row_carrying_a_note_reference_is_never_joined():
    """Folding it away would silently discard the note number."""
    table = _wrap_table(upper_cells="| 7 |  |  |")
    assert sr.join_wrapped_labels(table, _wrap_lines())[0] == []


# ---------------------------------------------------------------------------
# Heading vs line item, and the rows that lost their figures
# ---------------------------------------------------------------------------

def _heading_of(label: str, note: str = "") -> bool:
    table = _table(_WRAP_HEADER + f"| {label} | {note} |  |  |\n| (z) filler | 1 | 1.00 | 1.00 |\n")
    return 0 in sr._heading_rows(table, [], {})


def test_a_trailing_colon_marks_a_heading():
    assert _heading_of("Current liabilities:") is True


def test_a_category_enumerator_marks_a_heading():
    assert _heading_of("(1) Shareholders funds") is True
    assert _heading_of("II. Assets") is True


def test_a_leaf_enumerator_marks_a_line_item():
    assert _heading_of("(a) Long-term borrowings") is False
    assert _heading_of("(ii) Deferred tax") is False


def test_an_all_caps_caption_marks_a_heading():
    assert _heading_of("EQUITY AND LIABILITIES") is True


def test_a_total_line_is_never_a_heading_even_in_capitals():
    assert _heading_of("TOTAL") is False


def test_a_caption_carrying_a_note_reference_is_never_a_heading():
    """Rule 2 outranks rule 6: a note number means a figure belongs here."""
    assert _heading_of("EQUITY AND LIABILITIES", note="3") is False


def _classify_fixture(rows_md: str, token_lines):
    table = _table(_WRAP_HEADER + rows_md)
    cells = [
        _cell("Particulars", 0, 0, 48.0, 60.0, 300.0, 80.0),
        _cell("Note", 0, 1, 350.0, 60.0, 400.0, 80.0),
        _cell("2024", 0, 2, 500.0, 60.0, 650.0, 80.0),
        _cell("2023", 0, 3, 700.0, 60.0, 850.0, 80.0),
    ]
    ct = ConvertedTable(page_no=1, markdown=table.to_markdown(), cells=cells,
                        ocr_lines=token_lines, page_height_pt=842.0)
    return table, ct


def test_a_line_item_whose_figures_OCR_read_but_the_grid_dropped_is_reported_lost():
    table, ct = _classify_fixture(
        "| (a) Long-term borrowings |  |  |  |\n| (b) Trade payables | 4 | 1.00 | 2.00 |\n",
        [
            _line("(a) Long-term borrowings", 48.0, 100.0, 230.0, 112.0),
            _line("12,500.00", 520.0, 100.0, 620.0, 112.0),
            _line("(b) Trade payables", 48.0, 130.0, 200.0, 142.0),
        ],
    )
    headings, lost = sr.classify_label_only_rows(table, ct)
    # Per CELL, not per row: the figure sits in the 2024 column only, so the
    # 2023 cell -- which may genuinely be blank -- is NOT reported.
    assert lost == {(0, 2)}
    assert 0 not in headings


def test_a_blank_line_item_with_no_figure_anywhere_is_NOT_reported_lost():
    """A genuinely nil caption and a caption OCR failed to read look exactly
    alike -- flagging the former [unreadable] would assert something was
    printed when nothing was."""
    table, ct = _classify_fixture(
        "| (a) Long-term borrowings |  |  |  |\n| (b) Trade payables | 4 | 1.00 | 2.00 |\n",
        [
            _line("(a) Long-term borrowings", 48.0, 100.0, 230.0, 112.0),
            _line("(b) Trade payables", 48.0, 130.0, 200.0, 142.0),
        ],
    )
    assert sr.classify_label_only_rows(table, ct)[1] == set()


def test_a_total_with_no_figure_is_reported_lost_even_in_capitals():
    """Rule 1 beats the all-caps rule: a bare TOTAL is a line item that lost
    its figures, never a heading -- and no OCR evidence is needed, because a
    statement never prints a total with nothing against it."""
    table, ct = _classify_fixture(
        "| (a) Trade payables | 4 | 1.00 | 2.00 |\n| TOTAL |  |  |  |\n",
        [_line("(a) Trade payables", 48.0, 100.0, 200.0, 112.0),
         _line("TOTAL", 48.0, 130.0, 100.0, 142.0)],
    )
    assert sr.classify_label_only_rows(table, ct)[1] == {(1, 2), (1, 3)}


def test_a_section_heading_is_never_reported_lost_even_with_figures_beside_it():
    table, ct = _classify_fixture(
        "| EQUITY AND LIABILITIES |  |  |  |\n| (b) Trade payables | 4 | 1.00 | 2.00 |\n",
        [
            _line("EQUITY AND LIABILITIES", 48.0, 100.0, 230.0, 112.0),
            _line("9,999.00", 520.0, 100.0, 620.0, 112.0),
            _line("(b) Trade payables", 48.0, 130.0, 200.0, 142.0),
        ],
    )
    headings, lost = sr.classify_label_only_rows(table, ct)
    assert 0 in headings and lost == set()


# ---------------------------------------------------------------------------
# A table layout analysis never found
#
# Positions are transcribed from a real docling conversion of OD-SPSU-SO-032
# 2021-22 SFS page 2 ("Statement of Income & Expenditure", almost no ruling
# lines). Docling produced 55 layout clusters and none was a table; OCR read
# every figure correctly; emit.build_text_records then dropped each one for
# being under 25 characters. Coordinates: (left, top, right, bottom), page
# points, TOP-LEFT origin.
# ---------------------------------------------------------------------------

def _frag(text, l, t, r, b, label="text"):
    return sr.TextFragment(text=text, bbox=(l, t, r, b), label=label)


def _income_expenditure_page():
    return [
        _frag("Statement of Income & Expenditure for the period From 30th March, 2022 to 31st March, 2022",
              234, 187, 689, 233, "section_header"),
        _frag("Partculars", 140, 291, 229, 310, "section_header"),
        _frag("Notes", 566, 289, 619, 308),
        _frag("For the year ended 31st March, 2022", 690, 282, 845, 323),
        _frag("Revenue from operation", 139, 326, 307, 347, "list_item"),
        _frag("Other Income", 98, 350, 241, 371, "list_item"),
        _frag("III. Total Income (I+II)", 96, 379, 284, 399, "section_header"),
        _frag("Expenses:", 139, 407, 218, 428, "section_header"),
        _frag("Employee Benefit Expenses", 140, 430, 333, 451),
        _frag("Finance Cost", 140, 450, 234, 469),
        _frag("7", 586, 451, 601, 469),
        _frag("460", 811, 450, 844, 471),
        _frag("Other Expenses", 140, 473, 251, 492),
        _frag("8", 586, 472, 601, 491),
        _frag("25,000", 791, 471, 844, 492),
        _frag("Total Expenditure", 162, 494, 300, 514, "section_header"),
        _frag("25,460", 790, 494, 844, 514),
        _frag("V. (Defecit)/Surplus before Tax expenses (III-IV)", 98, 538, 475, 557, "section_header"),
        _frag("VI. (Defecit)/Surplus after Tax expenses (III-IV)", 98, 652, 470, 672, "section_header"),
        _frag("(25,460)", 786, 651, 849, 673),
        _frag("Earnings per equity share- Basic", 83, 692, 296, 710, "section_header"),
        _frag("9", 587, 691, 602, 709),
        _frag("(0.17)", 809, 691, 850, 711),
        # Page furniture and the signature block: must never be figures.
        _frag("Significant Accounting Policies", 83, 723, 282, 740),
        _frag("2", 456, 722, 470, 739),
        _frag("Membership No: 059274", 83, 806, 270, 845),
        _frag("11", 817, 1139, 837, 1160, "page_footer"),
    ]


def test_orphan_figures_find_every_lost_figure_and_no_furniture():
    frags = _income_expenditure_page()
    orphans = sr.orphan_figures(frags, table_boxes=[], picture_boxes=[])
    assert sorted(f.text for f in orphans) == sorted(
        ["7", "460", "8", "25,000", "25,460", "(25,460)", "9", "(0.17)", "2"]
    )  # "11" is a page_footer; "059274" sits inside a longer text item


def test_a_figure_inside_a_detected_table_is_not_an_orphan():
    frags = _income_expenditure_page()
    assert sr.orphan_figures(frags, [(0, 0, 2000, 2000)], []) == []


def test_a_figure_inside_a_picture_region_is_not_an_orphan():
    frags = _income_expenditure_page()
    stamp = (780, 640, 860, 680)  # around "(25,460)"
    left = {f.text for f in sr.orphan_figures(frags, [], [stamp])}
    assert "(25,460)" not in left


def test_synthesizes_the_income_and_expenditure_table():
    frags = _income_expenditure_page()
    orphans = sr.orphan_figures(frags, [], [])
    built = sr.synthesize_table(frags, orphans, [])
    assert built is not None
    lines = built.markdown.splitlines()
    assert lines[0] == "| Partculars | Notes | For the year ended 31st March, 2022 |"
    body = {l.split("|")[1].strip(): [c.strip() for c in l.split("|")[2:-1]] for l in lines[2:]}
    assert body["Finance Cost"] == ["7", "460"]
    assert body["Other Expenses"] == ["8", "25,000"]
    assert body["Total Expenditure"] == ["", "25,460"]
    assert body["VI. (Defecit)/Surplus after Tax expenses (III-IV)"] == ["", "(25,460)"]
    assert body["Earnings per equity share- Basic"] == ["9", "(0.17)"]
    # Rows with no printed figure stay blank -- nothing is invented for them.
    assert body["Revenue from operation"] == ["", ""]
    assert built.title.startswith("Statement of Income & Expenditure")
    assert built.figures == 8  # "2" is a lone figure, not a column


def test_the_rebuilt_table_foots():
    from app.tables import parse_markdown_tables
    from app.verify import verify_table

    frags = _income_expenditure_page()
    built = sr.synthesize_table(frags, sr.orphan_figures(frags, [], []), [])
    table = parse_markdown_tables(built.markdown, 1, prefix="t1_")[0]
    footings, _ = verify_table(table, 1)
    passed = {f.subtotal_label: f.passed for f in footings}
    assert passed.get("Total Expenditure") is True  # 460 + 25,000 = 25,460


def test_prose_with_a_few_numbers_does_not_synthesize():
    frags = [
        _frag("Auditor's remarks", 90, 100, 300, 120, "section_header"),
        _frag("The company has 3 divisions", 90, 130, 400, 150),
        _frag("12", 700, 130, 720, 150),
        _frag("Other matters", 90, 160, 300, 180),
        _frag("15", 700, 160, 720, 180),
    ]
    orphans = sr.orphan_figures(frags, [], [])
    assert sr.synthesize_table(frags, orphans, []) is None  # only 2 in the column


def test_a_column_of_figures_with_no_labels_does_not_synthesize():
    frags = [_frag(str(n), 700, 100 + 25 * i, 730, 120 + 25 * i) for i, n in enumerate((10, 20, 30, 40))]
    frags.append(_frag("Heading", 700, 60, 760, 80))
    assert sr.synthesize_table(frags, sr.orphan_figures(frags, [], []), []) is None


def test_a_rebuild_never_overlaps_a_detected_table():
    frags = _income_expenditure_page()
    orphans = sr.orphan_figures(frags, [], [])
    assert sr.synthesize_table(frags, orphans, [(300, 300, 700, 500)]) is None


# ---------------------------------------------------------------------------
# Rows below a detected table that its box stopped short of
#
# Real docling conversion of OD-SPSU-SO-032 2021-22 SFS page 1 (balance sheet).
# The detected table's box ends at y=526 (the liabilities subtotal); the
# liabilities TOTAL, the whole ASSETS section and the closing TOTAL lie below
# it. Fragment positions are transcribed from that run. The detected table's
# own figures are represented by their columns' right edges (amounts ~841,
# notes ~559, from its cell bands); their Y values are not needed by the
# anchor logic and are placeholders.
# ---------------------------------------------------------------------------

_BS_DETECTED_MD = """\
|                           | Partculars                    | Notes | As at 31st March, 2022 |
|---------------------------|-------------------------------|-------|-----------|
| I. EQUITY AND LIABILITIES | I. EQUITY AND LIABILITIES     |       |           |
|                           | (a) Share Capital             | 3     | 15,00,000 |
|                           | (b) Surplus                   | 4     | (25,460)  |
|                           |                               |       | 14,74,540 |
|                           | (c) Other current liabilities | 5     | 25,000    |
"""


def _bs_ocr():
    def line(text, r, y):
        return NS(text=text, confidence=0.99, bbox=(r - 60, y, r, y + 17))
    return [
        line("15,00,000", 841, 250), line("(25,460)", 840, 270), line("14,74,540", 842, 300),
        line("25,000", 841, 470), line("3", 559, 250), line("4", 559, 270), line("5", 559, 470),
    ]


def _bs_fragments():
    return [
        _frag("TOTAL", 308, 529, 366, 546, "section_header"),
        _frag("14,99,540", 777, 529, 841, 547),
        _frag("II. ASSETS", 81, 568, 171, 585, "section_header"),
        _frag("1", 156, 587, 168, 603),
        _frag("Non-current assets", 212, 587, 336, 604),
        _frag("(a) Tangible Assets assets", 213, 607, 370, 623),
        _frag("Less: Depreciation", 223, 644, 339, 661),
        _frag("2", 156, 733, 169, 750),
        _frag("Current assets", 213, 735, 308, 750),
        _frag("(a) Cash and cash equivalents", 212, 752, 394, 770, "list_item"),
        _frag("14,99,540", 778, 752, 841, 770),
        _frag("6", 548, 754, 559, 768),
        _frag("(c) Other current assets", 213, 808, 355, 826, "list_item"),
        _frag("14,99,540", 778, 830, 842, 847),
        _frag("TOTAL", 330, 868, 388, 886),
        _frag("14,99,540", 778, 869, 842, 886),
        _frag("Significant Accounting Policies The accompanying notes 1-9", 74, 899, 520, 947),
        _frag("2.", 588, 899, 605, 916),
    ]


_BS_BOX = (70.0, 184.0, 845.0, 526.5)


def test_rows_below_a_detected_table_are_rebuilt_under_its_headings():
    frags = _bs_fragments()
    orphans = sr.orphan_figures(frags, [_BS_BOX], [])
    built = sr.synthesize_continuation(frags, orphans, _BS_DETECTED_MD, _bs_ocr(), _BS_BOX)
    assert built is not None
    table, consumed = built
    lines = table.markdown.splitlines()
    assert lines[0] == "| Partculars | Notes | As at 31st March, 2022 |"
    body = {l.split("|")[1].strip(): [c.strip() for c in l.split("|")[2:-1]] for l in lines[2:]}
    assert body["(a) Cash and cash equivalents"] == ["6", "14,99,540"]
    assert body["1 Non-current assets"] == ["", ""]
    assert len(consumed) == 5  # four 14,99,540 and the note 6


def test_continuation_refuses_when_the_figures_are_not_in_the_tables_columns():
    frags = [_frag("x", 0, 0, 1, 1)] + [
        _frag(str(n), 300, 540 + 25 * i, 330, 560 + 25 * i) for i, n in enumerate((10, 20, 30))
    ]
    orphans = sr.orphan_figures(frags, [_BS_BOX], [])
    assert sr.synthesize_continuation(frags, orphans, _BS_DETECTED_MD, _bs_ocr(), _BS_BOX) is None


def test_continuation_refuses_when_a_gap_separates_it_from_the_table():
    frags = [_frag("Cash", 212, 900, 300, 918)] + [
        _frag("14,99,540", 778, 900 + 25 * i, 842, 918 + 25 * i) for i in range(3)
    ]
    orphans = sr.orphan_figures(frags, [_BS_BOX], [])
    assert sr.synthesize_continuation(frags, orphans, _BS_DETECTED_MD, _bs_ocr(), _BS_BOX) is None
