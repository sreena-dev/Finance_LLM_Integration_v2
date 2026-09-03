"""
Assembly — signal results into cluster packages into an FDR (§10, §11, §14).

The clustering, interaction and prioritisation logic of M10-M11 slots in here. What M3
establishes is the part that must be right whatever the diagnostics say:

  - every cluster in Appendix D produces a package, including the ones that could not be
    evaluated. A cluster that goes quiet is indistinguishable from a clean one (§4.4);
  - a cluster raised while some contributing signal could not be evaluated NAMES those
    signals, so nobody reads a partial cluster as a complete one;
  - the diagnostics-not-run list is generated from the registry, not hand-maintained, so
    it cannot drift from what the system actually attempted.
"""
from __future__ import annotations

from . import clusters as CL
from . import signals as SG
from . import assertions as A
from . import grading as GR
from . import headline as HL
from . import interactions as IX
from . import planning as PL
from . import priority as PR
from . import tracing as T
from .evaluate import evaluate_all, PanelLike, ASSUMED_MARKER
from .model import (
    BusinessProfile, Coverage, ClusterPackage, DiagnosticNotRun, FDRReport, SignalResult,
    RAISED, NOT_RAISED, CLUSTER_ABSTAIN, CLUSTER_NOT_APPLICABLE,
    FIRED, ABSTAIN, NOT_APPLICABLE, SUPPRESSED, BLOCKED_DATA, BLOCKED_SCOPE,
)

CONTRACT_VERSION = "appendix-d.2026-07-31"


_NO_BUSINESS_UNDERSTANDING = (
    "INTERPRETATION WITHHELD (§2.1). No business understanding was formed, so this cluster "
    "cannot be interpreted: whether these signals are a risk or a normal feature of the "
    "entity's business model is undecidable without it. The signals below are stated as "
    "observations only. They are not a risk conclusion, and this cluster must not be read "
    "as a planning priority until block 3 is populated."
)


def _package(cluster: CL.Cluster, results: dict[str, SignalResult],
             *, business_understanding: bool = False) -> ClusterPackage:
    mine = tuple(results[sid] for sid in cluster.signals)
    fired = [r for r in mine if r.status == FIRED]
    abstained = [r for r in mine if r.status == ABSTAIN]
    n_a = [r for r in mine if r.status == NOT_APPLICABLE]
    suppressed = [r for r in mine if r.status == SUPPRESSED]

    common = dict(
        cluster_id=cluster.id,
        theme=cluster.theme,
        contributing_signals=mine,
        affected_assertions=tuple(sorted(cluster.assertions)),
        regularity_matters=tuple(sorted(cluster.regularity_matters)),
        control_implications=cluster.control_implications,
        specialist_referral=cluster.specialist,
        evidence_request=cluster.evidence,
        unevaluated_signals=tuple(r.signal_id for r in abstained),
        anchor=_anchor(mine, fired),
        # `interactions` is deliberately NOT set here. Resolving §10.2 needs every cluster's
        # status, and inside this function only one cluster exists — which is exactly how
        # the old build came to print "reinforces RC-FUND" on clusters that were never
        # raised, against counterparts that were never raised either. It is attached in
        # `build_report` once all six packages are known.
        interpretation_withheld=("" if business_understanding else _NO_BUSINESS_UNDERSTANDING),
    )

    # Every contributing signal is inapplicable under this framework -> the whole theme is.
    if len(n_a) == len(mine):
        return ClusterPackage(
            status=CLUSTER_NOT_APPLICABLE,
            reason="Every contributing signal is a concept the entity's reporting framework "
                   "does not use. The theme does not arise for this entity.",
            **common,
        )

    if fired:
        pc = PL.BY_ID[cluster.id]
        ctrl, _ = PL.control_implications_for(cluster.id)
        # Every §10.3 field is populated here. A raised cluster with an empty response or
        # no evidence request renders as a dash, which a reviewer reads as "nothing to do".
        common["control_implications"] = ctrl
        common["evidence_request"] = tuple(e for e, _src in PL.evidence_for(cluster.id))
        return ClusterPackage(
            status=RAISED,
            alt_explanations=_alt_explanations(fired),
            inherent_risk=pc.inherent_risk,
            significant_risk=pc.significant_risk,
            planning_significance=_scoped_significance(pc, cluster, fired, abstained),
            recommended_response={
                "nature": pc.response.nature,
                "timing": pc.response.timing,
                "extent": pc.response.extent,
            },
            materiality_basis=_materiality_basis(pc, fired),
            diagnostic_confidence=_cluster_confidence(fired),
            confidence_basis=_confidence_basis(fired),
            **common,
        )

    # Nothing fired. Distinguish "checked, nothing there" from "could not check".
    evaluated = [r for r in mine if r.ran]
    setaside = _setaside_clause(suppressed, n_a)

    if abstained:
        ids = ", ".join(r.signal_id for r in abstained)
        if evaluated:
            reason = (f"{len(evaluated)} of {len(mine)} contributing signals were evaluated "
                      f"and none fired, but {len(abstained)} could not be evaluated ({ids})"
                      f"{setaside}. The theme is not cleared on partial evidence.")
        else:
            reason = (f"No contributing signal was evaluated: {len(abstained)} of {len(mine)} "
                      f"could not be evaluated ({ids}){setaside}. This theme was NOT checked "
                      f"— it is not a clean result.")
        return ClusterPackage(status=CLUSTER_ABSTAIN, reason=reason, **common)

    return ClusterPackage(
        status=NOT_RAISED,
        reason=f"All {len(evaluated)} evaluated contributing signals were checked and none "
               f"fired{setaside}.",
        **common,
    )


