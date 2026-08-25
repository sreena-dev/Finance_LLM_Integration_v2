"""
§11 risk-prioritisation engine — transparent, reproducible, and never speaking alone.

THREE RULES FROM THE SPECIFICATION SHAPE THIS MODULE
----------------------------------------------------
§11.2  "Combine the dimensions transparently. The combination method is disclosed in the
       output; the score is reproducible from the stated inputs."  -> every dimension is
       scored individually, carries its own basis, and is rendered.

§11.2  "Apply the by-nature override. A risk material by nature or context — regularity,
       propriety or public interest — is elevated even when quantitatively small."  -> the
       override is a hard constraint on the ordering, not a bonus added to a score, because
       a bonus can be outweighed and an override cannot.

§11.2  "Never let the score speak alone."  -> `Priority.reasoning` states why the cluster
       ranks where it does AND why the one below it ranks lower. A ranking without that
       sentence is not a valid output (§16, black-box scoring is prohibited).

UNAVAILABLE DIMENSIONS ARE NOT ZERO
-----------------------------------
Several §11.1 dimensions need figures (magnitude), a business profile (business
significance) or an evaluated diagnostic (confidence). Where one cannot be assessed it
scores `None` and is excluded from the mean — it does not score zero. Scoring it zero
would silently penalise a cluster for the system's own missing data, which is the
prioritisation equivalent of fabricating a figure (P4: never fabricate a zero).

The count of assessed dimensions is published alongside the score, so a rank resting on
three dimensions is visibly weaker than one resting on nine.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any

from typing import TYPE_CHECKING

from . import planning as PL
from .model import ClusterPackage, FIRED, HIGH, MEDIUM, LOW

if TYPE_CHECKING:                       # runtime import would be circular via assemble
    from .interactions import InteractionMap

# §11.1 — the nine dimensions, in the specification's order.
MAGNITUDE = "magnitude"
LIKELIHOOD = "likelihood"
PERSISTENCE = "persistence"
BUSINESS_SIGNIFICANCE = "business_significance"
MATERIALITY = "materiality"
CORROBORATION = "corroboration_strength"
TECHNIQUE_DIVERSITY = "technique_diversity"
DIAGNOSTIC_CONFIDENCE = "diagnostic_confidence"
JUDGEMENT_EXPOSURE = "management_judgement_exposure"
# §10.2 — "a reinforcing interaction RAISES the cluster's planning priority; an offsetting
# one is stated and MODERATES it." That is an instruction to the ranking engine, and the
# only honest place to obey it is as a scored dimension. Applied instead as a multiplier on
# the finished score it would be invisible in the output, and §11.2 requires the score to
# be reproducible from the stated inputs — a reader cannot reproduce a factor they cannot
# see. Scored here, the interaction shows up in the dimension table with its own basis
# naming the counterpart theme, and a reader can disagree with it specifically.
INTERACTION_PRESSURE = "interaction_pressure"

DIMENSIONS = (
    MAGNITUDE, LIKELIHOOD, PERSISTENCE, BUSINESS_SIGNIFICANCE, MATERIALITY,
    CORROBORATION, TECHNIQUE_DIVERSITY, DIAGNOSTIC_CONFIDENCE, JUDGEMENT_EXPOSURE,
    INTERACTION_PRESSURE,
)

MAX = 3          # every dimension scores 0-3, or None when it cannot be assessed

COMBINATION_METHOD = (
    "Each dimension scores 0-3 or is marked not assessed. The cluster score is the mean of "
    "the ASSESSED dimensions only, expressed out of 100; dimensions that could not be "
    "assessed are excluded rather than scored zero. Clusters material by nature are then "
    "ordered above those that are not, whatever their scores (§11.2 override). Ties break "
    "on the number of assessed dimensions, then on cluster id, so the order is reproducible. "
    "Risk interactions (§10.2) enter through the interaction_pressure dimension and nowhere "
    "else: they are scored in the open alongside the other dimensions, never applied as a "
    "multiplier on the result, so a rank that moved because two themes reinforce each other "
    "says so on its face."
)

_LEVEL = {HIGH: 3, MEDIUM: 2, LOW: 1}


@dataclass(frozen=True)
class DimensionScore:
    name: str
    score: int | None
    basis: str

    @property
    def assessed(self) -> bool:
        return self.score is not None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Priority:
    cluster_id: str
    rank: int
    score: float | None
    dimensions: tuple[DimensionScore, ...]
    by_nature: bool
    reasoning: str = ""

    @property
    def assessed_count(self) -> int:
        return sum(1 for d in self.dimensions if d.assessed)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["dimensions"] = [x.to_dict() for x in self.dimensions]
        d["assessed_count"] = self.assessed_count
        d["combination_method"] = COMBINATION_METHOD
        return d


def _score_cluster(pkg: ClusterPackage, *, optional_inputs: tuple[str, ...],
                   business_profile_formed: bool,
                   interactions: "InteractionMap | None" = None,
                   ) -> tuple[DimensionScore, ...]:
    pc = PL.BY_ID[pkg.cluster_id]
    fired = [s for s in pkg.contributing_signals if s.status == FIRED]

    # --- magnitude: needs figures. -------------------------------------------------
    mag = DimensionScore(
        MAGNITUDE, None,
        "Not assessed: the size of the amounts and movements involved is not available "
        "without the entity-year panel.",
    )

    # --- likelihood: how likely the signal reflects a real issue (qualitative). -----
    if fired:
        sev = max(_LEVEL.get(s.severity or LOW, 1) for s in fired)
        like = DimensionScore(
            LIKELIHOOD, sev,
            f"{len(fired)} signal(s) present, the strongest carrying severity "
            f"{max((s.severity for s in fired), key=lambda x: _LEVEL.get(x or LOW, 1))}. "
            f"Qualitative; no probability is stated (§17.2).",
        )
    else:
        like = DimensionScore(LIKELIHOOD, None, "Not assessed: no signal was evaluated.")

    # --- persistence: single-year event or across the series? ----------------------
    trend_fired = [s for s in fired if _is_trend(s.signal_id)]
    if fired:
        persist = DimensionScore(
            PERSISTENCE,
            3 if len(trend_fired) >= 2 else (2 if trend_fired else 1),
            f"{len(trend_fired)} of {len(fired)} present signals are multi-year trends rather "
            f"than point-in-time states.",
        )
    else:
        persist = DimensionScore(PERSISTENCE, None, "Not assessed: no signal was evaluated.")

    # --- business significance: needs the business profile (§5). --------------------
    bus = DimensionScore(
        BUSINESS_SIGNIFICANCE, None,
        "Not assessed: no business understanding was formed, so how central this area is to "
        "the entity's value drivers is unknown (§5.3, ROADMAP M5)."
        if not business_profile_formed else
        "Not assessed: the sector overlay that maps a business model to its material areas "
        "is not built (ROADMAP M2).",
    )

    # --- materiality: the outcome of the §10.6 filter. ------------------------------
    mat = DimensionScore(
        MATERIALITY,
        3 if pc.by_nature else 2,
        (pc.by_nature_basis if pc.by_nature else
         f"Admitted on {', '.join(sorted(pc.admits_on))}; no by-nature elevation applies. "
         f"The value dimension itself needs figures and is not assessed."),
    )

    # --- corroboration strength (§12). ---------------------------------------------
    corr = DimensionScore(
        CORROBORATION,
        1 if not optional_inputs else 2,
        "Standalone: no trial-balance, financial-statement or auditor's-report analysis was "
        "supplied. §1.3 — a standalone report carries the same confidence in its own "
        "reasoning; only this dimension of the ranking is affected."
        if not optional_inputs else
        f"Corroboration inputs available: {', '.join(optional_inputs)}. Whether they actually "
        f"corroborate this cluster is settled by the integration framework (ROADMAP M12).",
    )

    # --- technique diversity: do independent diagnostics point the same way? --------
    # §11.1 asks "whether more than one INDEPENDENT DIAGNOSTIC points to the same underlying
    # issue" — that is a count of distinct diagnostics, not of layers. Ageing CWIP and a
    # rising non-current-other share are two independent measurements of the same concern
    # and both sit in Layer 2; scoring by layer counted them as one, and understated exactly
    # the weak-signals-combine case §10.1 exists to catch. Layer spread is reported in the
    # basis as supporting context, not used as the score.
    if fired:
        layers = {_layer(s.signal_id) for s in fired}
        div = DimensionScore(
            TECHNIQUE_DIVERSITY, min(MAX, len(fired)),
            f"{len(fired)} independent diagnostic(s) point to this theme, drawn from "
            f"{len(layers)} diagnostic layer(s). §10.1 — a multi-signal cluster is far more "
            f"significant for planning than a single weak signal.",
        )
    else:
        div = DimensionScore(TECHNIQUE_DIVERSITY, None,
                             "Not assessed: no signal was evaluated.")

    # --- diagnostic confidence (App H) — never inferred from severity. --------------
    conf = DimensionScore(
        DIAGNOSTIC_CONFIDENCE,
        _LEVEL[pkg.diagnostic_confidence] if pkg.diagnostic_confidence else None,
        f"Diagnostic confidence {pkg.diagnostic_confidence}." if pkg.diagnostic_confidence
        else "Not assessed: no confidence was formed, because no diagnostic ran. Confidence "
             "is never inferred from severity (§4.4).",
    )

    # --- management-judgement exposure: a property of the area, always available. ---
    judge = DimensionScore(
        JUDGEMENT_EXPOSURE, _LEVEL[pc.management_judgement_exposure],
        f"{pc.management_judgement_exposure} exposure to estimates and management judgement.",
    )

    # --- interaction pressure (§10.2) — reinforcement raises, offset moderates. ------
    inter = _interaction_dimension(pkg.cluster_id, interactions)

    return (mag, like, persist, bus, mat, corr, div, conf, judge, inter)


def _n(count: int, noun: str) -> str:
    """"1 interaction" / "3 interactions". The dimension bases are read by auditors, and
    "1 interaction(s)" is the tell of a number nobody looked at."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _interaction_dimension(cluster_id: str,
                           interactions: "InteractionMap | None") -> DimensionScore:
    """Score §10.2 from the RESOLVED matrix — never from the declared one.

    The distinction is the whole point. Every cluster in Appendix D has declared
    interactions; scoring those would hand each cluster a constant, which ranks nothing.
    What ranks is whether the counterpart theme was ACTUALLY raised in this report.
    """
    from . import clusters as CL
    from .interactions import LATENT

    declared = CL.interactions_for(cluster_id)
    if not declared:
        return DimensionScore(
            INTERACTION_PRESSURE, None,
            "Not assessed: the §10.2 matrix declares no interaction touching this theme, so "
            "there is nothing to resolve. This is a property of the diagnostic design, not "
            "a gap in the entity's data.",
        )
    if interactions is None:
        return DimensionScore(
            INTERACTION_PRESSURE, None,
            f"Not assessed: {_n(len(declared), 'interaction')} declared for this theme, but "
            f"the matrix was not resolved for this run, so whether any is live is unknown. "
            f"Scored as unassessed rather than as absent (P4 — never fabricate a zero).",
        )

    mine = interactions.for_cluster(cluster_id)
    reinforcing = [e for e in mine if e.live_reinforcing]
    offsetting = [e for e in mine if e.live_offsetting]
    latent = [e for e in mine if e.state == LATENT]

    if len(reinforcing) >= 2:
        score = 3
        why = (f"{len(reinforcing)} reinforcing interactions are live: "
               f"{', '.join(e.counterpart for e in reinforcing)} are all raised alongside "
               f"this theme. §10.1 — several diagnostics converging on one underlying issue "
               f"is far more significant for planning than any of them alone.")
    elif reinforcing:
        e = reinforcing[0]
        score = 2
        why = (f"One reinforcing interaction is live: {e.counterpart} "
               f"({e.counterpart_theme}) is also raised. {e.mechanism}")
    else:
        score = 1
        declared_txt = _n(len(declared), "declared interaction")
        why = (f"No reinforcing interaction is live: of the {declared_txt} touching this "
               f"theme, none has a raised counterpart. The theme stands on its own "
               f"diagnostics.")

    if offsetting:
        before, names = score, ", ".join(e.counterpart for e in offsetting)
        score = max(1, score - 1)
        moved = (f"{before}/3 reduced to {score}/3" if score < before else
                 f"already at the floor of {score}/3, so the offset is recorded and "
                 f"changes no score — it cannot push a live theme below the base level")
        why += (f" Moderated by {_n(len(offsetting), 'live offsetting interaction')} "
                f"({names}): {moved}. §10.2 — an offset is STATED and moderates; it never "
                f"clears the theme, because the thing doing the offsetting is itself an "
                f"exposure the audit team should test in its own right.")

    if latent:
        why += (f" {_n(len(latent), 'further interaction')} latent "
                f"({', '.join(e.counterpart for e in latent)} not checked) and NOT scored "
                f"here by design — an unrun diagnostic must not add weight to a rank.")

    return DimensionScore(INTERACTION_PRESSURE, score, why)


