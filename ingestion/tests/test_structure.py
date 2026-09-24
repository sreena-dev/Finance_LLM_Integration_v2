"""Table structure: geometry proposes, Gemma organises, geometry validates."""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import structure  # noqa: E402
from app.llm_client import LLMResult  # noqa: E402
from app.tabletypes import KIND_HEADING, KIND_TOTAL, ROLE_NOTE, ROLE_VALUE  # noqa: E402
from tests._helpers import BALANCE_SHEET, make_tokens, region_for  # noqa: E402


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.last = None
        self.prompts = []

    def configured(self):
        return True

    def chat(self, kind, prompt, images=None, **kw):
        self.prompts.append(prompt)
        reply = self.replies.pop(0) if self.replies else self.last
        self.last = reply
        if reply is None:
            return LLMResult(None, False, "boom")
        return LLMResult(reply if isinstance(reply, str) else json.dumps(reply), True)


@pytest.fixture
def fake(monkeypatch):
    def install(replies):
        f = FakeLLM(replies)
        monkeypatch.setattr(structure, "CLIENT", f)
        return f
    return install


def _build(rows, use_llm=False, **kw):
    tokens = make_tokens(rows, **kw)
    return structure.build_plan(tokens, region_for(tokens), 1, None, None, use_llm=use_llm, doc_id=None)


def test_geometry_alone_finds_label_note_and_two_value_columns():
    plan = _build(BALANCE_SHEET)
    roles = [c.role for c in plan.columns]
    assert roles[0] == "label" and roles.count(ROLE_NOTE) == 1 and roles.count(ROLE_VALUE) == 2
    assert not plan.structure_confirmed


def test_geometry_alone_builds_rows_kinds_and_headers():
    plan = _build(BALANCE_SHEET)
    labels = [r.label for r in plan.rows]
    assert labels == ["EQUITY AND LIABILITIES", "Share capital", "Reserves and surplus", "Total equity"]
    kinds = {r.label: r.kind for r in plan.rows}
    assert kinds["EQUITY AND LIABILITIES"] == KIND_HEADING and kinds["Total equity"] == KIND_TOTAL
    assert [c.header for c in plan.value_columns] == ["31st March, 2024", "31st March, 2023"]
    assert [c.period for c in plan.value_columns] == ["2024", "2023"]


def test_figures_land_in_their_own_columns_and_notes_stay_out_of_them():
    plan = _build(BALANCE_SHEET)
    share = next(r for r in plan.rows if r.label == "Share capital")
    assert share.note == "1"
    values = [[t.text for t in toks] for _, toks in sorted(share.cells.items())]
    assert values == [["1,00,000"], ["1,00,000"]]


def _decisions(n_rows=5, kinds=None, joins=None, moves=None, col_roles=None):
    kinds = kinds or ["header", "heading", "item", "item", "total"]
    return {
        "columns": [{"id": i, "role": r} for i, r in (col_roles or {1: "note", 2: "value", 3: "value"}).items()],
        "rows": [{"id": i, "kind": kinds[i], "join": (joins or {}).get(i)} for i in range(n_rows)],
        "moves": moves or [],
    }


def test_a_valid_gemma_reply_is_accepted_and_confirms_structure(fake):
    f = fake([_decisions()])
    plan = _build(BALANCE_SHEET, use_llm=True)
    assert plan.structure_confirmed and len(f.prompts) == 1
    assert [r.label for r in plan.rows][-1] == "Total equity"


def test_gemma_can_overrule_the_geometry_default_for_a_row_kind(fake):
    fake([_decisions(kinds=["header", "heading", "item", "item", "subtotal"])])
    plan = _build(BALANCE_SHEET, use_llm=True)
    assert plan.rows[-1].kind == "subtotal"


def test_a_row_id_that_is_not_in_the_table_is_ignored_not_fatal(fake):
    reply = _decisions()
    reply["rows"].append({"id": 99, "kind": "item", "join": None})
    f = fake([reply])
    plan = _build(BALANCE_SHEET, use_llm=True)
    assert plan.structure_confirmed and len(f.prompts) == 1
    assert any("not one of the rows shown" in n for n in plan.notes)


def test_two_bad_replies_fall_back_to_geometry_and_say_so(fake):
    bad = _decisions()
    bad["rows"] = "not a list"
    fake([bad, bad])
    plan = _build(BALANCE_SHEET, use_llm=True)
    assert not plan.structure_confirmed
    assert any("could not be validated" in n for n in plan.notes)
    assert [r.label for r in plan.rows][-1] == "Total equity"          # still a usable table


