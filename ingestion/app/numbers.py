"""Parsing money cells out of an OCR'd Indian financial statement.

This is a port of the cell parser in
``backend/modes/financial_diagnostic_report/pipeline/fs_db/md_parser.py``
(``parse_num``), which already handles the hard parts of a *filed* statement:
Indian comma grouping, the CPSU ``(-)``/``(+)`` sign markers, typographic minus
glyphs, currency words sitting inside the sign marker, and nil markers. It is
ported rather than imported because this service is a separate image and must
not take a dependency on the gateway's package tree.

Two things are added here that the database-side parser does not need, because
they only arise when the text came out of an OCR engine rather than a PDF text
layer. Both were observed in the sample corpus:

1. **Clipped parentheses.** ``data/MH-.../2022-23/..._SFS_...pdf`` page 5 prints
   ``(1,757`` and ``(10,378`` -- the closing paren of a negative falls outside
   the ruled table border and is cropped away. Read naively that is a positive
   number, and a sign error on a disposals row is exactly the kind of silent
   defect this whole pipeline exists to stop. An unbalanced leading ``(`` is
   strong evidence of a negative (nothing else puts one there), so the value is
   returned negative with ``sign_uncertain`` set, and ``verify.py`` either
   confirms it against the column's own footing or marks the cell unreadable.

2. **Separator confusion.** The same corpus produced ``18.00.000`` for
   ``18,00,000`` -- the OCR read commas as full stops. A cell with more than one
   ``.`` cannot be a decimal, so the dots are separators. Cells whose digit
   grouping matches neither the Indian (2,2,3) nor the Western (3,3,3) pattern
   are flagged rather than repaired.

Nothing here ever *guesses* a digit. A cell that cannot be read confidently
comes back with flags attached, and the caller's job is to withhold it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Cells that mean "no figure", not "zero". A filing prints a dash for a line
# that does not apply; reading that as 0.0 makes an absent row foot correctly
# and hides the fact that it was never there.
NIL = {
    "", "-", "--", "---", "n/a", "na", "nil", "none",
    "–", "—", "−", ".", "_",
}

_CURRENCY_RE = re.compile(r"[₹$]|\brs\.?|\binr\b", re.I)
_UNICODE_MINUS = "−–—"
_SIGN_PREFIX_RE = re.compile(r"^\(\s*([+-])\s*\)\s*")
_SIGN_SUFFIX_RE = re.compile(r"\s*\(\s*([+-])\s*\)$")
_NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")

# A number followed by a unit-of-measure word -- "3 Years", "10 Yrs" -- the
# recurring shape on useful-life / amortisation-period schedules. `_NUM_RE`
# alone rejects these outright (the trailing letters make the whole cleaned
# string fail it), and nothing else in this module recognises them either, so
# a cell like this used to come back with `value=None` and NO flags set at
# all -- not wrong, just silently unusable, and verify.py then classifies it
# as `unreadable_text` purely because the letters aren't digits. Tried only as
# a fallback AFTER `_NUM_RE` fails (see parse_cell), so it never changes how
# an ordinary number parses. Deliberately a plain tuple, not exhaustive on day
# one -- extend it (e.g. "kgs?") rather than redesigning around it.
_UNIT_WORDS = ("years?", "yrs?", "months?", "days?", "hours?", "hrs?")
_UNIT_SUFFIX_RE = re.compile(
    r"^(-?\d+(?:\.\d+)?)\s*(?:" + "|".join(_UNIT_WORDS) + r")$", re.I,
)

# Digit grouping. Indian: 12,34,56,789 -- the last group is 3, every group
# before it is 2. Western: 123,456,789 -- every group is 3.
_INDIAN_GROUPING = re.compile(r"^\d{1,2}(,\d{2})+,\d{3}$")
_WESTERN_GROUPING = re.compile(r"^\d{1,3}(,\d{3})+$")

# Characters an OCR engine substitutes for digits often enough to be worth
# naming in a caveat. Never auto-corrected -- see the module docstring.
_OCR_LOOKALIKES = {"O", "o", "l", "I", "S", "B", "Z"}


@dataclass
class Cell:
    """One parsed money cell, with every reason to doubt it recorded.

    ``value is None`` means no figure was read. That is not the same as zero and
    callers must not conflate them.
    """

    raw: str
    value: float | None = None
    is_nil: bool = False
    #: A leading "(" with no closing ")" (or the reverse). The value is returned
    #: negative but the sign needs independent confirmation.
    sign_uncertain: bool = False
    #: Dots were treated as thousands separators rather than a decimal point.
    separators_repaired: bool = False
    #: Digit grouping matched neither the Indian nor the Western pattern.
    grouping_odd: bool = False
    #: Letters that are common OCR substitutions for digits appeared inside what
    #: is otherwise a number.
    lookalikes: list[str] = field(default_factory=list)

    @property
    def suspect(self) -> bool:
        """True if anything about this reading needs corroborating."""
        return bool(
            self.sign_uncertain
            or self.separators_repaired
            or self.grouping_odd
            or self.lookalikes
        )

    def flags(self) -> list[str]:
        out: list[str] = []
        if self.sign_uncertain:
            out.append("sign_uncertain")
        if self.separators_repaired:
            out.append("separators_repaired")
        if self.grouping_odd:
            out.append("grouping_odd")
        if self.lookalikes:
            out.append("ocr_lookalike:" + ",".join(sorted(set(self.lookalikes))))
        return out


def parse_cell(raw: str | None) -> Cell:
    """Parse one table cell. Never raises; never guesses a digit."""
    if raw is None:
        return Cell(raw="", is_nil=True)

    original = str(raw)
    s = original.strip()
    if s.lower() in NIL:
        return Cell(raw=original, is_nil=True)

    cell = Cell(raw=original)

    # Currency first: it sits *inside* both the sign marker ("(-) Rs. 96.73") and
    # the parenthesised negative, and in both places it blocks the rule that has
    # to fire next.
    s = _CURRENCY_RE.sub("", s).strip()
    if s.lower() in NIL:
        cell.is_nil = True
        return cell

    # An explicit (-)/(+) marker is authoritative and suppresses the
    # parenthesised-negative rule below: filings that use the marker often also
    # print the figure in parens, and that is one sign stated twice.
    marked = None
    m = _SIGN_PREFIX_RE.match(s)
    if m:
        marked, s = m.group(1), s[m.end():].strip()
    else:
        m = _SIGN_SUFFIX_RE.search(s)
        if m:
            marked, s = m.group(1), s[: m.start()].strip()

    neg = False
    opens, closes = s.count("("), s.count(")")
    if opens and closes and s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1].strip()
    elif opens != closes:
        # Clipped by the table border. See the module docstring.
        neg = True
        cell.sign_uncertain = True
        s = s.replace("(", "").replace(")", "").strip()

    if not s:
        cell.is_nil = True
        return cell

    has_digit = any(c.isdigit() for c in s)
    if has_digit:
        cell.lookalikes = [c for c in s if c in _OCR_LOOKALIKES]

    grouped = s.replace(" ", "")

    # Separator confusion: more than one "." cannot be a decimal point.
    if grouped.count(".") > 1:
        grouped = grouped.replace(".", ",")
        cell.separators_repaired = True

    # Validate the grouping before stripping it -- once the commas are gone the
    # evidence is gone with them.
    # lstrip the typographic minus glyphs too, not just ASCII: a cell written
    # "−1,234" is correctly grouped, and leaving the glyph on the front makes
    # the pattern miss and reports a false grouping defect.
    int_part = grouped.split(".")[0].lstrip("+-" + _UNICODE_MINUS)
    if "," in int_part:
        if not (_INDIAN_GROUPING.match(int_part) or _WESTERN_GROUPING.match(int_part)):
            cell.grouping_odd = True

    s = grouped.replace(",", "")
    if s and s[0] in _UNICODE_MINUS:
        s = "-" + s[1:]
    if s.endswith("-") and len(s) > 1:
        # A trailing dash is a filler for "nothing after this", not a sign.
        s = s[:-1]

    if not _NUM_RE.match(s):
        # A unit-suffixed number ("3Years", after whitespace was already
        # stripped above) -- the raw text still shows "3 Years" verbatim
        # (cell.raw is untouched); only the parsed value changes, from
        # unusable to 3.0. Not flagged suspect in any sense -- a cleanly
        # recognised unit word is not an OCR defect.
        unit_match = _UNIT_SUFFIX_RE.match(s)
        if unit_match:
            value = float(unit_match.group(1))
            if marked is not None:
                cell.value = -abs(value) if marked == "-" else abs(value)
                cell.sign_uncertain = False
            else:
                cell.value = -value if neg else value
            return cell
        # Includes a bare "(-)": a sign with no figure behind it.
        return cell

    value = float(s)
    if marked is not None:
        cell.value = -abs(value) if marked == "-" else abs(value)
        # An explicit marker settles the sign, whatever the parens did.
        cell.sign_uncertain = False
    else:
        cell.value = -value if neg else value
    return cell


def parse_num(raw: str | None) -> float | None:
    """Value-only convenience wrapper, matching the gateway parser's signature."""
    return parse_cell(raw).value


def format_amount(value: float | None) -> str:
    return "not extracted" if value is None else f"{value:,.2f}"
