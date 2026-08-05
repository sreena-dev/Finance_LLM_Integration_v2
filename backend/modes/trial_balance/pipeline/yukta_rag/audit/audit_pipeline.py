"""Audit-mode pipeline (spec): normalise -> map -> screen -> relationships ->
materiality -> findings -> safe-language narrative.

All numbers are computed deterministically; the audit analyst only narrates them
in the spec response order and safe language. The deterministic report is safe by
construction and is used as the fallback whenever the model is unavailable, too
thin, or emits forbidden (conclusion) wording.
"""

from __future__ import annotations

import logging
import re

from yukta_rag.agents.audit_agents import (build_audit_analyst,
                                            build_doc_evidence_extractor,
                                            contains_forbidden)
from yukta_rag.audit.audit_citations import (citations_for_items,
                                             parse_standard_number,
                                             scenario_query)
from yukta_rag.audit.audit_doc_evidence import extract_doc_risk_items
from yukta_rag.audit.audit_findings import (NO_OPINION_CLOSING, SAFE_LIMITATION,
                                            build_findings)
from yukta_rag.audit.audit_gaps import (financial_snapshot, ind_as_gaps,
                                        snapshot_contributors)
from yukta_rag.audit.audit_workbook import build_audit_workbook
from yukta_rag.audit.audit_sections import (audit_focus_sequence,
                                            internal_control_risks,
                                            schedule_iii_disclosure_gaps,
                                            statutory_compliance_risks)
from yukta_rag.audit.audit_mapping import map_accounts
from yukta_rag.audit.audit_materiality import materiality_proxies, structure_ratios
from yukta_rag.audit.audit_normalize import input_quality_note
from yukta_rag.audit.audit_relationships import analyze_relationships
from yukta_rag.audit.audit_screen import (find_clearing_series, find_duplicate_rows,
                                          find_mirrored_pairs, find_offsetting_activity,
                                          largest_balances, screen_tb)
from yukta_rag.audit.audit_variance import opening_vs_closing
from yukta_rag.core.llm import build_llm
from yukta_rag.core.run_log import build_run_log
from yukta_rag.trial_balance.tb_compare import compare_trial_balances, merge_trial_balances
from yukta_rag.trial_balance.tb_tools import (classify_tb, compute_ratios,
                                              compute_statements, compute_variance)
from yukta_rag.trial_balance.trial_balance import get_trial_balance

logger = logging.getLogger(__name__)
# item 10 — explicit level so the narration-rejection log line is actually
# visible: this app has no logging.basicConfig, so an unset logger level
# resolves to the root default (WARNING) and silently drops .info() calls
# even though a handler (via uvicorn) is present to display them.
logger.setLevel(logging.INFO)


def _fmt(n) -> str:
    try:
        return f"{float(n):,.0f}"
    except (TypeError, ValueError):
        return str(n)


# client-format rules A1/A2/F16/G19/G20/B5 — structural markers that MUST and MUST NOT
# appear in an accepted LLM narration, so a stale/drifted prompt can never silently
# regress the report back to an old structure (defense in depth alongside
# contains_forbidden): if the narration fails this check, the deterministic report
# (already safe and rule-compliant by construction) is used instead.
_REQUIRED_MARKERS = ("notice to the reader", "engagement context and assumptions",
                    "fsli summary", "focus areas")
_BANNED_MARKERS = ("prioritised findings", "consolidated evidence-request list",
                   "management-query list", "accounting framework:")


def _structurally_compliant(text: str) -> bool:
    low = text.lower()
    return (all(m in low for m in _REQUIRED_MARKERS)
            and not any(m in low for m in _BANNED_MARKERS))


# the vendored LLM-continuation framework (vendor/yukta/core/Agent/agent.py)
# appends a developer-facing tracking marker like "[✅ Response completed with
# 2 continuation(s)]" to the END of the narration whenever it needed
# continuations — never meant for a client to see. The completeness checks
# above run on the raw narration (they specifically look for the "still
# incomplete" variant of this same marker), so this strip is applied only to
# the string actually assigned to the accepted report, not before.
_CONTINUATION_MARKER_RE = re.compile(r"\n*\[(?:✅|⚠️)[^\]]*continuation[^\]]*\]\s*$", re.I)


def _strip_continuation_marker(text: str) -> str:
    return _CONTINUATION_MARKER_RE.sub("", text).rstrip()


def _select_base_period_index(periods: list[str]) -> int:
    """Requirement 2 — when a single file contains >2 periods' worth of
    columns, pick the most recent as the base year: parse a 4-digit year out
    of each label and take the max; if labels aren't all parseable, fall back
    to the existing convention that column 1 (index 0, leftmost) is most
    recent — the same convention `_period_labels()`'s own defaults already
    assume for the 2-period case."""
    years = []
    for label in periods:
        m = re.search(r"(20\d{2}|19\d{2})", label or "")
        years.append(int(m.group(1)) if m else None)
    if all(y is not None for y in years):
        return years.index(max(years))
    return 0


def _slice_period(tb: dict, period_index: int) -> dict:
    """A single-period *view* of one column-block within a multi-period `tb`
    dict, shaped exactly like a natively single-period parse (`p1_debit`/
    `p1_credit`/`p1_net` only) — so it can be fed into the existing,
    unchanged `compare_trial_balances()` exactly like the two-*file* path
    does, reusing 100% of that non-merging comparison logic rather than
    writing a new one. `period_index` is 0-based into `tb["periods"]`."""
    p = period_index + 1
    accounts = []
    for a in tb["accounts"]:
        rec = dict(a)
        rec["p1_debit"] = a.get(f"p{p}_debit", 0.0)
        rec["p1_credit"] = a.get(f"p{p}_credit", 0.0)
        rec["p1_net"] = a.get(f"p{p}_net", 0.0)
        accounts.append(rec)
    sliced = dict(tb)
    sliced["accounts"] = accounts
    sliced["periods"] = [tb["periods"][period_index]]
    return sliced