def test_a_failed_model_call_falls_back_to_geometry(fake):
    fake([None])
    assert not _build(BALANCE_SHEET, use_llm=True).structure_confirmed


def test_a_reply_that_is_not_json_is_rejected(fake):
    fake(["I cannot help with that.", "still no json"])
    assert not _build(BALANCE_SHEET, use_llm=True).structure_confirmed


def test_a_repeated_row_id_keeps_the_first_entry(fake):
    reply = _decisions()
    reply["rows"][3]["id"] = 2
    fake([reply])
    plan = _build(BALANCE_SHEET, use_llm=True)
    assert plan.structure_confirmed
    assert any("second entry for row 2" in n for n in plan.notes)


def test_a_move_of_a_nonexistent_token_is_ignored_and_the_rest_of_the_reply_kept(fake):
    fake([_decisions(moves=[{"token": 9999, "column": 2}])])
    plan = _build(BALANCE_SHEET, use_llm=True)
    assert plan.structure_confirmed
    assert any("no such token" in n for n in plan.notes)


def test_a_move_into_a_non_figure_column_is_ignored(fake):
    fake([_decisions(moves=[{"token": 1, "column": 0}])])
    plan = _build(BALANCE_SHEET, use_llm=True)
    assert plan.structure_confirmed
    assert any("not a figure column" in n for n in plan.notes)


WRAPPED = [
    ("Particulars", "Note", ["31st March, 2024", "31st March, 2023"]),
    ("Property, Plant and", "", []),
    ("Equipment", "3", ["68,67,416", "3,10,253"]),
    ("Total", "", ["68,67,416", "3,10,253"]),
]


def test_a_wrapped_label_is_joined_when_gemma_says_so(fake):
    fake([_decisions(n_rows=4, kinds=["header", "item", "item", "total"], joins={1: "next"})])
    plan = _build(WRAPPED, use_llm=True)
    assert plan.structure_confirmed
    assert [r.label for r in plan.rows] == ["Property, Plant and Equipment", "Total"]


def test_a_join_on_a_row_that_has_figures_is_ignored_not_fatal(fake):
    fake([_decisions(n_rows=4, kinds=["header", "item", "item", "total"], joins={2: "next"})])
    plan = _build(WRAPPED, use_llm=True)
    assert plan.structure_confirmed
    assert any("figures of its own" in n for n in plan.notes)
    assert [r.label for r in plan.rows] == ["Property, Plant and", "Equipment", "Total"]      # nothing merged


def test_marking_both_halves_of_a_wrapped_label_merges_it_once(fake):
    fake([_decisions(n_rows=4, kinds=["header", "item", "item", "total"], joins={1: "next", 2: "prev"})])
    plan = _build(WRAPPED, use_llm=True)
    assert plan.structure_confirmed
    assert [r.label for r in plan.rows] == ["Property, Plant and Equipment", "Total"]
    assert plan.rows[0].note == "3"


def test_the_continuation_line_may_carry_the_note_number(fake):
    fake([_decisions(n_rows=4, kinds=["header", "item", "item", "total"], joins={2: "prev"})])
    rows = [
        ("Particulars", "Note", ["31st March, 2024", "31st March, 2023"]),
        ("Income from Investments (Income on", "", ["1,000", "2,000"]),
        ("Investment from funds)", "15", []),
        ("Total", "", ["1,000", "2,000"]),
    ]
    plan = _build(rows, use_llm=True)
    assert plan.structure_confirmed
    assert plan.rows[0].label == "Income from Investments (Income on Investment from funds)"
    assert plan.rows[0].note == "15"


def test_an_entry_for_the_fixed_label_column_is_ignored_not_rejected(fake):
    reply = _decisions()
    reply["columns"].insert(0, {"id": 0, "role": "ignore"})
    f = fake([reply])
    plan = _build(BALANCE_SHEET, use_llm=True)
    assert plan.structure_confirmed and len(f.prompts) == 1


def test_long_tables_are_split_and_reuse_the_first_calls_column_roles(fake, monkeypatch):
    monkeypatch.setattr(structure.Config, "STRUCTURE_ROWS_PER_CALL", 5)
    rows = [("Particulars", "", ["31st March, 2024", "31st March, 2023"])]
    rows += [(f"Item number {i}", "", [f"{i + 1},000", f"{i},000"]) for i in range(1, 9)]
    rows += [("Total", "", ["99,000", "88,000"])]
    n = len(rows)
    first = {"columns": [{"id": 1, "role": "value"}, {"id": 2, "role": "value"}],
             "rows": [{"id": i, "kind": "header" if i == 0 else "item", "join": None} for i in range(5)],
             "moves": []}
    second = {"columns": [],
              "rows": [{"id": i, "kind": "total" if i == n - 1 else "item", "join": None} for i in range(5, n)],
              "moves": []}
    f = fake([first, second])
    plan = _build(rows, use_llm=True)
    assert plan.structure_confirmed and len(f.prompts) == 2
    assert "already decided" in f.prompts[1]
    assert plan.rows[-1].label == "Total" and plan.rows[-1].kind == "total"


