"""
§14.1 renderer — the planning-first report, ten blocks, in the spec's order.

"The report is built to support an audit-planning decision on its first pages, not to
present a technical dump." So coverage and limitations come first, the dashboard second,
and calculations never appear in the narrative at all.

The renderer is deterministic and contains no model call. When narration lands (ROADMAP
M8 of the structure design / §6 of the FDR spec), the LLM writes INTO these blocks under
the numeric guard and this lint — it does not choose the blocks or their order.
"""
from __future__ import annotations

from . import assertions as A
from . import headline as HL
from . import priority as PRI
from . import tracing as T
from .model import (
    FDRReport, ClusterPackage, RAISED, NOT_RAISED, CLUSTER_ABSTAIN, CLUSTER_NOT_APPLICABLE,
    FIRED, ABSTAIN, NOT_APPLICABLE, SUPPRESSED, NOT_FIRED,
)

_STATUS_WORD = {
    RAISED: "RAISED",
    NOT_RAISED: "not raised — checked, nothing found",
    CLUSTER_ABSTAIN: "NOT CHECKED",
    CLUSTER_NOT_APPLICABLE: "not applicable to this entity",
}

_SIGNAL_WORD = {
    FIRED: "present",
    NOT_FIRED: "absent",
    ABSTAIN: "not evaluated",
    NOT_APPLICABLE: "not applicable",
    SUPPRESSED: "suppressed",
}


def _h(n: int, title: str) -> str:
    return f"\n{'=' * 78}\n{n}. {title.upper()}\n{'=' * 78}"


def _wrap(text: str, indent: str = "  ", width: int = 76) -> str:
    out, line = [], indent
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            out.append(line.rstrip())
            line = indent
        line += word + " "
    out.append(line.rstrip())
    return "\n".join(out)


def render(rep: FDRReport) -> str:
    with T.span("render", input={"blocks": 10, "entity": rep.coverage.entity}) as sp:
        text = _render(rep)
        sp.set("fdr.render.chars", len(text))
        sp.set("fdr.render.review_mode", rep.coverage.review_mode)
        sp.set_output(f"{len(text.splitlines())} lines")
        return text


