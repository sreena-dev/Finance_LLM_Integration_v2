"""
consistency_engine.py — Deterministic consistency-matrix + silence rules
============================================================================
Gap-closure Phase 2, Gap #3 ("Deterministic consistency + silence engine").
See ../GAP_CLOSURE_LOG.md for full rationale, what's covered, and what
isn't yet.

WHY THIS EXISTS
---------------
Source spec §26 (Consistency Matrix Engine) lists ~15 pairwise cross-report
checks; §27/28 (Silence / Gap Analytics) ask for the same "does the report
address X" question run against a list of high-risk scenarios. Before this
module, only 3 of those rows existed as deterministic code
(tool_sar.check_opinion_coherence_signals — opinion vs. CARO, opinion vs.
IFC, opinion vs. going-concern-distress); everything else was left to the
writer LLM to notice and tag correctly from a large prose prompt, which is
exactly what the wiki's own Key Takeaway #6 ("cross-report consistency is
a primary analytical engine") says should not be the case.

THIS IS A PARTIAL IMPLEMENTATION — SEE "NOT YET COVERED" BELOW
-----------------------------------------------------------------
Seven rules are implemented here, chosen because (a) the data they need
already exists in the merged extractor JSON without a schema change, and
(b) several map directly onto the wiki's own worked evaluation cases
(EVAL-03, EVAL-06) or named high-risk silence scenarios (§28: fraud,
related-party balances, disputed statutory dues). The remaining rows are
blocked on other gaps not yet built (pervasiveness/quantification — Gap
#8; prior-year data — Gap #6; FS-notes text extraction, which nothing in
this pipeline currently does at all) and are listed at the bottom of this
docstring rather than silently omitted.

RULES IMPLEMENTED (each returns one Observation, or None if it doesn't fire):
  1. check_caro_going_concern_silence   — CARO (ix) default / (xix) adverse,
     no MURGC/going-concern discussion. Matches EVAL-03 exactly.
  2. check_caro_fraud_silence           — CARO (xi) fraud flag, no echo of
     fraud / s.143(12) anywhere else in the report. Never concludes fraud
     occurred — only that the report is silent where CARO raised a flag.
  3. check_caro_rpt_silence             — CARO (xiii) related-party flag,
     no echo in KAM/EoM.
  4. check_caro_statutory_dues_silence  — CARO (vii) statutory-dues flag,
     no echo in Rule 11 / KAM / EoM.
  5. check_rule11g_ifc_tension          — Rule 11(g) audit-trail adverse,
     but IFC opinion unmodified / no IT-control weakness noted. Matches
     EVAL-06 exactly ("High cross-report Risk Flag").
  6. check_cag_directions_unquantified  — C&AG directions present with a
     pending count but no reconciling text. A lightweight stand-in for the
     full §25 four-dimension test (Gap #2, on hold — see GAP_CLOSURE_LOG.md)
     — this rule only checks "is there text to reconcile against at all",
     not responsiveness against actual standing directions.
  7. check_kam_high_risk_silence        — a KAM section exists but does not
     appear to cover a distress signal already computed elsewhere in the
     pipeline (negative net worth / current ratio < 1 / negative OCF).

Every rule that reasons from CARO or IFC data gates on applicability
(applicability.py, Gap #4) — it returns None rather than firing when the
relevant area's status is not "applicable", so an unresolved-applicability
package does not also generate consistency-engine noise on top of the one
APPL-*-01 pointer applicability.py already raises for it.

NOT YET COVERED (tracked for a future pass, not silently dropped)
---------------------------------------------------------------------
  * Opinion vs. Basis for Opinion (nature/pervasiveness) — needs Gap #8's
    pervasiveness cues.
  * Modified amount vs. FS note (amount/accounting effect) — needs Gap #8's
    modification-quantification extraction.
  * EoM vs. FS disclosure (does the disclosed matter actually exist in the
    FS notes) — nothing in this pipeline extracts FS notes text separately
    from the BS/P&L/CF tables; there is no note text to check against.
  * Prior modification vs. current report; prior C&AG comment vs. current
    package — needs Gap #6 (the `sar_results` table doesn't exist yet).
  * The full §25 four-dimension C&AG-directions test — Gap #2, on hold
    pending a decision on what "actual directions for this entity/year"
    means against the only directions data found so far
    (`cag_directions_chunks`, a reference/rules-DB table of standing
    direction text with no entity/FY column — see GAP_CLOSURE_LOG.md).
"""

from __future__ import annotations

import logging

from sar_prod_v3.observation import Observation

