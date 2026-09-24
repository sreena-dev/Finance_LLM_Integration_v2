"""
Downloadable PDF for the "XBRL Direct" tab (as_db path) — Dashboard, Business
Profile, Financial Health Summary, Key Trends and Key Risk Clusters for one
filing, assembled into a single markdown document and handed to the same
`pdf.py` renderer the fs_db report download already uses.

STRUCTURE FOLLOWS THE SPEC'S OWN PLANNING-FIRST ORDER (§14.1), NOT SCREEN ORDER
The screen renders Dashboard -> Business Profile -> Health -> Trends -> Risk
Clusters because that is the order the data becomes available. The spec's
output architecture puts the executive dashboard right after coverage, for
triage, and the audit-planning matrix as its own principal-deliverable
section rather than folded into the risk-cluster narrative — so this module
reorders the same five payloads into: coverage -> executive dashboard ->
business profile -> financial health -> key trends -> risk clusters with
interactions -> audit-planning matrix -> evidence & specialist-referral
summary -> closing caveats. Nothing here computes a new figure or reworded a
sentence; every value comes from the same payload the screen already shows.

RAISED CLUSTERS GET FULL CARDS; NOT-RAISED ONES ARE ONE TABLE, NOT SIX CARDS
An auditor scanning a printed page does not need six near-empty "not raised,
no contributing signals" cards taking a page each — that is padding, not
information. Clusters with `raised: True` keep their full planning package
(signals, alternative explanations, response, evidence); the rest collapse
into a single compact table of theme + reason, matching the spec's own
"reported... with unraised clusters explicitly presenting their non-
applicability rationale" (`xbrl_risk_deterministic.py`) — stated, not hidden,
but not padded either.
"""
from __future__ import annotations
from typing import Any


def _fmt_money(v: Any) -> str:
    if isinstance(v, (int, float)):
        return f"{v:,.2f}"
    return str(v) if v is not None else "—"


def _section_coverage(dash: dict[str, Any]) -> str:
    lines = [
        f"# Financial Diagnostic Report (XBRL Direct) — {dash.get('company_name', '')}",
        "",
        f"*Audit-planning intelligence. Leads for audit attention — not an audit "
        f"opinion, and not the audit plan.*",
        "",
        "## 1. Coverage",
        "",
        f"- **Entity:** {dash.get('company_name', '—')}",
        f"- **CIN:** {dash.get('entity_cin', '—')}",
        f"- **Financial year:** {dash.get('fy_label', '—')}",
        f"- **Filing (doc_id):** {dash.get('doc_id', '—')}",
        f"- **Source:** as_db (direct XBRL extraction, no LLM-free narrative path)",
        "",
    ]
    return "\n".join(lines)


def _section_dashboard(dash: dict[str, Any]) -> str:
    tiles = dash.get("tiles") or []
    lines = ["## 2. Executive dashboard", "", dash.get("lede") or "", "",
             "| # | Metric | Value | Movement | Flag |",
             "|---|---|---|---|---|"]
    for t in tiles:
        value = f"{t.get('display', '—')} {t.get('unit_label', '')}".strip() if t.get("computed") else "—"
        movement = t.get("movement_label") or t.get("reason") or "—"
        flag = "⚠" if t.get("attention") else ""
        lines.append(f"| {t.get('id', '')} | {t.get('label', '')} | {value} | {movement} | {flag} |")
    total = dash.get("total_count") or 0
    computed = dash.get("computed_count") or 0
    if total and computed < total:
        lines += ["", f"*{total - computed} of {total} figures could not be computed "
                       f"for this filing — see each row's Movement column for why.*"]
    lines.append("")
    return "\n".join(lines)


def _section_profile(profile: dict[str, Any] | None) -> str:
    if not profile or not profile.get("formed"):
        return "## 3. Business profile\n\n*Not formed for this run.*\n"
    fields = profile.get("fields") or []
    lines = ["## 3. Business profile", ""]
    for f in fields:
        if f.get("text"):
            lines.append(f"**{f.get('label', f.get('key', ''))}:** {f['text']}")
            lines.append("")
    if profile.get("reason"):
        lines.append(f"*{profile['reason']}*")
        lines.append("")
    return "\n".join(lines)