def _is_trend(signal_id: str) -> bool:
    from . import signals as SG
    return SG.BY_ID[signal_id].is_trend


def _layer(signal_id: str) -> int:
    from . import signals as SG
    return SG.BY_ID[signal_id].layer


def rank(
    packages: tuple[ClusterPackage, ...],
    *,
    optional_inputs: tuple[str, ...] = (),
    business_profile_formed: bool = False,
    shortlist_size: int | None = None,
    interactions: "InteractionMap | None" = None,
) -> tuple[Priority, ...]:
    """Rank the raised clusters. Clusters that were not raised are never ranked.

    `interactions` is the RESOLVED §10.2 matrix (`interactions.resolve`). Omitting it does
    not silently drop §10.2 — the interaction dimension reports itself unassessed, and the
    published `assessed_count` falls, so a ranking produced without it is visibly weaker
    rather than quietly different.
    """
    from .model import RAISED

    raised = [p for p in packages if p.status == RAISED]
    scored: list[Priority] = []
    for pkg in raised:
        dims = _score_cluster(pkg, optional_inputs=optional_inputs,
                              business_profile_formed=business_profile_formed,
                              interactions=interactions)
        assessed = [d.score for d in dims if d.assessed]
        score = round(100.0 * sum(assessed) / (MAX * len(assessed)), 1) if assessed else None
        scored.append(Priority(
            cluster_id=pkg.cluster_id, rank=0, score=score, dimensions=dims,
            by_nature=PL.BY_ID[pkg.cluster_id].by_nature,
        ))

    # §11.2 override: by-nature clusters order above the rest, whatever the score.
    scored.sort(key=lambda p: (
        0 if p.by_nature else 1,
        -(p.score or 0.0),
        -p.assessed_count,
        p.cluster_id,
    ))

    for i, p in enumerate(scored):
        p.rank = i + 1
        p.reasoning = _explain(p, scored[i + 1] if i + 1 < len(scored) else None)

    if shortlist_size is not None:
        scored = scored[:shortlist_size]
    return tuple(scored)


