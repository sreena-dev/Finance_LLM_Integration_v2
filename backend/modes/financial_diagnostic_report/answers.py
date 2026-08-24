"""Turn a classified intent into an answer, by reading the evaluated report.

NOTHING HERE IS GENERATED
-------------------------
Every answer below is a status, a count, a list or a figure that was read out of
the evaluation, formatted into prose. There is no model in this path. A misread
intent can therefore produce an answer to the wrong question, but it cannot
produce an invented one — no number appears in an answer that was not computed
from a filing, and no diagnostic is described as firing unless the rule fired.

WHY THE CAVEATS ARE PART OF THE ANSWER, NOT A FOOTER
-----------------------------------------------------
An abstain that says "not evaluated" is a shrug. An abstain that says which
figures were missing, what the derivation would have been and which schedule
holds the inputs is a work instruction. Every projection here carries that
detail inline for the same reason the report itself does: the reader's next
action depends on it, and a caveat they have to go looking for is a caveat they
will not read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import intent as IT

# The panel is denominated in INR lakh throughout — the unit the extraction
# normalises to. Stated on every figure rather than assumed.
_UNIT = "INR lakh"


@dataclass(frozen=True)
class Answer:
    kind: str
    text: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    intent: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "text": self.text, "rows": self.rows,
                "provenance": self.provenance, "intent": self.intent}


def _name(evaluated) -> str:
    """The entity as a reader sees it.

    `entity_id` is the corpus key ("Coal_India") and stays exactly that in
    `rows` and `provenance`, where it is an identifier a caller may match on.
    Prose is for a person, and a person should not have to read an underscore.
    """
    return str(evaluated.entity_id).replace("_", " ")


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:,.0f}"


def _plural(n: int, one: str, many: str | None = None) -> str:
    return one if n == 1 else (many or one + "s")


def _capability_list() -> str:
    return "\n".join(f"- {line}" for line in IT.capabilities())


def _signals_of(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Every signal result in the report, each tagged with its cluster.

    A signal can belong to more than one cluster, so this deliberately yields one
    row per (signal, cluster) pair rather than de-duplicating — the cluster is
    part of what the reader is being told.
    """
    return [{**s, "cluster_id": c["cluster_id"], "theme": c["theme"]}
            for c in payload.get("risk_clusters", [])
            for s in c.get("contributing_signals", [])]


def _withheld_note(payload: dict[str, Any]) -> str:
    """§2.1 — measurement happened; interpretation did not. Said once, plainly."""
    profile = payload.get("business_profile") or {}
    if profile.get("model"):
        return ""
    return ("\n\n*No business model is in force for this entity, so §2.1 withholds "
            "interpretation: the diagnostics below are reported as measured, not as "
            "conclusions about the business.*")


# ---------------------------------------------------------------------------
# Projections
# ---------------------------------------------------------------------------

def _overview(ev, payload: dict[str, Any]) -> Answer:
    clusters = payload.get("risk_clusters", [])
    raised = [c for c in clusters if c["status"] == "RAISED"]
    abstained = [c for c in clusters if c["status"] == "ABSTAIN"]
    shortlist = payload.get("shortlist") or []

    years = ", ".join(ev.years) if ev.years else "no comparable years"
    head = (f"**{_name(ev)}** — {len(ev.years)} comparable "
            f"{_plural(len(ev.years), 'year')} ({years}), {ev.flavor}.\n\n")

    # Counted over SIGNALS, not only over themes. A theme abstains as soon as one
    # of its contributing diagnostics could not be evaluated, so a report where
    # six diagnostics ran cleanly and two were blocked shows eight abstaining
    # themes — and saying only "0 themes evaluated" would describe that as an
    # empty read, which it is not. The reader needs to know both numbers.
    signals = _signals_of(payload)
    seen: dict[str, str] = {}
    for row in signals:
        seen.setdefault(row["signal_id"], row["status"])
    ran = [sid for sid, status in seen.items() if status in ("FIRED", "NOT_FIRED")]
    fired_ids = [sid for sid, status in seen.items() if status == "FIRED"]
    unevaluated = [sid for sid, status in seen.items()
                   if status not in ("FIRED", "NOT_FIRED")]

    if not raised:
        body = (f"**No risk theme is raised.**\n\n"
                f"- {len(ran)} of {len(seen)} diagnostics were evaluated; "
                f"{len(fired_ids)} fired.\n"
                f"- {len(unevaluated)} could not be evaluated, which is why "
                f"{len(abstained)} of {len(clusters)} themes abstain rather than "
                f"reporting a clean result: a theme abstains as soon as one of its "
                f"diagnostics is blocked.\n\n"
                f"That is not a clean bill of health. Ask what is missing to see which "
                f"figures would let the remaining diagnostics run.")
    else:
        lines = []
        for c in sorted(raised, key=lambda x: (x.get("priority_rank") or 99)):
            fired = [s["signal_id"] for s in c.get("contributing_signals", [])
                     if s["status"] == "FIRED"]
            lines.append(
                f"**{c['cluster_id']}** — {c['theme']} "
                f"({len(fired)} of {len(c.get('contributing_signals', []))} diagnostics "
                f"firing: {', '.join(fired) or 'none'})")
        body = (f"**{len(raised)} risk {_plural(len(raised), 'theme')} raised** of "
                f"{len(clusters)}:\n\n- " + "\n- ".join(lines))
        if shortlist:
            body += f"\n\nShortlisted for planning attention: {', '.join(shortlist)}."

    if abstained:
        body += (f"\n\n{len(abstained)} theme{'s' if len(abstained) != 1 else ''} could "
                 f"not be evaluated: {', '.join(c['cluster_id'] for c in abstained)}. "
                 f"Ask what is missing to see which figures would unlock them.")

    return Answer("overview", head + body + _withheld_note(payload),
                  rows=[{"cluster_id": c["cluster_id"], "theme": c["theme"],
                         "status": c["status"],
                         "priority_rank": c.get("priority_rank")} for c in clusters])