def _scoped_significance(pc: PL.PlanningContent, cluster: CL.Cluster,
                         fired: list[SignalResult], abstained: list[SignalResult]) -> str:
    """§10.3 planning significance, scoped to the signals that actually fired.

    The planning content in `planning.py` is written for the THEME, covering every signal
    the cluster can carry. Rendered unqualified against a cluster raised by one of its
    signals, it reads as a claim about this entity that was never measured — RC-FUND's
    text describes a leverage-driven return (S13), and on an entity where only S15 fired
    and S13 abstained, that sentence asserted a story nothing in the run supports.

    So the framing is kept and its scope is stated: which signals it rests on, and which
    contributing signals were not evaluated. The alternative — writing per-signal
    significance for all twenty-two — is audit authoring, not engineering (ROADMAP M2).
    """
    by_id = ", ".join(r.signal_id for r in fired)
    scope = (f" This is the planning significance of the THEME. In this run it rests on "
             f"{by_id} alone")
    if abstained:
        scope += (f"; {', '.join(r.signal_id for r in abstained)} could not be evaluated, "
                  f"so any part of the framing above that depends on them is not a "
                  f"statement about this entity")
    others = [s for s in cluster.signals if s not in {r.signal_id for r in fired}]
    if others and not abstained:
        scope += f"; {', '.join(others)} did not fire"
    return pc.planning_significance + scope + "."


def _setaside_clause(suppressed: list[SignalResult], n_a: list[SignalResult]) -> str:
    """Signals set aside are named, never absorbed into a count (§15.2)."""
    parts = []
    if suppressed:
        parts.append(f"{', '.join(r.signal_id for r in suppressed)} suppressed as normal for "
                     f"the business model")
    if n_a:
        parts.append(f"{', '.join(r.signal_id for r in n_a)} not applicable under the "
                     f"reporting framework")
    return f"; {'; '.join(parts)}" if parts else ""


def _materiality_basis(pc: PL.PlanningContent, fired: list[SignalResult]) -> tuple[str, ...]:
    """§10.6 — the filter is applied transparently, with the reason stated.

    The VALUE dimension needs figures. A cluster that admits only on value therefore cannot
    be admitted yet, and says so; a cluster material by nature or context is admitted
    regardless, which is exactly what the filter exists to allow.
    """
    admitted = sorted(d for d in pc.admits_on if d != PL.VALUE)
    if pc.by_nature:
        return tuple(admitted) + ("by-nature override applies (§11.2)",)
    return tuple(admitted) or ("value only — not assessable without figures",)


def _cluster_confidence(fired: list[SignalResult]) -> str | None:
    """App H. None unless every contributing signal that fired carries a confidence.

    Confidence is never derived from severity, and never invented for a cluster whose
    signals were asserted rather than measured (§4.4).
    """
    levels = [s.confidence for s in fired]
    if any(c is None for c in levels):
        return None
    order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    return min(levels, key=lambda c: order[c])       # the weakest link governs