logger = logging.getLogger("sar_prod_v3.consistency_engine")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _clause(caro_json: dict, clause_no: str) -> dict | None:
    for c in (caro_json.get("clauses") or []):
        if str(c.get("clause_no", "")).strip().lower() == clause_no.lower():
            return c
    return None


def _clause_is_adverse(caro_json: dict, clause_no: str) -> bool:
    c = _clause(caro_json, clause_no)
    if not c:
        return False
    return bool(c.get("adverse")) or (c.get("answer") or "").lower() == "adverse"


def _gather_echo_text(merged_json: dict) -> str:
    """Concatenates every piece of extracted narrative text outside CARO
    that a genuine cross-report echo would show up in: EoM items, KAM
    items, Rule 11 sub-clauses, Other Matter. Lower-cased for a
    case-insensitive substring search — this is a recall-oriented check
    (§27 asks "does it appear addressed anywhere", not "is the wording
    exact"), not a precise semantic match.
    """
    parts: list[str] = []

    eom = merged_json.get("emphasis_of_matter") or {}
    for item in eom.get("items") or []:
        parts.append(item.get("description", ""))
        parts.append(item.get("quote", ""))

    for item in (merged_json.get("key_audit_matters") or {}).get("items") or []:
        parts.append(item.get("title", ""))
        parts.append(item.get("why_significant", ""))
        parts.append(item.get("how_addressed", ""))
        parts.append(item.get("related_note", ""))

    for sub in (merged_json.get("rule_11") or {}).get("sub_clauses") or []:
        parts.append(sub.get("topic", ""))
        parts.append(sub.get("text", ""))

    other_matter = merged_json.get("other_matter") or {}
    parts.append(other_matter.get("description", ""))

    return "\n".join(p for p in parts if p).lower()


def _echoed(merged_json: dict, keywords: list[str]) -> bool:
    text = _gather_echo_text(merged_json)
    return any(kw.lower() in text for kw in keywords)


def _is_applicable(applicability: dict, area: str) -> bool:
    return (applicability.get(area) or {}).get("status") == "applicable"


# ---------------------------------------------------------------------------
# Rule 1 — CARO (ix)/(xix) vs. Going Concern  (source spec §26 rows
# "MURGC <-> CARO(xix)" and "CARO(ix) <-> Going concern"; matches EVAL-03)
# ---------------------------------------------------------------------------

def check_caro_going_concern_silence(merged_json: dict, fin_metrics: dict, applicability: dict) -> Observation | None:
    if not _is_applicable(applicability, "caro"):
        return None
    caro = merged_json.get("CARO_2020") or {}
    flags = caro.get("high_priority_flags") or {}
    loan_default = bool(flags.get("loan_default_ix")) or _clause_is_adverse(caro, "ix")
    gc_adverse = bool(flags.get("going_concern_xix")) or _clause_is_adverse(caro, "xix")
    if not (loan_default or gc_adverse):
        return None

    gc = merged_json.get("going_concern") or {}
    if gc.get("murgc_paragraph_present") or gc.get("discussed"):
        return None  # already addressed — no tension to flag

    trigger = (
        "CARO clause (ix) reports a loan/borrowing default" if loan_default
        else "CARO clause (xix) is adverse on the Company's ability to meet its liabilities as they fall due"
    )
    return Observation(
        check_id="CONS-CARO-IX-01",
        component="Consistency — CARO (ix)/(xix) vs. Going Concern",
        tag="RISK_FLAG", risk_rating="High",
        observation=(
            f"{trigger}, but the main report contains no Material Uncertainty Related to Going "
            f"Concern paragraph and going concern does not otherwise appear to be discussed. "
            f"Per SA 570, this combination warrants supplementary-audit follow-up."
        ),
        evidence=f"CARO high-priority flags: loan_default_ix={flags.get('loan_default_ix')}, going_concern_xix={flags.get('going_concern_xix')}",
        evidence_required=[
            "Management's going-concern assessment", "cash-flow forecasts",
            "loan agreements / lender correspondence on the reported default",
        ],
    )


# ---------------------------------------------------------------------------
# Rule 2 — CARO (xi) fraud vs. s.143(12) echo (source spec §28 named
# scenario; guardrail: never concludes fraud occurred)
# ---------------------------------------------------------------------------

