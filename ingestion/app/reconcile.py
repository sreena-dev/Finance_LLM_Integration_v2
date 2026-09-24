"""Deciding how far each figure can be trusted. Nothing is ever overwritten.

Two independent readers looked at every figure: OCR (which supplied the
digits and their positions) and, where the budget and the model allowed, a
second read of the same printed row. This module compares them and gives each
cell one status:

    verified_dual_read  the two agree exactly (value and sign)
    ocr_only            no second read of this row exists; OCR is clean and confident
    flagged             the two read it differently -- shown as a marker, with
                        the second reader's figure attached for a human to judge
    unreadable          nothing trustworthy to show
    handwritten/struck  the second reader saw a handwritten or crossed-out
                        figure on this row
    empty               nothing is printed in this cell

The rule that keeps this honest: a doubtful figure is *flagged*, never
"fixed". OCR's text stays on the cell verbatim and the second reader's text is
kept beside it; which one is right is a human's call.
"""

from __future__ import annotations

import re

from .config import Config
from .normalize import KIND_DASH, KIND_NUMBER, parse_number, same_figure
from .second_read import RowRead
from .tabletypes import (
    STATUS_EMPTY, STATUS_FLAGGED, STATUS_HANDWRITTEN, STATUS_OCR_ONLY, STATUS_STRUCK,
    STATUS_UNREADABLE, STATUS_VERIFIED, CellResult, Row,
)


def _is_dash(text: str) -> bool:
    return (text or "").strip() in _DASHES


_DASHES = {"-", "\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2212", "nil", "Nil", "NIL"}


def _cell_text(tokens) -> tuple[str, float | None]:
    text = " ".join(t.text for t in tokens).strip()
    confs = [t.conf for t in tokens if t.conf is not None]
    return text, (min(confs) if confs else None)


def _ocr_only(row_i: int, col: int, text: str, conf: float | None, tokens) -> CellResult:
    parsed = parse_number(text)
    reasons: list[str] = []
    if parsed.kind not in (KIND_NUMBER, KIND_DASH) or not parsed.clean:
        reasons.append("unreadable_text")
        return CellResult(row_i, col, STATUS_UNREADABLE, text, None, conf, reasons, tokens)
    if conf is not None and conf < Config.OCR_MIN_CONFIDENCE:
        reasons.append(f"low_ocr_confidence:{conf:.2f}")
        return CellResult(row_i, col, STATUS_UNREADABLE, text, None, conf, reasons, tokens)
    return CellResult(row_i, col, STATUS_OCR_ONLY, text, None, conf, ["no_second_read_for_row"], tokens)


def reconcile_row(
    row: Row, value_cols: list[int], second: RowRead | None,
) -> tuple[dict[int, CellResult], list[str]]:
    """Cell results for one row, plus any row-level notes."""
    notes: list[str] = []
    filled = [c for c in value_cols if row.cells.get(c)]
    results: dict[int, CellResult] = {}

    for col in value_cols:
        if col not in filled:
            results[col] = CellResult(row.index, col, STATUS_EMPTY)

    if not filled:
        if second is not None and any(a not in ("", "-", "?") for a in second.amounts):
            notes.append(
                f"Row '{row.label}': the second read saw an amount that OCR found no text for "
                "in any figure column; nothing was added."
            )
        return results, notes

    if second is None:
        for col in filled:
            text, conf = _cell_text(row.cells[col])
            results[col] = _ocr_only(row.index, col, text, conf, row.cells[col])
        return results, notes

    amounts = list(second.amounts)
    # A note-reference number beside the label sometimes comes back as the
    # first "amount"; drop it only when that explains an off-by-one exactly.
    if len(amounts) == len(filled) + 1:
        first = amounts[0].strip()
        label_tail = row.label.split()[-1] if row.label.split() else ""
        # The reference may sit in a note column, or -- when the table has no note
        # column -- simply end the label ("... Basic 9"). Either way it is the same
        # printed number the reader listed first.
        if (row.note and first == row.note.strip()) or (
            re.fullmatch(r"\d{1,3}", first) and first == label_tail
        ):
            amounts = amounts[1:]

    if second.handwritten or second.struck_through:
        status = STATUS_HANDWRITTEN if second.handwritten else STATUS_STRUCK
        reason = "handwritten" if second.handwritten else "struck_through"
        for col in filled:
            text, conf = _cell_text(row.cells[col])
            results[col] = CellResult(row.index, col, status, text, None, conf, [reason], row.cells[col])
        return results, notes

    # A printed dash (nil) is small, so OCR reads some and misses others, and the second
    # reader lists every one it sees. Counting dashes on either side is therefore
    # unreliable; the real amounts are not. When the real amounts line up one-to-one, they
    # are compared in order. A cell OCR read as a dash stays as OCR read it; a cell OCR
    # found nothing in stays empty.
    dash_cells: list[int] = []
    if len(amounts) != len(filled):
        real_cols = [c for c in filled if not _is_dash(_cell_text(row.cells[c])[0])]
        real_amounts = [a for a in amounts if not _is_dash(a)]
        if real_cols and len(real_cols) == len(real_amounts):
            dash_cells = [c for c in filled if c not in real_cols]
            filled, amounts = real_cols, real_amounts

    if len(amounts) != len(filled):
        for col in filled:
            text, conf = _cell_text(row.cells[col])
            results[col] = CellResult(
                row.index, col, STATUS_FLAGGED, text, None, conf, ["row_count_mismatch"], row.cells[col],
            )
        notes.append(
            f"Row '{row.label}': OCR found {len(filled)} figure(s) but the second read saw "
            f"{len(amounts)}; every figure on the row is flagged rather than paired by guess."
        )
        return results, notes

    for col in dash_cells:
        text, conf = _cell_text(row.cells[col])
        results[col] = _ocr_only(row.index, col, text, conf, row.cells[col])

    for col, second_text in zip(filled, amounts):
        text, conf = _cell_text(row.cells[col])
        tokens = row.cells[col]
        if second_text == "?":
            results[col] = _ocr_only(row.index, col, text, conf, tokens)
            continue
        if same_figure(text, second_text):
            results[col] = CellResult(row.index, col, STATUS_VERIFIED, text, second_text, conf, [], tokens)
        else:
            results[col] = CellResult(
                row.index, col, STATUS_FLAGGED, text, second_text, conf, ["readers_disagree"], tokens,
            )
    return results, notes
