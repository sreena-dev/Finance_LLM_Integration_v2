"""
Audit-planning matrix export — Appendix E columns, in formats a team can work in.

§10.4: "The per-cluster packages are assembled into a single audit-planning matrix that the
audit team can use DIRECTLY in preparing its detailed plan." A matrix that exists only as
console prose fails that test — the team cannot sort it, filter it, annotate it, or paste it
into a working paper.

Two formats, for two uses:
  csv       spreadsheet-ready; one row per shortlisted cluster, Appendix E columns
  markdown  pasteable into a working paper or an issue tracker

Both carry the same columns and the same caveats. Neither is a plan: §10.5 governs, and the
caveat travels in the file so it cannot be separated from the content.
"""
from __future__ import annotations
import csv
import io

from . import assertions as A
from . import priority as PR
from . import tracing as T
from .model import FDRReport, ClusterPackage, FIRED

# Appendix E, in the specification's order, plus the traceability columns §10.4 names.
COLUMNS: tuple[tuple[str, str], ...] = (
    ("rank", "Rank"),
    ("cluster_id", "Cluster"),
    ("theme", "Risk cluster"),
    ("contributing_signals", "Contributing signals"),
    ("alt_explanations", "Alternative explanations"),
    ("affected_assertions", "Affected assertions"),
    ("regularity_matters", "Regularity matters"),
    ("inherent_risk", "Inherent risk"),
    ("significant_risk", "Significant risk"),
    ("control_implications", "Control implications"),
    ("planning_significance", "Planning significance"),
    ("response_nature", "Recommended response — nature"),
    ("response_timing", "Recommended response — timing"),
    ("response_extent", "Recommended response — extent"),
    ("specialist_referral", "Specialist referral"),
    ("evidence_request", "Evidence request"),
    ("diagnostic_confidence", "Diagnostic confidence"),
    ("confidence_basis", "Confidence basis"),
    ("corroboration", "Corroboration"),
    ("priority_score", "Priority score"),
    ("dimensions_assessed", "Dimensions assessed"),
    ("priority_reasoning", "Why this rank"),
    ("interactions", "Risk interactions"),
    # A team that sorts or filters this file loses the block-6 narrative, and with it the
    # fact that three of these rows are one testing problem. The complex travels on the
    # row so it survives being pasted into a working paper on its own.
    ("risk_complex", "Risk complex (§10.2)"),
    ("unevaluated_signals", "Contributing signals NOT evaluated"),
    ("interpretation_withheld", "Interpretation caveat"),
    ("source_trace", "Source trace"),
    # A matrix row a team works from has to carry the method as well as the conclusion.
    # Without these three columns the row says what to look at and not how the system got
    # there, which makes it unchallengeable in exactly the format most likely to be
    # circulated and quoted (§16).
    ("derivations", "How each signal was derived"),
    ("ar_sources", "Annual-report source"),
    ("proxies_applied", "Proxies applied"),
    ("observations", "Observed figures"),
)

FOOTER = (
    "These are candidate priorities for the audit team's decision (§10.5). The FDR does not "
    "set the audit strategy, decide scope or fix materiality. Every row is a lead for audit "
    "attention requiring corroboration, not a finding. Management explanation obtained "
    "against an evidence request is a lead to test, never audit evidence in itself (§2.3)."
)


def _complex_cell(rep: FDRReport, cluster_id: str) -> str:
    imap = rep.interactions
    if imap is None:
        return ""
    cx = imap.complex_for(cluster_id)
    if cx is None:
        return "none — this theme does not reinforce another raised theme"
    others = [m for m in cx.members if m != cluster_id]
    return (f"{' + '.join(cx.members)} (one reinforcing complex; this row reinforces "
            f"{', '.join(others)}; highest-ranked member {cx.dominant})")


def _row(rep: FDRReport, c: ClusterPackage) -> dict[str, str]:
    from . import signals as SG
    fired = [s for s in c.contributing_signals if s.status == FIRED]
    p = next((x for x in rep.priorities if x.cluster_id == c.cluster_id), None)

    return {
        "rank": str(c.priority_rank or ""),
        "cluster_id": c.cluster_id,
        "theme": c.theme,
        "contributing_signals": "; ".join(
            f"{s.signal_id} {SG.BY_ID[s.signal_id].title}" for s in fired),
        "alt_explanations": "; ".join(c.alt_explanations),
        "affected_assertions": ", ".join(A.HUMAN_LABEL[a] for a in c.affected_assertions),
        "regularity_matters": ", ".join(A.HUMAN_LABEL[r] for r in c.regularity_matters),
        "inherent_risk": c.inherent_risk or "",
        "significant_risk": ("yes" if c.significant_risk else "no")
                            if c.significant_risk is not None else "",
        "control_implications": c.control_implications,
        "planning_significance": c.planning_significance,
        "response_nature": c.recommended_response.get("nature", ""),
        "response_timing": c.recommended_response.get("timing", ""),
        "response_extent": c.recommended_response.get("extent", ""),
        "specialist_referral": ", ".join(c.specialist_referral) or "none indicated",
        "evidence_request": "; ".join(c.evidence_request),
        "diagnostic_confidence": c.diagnostic_confidence or "not formed",
        "confidence_basis": c.confidence_basis,
        "corroboration": c.corroboration,
        "priority_score": str(p.score) if p and p.score is not None else "",
        "dimensions_assessed": f"{p.assessed_count}/{len(PR.DIMENSIONS)}" if p else "",
        "priority_reasoning": p.reasoning if p else "",
        "interactions": "; ".join(c.interactions),
        "risk_complex": _complex_cell(rep, c.cluster_id),
        "unevaluated_signals": ", ".join(c.unevaluated_signals),
        "interpretation_withheld": c.interpretation_withheld,
        "source_trace": "; ".join(
            f"{s.signal_id}:{','.join(s.evidence) or 'no evidence recorded'}" for s in fired),
        "derivations": " | ".join(f"{s.signal_id}: {s.formula}" for s in fired if s.formula),
        "ar_sources": " | ".join(
            f"{s.signal_id}: {'; '.join(s.ar_source)}" for s in fired if s.ar_source),
        # Only the proxies ACTUALLY applied. An empty cell here means the derivation ran on
        # the lines it names, which is itself information a reader needs.
        "proxies_applied": " | ".join(
            f"{s.signal_id}: {'; '.join(s.proxies_used)}" for s in fired if s.proxies_used),
        "observations": " | ".join(
            f"{s.signal_id}: {s.observation}" for s in fired if s.observation),
    }


