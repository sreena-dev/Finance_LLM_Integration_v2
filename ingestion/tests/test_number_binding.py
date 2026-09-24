"""The number ledger and binder: "the VLM proposes, OCR disposes".

Every figure a candidate table structure claims must bind to an actual OCR
numeric token at a consistent position, or it does not become a plain
number. This is what makes "don't hallucinate a number" a mechanical
property rather than a prompting instruction (see `structure_repair.py`'s
module docstring for the full architecture, and `vlm_read.select_structure`
for how a `Binding`'s coverage score decides between two whole candidate
structures).

Every test here is either an ACCEPTANCE path (a clean, well-evidenced case
works) or a REFUSAL path (one specific way the system could accept, place or
score something it should not). The refusal tests are the safety-critical
ones -- this machinery is trusted with something no earlier VLM check in
this pipeline was trusted with: turning a raw OCR reading into a plain,
unmarked figure with no arithmetic proof at all.

Run with::

    cd ingestion
    venv/Scripts/python -m pytest tests/test_number_binding.py -q
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.convert import OcrLine, TableCellGeom             # noqa: E402
from app.tables import parse_markdown_tables               # noqa: E402
from app import structure_repair as sr                     # noqa: E402


def _line(text, x0, y0, x1, y1, confidence=0.99):
    return OcrLine(text=text, confidence=confidence, bbox=(x0, y0, x1, y1))


def _cell(text, row, col, x0, y0, x1, y1):
    return TableCellGeom(text=text, row_start=row, row_end=row + 1,
                         col_start=col, col_end=col + 1, bbox=(x0, y0, x1, y1))


def _table(markdown: str, page_no: int = 1):
    parsed = parse_markdown_tables(markdown, page_no, prefix="x")
    assert parsed, "fixture markdown did not parse into a table"
    return parsed[0]


# A clean, 3-row, 2-column fixture: label column x=0-200, value column
# x=250-350 -- each figure its own OCR detection region at the same Y as its
# label, the dominant real-corpus shape (see test_structure_repair.py's
# OD_TRADE_PAYABLES_REGION_LINES).
_CLEAN_MD = (
    "| Particulars | Amount |\n"
    "| --- | --- |\n"
    "| Revenue | 100.00 |\n"
    "| Other Income | 20.00 |\n"
    "| Total Income | 120.00 |\n"
)

_CLEAN_COLS = [
    _cell("Particulars", 0, 0, 0.0, 0.0, 200.0, 18.0),
    _cell("Amount", 0, 1, 250.0, 0.0, 350.0, 18.0),
]

_CLEAN_LINES = [
    _line("Particulars", 10.0, 0.0, 190.0, 18.0), _line("Amount", 260.0, 0.0, 340.0, 18.0),
    _line("Revenue", 10.0, 20.0, 190.0, 38.0), _line("100.00", 260.0, 20.0, 340.0, 38.0),
    _line("Other Income", 10.0, 40.0, 190.0, 58.0), _line("20.00", 260.0, 40.0, 340.0, 58.0),
    _line("Total Income", 10.0, 60.0, 190.0, 78.0), _line("120.00", 260.0, 60.0, 340.0, 78.0),
]

_CLEAN_HEADER_BOTTOM = 18.0


def _clean_ledger_and_bands():
    ledger = sr.build_number_ledger(_CLEAN_LINES, _CLEAN_HEADER_BOTTOM)
    col_bands = sr._column_ranges(_CLEAN_COLS, header_count=2)
    return ledger, col_bands


# ---------------------------------------------------------------------------
# build_number_ledger
# ---------------------------------------------------------------------------

def test_a_lone_numeric_token_takes_its_own_lines_bbox_unmodified():
    ledger, _ = _clean_ledger_and_bands()
    revenue_tok = next(t for t in ledger if t.text == "100.00")
    assert revenue_tok.interpolated is False
    assert revenue_tok.bbox == (260.0, 20.0, 340.0, 38.0)


def test_two_numbers_sharing_one_line_are_interpolated_and_flagged():
    line = _line("18,600.00 34,011.00", 500.0, 20.0, 900.0, 38.0)
    ledger = sr.build_number_ledger([line], header_bottom=None)
    assert len(ledger) == 2
    assert all(t.interpolated for t in ledger)
    # The first token's span sits left of the second's.
    first, second = sorted(ledger, key=lambda t: t.bbox[0])
    assert first.value == 18600.0
    assert second.value == 34011.0
    assert first.bbox[2] <= second.bbox[0] + 1e-6


def test_header_region_lines_never_enter_the_ledger():
    """A caption like "Figures as at 31st March, 2024" sitting above the
    header must never contribute phantom `2024`/`31` tokens -- these would
    otherwise show up as unaccounted-for figures in the half-read report or
    as spurious gap-fill candidates."""
    caption = _line("Figures as at 31st March, 2024", 10.0, -20.0, 300.0, -2.0)
    ledger = sr.build_number_ledger([caption] + _CLEAN_LINES, _CLEAN_HEADER_BOTTOM)
    assert all(t.value != 2024.0 for t in ledger)
    assert all(t.value != 31.0 for t in ledger)


# ---------------------------------------------------------------------------
# assign_columns
# ---------------------------------------------------------------------------

def test_a_token_squarely_inside_one_band_is_assigned_to_it():
    ledger, col_bands = _clean_ledger_and_bands()
    sr.assign_columns(ledger, col_bands)
    revenue_tok = next(t for t in ledger if t.text == "100.00")
    assert revenue_tok.column == 1


def test_an_interpolated_token_straddling_two_bands_is_refused():
    """A token whose FULL interpolated span crosses out of the band its
    centre falls into must not be assigned -- its position is too
    uncertain to trust even for binding."""
    # A single OCR line spanning both the label and value bands, with two
    # numeric-looking tokens whose interpolated positions land right at the
    # band boundary.
    wide_line = _line("1 999999999999999999.00", 0.0, 20.0, 350.0, 38.0)
    ledger = sr.build_number_ledger([wide_line], header_bottom=None)
    col_bands = {0: (0.0, 200.0), 1: (250.0, 350.0)}
    sr.assign_columns(ledger, col_bands, margin=6.0)
    # The long second token's interpolated span starts well before x=250
    # (it is most of a very long line) so it cannot fit inside band 1 -- it
    # must be refused (column stays None), not smeared across two bands.
    long_tok = next(t for t in ledger if "999999999999999999" in t.text)
    assert long_tok.interpolated is True
    assert long_tok.column is None


def test_a_token_equidistant_between_two_bands_is_refused():
    ledger = [sr.NumberToken(value=1.0, text="1", bbox=(224.0, 0.0, 226.0, 18.0),
                              confidence=0.99, interpolated=False)]
    col_bands = {0: (0.0, 200.0), 1: (250.0, 450.0)}
    sr.assign_columns(ledger, col_bands, margin=30.0)
    assert ledger[0].column is None


# ---------------------------------------------------------------------------
# bind_table -- acceptance
# ---------------------------------------------------------------------------

def test_every_value_cell_binds_with_no_leftovers_on_a_clean_table():
    table = _table(_CLEAN_MD)
    ledger, col_bands = _clean_ledger_and_bands()
    binding = sr.bind_table(table, ledger, col_bands, _CLEAN_LINES, _CLEAN_HEADER_BOTTOM)
    assert binding.refused is None
    assert binding.unbound == set()
    assert len(binding.bound) == 3
    assert binding.leftover == []


def test_a_printed_dash_is_neither_bound_nor_unbound():
    md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Revenue | 100.00 |\n"
        "| Finance Costs | - |\n"
    )
    lines = [
        _line("Particulars", 10.0, 0.0, 190.0, 18.0), _line("Amount", 260.0, 0.0, 340.0, 18.0),
        _line("Revenue", 10.0, 20.0, 190.0, 38.0), _line("100.00", 260.0, 20.0, 340.0, 38.0),
        _line("Finance Costs", 10.0, 40.0, 190.0, 58.0), _line("-", 260.0, 40.0, 340.0, 58.0),
    ]
    table = _table(md)
    ledger = sr.build_number_ledger(lines, 18.0)
    col_bands = sr._column_ranges(_CLEAN_COLS, header_count=2)
    binding = sr.bind_table(table, ledger, col_bands, lines, 18.0)
    assert (1, 1) not in binding.unbound
    assert (1, 1) not in binding.bound


# ---------------------------------------------------------------------------
# bind_table -- refusals
# ---------------------------------------------------------------------------

def test_a_figure_no_token_supports_is_unbound():
    md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Revenue | 999.00 |\n"  # nothing on the page says 999.00
    )
    lines = [
        _line("Particulars", 10.0, 0.0, 190.0, 18.0), _line("Amount", 260.0, 0.0, 340.0, 18.0),
        _line("Revenue", 10.0, 20.0, 190.0, 38.0), _line("100.00", 260.0, 20.0, 340.0, 38.0),
    ]
    table = _table(md)
    ledger = sr.build_number_ledger(lines, 18.0)
    col_bands = sr._column_ranges(_CLEAN_COLS, header_count=2)
    binding = sr.bind_table(table, ledger, col_bands, lines, 18.0)
    assert (0, 1) in binding.unbound
    assert (0, 1) not in binding.bound


def test_consume_once_one_token_cannot_back_two_cells():
    """Two rows both claiming the SAME figure (120.00) with only ONE token
    of that value on the page -- only one of the two cells may bind, the
    other must be unbound, never both silently accepted from one token."""
    md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Total Income | 120.00 |\n"
        "| Total Cost | 120.00 |\n"
    )
    lines = [
        _line("Particulars", 10.0, 0.0, 190.0, 18.0), _line("Amount", 260.0, 0.0, 340.0, 18.0),
        _line("Total Income", 10.0, 20.0, 190.0, 38.0), _line("120.00", 260.0, 20.0, 340.0, 38.0),
        _line("Total Cost", 10.0, 40.0, 190.0, 58.0),
        # No second "120.00" printed anywhere for the second row.
    ]
    table = _table(md)
    ledger = sr.build_number_ledger(lines, 18.0)
    col_bands = sr._column_ranges(_CLEAN_COLS, header_count=2)
    binding = sr.bind_table(table, ledger, col_bands, lines, 18.0)
    assert len(binding.bound) == 1
    assert (1, 1) in binding.unbound


def test_a_row_whose_label_matches_nothing_is_unanchored_and_its_cells_unbound():
    md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Revenue | 100.00 |\n"
        "| Something Printed Nowhere On This Page | 50.00 |\n"
    )
    table = _table(md)
    ledger, col_bands = _clean_ledger_and_bands()
    binding = sr.bind_table(table, ledger, col_bands, _CLEAN_LINES, _CLEAN_HEADER_BOTTOM)
    assert 1 in binding.unanchored_rows
    assert (1, 1) in binding.unbound


def test_too_few_column_bands_refuses_the_whole_binding():
    """One band, but the table has two value columns -- there is nothing to
    map the second onto. A refused binding must carry no bound/unbound
    verdict at all; callers must treat it as no measurement."""
    md = (
        "| Particulars | 2024 | 2023 |\n"
        "| --- | --- | --- |\n"
        "| Revenue | 100.00 | 90.00 |\n"
    )
    table = _table(md)
    col_bands = {0: (250.0, 350.0)}  # only one band -- fewer than value_cols
    binding = sr.bind_table(table, [], col_bands, [], None)
    assert binding.refused is not None
    assert binding.bound == {}
    assert binding.unbound == set()


# ---------------------------------------------------------------------------
# coverage_score
# ---------------------------------------------------------------------------

def test_coverage_rewards_a_split_that_binds_more_figures_than_a_merged_row():
    """The real defect this design targets: TableFormer merges two distinct
    printed lines into one row, so that row's single cell can bind AT MOST
    one of the two printed figures. Coverage counts the other as a
    leftover against it -- a split candidate that binds both scores
    higher, which a raw footing count would not reliably reward."""
    merged_md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Interest Income Finance Costs | 10.00 |\n"
    )
    split_md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Interest Income | 10.00 |\n"
        "| Finance Costs | 30.00 |\n"
    )
    lines = [
        _line("Particulars", 10.0, 0.0, 190.0, 18.0), _line("Amount", 260.0, 0.0, 340.0, 18.0),
        _line("Interest Income", 10.0, 20.0, 190.0, 38.0), _line("10.00", 260.0, 20.0, 340.0, 38.0),
        _line("Finance Costs", 10.0, 40.0, 190.0, 58.0), _line("30.00", 260.0, 40.0, 340.0, 58.0),
    ]
    col_bands = sr._column_ranges(_CLEAN_COLS, header_count=2)
    ledger = sr.build_number_ledger(lines, 18.0)

    merged_binding = sr.bind_table(_table(merged_md), ledger, col_bands, lines, 18.0)
    split_binding = sr.bind_table(_table(split_md), ledger, col_bands, lines, 18.0)

    assert sr.coverage_score(split_binding) > sr.coverage_score(merged_binding)


# ---------------------------------------------------------------------------
# propose_gap_fills / apply_gap_fills: position alone never places a number
# ---------------------------------------------------------------------------

def test_a_leftover_figure_is_placed_into_the_one_empty_cell_it_matches():
    md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Revenue | |\n"  # structure omitted the figure docling never placed
        "| Other Income | 20.00 |\n"
    )
    lines = [
        _line("Particulars", 10.0, 0.0, 190.0, 18.0), _line("Amount", 260.0, 0.0, 340.0, 18.0),
        _line("Revenue", 10.0, 20.0, 190.0, 38.0), _line("100.00", 260.0, 20.0, 340.0, 38.0),
        _line("Other Income", 10.0, 40.0, 190.0, 58.0), _line("20.00", 260.0, 40.0, 340.0, 58.0),
    ]
    table = _table(md)
    ledger = sr.build_number_ledger(lines, 18.0)
    col_bands = sr._column_ranges(_CLEAN_COLS, header_count=2)
    binding = sr.bind_table(table, ledger, col_bands, lines, 18.0)
    proposals = sr.propose_gap_fills(table, binding)
    token = next(t for t in ledger if t.text == "100.00")
    assert proposals == [(0, 1, token)]
    # A proposal is only a QUESTION for a second reader: nothing is written.
    assert table.cell(0, 1).value is None
    # Only once confirmed does apply_gap_fills place it.
    assert sr.apply_gap_fills(table, proposals) == [(0, 1, token)]
    assert table.cell(0, 1).value == 100.00


def test_gap_fill_never_overwrites_a_populated_cell():
    table = _table(_CLEAN_MD)
    before = table.to_markdown()
    ledger, col_bands = _clean_ledger_and_bands()
    binding = sr.bind_table(table, ledger, col_bands, _CLEAN_LINES, _CLEAN_HEADER_BOTTOM)
    # Manufacture a leftover pointing at an already-populated cell.
    fake_leftover = sr.NumberToken(
        value=999.0, text="999.00", bbox=(260.0, 20.0, 340.0, 38.0),
        confidence=0.99, interpolated=False, column=1,
    )
    binding.leftover.append(fake_leftover)
    placed = sr.propose_gap_fills(table, binding)
    assert placed == []
    assert table.to_markdown() == before


def test_gap_fill_never_overwrites_a_printed_dash():
    md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Finance Costs | - |\n"
    )
    table = _table(md)
    before = table.to_markdown()
    fake_leftover = sr.NumberToken(
        value=999.0, text="999.00", bbox=(260.0, 20.0, 340.0, 38.0),
        confidence=0.99, interpolated=False, column=1,
    )
    binding = sr.Binding(row_bands={0: (10.0, 50.0)}, col_map={1: 1}, leftover=[fake_leftover])
    placed = sr.propose_gap_fills(table, binding)
    assert placed == []
    assert table.to_markdown() == before


def test_gap_fill_refuses_an_interpolated_token():
    """A gap fill has no second reader at all -- an approximate,
    interpolated position must never be the sole basis for a plain
    number."""
    md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Revenue | |\n"
    )
    table = _table(md)
    fake_leftover = sr.NumberToken(
        value=100.0, text="100.00", bbox=(260.0, 20.0, 340.0, 38.0),
        confidence=0.99, interpolated=True, column=1,
    )
    binding = sr.Binding(row_bands={0: (10.0, 50.0)}, col_map={1: 1}, leftover=[fake_leftover])
    placed = sr.propose_gap_fills(table, binding)
    assert placed == []
    assert table.cell(0, 1).value is None


def test_gap_fill_refuses_a_low_confidence_token():
    md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Revenue | |\n"
    )
    table = _table(md)
    fake_leftover = sr.NumberToken(
        value=100.0, text="100.00", bbox=(260.0, 20.0, 340.0, 38.0),
        confidence=0.40, interpolated=False, column=1,
    )
    binding = sr.Binding(row_bands={0: (10.0, 50.0)}, col_map={1: 1}, leftover=[fake_leftover])
    placed = sr.propose_gap_fills(table, binding)
    assert placed == []


def test_two_leftovers_targeting_the_same_cell_are_placed_in_neither():
    md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Revenue | |\n"
    )
    table = _table(md)
    a = sr.NumberToken(value=100.0, text="100.00", bbox=(260.0, 20.0, 340.0, 38.0),
                        confidence=0.99, interpolated=False, column=1)
    b = sr.NumberToken(value=200.0, text="200.00", bbox=(260.0, 22.0, 340.0, 40.0),
                        confidence=0.99, interpolated=False, column=1)
    binding = sr.Binding(row_bands={0: (10.0, 50.0)}, col_map={1: 1}, leftover=[a, b])
    placed = sr.propose_gap_fills(table, binding)
    assert placed == []
    assert table.cell(0, 1).value is None


def test_a_proposed_gap_fill_is_never_placed_without_confirmation():
    """The user-chosen safety property: propose, never apply, and the cell
    stays empty. Nothing in the propose path writes to the table."""
    table = _table("| Particulars | Amount |\n| --- | --- |\n| Revenue | |\n")
    tok = sr.NumberToken(value=100.0, text="100.00", bbox=(260.0, 20.0, 340.0, 38.0),
                         confidence=0.99, interpolated=False, column=1)
    binding = sr.Binding(row_bands={0: (10.0, 50.0)}, col_map={1: 1}, leftover=[tok])
    before = table.to_markdown()
    assert sr.propose_gap_fills(table, binding) == [(0, 1, tok)]
    assert table.to_markdown() == before


def test_apply_refuses_a_stale_proposal_whose_target_is_no_longer_empty():
    """A proposal is made, confirmed over the network, and only then applied
    -- something else may have placed a figure in that cell in between."""
    table = _table("| Particulars | Amount |\n| --- | --- |\n| Revenue | |\n")
    tok = sr.NumberToken(value=100.0, text="100.00", bbox=(260.0, 20.0, 340.0, 38.0),
                         confidence=0.99, interpolated=False, column=1)
    binding = sr.Binding(row_bands={0: (10.0, 50.0)}, col_map={1: 1}, leftover=[tok])
    proposals = sr.propose_gap_fills(table, binding)
    table.rows[0][1] = "555.00"                      # placed by someone else meanwhile
    assert sr.apply_gap_fills(table, proposals) == []
    assert table.cell(0, 1).value == 555.00


def test_apply_refuses_a_target_that_became_a_marker():
    table = _table("| Particulars | Amount |\n| --- | --- |\n| Revenue | |\n")
    tok = sr.NumberToken(value=100.0, text="100.00", bbox=(260.0, 20.0, 340.0, 38.0),
                         confidence=0.99, interpolated=False, column=1)
    binding = sr.Binding(row_bands={0: (10.0, 50.0)}, col_map={1: 1}, leftover=[tok])
    proposals = sr.propose_gap_fills(table, binding)
    table.rows[0][1] = '[unreadable: page 1, table t1, row "Revenue", col "Amount"]'
    assert sr.apply_gap_fills(table, proposals) == []


# ---------------------------------------------------------------------------
# confirm_gap_fill: the second reader. Refusal is the default at every step.
# ---------------------------------------------------------------------------

def _confirm(monkeypatch, read_value, token_value=100.0):
    from app import vlm_read
    monkeypatch.setattr(vlm_read, "_reread_cell_value", lambda *a, **k: read_value)
    table = _table("| Particulars | Amount |\n| --- | --- |\n| Revenue | |\n")
    tok = sr.NumberToken(value=token_value, text="100.00", bbox=(260.0, 20.0, 340.0, 38.0),
                         confidence=0.99, interpolated=False, column=1)
    return vlm_read.confirm_gap_fill(table, 0, 1, tok, None, None)


def test_a_gap_fill_is_confirmed_only_when_the_second_read_agrees(monkeypatch):
    assert _confirm(monkeypatch, 100.0) is True


def test_a_second_read_of_a_different_figure_confirms_nothing(monkeypatch):
    assert _confirm(monkeypatch, 101.0) is False


def test_an_unreachable_or_illegible_second_read_confirms_nothing(monkeypatch):
    """`_reread_cell_value` returns None on every refusal -- no image, no
    band, no reply, ILLEGIBLE, an unreachable model. There is no fallback to
    position alone."""
    assert _confirm(monkeypatch, None) is False


def test_the_confirmation_tolerance_matches_the_rest_of_the_pipeline(monkeypatch):
    assert _confirm(monkeypatch, 100.005) is True       # within max(0.01, ...)
    assert _confirm(monkeypatch, 100.02) is False


# ---------------------------------------------------------------------------
# build_number_ledger: footnote-marker vs. real-figure disambiguation
# ---------------------------------------------------------------------------

def test_a_short_small_line_next_to_normal_height_lines_is_dropped_as_a_marker():
    lines = [
        _line("Revenue", 0.0, 100.0, 200.0, 118.0),       # height 18 (normal)
        _line("100.00", 250.0, 100.0, 350.0, 118.0),       # height 18 (normal)
        # A lone "1" detected as its OWN small OCR region, well under the
        # region's median line height -- the footnote-marker signature.
        _line("1", 355.0, 102.0, 362.0, 110.0),            # height 8
    ]
    ledger = sr.build_number_ledger(lines)
    values = sorted(t.value for t in ledger)
    assert values == [100.0], "the marker's '1' must not appear as a figure"


def test_a_normal_height_short_number_is_kept_as_a_real_figure():
    # Same shape, but the short line is the SAME height as everything else
    # -- a real one- or two-digit figure (e.g. a Note column), not a marker.
    lines = [
        _line("Revenue", 0.0, 100.0, 200.0, 118.0),
        _line("4", 210.0, 100.0, 220.0, 118.0),            # height 18, normal
        _line("100.00", 250.0, 100.0, 350.0, 118.0),
    ]
    ledger = sr.build_number_ledger(lines)
    values = sorted(t.value for t in ledger)
    assert values == [4.0, 100.0]


def test_a_multidigit_short_line_is_never_treated_as_a_marker():
    # The marker filter is deliberately narrow: 1-2 digits only. A small but
    # multi-digit figure (a real, if oddly-scaled, OCR box) is left alone.
    lines = [
        _line("Revenue", 0.0, 100.0, 200.0, 118.0),
        _line("100.00", 250.0, 100.0, 350.0, 118.0),
        _line("123", 355.0, 102.0, 375.0, 110.0),          # 3 digits, short box
    ]
    ledger = sr.build_number_ledger(lines)
    values = sorted(t.value for t in ledger)
    assert values == [100.0, 123.0]


def test_a_footnote_marker_sharing_its_line_with_a_real_figure_is_not_caught():
    # Documented limitation: no font-size data exists for a marker MERGED
    # into the same OCR line as its neighbouring figure -- this is
    # deliberately left untouched rather than guessed at (see
    # `_looks_like_footnote_marker`'s own comment). Confirms this stays
    # honestly unhandled, not silently "working" by accident.
    lines = [_line("Revenue 100.00 1", 0.0, 100.0, 400.0, 118.0)]
    ledger = sr.build_number_ledger(lines)
    values = sorted(t.value for t in ledger)
    assert values == [1.0, 100.0]


def test_looks_like_footnote_marker_directly():
    short = OcrLine(text="1", confidence=0.99, bbox=(0.0, 0.0, 10.0, 8.0))
    normal = OcrLine(text="1", confidence=0.99, bbox=(0.0, 0.0, 10.0, 18.0))
    two_digit_short = OcrLine(text="12", confidence=0.99, bbox=(0.0, 0.0, 10.0, 8.0))
    three_digit_short = OcrLine(text="123", confidence=0.99, bbox=(0.0, 0.0, 10.0, 8.0))

    assert sr._looks_like_footnote_marker(short, median_line_height=18.0) is True
    assert sr._looks_like_footnote_marker(normal, median_line_height=18.0) is False
    assert sr._looks_like_footnote_marker(two_digit_short, median_line_height=18.0) is True
    assert sr._looks_like_footnote_marker(three_digit_short, median_line_height=18.0) is False
    assert sr._looks_like_footnote_marker(short, median_line_height=0.0) is False
