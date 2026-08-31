"""
Scale resolution — the defect that is invisible to every other check.

WHY THIS MODULE EXISTS
----------------------
77% of typed financial tables in `finance_llm` carry no `unit` (7,362 of 9,524, measured
2026-08-01). A missing scale is harmless inside one statement — a ratio divides it out —
which is why nothing has failed yet. It stops being harmless the moment figures from two
filings are compared: an entity that switched presentation from lakh to crore produces a
100x "trend" that every arithmetic tie-out will happily confirm, because each filing
balances perfectly against itself.

Every FDR trend signal reads a multi-year panel. So the scale has to be resolved, and
resolved with a stated confidence, BEFORE any panel is built.

THE WATERFALL
-------------
Six tiers, most authoritative first. Each returns (scale, source, confidence).

  1 explicit `unit` column          HIGH    2,162 tables
  2 banner inside `table_md`        HIGH    ⎫
  3 preceding / following text      MEDIUM  ⎬ 4,585 of the 7,362 NULLs (union, measured)
  4 document modal unit             LOW     ⎭
  5 cross-year magnitude            MEDIUM  the remainder
  6 nothing                         NONE -> UNRESOLVED

Tier 4 is deliberately LOW: within one filing a note can be presented in lakh while the
face statement is in crore. The document mode is a hint, never a verdict.

`UNRESOLVED` is a legal, queryable outcome. A NULL is not — a figure whose scale is
unknown must be visibly unknown, not silently assumed (finance-core P3, abstain > guess).

THE DETECTOR THAT MAKES TIERS 4-5 TRUSTWORTHY
----------------------------------------------
A scale error cannot be caught by any within-table identity, but it has a characteristic
signature ACROSS tables: the discrepancy is a clean power of ten. `classify_mismatch()`
turns a failed note-to-face or cross-year check into a unit diagnosis instead of a figure
diagnosis. That is what allows a LOW-confidence tier-4 resolution to be promoted (or
corrected) rather than merely believed.

Deterministic and dependency-free apart from `db` for the text-chunk join.
"""
from __future__ import annotations
import re
from collections import Counter
from dataclasses import dataclass, asdict
from typing import Any

# ---- the scale vocabulary --------------------------------------------------------
UNITS, THOUSAND, LAKH, MILLION, CRORE, BILLION = (
    "units", "thousand", "lakh", "million", "crore", "billion")
UNRESOLVED = "UNRESOLVED"

# Multiplier to ONE normalised scale. Lakh is chosen because it loses no precision on the
# smallest entities in the corpus and keeps crore-scale figures in a readable range.
TO_LAKH: dict[str, float] = {
    UNITS:     0.00001,      # 1 rupee            = 1e-5 lakh
    THOUSAND:  0.01,         # 1 thousand         = 1e-2 lakh
    LAKH:      1.0,
    MILLION:   10.0,         # 1 million          = 10 lakh
    CRORE:     100.0,        # 1 crore            = 100 lakh
    BILLION:   10000.0,      # 1 billion          = 10,000 lakh
}
SCALES = frozenset(TO_LAKH)

HIGH, MEDIUM, LOW, NONE = "HIGH", "MEDIUM", "LOW", "NONE"

# ---- recognition -----------------------------------------------------------------
# Deliberately NOT anchored: the banner appears as "(₹ in Crore)", "₹ Crore",
# "Rs. in lakhs", "(All amounts in ₹ million unless otherwise stated)".
_SCALE_RX = re.compile(
    r"\b(crores?|cr\.?|lakhs?|lacs?|millions?|mn\.?|billions?|bn\.?|thousands?|'?000s?)\b",
    re.I,
)
_CANON = {
    "cr": CRORE, "crore": CRORE, "crores": CRORE,
    "lakh": LAKH, "lakhs": LAKH, "lac": LAKH, "lacs": LAKH,
    "mn": MILLION, "million": MILLION, "millions": MILLION,
    "bn": BILLION, "billion": BILLION, "billions": BILLION,
    "thousand": THOUSAND, "thousands": THOUSAND, "000": THOUSAND, "'000": THOUSAND,
    "000s": THOUSAND, "'000s": THOUSAND,
}

