"""Filters that say why they eliminated everything.

The bug this exists to prevent: a filter matches nothing, the tool returns an
empty result, and that is indistinguishable from a document which genuinely lacks
the thing. No exception, no log line, no failing test — and an answer that reads
as authoritative. It happened once already. ``_policy_headings`` required
``section_breadcrumb`` to contain "notes to", copied faithfully from the corpus
SQL, and on the real scans docling emits no such heading on the policy page. The
filter eliminated every candidate and ``get_accounting_policy_note`` reported no
policy note for a document that plainly has one.

**The pattern is not new to this codebase.** ``ComplianceTools._find_statement_tables``
hit the same class against the corpus and answered it with progressive
relaxation — five queries, each looser than the last, each commented with the
document that broke the tier above:

    Many entities prefix the statement title with their own name — "STEEL
    AUTHORITY OF INDIA LIMITED Standalone Balance Sheet" puts the keyword well
    past character 25, so every position-bounded query above misses it and the
    whole document reads as having no statements.

And the line those authors drew is the important one: ``NOT ILIKE
'%consolidated%'`` appears in **every tier, including the last resort**. Some
filters may relax; one may not. This module makes that distinction explicit
rather than leaving it to whoever writes the next filter:

``SAFETY``
    Never relaxed. Relaxing it would return a *wrong* answer rather than a less
    precise one — a consolidated note answering a standalone question. If it
    empties the set, the sieve stops and says so.

``PRECISION``
    Relaxed, through progressively weaker tiers. Tiers rather than an on/off
    switch because "drop the filter entirely" is usually too blunt: the policy
    numbering filter's second tier still excludes ``NOTE 27`` movement
    schedules, which is what the filter existed for.

A precision stage whose every tier empties the set is normally skipped -- losing
precision beats losing the answer. ``floor=True`` says otherwise: the weakest
tier is still binding, and if even that matches nothing the sieve blocks. That
flag is what keeps a relaxation from reintroducing the bug the filter prevented.
Without it, a document containing only ``NOTE 27 PROVISIONS`` fails the numbering
tier, fails the not-a-schedule tier, and then has the whole stage dropped -- which
hands back the movement schedule as an accounting policy. A test caught exactly
that.

Whatever happens is recorded. A caller with nothing to show can say why, and a
caller that relaxed can say it relaxed — which is what prompt rules 16, 17 and 24
already require of the model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

SAFETY = "safety"
PRECISION = "precision"

Predicate = Callable[[dict], bool]


@dataclass
class Stage:
    """One filter, with its fallbacks.

    ``tiers`` runs strictest-first; the first tier leaving a non-empty set wins.
    ``why`` is a sentence for a human, used when the stage relaxes or blocks, so
    keep it readable rather than terse.
    """

    name: str
    kind: str
    tiers: list[Predicate]
    why: str
    #: Optional label per tier, for the caveat text. Falls back to the index.
    tier_names: list[str] = field(default_factory=list)
    #: When every tier matches nothing: block (True) rather than skip the stage
    #: (False). Set it wherever the weakest tier is still a real requirement --
    #: dropping it would admit what the filter exists to exclude.
    floor: bool = False

    def tier_name(self, index: int) -> str:
        if 0 <= index < len(self.tier_names):
            return self.tier_names[index]
        return f"tier {index + 1}"


@dataclass
class SieveResult:
    rows: list[dict]
    #: (stage name, tier index) for each stage that had to fall back. A tier of
    #: -1 means every tier emptied the set and the stage was skipped entirely.
    relaxed: list[tuple[str, int]] = field(default_factory=list)
    #: The SAFETY stage that eliminated everything, if one did.
    blocked_by: Stage | None = None
    #: (stage name, surviving count) after each stage, for diagnostics.
    trace: list[tuple[str, int]] = field(default_factory=list)
    #: True when there was nothing to filter in the first place.
    no_candidates: bool = False

    def __bool__(self) -> bool:
        return bool(self.rows)

    @property
    def degraded(self) -> bool:
        return bool(self.relaxed)

    def caveat(self) -> str | None:
        """One sentence for the tool's output, or None when nothing is worth saying.

        Deliberately worded as an extraction limitation rather than a finding
        about the entity — the distinction prompt rule 24 turns on.
        """
        if self.no_candidates:
            return (
                "This document yielded no candidates for that lookup at all, which "
                "is a limitation of what could be recovered from the scan rather "
                "than evidence that the filing does not contain it."
            )
        if self.blocked_by is not None:
            return (
                f"Every candidate was excluded by the {self.blocked_by.name} check "
                f"({self.blocked_by.why}). Nothing is reported rather than "
                "reporting something from the wrong scope."
            )
        if self.relaxed:
            parts = []
            for name, tier in self.relaxed:
                parts.append(
                    f"the {name} filter matched nothing and was dropped"
                    if tier < 0 else
                    f"the {name} filter was relaxed to a looser match"
                )
            # Neutral about whether anything was found: this caveat is attached
            # to successful and empty lookups alike, and "the passage below"
            # reads as a promise when there is no passage below.
            return (
                "NOTE: " + "; ".join(parts) + ". This lookup therefore ran with a "
                "weaker filter than usual, so confirm any passage it returned is "
                "the one you wanted, and treat a miss as inconclusive."
            )
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "rows": len(self.rows),
            "relaxed": [{"stage": n, "tier": t} for n, t in self.relaxed],
            "blocked_by": self.blocked_by.name if self.blocked_by else None,
            "trace": [{"stage": n, "surviving": c} for n, c in self.trace],
            "no_candidates": self.no_candidates,
        }


def sieve(rows: list[dict], stages: list[Stage]) -> SieveResult:
    """Apply stages in order, relaxing precision filters rather than emptying out."""
    if not rows:
        return SieveResult(rows=[], no_candidates=True)

    current = list(rows)
    result = SieveResult(rows=current)

    for stage in stages:
        survivors = None
        used_tier = 0
        for index, tier in enumerate(stage.tiers):
            candidate = [r for r in current if tier(r)]
            if candidate:
                survivors, used_tier = candidate, index
                break

        if survivors is None:
            # Every tier eliminates everything.
            if stage.kind == SAFETY or stage.floor:
                result.rows = []
                result.blocked_by = stage
                result.trace.append((stage.name, 0))
                return result
            # A precision filter that cannot be satisfied is skipped, leaving the
            # set as it was. Losing precision beats losing the answer -- unless
            # the stage declared a floor, handled above.
            result.relaxed.append((stage.name, -1))
            result.trace.append((stage.name, len(current)))
            continue

        if used_tier > 0:
            result.relaxed.append((stage.name, used_tier))
        current = survivors
        result.trace.append((stage.name, len(current)))

    result.rows = current
    return result


def first(rows: list[dict], stages: list[Stage], key=None) -> tuple[dict | None, SieveResult]:
    """Sieve, then take the best row by ``key``. Returns the row and the trace.

    Separated from ``sieve`` because most callers want one anchor rather than a
    set, and the trace has to survive alongside it — a bare ``None`` is exactly
    the silent empty this module exists to eliminate.
    """
    result = sieve(rows, stages)
    if not result.rows:
        return None, result
    ordered = sorted(result.rows, key=key) if key else result.rows
    return ordered[0], result