def _render(rep: FDRReport) -> str:
    o: list[str] = []
    cov, prof = rep.coverage, rep.business_profile

    o.append("FINANCIAL DIAGNOSTIC REPORT")
    o.append(f"Audit-planning intelligence · {cov.entity} · "
             f"{', '.join(cov.periods) or 'period not stated'}")
    o.append("Leads for audit attention. Not an audit opinion, and not the audit plan.")

    if cov.review_mode:
        o.append("\n" + "!" * 78)
        o.append("REVIEW MODE — NOT AN ASSESSMENT OF THIS ENTITY")
        o.append("!" * 78)
        o.append(_wrap(
            f"The following signals were ASSERTED present so that the planning packages, "
            f"ranking and matrix rows they produce can be reviewed and corrected: "
            f"{', '.join(cov.assumed_signals)}. Nothing was measured. No figure, trend or "
            f"balance of this entity was read. Every conclusion below follows from the "
            f"assertion alone and must not be used, quoted or relied on as a diagnostic "
            f"result.", "  "))
        o.append("!" * 78)

    # ---- 1 ----
    o.append(_h(1, "Coverage and limitations"))
    o.append(f"  Entity                {cov.entity}")
    o.append(f"  Period(s)             {', '.join(cov.periods) or '—'}")
    o.append(f"  Statement flavour     {cov.flavor}")
    o.append(f"  Input Quality Grade   {cov.input_quality_grade or 'NOT ASSESSED'}"
             + (f"   (caps diagnostic confidence at {cov.grade_ceiling})"
                if cov.grade_ceiling else ""))
    if cov.grade_basis:
        o.append(_wrap(cov.grade_basis, "                        "))
    # §9.4's guardrail against a bare composite applies to the grade too: a letter printed
    # without the counts behind it is a letter a reader can only accept or ignore. Each
    # component also names its own fix, which is what makes the grade a work list.
    for comp in cov.grade_components:
        o.append(f"    {comp['name']:26} {comp['ceiling']}")
        o.append(_wrap(comp["detail"], "        "))
    o.append(f"  Optional inputs       {', '.join(cov.optional_inputs) or 'none — standalone run'}")
    o.append(f"  Statements received   {', '.join(cov.statements_received) or 'none'}")
    if cov.statements_missing:
        o.append(f"  Statements missing    {', '.join(cov.statements_missing)}")
    o.append(f"  Comparable years      {cov.comparable_years}")

    o.append("\n  Pre-analysis validation note (§4.4, mandatory)")
    for line in cov.validation_note.splitlines():
        o.append(_wrap(line, "    "))

    if cov.limitations:
        o.append("\n  Limitations")
        for lim in cov.limitations:
            o.append(_wrap(f"- {lim}", "    "))

    # ---- 2 ----
    # Two halves, in this order. The headline reads come FIRST because §14.1 asks for a
    # report that supports a planning decision on its first pages: a reviewer forms the
    # shape of the entity from ten figures and then reads the triage list against it. The
    # reverse order asks them to hold a ranked list of themes in mind with nothing to
    # scale it against.
    o.append(_h(2, "Executive dashboard"))
    o.append(_render_headline(rep))

    raised = rep.raised()
    o.append("\n  Raised clusters — the triage order")
    if raised:
        if any(c.interpretation_withheld for c in raised):
            o.append(_wrap("§2.1 PRECONDITION UNMET — no business understanding was formed. "
                           "The items below are observations, not interpreted risks, and are "
                           "not a triage order. See block 3.", "  "))
        for i, c in enumerate(raised, 1):
            o.append(f"  {i}. {c.theme}  [{c.diagnostic_confidence or 'confidence not set'}]")
    else:
        o.append(_wrap(
            "No risk cluster is raised. This is NOT a clean result: no diagnostic could be "
            "evaluated in this run. See block 1 for why, and the diagnostics-not-run list in "
            "block 9. Absence of evidence is not evidence of absence (§2.3).", "  "))

    # ---- 3 ----
    o.append(_h(3, "Business profile"))
    if prof.formed:
        o.append(f"  Business model        {prof.model} ({prof.model_confidence})")
        o.append(f"  Revenue model         {prof.revenue_model or '—'}")
        o.append(f"  Cost structure        {prof.cost_structure or '—'}")
        o.append(f"  Financing structure   {prof.financing or '—'}")
        o.append(f"  Value drivers         {', '.join(prof.value_drivers) or '—'}")
        if prof.inherent_risk_expectations:
            o.append("  Sector inherent-risk expectations, formed before any ratio (§5.3):")
            for e in prof.inherent_risk_expectations:
                o.append(_wrap(f"- {e}", "    "))
        # A sector overlay MODIFIES a derivation rather than suppressing it. Applying the
        # generic method where an overlay applies produces a signal that is an artefact of
        # the accounting policy, so the overlay is stated whether or not it fires.
        for ov in _overlays(prof.model):
            o.append(f"\n  Sector overlay {ov.id} — modifies {', '.join(ov.affects)}")
            o.append(_wrap(ov.instruction, "    "))
    else:
        o.append(_wrap(
            "NOT FORMED. §2.1 prohibits interpreting a ratio before a business understanding "
            "exists, because the same number means different things in different business "
            "models. Until this block is populated, no diagnostic in this report may be "
            "interpreted even where the arithmetic is available.", "  "))

    # ---- 4, 5 ----
    # These two blocks report what was MEASURED, whatever the verdict. A signal that ran
    # and did not fire is a real negative and belongs here: it is the only thing in the
    # report that distinguishes "checked, nothing there" from "not checked", and §2.3
    # turns on that distinction.
    measured = [r for c in rep.clusters for r in c.contributing_signals
                if r.ran and r.observation]

    o.append(_h(4, "Financial health summary"))
    if measured:
        o.append(_wrap("What the evaluated diagnostics observed, at the level of structure "
                       "and performance. Each is stated with the arithmetic that produced "
                       "it and the annual-report source it was read from, so a reviewer can "
                       "reconstruct it without reading the code (§16).", "  "))
        for r in sorted(measured, key=lambda x: _layer(x.signal_id)):
            o.append(_render_signal_detail(r))
    else:
        o.append(_wrap("Structure and performance (Layers 2-3) are not computed in this run. "
                       "No diagnostic could be evaluated; see block 9 for what each one "
                       "needs.", "  "))

    o.append(_h(5, "Key trends and structural drift"))
    trends = [r for r in measured if _is_trend(r.signal_id)]
    if trends:
        o.append(_wrap(f"{len(trends)} trend diagnostic(s) ran on {cov.comparable_years} "
                       f"comparable year(s). §9.5 requires three before any trend is "
                       f"presented and forbids presenting a two-point movement as a trend; "
                       f"the panel returns no series at all below that, so a rule cannot "
                       f"reach this block on a short one.", "  "))
        for r in trends:
            o.append(f"\n    {r.signal_id}  {SIGNAL_TITLE(r.signal_id)}"
                     f"   [{_SIGNAL_WORD[r.status]}]")
            o.append(_wrap(r.observation, "        "))
    else:
        o.append(_wrap(f"Not computed. {cov.comparable_years} comparable year(s) available; "
                       f"§9.5 requires three before any trend is presented, and forbids "
                       f"presenting a two-point movement as a trend. An annual report "
                       f"carries the current period and one comparative, so a trend needs "
                       f"the preceding annual reports as well.", "  "))

    # ---- 6 ----
    o.append(_h(6, "Risk clusters with interactions"))
    # The interaction note leads the block. A reader who stops after the first paragraph
    # should already know whether these themes are separate leads or one connected problem
    # — that distinction changes how the whole block is read, so it cannot sit at the end.
    if rep.interactions is not None:
        o.append(_wrap(rep.interactions.note, "  "))
        for cx in rep.interactions.complexes:
            o.append(f"\n  RISK COMPLEX — {' + '.join(cx.members)}")
            o.append(_wrap(cx.narrative, "      "))
    for c in rep.clusters:
        o.append(_render_cluster(c))

    # ---- 7 ----
    o.append(_h(7, "Audit-planning matrix"))
    if raised:
        o.append(_wrap("One row per prioritised cluster (§10.4). The FDR proposes; the audit "
                       "team decides the nature, timing and extent actually performed (§10.5).",
                       "  "))
        for c in sorted(raised, key=lambda x: x.priority_rank or 99):
            o.append(_render_matrix_row(c))
    else:
        o.append(_wrap("Empty. The matrix is the FDR's principal deliverable (§10.4); it is "
                       "populated from raised clusters, and none is raised. The rows that "
                       "would appear here are listed as unevaluated in block 9.", "  "))

    # ---- 8 ----
    o.append(_h(8, "Top focus areas"))
    if rep.priorities:
        cap = rep.capacity or {}
        o.append(_wrap(f"Capacity: {cap.get('basis', 'not stated')}", "  "))
        if cap.get("capacity") is not None:
            o.append(f"    stated capacity {cap['capacity']}   used {cap['used']}   "
                     f"unallocated {cap['unused']}   "
                     f"(all ranked themes would need {cap['required']})")
            o.append(_wrap(cap.get("method", ""), "    "))
        o.append(_wrap(f"Combination method (§11.2): {_combination_method()}", "  "))
        for p in rep.priorities:
            o.append(f"\n  {p.rank}. {_theme(rep, p.cluster_id)}"
                     f"   score {p.score if p.score is not None else '—'}"
                     f"  ({p.assessed_count}/{len(PRI.DIMENSIONS)} dimensions assessed)"
                     + ("   [BY NATURE]" if p.by_nature else ""))
            o.append(_wrap(p.reasoning, "      "))
            o.append("      dimensions")
            for d in p.dimensions:
                o.append(f"        {d.name:32} "
                         f"{str(d.score) + '/3' if d.assessed else 'not assessed'}")
                o.append(_wrap(d.basis, "            "))

        if rep.below_the_line:
            o.append("\n  Below the line — excluded by capacity, not by judgement")
            for b in rep.below_the_line:
                o.append(f"    {b.cluster_id}  (ranked {b.rank}, effort {b.effort})")
                o.append(_wrap(b.reason, "        "))
            o.append(_wrap("These themes were ranked and then did not fit. They are listed so "
                           "the exclusion is a visible decision the team can reverse, not a "
                           "silent omission.", "    "))
    else:
        o.append(_wrap("No ranked shortlist. Ranking (§11) requires raised clusters to rank. "
                       "Nothing is implied about the entity by this being empty.", "  "))

    # ---- 9 ----
    o.append(_h(9, "Evidence matrix and diagnostics not run"))
    o.append("  Evidence requests held against each theme (Appendix F):")
    for c in rep.clusters:
        label = (', '.join(c.evidence_request) if c.evidence_request
                 else "** none authored — Appendix F gap **")
        o.append(_wrap(f"{c.cluster_id}  {label}", "    "))
        if c.specialist_referral:
            o.append(_wrap(f"specialist: {', '.join(c.specialist_referral)}", "      "))

    o.append(_wrap("Schedule III makes several of these schedules mandatory. Their ABSENCE "
                   "from a filing is itself a reportable fact, not a gap in this report:",
                   "  "))
    for did, where in _mandatory_schedules():
        o.append(_wrap(f"- {did}: {where}", "    "))

    o.append("\n  Cross-cutting reads (§12 and §8.2) — inputs that condition several "
             "diagnostics at once:")
    for d in _cross_cutting():
        o.append(f"    {d.id}  {d.title}")
        o.append(_wrap(d.formula, "        "))
        for s in d.source_lines():
            o.append(_wrap(f"- {s}", "        "))

    o.append(f"\n  Diagnostics not run ({len(rep.diagnostics_not_run)}) — §4.4 requires each "
             f"to state its effect. Each carries the derivation that would have been "
             f"computed and the schedule its inputs are read from, so it is a work list "
             f"rather than an apology:")
    for d in rep.diagnostics_not_run:
        # DATA and SCOPE are different work for different people — an extraction ticket
        # versus a roadmap item — so the tag travels with every entry, not just the tally.
        tag = f"{d.blocked_by}/{d.reason_code}" if d.blocked_by else d.reason_code
        o.append(_wrap(f"- {d.diagnostic}  [{d.impact}]" + (f"  <{tag}>" if tag else ""),
                       "    "))
        o.append(_wrap(d.reason, "        "))
        if d.formula:
            o.append(_wrap(f"would be derived as: {d.formula}", "        "))
        for s in d.ar_source:
            o.append(_wrap(f"read from: {s}", "        "))

    # ---- 10 ----
    o.append(_h(10, "Planning summary"))
    if rep.priorities:
        ran = sum(1 for c in rep.clusters for r in c.contributing_signals if r.ran)
        notrun = len(rep.diagnostics_not_run)
        o.append(_wrap(
            f"{len(rep.priorities)} candidate priority theme(s), ranked, from {ran} "
            f"diagnostic(s) that were evaluated. {notrun} could not be evaluated and are "
            f"listed in block 9 with the derivation and the source each of them needs. A "
            f"theme that is not raised here was either checked and clear or not checked at "
            f"all, and block 6 states which — absence of evidence is not evidence of "
            f"absence (§2.3).", "  "))
    else:
        o.append(_wrap(
            "This run produced no candidate priorities. What each diagnostic needs in order "
            "to run is stated against it in block 9. An empty shortlist is not a clean "
            "result and implies nothing about the entity.", "  "))
    o.append(_wrap("These are candidate priorities for the audit team's decision. The team "
                   "decides the strategy, scope and materiality.", "  "))

    o.append("\n  Standing caveats (§17.3)")
    for c in rep.caveats:
        o.append(_wrap(f"- {c}", "    "))

    o.append("\n  Versions (§16 reproducibility)")
    for k, v in sorted(rep.versions.items()):
        o.append(f"    {k:20} {v}")

    return "\n".join(o)