def _cluster(ev, payload: dict[str, Any], cluster_id: str) -> Answer:
    match = next((c for c in payload.get("risk_clusters", [])
                  if c["cluster_id"] == cluster_id), None)
    if match is None:
        return Answer("cluster",
                      f"`{cluster_id}` is not a risk theme in the registry in force.")

    signals = match.get("contributing_signals", [])
    fired = [s for s in signals if s["status"] == "FIRED"]
    not_fired = [s for s in signals if s["status"] == "NOT_FIRED"]
    unevaluated = [s for s in signals if s["status"] in ("ABSTAIN", "NOT_APPLICABLE",
                                                         "SUPPRESSED")]

    verdict = {
        "RAISED": f"is **raised** for {_name(ev)}",
        "NOT_RAISED": f"is **not raised** for {_name(ev)}",
        "ABSTAIN": f"**could not be evaluated** for {_name(ev)}",
        "NOT_APPLICABLE": f"is **not applicable** to {_name(ev)}",
    }.get(match["status"], f"is {match['status']}")

    parts = [f"**{cluster_id} — {match['theme']}** {verdict}."]
    if match.get("reason"):
        parts.append(match["reason"])

    if fired:
        parts.append("**Firing:**\n" + "\n".join(
            f"- `{s['signal_id']}` — {s.get('observation') or s.get('reason') or 'fired'}"
            + (f" *(confidence {s['confidence']})*" if s.get("confidence") else "")
            for s in fired))
    if not_fired:
        parts.append("**Evaluated, did not fire:** "
                     + ", ".join(f"`{s['signal_id']}`" for s in not_fired))
    if unevaluated:
        parts.append("**Not evaluated:**\n" + "\n".join(
            f"- `{s['signal_id']}` — {s.get('reason') or 'no reason recorded'}"
            + (f" (missing: {', '.join(s['missing_inputs'])})"
               if s.get("missing_inputs") else "")
            for s in unevaluated))

    if match.get("interpretation_withheld"):
        parts.append(f"*{match['interpretation_withheld']}*")

    return Answer("cluster", "\n\n".join(parts), rows=signals)