def _section_health(health: dict[str, Any] | None) -> str:
    if not health or not health.get("formed"):
        return "## 4. Financial health summary\n\n*Not built for this run.*\n"
    lines = ["## 4. Financial health summary — structure & performance, interpreted", ""]
    if health.get("business_type"):
        lines += [f"**Business profile & core activities:** {health['business_type']}", ""]
    lines += ["**Structure**", "", health.get("structure") or "—", "",
              "**Performance (decomposed)**", "", health.get("performance") or "—", ""]
    citations = health.get("citations") or []
    if citations:
        lines.append("*Grounded figures & audit trail:* " + "; ".join(
            f"{c.get('concept', '')}: {c.get('value', '')} ({c.get('scale', 'native')})"
            for c in citations
        ))
        lines.append("")
    if health.get("reason"):
        lines.append(f"*{health['reason']}*")
        lines.append("")
    return "\n".join(lines)


def _section_trends(trends: dict[str, Any] | None) -> str:
    if not trends or not trends.get("formed"):
        return "## 5. Key trends & structural drift\n\n*Not built for this run.*\n"
    lines = ["## 5. Key trends & structural drift", ""]
    if (trends.get("series_years") or 0) <= 1:
        lines += [f"**Initial reporting period notice:** {trends.get('summary_lede', '')}", ""]
        return "\n".join(lines)

    if trends.get("summary_lede"):
        lines += [f"**Longitudinal drift analysis:** {trends['summary_lede']}", ""]

    dupont = trends.get("dupont_schedule") or []
    if dupont:
        lines += ["**Extended DuPont profitability decomposition** "
                  "(Margin × Turnover × Multiplier = ROE)", "",
                  trends.get("dupont_narrative") or "", "",
                  "| Period | Net margin | Asset turnover | Equity multiplier | ROE |",
                  "|---|---|---|---|---|"]
        for dp in dupont:
            lines.append(f"| {dp.get('period_date', '')} | {dp.get('margin_display', '')} | "
                         f"{dp.get('turnover_display', '')} | {dp.get('multiplier_display', '')} | "
                         f"{dp.get('roe_display', '')} |")
        lines.append("")

    cards = trends.get("structural_drift_cards") or []
    if cards:
        lines.append(f"**Diagnostic trend leads & directional drift ({len(cards)})**")
        lines.append("")
        for c in cards:
            lines += [f"- **[{c.get('signal_id', '')}] {c.get('title', '')}** "
                     f"({(c.get('severity') or 'medium').upper()})",
                     f"  Observation: {c.get('observation', '')}",
                     f"  Audit lead: {c.get('audit_lead', '')}"]
            if c.get("evidence_lead"):
                lines.append(f"  Evidence to verify: {c['evidence_lead']}")
        lines.append("")

    highlights = trends.get("common_size_highlights") or []
    if highlights:
        lines.append("**Common-size balance sheet drift highlights**")
        lines.append("")
        for hl in highlights:
            drift = hl.get("drift_pp")
            drift_display = f"+{drift}" if isinstance(drift, (int, float)) and drift > 0 else drift
            lines.append(f"- **{hl.get('item', '')} ({drift_display} pp):** {hl.get('narrative', '')}")
        lines.append("")

    return "\n".join(lines)


def _section_risk_clusters(risk: dict[str, Any] | None) -> str:
    if not risk or not risk.get("risk_clusters"):
        return "## 6. Key risk clusters with interactions\n\n*Not built for this run.*\n"

    clusters = risk.get("risk_clusters") or []
    raised = [c for c in clusters if c.get("raised")]
    unraised = [c for c in clusters if not c.get("raised")]

    lines = ["## 6. Key risk clusters with interactions", ""]
    if not raised:
        lines.append("*No risk clusters met the activation threshold for this filing.*")
        lines.append("")

    for c in raised:
        resp = c.get("recommended_response") or {}
        lines += [f"### {c.get('theme', '')} ({c.get('id', '')})", ""]
        if c.get("priority_rank") is not None:
            lines.append(f"**Priority {c['priority_rank']}** · "
                         f"Inherent risk: {c.get('inherent_risk', '—')} · "
                         f"Confidence: {c.get('diagnostic_confidence', '—')}")
            lines.append("")
        signals = c.get("contributing_signals") or []
        if signals:
            lines.append("**Contributing signals**")
            for s in signals:
                lines.append(f"- {s.get('signal', '')} (`{s.get('source_trace', '')}`)")
            lines.append("")
        alts = c.get("alt_explanations") or []
        if alts:
            lines.append("**Plausible alternative explanations**")
            for a in alts:
                lines.append(f"- {a}")
            lines.append("")
        assertions = c.get("affected_assertions") or []
        if assertions:
            lines.append(f"**Affected assertions:** {', '.join(assertions)}")
            lines.append("")
        if resp.get("nature") or resp.get("timing") or resp.get("extent"):
            lines.append("**Candidate audit response (nature · timing · extent)**")
            if resp.get("nature"):
                lines.append(f"- Nature: {resp['nature']}")
            if resp.get("timing"):
                lines.append(f"- Timing: {resp['timing']}")
            if resp.get("extent"):
                lines.append(f"- Extent: {resp['extent']}")
            lines.append("")
        if c.get("evidence_request"):
            lines.append(f"**Evidence request:** {c['evidence_request']}")
            lines.append("")
        if c.get("specialist_referral") and c["specialist_referral"] != "None":
            lines.append(f"**Specialist referral:** {c['specialist_referral']}")
            lines.append("")
        if c.get("priority_reasoning"):
            lines.append(f"*{c['priority_reasoning']}*")
            lines.append("")

    if unraised:
        lines += ["**Clusters not raised**", "",
                 "| Theme | Reason |", "|---|---|"]
        for c in unraised:
            lines.append(f"| {c.get('theme', '')} | {c.get('reason', '')} |")
        lines.append("")

    interactions = risk.get("interactions") or []
    if interactions:
        lines.append("**Risk interactions**")
        lines.append("")
        for i in interactions:
            lines.append(f"- {i.get('cluster_a', '')} ↔ {i.get('cluster_b', '')} "
                         f"({i.get('relationship', '')}): {i.get('rationale', '')}")
        lines.append("")

    return "\n".join(lines)