def check_caro_fraud_silence(merged_json: dict, fin_metrics: dict, applicability: dict) -> Observation | None:
    if not _is_applicable(applicability, "caro"):
        return None
    caro = merged_json.get("CARO_2020") or {}
    flags = caro.get("high_priority_flags") or {}
    if not (bool(flags.get("fraud_xi")) or _clause_is_adverse(caro, "xi")):
        return None
    if _echoed(merged_json, ["fraud", "143(12)", "whistle"]):
        return None

    return Observation(
        check_id="CONS-CARO-XI-01",
        component="Consistency — CARO (xi) Fraud vs. Report-Wide Echo",
        tag="RISK_FLAG", risk_rating="High",
        observation=(
            "CARO clause (xi) raises a fraud-related flag, but no corresponding reference to "
            "fraud reporting under section 143(12), or to a whistle-blower matter, was identified "
            "elsewhere in the report. This is a silence signal for supplementary-audit follow-up, "
            "not a finding that fraud occurred or that reporting was omitted."
        ),
        evidence=f"CARO clause (xi) flag: {flags.get('fraud_xi')}",
        evidence_required=["Section 143(12) fraud report (if any)", "Board report whistle-blower disclosures"],
    )


# ---------------------------------------------------------------------------
# Rule 3 — CARO (xiii) related parties vs. KAM/EoM echo
# ---------------------------------------------------------------------------

def check_caro_rpt_silence(merged_json: dict, fin_metrics: dict, applicability: dict) -> Observation | None:
    if not _is_applicable(applicability, "caro"):
        return None
    caro = merged_json.get("CARO_2020") or {}
    flags = caro.get("high_priority_flags") or {}
    if not (bool(flags.get("rpt_xiii")) or _clause_is_adverse(caro, "xiii")):
        return None
    if _echoed(merged_json, ["related part", "rpt", "section 177", "section 188"]):
        return None

    return Observation(
        check_id="CONS-CARO-XIII-01",
        component="Consistency — CARO (xiii) Related Parties vs. KAM/EoM",
        tag="RISK_FLAG", risk_rating="Medium",
        observation=(
            "CARO clause (xiii) raises a related-party flag, but no corresponding related-party "
            "matter was identified in the Key Audit Matters or Emphasis of Matter sections."
        ),
        evidence=f"CARO clause (xiii) flag: {flags.get('rpt_xiii')}",
        evidence_required=["Related-party transaction register", "ss.177/188 approvals", "arm's-length assessment"],
    )


# ---------------------------------------------------------------------------
# Rule 4 — CARO (vii) statutory dues vs. Rule 11/KAM/EoM echo
# ---------------------------------------------------------------------------

def check_caro_statutory_dues_silence(merged_json: dict, fin_metrics: dict, applicability: dict) -> Observation | None:
    if not _is_applicable(applicability, "caro"):
        return None
    caro = merged_json.get("CARO_2020") or {}
    flags = caro.get("high_priority_flags") or {}
    if not (bool(flags.get("statutory_dues_vii")) or _clause_is_adverse(caro, "vii")):
        return None
    if _echoed(merged_json, ["statutory due", "gst", "tds", "provident fund", "income tax", "esi"]):
        return None

    return Observation(
        check_id="CONS-CARO-VII-01",
        component="Consistency — CARO (vii) Statutory Dues vs. Report-Wide Echo",
        tag="RISK_FLAG", risk_rating="Medium",
        observation=(
            "CARO clause (vii) raises a statutory-dues flag, but no corresponding matter was "
            "identified in Rule 11, the Key Audit Matters, or the Emphasis of Matter sections."
        ),
        evidence=f"CARO clause (vii) flag: {flags.get('statutory_dues_vii')}",
        evidence_required=["Statutory-dues reconciliation", "challans", "dispute status / tax orders"],
    )


# ---------------------------------------------------------------------------
# Rule 5 — Rule 11(g) audit trail vs. IFC opinion (matches EVAL-06 exactly)
# ---------------------------------------------------------------------------