# A scale word only means "this table is presented in X" when it appears in a UNIT
# context. "Payment of ₹ 5 crore to the supplier" inside a narrative chunk is a sentence
# about a figure, not a presentation basis. Requiring a unit cue in front of the scale
# word is what keeps tier 3 from resolving off arbitrary prose — the measured cost of
# omitting this was resolutions driven by contingent-liability narrative.
_UNIT_CUE = re.compile(
    r"(?:₹|rs\.?|inr|amounts?|figures?|all\s+amounts?|in)\s*"
    r"(?:in\s+)?[^\w]{0,3}$",
    re.I,
)
# Currency, for completeness. The corpus is INR throughout; a USD table would be a
# presentation-currency disclosure and must not be silently normalised as if it were INR.
_CCY_RX = re.compile(r"\b(usd|us\s*\$|\$|eur|gbp)\b", re.I)


@dataclass(frozen=True)
class UnitResolution:
    """The scale of one table, with how it was decided and how much to trust it."""
    scale: str                    # a member of SCALES, or UNRESOLVED
    currency: str = "INR"
    source: str = UNRESOLVED      # explicit|table_banner|preceding_text|following_text
                                  # |doc_modal|cross_year|UNRESOLVED
    confidence: str = NONE        # HIGH|MEDIUM|LOW|NONE
    evidence: str = ""            # the text the decision was read from

    @property
    def resolved(self) -> bool:
        return self.scale in SCALES

    @property
    def multiplier_to_lakh(self) -> float | None:
        return TO_LAKH.get(self.scale)

    def to_lakh(self, value: float | None) -> float | None:
        """Convert a native figure to the normalised scale. None when unresolved."""
        m = self.multiplier_to_lakh
        return None if (m is None or value is None) else value * m

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


UNKNOWN = UnitResolution(scale=UNRESOLVED)


# ---- tier helpers ----------------------------------------------------------------

def parse_scale(text: str | None, *, require_cue: bool = False) -> tuple[str | None, str]:
    """Find a presentation scale in free text. Returns (scale, matched_evidence).

    `require_cue` demands a unit context ("₹ in", "amounts in", "Rs.") immediately before
    the scale word. Used for narrative chunks, where a bare "crore" is far more likely to
    be part of a sentence about a figure than a presentation basis.
    """
    if not text:
        return None, ""
    for m in _SCALE_RX.finditer(text):
        word = m.group(1).lower().rstrip(".").strip("'")
        scale = _CANON.get(word) or _CANON.get(word.rstrip("s"))
        if scale is None:
            continue
        if require_cue and not _UNIT_CUE.search(text[max(0, m.start() - 24):m.start()]):
            continue
        lo, hi = max(0, m.start() - 30), min(len(text), m.end() + 10)
        return scale, " ".join(text[lo:hi].split())
    return None, ""


def parse_currency(text: str | None) -> str:
    m = _CCY_RX.search(text or "")
    if not m:
        return "INR"
    t = m.group(1).lower().replace(" ", "")
    return {"us$": "USD", "$": "USD"}.get(t, t.upper())


def _banner_zone(table_md: str | None, lines: int = 4) -> str:
    """The header plus the first few rows — where a unit banner is printed.

    Scanning the whole table would match any row whose LABEL contains a scale word
    ("Investment in ... Crore Bonds"), which is not a presentation basis.
    """
    if not table_md:
        return ""
    return "\n".join(table_md.splitlines()[:lines])


# ---- the waterfall ---------------------------------------------------------------

def resolve(
    *,
    unit_column: str | None = None,
    table_md: str | None = None,
    preceding_text: str | None = None,
    following_text: str | None = None,
    doc_modal: str | None = None,
) -> UnitResolution:
    """Resolve one table's scale. Pure — the caller supplies the text, this decides.

    Tier 5 (cross-year) is NOT here: it needs two filings and therefore belongs to the
    materialisation pass, which has both. `promote_by_cross_year()` applies it.
    """
    ccy = parse_currency(unit_column) if unit_column else "INR"

    # 1 — the explicit column, normalised out of its free-text form.
    if unit_column:
        scale, ev = parse_scale(unit_column)
        if scale:
            return UnitResolution(scale, ccy, "explicit", HIGH, ev or unit_column)

    # 2 — a banner printed inside the table itself.
    zone = _banner_zone(table_md)
    scale, ev = parse_scale(zone, require_cue=True)
    if scale:
        return UnitResolution(scale, parse_currency(zone), "table_banner", HIGH, ev)

    # 3 — the narrative immediately around the table. Preceding first: the banner is
    #     printed ABOVE the table far more often than below it.
    for text, src in ((preceding_text, "preceding_text"),
                      (following_text, "following_text")):
        scale, ev = parse_scale(text, require_cue=True)
        if scale:
            return UnitResolution(scale, parse_currency(text), src, MEDIUM, ev)

    # 4 — what the rest of this filing is presented in. A hint, never a verdict: a note
    #     may legitimately use a different scale from the face statement.
    if doc_modal in SCALES:
        return UnitResolution(doc_modal, "INR", "doc_modal", LOW,
                              "most common resolved scale in this document")

    return UNKNOWN