def _section_matrix(risk: dict[str, Any] | None) -> str:
    clusters = [c for c in (risk or {}).get("risk_clusters") or [] if c.get("raised")]
    lines = ["## 7. Audit-planning matrix — principal deliverable", ""]
    if not clusters:
        lines.append("*No cluster reached the activation threshold, so no rows are "
                     "shown — this is not the same as a clean bill of health; see "
                     "Section 6 for what was and was not evaluated.*")
        lines.append("")
        return "\n".join(lines)

    clusters = sorted(clusters, key=lambda c: c.get("priority_rank") or 99)
    lines += ["| Rank | Cluster | Affected assertions | Risk | Response (N/T/E) | "
             "Specialist | Confidence |",
             "|---|---|---|---|---|---|---|"]
    for c in clusters:
        resp = c.get("recommended_response") or {}
        response_text = " / ".join(
            v for v in (resp.get("nature"), resp.get("timing"), resp.get("extent")) if v
        ) or "—"
        risk_label = "Significant risk" if c.get("significant_risk") else "Inherent risk"
        specialist = c.get("specialist_referral") or "—"
        lines.append(f"| {c.get('priority_rank', '')} | {c.get('theme', '')} "
                     f"({c.get('id', '')}) | {', '.join(c.get('affected_assertions') or [])} | "
                     f"{risk_label} | {response_text} | {specialist} | "
                     f"{c.get('diagnostic_confidence', '—')} |")
    lines.append("")
    return "\n".join(lines)


def _section_evidence(risk: dict[str, Any] | None) -> str:
    clusters = [c for c in (risk or {}).get("risk_clusters") or [] if c.get("raised")]
    lines = ["## 8. Evidence matrix & specialist referrals", ""]
    if not clusters:
        lines.append("*No raised cluster to request evidence for.*")
        lines.append("")
        return "\n".join(lines)
    for c in sorted(clusters, key=lambda c: c.get("priority_rank") or 99):
        if not c.get("evidence_request"):
            continue
        lines += [f"**{c.get('theme', '')} ({c.get('id', '')})**", "",
                 c["evidence_request"], ""]
        if c.get("specialist_referral") and c["specialist_referral"] != "None":
            lines += [f"*Specialist referral: {c['specialist_referral']}*", ""]
    return "\n".join(lines)


_CAVEATS = """## 9. Planning summary

These are candidate priorities for the audit team's decision. The outputs above
are leads for audit attention, not findings; composites are adapted for
screening, not predictive; benchmarks are used only when supplied; and the
audit team decides the audit strategy, scope and materiality — this report
informs but does not decide the plan.
"""


def to_markdown(
    dash: dict[str, Any],
    profile: dict[str, Any] | None,
    health: dict[str, Any] | None,
    trends: dict[str, Any] | None,
    risk: dict[str, Any] | None,
) -> str:
    """Assemble the XBRL Direct tab's five payloads into one downloadable report,
    in the spec's planning-first block order rather than the screen's fetch order."""
    sections = [
        _section_coverage(dash),
        _section_dashboard(dash),
        _section_profile(profile),
        _section_health(health),
        _section_trends(trends),
        _section_risk_clusters(risk),
        _section_matrix(risk),
        _section_evidence(risk),
        _CAVEATS,
    ]
    return "\n".join(sections)