# ---- §11.2 capacity adjustment ----------------------------------------------------

@dataclass(frozen=True)
class BelowLine:
    """A cluster that did not fit the stated capacity. Never silently dropped."""
    cluster_id: str
    rank: int
    effort: int
    reason: str
    by_nature: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


CAPACITY_METHOD = (
    "The shortlist is filled in rank order against the stated capacity, in the relative "
    "effort units of planning.EFFORT_SCALE. Filling stops at the first item that does not "
    "fit: lower-ranked items are NOT promoted past it to use up the remainder, because "
    "reordering by what fits would contradict the ranking it was just given. Any unused "
    "capacity is reported so the team can decide what to add."
)


def apply_capacity(
    priorities: tuple[Priority, ...],
    capacity: int | None,
) -> tuple[tuple[Priority, ...], tuple[BelowLine, ...], dict[str, Any]]:
    """Size the shortlist to the audit team's capacity (§11.2).

    Returns (shortlist, below_the_line, arithmetic). With no capacity stated, everything
    ranked is on the shortlist — the FDR does not invent a capacity it was not given.
    """
    total = sum(_effort(p.cluster_id) for p in priorities)
    if capacity is None:
        return priorities, (), {
            "capacity": None, "required": total, "used": total, "unused": 0,
            "basis": "No capacity was stated, so no item was excluded. §11.2's "
                     "proportionality is the audit team's to apply.",
            "method": CAPACITY_METHOD,
        }

    used, shortlist, below = 0, [], []
    blocked_by: str | None = None
    for p in priorities:
        e = _effort(p.cluster_id)
        if blocked_by is None and used + e <= capacity:
            used += e
            shortlist.append(p)
            continue

        if blocked_by is None:
            blocked_by = p.cluster_id
            why = (f"Did not fit: effort {e} against {capacity - used} unit(s) remaining of a "
                   f"capacity of {capacity}.")
        elif e <= capacity - used:
            why = (f"Blocked behind {blocked_by}, which was ranked higher and did not fit. "
                   f"Its own effort of {e} would have fitted the {capacity - used} unit(s) "
                   f"remaining, but promoting it past a higher-ranked theme would reorder the "
                   f"shortlist by what is convenient to fit rather than by priority.")
        else:
            why = (f"Ranked below {blocked_by}, which did not fit; its own effort of {e} also "
                   f"exceeds the {capacity - used} unit(s) remaining.")

        below.append(BelowLine(
            cluster_id=p.cluster_id, rank=p.rank, effort=e, by_nature=p.by_nature,
            reason=(
                f"Below the line at capacity {capacity}. {why} Not a judgement that the theme "
                f"is unimportant — it is what this capacity excludes, and the team can raise "
                f"the capacity or displace something above it."
                + (" MATERIAL BY NATURE AND EXCLUDED BY CAPACITY: this needs an explicit human "
                   "decision, because §11.2 elevates regularity, propriety and public-interest "
                   "matters regardless of size." if p.by_nature else "")
            ),
        ))
    return tuple(shortlist), tuple(below), {
        "capacity": capacity, "required": total, "used": used,
        "unused": capacity - used,
        "basis": f"{len(shortlist)} of {len(priorities)} ranked themes fit a capacity of "
                 f"{capacity}; {used} unit(s) used, {capacity - used} left unallocated.",
        "method": CAPACITY_METHOD,
    }


