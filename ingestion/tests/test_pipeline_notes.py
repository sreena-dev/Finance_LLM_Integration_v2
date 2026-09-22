"""The two Phase 1 notes: an empty page and a VLM-cap truncation.

Both used to be silent. Extracted into standalone functions specifically so
they can be tested here without needing docling or a reachable vision model --
`pipeline.run()` itself needs both and has no test coverage at all, which is
exactly how the bugs these notes exist to surface went unnoticed as long as
they did.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.convert import Converted, ConvertedTable          # noqa: E402
from app.models import PageQuality                         # noqa: E402
from app.pipeline import _empty_page_note, _vlm_cap_note    # noqa: E402


def _quality(page_no: int) -> PageQuality:
    return PageQuality(page_no=page_no)


# --------------------------------------------------------------------------
# _empty_page_note
# --------------------------------------------------------------------------

def test_a_page_with_a_table_is_not_empty():
    converted = Converted(
        markdown="",
        tables=[ConvertedTable(page_no=1, markdown="| a | b |")],
        page_markdown={},
    )
    assert _empty_page_note([_quality(1)], converted) is None


def test_a_page_with_narrative_text_is_not_empty():
    converted = Converted(
        markdown="",
        page_markdown={1: "A real paragraph of narrative text, well past the threshold."},
    )
    assert _empty_page_note([_quality(1)], converted) is None


def test_a_handful_of_stray_characters_still_counts_as_empty():
    """The threshold exists precisely so noise does not mask a real failure."""
    converted = Converted(markdown="", page_markdown={1: "  \n 1 \n"})
    note = _empty_page_note([_quality(1)], converted)
    assert note is not None
    assert "1" in note


def test_the_real_bug_this_exists_to_catch():
    """OD-SPSU-SO-032_2023-24_SFS, before the Phase 1 OCR fix: 0 tables, 0
    chunks, a clean-looking conversion with no note anywhere to explain it."""
    converted = Converted(markdown="", tables=[], page_markdown={})
    kept = [_quality(p) for p in range(1, 23)]
    note = _empty_page_note(kept, converted)
    assert note is not None
    assert all(str(p) in note for p in range(1, 23))


def test_only_kept_pages_are_named_never_a_blank_or_duplicate_one():
    """A blank/duplicate page is SUPPOSED to contribute nothing -- it was
    already filtered out of `kept_qualities` before this ever runs, and must
    not be reported as a failure alongside a page that genuinely should have
    produced content and did not."""
    converted = Converted(markdown="", tables=[], page_markdown={})
    # Page 2 was blank/duplicate and never reaches kept_qualities at all.
    note = _empty_page_note([_quality(1), _quality(3)], converted)
    assert note is not None
    assert note.startswith("Page(s) 1, 3 ")


def test_a_mix_of_empty_and_populated_pages_names_only_the_empty_ones():
    """The MH-CPSU-ITSL-048_2024-25_AR case: 74 of 78 pages read fine, 4 did
    not. Only the 4 belong in the note."""
    converted = Converted(
        markdown="",
        tables=[ConvertedTable(page_no=2, markdown="| a |")],
        page_markdown={5: "Plenty of real narrative text on this page."},
    )
    kept = [_quality(p) for p in (1, 2, 5, 48, 65, 78)]
    note = _empty_page_note(kept, converted)
    assert note is not None
    assert note.startswith("Page(s) 1, 48, 65, 78 ")


# --------------------------------------------------------------------------
# _vlm_cap_note
# --------------------------------------------------------------------------

def _table_entry(index: int, has_crop: bool = True) -> dict:
    return {"index": index, "crop": object() if has_crop else None}


def test_no_note_when_nothing_exceeds_the_cap():
    prepared = [_table_entry(i) for i in range(1, 5)]
    assert _vlm_cap_note(prepared, vlm_available=True) is None


def test_no_note_when_the_vlm_was_never_available():
    """The cap is meaningless noise on top of the already-explained
    VLM-unavailable note; do not double up on it."""
    from app.config import Config
    prepared = [_table_entry(i) for i in range(1, Config.VLM_MAX_TABLES + 5)]
    assert _vlm_cap_note(prepared, vlm_available=False) is None


def test_tables_past_the_cap_are_counted_and_named():
    from app.config import Config
    n = Config.VLM_MAX_TABLES
    prepared = [_table_entry(i) for i in range(1, n + 6)]  # 5 past the cap
    note = _vlm_cap_note(prepared, vlm_available=True)
    assert note is not None
    assert "5 table(s)" in note
    assert str(n) in note


def test_a_table_with_no_crop_past_the_cap_is_not_double_counted():
    """A table already excluded from the VLM pass for its own reason (no
    bbox/crop, e.g.) should not inflate the cap count -- it was never a
    candidate the cap actually truncated."""
    from app.config import Config
    n = Config.VLM_MAX_TABLES
    prepared = [_table_entry(i, has_crop=(i != n + 1)) for i in range(1, n + 3)]
    note = _vlm_cap_note(prepared, vlm_available=True)
    assert note is not None
    assert "1 table(s)" in note


# --------------------------------------------------------------------------
# _place_confirmed_gap_fills: position alone never places a number
# --------------------------------------------------------------------------

def _gap_fill_entry():
    """One prepared table with a blank cell and a proposal for it."""
    from app import structure_repair as sr
    from app.convert import OcrLine, TableCellGeom
    from app.tables import parse_markdown_tables

    table = parse_markdown_tables(
        "| Particulars | Amount |\n| --- | --- |\n| Revenue |  |\n| Other Income | 20.00 |\n", 1)[0]
    lines = [
        OcrLine("Particulars", 0.99, (10.0, 0.0, 190.0, 18.0)),
        OcrLine("Amount", 0.99, (260.0, 0.0, 340.0, 18.0)),
        OcrLine("Revenue", 0.99, (10.0, 20.0, 190.0, 38.0)),
        OcrLine("100.00", 0.99, (260.0, 20.0, 340.0, 38.0)),
        OcrLine("Other Income", 0.99, (10.0, 40.0, 190.0, 58.0)),
        OcrLine("20.00", 0.99, (260.0, 40.0, 340.0, 58.0)),
    ]
    cells = [
        TableCellGeom("Particulars", 0, 1, 0, 1, (10.0, 0.0, 190.0, 18.0)),
        TableCellGeom("Amount", 0, 1, 1, 2, (260.0, 0.0, 340.0, 18.0)),
    ]
    ct = ConvertedTable(page_no=1, markdown=table.to_markdown(), cells=cells,
                        ocr_lines=lines, page_height_pt=842.0)
    ledger = sr.build_number_ledger(lines, 18.0)
    col_bands = sr._column_ranges(cells, 2)
    binding = sr.bind_table(table, ledger, col_bands, lines, 18.0)
    proposals = sr.propose_gap_fills(table, binding)
    assert proposals, "fixture must produce a proposal"
    return {"table": table, "converted_table": ct, "binding": binding, "gap_fills": proposals}


def test_no_gap_fill_is_placed_when_no_second_reader_is_reachable(monkeypatch):
    """THE property: with the vision model unavailable, nothing is placed --
    there is no fallback to position alone."""
    from app import pipeline, vlm_read
    entry = _gap_fill_entry()
    called = []
    monkeypatch.setattr(vlm_read, "confirm_gap_fill", lambda *a, **k: called.append(1) or True)
    notes: list[str] = []

    pipeline._place_confirmed_gap_fills([entry], False, {}, notes)

    assert entry["table"].cell(0, 1).value is None
    assert called == []                                  # never even asked
    assert any("requires a second reader" in n for n in notes)


def test_a_confirmed_gap_fill_is_placed_and_the_table_is_rebound(monkeypatch):
    from app import pipeline, vlm_read
    entry = _gap_fill_entry()
    monkeypatch.setattr(vlm_read, "confirm_gap_fill", lambda *a, **k: True)

    pipeline._place_confirmed_gap_fills([entry], True, {}, [])

    assert entry["table"].cell(0, 1).value == 100.0
    # Re-bound: the filled cell now backs a token, so it is no longer leftover.
    assert (0, 1) in entry["binding"].bound
    assert all(t.value != 100.0 for t in entry["binding"].leftover)


def test_an_unconfirmed_gap_fill_is_never_placed(monkeypatch):
    from app import pipeline, vlm_read
    entry = _gap_fill_entry()
    monkeypatch.setattr(vlm_read, "confirm_gap_fill", lambda *a, **k: False)
    pipeline._place_confirmed_gap_fills([entry], True, {}, [])
    assert entry["table"].cell(0, 1).value is None


def test_a_crashing_confirmation_places_nothing_and_does_not_take_the_document_down(monkeypatch):
    from app import pipeline, vlm_read

    def boom(*a, **k):
        raise RuntimeError("endpoint fell over")

    entry = _gap_fill_entry()
    monkeypatch.setattr(vlm_read, "confirm_gap_fill", boom)
    pipeline._place_confirmed_gap_fills([entry], True, {}, [])
    assert entry["table"].cell(0, 1).value is None


def test_confirmations_past_the_cap_are_not_placed_and_are_named(monkeypatch):
    from app import pipeline, vlm_read
    from app.config import Config
    monkeypatch.setattr(Config, "VLM_MAX_GAP_FILL_CONFIRMS", 1)
    first, second = _gap_fill_entry(), _gap_fill_entry()
    monkeypatch.setattr(vlm_read, "confirm_gap_fill", lambda *a, **k: True)
    notes: list[str] = []

    pipeline._place_confirmed_gap_fills([first, second], True, {}, notes)

    assert first["table"].cell(0, 1).value == 100.0
    assert second["table"].cell(0, 1).value is None
    assert any("BUDGET" in n and "INGEST_VLM_MAX_GAP_FILL_CONFIRMS" in n for n in notes)