def _confidence_basis(fired: list[SignalResult]) -> str:
    if any(s.confidence is None for s in fired):
        return ("No diagnostic confidence: at least one contributing signal was not measured, "
                "so no confidence was formed. Severity is stated separately and does not "
                "imply confidence (§4.4).")
    return ("Set to the weakest contributing signal's confidence — a cluster is no more "
            "dependable than the least dependable diagnostic in it.")


def _alt_explanations(fired: list[SignalResult]) -> tuple[str, ...]:
    """§2.2 — for every material signal, the non-error explanations that could account for it."""
    out: list[str] = []
    for r in fired:
        for e in SG.BY_ID[r.signal_id].alt_explanations:
            if e not in out:
                out.append(e)
    return tuple(out)


def _anchor(mine: tuple[SignalResult, ...],
            fired: list[SignalResult]) -> tuple[str, ...]:
    """The provenance stamp under the cluster title (§10.3 traceability, in the header).

    Two facts, both measured rather than authored: where the present signals were read
    from, and how much of the theme actually fired. The second matters more than it looks.
    A theme raised on one signal of four and a theme raised on four of four render
    identically once the card is open — the same fields, the same confident prose — and the
    difference between them is exactly what a planning reader needs before deciding where
    to spend a week. Putting it in the header makes it unmissable.
    """
    places: list[str] = []
    for r in fired:
        for src in r.ar_source:
            # "Balance sheet — trade receivables" -> "Balance sheet". The specific line is
            # in the signal detail; the header wants the schedule, not the row.
            place = src.split(" — ")[0].strip()
            if place and place not in places:
                places.append(place)

    out: list[str] = []
    if places:
        shown = ", ".join(places[:3])
        out.append(shown + (f" +{len(places) - 3} more" if len(places) > 3 else ""))

    evaluated = [r for r in mine if r.ran]
    if fired:
        out.append(f"{len(fired)} of {len(mine)} signals present")
    elif evaluated:
        out.append(f"0 of {len(mine)} signals present; {len(evaluated)} evaluated")
    else:
        out.append(f"0 of {len(mine)} signals evaluated")
    return tuple(out)


def _diagnostics_not_run(results: dict[str, SignalResult]) -> tuple[DiagnosticNotRun, ...]:
    """§4.4 — generated from the registry, so it cannot drift from what was attempted."""
    out: list[DiagnosticNotRun] = []
    for sig in SG.SIGNALS:
        r = results[sig.id]
        if r.status != ABSTAIN:
            continue
        consumers = ", ".join(CL.clusters_for(sig.id))
        out.append(DiagnosticNotRun(
            diagnostic=f"{sig.id} — {sig.title}",
            reason=r.reason + (f" Required figures: {', '.join(r.missing_inputs)}."
                               if r.missing_inputs else ""),
            impact=f"weakens {consumers}",
            # Carried through from the derivation registry so the entry says what would
            # have been computed and which schedule holds the inputs — a diagnostic nobody
            # can act on is not meaningfully different from one nobody reported.
            formula=r.formula,
            ar_source=r.ar_source,
            # The queryable half. `blocked_by` says whether this is an extraction ticket or
            # a roadmap item; `reason_code` and `reason_detail` say which figure and which
            # years, so "what buys the most coverage" is a query rather than a reading task.
            reason_code=r.reason_code,
            blocked_by=r.blocked_by,
            reason_detail=r.reason_detail,
        ))
    return tuple(out)