def to_csv(rep: FDRReport) -> str:
    with T.span("export matrix", input={"format": "csv",
                                        "rows": len(rep.raised())}) as sp:
        out = _to_csv(rep)
        sp.set("fdr.export.columns", len(COLUMNS))
        sp.set("fdr.export.bytes", len(out))
        sp.set_output(f"csv, {len(rep.raised())} row(s), {len(COLUMNS)} columns")
        return out


def _to_csv(rep: FDRReport) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([label for _, label in COLUMNS])
    for c in sorted(rep.raised(), key=lambda x: x.priority_rank or 99):
        row = _row(rep, c)
        w.writerow([row[key] for key, _ in COLUMNS])
    if rep.below_the_line:
        w.writerow([])
        w.writerow(["BELOW THE LINE — excluded by capacity, not by judgement"])
        w.writerow(["Cluster", "Rank", "Effort", "Reason"])
        for b in rep.below_the_line:
            w.writerow([b.cluster_id, b.rank, b.effort, b.reason])
    w.writerow([])
    w.writerow([FOOTER])
    if rep.coverage.review_mode:
        w.writerow([f"REVIEW MODE — signals asserted, not measured: "
                    f"{', '.join(rep.coverage.assumed_signals)}. This file describes what the "
                    f"system would report and says nothing about the entity named in it."])
    return buf.getvalue()


def to_markdown(rep: FDRReport) -> str:
    with T.span("export matrix", input={"format": "markdown",
                                        "rows": len(rep.raised())}) as sp:
        out = _to_markdown(rep)
        sp.set("fdr.export.bytes", len(out))
        sp.set_output(f"markdown, {len(rep.raised())} row(s)")
        return out


def _to_markdown(rep: FDRReport) -> str:
    o: list[str] = [f"# Audit-planning matrix — {rep.coverage.entity}",
                    f"_{', '.join(rep.coverage.periods) or 'period not stated'}_", ""]
    if rep.coverage.review_mode:
        o += ["> **REVIEW MODE — NOT AN ASSESSMENT OF THIS ENTITY.** Signals asserted, not "
              f"measured: {', '.join(rep.coverage.assumed_signals)}. Nothing was computed.", ""]

    raised = sorted(rep.raised(), key=lambda x: x.priority_rank or 99)
    if not raised:
        o += ["_No cluster is raised, so the matrix is empty. This is not a clean result — "
              "see the coverage note for what could not be evaluated._", ""]
    for c in raised:
        row = _row(rep, c)
        o.append(f"## Rank {row['rank']} — {c.theme} (`{c.cluster_id}`)")
        if c.interpretation_withheld:
            o.append(f"> {c.interpretation_withheld}")
        o.append("")
        for key, label in COLUMNS:
            if key in ("rank", "cluster_id", "theme", "interpretation_withheld"):
                continue
            val = row[key]
            if val:
                o.append(f"- **{label}:** {val}")
        o.append("")

    if rep.below_the_line:
        o += ["## Below the line", "",
              "_Excluded by capacity, not by judgement._", "",
              "| Cluster | Rank | Effort | Reason |", "|---|---|---|---|"]
        for b in rep.below_the_line:
            o.append(f"| {b.cluster_id} | {b.rank} | {b.effort} | {b.reason} |")
        o.append("")

    if rep.capacity:
        o += ["## Capacity", "",
              f"- **Stated capacity:** {rep.capacity.get('capacity') or 'none stated'}",
              f"- **Required for all ranked themes:** {rep.capacity.get('required')}",
              f"- **Used:** {rep.capacity.get('used')}   "
              f"**Unallocated:** {rep.capacity.get('unused')}",
              f"- {rep.capacity.get('basis', '')}",
              f"- _{rep.capacity.get('method', '')}_", ""]

    o += ["---", "", FOOTER]
    return "\n".join(o)
