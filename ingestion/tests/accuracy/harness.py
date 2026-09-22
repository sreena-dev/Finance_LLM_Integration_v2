"""Scoring an extraction against figures a human read off the printed page.

This is the scoreboard the pipeline work is judged by. Nothing here runs the
pipeline -- see ``runner.py`` for that -- so the scoring rules can be unit
tested in seconds without docling, torch or a reachable vision model.

**Five outcomes, and the fourth (MISSING) is the historical point; the fifth
(RECOVERED) is this feature's own acceptance criterion.**

``CORRECT``   the emitted figure matches, within ``verify._tolerance``.
``WRONG``     a figure was emitted and it differs. The only outcome that makes
              the tool dangerous rather than merely disappointing: an auditor
              acting on a wrong number is worse off than one told nothing.
              This is the metric that gates every change.
``WITHHELD``  an ``[unreadable: ...]`` marker sits where the figure should be.
              A miss, but a *disclosed* one -- the system said so.
``RECOVERED`` a ``[recovered ...]`` marker sits where the figure should be --
              a second-read figure the arithmetic did NOT confirm, shown but
              not vouched for. Scored against the expectation exactly like
              CORRECT/WRONG would be (see ``Result.found`` and
              ``Scorecard.recovered_mismatched()``), because "recovered and
              right" vs "recovered and wrong" -- not merely the recovery
              count -- is what says whether this feature is trustworthy. An
              arithmetic-PROMOTED recovery carries no marker at all and scores
              as plain ``CORRECT``/``WRONG``, exactly as a cleanly-read figure
              would: the whole point of promotion is that nothing distinguishes
              it from one.
``MISSING``   no row, no table, nothing. **Silent** loss.

Keeping ``MISSING`` distinct from ``WITHHELD`` is not bookkeeping. Every worst
bug found in this corpus produces ``MISSING``, not ``WITHHELD``: a born-digital
filing that extracted zero tables while reporting a clean conversion, four
scanned pages of an annual report that routed down the no-OCR path, a row
swallowed by a neighbour when TableFormer merged two grid rows into one.
Collapsing the two would hide exactly the class of failure that most needs
finding, because a document can score perfectly on ``WRONG`` by emitting almost
nothing at all.

**Only ``verified: true`` expectations score.** An expectation file can be
bootstrapped from the pipeline's own output (``runner.py --propose``), which is
far cheaper than transcribing a filing by hand -- but a proposal is a record of
what the system *did*, not of what the page *says*. Until a human has checked it
against the printed statement and flipped the flag, it is excluded. Otherwise
the harness certifies today's bugs as correct and locks them in, which is the
precise failure the sibling ``tests/fixtures/README.md`` describes.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.numbers import parse_cell                       # noqa: E402
from app.tables import parse_markdown_tables             # noqa: E402
from app.verify import _tolerance                        # noqa: E402
from app.vlm_read import _normalise_label                # noqa: E402


class Outcome(str, Enum):
    CORRECT = "CORRECT"
    WRONG = "WRONG"
    WITHHELD = "WITHHELD"
    RECOVERED = "RECOVERED"
    MISSING = "MISSING"


#: How close a row label must be to count as the row we meant. Reuses the same
#: edit-distance approach -- and very nearly the same threshold -- as
#: ``vlm_read._row_score``, which was tuned against OCR corruption in this exact
#: corpus ("Capltal work in prograss"). Slightly looser here because the
#: expectation is typed by a human from the printed page and will not carry the
#: same OCR damage the extracted label does.
_MIN_LABEL_SCORE = 0.70

#: Marker written by ``verify.redact`` in place of a figure it will not vouch for.
_WITHHELD_MARKER = "[unreadable:"
#: Marker for a figure recovered by a second read but NOT confirmed by the
#: column's own arithmetic (see ``verify.py``'s RECOVERY section). Deliberately
#: does NOT contain "unreadable" -- that is the whole design (see
#: ``tools_fs.py``'s ``_CELL_MARKER_RE``) -- so it needs its own constant here
#: too; a cell matching this must never be counted as WITHHELD or fall through
#: to CORRECT/WRONG scoring as if it were a plain number.
_RECOVERED_MARKER = "[recovered"
#: The recovered figure's own text, exactly as `models.CellFinding.marker`
#: writes it: "[recovered <text>; second read, confidence ...". Extracted here
#: only for SCORING -- comparing what the second reader said against the
#: expectation -- never to promote it; promotion is verify.py's job alone,
#: and by the time a figure reaches a plain cell it carries no marker at all.
_RECOVERED_VALUE_RE = re.compile(r"^\[recovered\s+(.+?);")


@dataclass
class Expectation:
    """One figure a human read off the printed page."""

    row_label: str
    value: float
    page: int | None = None
    column_label: str | None = None
    verified: bool = False
    note: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Expectation":
        return cls(
            row_label=str(raw["row_label"]),
            value=float(raw["value"]),
            page=raw.get("page"),
            column_label=raw.get("column_label"),
            verified=bool(raw.get("verified", False)),
            note=raw.get("note"),
        )


@dataclass
class Result:
    """What the harness found for one expectation."""

    expectation: Expectation
    outcome: Outcome
    found: float | None = None
    found_raw: str | None = None
    detail: str | None = None


@dataclass
class Scorecard:
    results: list[Result] = field(default_factory=list)

    def count(self, outcome: Outcome) -> int:
        return sum(1 for r in self.results if r.outcome is outcome)

    @property
    def scored(self) -> int:
        return len(self.results)

    def as_dict(self) -> dict[str, int]:
        return {o.value: self.count(o) for o in Outcome}

    def wrong(self) -> list[Result]:
        return [r for r in self.results if r.outcome is Outcome.WRONG]

    def recovered(self) -> list[Result]:
        return [r for r in self.results if r.outcome is Outcome.RECOVERED]

    def recovered_mismatched(self) -> list[Result]:
        """Recovered-but-WRONG: the `[recovered ...]` marker's own figure
        does not match the expected value. THE acceptance measurement for
        the recovery feature -- see the module docstring's RECOVERED entry.
        A non-empty result here means the second reader is being shown to a
        human at a confidence band that does not track its actual accuracy,
        which is exactly the signal that should tighten
        `models.derive_confidence`'s bands, not just this scoreboard."""
        return [
            r for r in self.recovered()
            if r.found is not None and abs(r.found - r.expectation.value) > _tolerance(r.expectation.value)
        ]