def _validation_note(coverage: Coverage, results: dict[str, SignalResult],
                     profile: BusinessProfile) -> str:
    """§4.4 — mandatory whatever the grade. What was received, mapped, uncertain, and its effect."""
    from . import rules as R
    ran = sum(1 for r in results.values() if r.ran)
    na = sum(1 for r in results.values() if r.status == NOT_APPLICABLE)
    sup = sum(1 for r in results.values() if r.status == SUPPRESSED)
    face = sum(1 for s in SG.SIGNALS if s.availability == SG.FACE)
    blocked = sum(1 for r in results.values() if r.blocked_by == BLOCKED_DATA)
    scoped = sum(1 for r in results.values() if r.blocked_by == BLOCKED_SCOPE)
    note_scope = sum(1 for r in results.values()
                     if r.reason_code == R.NEEDS_NOTE_EXTRACTION)
    rule_scope = sum(1 for r in results.values()
                     if r.reason_code == R.RULE_NOT_IMPLEMENTED)

    lines = [
        f"Received: {', '.join(coverage.statements_received) or 'no financial-statement dataset'}"
        f" for {', '.join(coverage.periods) or 'no stated period'}.",
        f"Comparable years available: {coverage.comparable_years}. "
        + ("Trend diagnostics require three (§9.5); a two-point movement is never presented "
           "as a trend." if coverage.comparable_years < 3 else ""),
        f"Business understanding: "
        + (f"{profile.model} (confidence {profile.model_confidence})." if profile.formed
           else "NOT FORMED. §2.1 prohibits interpreting any ratio before a business "
                "understanding exists, so no signal may be interpreted in this run even "
                "where the arithmetic would have been available."),
        f"Input Quality Grade: "
        + (f"{coverage.input_quality_grade} — {coverage.grade_basis}"
           if coverage.input_quality_grade else
           "not assessed for this run, so nothing caps diagnostic confidence and no "
           "diagnostic may be relied on."),
        # THREE-WAY, NOT TWO-WAY. "Not evaluated" merged two different things and misled in
        # one direction: it reported the system as broken when part of the count is work
        # deliberately not yet built and declared as such. A reader can act on the split —
        # DATA is an extraction ticket, SCOPE is a roadmap item — and cannot act on the sum.
        f"Of {len(SG.SIGNALS)} signals in the contract: {ran} evaluated; {blocked} blocked "
        f"for want of data; {scoped} not yet built ("
        + (f"{note_scope} needing note-level extraction, {rule_scope} awaiting a rule"
           if (note_scope or rule_scope) else "none")
        + f"); {na} not applicable to this framework; {sup} suppressed as normal for the "
        f"business model. {face} of {len(SG.SIGNALS)} are derivable from the face of the "
        f"statements.",
        "Effect on confidence: every diagnostic in this report that did not run carries no "
        "confidence rating, because none was formed. Absence of a signal here is absence of "
        "evidence, not evidence of absence (§2.3).",
    ]
    return "\n".join(l for l in lines if l.strip())