# ---- block 2, first half: the headline reads ---------------------------------------

# The console report presents rupee figures in INR lakh — the unit the panel normalises
# every figure to. Rescaling for presentation is `headline.format_value`'s job and is
# offered to the JSON consumer; the text report does not do it, because a report that
# silently re-expresses figures in a unit the reader did not choose is worse than one that
# states its unit once and keeps it.
HEADLINE_SCALE = HL.LAKH
_HEADLINE_UNIT_LABEL = {HL.LAKH: "INR lakh", HL.MILLION: "INR million",
                        HL.CRORE: "INR crore"}


def _render_headline(rep: FDRReport) -> str:
    """The tiles, above the triage list. Unbound tiles are shown, never dropped."""
    tiles = tuple(rep.headline)
    if not tiles:
        return _wrap(
            "No headline reads. The dashboard is computed from the entity-year panel and "
            "none was built in this run, so there are no figures to show. This says "
            "nothing about the entity.", "  ")

    # One cause, stated once. Every tile carries its own reason because each is usually a
    # DIFFERENT reason — a binder gap here, a broken series there — and that per-tile
    # detail is the whole point. When the cause is the same for all of them, repeating it
    # ten times buries the ten labels that are the actual information.
    if all(t.state == HL.NO_PANEL for t in tiles):
        return (_wrap(
            f"Not computed — no entity-year panel was built in this run, so not one of the "
            f"{len(tiles)} headline reads could be formed. This is a fact about the run, "
            f"not about the entity.", "  ") + "\n"
            + _wrap("Tiles that would have been shown: "
                    + ", ".join(f"{t.tile_id} {t.label}" for t in tiles) + ".", "    "))

    o: list[str] = [_wrap(
        f"Headline reads from the face of the statements. Rupee figures in "
        f"{_HEADLINE_UNIT_LABEL[HEADLINE_SCALE]}. Every movement is stated AGAINST THE "
        f"YEAR IT IS MEASURED AGAINST: these are one-year movements, not trends, and §9.5 "
        f"forbids presenting a two-point movement as one. None of these figures is a "
        f"finding — they are the shape of the entity, read before anything is interpreted "
        f"(§2.1).", "  ")]

    shown = [t for t in tiles if t.computed]
    unbound = [t for t in tiles if not t.computed]

    if shown:
        o.append("")
        for t in shown:
            mark = "!" if t.attention else " "
            tail = t.movement_label or "movement not shown"
            # The declared second read sits on the same line as the movement rather than
            # under the value. A cover multiple is a READ OF the figure, and putting it in
            # the value column invites it to be read as another figure.
            ctx = t.context_display()
            if ctx:
                tail += f"   ·   {ctx}"
            o.append(f"  {mark} {t.tile_id}  {t.label[:44]:<44} "
                     f"{t.display(HEADLINE_SCALE):>16}   {tail}")
            if t.state == HL.VALUE_ONLY:
                o.append(_wrap(t.reason, "        "))
            # The trust that came with the figure. Silent when every input was verified at
            # a scale read from the filing, because a note on every row is a note nobody
            # reads — and this one has to be noticed when it appears.
            if t.trust_note:
                o.append(_wrap(f"read with a caveat: {t.trust_note}", "        "))

        flagged = [t for t in shown if t.attention]
        if flagged:
            o.append("")
            o.append(_wrap(
                f"Marked (!): {', '.join(t.tile_id for t in flagged)}. "
                f"{HL.ATTENTION_BASIS}", "  "))
        else:
            o.append("")
            o.append(_wrap(
                f"No movement is marked for attention. {HL.ATTENTION_BASIS}", "  "))

    if unbound:
        o.append("\n  Not computed")
        o.append(_wrap("Shown rather than dropped: a dashboard missing the tile it could "
                       "not compute reads as a complete dashboard (§4.4).", "  "))
        for t in unbound:
            o.append(f"    {t.tile_id}  {t.label}   [{t.state}]")
            o.append(_wrap(t.reason, "        "))
            o.append(_wrap(f"would be computed as: {t.formula}", "        "))
    return "\n".join(o)


