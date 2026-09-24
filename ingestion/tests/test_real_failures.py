"""Failures seen on real filings, replayed offline.

Each case is built from what the audit logs recorded for a table that went
wrong in a full run -- the tokens' text and column positions, and the exact
structure reply Gemma gave -- so the fix is checked without docling or the
model, and stays fixed.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import structure  # noqa: E402
from app.llm_client import LLMResult  # noqa: E402
from app.tabletypes import ROLE_IGNORE, ROLE_NOTE, ROLE_VALUE  # noqa: E402
from tests._helpers import make_tokens, region_for  # noqa: E402

# OD-SPSU-SO-032 2023-24, balance sheet. Columns as the log recorded them: notes at
# x=1553-1627, 2024 figures right-aligned at 2712, 2023 figures at 3563.
OD_ROWS = [
    ("(R up ees in iN k )", "", []),
    ("Particulars", "Note", ["Figures as at 31st March,", "Figures as at 31st March,"]),
    ("", "No.", ["2024", "2023"]),
    ("1", "2", ["3", "4"]),
    ("I. Equity and Liabilities", "", []),
    ("(a) Share capital", "1", ["15,00,000.00", "15,00,000.00"]),
    ("(b) Reserves and surplus", "2", ["1,04,38,195.33", "38,81,031.72"]),
    ("(b) Refundable Grant", "3", ["29,81,00,248.00", "15,00,34,815.00"]),
    ("(b) Trade payables", "4", ["18,600.00", "34,011.00"]),
    ("Total", "", ["64,88,82,047.93", "43,34,97,783.82"]),
    ("(b) Cash and cash equivalents", "8", ["31,94,52,732.35", "25,46,94,119.72"]),
    ("TOTAL", "", ["64,88,82,047.93", "43,34,97,783.82"]),
]
KINDS = ["blank", "header", "header", "header", "heading", "item", "item", "item", "item",
         "total", "item", "total"]


class Replay:
    def __init__(self, columns):
        self.columns = columns

    def configured(self):
        return True

    def chat(self, kind, prompt, images=None, **kw):
        rows = [{"id": i, "kind": k, "join": None} for i, k in enumerate(KINDS)]
        return LLMResult(json.dumps({"columns": self.columns, "rows": rows, "moves": []}), True)


def plan_with(monkeypatch, columns):
    monkeypatch.setattr(structure, "CLIENT", Replay(columns))
    tokens = make_tokens(OD_ROWS, note_right=1627, value_rights=(2712, 3563))
    return structure.build_plan(tokens, region_for(tokens), 1, None, None, use_llm=True, doc_id=None)


def check_two_year_columns(plan):
    vals = plan.value_columns
    assert len(vals) == 2, [c.role for c in plan.columns]
    assert [c.period for c in vals] == ["2024", "2023"]
    reserves = next(r for r in plan.rows if r.label == "(b) Reserves and surplus")
    assert [[t.text for t in reserves.cells[c.index]] for c in vals] == [["1,04,38,195.33"], ["38,81,031.72"]]
    assert reserves.note == "2"


def test_the_real_reply_numbering_columns_past_the_last_one_shown_is_not_trusted(monkeypatch):
    # Exactly what Gemma answered: ids 1-4 for a table with three columns.
    plan = plan_with(monkeypatch, [
        {"id": 1, "role": "ignore"}, {"id": 2, "role": "note"},
        {"id": 3, "role": "value"}, {"id": 4, "role": "value"},
    ])
    assert plan.structure_confirmed
    assert any("past the last one shown" in n for n in plan.notes)
    check_two_year_columns(plan)


def test_wrong_roles_on_the_right_number_of_columns_are_overruled_by_the_content(monkeypatch):
    # Same mistake without the extra id: the 2024 amounts column called a note column.
    plan = plan_with(monkeypatch, [
        {"id": 1, "role": "ignore"}, {"id": 2, "role": "note"}, {"id": 3, "role": "value"},
    ])
    assert plan.structure_confirmed
    assert [c.role for c in plan.columns][1:].count(ROLE_VALUE) == 2
    assert any("full of amounts" in n for n in plan.notes)
    vals = plan.value_columns
    assert [c.period for c in vals] == ["2024", "2023"]


def test_a_correct_reply_is_left_alone(monkeypatch):
    plan = plan_with(monkeypatch, [
        {"id": 1, "role": "note"}, {"id": 2, "role": "value"}, {"id": 3, "role": "value"},
    ])
    check_two_year_columns(plan)
    assert not any("stays a" in n or "past the last" in n for n in plan.notes)


def test_a_note_reference_column_called_a_value_column_stays_a_note_column(monkeypatch):
    plan = plan_with(monkeypatch, [
        {"id": 1, "role": "value"}, {"id": 2, "role": "value"}, {"id": 3, "role": "value"},
    ])
    check_two_year_columns(plan)
    assert any("holds note references" in n for n in plan.notes)


def test_roles_are_decided_by_content_without_any_model():
    tokens = make_tokens(OD_ROWS, note_right=1627, value_rights=(2712, 3563))
    plan = structure.build_plan(tokens, region_for(tokens), 1, None, None, use_llm=False, doc_id=None)
    check_two_year_columns(plan)


# ---- header checks -----------------------------------------------------------------

def _plan(rows, **kw):
    tokens = make_tokens(rows, **kw)
    return structure.build_plan(tokens, region_for(tokens), 1, None, None, use_llm=False, doc_id=None)


def test_a_clean_two_year_table_raises_no_header_notes():
    assert structure.header_notes(_plan(OD_ROWS, note_right=1627, value_rights=(2712, 3563))) == []


def test_two_columns_printing_the_same_year_are_noted():
    rows = [("Particulars", "", ["31st March, 2024", "31st March, 2024"]),
            ("Revenue", "", ["100,000", "90,000"]), ("Total", "", ["100,000", "90,000"])]
    notes = structure.header_notes(_plan(rows))
    assert any("same year" in n for n in notes)


def test_a_year_on_only_some_columns_is_noted():
    rows = [("Particulars", "", ["31st March, 2024", "Amount"]),
            ("Revenue", "", ["100,000", "90,000"]), ("Total", "", ["100,000", "90,000"])]
    assert any("only some figure columns" in n for n in structure.header_notes(_plan(rows)))


def test_headings_without_any_year_are_not_noise():
    rows = [("Particulars", "", ["Amount", "Amount"]),
            ("Revenue", "", ["100,000", "90,000"]), ("Total", "", ["100,000", "90,000"])]
    assert structure.header_notes(_plan(rows)) == []


def test_ascending_years_are_still_reported():
    rows = [("Particulars", "", ["31st March, 2023", "31st March, 2024"]),
            ("Revenue", "", ["100,000", "90,000"]), ("Total", "", ["100,000", "90,000"])]
    assert any("ascending" in n for n in structure.header_notes(_plan(rows)))


# ---- replayable token dumps --------------------------------------------------------

def test_a_tables_tokens_are_dumped_and_load_back_identically(tmp_path, monkeypatch):
    monkeypatch.setattr(structure.Config, "LLM_LOG_DIR", str(tmp_path))
    tokens = make_tokens(OD_ROWS, note_right=1627, value_rights=(2712, 3563))
    region = region_for(tokens, title="Balance Sheet")
    structure.build_plan(tokens, region, 7, None, None, use_llm=False, doc_id="up_replay")
    path = tmp_path / "up_replay" / "tables" / "t7.json"
    assert path.exists()
    loaded_region, loaded = structure.load_dump(path)
    assert loaded_region.title == "Balance Sheet" and loaded_region.bbox == region.bbox
    assert [(t.id, t.text, t.bbox, t.conf) for t in loaded] == [(t.id, t.text, t.bbox, t.conf) for t in tokens]
    replay = structure.build_plan(loaded, loaded_region, 7, None, None, use_llm=False, doc_id=None)
    check_two_year_columns(replay)


def test_a_dump_failure_never_loses_the_table(monkeypatch):
    monkeypatch.setattr(structure.Config, "LLM_LOG_DIR", "\0invalid")
    tokens = make_tokens(OD_ROWS, note_right=1627, value_rights=(2712, 3563))
    plan = structure.build_plan(tokens, region_for(tokens), 1, None, None, use_llm=False, doc_id="up_x")
    assert plan.rows


def test_a_units_line_printed_over_a_figure_column_is_a_heading_not_a_data_row(monkeypatch):
    # The log's first row: the "(Rupees in INR)" line sits over the 2023 column, so its
    # token lands in a figure column. It must not make the real headings read as data.
    rows = [("", "", ["", "(R up ees in iN k )"])] + OD_ROWS[1:]
    monkeypatch.setattr(structure, "CLIENT", Replay([
        {"id": 1, "role": "note"}, {"id": 2, "role": "value"}, {"id": 3, "role": "value"},
    ]))
    tokens = make_tokens(rows, note_right=1627, value_rights=(2712, 3563))
    plan = structure.build_plan(tokens, region_for(tokens), 1, None, None, use_llm=True, doc_id=None)
    assert plan.rows[0].label == "I. Equity and Liabilities"
    assert not any("R up ees" in r.label or r.label == "Particulars" for r in plan.rows)
    assert [c.period for c in plan.value_columns] == ["2024", "2023"]
    assert all(c.header for c in plan.value_columns)


def test_the_same_holds_with_no_model(monkeypatch):
    rows = [("", "", ["", "(R up ees in iN k )"])] + OD_ROWS[1:]
    tokens = make_tokens(rows, note_right=1627, value_rights=(2712, 3563))
    plan = structure.build_plan(tokens, region_for(tokens), 1, None, None, use_llm=False, doc_id=None)
    assert plan.rows[0].label == "I. Equity and Liabilities"
    assert [c.period for c in plan.value_columns] == ["2024", "2023"]


def test_a_line_item_with_no_amounts_yet_is_not_mistaken_for_a_heading():
    rows = [("Particulars", "Note", ["2024", "2023"]),
            ("Opening", "", ["1,00,000", "90,000"]), ("Total", "", ["1,00,000", "90,000"])]
    plan = _plan(rows)
    assert [r.label for r in plan.rows] == ["Opening", "Total"]


def test_a_first_row_with_a_small_whole_number_amount_is_data_not_a_heading():
    # OD 2021-22 page 2: the table's first line is "Finance Cost | 7 | 460". "460" has no
    # comma and only three digits, and the row was being swallowed as the column heading.
    rows = [
        ("Finance Cost", "7", ["460"]),
        ("Other Expenses", "8", ["25,000"]),
        ("Total Expenditure", "", ["25,460"]),
    ]
    tokens = make_tokens(rows, value_rights=(1450,))
    plan = structure.build_plan(tokens, region_for(tokens), 1, None, None, use_llm=False, doc_id=None)
    assert [r.label for r in plan.rows] == ["Finance Cost", "Other Expenses", "Total Expenditure"]
    first = plan.rows[0]
    assert [t.text for t in first.cells[plan.value_columns[0].index]] == ["460"]
    assert first.note == "7"
    assert plan.columns[0].header == ""            # nothing was taken as a heading


def test_a_table_of_only_small_whole_numbers_keeps_every_row():
    rows = [("Particulars", "", ["2022", "2021"]),
            ("Directors", "", ["12", "10"]), ("Employees", "", ["340", "310"]),
            ("Branches", "", ["7", "7"])]
    tokens = make_tokens(rows)
    plan = structure.build_plan(tokens, region_for(tokens), 1, None, None, use_llm=False, doc_id=None)
    assert [r.label for r in plan.rows] == ["Directors", "Employees", "Branches"]
    assert [c.period for c in plan.value_columns] == ["2022", "2021"]