def build_report(
    entity: str,
    periods: tuple[str, ...] = (),
    *,
    flavor: str = "unknown",
    statements_received: tuple[str, ...] = (),
    statements_missing: tuple[str, ...] = (),
    comparable_years: int = 0,
    optional_inputs: tuple[str, ...] = (),
    business_profile: BusinessProfile | None = None,
    framework: str | None = None,
    panel: PanelLike | None = None,
    assume_fired: frozenset[str] = frozenset(),
    shortlist_size: int | None = None,
    capacity: int | None = None,
    fact_coverage: dict[str, Any] | None = None,
) -> FDRReport:
    profile = business_profile or BusinessProfile()

    # §4.3 — graded BEFORE anything is evaluated, because the grade caps what every
    # diagnostic may claim, and a ceiling applied afterwards would have to reach back into
    # results already written. It reads no figure's value: only how many were read, how
    # many were confirmed, how many carried an inferred scale, and how much history stands
    # behind them.
    igrade = GR.grade(fact_coverage, comparable_years=comparable_years,
                      business_model=profile.model,
                      model_confidence=profile.model_confidence)

    root = T.span("fdr report", input={
        "entity": entity, "periods": list(periods), "flavor": flavor,
        "comparable_years": comparable_years, "framework": framework,
        "business_model": profile.model, "review_mode": bool(assume_fired),
        "capacity": capacity, "shortlist_size": shortlist_size,
        "optional_inputs": list(optional_inputs),
        "input_quality_grade": igrade.letter,
    })
    with root as rsp:
        results = evaluate_all(
            business_model=profile.model, framework=framework, panel=panel,
            assume_fired=assume_fired, grade=igrade.letter,
        )
        rsp.set("fdr.input_quality_grade", igrade.letter)
        rsp.set("fdr.input_quality_grade.binding", list(igrade.binding))
        rsp.set("fdr.confidence_ceiling", igrade.confidence_ceiling or "none")

        # Block 2's headline reads. Computed HERE rather than in the renderer because the
        # panel is in scope here and nowhere downstream: `evaluate_all` consumes it and
        # `FDRReport` never sees it. A dashboard assembled in the renderer would either
        # have to re-read the corpus or be handed figures somebody else had already
        # interpreted — and it must be the same figures the §14.3 payload carries.
        with T.span("headline", input={"tiles": len(HL.tiles_for(profile.model)),
                                       "business_model": profile.model}) as hsp:
            headline = HL.compute(panel, business_model=profile.model,
                                  framework=framework)
            for tv in headline:
                hsp.event(tv.tile_id, state=tv.state, label=tv.label,
                          value=("-" if tv.value is None else f"{tv.value:,.2f}"),
                          movement=tv.movement_label or "-",
                          attention=tv.attention)
            hsp.set("fdr.headline.unbound",
                    [t.tile_id for t in headline if not t.computed])
            hsp.set("fdr.headline.attention",
                    [t.tile_id for t in headline if t.attention])
            hsp.set_output(HL.summary(headline))

        with T.span("cluster signals", input={"clusters": len(CL.CLUSTERS)}) as csp:
            packages = tuple(_package(c, results, business_understanding=profile.formed)
                             for c in CL.CLUSTERS)
            for pkg in packages:
                csp.event(pkg.cluster_id, status=pkg.status, theme=pkg.theme,
                          unevaluated=",".join(pkg.unevaluated_signals) or "-",
                          reason=pkg.reason[:300] or "-")
            csp.set("fdr.clusters.raised",
                    [p.cluster_id for p in packages if p.status == RAISED])
            csp.set("fdr.clusters.unchecked",
                    [p.cluster_id for p in packages if p.status == CLUSTER_ABSTAIN])
            csp.set("fdr.clusters.not_applicable",
                    [p.cluster_id for p in packages if p.status == CLUSTER_NOT_APPLICABLE])
            csp.set("fdr.interpretation_withheld",
                    any(p.interpretation_withheld for p in packages))
            csp.set_output({p.cluster_id: p.status for p in packages})

        # §10.2 — resolved BEFORE ranking, because a reinforcing interaction raises the
        # cluster's planning priority and an offsetting one moderates it. Resolving after
        # the ranking would leave the interaction as decoration on a rank it was supposed
        # to influence.
        with T.span("interactions", input={"declared": len(CL.INTERACTIONS)}) as isp:
            imap = IX.resolve(packages)
            for e in imap.edges:
                if e.state != IX.DORMANT:
                    isp.event(f"{e.subject}->{e.counterpart}", kind=e.kind, state=e.state)
            isp.set("fdr.interactions.live",
                    sorted({f"{e.subject}/{e.counterpart}" for e in imap.edges
                            if e.state == IX.LIVE}))
            isp.set("fdr.interactions.latent",
                    sorted({f"{e.subject}/{e.counterpart}" for e in imap.edges
                            if e.state == IX.LATENT}))
            isp.set_output({e.state: 1 for e in imap.edges})

        with T.span("prioritise", input={"raised": len(
                [p for p in packages if p.status == RAISED])}) as psp:
            priorities = PR.rank(
                packages,
                optional_inputs=optional_inputs,
                business_profile_formed=profile.formed,
                shortlist_size=shortlist_size,
                interactions=imap,
            )
            for p in priorities:
                psp.event(p.cluster_id, rank=p.rank, score=p.score,
                          assessed=f"{p.assessed_count}/{len(PR.DIMENSIONS)}",
                          by_nature=p.by_nature)
            unassessed = sorted({d.name for p in priorities
                                 for d in p.dimensions if not d.assessed})
            psp.set("fdr.priority.dimensions_unassessed", unassessed)
            psp.set_output([(p.rank, p.cluster_id, p.score) for p in priorities])

        with T.span("capacity", input={"capacity": capacity}) as xsp:
            priorities, below_line, capacity_note = PR.apply_capacity(priorities, capacity)
            xsp.set("fdr.capacity", capacity_note)
            for b in below_line:
                xsp.event(f"below_line:{b.cluster_id}", rank=b.rank, effort=b.effort,
                          by_nature=b.by_nature, reason=b.reason[:300])
            if any(b.by_nature for b in below_line):
                xsp.set("fdr.capacity.by_nature_excluded",
                        [b.cluster_id for b in below_line if b.by_nature])
            xsp.set_output(capacity_note)

        ranked = {p.cluster_id: p for p in priorities}
        for pkg in packages:
            p = ranked.get(pkg.cluster_id)
            if p is not None:
                pkg.priority_rank = p.rank
                pkg.priority_reasoning = p.reasoning

        # The complexes need the ranks (they name a dominant member), and the ranks needed
        # the edges — so the map is completed here, after both. Each package then takes the
        # statements that bear on it, which keeps one resolution behind all three renderers
        # instead of three that can disagree.
        imap = IX.with_ranks(imap, {p.cluster_id: p.rank for p in priorities})
        for pkg in packages:
            pkg.interactions = imap.statements_for(pkg.cluster_id)
        rsp.set("fdr.interactions.complexes",
                [",".join(c.members) for c in imap.complexes])

        rsp.set("fdr.shortlist", [p.cluster_id for p in priorities])
        rsp.set("fdr.below_the_line", [b.cluster_id for b in below_line])
        rsp.set("fdr.diagnostics_not_run.count",
                sum(1 for r in results.values() if r.status == ABSTAIN))

        coverage = Coverage(
            entity=entity,
            periods=periods,
            flavor=flavor,
            input_quality_grade=igrade.letter,
            grade_basis=igrade.basis,
            grade_components=tuple(c.to_dict() for c in igrade.components),
            grade_ceiling=igrade.confidence_ceiling,
            optional_inputs=optional_inputs,
            statements_received=statements_received,
            statements_missing=statements_missing,
            comparable_years=comparable_years,
            limitations=_limitations(
                profile, comparable_years, assume_fired, panel=panel,
                signals_ran=sum(1 for r in results.values() if r.ran)),
            review_mode=bool(assume_fired),
            assumed_signals=tuple(sorted(assume_fired)),
        )
        coverage.validation_note = _validation_note(coverage, results, profile)

        return FDRReport(
            coverage=coverage,
            business_profile=profile,
            headline=headline,
            clusters=packages,
            interactions=imap,
            priorities=priorities,
            below_the_line=below_line,
            capacity=capacity_note,
            shortlist=tuple(p.cluster_id for p in priorities),
            diagnostics_not_run=_diagnostics_not_run(results),
            versions={
                "signal_contract": CONTRACT_VERSION,
                "signals": str(len(SG.SIGNALS)),
                "clusters": str(len(CL.CLUSTERS)),
                "pipeline": "M10-planning" + ("-REVIEW-MODE" if assume_fired else ""),
                "planning_content": PL.SPEC + "+" + PL.PROPOSED,
                # A tile change alters the first screen of every report, so the run
                # records which registry produced it (§18.3).
                "headline": HL.VERSION,
            },
        )