def _render_cluster(c: ClusterPackage) -> str:
    o = [f"\n  {c.cluster_id}  {c.theme}"]
    if c.anchor:
        o.append(f"      {' · '.join(c.anchor)}")
    o.append(f"      status        {_STATUS_WORD[c.status]}")
    if c.status == RAISED and c.interpretation_withheld:
        o.append(_wrap(c.interpretation_withheld, "                    "))
    if c.reason:
        o.append(_wrap(c.reason, "                    "))
    o.append(f"      assertions    "
             f"{', '.join(A.HUMAN_LABEL[a] for a in c.affected_assertions)}")
    if c.regularity_matters:
        o.append(f"      regularity    "
                 f"{', '.join(A.HUMAN_LABEL[r] for r in c.regularity_matters)}")
    o.append("      signals")
    for r in c.contributing_signals:
        o.append(f"        {r.signal_id}  {_SIGNAL_WORD[r.status]:16}"
                 f"{'sev ' + r.severity if r.severity else ''}")
        if r.status in (SUPPRESSED, NOT_APPLICABLE):
            o.append(_wrap(r.reason, "              "))
        elif r.status == FIRED and r.observation:
            o.append(_wrap(r.observation, "              "))
        elif r.status == ABSTAIN:
            o.append(_wrap(r.reason, "              "))
            if r.ar_source:
                o.append(_wrap("read from: " + "; ".join(r.ar_source), "              "))

    # The supporting derivations for this theme. They raise nothing on their own and are
    # not Appendix D signals — they are the reads that turn a raised theme into a
    # testable one, and they are stated so that the evidence request has a method behind
    # it rather than only a document name.
    sup = _supporting(c.cluster_id)
    if sup:
        o.append("      supporting derivations (not Appendix D signals; they raise nothing "
                 "on their own)")
        for d in sup:
            o.append(f"        {d.id}  {d.title}")
            o.append(_wrap(d.formula, "              "))
            for s in d.source_lines():
                o.append(_wrap(f"- {s}", "              "))

    if c.interactions:
        o.append("      interactions (§10.2) — resolved against what this run found")
        for i in c.interactions:
            o.append(_wrap(f"- {i}", "        "))
    return "\n".join(o)


