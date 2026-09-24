"""A cash flow statement with two sub-columns per year (items and their subtotals).

Modelled on a real page: each year has an inner column of line items and an outer
column of subtotals, one heading ("Year ended 31.03.2024") is printed over both,
and OCR sometimes returns two neighbouring figures as a single word.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import export, ocr, reconcile, structure  # noqa: E402
from app.llm_client import LLMResult  # noqa: E402
from app.normalize import looks_like_date, looks_numeric  # noqa: E402
from app.tabletypes import Token  # noqa: E402
from app.validate import FootRow, check_cash_flow  # noqa: E402
from tests._helpers import CHAR_W, ROW_H, TOKEN_H, region_for  # noqa: E402

# right edges: 2024 items, 2024 subtotals, 2023 items, 2023 subtotals
RIGHTS = (1150, 1450, 1750, 2050)


def tokens_for(rows, first_y=300):
    """rows: (label, [figure or '' for each of the four columns])."""
    tokens: list[Token] = []

    def add(text, x0, x1, y):
        tokens.append(Token(f"t1:{len(tokens)}", text, (float(x0), float(y), float(x1), float(y + TOKEN_H)), 1, 0.98))

    # the two headings, each printed ONCE over an items column and its subtotal column
    add("Year ended 31.03.2024", 1010, 1460, 100)
    add("Year ended 31.03.2023", 1610, 2060, 100)
    add("(Amount in '000)", 1010, 1460, 160)
    add("(Amount in '000)", 1610, 2060, 160)
    add("PARTICULARS", 100, 420, 130)
    for r, (label, figures) in enumerate(rows):
        y = first_y + r * ROW_H
        if label:
            add(label, 100, 100 + CHAR_W * len(label), y)
        for text, right in zip(figures, RIGHTS):
            if text:
                add(text, right - CHAR_W * len(text), right, y)
    return tokens


ROWS = [
    ("Amortization of Revenue", ["-1,401", "", "-3,596", ""]),
    ("Finance Cost on Contract Liabilities", ["27,894", "", "25,566", ""]),
    ("Rent Received", ["-1,995", "", "-1,753", ""]),
    ("", ["", "-2,19,410", "", "-53,488"]),
    ("Increase / (Decrease) in Other Long term Liabilities", ["-81,097", "7,00,851", "-4,037", "6,29,781"]),
    ("Net cash generated from Operating Activities (A)", ["", "10,50,415", "", "10,19,494"]),
    ("Net cash generated from Investing activities (B)", ["", "-6,80,746", "", "-8,34,480"]),
    ("Net Cash generated from Financing activities", ["", "-2,66,678", "", "-1,38,375"]),
    ("Net increase/(decrease) in Cash & Cash equivalent", ["", "1,02,990", "", "46,639"]),
    ("Opening balance of Cash & Cash equivalent", ["", "50,365", "", "3,725"]),
    ("Closing balance of Cash & Cash equivalent", ["", "1,53,355", "", "50,365"]),
]


def plan_for(rows=ROWS):
    tokens = tokens_for(rows)
    return structure.build_plan(tokens, region_for(tokens), 1, None, None, use_llm=False, doc_id=None), tokens


# ---- dates are not figures -------------------------------------------------------------

def test_dates_are_not_figure_shaped():
    for text in ("31.03.2024", "31/03/2023", "31-3-24"):
        assert looks_like_date(text) and not looks_numeric(text)
    assert looks_numeric("3,10,253.00") and looks_numeric("-4,037") and looks_numeric("(0.17)")


def test_a_heading_carrying_dates_is_a_heading_and_names_its_columns():
    plan, _ = plan_for()
    assert [r.label for r in plan.rows][0] == "Amortization of Revenue"       # no heading leaked in as data
    assert all(c.header for c in plan.value_columns)
    assert all("2024" in c.header or "2023" in c.header for c in plan.value_columns)


# ---- two neighbouring figures returned as one word --------------------------------------

def test_two_figures_returned_as_one_word_are_split_by_position():
    word = ("-4,037 6,29,781", 0.99, (1600.0, 100.0, 2060.0, 134.0))
    pieces = ocr.split_joined_figures(word)
    assert [p[0] for p in pieces] == ["-4,037", "6,29,781"]
    assert pieces[0][2][2] <= pieces[1][2][0] + 1
    assert pieces[0][2][0] == 1600.0 and abs(pieces[1][2][2] - 2060.0) < 1e-6


def test_a_label_with_spaces_is_not_split():
    word = ("Cash on Hand", 0.99, (100.0, 10.0, 400.0, 44.0))
    assert ocr.split_joined_figures(word) == [word]


def test_grouped_words_keep_the_two_figures_separate():
    tokens = ocr.group_words([("-4,037 6,29,781", 0.99, (1600.0, 100.0, 2060.0, 134.0))])
    assert [t[0] for t in tokens] == ["-4,037", "6,29,781"]


# ---- a heading printed once over two columns --------------------------------------------

def test_one_heading_over_an_items_column_and_its_subtotal_column_names_both():
    plan, _ = plan_for()
    values = plan.value_columns
    assert len(values) == 4
    assert [c.period for c in values] == ["2024", "2024", "2023", "2023"]
    assert values[0].header.endswith("(column 1 of 2)") and values[1].header.endswith("(column 2 of 2)")
    assert values[2].header.endswith("(column 1 of 2)") and values[3].header.endswith("(column 2 of 2)")
    assert structure.header_notes(plan) == []                     # expected, not a swap or a duplicate


def test_every_figure_lands_in_the_column_it_is_printed_under():
    plan, _ = plan_for()
    cols = [c.index for c in plan.value_columns]
    row = next(r for r in plan.rows if r.label.startswith("Increase / (Decrease)"))
    assert [[t.text for t in row.cells[c]] for c in cols] == [["-81,097"], ["7,00,851"], ["-4,037"], ["6,29,781"]]
    net = next(r for r in plan.rows if r.label.startswith("Net cash generated from Operating"))
    assert [[t.text for t in net.cells[c]] for c in cols if c in net.cells] == [["10,50,415"], ["10,19,494"]]


# ---- Gemma cannot move a figure away from where it is printed ----------------------------

class MovingLLM:
    """Replies as the real one did: a move for a figure that sits under a different column."""

    def __init__(self, moves):
        self.moves = moves

    def configured(self):
        return True

    def chat(self, kind, prompt, images=None, **kw):
        n = prompt.count("\nr")
        rows = [{"id": i, "kind": "item", "join": None} for i in range(200) if f"\nr{i}:" in prompt]
        cols = [{"id": i, "role": "value"} for i in range(1, 5)]
        return LLMResult(json.dumps({"columns": cols, "rows": rows, "moves": self.moves}), True)


def test_a_move_that_contradicts_where_the_figure_is_printed_is_ignored(monkeypatch):
    tokens = tokens_for(ROWS)
    row_tok = next(t for t in tokens if t.text == "-1,401")
    number = int(row_tok.id.rsplit(":", 1)[-1])
    monkeypatch.setattr(structure, "CLIENT", MovingLLM([{"token": number, "column": 4}]))
    plan = structure.build_plan(tokens, region_for(tokens), 1, None, None, use_llm=True, doc_id=None)
    assert plan.structure_confirmed
    first = next(r for r in plan.rows if r.label == "Amortization of Revenue")
    columns = [c.index for c in plan.value_columns]
    assert [t.text for t in first.cells[columns[0]]] == ["-1,401"]            # stayed under its own column
    assert any("printed under a different column" in n for n in plan.notes)


# ---- cash flow identities instead of a meaningless footing ---------------------------------

def _foot(**rows):
    return [FootRow(label, "item", {2: value}) for label, value in rows.items()]


def test_the_cash_flow_identities_pass_on_the_real_figures():
    rows = [
        FootRow("Net cash generated from Operating Activities (A)", "item", {2: 1050415.0}),
        FootRow("Net cash generated from Investing activities (B)", "item", {2: -680746.0}),
        FootRow("Net Cash generated from Financing activities", "item", {2: -266678.0}),
        FootRow("Net increase/(decrease) in Cash & Cash equivalent", "item", {2: 102990.0}),
        FootRow("Opening balance of Cash & Cash equivalent", "item", {2: 50365.0}),
        FootRow("Closing balance of Cash & Cash equivalent", "item", {2: 153355.0}),
    ]
    checks = check_cash_flow("t1", 3, rows, [2])
    assert len(checks) == 2 and all(c.passed for c in checks)


def test_a_cash_flow_that_does_not_reconcile_is_reported():
    rows = [
        FootRow("Net increase/(decrease) in Cash & Cash equivalent", "item", {2: 102990.0}),
        FootRow("Opening balance of Cash & Cash equivalent", "item", {2: 50365.0}),
        FootRow("Closing balance of Cash & Cash equivalent", "item", {2: 999999.0}),
    ]
    checks = check_cash_flow("t1", 3, rows, [2])
    assert len(checks) == 1 and not checks[0].passed


def test_an_unread_figure_makes_a_cash_flow_check_unprovable_not_failed():
    rows = [
        FootRow("Net increase/(decrease) in Cash & Cash equivalent", "item", {2: None}),
        FootRow("Opening balance of Cash & Cash equivalent", "item", {2: 50365.0}),
        FootRow("Closing balance of Cash & Cash equivalent", "item", {2: 153355.0}),
    ]
    assert check_cash_flow("t1", 3, rows, [2]) == []


def test_a_cash_flow_statement_no_longer_reports_false_footing_failures():
    plan, _ = plan_for()
    value_cols = [c.index for c in plan.value_columns]
    results = {r.index: reconcile.reconcile_row(r, value_cols, None)[0] for r in plan.rows}
    record, _ = export.build_table_record(
        plan, results, "up_cf", "## CASH FLOW STATEMENT ANNEXED TO BALANCE SHEET\n(Amount in '000)", None, None,
    )
    assert record.financial_stmt_type == "cash_flow"
    assert [f for f in record.footings if not f.passed] == []
    assert any("Closing balance" in f.subtotal_label for f in record.footings)


# ---- a letterhead and statement title inside the table box ------------------------------------

def letterhead_tokens():
    tokens = tokens_for(ROWS)
    extra = []

    def add(text, x0, x1, y):
        extra.append(Token(f"t9:{len(extra)}", text, (float(x0), float(y), float(x1), float(y + TOKEN_H)), 1, 0.98))

    add("IDBI trustee", 100, 380, -240)                       # logo, top left
    add("IDBI TRUSTEESHIP SERVICES LIMITED", 700, 1500, -240)  # company name, centred over the table
    add("Universal Insurance Building, Ground Floor, Fort, Mumbai -", 500, 1600, -180)
    add("400", 1620, 1700, -180)                               # pin code, printed as two figure-shaped words
    add("001", 1720, 1790, -180)
    add("CASH FLOW STATEMENT ANNEXED TO BALANCE SHEET FOR THE YEAR ENDED ON 31.03.2024", 300, 2000, -60)
    for i, tok in enumerate(extra):
        tokens.append(Token(f"t1:{len(tokens)}", tok.text, tok.bbox, 1, 0.98))
    return tokens


def test_a_letterhead_and_title_inside_the_box_do_not_become_rows_or_column_names():
    tokens = letterhead_tokens()
    plan = structure.build_plan(tokens, region_for(tokens), 1, None, None, use_llm=False, doc_id=None)
    labels = [r.label for r in plan.rows]
    assert labels[0] == "Amortization of Revenue", labels[:4]
    assert not any("IDBI" in label or "Universal" in label for label in labels)
    values = plan.value_columns
    assert all("CASH FLOW STATEMENT" not in c.header for c in values)
    assert [c.period for c in values] == ["2024", "2024", "2023", "2023"]
    assert values[0].header.endswith("(column 1 of 2)")
    assert "IDBI" not in plan.columns[0].header and plan.columns[0].header == "PARTICULARS"


def test_an_address_with_a_pin_code_is_not_a_line_item():
    from app.structure import is_data_row

    row = [
        Token("a", "IDBI Trusteeship Services Ltd", (100, 0, 500, 34), 1, 0.9),
        Token("b", "Universal Insurance Building, Ground Floor, Mumbai -", (600, 0, 1500, 34), 1, 0.9),
        Token("c", "400", (1600, 0, 1680, 34), 1, 0.9),
        Token("d", "001", (1700, 0, 1780, 34), 1, 0.9),
    ]
    assert not is_data_row(row)
    item = [Token("a", "Finance Cost", (100, 0, 400, 34), 1, 0.9), Token("b", "460", (1000, 0, 1100, 34), 1, 0.9)]
    assert is_data_row(item)


# ---- wide schedules -------------------------------------------------------------------------

WIDE_RIGHTS = (1000, 1250, 1500, 1750, 2000, 2250, 2500, 2750, 3000, 3250)


def wide_tokens():
    tokens: list[Token] = []

    def add(text, x0, x1, y):
        tokens.append(Token(f"t1:{len(tokens)}", text, (float(x0), float(y), float(x1), float(y + TOKEN_H)), 1, 0.98))

    add("Particulars", 100, 420, 200)
    heads = [
        "Opening balance as on 1 April 2022", "Additions during the year", "Deletions during the year",
        "Closing balance as on 31st March 2023", "Opening balance as on 1 April 2022",
        "Additions during the year", "Deletions during the year", "Closing balance as on 31st March 2023",
        "As on 1st April 2022", "Closing balance as on 31st March 2023",
    ]
    for text, right in zip(heads, WIDE_RIGHTS):
        add(text.split()[0], right - 200, right, 200)          # short first word keeps columns apart
        add(" ".join(text.split()[1:]) or "x", right - 200, right, 240)
    add("Tangibles -", 100, 400, 330)
    for r, (label, vals) in enumerate([
        ("Motor Car", ["1,739", "", "", "1,739", "348", "434", "", "783", "1,391", "957"]),
        ("Freehold Land", ["2,095", "", "", "2,095", "", "", "", "", "2,095", "2,095"]),
        ("Building", ["9,983", "120", "45", "10,058", "6,065", "190", "30", "6,225", "3,918", "3,833"]),
        ("Total", ["55,329", "38,289", "10,857", "82,761", "42,982", "4,345", "10,021", "37,307", "12,346", "45,454"]),
    ]):
        y = 400 + r * ROW_H
        add(label, 100, 100 + CHAR_W * len(label), y)
        for v, right in zip(vals, WIDE_RIGHTS):
            if v:
                add(v, right - CHAR_W * len(v), right, y)
    return tokens


def test_a_wide_schedule_does_not_raise_year_order_warnings():
    tokens = wide_tokens()
    plan = structure.build_plan(tokens, region_for(tokens), 1, None, None, use_llm=False, doc_id=None)
    assert len(plan.value_columns) == 10
    assert structure.header_notes(plan) == []


def test_a_section_title_after_the_column_headings_is_a_row_not_a_heading(monkeypatch):
    tokens = wide_tokens()

    class Llm:
        def configured(self):
            return True

        def chat(self, kind, prompt, images=None, **kw):
            rows = [{"id": i, "kind": "header" if i < 4 else "item", "join": None}
                    for i in range(40) if f"\nr{i}:" in prompt]
            cols = [{"id": i, "role": "value"} for i in range(1, 11)]
            return LLMResult(json.dumps({"columns": cols, "rows": rows, "moves": []}), True)

    monkeypatch.setattr(structure, "CLIENT", Llm())
    plan = structure.build_plan(tokens, region_for(tokens), 1, None, None, use_llm=True, doc_id=None)
    assert plan.columns[0].header == "Particulars"           # not "Tangibles -"
    assert "Tangibles -" in [r.label for r in plan.rows]
