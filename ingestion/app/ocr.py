"""OCR of a table region: every word, with its box. The only source of digits.

RapidOCR returns *lines*, and on a table it happily merges neighbouring cells
into one line ("Property, Plant and Equipment  3  68,67,416.61  3,10,253.00").
So the request is made with word boxes, and the words are regrouped here into
cell-sized tokens: consecutive words that are close together (a label's words)
stay one token, while a wide gap -- a column boundary -- starts a new one.

A figure token is never split or merged with another figure across a column
gap, and its text is exactly what OCR printed. Nothing downstream rewrites it.
"""

from __future__ import annotations

import logging
import threading

from .config import Config
from .normalize import looks_numeric
from .tabletypes import Box, Token

logger = logging.getLogger(__name__)

# A gap wider than this (in line heights) between two words starts a new token.
_PHRASE_GAP = 0.9
# Two figure-shaped words must be much closer than that to be one figure
# ("1 200" printed with a thin space), or two columns would fuse.
_FIGURE_GAP = 0.25

Word = tuple[str, float | None, Box]


def _box(quad) -> Box:
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    return (float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys)))


def split_joined_figures(word: Word) -> list[Word]:
    """A "word" that is really several figures -- ``-4,037 6,29,781`` -- as several words.

    The detector sometimes returns two neighbouring columns' figures as one box.
    Left whole it is one malformed cell and the row's other figure is lost. Each
    part is given its share of the box in proportion to its characters, which is
    exact enough to decide which column it belongs to.
    """
    text, conf, box = word
    parts = text.split()
    if len(parts) < 2 or not all(looks_numeric(p) for p in parts):
        return [word]
    total = max(1, len(text))
    width = box[2] - box[0]
    out: list[Word] = []
    cursor = 0
    for part in parts:
        start = text.index(part, cursor)
        end = start + len(part)
        cursor = end
        out.append((part, conf, (box[0] + width * start / total, box[1], box[0] + width * end / total, box[3])))
    return out


def group_words(words: list[Word]) -> list[tuple[str, float | None, Box]]:
    """One line's words -> cell-sized (text, confidence, box) tokens."""
    words = [piece for w in words for piece in split_joined_figures(w)]
    words = sorted(words, key=lambda w: w[2][0])
    tokens: list[tuple[str, float | None, Box]] = []
    for text, conf, box in words:
        if not text.strip():
            continue
        if tokens:
            p_text, p_conf, p_box = tokens[-1]
            height = max(box[3] - box[1], p_box[3] - p_box[1], 1.0)
            gap = box[0] - p_box[2]
            # Only two plain words share a token across a normal word gap. As
            # soon as either side looks like a figure, the gap must be tiny --
            # otherwise a label swallows the note number printed after it.
            plain_pair = not looks_numeric(p_text) and not looks_numeric(text)
            limit = _PHRASE_GAP if plain_pair else _FIGURE_GAP
            if gap <= limit * height:
                confs = [c for c in (p_conf, conf) if c is not None]
                tokens[-1] = (
                    f"{p_text} {text}",
                    min(confs) if confs else None,
                    (min(p_box[0], box[0]), min(p_box[1], box[1]), max(p_box[2], box[2]), max(p_box[3], box[3])),
                )
                continue
        tokens.append((text, conf, box))
    return tokens


_local = threading.local()


def _engine():
    """One RapidOCR engine per thread: its ONNX sessions are not shared safely."""
    engine = getattr(_local, "engine", None)
    if engine is None:
        from rapidocr import RapidOCR

        engine = RapidOCR(params={"Global.use_cls": Config.OCR_USE_ANGLE_CLASSIFIER})
        _local.engine = engine
    return engine


def read_region(page_image, bbox: Box, page_no: int, table_no: int, pad: float = 12.0) -> list[Token]:
    """Every word OCR reads inside `bbox` (page pixels), as ordered tokens."""
    height, width = page_image.shape[:2]
    x0, y0 = max(0, int(bbox[0] - pad)), max(0, int(bbox[1] - pad))
    x1, y1 = min(width, int(bbox[2] + pad)), min(height, int(bbox[3] + pad))
    if x1 <= x0 or y1 <= y0:
        return []
    crop = page_image[y0:y1, x0:x1]
    out = _engine()(crop, return_word_box=True)
    lines = getattr(out, "word_results", None)

    raw: list[list[Word]] = []
    if lines:
        for line in lines:
            words: list[Word] = []
            for text, conf, quad in line:
                b = _box(quad)
                words.append((str(text), float(conf) if conf is not None else None,
                              (b[0] + x0, b[1] + y0, b[2] + x0, b[3] + y0)))
            raw.append(words)
    elif getattr(out, "txts", None):
        # No word boxes from this build: fall back to whole lines rather than nothing.
        for text, score, quad in zip(out.txts, out.scores, out.boxes):
            b = _box(quad)
            raw.append([(str(text), float(score), (b[0] + x0, b[1] + y0, b[2] + x0, b[3] + y0))])

    tokens: list[Token] = []
    for words in raw:
        for text, conf, box in group_words(words):
            tokens.append(Token(
                id=f"t{table_no}:{len(tokens)}", text=text.strip(), bbox=box,
                page_no=page_no, conf=conf,
            ))
    return tokens