def _combination_method() -> str:
    from .priority import COMBINATION_METHOD
    return COMBINATION_METHOD


def _theme(rep: FDRReport, cluster_id: str) -> str:
    for c in rep.clusters:
        if c.cluster_id == cluster_id:
            return c.theme
    return cluster_id


def _field(label: str, value: str, indent: str = "      ") -> str:
    """Long planning prose is wrapped, not truncated — the content IS the deliverable."""
    head = f"{indent}{label:<22}"
    if len(value) <= 52:
        return head + value
    return head + "\n" + _wrap(value, indent + "  ")


def _render_matrix_row(c: ClusterPackage) -> str:
    """The Appendix E template, one row per prioritised cluster."""
    fired = [r for r in c.contributing_signals if r.status == FIRED]
    o = [f"\n  {'-' * 74}",
         f"  RANK {c.priority_rank or '—'}   {c.theme}   [{c.cluster_id}]",
         f"  {'-' * 74}"]
    if c.interpretation_withheld:
        o.append(_wrap(c.interpretation_withheld, "      "))
    o.append(_field("contributing", ", ".join(
        f"{r.signal_id} ({SIGNAL_TITLE(r.signal_id)})" for r in fired)))
    o.append(_field("assertions",
                    ", ".join(A.HUMAN_LABEL[a] for a in c.affected_assertions)))
    if c.regularity_matters:
        o.append(_field("regularity",
                        ", ".join(A.HUMAN_LABEL[r] for r in c.regularity_matters)))
    o.append(_field("inherent risk", c.inherent_risk or "—"))
    o.append(_field("significant risk",
                    "YES — warrants special audit attention" if c.significant_risk
                    else "not by default; see planning significance"))
    o.append(_field("materiality basis", ", ".join(c.materiality_basis) or "—"))
    o.append(_field("planning significance", c.planning_significance or "—"))
    o.append(_field("control implications", c.control_implications or "—"))
    o.append("      response — candidate nature, timing and extent (§10.5)")
    for k in ("nature", "timing", "extent"):
        o.append(f"        {k}")
        o.append(_wrap(c.recommended_response.get(k, "—"), "          "))
    o.append(_field("specialist", ", ".join(c.specialist_referral) or "none indicated"))
    o.append("      evidence to request")
    for e in c.evidence_request:
        o.append(_wrap(f"- {e}", "        "))
    o.append(_wrap("Management explanation obtained against these is a lead to test, never "
                   "audit evidence in itself (§2.3).", "        "))
    if c.alt_explanations:
        o.append("      alternative explanations to eliminate first (§2.2)")
        for e in c.alt_explanations:
            o.append(_wrap(f"- {e}", "        "))
    o.append(_field("confidence", c.diagnostic_confidence or "not formed"))
    if c.confidence_basis:
        o.append(_wrap(c.confidence_basis, "        "))
    o.append(_field("corroboration", c.corroboration))
    if c.unevaluated_signals:
        o.append(_wrap(f"NOT EVALUATED within this theme: {', '.join(c.unevaluated_signals)}. "
                       f"This row rests on partial evidence (§4.4).", "      "))
    if c.priority_reasoning:
        o.append("      why this rank (§11.2)")
        o.append(_wrap(c.priority_reasoning, "        "))
    return "\n".join(o)