def _has_malformed_table_rows(text: str) -> bool:
    """Detect a splice/duplication artifact from the LLM's own continuation
    mechanism on very large reports: two table rows glued together with no
    line break in between (e.g. "...-143,569| Borrowings | -681,942,766,405
    ..." — a row cut off mid-cell, immediately followed by a fresh row with
    no newline). The general signature: a data row with MORE "|" delimiters
    than its own table's header row. Reset the expected count whenever a
    non-"|" line ends a table block."""
    header_pipes = None
    for raw in text.split("\n"):
        line = raw.strip()
        if not line.startswith("|"):
            header_pipes = None
            continue
        pipes = line.count("|")
        if re.match(r"^\|[\s:\-|]+\|$", line):  # separator row, e.g. |---|---:|
            header_pipes = pipes
            continue
        if header_pipes is None:
            header_pipes = pipes  # first row of a new table block = its header
            continue
        if pipes > header_pipes:
            return True
    return False


def build_audit_report(entity: str, iq: dict, mapping: dict, screen: dict,
                       relationships: dict, materiality: dict, ratios: dict,
                       findings: dict, snapshot: dict | None = None,
                       gaps: dict | None = None, context_note: str | None = None,
                       quantified: dict | None = None, sections: dict | None = None,
                       focus: list | None = None, period_ratios: dict | None = None,
                       variance: dict | None = None, run_log: dict | None = None,
                       dup_rows: dict | None = None, offsetting: dict | None = None,
                       filename: str | None = None, sheet: str | None = None,
                       clearing: dict | None = None, largest: list | None = None,
                       opening_closing: dict | None = None,
                       comparison: dict | None = None,
                       mirror_pairs: dict | None = None,
                       base_year_note: str | None = None) -> str:
    """Deterministic, safe-by-construction Markdown report — client-approved format."""
    sections = sections or {}
    n_accounts = len(mapping["accounts"])
    excluded_names = (set((clearing or {}).get("excluded_names", []))
                     | set((mirror_pairs or {}).get("excluded_names", [])))

    # Notice to the Reader (client-format rule A1)
    src_bits = []
    if filename:
        src_bits.append(f'file "{filename}"')
    if sheet:
        src_bits.append(f'sheet "{sheet}"')
    src_desc = (", ".join(src_bits) + ", ") if src_bits else ""
    L = [f"# Trial Balance Audit Analytics — {entity}", "",
         f"> **Notice to the Reader.** This note is generated from the trial balance "
         f"{src_desc}comprising {n_accounts} account(s). No external facts about the "
         "entity were used — the analysis is confined to what the trial balance itself "
         "shows. This is a risk-assessment / planning aid only. It contains no audit "
         "opinion or conclusion on misstatement, fraud, non-compliance, recoverability, "
         "going concern or irregularity.", ""]

    # Engagement Context and Assumptions (client-format rule A2)
    grouping_source = mapping.get("grouping_source", "keyword_inference")
    grouping_label = ("management-supplied chart-of-accounts / FSLI grouping"
                      if grouping_source == "management_grouping"
                      else "keyword-based inference (no chart-of-accounts supplied)")
    currency_note = (f"{iq['currency']} (assumed — not stated in file)"
                     if iq.get("currency_assumed") else iq["currency"])
    erp_basis = ("column layout / GL code pattern" if "inferred" in iq["source_system"].lower()
                else "as stated" if iq["source_system"] != "unknown" else "not determinable")
    L += ["## Engagement Context and Assumptions",
          "| Item | Assumption Made | Basis/Confirmation Needed |", "|---|---|---|",
          "| Standalone vs consolidated | Assumed standalone | Confirm with engagement "
          "letter / entity structure |",
          f"| Currency and scale | Currency: {currency_note}; scale: {iq['scale']} | "
          "Inferred from anchor account magnitudes; confirm against source records |",
          f"| ERP / source system | {iq['source_system']} | Basis: {erp_basis} |",
          f"| Period / opening balance basis | Period: {iq['period_end']}; opening "
          f"balances {'present' if iq.get('opening_present') else 'not present'} | "
          f"Classification source: {grouping_label} |"]
    if base_year_note:
        L.append(f"| Multi-year file — base year selection | {base_year_note} | "
                 "Auto-detected from period column labels; confirm against the source file |")
    L.append("")

    if context_note:
        L += ["## Context", context_note, ""]

    # Input quality & normalisation (client-format rule B3, B4, B5, D12 pass/fail check)
    classification_sentence = f"Classification basis: {grouping_label}."
    L += ["## Input quality & normalisation",
          f"- Source system: {iq['source_system']}; period: {iq['period_end']}; "
          f"currency: {iq['currency']}; scale: {iq['scale']}.",
          f"- Balanced: {'PASS' if iq['balanced'] else 'FAIL'} (residual "
          f"{_fmt(iq['trial_balance_residual'])}); comparative present: "
          f"{iq['comparative_present']}; movement columns: {iq['movement_columns_present']}.",
          f"- {classification_sentence} {iq['n_unmapped']} of {iq['n_accounts']} "
          "account(s) could not be classified with confidence and are listed as "
          "unmapped below."]
    if iq.get("engagement_context", "unknown") == "unknown":
        L.append("- Engagement context not provided — treating downstream conclusions "
                 "under the most conservative framing (standalone risk indicators only, "
                 "no assumption of the engagement's scope or reporting deadline).")
    else:
        L.append(f"- Engagement context: {iq['engagement_context']}.")
    for f in iq["data_quality_flags"]:
        L.append(f"  - {f}")
    if iq.get("opening_movement_check"):
        om = iq["opening_movement_check"]
        check_pass = om["mismatches"] == 0
        L.append("- Opening + movement = closing check: "
                 + ("PASS" if check_pass else
                    f"FAIL ({om['mismatches']} of {om['checked']} mismatch(es))"))

    # FSLI Summary table (client-format rule C8)
    L += ["", "## FSLI Summary",
          "| FSLI Group | No. of GL Accounts | Amount | Mapping Basis/Caveat |",
          "|---|---:|---:|---|"]
    acct_by_name = {a["name"]: a for a in mapping["accounts"]}
    for fsli, names in list(mapping["fsli_groups"].items())[:20]:
        members = [acct_by_name[n] for n in names
                  if n in acct_by_name and n not in excluded_names]
        if not members:
            continue
        amount = round(sum(m["net"] for m in members), 2)
        confs = {m["mapping_confidence"] for m in members}
        srcs = {m.get("mapping_source") for m in members}
        if "management_grouping" in srcs:
            basis = "management grouping"
        elif confs == {"high"}:
            basis = "keyword match, high confidence"
        elif "low" in confs or "unmapped" in confs:
            basis = "keyword match, low confidence — verify"
        else:
            basis = "keyword match, generic default — verify"
        L.append(f"| {fsli} | {len(members)} | {_fmt(amount)} | {basis} |")
        # "of which" sub-lines MUST stay valid table rows (same column count, blank
        # cells where not applicable) — a bare bullet line here breaks the markdown
        # table parser mid-block and everything after renders as raw pipe text.
        non_current = [m for m in members if "non-current" in m["name"].lower()
                       or "non current" in m["name"].lower()]
        current = [m for m in members if m not in non_current
                  and " current" in f" {m['name'].lower()}"]
        sub_total = 0.0
        if non_current and len(non_current) < len(members):
            nc_sum = round(sum(m["net"] for m in non_current), 2)
            L.append(f"| ↳ of which: non-current | | {_fmt(nc_sum)} | |")
            sub_total += nc_sum
        if current:
            cur_sum = round(sum(m["net"] for m in current), 2)
            L.append(f"| ↳ of which: current | | {_fmt(cur_sum)} | |")
            sub_total += cur_sum
        # the keyword-based split is inherently partial (most account names carry
        # neither word) — surface the unclassified residual explicitly rather than
        # let the sub-lines silently look like a complete breakdown.
        if (non_current or current) and abs(round(amount - sub_total, 2)) > 0.01:
            L.append(f"| ↳ of which: other (unclassified) | | "
                     f"{_fmt(round(amount - sub_total, 2))} | |")
    if mapping["sensitive_summary"]:
        L.append("")
        L.append("Sensitive tags: " + ", ".join(f"{k} ({v})" for k, v in
                                                 mapping["sensitive_summary"].items()))
    if clearing and clearing.get("groups"):
        L.append("")
        L.append(f"_{len(clearing['groups'])} GL series excluded from the above as internal "
                 f"clearing/inter-unit/statistical postings (combined net "
                 f"{_fmt(clearing['total_excluded_net'])}) — see the Financial Snapshot "
                 "memorandum line and Focus Areas for any unresolved series._")
    if mirror_pairs and mirror_pairs.get("pairs"):
        L.append("")
        L.append(f"_{len(mirror_pairs['pairs'])} mirrored inter-unit/clearing pair(s) excluded "
                 f"from the above (combined net {_fmt(mirror_pairs['total_excluded_net'])}) — "
                 "see the Financial Snapshot memorandum line and Focus Areas for any "
                 "unresolved pair._")
        L.append("")
        L.append("**Memorandum: excluded inter-unit/clearing pairs:**")
        for p in mirror_pairs["pairs"]:
            a, b = p["accounts"]
            status = "nets to ~nil" if p["resolved"] else "does NOT net to nil — see Focus Areas"
            L.append(f"- {a.get('code') or '—'} {a['name']} ({_fmt(a['net'])}) ↔ "
                     f"{b.get('code') or '—'} {b['name']} ({_fmt(b['net'])}) — {status}")

    # Financial Snapshot (client-format rule D11)
    low_coverage_warning = (
        f"> ⚠ **Low classification coverage: only {iq['value_coverage_pct']}% of total ledger "
        f"value is classified** ({iq['n_contra_pairs_excluded']} contra/rollup pair(s) excluded "
        "as immaterial). The figures below may not reflect true magnitudes — do not use them for "
        "trend analysis until chart-of-accounts mapping is improved.")
    if snapshot and snapshot.get("rows"):
        periods = snapshot["periods"]
        if iq.get("low_value_coverage"):
            L += ["", low_coverage_warning]
        L += ["", "## Financial Snapshot (Provisional)"]
        reasons = []
        if not iq["balanced"]:
            reasons.append("trial balance does not tie out")
        if clearing and clearing.get("groups"):
            reasons.append(f"{len(clearing['groups'])} GL series excluded as internal "
                           "clearing/inter-unit postings")
        if mirror_pairs and mirror_pairs.get("pairs"):
            reasons.append(f"{len(mirror_pairs['pairs'])} mirrored inter-unit/clearing "
                           "pair(s) excluded")
        if iq["n_unmapped"]:
            reasons.append(f"{iq['n_unmapped']} account(s) unmapped/uncertain FSLI placement")
        if reasons:
            L.append(f"_Provisional — does not self-balance to the trial balance total. "
                     f"Reasons: {'; '.join(reasons)}._")
        L += ["| Metric | " + " | ".join(periods) + " |", "|---|" + "---:|" * len(periods)]
        for r in snapshot["rows"]:
            L.append(f"| {r['metric']} | " + " | ".join(_fmt(v) for v in r["values"]) + " |")
        if clearing and clearing.get("groups"):
            L.append(f"| *Memorandum: excluded clearing/inter-unit series (net)* | "
                     + " | ".join([_fmt(clearing["total_excluded_net"])] + ["—"] * (len(periods) - 1)) + " |")
        if mirror_pairs and mirror_pairs.get("pairs"):
            L.append(f"| *Memorandum: excluded mirrored clearing pairs (net)* | "
                     + " | ".join([_fmt(mirror_pairs["total_excluded_net"])] + ["—"] * (len(periods) - 1)) + " |")

    # Risk areas with quantified potential effect (per uploaded documents)
    if quantified and quantified.get("rows"):
        L += ["", "## Risk areas with quantified potential effect (per uploaded documents)",
              "_Each effect is quoted from the cited source document; these are potential "
              "effects for corroboration, not audited misstatements._", "",
              "| # | Item | Head affected | Potential effect (per source) |",
              "|---|---|---|---|"]
        for r in quantified["rows"]:
            L.append(f"| {r['n']} | {r['item']} | {', '.join(r['head_affected']) or '—'} | "
                     f"{r['effect_text']} |")
        cum = quantified.get("cumulative") or {}
        if cum.get("n_items"):
            per_dir = "; ".join(f"{k.replace('_', ' ')}: {_fmt(v)}"
                                for k, v in cum.get("by_direction", {}).items())
            L.append(f"\nCumulative ({cum['n_items']} quantified item(s)"
                     + (f", {cum['unit']}" if cum.get("unit") else "") + f"): {per_dir}. "
                     + cum.get("note", "")
                     + (f" {cum['unit_note']}" if cum.get("unit_note") else ""))

    # Internal control & process risk indicators
    if sections.get("internal_control_risks"):
        L += ["", "## Internal control & process risk indicators"]
        for r in sections["internal_control_risks"]:
            L.append(f"- **{r['area']}**: {r['risk']} "
                     f"Evidence: {', '.join(r['evidence_requested'])}.")

    # Statutory compliance risk indicators
    if sections.get("statutory_compliance_risks"):
        L += ["", "## Statutory compliance risk indicators",
              "_Reasonableness indicators only — filing and payment status require the "
              "returns and challans requested._"]
        for r in sections["statutory_compliance_risks"]:
            L.append(f"- **{r['statute']}** [{r.get('severity', 'low').upper()}]: "
                     f"{r['observation']} {r['risk']} "
                     f"Evidence: {', '.join(r['evidence_requested'])}.")

    # Trial-balance-wide screen (client-format rule B6 — no percentages, qualitative reading)
    L += ["", "## Trial-balance-wide screen"]
    if screen["abnormal_signs"]:
        L.append(f"- Abnormal signs: {screen['n_abnormal']} ledger(s) across "
                 f"{len(screen['abnormal_signs'])} line item(s); largest: "
                 + ", ".join(f"{g['fsli']} ({g['count']})" for g in screen["abnormal_signs"][:4]) + ".")
    if screen["concentration"]:
        L.append("- Concentration: " + "; ".join(
            f"{c['account']} (net {_fmt(c['net'])}) is a material share of its "
            f"{c['category']} class" for c in screen["concentration"][:4]) + ".")
    if screen["round_sums"]:
        L.append(f"- Round-sum balances: {len(screen['round_sums'])} flagged.")
    flipped = [a for a in mapping["accounts"] if a.get("sign_flipped")]
    if flipped:
        L.append(f"- Sign flips: {len(flipped)} ledger(s) changed sign between opening and "
                 "closing (e.g. " + ", ".join(a["name"] for a in flipped[:4]) + ").")
    if dup_rows:
        if dup_rows.get("n_duplicate_codes"):
            L.append(f"- Duplicate account codes: {dup_rows['n_duplicate_codes']} found "
                     "(e.g. " + ", ".join(r["code"] for r in dup_rows["duplicate_codes"][:4]) + ").")
        if dup_rows.get("n_duplicate_rows"):
            L.append(f"- Duplicate rows (same name + amount): {dup_rows['n_duplicate_rows']} found.")
        if dup_rows.get("n_subtotal_rows"):
            L.append(f"- Possible subtotal rows left in as accounts: {dup_rows['n_subtotal_rows']} "
                     "found (e.g. " + ", ".join(r["name"] for r in dup_rows["subtotal_rows"][:4]) + ").")
    if offsetting and offsetting.get("computable") and offsetting.get("n_flags"):
        top = offsetting["flags"][0]
        L.append(f"- Offsetting activity: {offsetting['n_flags']} account(s) show gross debit "
                 f"({_fmt(top['movement_debit'])}) and credit ({_fmt(top['movement_credit'])}) "
                 "movement that is each material relative to the group, while net movement "
                 f"({_fmt(top['movement_net'])}) is small — consistent with internal/offsetting "
                 f"postings (e.g. '{top['account']}').")
    if clearing and clearing.get("computable") and clearing.get("groups"):
        L.append(f"- Clearing/inter-unit series: {len(clearing['groups'])} GL series identified "
                 "as internal clearing/inter-unit/statistical postings and excluded from FSLI "
                 f"totals and the Financial Snapshot (combined net "
                 f"{_fmt(clearing['total_excluded_net'])})."
                 + (f" {len(clearing['unresolved'])} did not resolve to nil — see Focus Areas."
                    if clearing.get("unresolved") else ""))
    if mirror_pairs and mirror_pairs.get("pairs"):
        L.append(f"- Mirrored clearing pairs: {len(mirror_pairs['pairs'])} pair(s) of accounts "
                 "cross-referencing each other by name identified as internal transfer/contra "
                 f"pairs and excluded from FSLI totals and the Financial Snapshot (combined net "
                 f"{_fmt(mirror_pairs['total_excluded_net'])})."
                 + (f" {len(mirror_pairs['unresolved'])} did not resolve to nil — see Focus Areas."
                    if mirror_pairs.get("unresolved") else ""))

    # Concentration — largest balances (client-format rule D13)
    if largest:
        L += ["", "## Concentration — largest balances (top 10 by absolute value)",
              "| GL Code | GL Name | Amount | Nature/Treatment |", "|---|---|---:|---|"]
        for r in largest:
            L.append(f"| {r.get('code') or '—'} | {r['name']} | {_fmt(r['amount'])} | "
                     f"{r['nature']} |")

    # Relationship Analytics (symmetric — consistent + flagged; client-format rule B6 no ratios)
    rel_list = (relationships or {}).get("relationships") or []
    if rel_list:
        L += ["", "## Relationship Analytics",
              "_Indicators only, tested using accounts present in the trial balance. A "
              "consistent relationship is not proof of correctness, and a flagged one is "
              "not proof of error — both require corroboration with the evidence "
              "requested. High/Medium/Low-severity items below are also raised as Focus "
              "Areas._", "", "| Relationship tested | Reading |", "|---|---|"]
        for r in rel_list:
            tag = "" if r["severity"] == "none" else f" [{r['severity'].upper()}]"
            L.append(f"| {r['relationship']}{tag} | {r['observation']} {r['gap']} |")

    # Provisional materiality (client-format rule B6 — materiality basis kept, no structure ratios)
    L += ["", "## Provisional materiality",
          f"- Provisional overall materiality: {_fmt(materiality['provisional_overall_materiality'])} "
          f"(basis: {materiality.get('calculation_basis', materiality['chosen_base'])}). "
          f"{materiality['note']}",
          f"- **Why this base:** {materiality.get('base_reason', '')}"]

    # Variance — opening vs closing (client-format rule D12, absolute figures only)
    if opening_closing and opening_closing.get("rows"):
        L += ["", "## Variance — opening vs closing (absolute figures)",
              "| FSLI Group | Opening | Closing | Movement | Note |",
              "|---|---:|---:|---:|---|"]
        for r in opening_closing["rows"][:20]:
            L.append(f"| {r['fsli']} | {_fmt(r['opening'])} | {_fmt(r['closing'])} | "
                     f"{_fmt(r['movement'])} | {r['note']} |")

    # Comparison to prior period (client-format rule E14 — independent, never merged)
    if comparison and comparison.get("rows"):
        L += ["", "## Comparison to prior period (independent, side-by-side)",
              "_The current/base year drives all analysis above; the prior period is shown "
              "here for comparison only and is processed independently — figures are never "
              "merged into one combined dataset._", "",
              "| Metric | Current | Prior | Change |", "|---|---:|---:|---:|"]
        for r in comparison["rows"]:
            L.append(f"| {r['metric']} | {_fmt(r['current'])} | {_fmt(r['prior'])} | "
                     f"{_fmt(r['change'])} |")
        if comparison.get("new_accounts"):
            L += ["", "**New accounts** (present in the current period only):"]
            for a in comparison["new_accounts"][:15]:
                L.append(f"- {a.get('code') or '—'} {a['name']} ({_fmt(a['current'])})")
        if comparison.get("dropped_accounts"):
            L += ["", "**Dropped accounts** (present in the prior period only):"]
            for a in comparison["dropped_accounts"][:15]:
                L.append(f"- {a.get('code') or '—'} {a['name']} ({_fmt(a['prior'])})")
        if comparison.get("flagged_swings"):
            L += ["", "**Notable swings:**",
                  "| GL Code | GL Name | Current | Prior | Change |",
                  "|---|---|---:|---:|---:|"]
            for a in comparison["flagged_swings"][:15]:
                L.append(f"| {a.get('code') or '—'} | {a['name']} | {_fmt(a['current'])} | "
                         f"{_fmt(a['prior'])} | {_fmt(a['change'])} |")

    # Focus Areas (client-format rules F16, F17, F18 — renamed from Prioritised findings)
    L += ["", "## Focus Areas"]
    for f in findings["findings"]:
        flags = []
        if f.get("regularity_flag"):
            flags.append("regularity")
        if f.get("propriety_flag"):
            flags.append("propriety")
        flag_txt = f"; **{'/'.join(flags)} flag**" if flags else ""
        amount_txt = f" — {_fmt(f['amount'])}" if f.get("amount") else ""
        L += [f"### {f.get('reference_id', '')}. [{f['risk_rating'].upper()}] "
              f"{f['account']}{amount_txt}",
              f"- **Observation and Gap:** {f['observation']} {f['gap']}",
              f"- **Assertions/Risk Basis:** {', '.join(f['assertion'])}; "
              f"{', '.join(f['risk_basis'])}{flag_txt}.",
              f"- **Risk Rating:** {f['risk_rating'].upper()}",
              "- **Evidence Requested:** "
              + (', '.join(f['evidence_requested']) if f.get('evidence_requested') else '—')]
        if f.get("calculation_basis"):
            L.append(f"- **Calculation basis:** {f['calculation_basis']}")
    # single safe-limitation disclaimer for all findings (stated once, not per-finding)
    L += ["", "---", "", f"_{SAFE_LIMITATION}_"]

    # Suggested audit focus sequence
    if focus:
        L += ["", "## Suggested audit focus sequence",
              "_Priority order for planning, driven by quantified document-sourced items, "
              "risk-ranked findings and statutory indicators._"]
        for r in focus:
            L.append(f"{r['priority']}. **{r['area']}** — {r['reason']}")

    # Limitations & no-opinion closing (spec 2.3 can-indicate vs cannot-conclude)
    L += ["", "## Limitations & no-opinion statement",
          "**The trial balance can indicate:** arithmetic integrity, abnormal signs, large "
          "balances, sensitive ledgers, suspense accounts, relationship gaps, unmapped ledgers, "
          "missing comparatives, and internal ratios/structural relationships where the component "
          "lines are present.",
          "**The trial balance cannot conclude:** existence of assets, title/ownership, "
          "recoverability, completeness of liabilities, physical inventory, authorisation, "
          "statutory compliance, related-party completeness, ageing, transaction-level "
          "cut-off/occurrence, fraud or propriety — nor whether the absence of an expected "
          "counterpart account is justified. Those require the corroborative records requested above.",
          "", NO_OPINION_CLOSING.format(entity=entity)]

    # 9. Run log (spec OUT-05) — provenance for this specific run
    if run_log:
        L += ["", "## Run log",
              f"- File: {run_log['file_name']} (hash: {run_log['file_hash']})",
              f"- Model version: {run_log['model_version']}",
              f"- Prompt version: {run_log['prompt_version']}",
              f"- Mapping version: {run_log['mapping_version']}",
              f"- Run timestamp: {run_log['run_timestamp']}",
              f"- Output file: {run_log['output_file_name']}"]
    return "\n".join(L)