#: How much of the LONGER label a containment match must cover before it counts.
#: Without this, containment is a trapdoor: "Income" is a substring of "Balance
#: being excess of Expenditure over Income (B-A)", so a short section-header row
#: scored 0.95 against a long expectation and the harness read the blank header
#: row instead of the figure. The same collision -- "current assets" inside
#: "non-current assets" -- has bitten this codebase repeatedly. A bare
#: substring test cannot express "is nearly all of", which is what was meant.
_MIN_CONTAINMENT_COVERAGE = 0.6


def _label_score(wanted: str, candidate: str) -> float:
    """Similarity between an expected row label and an extracted one."""
    a, b = _normalise_label(wanted), _normalise_label(candidate)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    # Containment matters here in a way it does not for reader-vs-reader
    # matching: a human writes "Revenue from operations" while the statement
    # prints "I. Revenue from operations" with an enumerator the extraction
    # faithfully kept. But it only means anything when the shorter label is
    # MOST of the longer one -- otherwise any short label matches any row that
    # happens to mention it.
    if (a in b or b in a) and min(len(a), len(b)) / max(len(a), len(b)) >= _MIN_CONTAINMENT_COVERAGE:
        return max(0.95, SequenceMatcher(None, a, b).ratio())
    return SequenceMatcher(None, a, b).ratio()


def _tables_for(result: dict[str, Any], page: int | None) -> list[dict[str, Any]]:
    tables = result.get("tables") or []
    if page is None:
        return list(tables)
    on_page = [t for t in tables if t.get("page_ocr_start") == page]
    # A page number is a hint, not a constraint: a table split across a page
    # break is emitted once, against the page it started on, so an expectation
    # naming the continuation page would find nothing. Fall back rather than
    # score a MISSING that is really a page-numbering artefact.
    return on_page or list(tables)


def _column_index(table, column_label: str | None) -> int | None:
    """The value column matching ``column_label``, or None to mean 'try all'."""
    if not column_label:
        return None
    wanted = _normalise_label(column_label)
    if not wanted:
        return None
    best, best_score = None, 0.0
    for c in table.value_cols:
        header = _normalise_label(table.column_name(c))
        if not header:
            continue
        if header == wanted or wanted in header or header in wanted:
            return c
        score = SequenceMatcher(None, wanted, header).ratio()
        if score > best_score:
            best, best_score = c, score
    return best if best_score >= 0.6 else None