def check_rule11g_ifc_tension(merged_json: dict, fin_metrics: dict, applicability: dict) -> Observation | None:
    if not _is_applicable(applicability, "ifc"):
        return None

    rule11g_adverse = False
    for sub in (merged_json.get("rule_11") or {}).get("sub_clauses") or []:
        topic = (sub.get("topic") or "").lower()
        if "audit trail" in topic and sub.get("adverse"):
            rule11g_adverse = True
            break
    if not rule11g_adverse:
        return None

    ifc = merged_json.get("IFC_REPORT") or {}
    ifc_opinion_type = ((ifc.get("ifc_opinion") or {}).get("type") or "").lower()
    ifc_has_weakness = bool((ifc.get("material_weaknesses") or {}).get("present")) or bool(ifc.get("it_controls_mentioned"))
    if ifc_opinion_type != "unmodified" or ifc_has_weakness:
        return None  # IFC already reflects a control issue — no tension

    return Observation(
        check_id="CONS-R11G-01",
        component="Consistency — Rule 11(g) Audit Trail vs. IFC Opinion",
        tag="RISK_FLAG", risk_rating="High",
        observation=(
            "Rule 11(g) reports an adverse finding on the audit trail / edit log, but the IFC "
            "opinion is unmodified and does not mention an IT-controls weakness. An audit-trail "
            "deficiency is ordinarily an IT general control matter; its absence from the IFC "
            "opinion is a cross-report tension warranting follow-up."
        ),
        evidence="Rule 11(g) sub-clause marked adverse; IFC opinion: unmodified, no IT-control weakness noted.",
        evidence_required=["ITGC report", "audit-trail logs", "access-rights matrix"],
    )


# ---------------------------------------------------------------------------
# Rule 6 — C&AG directions present but unquantified (lightweight stand-in
# for the full §25 engine — see module docstring)
# ---------------------------------------------------------------------------

def check_cag_directions_unquantified(merged_json: dict, fin_metrics: dict, applicability: dict) -> Observation | None:
    directions = merged_json.get("cag_directions") or {}
    if not directions.get("present"):
        return None
    pending = directions.get("pending_count")
    text = (directions.get("text") or "").strip()
    if not pending or text:
        return None  # nothing pending, or there's substantive text to review already

    return Observation(
        check_id="CONS-CAGDIR-01",
        component="Consistency — C&AG Directions Completeness",
        tag="AUDIT_POINTER", risk_rating="Information request only",
        observation=(
            f"The report indicates {pending} pending C&AG direction(s) under section 143(5) but "
            f"no reconciling text was extracted for them. This is a completeness pointer, not a "
            f"finding — the actual directions and the auditor's response require separate "
            f"verification (source spec §25.1's non-response rule)."
        ),
        evidence=f"cag_directions.pending_count={pending}",
        evidence_required=["Actual C&AG directions/sub-directions for the assignment year", "auditor's response", "impact workings"],
    )


# ---------------------------------------------------------------------------
# Rule 7 — KAM present but appears to miss an already-computed distress signal
# ---------------------------------------------------------------------------

def check_kam_high_risk_silence(merged_json: dict, fin_metrics: dict, applicability: dict) -> Observation | None:
    kam = merged_json.get("key_audit_matters") or {}
    if not kam.get("present"):
        return None  # nothing to check coverage of; absence itself is applicability.py's concern
    distress_signals = fin_metrics.get("distress_signals") or []
    if not distress_signals:
        return None

    kam_text = " ".join(
        " ".join(str(item.get(k, "")) for k in ("title", "why_significant", "related_note"))
        for item in (kam.get("items") or [])
    ).lower()
    covers_liquidity = any(
        kw in kam_text for kw in ("going concern", "liquidity", "net worth", "current ratio", "cash flow")
    )
    if covers_liquidity:
        return None

    return Observation(
        check_id="CONS-KAM-01",
        component="Consistency — KAM Coverage vs. Financial-Statement Distress Signals",
        tag="RISK_FLAG", risk_rating="Medium",
        observation=(
            "The financial statements show distress indicator(s) ("
            + "; ".join(distress_signals[:2])
            + "), but the Key Audit Matters do not appear to cover going concern, liquidity, or "
            "cash-flow risk. Per source spec §19, high-risk financial-statement areas are expected "
            "to be compared against KAM coverage."
        ),
        evidence="; ".join(distress_signals),
        evidence_required=["Management's assessment of the identified distress indicator(s)"],
    )


_ALL_RULES = [
    check_caro_going_concern_silence,
    check_caro_fraud_silence,
    check_caro_rpt_silence,
    check_caro_statutory_dues_silence,
    check_rule11g_ifc_tension,
    check_cag_directions_unquantified,
    check_kam_high_risk_silence,
]


def run_consistency_checks(merged_json: dict, fin_metrics: dict, applicability: dict) -> list[Observation]:
    """Runs every rule above once. A rule raising an exception is logged
    and skipped rather than aborting the others — one malformed extractor
    field should not take down every other consistency check."""
    observations: list[Observation] = []
    for rule in _ALL_RULES:
        try:
            result = rule(merged_json, fin_metrics, applicability)
        except Exception:
            logger.exception("Consistency rule %s failed", rule.__name__)
            continue
        if result is not None:
            observations.append(result)
    return observations