class AuditPipeline:
    """Audit-mode risk analytics over a single stored trial balance."""

    # citation caps (latency: one batched embedding call + parallel pgvector selects)
    _CITE_FINDINGS = 6
    _CITE_QUANTIFIED = 10
    _CITE_SCHED_III = 6

    # reference corpora cited in the audit report. EAC opinions are deliberately
    # EXCLUDED here: they are interpretive committee views, summarised only when a
    # user explicitly asks about them in chat — never volunteered in audit output.
    _AUDIT_CORPORA = ["schedule_iii", "caro", "sa700", "ind_as_appendix"]

    def _attach_citations(self, gaps: dict, findings: dict, quantified: dict,
                          sched_iii: list[dict]) -> None:
        """Attach retrieval-driven citations in place (single batched embed call).

        Items are searched across the Ind AS corpus and the audit reference
        corpora (Schedule III / CARO / SA 700 / Ind AS appendices — not EAC);
        Ind AS gap rows are additionally restricted to their own standard so the
        exact requirement paragraphs surface. Schedule III checklist rows search
        the Schedule III corpus only.
        """
        targets: list[dict] = []
        items: list[tuple[str, list[int] | None]] = []
        corpora: list[list[str] | None] = []

        for g in gaps.get("gaps", []):
            targets.append(g)
            items.append((scenario_query(g), parse_standard_number(g.get("standard", ""))))
            corpora.append(self._AUDIT_CORPORA)
        ranked = [f for f in findings.get("findings", [])
                  if f.get("risk_rating") in ("high", "medium")][:self._CITE_FINDINGS]
        for f in ranked:
            targets.append(f)
            items.append((scenario_query(f), None))
            corpora.append(self._AUDIT_CORPORA)
        for r in (quantified.get("rows") or [])[:self._CITE_QUANTIFIED]:
            targets.append(r)
            q = "; ".join(r.get("head_affected") or []) + "; " + (r.get("item") or "")
            items.append((q[:300], None))
            corpora.append(self._AUDIT_CORPORA)
        for r in sched_iii[:self._CITE_SCHED_III]:
            targets.append(r)
            items.append((scenario_query(r), None))
            corpora.append(["schedule_iii"])

        results = citations_for_items(items, corpora_per_item=corpora)
        for target, cites in zip(targets, results):
            target["citations"] = cites
            top_indas = next((c for c in cites if c["source"] == "ind_as"), None)
            if top_indas and "requirement" in target:
                target["requirement_retrieved"] = top_indas["snippet"]

    def __init__(self):
        # the audit narrative is long; give the analyst headroom so it completes in
        # one response rather than triggering a (fragile) continuation call.
        self.llm = build_llm(temperature=0.1, max_tokens=4500)
        self.analyst = build_audit_analyst(self.llm)
        # separate low-temperature client for strict-JSON document extraction
        self.extractor = build_doc_evidence_extractor(
            build_llm(temperature=0.0, max_tokens=2000))

    def audit(self, doc_id: str, metadata: dict | None = None,
              upload_doc_ids: list[str] | None = None,
              doc_id_prior: str | None = None,
              grouping_override: dict[str, str] | None = None) -> dict:
        tb = get_trial_balance(doc_id)
        if tb is None:
            raise ValueError(f"no stored trial balance for doc_id {doc_id!r}")
        # client-format rule E14: the current/base year drives ALL analysis below.
        # A prior period is NEVER merged into one combined dataset — it's classified
        # independently and shown only as a side-by-side comparison (see `comparison`
        # further down), reusing the already-independent compare_trial_balances path.
        comparison = None
        comparison_risk = None
        base_year_note = None
        if doc_id_prior:
            # item 12 (explicitly scoped, not undefined): this two-*file* path always
            # compares period 1 of each file only (the existing convention
            # `compare_trial_balances()`/`classify_tb()` already assume). If either
            # file ALSO independently contains >2 periods of its own columns, those
            # extra embedded periods are ignored in this combination — composing
            # Requirement 2's in-file auto-detection with the two-file path is a
            # reasonable follow-up, out of scope for this pass.
            tb_prior = get_trial_balance(doc_id_prior)
            if tb_prior is None:
                raise ValueError(f"no stored trial balance for doc_id {doc_id_prior!r}")
            try:
                cmp = compare_trial_balances(tb, tb_prior)
                comparison = {"rows": [
                    {"metric": r["category"], "current": r["current"], "prior": r["prior"],
                     "change": r["change"]}
                    for r in cmp.get("by_category", [])],
                    "new_accounts": cmp.get("new_accounts", []),
                    "dropped_accounts": cmp.get("dropped_accounts", []),
                    "flagged_swings": cmp.get("flagged_swings", []),
                    "lines": [
                        {"code": r.get("code"), "name": r.get("name"), "current": r["current"],
                         "prior": r["prior"], "change": r["change"], "pct_change": r.get("pct_change")}
                        for r in cmp.get("lines", [])]}
                comparison_risk = cmp.get("risk")
            except Exception:  # noqa: BLE001 - comparison is supplementary context only
                comparison = None
                comparison_risk = None
        elif len(tb.get("periods", [])) > 2:
            # Requirement 2 — multiple years detected as columns within ONE file
            # (distinct from the doc_id_prior two-*file* case above). Auto-select
            # the base year, run the full engine on it only, and compare against
            # just the single next-most-recent other period — independently
            # classified, never merged — reusing compare_trial_balances() exactly
            # as the two-file path does.
            periods = tb["periods"]
            base_idx = _select_base_period_index(periods)
            other_indices = [i for i in range(len(periods)) if i != base_idx]
            comparator_idx = other_indices[0] if other_indices else None
            base_year_note = (
                f"{len(periods)} periods detected within one file ({', '.join(periods)}); "
                f"\"{periods[base_idx]}\" auto-selected as the base year for all analysis "
                "below" + (f"; \"{periods[comparator_idx]}\" shown only for comparison"
                          if comparator_idx is not None else "") + ".")
            base_slice = _slice_period(tb, base_idx)
            if comparator_idx is not None:
                try:
                    cmp = compare_trial_balances(base_slice, _slice_period(tb, comparator_idx))
                    comparison = {"rows": [
                        {"metric": r["category"], "current": r["current"], "prior": r["prior"],
                         "change": r["change"]}
                        for r in cmp.get("by_category", [])],
                        "new_accounts": cmp.get("new_accounts", []),
                        "dropped_accounts": cmp.get("dropped_accounts", []),
                        "flagged_swings": cmp.get("flagged_swings", []),
                        "lines": [
                            {"code": r.get("code"), "name": r.get("name"), "current": r["current"],
                             "prior": r["prior"], "change": r["change"], "pct_change": r.get("pct_change")}
                            for r in cmp.get("lines", [])]}
                    comparison_risk = cmp.get("risk")
                except Exception:  # noqa: BLE001 - comparison is supplementary context only
                    comparison = None
                    comparison_risk = None
            tb = base_slice
        entity = (metadata or {}).get("entity") or tb.get("filename") or doc_id

        # deterministic audit engine (current/base year TB only)
        classification = classify_tb(tb)
        mapping = map_accounts(tb, classification, grouping_override=grouping_override)
        screen = screen_tb(mapping)
        dup_rows = find_duplicate_rows(tb)
        offsetting = find_offsetting_activity(tb, mapping)
        clearing = find_clearing_series(tb, mapping)
        mirror_pairs = find_mirrored_pairs(mapping)
        # item 6 — two independent clearing mechanisms (GL-prefix series and
        # name-based mirror pairs) feed one combined exclusion set; a plain set
        # union is naturally idempotent even if an account were (implausibly)
        # claimed by both, so totals are never double-counted either way.
        all_excluded_names = (set(clearing.get("excluded_names", []))
                              | set(mirror_pairs.get("excluded_names", [])))
        largest = largest_balances(mapping, all_excluded_names)
        opening_closing = opening_vs_closing(mapping)
        relationships = analyze_relationships(mapping)
        statements = compute_statements(tb, classification)["per_period"][0]
        materiality = materiality_proxies(statements)
        ratios = structure_ratios(relationships["role_totals"],
                                  relationships["role_coverage_pct"], statements)
        iq = input_quality_note(tb, mapping, metadata)
        findings = build_findings(mapping, screen, relationships, materiality, iq,
                                  clearing=clearing, comparison_risk=comparison_risk,
                                  mirror_pairs=mirror_pairs)
        # full-analysis content folded into audit mode (deterministic, multi-period aware)
        try:
            period_ratios = compute_ratios(tb, classification)
            variance = compute_variance(tb, classification)
        except Exception:  # noqa: BLE001
            period_ratios, variance = {}, {}
        snapshot = financial_snapshot(tb, mapping, all_excluded_names)
        gaps = ind_as_gaps(mapping, relationships)

        # client-format sections (deterministic re-shaping; never fail the audit)
        try:
            ic_risks = internal_control_risks(screen, iq, mapping)
            statutory = statutory_compliance_risks(relationships, mapping)
            sched_iii = schedule_iii_disclosure_gaps(mapping, iq, screen)
        except Exception:  # noqa: BLE001
            ic_risks, statutory, sched_iii = [], [], []

        # document-sourced quantified risk items (only when PDFs are attached)
        try:
            doc_ev = extract_doc_risk_items(upload_doc_ids, self.extractor,
                                            (metadata or {}).get("engagement_context"))
        except Exception:  # noqa: BLE001
            doc_ev = {"doc_evidence_status": "unavailable", "context_note": "",
                      "quantified_risk_areas": {"rows": [], "cumulative": {}},
                      "source_chunks": []}
        quantified = doc_ev["quantified_risk_areas"]

        # dynamic retrieval-driven citations: every gap/finding/quantified item is
        # searched across ALL corpora (Ind AS + Schedule III/CARO/SA 700/EAC)
        try:
            self._attach_citations(gaps, findings, quantified, sched_iii)
        except Exception:  # noqa: BLE001 - citations are optional context
            pass

        try:
            focus = audit_focus_sequence(findings, quantified, gaps, statutory)
        except Exception:  # noqa: BLE001
            focus = []

        sections = {"schedule_iii_gaps": sched_iii,
                    "internal_control_risks": ic_risks,
                    "statutory_compliance_risks": statutory}
        context_note = doc_ev.get("context_note") or (
            "Analysis is based solely on the trial balance supplied; no supporting "
            "documents were provided.")

        run_log = build_run_log(tb.get("filename") or doc_id, doc_id, "TB_Audit_Report.md")
        deterministic = build_audit_report(entity, iq, mapping, screen, relationships,
                                           materiality, ratios, findings, snapshot, gaps,
                                           context_note=context_note,
                                           quantified=quantified, sections=sections,
                                           focus=focus, period_ratios=period_ratios,
                                           variance=variance, run_log=run_log,
                                           dup_rows=dup_rows, offsetting=offsetting,
                                           filename=tb.get("filename"), sheet=tb.get("sheet"),
                                           clearing=clearing, largest=largest,
                                           opening_closing=opening_closing,
                                           comparison=comparison, mirror_pairs=mirror_pairs,
                                           base_year_note=base_year_note)

        # safe-language narrative: let the analyst re-narrate, but fall back to the
        # deterministic report if it is thin or emits any forbidden (conclusion) wording.
        report = deterministic
        try:
            run = self.analyst.run(
                f"Entity: {entity}\n\n=== Computed audit object (use verbatim) ===\n{deterministic}"
                "\n\nWrite the audit analytics report in the required order and safe language.",
                reset_conversation=True)
            narrated = (run.get("response") or "").strip()
            # accept the narrative only if it is safe AND actually completed (reached the
            # no-opinion CLOSING SENTENCE, not merely the "...no-opinion statement" section
            # HEADER — a bare "no-opinion" substring match also matches the header text
            # itself, so a response cut off immediately after writing that heading (and
            # never reaching the actual closing sentence) was incorrectly treated as
            # complete; confirmed live on a large report that ended
            # "[Response still incomplete after 3 continuations]" yet passed this check).
            # A truncated narrative -> keep the complete deterministic report, which is
            # safe by construction.
            completed = ("does not express an audit opinion" in narrated.lower()
                         or "does not certify" in narrated.lower())
            if re.search(r"\[.*(still incomplete|response incomplete).*\]", narrated, re.I):
                completed = False
            # item 10 — visibility into how often narration is rejected and why,
            # so continuation-splicing (bug 2) can be tracked as rare vs. routine
            # on large reports rather than being invisible in the logs.
            reasons = []
            if len(narrated) < 200 or not completed:
                reasons.append("too short/incomplete")
            if contains_forbidden(narrated):
                reasons.append("forbidden wording")
            if not _structurally_compliant(narrated):
                reasons.append("structurally non-compliant")
            if _has_malformed_table_rows(narrated):
                reasons.append("malformed/spliced table rows")
            if reasons:
                logger.info("narration rejected for doc_id=%s (n_accounts=%d): %s "
                           "— using deterministic report", doc_id,
                           len(mapping["accounts"]), ", ".join(reasons))
            else:
                report = _strip_continuation_marker(narrated)
        except Exception as exc:  # noqa: BLE001 - never fail the request on an agent hiccup
            logger.warning("narration attempt raised %s: %s — using deterministic report",
                          type(exc).__name__, exc)

        return {
            "doc_id": doc_id,
            "entity": entity,
            "context_note": context_note,
            "doc_evidence_status": doc_ev.get("doc_evidence_status", "no_docs"),
            "quantified_risk_areas": quantified,
            "schedule_iii_gaps": sched_iii,
            "internal_control_risks": ic_risks,
            "statutory_compliance_risks": statutory,
            "audit_focus_sequence": focus,
            "period_ratios": period_ratios,
            "variance": variance,
            "report": report,
            "input_quality": iq,
            "mapping_summary": {
                "method": mapping["method"],
                "grouping_source": mapping.get("grouping_source"),
                "n_grouping_override": mapping.get("n_grouping_override", 0),
                "fsli_groups": {k: len(v) for k, v in mapping["fsli_groups"].items()},
                "mapping_confidence_summary": mapping["mapping_confidence_summary"],
                "sensitive_summary": mapping["sensitive_summary"],
                "unmapped_accounts": mapping["unmapped_accounts"][:50],
            },
            "financial_snapshot": snapshot,
            "ind_as_gaps": gaps["gaps"],
            "tb_screen": screen,
            "clearing_series": clearing,
            "largest_balances": largest,
            "opening_vs_closing": opening_closing,
            "comparison": comparison,
            "relationship_analytics": relationships["relationships"],
            "materiality": materiality,
            "structure_ratios": ratios,
            "findings": findings["findings"],
            "findings_summary": findings["summary"],
            "evidence_request_list": findings["evidence_request_list"],
            "management_query_list": findings["management_query_list"],
        }

    def audit_workbook(self, doc_id: str, doc_id_prior: str | None = None,
                       upload_doc_ids: list[str] | None = None) -> bytes:
        """Build the downloadable TB_Audit.xlsx (deterministic only — no LLM).

        Contains the snapshot with a full account-level drill-down per metric,
        the Ind AS gaps with the inputs taken for each assessment plus the
        accounts considered per standard, the ranked findings, and — when
        supporting PDFs are supplied — the same document-sourced quantified risk
        items ``audit()`` computes (previously never wired through here at all,
        so the Quantified Risk Areas sheet was always empty even with PDFs).
        """
        tb = get_trial_balance(doc_id)
        if tb is None:
            raise ValueError(f"no stored trial balance for doc_id {doc_id!r}")
        if doc_id_prior:
            tb_prior = get_trial_balance(doc_id_prior)
            if tb_prior is None:
                raise ValueError(f"no stored trial balance for doc_id {doc_id_prior!r}")
            tb = merge_trial_balances(tb, tb_prior)
        entity = tb.get("filename") or doc_id

        classification = classify_tb(tb)
        mapping = map_accounts(tb, classification)
        screen = screen_tb(mapping)
        relationships = analyze_relationships(mapping)
        statements = compute_statements(tb, classification)["per_period"][0]
        materiality = materiality_proxies(statements)
        iq = input_quality_note(tb, mapping, None)
        findings = build_findings(mapping, screen, relationships, materiality, iq)
        snapshot = financial_snapshot(tb, mapping)
        contributors = snapshot_contributors(tb, mapping)
        gaps = ind_as_gaps(mapping, relationships)
        try:
            doc_ev = extract_doc_risk_items(upload_doc_ids, self.extractor, None)
        except Exception:  # noqa: BLE001
            doc_ev = {"quantified_risk_areas": {"rows": [], "cumulative": {}}}
        quantified = doc_ev["quantified_risk_areas"]
        try:
            # citations enrich the gaps sheet; the workbook still builds without them
            self._attach_citations(gaps, findings, quantified, [])
        except Exception:  # noqa: BLE001
            pass
        return build_audit_workbook(entity, tb, snapshot, contributors,
                                    gaps["gaps"], mapping, findings["findings"],
                                    quantified=quantified)