def test_years_printed_in_ascending_order_are_noted_not_reordered():
    rows = [("Particulars", "", ["31st March, 2023", "31st March, 2024"]),
            ("Revenue", "", ["100", "200"]), ("Total", "", ["100", "200"])]
    plan = _build(rows)
    assert "ascending" in (structure.year_order_note(plan) or "")
    assert [c.period for c in plan.value_columns] == ["2023", "2024"]      # order untouched


def test_current_year_first_is_silent():
    assert structure.year_order_note(_build(BALANCE_SHEET)) is None


def test_wrap_evidence_reads_the_text_not_the_model():
    we = structure.wrap_evidence
    assert we("Property, Plant and", "Equipment")
    assert we("Income from Investments (Income on Investment from", "earmarked funds)")
    assert we("Increase/(decrease) in stock of finished goods and works - in -", "progess.")
    assert we("Balance being excess of Expenditure over", "Income (B-A)")
    assert we("Total revenue", "and other income")            # second line starts in lower case
    assert not we("Income from Sales/Services", "Grants/Subsidies")
    assert not we("Other Income", "Increase/(decrease) in stock")
    assert not we("Property, Plant and", "(b) Capital work in progress")   # an enumerator opens a new item
    assert not we("", "x") and not we("x", "")


def test_a_join_of_two_complete_line_items_is_ignored_and_both_rows_survive(fake):
    rows = [
        ("Particulars", "Note", ["31st March, 2024", "31st March, 2023"]),
        ("Income from Sales/Services", "12", []),
        ("Grants/Subsidies", "13", ["8,55,000", "6,37,000"]),
        ("Total", "", ["8,55,000", "6,37,000"]),
    ]
    fake([_decisions(n_rows=4, kinds=["header", "item", "item", "total"], joins={1: "next"})])
    plan = _build(rows, use_llm=True)
    assert plan.structure_confirmed
    assert [r.label for r in plan.rows] == ["Income from Sales/Services", "Grants/Subsidies", "Total"]
    assert [r.note for r in plan.rows][:2] == ["12", "13"]
    assert any("complete line item" in n for n in plan.notes)


def test_a_prev_join_onto_a_row_that_does_not_continue_is_ignored(fake):
    rows = [
        ("Particulars", "Note", ["31st March, 2024", "31st March, 2023"]),
        ("Other Income", "18", ["5,59,000", "3,45,013"]),
        ("Increase/(decrease) in stock of goods", "", []),
        ("Total", "", ["5,59,000", "3,45,013"]),
    ]
    fake([_decisions(n_rows=4, kinds=["header", "item", "item", "total"], joins={2: "prev"})])
    plan = _build(rows, use_llm=True)
    assert [r.label for r in plan.rows][:2] == ["Other Income", "Increase/(decrease) in stock of goods"]


NUMBERED = [
    ("Particulars", "Note", ["Figures as at 31st March, 2024", "Figures as at 31st March, 2023"]),
    ("1", "2", ["3", "4"]),
    ("Share capital", "1", ["1,00,000", "90,000"]),
    ("Reserves and surplus", "2", ["2,50,000", "2,00,000"]),
    ("Total", "", ["3,50,000", "2,90,000"]),
]


def test_a_row_of_column_numbers_and_year_headings_do_not_invent_columns():
    plan = _build(NUMBERED)
    assert [c.role for c in plan.columns].count(ROLE_VALUE) == 2
    assert len(plan.columns) == 4                                   # label, note, two figure columns
    assert [r.label for r in plan.rows] == ["Share capital", "Reserves and surplus", "Total"]
    assert [c.header for c in plan.value_columns] == [
        "Figures as at 31st March, 2024", "Figures as at 31st March, 2023",
    ]                                                              # the "3" and "4" stay out of the names


def test_year_headings_alone_never_form_a_column():
    rows = [("Particulars", "", ["2024", "2023"]), ("Revenue", "", ["1,00,000", "90,000"]), ("Total", "", ["1,00,000", "90,000"])]
    plan = _build(rows)
    assert len(plan.value_columns) == 2
    assert [c.header for c in plan.value_columns] == ["2024", "2023"]


