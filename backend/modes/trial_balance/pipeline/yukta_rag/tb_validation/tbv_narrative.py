"""Mechanical narrative assembly + guardrails.

Only ever states factual, mechanical observations ("Ledger X increased by
Y%, exceeding the materiality threshold" / "GL Code 4001 fails TB-005 by
₹50, outside the 5% tolerance"). Never an audit opinion, conclusion, or
assurance statement — enforced twice: by construction (every sentence
builder below only emits mechanical facts) AND by a defense-in-depth
regex guard that asserts none of the forbidden words appear before the
narrative is ever returned.
"""

from __future__ import annotations

import re

# Hardcoded, always appended verbatim — never dependent on prompt/LLM adherence.
DISCLAIMER = (
    "This observation is a risk indicator from the trial balance only. It is not a "
    "conclusion on misstatement, fraud, non-compliance, irregularity, recoverability or "
    "going concern. The matter requires corroboration through the audit procedures and "
    "evidence listed."
)

# checked against the narrative BODY only (before the disclaimer is appended,
# since the disclaimer itself must legitimately name these concepts to deny them).
_FORBIDDEN_RE = re.compile(
    r"\bopinion\b|\bcertif(?:y|ies|ied)\b|\bconfirm(?:s|ed)?\b|\bprov(?:es|en)\b|"
    r"\bverif(?:y|ies|ied)\b|\btrue and fair\b|\bcorrectness\b|\bmisstat(?:ement|ed)\b|"
    r"\bregularity\b|\birregular(?:ity)?\b|\bfraud(?:ulent)?\b|\bnon-?compliant?\b|"
    r"\bnon-?compliance\b|\brecoverab(?:le|ility)\b|\bgoing concern\b",
    re.I,
)


class ForbiddenNarrativeWordError(RuntimeError):
    """Raised if a generated narrative body contains a forbidden assurance
    word — should never actually fire given the mechanical sentence
    builders below, but is a deliberate belt-and-suspenders guard."""


def _check_no_forbidden_words(body: str) -> None:
    m = _FORBIDDEN_RE.search(body)
    if m:
        raise ForbiddenNarrativeWordError(
            f"Generated narrative contains a forbidden assurance word: {m.group()!r}")


def _fmt(v: float | None) -> str:
    return f"{v:,.2f}" if v is not None else "—"


def build_summary_narrative(*, table_label: str, prior_label: str | None,
                           layer1_results: dict, structural_result: dict | None,
                           variance_rows: list[dict] | None,
                           formula_flags: list[dict] | None, phase_reached: str) -> str:
    """Assemble a mechanical, source-cited summary from already-computed
    structured results — never re-derives or asserts anything new.

    ``table_label`` is always the CY (or single-table) label; ``prior_label``
    is PY's label, or None in single-table mode. Each rule result is cited
    against its OWN source table explicitly (single/py/cy) — never zipped
    positionally, which previously caused PY's findings to be mislabeled
    with CY's filename and vice versa (caught live against the real GAIL
    FY24-25/FY23-24 pair)."""
    lines: list[str] = []
    single_results = layer1_results.get("single")
    py_results = layer1_results.get("py")
    cy_results = layer1_results.get("cy")
    cross_year_results = layer1_results.get("cross_year_results") or []

    if phase_reached == "HALTED":
        halted: list[tuple[dict, str]] = []
        if single_results is not None:
            halted += [(r, table_label) for r in single_results if r["status"] == "HALT"]
        else:
            halted += [(r, prior_label) for r in (py_results or []) if r["status"] == "HALT"]
            halted += [(r, table_label) for r in (cy_results or []) if r["status"] == "HALT"]
            halted += [(r, f"{table_label}/{prior_label}") for r in cross_year_results
                      if r["status"] == "HALT"]
        scope = table_label if single_results is not None else f"{table_label} vs {prior_label}"
        lines.append(f"Validation halted at Layer 1 for {scope} "
                     f"({len(halted)} rule(s) resolved HALT). Downstream layers were not run.")
        for h, src in halted:
            lines.append(f"- {h['rule_id']}: {h['message']} (source: {src}).")
    else:
        if single_results is not None:
            for w in [r for r in single_results if r["status"] == "WARNING"]:
                lines.append(f"- {w['rule_id']}: {w['message']} (source: {table_label}).")
        else:
            for w in [r for r in (py_results or []) if r["status"] == "WARNING"]:
                lines.append(f"- {w['rule_id']}: {w['message']} (source: {prior_label}).")
            for w in [r for r in (cy_results or []) if r["status"] == "WARNING"]:
                lines.append(f"- {w['rule_id']}: {w['message']} (source: {table_label}).")
            for w in [r for r in cross_year_results if r["status"] == "WARNING"]:
                lines.append(f"- {w['rule_id']}: {w['message']} (source: {table_label}/{prior_label}).")

        if structural_result:
            lines.append(f"Structural comparison ({prior_label} -> {table_label}): "
                         f"{structural_result['n_new']} new ledger(s), "
                         f"{structural_result['n_removed']} removed ledger(s), "
                         f"net delta {structural_result['delta_count']}.")

        if variance_rows:
            flagged = [r for r in variance_rows if r["flag"] in ("HIGH_PRIORITY", "MEDIUM", "NEW_ENTRY", "DROPPED")]
            for r in flagged[:25]:
                if r["flag"] == "NEW_ENTRY":
                    lines.append(f"- Ledger {r['ledger_code']} is new in {table_label} "
                                 f"({_fmt(r['cy_balance'])}), absent in {prior_label} (source: {table_label}/{prior_label}).")
                elif r["flag"] == "DROPPED":
                    lines.append(f"- Ledger {r['ledger_code']} present in {prior_label} "
                                 f"({_fmt(r['py_balance'])}) is absent in {table_label} (source: {table_label}/{prior_label}).")
                else:
                    lines.append(f"- Ledger {r['ledger_code']} changed from {_fmt(r['py_balance'])} to "
                                 f"{_fmt(r['cy_balance'])} ({r['variance_pct']}%), flagged {r['flag']} "
                                 f"(source: {table_label}/{prior_label}).")

        if formula_flags:
            scope = table_label if not prior_label else f"{table_label} and/or {prior_label}"
            lines.append(f"{len(formula_flags)} cell(s) contain a live formula rather than a "
                         f"static value (informational; source: {scope}).")

    if not lines:
        lines.append(f"No warnings or halts identified for {table_label}"
                     f"{' vs ' + prior_label if prior_label else ''}.")

    body = "\n".join(lines)
    _check_no_forbidden_words(body)
    return body + "\n\n" + DISCLAIMER