def _limitations(profile: BusinessProfile, comparable_years: int,
                 assume_fired: frozenset[str] = frozenset(),
                 panel: PanelLike | None = None,
                 signals_ran: int = 0) -> tuple[str, ...]:
    out = []
    if assume_fired:
        out.append(
            f"REVIEW MODE. {len(assume_fired)} signal(s) were ASSERTED present, not measured: "
            f"{', '.join(sorted(assume_fired))}. Every cluster, ranking and matrix row below "
            f"rests on that assertion. This output describes what the system WOULD report, and "
            f"says nothing whatever about this entity."
        )
    # This limitation used to be emitted unconditionally, which put "no entity-year panel
    # is built, so no signal rule could run" into reports that carried a five-year panel
    # and four rules that had plainly run. A report whose whole claim is that its
    # self-description can be trusted cannot state something the same page disproves.
    if panel is None:
        out.append("No entity-year panel is built, so no signal rule could run (ROADMAP M6).")
    elif signals_ran == 0:
        out.append(
            f"A panel of {comparable_years} year(s) was built, but no signal rule could "
            f"run on it: no diagnostic had all of its inputs bound in every year of a "
            f"window. The per-diagnostic reasons are in block 9."
        )
    out.append(
        "Input quality is not graded, so no confidence ceiling was applied (ROADMAP M4).")
    if not profile.formed:
        out.append(
            "No business understanding was formed (ROADMAP M5). §2.1 makes this a "
            "precondition for interpretation, not a refinement."
        )
    if comparable_years < 3:
        out.append(
            f"Only {comparable_years} comparable year(s) available; §9.5 marks trend and "
            f"statistical diagnostics NOT RUN below three."
        )
    return tuple(out)
