"""Builders for table-extraction tests: tokens laid out the way a printed table is."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.tabletypes import TableRegion, Token  # noqa: E402

ROW_H = 60
CHAR_W = 16
TOKEN_H = 34


def make_tokens(rows, table_no=1, page_no=1, y0=100, label_x=100,
                note_right=760, value_rights=(1150, 1450), conf=0.98):
    """rows: list of (label, note, [values...]). Empty strings are skipped.

    Labels are left-aligned at `label_x`; notes and figures are right-aligned
    at their column's right edge, like a printed statement.
    """
    tokens: list[Token] = []

    def add(text, x0, x1, y):
        tokens.append(Token(
            id=f"t{table_no}:{len(tokens)}", text=text,
            bbox=(float(x0), float(y), float(x1), float(y + TOKEN_H)),
            page_no=page_no, conf=conf,
        ))

    for r, (label, note, values) in enumerate(rows):
        y = y0 + r * ROW_H
        if label:
            add(label, label_x, label_x + CHAR_W * len(label), y)
        if note:
            add(note, note_right - CHAR_W * len(note), note_right, y)
        for text, right in zip(values, value_rights):
            if text:
                add(text, right - CHAR_W * len(text), right, y)
    return tokens


def region_for(tokens, page_no=1, title=None):
    x0 = min(t.x0 for t in tokens) - 10
    y0 = min(t.y0 for t in tokens) - 10
    x1 = max(t.x1 for t in tokens) + 10
    y1 = max(t.y1 for t in tokens) + 10
    return TableRegion(page_no=page_no, bbox=(x0, y0, x1, y1), title=title)


BALANCE_SHEET = [
    ("Particulars", "Note", ["31st March, 2024", "31st March, 2023"]),
    ("EQUITY AND LIABILITIES", "", []),
    ("Share capital", "1", ["1,00,000", "1,00,000"]),
    ("Reserves and surplus", "2", ["2,50,000", "2,00,000"]),
    ("Total equity", "", ["3,50,000", "3,00,000"]),
]
