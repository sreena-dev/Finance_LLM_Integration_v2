"""An independent second read of one table row, from its own cropped image.

OCR has already read every figure and supplied its box. The second read asks a
different question of a different reader: looking only at this one printed
line, what amounts do you see, left to right? The answer is then *compared*
with OCR by `reconcile.py`. It is never used to fill in a figure OCR did not
find and never overwrites what OCR read; where the two disagree the cell is
flagged for a human.

Reading a single row (rather than the whole table) is deliberate. A whole-table
read by a vision model was measured to transpose the current- and previous-year
columns and to invent plausible rows; a one-line crop leaves the model nothing
to reorder and little to invent, and it is cheap: one short reply per row.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import Config
from .llm_client import CLIENT, KIND_VISION, parse_json

logger = logging.getLogger(__name__)

_PROMPT = """{hint}This image is one printed line of a financial statement table.

List every amount printed on this line, in order from LEFT to RIGHT, exactly as printed: keep commas, brackets and decimal points. Ignore the description text on the left and any small note-reference number beside it. If a cell shows only a dash, write "-". If an amount is too unclear to read, write "?". Never guess or complete a number.

Reply with JSON only:
{"amounts": ["1,20,000", "(95,000)"], "handwritten": false, "struck_through": false}

"handwritten" is true only if an amount on this line is handwritten rather than printed. "struck_through" is true only if a printed amount is crossed out."""


@dataclass
class RowRead:
    amounts: list[str]
    handwritten: bool = False
    struck_through: bool = False


def parse_reply(content: str | None) -> RowRead | None:
    """A strict reading of the model's reply, or None if it is not usable."""
    data = parse_json(content)
    if not isinstance(data, dict):
        return None
    raw = data.get("amounts")
    if not isinstance(raw, list):
        return None
    amounts: list[str] = []
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, (str, int, float)):
            return None
        amounts.append(str(item).strip())
    return RowRead(
        amounts=amounts,
        handwritten=data.get("handwritten") is True,
        struck_through=data.get("struck_through") is True,
    )


def row_crop(page_image, y0: float, y1: float, x0: float, x1: float, pad: float):
    """The strip of the page holding one row, upscaled for the model."""
    import cv2

    height, width = page_image.shape[:2]
    top, bottom = max(0, int(y0 - pad)), min(height, int(y1 + pad))
    left, right = max(0, int(x0)), min(width, int(x1))
    if bottom <= top or right <= left:
        return None
    strip = page_image[top:bottom, left:right]
    scale = Config.SECOND_READ_CROP_SCALE
    if scale and scale != 1.0:
        new_w = int(strip.shape[1] * scale)
        if new_w > 2400:
            scale = 2400 / strip.shape[1]
        strip = cv2.resize(strip, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    return strip


def _hint(row_label: str | None) -> str:
    text = " ".join((row_label or "").split())[:70].replace('"', "'")
    if not text:
        return ""
    return (
        f'The description printed on this line starts with: "{text}". Read only the amounts on THAT '
        "line; ignore any part of a neighbouring line visible above or below it.\n\n"
    )


def read_row(page_image, y0: float, y1: float, x0: float, x1: float, pad: float,
             doc_id: str | None, label: str, row_label: str | None = None) -> RowRead | None:
    strip = row_crop(page_image, y0, y1, x0, x1, pad)
    if strip is None:
        return None
    reply = CLIENT.chat(KIND_VISION, _PROMPT.replace("{hint}", _hint(row_label)), [strip],
                        doc_id=doc_id, label=label)
    if not reply.ok:
        return None
    return parse_reply(reply.content)
