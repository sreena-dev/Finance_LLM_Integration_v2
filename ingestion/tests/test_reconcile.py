"""Cell status: two independent reads compared, nothing overwritten."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.reconcile import reconcile_row  # noqa: E402
from app.second_read import RowRead, parse_reply  # noqa: E402
from app.tabletypes import (  # noqa: E402
    STATUS_EMPTY, STATUS_FLAGGED, STATUS_HANDWRITTEN, STATUS_OCR_ONLY, STATUS_STRUCK,
    STATUS_UNREADABLE, STATUS_VERIFIED, Row, Token,
)


def tok(text, conf=0.98, i=[0]):
    i[0] += 1
    return Token(f"t1:{i[0]}", text, (0, 0, 10, 10), 1, conf)


def row(*cells, note="", label="Share capital"):
    r = Row(0, "item", 0, 10, label_tokens=[tok(label)])
    if note:
        r.note_tokens = [tok(note)]
    for col, text in enumerate(cells, start=2):
        if text is not None:
            r.cells[col] = [tok(text[0], text[1])] if isinstance(text, tuple) else [tok(text)]
    return r


COLS = [2, 3]


def test_agreeing_reads_are_verified():
    results, _ = reconcile_row(row("1,00,000", "90,000"), COLS, RowRead(["1,00,000", "90,000"]))
    assert [results[c].status for c in COLS] == [STATUS_VERIFIED, STATUS_VERIFIED]


def test_grouping_style_does_not_break_agreement():
    results, _ = reconcile_row(row("1,00,000", "90,000"), COLS, RowRead(["100,000", "90,000"]))
    assert results[2].status == STATUS_VERIFIED


def test_disagreement_is_flagged_and_keeps_both_readings_verbatim():
    results, _ = reconcile_row(row("1,00,000", "90,000"), COLS, RowRead(["1,00,800", "90,000"]))
    cell = results[2]
    assert cell.status == STATUS_FLAGGED and cell.ocr_text == "1,00,000" and cell.second_text == "1,00,800"
    assert "readers_disagree" in cell.reasons
    assert results[3].status == STATUS_VERIFIED


def test_a_sign_difference_is_a_disagreement():
    results, _ = reconcile_row(row("(1,00,000)", "90,000"), COLS, RowRead(["1,00,000", "90,000"]))
    assert results[2].status == STATUS_FLAGGED


def test_a_garbled_ocr_cell_with_a_clean_second_read_is_flagged_not_repaired():
    results, _ = reconcile_row(row("1,2O0", "90,000"), COLS, RowRead(["1,200", "90,000"]))
    cell = results[2]
    assert cell.status == STATUS_FLAGGED and cell.ocr_text == "1,2O0" and cell.second_text == "1,200"


def test_no_second_read_falls_back_to_ocr_only_when_clean_and_confident():
    results, _ = reconcile_row(row("1,00,000", "90,000"), COLS, None)
    assert results[2].status == STATUS_OCR_ONLY


def test_no_second_read_and_low_confidence_is_unreadable():
    results, _ = reconcile_row(row(("1,00,000", 0.31), "90,000"), COLS, None)
    assert results[2].status == STATUS_UNREADABLE
    assert any(r.startswith("low_ocr_confidence") for r in results[2].reasons)


def test_no_second_read_and_malformed_ocr_is_unreadable():
    results, _ = reconcile_row(row("1,2O0", "90,000"), COLS, None)
    assert results[2].status == STATUS_UNREADABLE and "unreadable_text" in results[2].reasons


def test_two_readers_agreeing_outweighs_one_low_ocr_confidence():
    results, _ = reconcile_row(row(("1,00,000", 0.31), "90,000"), COLS, RowRead(["1,00,000", "90,000"]))
    assert results[2].status == STATUS_VERIFIED


def test_a_question_mark_from_the_second_reader_leaves_ocr_standing():
    results, _ = reconcile_row(row("1,00,000", "90,000"), COLS, RowRead(["?", "90,000"]))
    assert results[2].status == STATUS_OCR_ONLY and results[3].status == STATUS_VERIFIED


def test_a_count_mismatch_flags_every_figure_on_the_row_and_pairs_nothing_by_guess():
    results, notes = reconcile_row(row("1,00,000", "90,000"), COLS, RowRead(["1,00,000"]))
    assert all(results[c].status == STATUS_FLAGGED for c in COLS)
    assert "row_count_mismatch" in results[2].reasons and results[2].second_text is None
    assert notes


def test_a_note_number_read_as_an_amount_is_dropped_when_it_explains_the_extra():
    results, _ = reconcile_row(row("1,00,000", "90,000", note="3"), COLS, RowRead(["3", "1,00,000", "90,000"]))
    assert [results[c].status for c in COLS] == [STATUS_VERIFIED, STATUS_VERIFIED]


def test_handwriting_and_strike_through_are_flagged_on_the_row():
    hw, _ = reconcile_row(row("1,00,000", "90,000"), COLS, RowRead(["1,00,000", "90,000"], handwritten=True))
    st, _ = reconcile_row(row("1,00,000", "90,000"), COLS, RowRead(["1,00,000", "90,000"], struck_through=True))
    assert all(hw[c].status == STATUS_HANDWRITTEN for c in COLS)
    assert all(st[c].status == STATUS_STRUCK for c in COLS)


def test_empty_cells_stay_empty_and_a_stray_second_read_amount_is_only_noted():
    results, notes = reconcile_row(row(None, None), COLS, RowRead(["5,000"]))
    assert all(results[c].status == STATUS_EMPTY for c in COLS) and notes


def test_a_dash_is_a_real_figure_that_agrees_with_a_dash():
    results, _ = reconcile_row(row("-", "90,000"), COLS, RowRead(["-", "90,000"]))
    assert results[2].status == STATUS_VERIFIED


def test_second_read_reply_parsing_is_strict():
    ok = parse_reply('{"amounts": ["1,00,000", "(95)"], "handwritten": false, "struck_through": true}')
    assert ok.amounts == ["1,00,000", "(95)"] and ok.struck_through and not ok.handwritten
    assert parse_reply('```json\n{"amounts": []}\n```').amounts == []
    assert parse_reply("no json here") is None
    assert parse_reply('{"amounts": "1,00,000"}') is None
    assert parse_reply('{"amounts": [["nested"]]}') is None
    assert parse_reply('{"amounts": [true]}') is None


def test_the_second_read_prompt_carries_the_rows_own_description_as_a_hint(monkeypatch):
    import numpy as np

    from app import second_read

    seen = {}

    class Fake:
        def chat(self, kind, prompt, images=None, **kw):
            seen["prompt"] = prompt
            from app.llm_client import LLMResult
            return LLMResult('{"amounts": ["1"]}', True)

    monkeypatch.setattr(second_read, "CLIENT", Fake())
    read = second_read.read_row(np.full((200, 400), 255, np.uint8), 50, 90, 0, 400, 4, None, "t1_r1", 'Total (B) "x"')
    assert read.amounts == ["1"]
    assert "starts with: \"Total (B) 'x'\"" in seen["prompt"]
    assert "neighbouring line" in seen["prompt"]
    monkeypatch.setattr(second_read, "CLIENT", Fake())
    second_read.read_row(np.full((200, 400), 255, np.uint8), 50, 90, 0, 400, 4, None, "t1_r1")
    assert seen["prompt"].startswith("This image is one printed line")


def test_a_printed_dash_ocr_found_no_token_for_does_not_flag_the_row():
    # OCR saw one figure (the second column); the second reader also listed the dash before it.
    results, notes = reconcile_row(row(None, "2,67,238.72"), COLS, RowRead(["-", "2,67,238.72"]))
    assert results[2].status == STATUS_EMPTY and results[3].status == STATUS_VERIFIED
    assert not notes


def test_extra_non_dash_amounts_still_flag_the_row():
    results, notes = reconcile_row(row(None, "2,67,238.72"), COLS, RowRead(["5,000", "2,67,238.72"]))
    assert results[3].status == STATUS_FLAGGED and notes


def test_a_note_number_ending_the_label_is_dropped_when_the_table_has_no_note_column():
    # "Earnings per equity share- Basic 9": the 9 is a note reference inside the label; the
    # second reader listed it as the first amount.
    results, notes = reconcile_row(
        row("(0.17)", label="Earnings per equity share- Basic 9"), [2], RowRead(["9", "(0.17)"]),
    )
    assert results[2].status == STATUS_VERIFIED and not notes


def test_a_leading_amount_that_is_not_the_labels_number_still_flags_the_row():
    results, notes = reconcile_row(
        row("(0.17)", label="Earnings per equity share- Basic 9"), [2], RowRead(["5", "(0.17)"]),
    )
    assert results[2].status == STATUS_FLAGGED and notes


def test_dashes_ocr_read_and_dashes_it_missed_do_not_flag_a_row():
    # "Freehold Land": OCR read 2,095 and one printed dash; the second reader listed the
    # amounts and four dashes. The real amounts line up, so they are compared in order.
    cols = [2, 3, 4, 5, 6]
    r = Row(0, "item", 0, 10, label_tokens=[tok("Freehold Land")])
    r.cells = {2: [tok("2,095")], 3: [tok("-")], 5: [tok("2,095")], 6: [tok("2,095")]}
    results, notes = reconcile_row(r, cols, RowRead(["2,095", "-", "-", "2,095", "-", "2,095"]))
    assert results[2].status == STATUS_VERIFIED and results[5].status == STATUS_VERIFIED
    assert results[6].status == STATUS_VERIFIED
    assert results[3].status == STATUS_OCR_ONLY and results[3].ocr_text == "-"
    assert results[4].status == STATUS_EMPTY
    assert not notes


def test_real_amounts_that_do_not_line_up_still_flag_the_row_whatever_the_dashes():
    cols = [2, 3]
    r = Row(0, "item", 0, 10, label_tokens=[tok("Building")])
    r.cells = {2: [tok("1,000")], 3: [tok("-")]}
    results, notes = reconcile_row(r, cols, RowRead(["1,000", "2,000", "-"]))
    assert results[2].status == STATUS_FLAGGED and notes