def _signal(ev, payload: dict[str, Any], signal_id: str) -> Answer:
    from fdr import clusters as CL, signals as SG

    spec = next((s for s in SG.SIGNALS if s.id == signal_id), None)
    if spec is None:
        return Answer("signal", f"`{signal_id}` is not a diagnostic in the registry.")

    results = [s for s in _signals_of(payload) if s["signal_id"] == signal_id]
    title = f"**{signal_id} — {spec.title}**"

    if not results:
        # In the registry, absent from the report: the signal belongs to no
        # cluster that was packaged, which is a scope fact, not a data gap.
        return Answer("signal",
                      f"{title}\n\nThis diagnostic is in the registry but was not "
                      f"packaged into any risk theme for this run. Themes it belongs "
                      f"to: {', '.join(CL.clusters_for(signal_id)) or 'none'}.")

    result = results[0]
    status_text = {
        "FIRED": "**is firing**",
        "NOT_FIRED": "was evaluated and **did not fire**",
        "ABSTAIN": "**was not evaluated**",
        "NOT_APPLICABLE": "is **not applicable**",
        "SUPPRESSED": "was **suppressed**",
    }.get(result["status"], result["status"])

    parts = [f"{title} {status_text} for {_name(ev)}."]

    if result.get("observation"):
        parts.append(result["observation"])
    if result.get("trace"):
        parts.append(f"Arithmetic: `{result['trace']}`")
    if result.get("reason"):
        parts.append(result["reason"])
    if result.get("missing_inputs"):
        parts.append(f"**Missing inputs:** {', '.join(result['missing_inputs'])}.")
    if result.get("confidence"):
        parts.append(f"Confidence **{result['confidence']}**"
                     + (f" — {result['confidence_basis']}"
                        if result.get("confidence_basis") else "") + ".")
    if result.get("formula"):
        parts.append(f"Derivation: `{result['formula']}`")
    if result.get("ar_source"):
        parts.append(f"Read from: {', '.join(result['ar_source'])}.")
    if result.get("proxies_used"):
        parts.append(f"⚠ Stand-ins applied: {', '.join(result['proxies_used'])}.")

    themes = ", ".join(f"{r['cluster_id']} ({r['theme']})" for r in results)
    parts.append(f"Contributes to: {themes}.")

    return Answer("signal", "\n\n".join(parts), rows=results)


def _figure(ev, payload: dict[str, Any], canonical_key: str) -> Answer:
    from fdr import signals as SG

    series = ev.panel.series(canonical_key)
    label = canonical_key.replace("_", " ")

    if not series:
        # Say WHY it is absent, and in which years, rather than reporting a gap
        # as a zero. `diagnose` distinguishes never-bound from bound-in-some-years.
        detail = ev.panel.diagnose([canonical_key]).get(canonical_key, {})
        note = detail.get("reason") or detail.get("status") or "not bound in any year"
        return Answer(
            "figure",
            f"**{label.title()}** is not available for {_name(ev)} — {note}.\n\n"
            f"It is absent, not zero. A figure that could not be bound from the "
            f"statements is reported as missing so a diagnostic built on it abstains "
            f"rather than computing on a false value.",
            rows=[])

    lines = [f"{year} — {_fmt(value)}" for year, value in series]
    text = (f"**{label.title()}** for {_name(ev)} ({_UNIT}), "
            f"{ev.flavor}:\n\n- " + "\n- ".join(lines))

    if len(series) >= 2:
        first, last = series[0][1], series[-1][1]
        if first:
            change = (last - first) / abs(first) * 100.0
            text += (f"\n\n{series[-1][0]} is {change:+.1f}% against {series[0][0]}. "
                     f"That is a disclosed movement, not an assessment of it.")

    consumers = [s.id for s in SG.SIGNALS if canonical_key in s.inputs]
    if consumers:
        text += f"\n\nDiagnostics that read this figure: {', '.join(consumers)}."

    return Answer("figure", text,
                  rows=[{"fy_label": y, "value": v, "canonical_key": canonical_key,
                         "unit": _UNIT} for y, v in series])


def _coverage(ev, payload: dict[str, Any], readiness_rows: list[dict]) -> Answer:
    by_state: dict[str, list[str]] = {}
    for row in readiness_rows:
        by_state.setdefault(row["state"], []).append(row["signal_id"])

    coverage = payload.get("coverage") or {}
    facts = payload.get("fact_coverage") or {}

    parts = [
        f"**Coverage for {_name(ev)}** — {len(ev.years)} comparable "
        f"{_plural(len(ev.years), 'year')}"
        + (f" ({', '.join(ev.years)})" if ev.years else "")
        + f", input quality grade **{coverage.get('input_quality_grade') or 'not graded'}**."
    ]

    ready = by_state.get("READY", [])
    parts.append(f"**{len(ready)} of {len(readiness_rows)} diagnostics can be evaluated.**")

    order = [("MISSING_INPUTS", "blocked for want of a figure"),
             ("SHORT_SERIES", "needs more comparable years"),
             ("NO_RULE", "not built yet"),
             ("NOTE_LEVEL", "needs note-level extraction, not built")]
    for state, description in order:
        ids = by_state.get(state, [])
        if ids:
            parts.append(f"- **{len(ids)}** {description}: {', '.join(sorted(ids))}")

    if facts:
        # Key names come from `fdr.source.SourceReport.to_dict`. Read them off
        # that contract rather than guessing: a mistyped key here degrades
        # silently to "0 figures", which reads as an empty corpus and is the
        # single most misleading thing this surface could say.
        parts.append(
            f"Read from the corpus: {facts.get('filings_read', 0)} of "
            f"{facts.get('filings_found', 0)} filings, "
            f"{facts.get('facts_trusted', 0)} of {facts.get('facts_total', 0)} "
            f"extracted figures trusted"
            + (f", {facts['withheld_contradicted']} withheld for failing a tie-out"
               if facts.get("withheld_contradicted") else "")
            + (f", {facts['withheld_no_scale']} withheld for an unresolved scale"
               if facts.get("withheld_no_scale") else "") + ".")
        if facts.get("filings_failed"):
            parts.append(f"⚠ {facts['filings_failed']} filing(s) could not be read at all.")

    if coverage.get("limitations"):
        parts.append("**Limitations:**\n" + "\n".join(
            f"- {line}" for line in coverage["limitations"]))

    return Answer("coverage", "\n\n".join(parts), rows=readiness_rows)