def test_gemma_cannot_turn_an_amount_row_into_a_header(fake):
    kinds = ["header", "heading", "header", "item", "total"]           # row 2 (Share capital) has amounts
    fake([_decisions(kinds=kinds)])
    plan = _build(BALANCE_SHEET, use_llm=True)
    assert "Share capital" in [r.label for r in plan.rows]
    assert not any("1,00,000" in c.header for c in plan.columns)
    assert any("carries amounts" in n for n in plan.notes)


def test_a_join_word_in_the_kind_field_keeps_the_rest_of_the_reply(fake):
    reply = _decisions()
    reply["rows"][3]["kind"] = "prev"
    plan = _build(BALANCE_SHEET, use_llm=True) if fake([reply]) else None
    assert plan.structure_confirmed
    assert any("not recognised" in n for n in plan.notes)
    assert plan.rows[-1].label == "Total equity"


def test_an_unknown_column_role_falls_back_to_the_geometry_role(fake):
    reply = _decisions()
    reply["columns"][1]["role"] = "amount"
    fake([reply])
    plan = _build(BALANCE_SHEET, use_llm=True)
    assert plan.structure_confirmed and [c.role for c in plan.columns].count(ROLE_VALUE) == 2


def test_a_row_gemma_calls_blank_keeps_its_text(fake):
    kinds = ["header", "blank", "item", "item", "total"]
    fake([_decisions(kinds=kinds)])
    plan = _build(BALANCE_SHEET, use_llm=True)
    assert "EQUITY AND LIABILITIES" in [r.label for r in plan.rows]


def test_a_header_row_after_the_figures_have_started_keeps_its_text(fake):
    kinds = ["header", "heading", "item", "header", "total"]
    fake([_decisions(kinds=kinds)])
    plan = _build(BALANCE_SHEET, use_llm=True)
    assert "Reserves and surplus" in [r.label for r in plan.rows]


def test_a_column_and_rows_beyond_the_table_are_ignored_not_fatal(fake):
    reply = _decisions()
    reply["columns"].append({"id": 4, "role": "value"})
    reply["rows"] += [{"id": 5, "kind": "item", "join": None}, {"id": 6, "kind": "item", "join": None}]
    fake([reply])
    plan = _build(BALANCE_SHEET, use_llm=True)
    assert plan.structure_confirmed


def test_a_reply_that_ignores_every_column_cannot_remove_the_amount_columns(fake):
    reply = _decisions(col_roles={1: "ignore", 2: "ignore", 3: "ignore"})
    fake([reply])
    plan = _build(BALANCE_SHEET, use_llm=True)
    assert plan.structure_confirmed
    assert [c.role for c in plan.columns].count(ROLE_VALUE) == 2
    assert any("full of amounts" in n for n in plan.notes)


MH_STYLE = [
    ("Particulars", "Note No", ["31st March 2025", "31st March 2024"]),
    ("1.", "", []),
]


def test_serial_numerals_and_alphanumeric_note_refs_do_not_make_columns():
    rows = [
        ("Particulars", "Note No", ["31st March 2025", "31st March 2024"]),
        ("1.  Revenue from Operations", "B-21", ["7,69,561", "7,40,888"]),
        ("II.  Other Income", "B-22", ["2,81,304", "2,60,921"]),
        ("III. Total Income (I +II)", "", ["10,50,866", "10,01,810"]),
        ("(1) Basic (in rupees)", "", ["90.11", "90.89"]),
    ]
    tokens = make_tokens(rows)
    # split the serial numeral into its own token, as OCR does on the scan
    from app.tabletypes import Token
    fixed = []
    for t in tokens:
        head, _, tail = t.text.partition("  ")
        if tail and head in ("1.", "II."):
            fixed.append(Token(t.id, head, (t.x0, t.y0, t.x0 + 30, t.y1), t.page_no, t.conf))
            fixed.append(Token(t.id + "b", tail, (t.x0 + 60, t.y0, t.x1, t.y1), t.page_no, t.conf))
        else:
            fixed.append(t)
    plan = structure.build_plan(fixed, region_for(fixed), 1, None, None, use_llm=False, doc_id=None)
    roles = [c.role for c in plan.columns]
    assert roles.count(ROLE_VALUE) == 2 and roles.count(ROLE_NOTE) == 1
    first = plan.rows[0]
    assert first.note == "B-21" and first.label.startswith("1.")
    assert [t.text for t in first.cells[plan.value_columns[0].index]] == ["7,69,561"]
    assert plan.rows[-1].label == "(1) Basic (in rupees)"