def SIGNAL_TITLE(signal_id: str) -> str:
    from . import signals as SG
    return SG.BY_ID[signal_id].title


def _layer(signal_id: str) -> int:
    from . import signals as SG
    return SG.BY_ID[signal_id].layer


def _is_trend(signal_id: str) -> bool:
    from . import signals as SG
    return SG.BY_ID[signal_id].is_trend


def _supporting(cluster_id: str):
    from . import derivations as DV
    return DV.supporting_for(cluster_id)


def _cross_cutting():
    from . import derivations as DV
    return DV.CROSS_CUTTING


def _mandatory_schedules():
    from . import derivations as DV
    return DV.mandatory_schedules()


def _overlays(business_model):
    from . import derivations as DV
    return DV.overlays_for(business_model)


def _render_signal_detail(r) -> str:
    """One measured diagnostic, with everything a reviewer needs to challenge it.

    Order is the §14.2 chain as far as this block goes: what was observed, how it was
    derived, where it was read from, what stood in for what, and how far it can be leaned
    on. The trace is last because it is the check, not the message.
    """
    o = [f"\n    {r.signal_id}  L{_layer(r.signal_id)}  {SIGNAL_TITLE(r.signal_id)}"
         f"   [{_SIGNAL_WORD[r.status]}"
         + (f", confidence {r.confidence}" if r.confidence else "") + "]"]
    if r.observation:
        o.append(_wrap(r.observation, "        "))
    if r.formula:
        o.append("        derivation")
        o.append(_wrap(r.formula, "          "))
    if r.ar_source:
        o.append("        read from")
        for s in r.ar_source:
            o.append(_wrap(f"- {s}", "          "))
    if r.proxies_used:
        o.append("        PROXIES APPLIED — a stand-in is stated, never silent")
        for p in r.proxies_used:
            o.append(_wrap(f"- {p}", "          "))
    if r.reconciling_items:
        o.append("        eliminate before treating this as a lead")
        for x in r.reconciling_items:
            o.append(_wrap(f"- {x}", "          "))
    if r.trace:
        o.append("        trace")
        o.append(_wrap(r.trace, "          "))
    if r.confidence_basis:
        o.append(_wrap(f"confidence: {r.confidence_basis}", "        "))
    return "\n".join(o)
