"""
§10.2 — resolving the declared risk-interaction matrix against what a run actually found.

WHY THIS IS A SEPARATE STEP FROM THE DECLARATION
------------------------------------------------
`clusters.py` declares which cluster pairs CAN interact. That is a permanent property of
the diagnostic design. Whether a pair DOES interact in a given report is a property of the
run, and the two must not be rendered as the same thing.

Before this module existed, every cluster carried the text "reinforces RC-FUND" whether or
not RC-FUND had been raised — and it carried it even when the cluster itself was not
raised. An interaction between two things, only one of which is present, is not an
interaction; printing it as one inflates the apparent weight of a theme using evidence
that was never found. §10.2's own example is precise about this: weak operating cash flow
COMBINED WITH rising leverage AND a receivables build-up. The conjunction is the claim.

THREE STATES, BECAUSE "NOT LIVE" MEANS TWO DIFFERENT THINGS
-----------------------------------------------------------
  LIVE     both clusters are raised. The interaction is present, and §10.2's consequence
           applies: reinforcing raises the priority, offsetting moderates it.
  LATENT   this cluster is raised and the counterpart was NOT CHECKED. The interaction can
           be neither asserted nor ruled out. It changes no priority — it is a coverage
           fact, and it is stated so the team knows what checking the counterpart would
           settle.
  DORMANT  the counterpart was checked and nothing fired, or one side does not arise for
           this entity. The interaction is genuinely absent.

Collapsing LATENT into DORMANT would report an unchecked theme as an absent one, which is
the §4.4 error the whole report is built to avoid. Collapsing it into LIVE would let a
theme borrow weight from a diagnostic nobody ran.

RISK COMPLEXES
--------------
A reinforcing pair is a fact about two themes. What an audit team plans against is the
connected GROUP: when three raised themes all rest on the same receivable balance, they
are one testing problem, not three, and the planning consequence is different in kind from
three separate leads. A complex is therefore a connected component over the LIVE
reinforcing edges — computed, not authored, so it cannot claim a group the evidence does
not support.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any, Iterable

from . import clusters as CL
from .model import (
    ClusterPackage, RAISED, NOT_RAISED, CLUSTER_ABSTAIN, CLUSTER_NOT_APPLICABLE,
)

LIVE = "LIVE"
LATENT = "LATENT"
DORMANT = "DORMANT"
STATES = frozenset({LIVE, LATENT, DORMANT})


@dataclass(frozen=True)
class ResolvedInteraction:
    """One declared edge, resolved against one run, from the point of view of `subject`.

    Directional in presentation and undirected in fact: the same edge resolves to two
    ResolvedInteractions, one per endpoint, because what a reader needs to see on RC-WC's
    card is what RC-WC gains or loses — not a symmetric statement they must re-read from
    the other side.
    """
    subject: str            # the cluster whose card this appears on
    counterpart: str
    counterpart_theme: str
    kind: str               # CL.REINFORCES | CL.OFFSETS
    state: str              # LIVE | LATENT | DORMANT
    mechanism: str
    basis: str
    statement: str          # the rendered sentence, built once so all three renderers agree

    @property
    def live_reinforcing(self) -> bool:
        return self.state == LIVE and self.kind == CL.REINFORCES

    @property
    def live_offsetting(self) -> bool:
        return self.state == LIVE and self.kind == CL.OFFSETS

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Complex:
    """A connected group of mutually reinforcing raised themes (§10.2)."""
    members: tuple[str, ...]          # cluster ids, in rank order where ranks are known
    member_themes: tuple[str, ...]
    edges: tuple[tuple[str, str], ...]
    mechanisms: tuple[str, ...]
    dominant: str                     # the highest-ranked member
    # Two lengths, for two places. `summary` is one sentence for the block-6 lead note,
    # where several complexes may appear together; `narrative` is the full reasoning for
    # the complex's own callout. Printing the narrative in both — which is what happened
    # first — buries the note it was meant to open.
    summary: str
    narrative: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InteractionMap:
    """Every declared edge, resolved — plus the complexes and the block-6 lead note."""
    edges: tuple[ResolvedInteraction, ...] = ()
    complexes: tuple[Complex, ...] = ()
    note: str = ""

    def for_cluster(self, cluster_id: str) -> tuple[ResolvedInteraction, ...]:
        return tuple(e for e in self.edges if e.subject == cluster_id)

    def statements_for(self, cluster_id: str) -> tuple[str, ...]:
        """What `ClusterPackage.interactions` carries. DORMANT edges are recorded in the
        map but not repeated on the card: a theme's planning package should state what
        bears on it, and an interaction that is genuinely absent does not."""
        return tuple(e.statement for e in self.for_cluster(cluster_id)
                     if e.state in (LIVE, LATENT))

    def complex_for(self, cluster_id: str) -> Complex | None:
        return next((c for c in self.complexes if cluster_id in c.members), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "edges": [e.to_dict() for e in self.edges],
            "complexes": [c.to_dict() for c in self.complexes],
            "note": self.note,
        }


# ---- resolution -------------------------------------------------------------------

def resolve(packages: Iterable[ClusterPackage]) -> InteractionMap:
    """Resolve every declared edge against the statuses this run produced.

    Called AFTER the packages are built and BEFORE ranking: the priority engine consumes
    the result, and the complexes are attached afterwards once ranks exist (`with_ranks`).
    """
    status = {p.cluster_id: p.status for p in packages}
    themes = {p.cluster_id: p.theme for p in packages}

    edges: list[ResolvedInteraction] = []
    for decl in CL.INTERACTIONS:
        for subject in (decl.a, decl.b):
            counterpart = decl.other(subject)
            state = _state(status.get(subject), status.get(counterpart))
            edges.append(ResolvedInteraction(
                subject=subject,
                counterpart=counterpart,
                counterpart_theme=themes.get(counterpart, counterpart),
                kind=decl.kind,
                state=state,
                mechanism=decl.mechanism,
                basis=decl.basis,
                statement=_statement(
                    decl.kind, state, counterpart,
                    themes.get(counterpart, counterpart), decl.mechanism,
                    status.get(counterpart),
                ),
            ))
    return InteractionMap(edges=tuple(edges), complexes=(), note=_note(tuple(edges), ()))


def _state(subject_status: str | None, counterpart_status: str | None) -> str:
    """A raised subject is a precondition: an interaction cannot bear on a theme that was
    not raised, whatever the counterpart is doing."""
    if subject_status != RAISED:
        return DORMANT
    if counterpart_status == RAISED:
        return LIVE
    if counterpart_status == CLUSTER_ABSTAIN:
        return LATENT
    return DORMANT     # NOT_RAISED (checked, nothing fired) or NOT_APPLICABLE


def _statement(kind: str, state: str, counterpart: str, counterpart_theme: str,
               mechanism: str, counterpart_status: str | None) -> str:
    verb = "reinforces" if kind == CL.REINFORCES else "is offset by"
    # The conditional mood needs its own form: "would is offset by" is not a sentence.
    would = "would reinforce" if kind == CL.REINFORCES else "would be offset by"
    head = f"{counterpart} ({counterpart_theme})"

    if state == LIVE and kind == CL.REINFORCES:
        return (
            f"LIVE — {verb} {head}, which is also raised. {mechanism} Planning consequence "
            f"(§10.2): the two are planned and tested together rather than separately, and "
            f"this theme's priority is raised accordingly."
        )
    if state == LIVE:
        return (
            f"LIVE — {verb} {head}, which is also raised. {mechanism} Planning consequence "
            f"(§10.2): stated and moderating. It lowers this theme's interaction pressure; "
            f"it does NOT clear the theme, and the offset is itself an observation about "
            f"dependency that the audit team should test in its own right."
        )
    if state == LATENT:
        return (
            f"LATENT — {would} {head}, but that theme was NOT CHECKED, so the interaction "
            f"can be neither asserted nor ruled out. {mechanism} It changes nothing in this "
            f"theme's priority — it is stated because checking {counterpart} would settle it."
        )

    why = {
        NOT_RAISED: f"{counterpart} was checked and no contributing signal fired",
        CLUSTER_NOT_APPLICABLE: f"{counterpart} does not arise for this entity",
        CLUSTER_ABSTAIN: f"{counterpart} was not checked",
    }.get(counterpart_status or "", f"{counterpart} is not raised")
    return f"DORMANT — declared interaction with {head} is not present: {why}."


def with_ranks(imap: InteractionMap, ranks: dict[str, int]) -> InteractionMap:
    """Attach the complexes once ranking has run, and rebuild the note around them.

    Complexes are computed here rather than in `resolve` for one reason: naming a dominant
    member is a ranking statement, and inventing one before the ranking exists would put a
    priority claim in the report that the priority engine never made.
    """
    comps = _complexes(imap.edges, ranks)
    return InteractionMap(edges=imap.edges, complexes=comps,
                          note=_note(imap.edges, comps))


def _complexes(edges: tuple[ResolvedInteraction, ...],
               ranks: dict[str, int]) -> tuple[Complex, ...]:
    live = [e for e in edges if e.live_reinforcing]
    if not live:
        return ()

    adj: dict[str, set[str]] = {}
    themes: dict[str, str] = {}
    for e in live:
        adj.setdefault(e.subject, set()).add(e.counterpart)
        adj.setdefault(e.counterpart, set()).add(e.subject)
        themes[e.counterpart] = e.counterpart_theme

    seen: set[str] = set()
    out: list[Complex] = []
    for start in sorted(adj):
        if start in seen:
            continue
        # Connected component by breadth-first walk over live reinforcing edges only.
        members, queue = set(), [start]
        while queue:
            node = queue.pop()
            if node in members:
                continue
            members.add(node)
            queue.extend(adj[node] - members)
        seen |= members
        if len(members) < 2:
            continue

        ordered = tuple(sorted(members, key=lambda c: (ranks.get(c, 99), c)))
        pairs = sorted({tuple(sorted((e.subject, e.counterpart))) for e in live
                        if e.subject in members})
        mechs = tuple(dict.fromkeys(
            e.mechanism for e in live if e.subject in members))
        dominant = ordered[0]
        # The most-connected member is a different fact from the highest-ranked one, and
        # conflating them would put a claim in the report that neither the ranking nor the
        # graph makes. Rank says where the complex enters the shortlist; connectivity says
        # which theme's evidence reaches the most of it. When they differ, that difference
        # is itself the useful planning information.
        hub = max(sorted(members), key=lambda c: len(adj[c]))
        out.append(Complex(
            members=ordered,
            member_themes=tuple(themes.get(c, c) for c in ordered),
            edges=tuple(pairs),
            mechanisms=mechs,
            dominant=dominant,
            summary=(
                f"{' + '.join(ordered)} form one reinforcing complex: for planning they are "
                f"one problem rather than {len(ordered)} separate leads, and "
                f"{dominant} is the highest-ranked member."
            ),
            narrative=_complex_narrative(ordered, themes, dominant, hub, adj, ranks, mechs),
        ))
    return tuple(out)


def _complex_narrative(members: tuple[str, ...], themes: dict[str, str], dominant: str,
                       hub: str, adj: dict[str, set[str]], ranks: dict[str, int],
                       mechanisms: tuple[str, ...]) -> str:
    named = ", ".join(f"{c} ({themes.get(c, c)})" for c in members)
    rank_txt = (f"ranked {ranks[dominant]}" if dominant in ranks
                else "first by cluster id, no rank available")

    parts = [
        f"{len(members)} raised themes reinforce one another: {named}. They rest on shared "
        f"drivers, so for planning they are one problem rather than {len(members)} separate "
        f"leads, and evidence obtained against one bears on the others.",
        f"{themes.get(dominant, dominant)} ({dominant}, {rank_txt}) is the highest-ranked "
        f"member — where the complex enters the shortlist.",
    ]
    if hub != dominant and len(adj[hub]) > 1:
        parts.append(
            f"{themes.get(hub, hub)} ({hub}) is the most connected member, reinforcing "
            f"{len(adj[hub])} of the others: it is the theme whose evidence reaches most of "
            f"the complex, which is a different question from which ranks highest and may "
            f"be the more efficient place to start."
        )
    parts.append("Shared drivers: " + " ".join(mechanisms))
    parts.append(
        "This states how the themes connect for planning purposes. It is not a conclusion "
        "that any of them is a finding (§17.3), and the complex does not merge them — each "
        "keeps its own package, assertions and evidence request."
    )
    return " ".join(parts)


# ---- the block-6 lead note --------------------------------------------------------

def _n(count: int, noun: str) -> str:
    return f"{count} {noun} is" if count == 1 else f"{count} {noun}s are"


def _note(edges: tuple[ResolvedInteraction, ...],
          comps: tuple[Complex, ...]) -> str:
    """The paragraph that opens block 6. Derived, never authored — so it cannot claim an
    interaction the resolution above did not produce."""
    declared = len(CL.INTERACTIONS)
    live_r = {tuple(sorted((e.subject, e.counterpart))) for e in edges if e.live_reinforcing}
    live_o = {tuple(sorted((e.subject, e.counterpart))) for e in edges if e.live_offsetting}
    latent = {tuple(sorted((e.subject, e.counterpart))) for e in edges if e.state == LATENT}

    parts = [
        f"Risk interactions (§10.2). {declared} interactions are declared between the "
        f"{len(CL.CLUSTERS)} themes; each is resolved against what this run actually found, "
        f"so an interaction is claimed only where BOTH themes are raised."
    ]

    if not live_r and not live_o:
        parts.append(
            "None is live in this report. Nothing follows from that about the entity: an "
            "interaction needs two raised themes, and it is absent here because the themes "
            "it connects were not both raised."
        )
    else:
        if comps:
            parts.append(" ".join(c.summary for c in comps))
        elif live_r:
            pair = sorted(live_r)[0]
            parts.append(
                f"One reinforcing interaction is live, between {pair[0]} and {pair[1]}. "
                f"Both themes are raised and rest on a shared driver, so they are planned "
                f"together."
            )
        if live_o:
            named = "; ".join(f"{a} by {b}" for a, b in sorted(live_o))
            parts.append(
                f"{_n(len(live_o), 'offsetting interaction')} live, moderating the themes "
                f"they touch ({named}). An offset is stated and reduces interaction "
                f"pressure; it never clears a theme, and the thing doing the offsetting is "
                f"itself an exposure worth testing."
            )

    if latent:
        named = "; ".join(f"{a}/{b}" for a, b in sorted(latent))
        parts.append(
            f"{_n(len(latent), 'declared interaction')} LATENT ({named}): one side is "
            f"raised and the other was not checked, so the interaction can be neither "
            f"asserted nor ruled out. These add nothing to any priority. They are the "
            f"cheapest coverage to buy — checking the counterpart theme settles each one."
        )

    parts.append(
        "A reinforcing interaction raises the affected theme's planning priority and an "
        "offsetting one moderates it, through the `interaction_pressure` dimension of the "
        "ranking (§11.1) — visible and scored, never applied as a hidden multiplier."
    )
    return " ".join(parts)
