"""Spec section 9.3's "must-move-together" table, as executable config.

The spec hands this table over already written out — revenue should move with
receivables, PPE with depreciation, borrowings with finance cost, and so on —
with the divergence each pair's split is meant to surface. This module is that
table transcribed, plus the comparison it exists to drive: given the captured
per-line-item year-over-year series `trend_capture.py` recovers, find any pair
present in the same statement and flag the year their percentage changes pull
apart by more than the threshold.

WHY KEYWORD MATCHING, NOT AN EXACT LABEL KEY
----------------------------------------------
`TrendAnalysisTools._extract_year_series` keys each row on a cleaned-but-not-
canonicalised label (`Row.label = _re_clean_label(raw_label).lower()`,
tools_fs.py) — it is filing-specific text, not a mapped FSLI. "Revenue from
Operations" in one filing and "Total Revenue from Operations" in another are
different keys. Every existing matcher in this pipeline (company resolution,
statement-table lookup, note-reference search) already works this way —
substring/keyword matching against printed text, never exact equality — so
this module follows the same convention rather than inventing exactness the
data does not support.

THE THRESHOLD
--------------
25 percentage points, matching the spec's own general default (section 6.2's
"Relationship divergence" trigger: paired lines diverging by more than
20-25% without a disclosed explanation) rather than inventing a separate
number per pair. A pair-specific override is supported but none is set today
— the spec does not give per-pair thresholds, only the one general rule.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_THRESHOLD_PCT = 25.0


@dataclass(frozen=True)
class LinePair:
    a_keywords: tuple[str, ...]
    b_keywords: tuple[str, ...]
    label: str  # what a divergence here suggests, per spec 9.3's own wording
    threshold_pct: float = DEFAULT_THRESHOLD_PCT


# Spec section 9.3, "Must-move-together expectation map". Not every row of
# that table has a clean two-line pairing usable for a chart (e.g. "PPE/gross
# block -> fixed-asset register" is a record, not a line item) — only the
# pairs where both sides are plausibly face-statement or note line items are
# included here.
PAIRS: tuple[LinePair, ...] = (
    LinePair(
        a_keywords=("revenue from operation", "revenue"),
        b_keywords=("trade receivable",),
        label="cut-off, collectability, fictitious revenue or delayed collections",
    ),
    LinePair(
        a_keywords=("property, plant", "gross block", "ppe"),
        b_keywords=("depreciation",),
        label="wrong useful lives, missed depreciation or miscapitalisation",
    ),
    LinePair(
        a_keywords=("borrowing",),
        b_keywords=("finance cost",),
        label="unrecorded interest, wrong current/non-current split or undisclosed default",
    ),
    LinePair(
        a_keywords=("inventor",),
        b_keywords=("cost of material", "cost of good", "purchase"),
        label="obsolescence, cut-off, valuation or physical verification issue",
    ),
    LinePair(
        a_keywords=("capital work", "cwip"),
        b_keywords=("capital advance",),
        label="stalled projects, capitalisation delay, idle funds/assets",
    ),
)


def _matches(label: str, keywords: tuple[str, ...]) -> bool:
    low = label.lower()
    return any(kw in low for kw in keywords)


def _row_for(rows: list[dict], keywords: tuple[str, ...]) -> dict | None:
    for row in rows:
        if _matches(row.get("label", ""), keywords):
            return row
    return None


def find_divergences(years_sorted: list[int], rows: list[dict]) -> list[dict]:
    """Divergence entries for one statement's already-captured rows.

    Pure function over already-computed `yoy` percentages — this never
    computes a financial figure, only compares two percentages the tool
    already derived server-side, which is what keeps it consistent with rule
    15 (the model never does the arithmetic; this does not do it either, it
    reads what `_compute_line_item_row` already produced).
    """
    out: list[dict] = []
    for pair in PAIRS:
        row_a = _row_for(rows, pair.a_keywords)
        row_b = _row_for(rows, pair.b_keywords)
        if row_a is None or row_b is None:
            continue
        yoy_a, yoy_b = row_a.get("yoy") or [], row_b.get("yoy") or []
        for i in range(1, len(years_sorted)):
            idx = i - 1
            if idx >= len(yoy_a) or idx >= len(yoy_b):
                continue
            _, pct_a = yoy_a[idx]
            _, pct_b = yoy_b[idx]
            if pct_a is None or pct_b is None:
                continue
            delta = abs(pct_a - pct_b)
            if delta > pair.threshold_pct:
                out.append({
                    "year": years_sorted[i],
                    "line_a": row_a["label"],
                    "line_b": row_b["label"],
                    "pct_a": pct_a,
                    "pct_b": pct_b,
                    "delta_pct": delta,
                    "label": pair.label,
                })
    return out


def annotate(captured: list[dict]) -> list[dict]:
    """`trend_capture.collect(...)`'s output, each entry augmented with its
    own `divergences` list. Never mutates the input entries."""
    out = []
    for entry in captured:
        divergences = find_divergences(entry["years_sorted"], entry["rows"])
        out.append({**entry, "divergences": divergences})
    return out
