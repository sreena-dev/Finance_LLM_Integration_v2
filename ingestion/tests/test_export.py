"""The record contract the gateway and the UI read, byte for byte."""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import export, reconcile, structure  # noqa: E402
from app.second_read import RowRead  # noqa: E402
from tests._helpers import BALANCE_SHEET, make_tokens, region_for  # noqa: E402
from tests.accuracy._parse import parse_markdown_tables  # noqa: E402

PAGE_TEXT = "## Balance Sheet as at 31st March, 2024\n\n(Amount in thousand)"


def build(reads=None, rows=BALANCE_SHEET, title="Balance Sheet", page_text=PAGE_TEXT):
    tokens = make_tokens(rows)
    plan = structure.build_plan(tokens, region_for(tokens, title=title), 1, None, None, use_llm=False, doc_id=None)
    value_cols = [c.index for c in plan.value_columns]
    results = {}
    for row in plan.rows:
        results[row.index], _ = reconcile.reconcile_row(row, value_cols, (reads or {}).get(row.label))
    return plan, export.build_table_record(plan, results, "up_abc123", page_text, None, "fs.pdf")


def cell_at(record, row_index, col_index):
    lines = record.table_md.split("\n")
    line = lines[row_index + 2]
    return [c.strip() for c in line.strip()[1:-1].split("|")][col_index]


def test_required_record_fields_are_present_and_typed():
    _, (record, info) = build()
    d = record.as_dict()
    assert record.table_id == "up_abc123_t1" and d["doc_id"] == "up_abc123"
    assert record.financial_stmt_type == "balance_sheet"
    assert record.unit == "thousand" and record.is_financial
    assert record.page_ocr_start == 1 and record.source_file == "fs.pdf"
    assert record.table_title == "Balance Sheet" and record.table_description
    assert info.stmt_type == "balance_sheet"


def test_table_md_is_a_pipe_table_that_the_scoring_parser_reads_back():
    _, (record, _) = build()
    lines = record.table_md.split("\n")
    assert lines[0].startswith("| Particulars | Note | 31st March, 2024 | 31st March, 2023 |")
    assert set(lines[1].replace("|", "").split()) == {"---"}
    parsed = parse_markdown_tables(record.table_md, 1)[0]
    assert parsed.label_col == 0 and parsed.note_col == 1 and parsed.value_cols == [2, 3]
    share = next(r for r in range(len(parsed.rows)) if parsed.label(r) == "Share capital")
    assert parsed.rows[share][2] == "1,00,000" and parsed.rows[share][1] == "1"


def test_a_flagged_cell_becomes_a_recovered_marker_addressed_exactly():
    reads = {"Reserves and surplus": RowRead(["2,50,800", "2,00,000"])}
    _, (record, _) = build(reads)
    assert len(record.findings) == 1
    f = record.findings[0]
    assert f.row_label == "Reserves and surplus" and f.column == "31st March, 2024"
    assert f.raw == "2,50,000" and f.recovered_text == "2,50,800" and "readers_disagree" in f.reasons
    assert cell_at(record, f.row_index, f.col_index) == f.marker
    assert f.marker.startswith("[recovered 2,50,800; second read, confidence low")
    assert "row \"Reserves and surplus\", col \"31st March, 2024\"" in f.marker
    assert "table t1" in f.marker and "page 1" in f.marker


def test_an_unreadable_cell_is_the_exact_unreadable_marker():
    tokens = make_tokens([
        BALANCE_SHEET[0], BALANCE_SHEET[2], ("Reserves and surplus", "2", ["2,5O,000", "2,00,000"]),
    ])
    plan = structure.build_plan(tokens, region_for(tokens), 1, None, None, use_llm=False, doc_id=None)
    cols = [c.index for c in plan.value_columns]
    results = {r.index: reconcile.reconcile_row(r, cols, None)[0] for r in plan.rows}
    record, _ = export.build_table_record(plan, results, "up_abc123", PAGE_TEXT, None, None)
    f = record.findings[0]
    assert f.recovered_text is None
    assert f.marker == '[unreadable: page 1, table t1, row "Reserves and surplus", col "31st March, 2024"]'
    assert cell_at(record, f.row_index, f.col_index) == f.marker
    assert re.fullmatch(r"\[unreadable: page \d+, table \S+, row \".*\", col \".*\"\]", f.marker)


def test_a_flagged_figure_never_parses_as_a_number_anywhere():
    reads = {"Reserves and surplus": RowRead(["2,50,800", "2,00,000"])}
    _, (record, _) = build(reads)
    parsed = parse_markdown_tables(record.table_md, 1)[0]
    row = next(r for r in range(len(parsed.rows)) if parsed.label(r) == "Reserves and surplus")
    from tests.accuracy._parse import parse_cell
    assert parse_cell(parsed.rows[row][2]).value is None


def test_pipes_in_text_cannot_shift_the_columns():
    rows = [BALANCE_SHEET[0], ("Loans | advances", "1", ["10,000", "9,000"]), ("Total", "", ["10,000", "9,000"])]
    _, (record, _) = build(rows=rows)
    for line in record.table_md.split("\n"):
        assert line.count("|") == record.table_md.split("\n")[0].count("|")


def test_footings_and_balance_sheet_identity_are_computed_into_the_record():
    rows = [
        BALANCE_SHEET[0],
        ("Share capital", "1", ["1,00,000", "1,00,000"]),
        ("Reserves", "2", ["2,50,000", "2,00,000"]),
        ("Total equity and liabilities", "", ["3,50,000", "3,00,000"]),
        ("Total assets", "", ["3,50,000", "2,90,000"]),
    ]
    _, (record, _) = build(rows=rows)
    assert record.confidence is not None
    failed = [f for f in record.footings if not f.passed]
    assert any("Total assets" in f.subtotal_label for f in failed)


def test_cross_table_links_compare_a_note_total_with_the_line_that_cites_it():
    _, (_, statement) = build()
    note_rows = [
        ("Particulars", "", ["31st March, 2024", "31st March, 2023"]),
        ("Opening", "", ["2,00,000", "1,50,000"]),
        ("Addition", "", ["50,000", "50,000"]),
        ("Total", "", ["2,50,000", "2,00,000"]),
    ]
    _, (_, note) = build(rows=note_rows, title="Note: 2 Reserves and surplus", page_text="")
    links = export.cross_table_links([statement, note])
    assert len(links) == 2
    checks = __import__("app.validate", fromlist=["x"]).check_cross_table(links)
    assert all(c.passed for c in checks) and "Note 2" in checks[0].subtotal_label


def test_cross_table_link_is_skipped_when_the_periods_do_not_match():
    _, (_, statement) = build()
    note_rows = [
        ("Particulars", "", ["31st March, 2022", "31st March, 2021"]),
        ("Total", "", ["1", "2"]),
    ]
    _, (_, note) = build(rows=note_rows, title="Note: 2 Reserves", page_text="")
    assert export.cross_table_links([statement, note]) == []