def locate(result: dict[str, Any], expectation: Expectation) -> Result:
    """Score one expectation against one extracted document."""
    best_row = None          # (score, table, row_index)
    for record in _tables_for(result, expectation.page):
        markdown = record.get("table_md") or ""
        if not markdown.strip():
            continue
        for table in parse_markdown_tables(markdown, record.get("page_ocr_start") or 0):
            for r in range(len(table.rows)):
                score = _label_score(expectation.row_label, table.label(r))
                if score >= _MIN_LABEL_SCORE and (best_row is None or score > best_row[0]):
                    best_row = (score, table, r)

    if best_row is None:
        return Result(expectation, Outcome.MISSING,
                      detail="no row matching that label was extracted")

    _score, table, row = best_row
    if not table.value_cols:
        return Result(expectation, Outcome.MISSING,
                      detail="the row was found but the table has no value columns")

    def _raw(c: int) -> str:
        return table.rows[row][c] if c < len(table.rows[row]) else ""

    target = _column_index(table, expectation.column_label)

    # Pass 1 -- an exact hit in ANY value column is CORRECT, but ONLY for an
    # expectation that did not name a column. Generous on purpose there: it
    # absorbs a column order the expectation did not anticipate, which is a
    # harness artefact rather than an extraction defect.
    #
    # Deliberately NOT done when a column IS named, because that would hide a
    # column shift -- the extraction putting the prior year's figure under the
    # current year's heading. Searching every column for the expected value
    # would find it sitting in the wrong place and call that correct, which is
    # the one thing a harness must never do. A named column is scored where it
    # was named, and nowhere else.
    if target is None:
        for c in table.value_cols:
            cell = parse_cell(_raw(c))
            if cell.value is not None and abs(cell.value - expectation.value) <= _tolerance(expectation.value):
                return Result(expectation, Outcome.CORRECT, cell.value, _raw(c))
        # Schedule III prints the current year first, so the leftmost value
        # column is what an unqualified expectation means.
        target = table.value_cols[0]

    raw = _raw(target)
    if _RECOVERED_MARKER in (raw or ""):
        m = _RECOVERED_VALUE_RE.match((raw or "").strip())
        recovered_cell = parse_cell(m.group(1)) if m else None
        recovered_value = recovered_cell.value if recovered_cell else None
        if recovered_value is None:
            detail = "recovered by a second read, but its own figure could not be parsed"
        elif abs(recovered_value - expectation.value) <= _tolerance(expectation.value):
            detail = "recovered by a second read, not arithmetic-confirmed, and MATCHES the expected figure"
        else:
            detail = (
                f"recovered by a second read, not arithmetic-confirmed, and does NOT match "
                f"the expected figure (expected {expectation.value}, recovered {recovered_value})"
            )
        return Result(expectation, Outcome.RECOVERED, found=recovered_value, found_raw=raw, detail=detail)
    if _WITHHELD_MARKER in (raw or ""):
        return Result(expectation, Outcome.WITHHELD, found_raw=raw,
                      detail="the figure was extracted but withheld as unreadable")

    cell = parse_cell(raw)
    if cell.value is None:
        return Result(expectation, Outcome.MISSING, found_raw=raw or None,
                      detail=f"the row was found but {table.column_name(target)!r} carried no figure")

    if abs(cell.value - expectation.value) <= _tolerance(expectation.value):
        return Result(expectation, Outcome.CORRECT, cell.value, raw)

    return Result(expectation, Outcome.WRONG, cell.value, raw,
                  detail=f"expected {expectation.value}, extracted {cell.value} "
                         f"from {table.column_name(target)!r}")


def score(result: dict[str, Any], expectations: list[Expectation]) -> Scorecard:
    """Score every VERIFIED expectation. Unverified ones are ignored entirely."""
    card = Scorecard()
    for expectation in expectations:
        if not expectation.verified:
            continue
        card.results.append(locate(result, expectation))
    return card


def propose(result: dict[str, Any], limit_per_table: int = 12) -> list[dict[str, Any]]:
    """Every figure the pipeline emitted, as candidate expectations.

    Written with ``verified: false`` so it scores nothing until a human has
    checked it against the printed page. This exists to save typing, not to
    save reading -- see the module docstring.
    """
    out: list[dict[str, Any]] = []
    for record in result.get("tables") or []:
        markdown = record.get("table_md") or ""
        if not markdown.strip():
            continue
        page = record.get("page_ocr_start")
        for table in parse_markdown_tables(markdown, page or 0):
            taken = 0
            for r in range(len(table.rows)):
                label = (table.label(r) or "").strip()
                if not label or taken >= limit_per_table:
                    continue
                for c in table.value_cols:
                    raw = table.rows[r][c] if c < len(table.rows[r]) else ""
                    # A recovered-but-unconfirmed marker must never be
                    # proposed as ground truth either -- a human ticking
                    # `verified: true` on it would enshrine an unconfirmed
                    # second-read guess as the expected figure. An
                    # arithmetic-PROMOTED recovery carries no marker at all
                    # and is correctly proposed here like any other figure.
                    if _WITHHELD_MARKER in (raw or "") or _RECOVERED_MARKER in (raw or ""):
                        continue
                    cell = parse_cell(raw)
                    if cell.value is None:
                        continue
                    out.append({
                        "row_label": label,
                        "column_label": table.column_name(c),
                        "page": page,
                        "value": cell.value,
                        "verified": False,
                    })
                    taken += 1
                    break
    return out