def modal_scale(resolutions: list[UnitResolution]) -> str | None:
    """The document's dominant scale, from tiers 1-3 only.

    Tier-4 results are excluded by construction — feeding doc_modal back into itself
    would let one weak resolution propagate across a whole filing.
    """
    strong = [r.scale for r in resolutions
              if r.resolved and r.confidence in (HIGH, MEDIUM)]
    if not strong:
        return None
    return Counter(strong).most_common(1)[0][0]


# ---- the power-of-ten detector ---------------------------------------------------

_POWERS = (-4, -3, -2, -1, 1, 2, 3, 4)


def classify_mismatch(a: float, b: float, *, tol: float = 0.02) -> tuple[str, float | None]:
    """Diagnose a failed cross-table comparison: scale error, or figure error?

    A unit error is invisible to every within-table identity but leaves a characteristic
    signature across tables — the two figures differ by a clean power of ten. A genuine
    figure error almost never does.

    Returns ("UNIT_MISMATCH", factor) or ("FIGURE_MISMATCH", None). `factor` is what `a`
    must be multiplied by to reach `b`, so the caller can name the correction.
    """
    if not a or not b:
        return "FIGURE_MISMATCH", None
    ratio = b / a
    if ratio <= 0:
        return "FIGURE_MISMATCH", None
    for k in _POWERS:
        p = 10.0 ** k
        if abs(ratio - p) <= tol * p:
            return "UNIT_MISMATCH", p
    return "FIGURE_MISMATCH", None


def scale_for_factor(current: str, factor: float) -> str | None:
    """The scale `current` would have to be for a figure to grow by `factor`.

    Used to turn a detected UNIT_MISMATCH into a concrete correction rather than a
    warning: if the note is 100x the face and the face reads crore, the note is in lakh.
    """
    if current not in SCALES:
        return None
    target = TO_LAKH[current] / factor
    for scale, mult in TO_LAKH.items():
        if abs(mult - target) <= 1e-9 * max(1.0, target):
            return scale
    return None


def promote_by_cross_year(
    weak: UnitResolution,
    *,
    this_year: float | None,
    prior_year_same_key: float | None,
    prior_scale: str | None,
) -> UnitResolution:
    """Tier 5. Settle a scale by comparing one figure against the prior filing's.

    The comparative column of filing N and the current column of filing N-1 report the
    SAME economic figure. If they differ by a clean power of ten, the scales differ by
    exactly that factor, and the prior year's known scale names this year's.

    A conservative promotion: it only fires when the prior scale is itself known, and it
    never overwrites a HIGH-confidence resolution.
    """
    if weak.confidence == HIGH or prior_scale not in SCALES:
        return weak
    if this_year is None or prior_year_same_key is None:
        return weak

    kind, factor = classify_mismatch(this_year, prior_year_same_key)
    if kind == "UNIT_MISMATCH" and factor:
        inferred = scale_for_factor(prior_scale, 1.0 / factor)
        if inferred:
            return UnitResolution(
                inferred, weak.currency, "cross_year", MEDIUM,
                f"prior filing presents the same figure {factor:g}x differently; "
                f"prior scale {prior_scale} implies {inferred}",
            )
    # Same magnitude as the prior filing -> same scale as the prior filing.
    if abs(this_year - prior_year_same_key) <= 0.02 * max(abs(this_year),
                                                          abs(prior_year_same_key)):
        return UnitResolution(prior_scale, weak.currency, "cross_year", MEDIUM,
                              "figure agrees with the prior filing at its known scale")
    return weak