def _effort(cluster_id: str) -> int:
    return PL.BY_ID[cluster_id].effort_weight


def _explain(p: Priority, below: Priority | None) -> str:
    """§11.2 — why it ranks here, and why the next one ranks lower. Never a bare score."""
    parts = [
        f"Ranks {p.rank}. Score {p.score if p.score is not None else 'not computable'} "
        f"from {p.assessed_count} of {len(DIMENSIONS)} priority dimensions; the remaining "
        f"{len(DIMENSIONS) - p.assessed_count} could not be assessed and were excluded rather "
        f"than scored zero."
    ]
    if p.by_nature:
        mat = next(d for d in p.dimensions if d.name == MATERIALITY)
        parts.append(f"Elevated by the §11.2 by-nature override: {mat.basis}")

    top = sorted((d for d in p.dimensions if d.assessed),
                 key=lambda d: -(d.score or 0))[:2]
    if top:
        parts.append("Ranked mainly on " + "; ".join(f"{d.name} ({d.score}/3) — {d.basis}"
                                                     for d in top))

    if below is not None:
        if p.by_nature and not below.by_nature:
            parts.append(
                f"{below.cluster_id} ranks lower because it is not material by nature, so the "
                f"override does not reach it — not because its diagnostics are weaker."
            )
        else:
            diff = _largest_gap(p, below)
            parts.append(
                f"{below.cluster_id} ranks lower" +
                (f" chiefly on {diff}." if diff else
                 " on the combined dimensions; no single dimension separates them.")
            )
    else:
        parts.append("Lowest-ranked item in this shortlist; nothing below it to compare against.")

    if p.assessed_count < 5:
        parts.append(
            "This ranking rests on fewer than five assessed dimensions and should be read as "
            "provisional. It is not a judgement that the other dimensions are favourable."
        )
    parts.append("A candidate priority for the audit team's decision, not a decided plan (§10.5).")
    return " ".join(parts)


def _largest_gap(a: Priority, b: Priority) -> str:
    best, gap = "", 0
    bm = {d.name: d for d in b.dimensions}
    for d in a.dimensions:
        o = bm.get(d.name)
        if d.assessed and o is not None and o.assessed:
            g = (d.score or 0) - (o.score or 0)
            if g > gap:
                best, gap = f"{d.name} ({d.score}/3 against {o.score}/3)", g
    return best