def _blocked(ev, payload: dict[str, Any], readiness_rows: list[dict]) -> Answer:
    # Count over BOTH sources of truth about a gap: what the readiness view says
    # is unbound, and what an abstained signal recorded as missing. They agree in
    # the normal case; where they differ the union is the honest answer.
    blocking: dict[str, set[str]] = {}
    for row in readiness_rows:
        for key in row.get("missing", []):
            blocking.setdefault(key, set()).add(row["signal_id"])
    for result in _signals_of(payload):
        if result["status"] == "ABSTAIN":
            for key in result.get("missing_inputs", []):
                blocking.setdefault(key, set()).add(result["signal_id"])

    if not blocking:
        return Answer("blocked",
                      f"No diagnostic for {_name(ev)} is held back by a missing "
                      f"figure. Anything not evaluated is out of scope rather than "
                      f"blocked — ask what is missing for the full picture.")

    ranked = sorted(blocking.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    lines = [f"`{key}` — blocks {len(ids)} {_plural(len(ids), 'diagnostic')} "
             f"({', '.join(sorted(ids))})" for key, ids in ranked]
    top_key, top_ids = ranked[0]

    return Answer(
        "blocked",
        f"For **{_name(ev)}**, the figure holding back the most diagnostics is "
        f"**{top_key.replace('_', ' ')}** ({len(top_ids)} of them).\n\n- "
        + "\n- ".join(lines)
        + "\n\nEach of these is a figure the statements should carry but which could not "
          "be bound from this entity's filings. Binding it is what unlocks the "
          "diagnostics listed beside it.",
        rows=[{"canonical_key": key, "signals": sorted(ids), "count": len(ids)}
              for key, ids in ranked])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build(decision: IT.Intent, evaluated=None, readiness_rows: list[dict] | None = None
          ) -> Answer:
    """The single dispatch. Every branch either reads the evaluation or refuses.

    The three no-data branches come first so that a refusal, an ambiguity or an
    unsupported question never triggers an extraction — the user gets the answer
    immediately rather than after a ten-second read they did not need.
    """
    meta = decision.to_dict()

    if decision.kind == IT.REFUSED:
        return Answer("refused",
                      decision.detail + "\n\nWhat it can answer instead:\n\n"
                      + _capability_list(), intent=meta)

    if decision.kind == IT.AMBIGUOUS:
        options = "\n".join(f"- {a}" for a in decision.alternatives)
        return Answer("ambiguous",
                      f"{decision.detail} Naming one of these settles it:\n\n{options}",
                      intent=meta)

    if decision.kind == IT.UNSUPPORTED:
        return Answer("unsupported",
                      f"{decision.detail}\n\nWhat this report can answer:\n\n"
                      + _capability_list(), intent=meta)

    if evaluated is None:                       # defensive; the router always supplies it
        return Answer("unsupported",
                      "This question needs the entity's figures, which were not read.",
                      intent=meta)

    payload = evaluated.payload
    rows = readiness_rows or []

    if decision.kind == IT.CLUSTER and decision.cluster_id:
        answer = _cluster(evaluated, payload, decision.cluster_id)
    elif decision.kind == IT.SIGNAL and decision.signal_id:
        answer = _signal(evaluated, payload, decision.signal_id)
    elif decision.kind == IT.FIGURE and decision.canonical_key:
        answer = _figure(evaluated, payload, decision.canonical_key)
    elif decision.kind == IT.COVERAGE:
        answer = _coverage(evaluated, payload, rows)
    elif decision.kind == IT.BLOCKED:
        answer = _blocked(evaluated, payload, rows)
    else:
        answer = _overview(evaluated, payload)

    return Answer(answer.kind, answer.text, answer.rows,
                  provenance=evaluated.provenance(), intent=meta)
