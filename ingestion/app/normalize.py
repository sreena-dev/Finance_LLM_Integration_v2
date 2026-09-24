"""Reading a printed figure as a number, without ever rewriting it.

Two rules shape everything here.

1. **Parsing never repairs.** A token that is not cleanly a number -- a letter
   O where a zero belongs, a stray space inside the digits -- is reported as
   malformed, not silently corrected. The text a reader saw stays on the cell
   verbatim; what this module adds is only a verdict and, where there is one, a
   value to compare against another reader's.

2. **Only comparison is tolerant.** `same_figure` compares two readings by
   value and sign, so `1,20,000` and `120,000` (Indian vs Western grouping)
   agree, but `120,000` and `(120,000)` do not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

KIND_NUMBER = "number"
KIND_DASH = "dash"
KIND_EMPTY = "empty"
KIND_TEXT = "text"
KIND_MALFORMED = "malformed"

#: A printed dash or "nil" in a value column is a statement -- "the amount is
#: zero" -- not a missing figure.
_DASH_RE = re.compile(r"^[\-‐-―−]+$|^nil$|^n\.?a\.?$", re.I)

_CURRENCY_RE = re.compile(r"^(?:rs\.?|inr|₹)\s*", re.I)
_MINUS = "-−‒–"

#: Letters OCR habitually produces in place of a digit. Their presence in an
#: otherwise numeric token means "not a clean number", never "the digit is X".
_LOOKALIKES = set("OoIl|SsBbZzGgQ")

_NUMERIC_BODY_RE = re.compile(r"^\d[\d,]*(?:\.\d+)?$|^\.\d+$")
_INDIAN_GROUPED_RE = re.compile(r"^\d{1,2}(?:,\d{2})*,\d{3}(?:\.\d+)?$")
_WESTERN_GROUPED_RE = re.compile(r"^\d{1,3}(?:,\d{3})+(?:\.\d+)?$")
_UNGROUPED_RE = re.compile(r"^\d+(?:\.\d+)?$|^\.\d+$")


@dataclass(frozen=True)
class ParsedNumber:
    kind: str
    value: float | None = None
    #: True when the figure is printed as a negative -- brackets or a minus.
    negative: bool = False
    #: True when nothing about the token needed forgiving.
    clean: bool = True
    #: What was odd about it, in words, when `clean` is False.
    flags: tuple[str, ...] = ()

    @property
    def is_figure(self) -> bool:
        """A value column entry that carries an amount (a dash counts: it is zero)."""
        return self.kind in (KIND_NUMBER, KIND_DASH)


#: "31.03.2024", "31/03/2023", "31-3-24": a date printed in a heading, not an amount.
_DATE_RE = re.compile(r"^\d{1,2}[./\-]\d{1,2}[./\-](?:19|20)?\d{2}$")


def looks_like_date(text: str | None) -> bool:
    return bool(_DATE_RE.match((text or "").strip()))


def looks_numeric(text: str) -> bool:
    """Cheap first-pass check: does this token belong in a value column at all?

    Deliberately generous -- it also accepts tokens that turn out malformed --
    because the geometry stage only needs to know "figure-shaped", and the
    verdict on whether it is a *clean* figure belongs to `parse_number`.
    """
    t = (text or "").strip()
    if not t:
        return False
    if _DASH_RE.match(t):
        return True
    if _DATE_RE.match(t):
        return False
    stripped = t.strip("()").lstrip(_MINUS)
    stripped = _CURRENCY_RE.sub("", stripped)
    return sum(ch.isdigit() for ch in stripped) >= 1 and (
        sum(ch.isalpha() and ch not in _LOOKALIKES for ch in stripped) == 0
    )


def parse_number(text: str | None) -> ParsedNumber:
    raw = (text or "").strip()
    if not raw:
        return ParsedNumber(KIND_EMPTY)
    if _DASH_RE.match(raw):
        return ParsedNumber(KIND_DASH, value=0.0)

    body = raw
    negative = False
    flags: list[str] = []

    if body.startswith("(") and body.endswith(")"):
        negative, body = True, body[1:-1].strip()
    elif body.startswith("(") or body.endswith(")"):
        # An unbalanced bracket is a clipped parenthesis: the figure may or may
        # not be negative, and guessing which is how a sign gets flipped.
        negative = body.startswith("(")
        body = body.strip("()").strip()
        flags.append("sign_uncertain")

    if body and body[0] in _MINUS:
        negative, body = True, body[1:].strip()

    body = _CURRENCY_RE.sub("", body)

    if not body:
        return ParsedNumber(KIND_TEXT)

    if any(ch in _LOOKALIKES for ch in body) and any(ch.isdigit() for ch in body):
        return ParsedNumber(
            KIND_MALFORMED, negative=negative, clean=False,
            flags=tuple(flags + ["ocr_lookalike"]),
        )

    if not any(ch.isdigit() for ch in body):
        return ParsedNumber(KIND_TEXT)

    if " " in body or not _NUMERIC_BODY_RE.match(body):
        return ParsedNumber(
            KIND_MALFORMED, negative=negative, clean=False,
            flags=tuple(flags + ["unparseable"]),
        )

    if "," in body and not (_INDIAN_GROUPED_RE.match(body) or _WESTERN_GROUPED_RE.match(body)):
        flags.append("grouping_odd")
    elif "," not in body and not _UNGROUPED_RE.match(body):
        flags.append("unparseable")

    value = float(body.replace(",", ""))
    return ParsedNumber(
        KIND_NUMBER,
        value=-value if negative else value,
        negative=negative,
        clean=not flags,
        flags=tuple(flags),
    )


def same_figure(a: str | None, b: str | None) -> bool:
    """True when two readings are the same amount with the same sign.

    Both must parse to an amount; a reading that is text or malformed never
    agrees with anything -- two garbled reads of one cell are not corroboration.
    """
    pa, pb = parse_number(a), parse_number(b)
    if not (pa.is_figure and pb.is_figure):
        return False
    if pa.kind == KIND_DASH or pb.kind == KIND_DASH:
        return pa.kind == pb.kind or (pa.value == 0.0 and pb.value == 0.0)
    if pa.value is None or pb.value is None:
        return False
    return abs(pa.value - pb.value) < 0.005


def tolerance(*values: float) -> float:
    """Slack allowed when checking a printed total against its parts.

    The larger of a flat allowance and a relative one: filings round to the
    nearest thousand, and also present in crore to two decimals.
    """
    from .config import Config

    biggest = max((abs(v) for v in values), default=0.0)
    return max(Config.FOOTING_ABS_TOLERANCE, biggest * Config.FOOTING_REL_TOLERANCE)
