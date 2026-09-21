"""
output_classification.py — Tags observations per the LLM Output Specification
=================================================================================
Gap-closure Phase 4. Implements the classification fields the output spec's
§18 "Recommended Structured Observation Object" requires that nothing in
Phases 1–3 produced: `consistency_type`, `sa_framework`, `source`,
`recommended_audit_action`, `candidate_143_6`. See ../GAP_CLOSURE_LOG.md,
Phase 4, for the full write-up and — importantly — the honest limitations
section, because several of the mappings here are judgement calls this
module had to make, not values the spec itself dictates.

WHY A STATIC check_id -> METADATA TABLE, NOT PER-CHECK ARGUMENTS
--------------------------------------------------------------------
Same pattern as check_registry.py: rather than thread five new parameters
through every rule function in consistency_engine.py / tool_sar.py /
pervasiveness.py / prior_year_continuity.py, this module tags observations
AFTER they're built, by looking up `check_id` in one static table. That
keeps every existing rule function's signature untouched, and keeps this
classification decision in one auditable place instead of scattered across
five files.

THE HONEST PART — READ BEFORE TRUSTING `source` / `consistency_type`
-------------------------------------------------------------------------
The output spec's example object implies each observation cleanly
decomposes into what the Financial Statements say, what the Audit Report
says, and what CARO says — as separate quoted extracts. Our checks don't
carry that: each one captures a single `evidence` string, sometimes from
one document, sometimes a synthesised fact ("Opinion type: Unmodified |
CARO adverse clauses: 2") that doesn't cleanly belong to just one document.
So `source` here is populated by **bucketing that one evidence string into
whichever document(s) `consistency_type` implies** — e.g. an `AR_CARO`
check's evidence lands in both `source.audit_report` and `source.caro`.
That satisfies the spec's *shape* (§18's schema) but not its apparent
intent (a genuine per-document quote breakdown), because we don't extract
FS-notes text or keep multiple quotes per check. Treat `source` as "which
documents this observation concerns", not as "the exact wording each one
used."

`sa_framework.materiality` / `.pervasiveness` default to "Requires
Assessment" / "Not Established" wherever this module can't derive a real
value from the observation itself (most checks) — never "Material" /
"Pervasive" by default, because asserting either without a real
quantification would be inventing a conclusion, which is exactly what the
source spec's guardrails (§3.1) prohibit.
"""

from __future__ import annotations

from typing import TypedDict

from sar_prod_v3.observation import Observation


class ClassificationMeta(TypedDict, total=False):
    consistency_type: str  # FS_AR | FS_CARO | AR_CARO | FS_AR_CARO | OTHER
    sa_framework: dict
    recommended_action: list[str]


def _sa(standard: str, nature: str, *, coherence: str = "Requires Evidence") -> dict:
    return {
        "standard": standard,
        "nature": nature,
        "materiality": "Requires Assessment",
        "pervasiveness": "Not Established",
        "opinion_coherence_issue": coherence,
    }


# check_id -> classification. Deliberately explicit per ID rather than
# pattern-matched on component text — a check's *identity* determines what
# kind of comparison it is; matching on wording risks misclassifying a
# check whose observation text happens to mention another document type.
_CLASSIFICATION: dict[str, ClassificationMeta] = {
    "CHK-COH-01": {  # unmodified opinion vs. CARO adverse clauses
        "consistency_type": "AR_CARO",
        "sa_framework": _sa("SA 705", "Reporting Issue"),
        "recommended_action": ["Raise audit query and obtain the statutory auditor's explanation for the coherence gap."],
    },
    "CHK-COH-02": {  # unmodified opinion vs. IFC material weakness — both live inside the audit-report package (Annexure B), not a cross-document comparison
        "consistency_type": "OTHER",
        "sa_framework": _sa("SA 705", "Reporting Issue"),
        "recommended_action": ["Raise audit query and obtain the statutory auditor's explanation for the coherence gap."],
    },
    "CHK-COH-03": {  # unmodified + no going concern + FS distress signals
        "consistency_type": "FS_AR",
        "sa_framework": _sa("SA 570", "Reporting Issue"),
        "recommended_action": ["Obtain the statutory auditor's going-concern assessment and management's own assessment."],
    },
    "CHK-EOM-01": {  # SA 706.8 closing sentence
        "consistency_type": "OTHER",
        "sa_framework": _sa("Other", "Reporting Issue", coherence="No"),
        "recommended_action": ["Verify against the signed report whether the mandatory closing sentence is genuinely absent."],
    },
    "CHK-KAM-01": {  # SA 701 "how our audit addressed" per-matter requirement
        "consistency_type": "OTHER",
        "sa_framework": _sa("SA 701", "Reporting Issue", coherence="No"),
        "recommended_action": ["Verify against the signed report whether the auditor's-response description is genuinely absent for the listed matter(s)."],
    },
    "CHK-DATE-01": {  # report date vs FS approval date
        "consistency_type": "OTHER",
        "sa_framework": _sa("Other", "Reporting Issue", coherence="No"),
        "recommended_action": ["Verify the report and FS-approval dates against the signed report and Board minutes."],
    },
    "CHK-UDIN-01": {
        "consistency_type": "OTHER",
        "recommended_action": ["Verify UDIN against the ICAI UDIN portal."],
    },
    "CONS-CARO-IX-01": {  # CARO (ix)/(xix) vs going concern
        "consistency_type": "AR_CARO",
        "sa_framework": _sa("SA 570", "Reporting Issue"),
        "recommended_action": ["Obtain management's going-concern assessment and the lender correspondence on the reported default."],
    },
    "CONS-CARO-XI-01": {  # fraud silence
        "consistency_type": "AR_CARO",
        "recommended_action": ["Obtain the section 143(12) fraud report (if any) and Board whistle-blower disclosures."],
    },
    "CONS-CARO-XIII-01": {  # RPT silence
        "consistency_type": "AR_CARO",
        "recommended_action": ["Requisition the related-party transaction register and ss.177/188 approvals."],
    },
    "CONS-CARO-VII-01": {  # statutory dues silence
        "consistency_type": "AR_CARO",
        "recommended_action": ["Requisition the statutory-dues reconciliation, challans and dispute status."],
    },
    "CONS-R11G-01": {  # Rule 11(g) vs IFC — both within the audit-report package
        "consistency_type": "OTHER",
        "sa_framework": _sa("Other", "Reporting Issue"),
        "recommended_action": ["Obtain the ITGC report, audit-trail logs and access-rights matrix."],
    },
    "CONS-CAGDIR-01": {
        "consistency_type": "OTHER",
        "recommended_action": ["Obtain the actual C&AG directions/sub-directions for the assignment year and the auditor's impact workings."],
    },
    "CONS-KAM-01": {  # KAM vs FS distress signal
        "consistency_type": "FS_AR",
        "recommended_action": ["Obtain management's assessment of the identified distress indicator(s)."],
    },
    "PERV-01": {  # opinion type vs its own recorded pervasiveness cues
        "consistency_type": "OTHER",
        "sa_framework": _sa("SA 705", "Misstatement", coherence="Yes"),
        "recommended_action": ["Obtain the auditor's own pervasiveness assessment / working papers."],
    },
    "PERV-02": {  # modification amount vs FS tables
        "consistency_type": "FS_AR",
        "sa_framework": _sa("SA 705", "Misstatement"),
        "recommended_action": ["Obtain the FS note reconciliation working and the auditor's quantification working paper."],
    },
    "PRIOR-OPN-01": {  # prior-year vs current-year opinion trend
        "consistency_type": "OTHER",
        "sa_framework": _sa("SA 700", "Reporting Issue"),
        "recommended_action": ["Obtain the prior-year auditor's report and management's explanation for the trend."],
    },
}

# C&AG direction checks (Gap-closure Phase 5, Gap #2) — not a cross-document
# consistency comparison at all (it's the report's own text vs. an external
# standing document), so consistency_type is OTHER for all five.
for _roman in ("I", "II", "III", "IV", "V"):
    _CLASSIFICATION[f"DIR-{_roman}-01"] = {
        "consistency_type": "OTHER",
        "recommended_action": [
            "Obtain the actual C&AG direction text and the auditor's response for this theme.",
            "Obtain impact workings if the direction requires a quantified answer.",
        ],
    }

# Applicability pointers (APPL-*) and pre-flight component-missing checks
# (PRE-*) share one classification — neither is a cross-document
# consistency question, both resolve the same way.
_DEFAULT_APPLICABILITY_OR_PREFLIGHT: ClassificationMeta = {
    "consistency_type": "OTHER",
    "recommended_action": ["Verify statutory applicability against entity classification data for the assignment year."],
}

_DEFAULT_UNCLASSIFIED: ClassificationMeta = {
    "consistency_type": "OTHER",
    "recommended_action": ["Raise audit query and obtain the statutory auditor's explanation."],
}


def _lookup(check_id: str) -> ClassificationMeta:
    if check_id in _CLASSIFICATION:
        return _CLASSIFICATION[check_id]
    if check_id.startswith(("APPL-", "PRE-")):
        return _DEFAULT_APPLICABILITY_OR_PREFLIGHT
    return _DEFAULT_UNCLASSIFIED


def _bucket_source(consistency_type: str, evidence: str) -> dict:
    """Best-effort `source` population — see module docstring. Puts the
    same evidence string into every document bucket the consistency_type
    implies; an AUDIT_POINTER-only check with no consistency_type meaning
    (OTHER) gets no bucket at all, since there's no comparison to attribute."""
    buckets: dict[str, list] = {"financial_statements": [], "audit_report": [], "caro": []}
    if not evidence:
        return buckets
    if consistency_type in ("FS_AR", "FS_AR_CARO"):
        buckets["financial_statements"].append(evidence)
        buckets["audit_report"].append(evidence)
    if consistency_type in ("FS_CARO", "FS_AR_CARO"):
        buckets["financial_statements"].append(evidence)
        buckets["caro"].append(evidence)
    if consistency_type in ("AR_CARO", "FS_AR_CARO"):
        buckets["audit_report"].append(evidence)
        buckets["caro"].append(evidence)
    return buckets


def is_candidate_143_6(obs: Observation) -> bool:
    """Output-spec §11 inclusion threshold, reduced to what's mechanically
    checkable: clear source trace (is_traceable — already enforced by
    suppress_untraceable before this runs, so tag==FINDING implies it
    passed) + materiality by nature-or-value (approximated here as
    risk_rating High) + tag==FINDING (a RISK_FLAG or AUDIT_POINTER is, by
    the wiki's own tag definitions, not yet established enough to be a
    143(6) candidate). This is a conservative proxy for the spec's fuller
    threshold ("sufficiently developed issue", "evidence considered or
    explicitly pending") — those two are judgement calls no deterministic
    rule can make; a human still decides for real, per the spec's own
    'Status: DRAFT — HUMAN REVIEW REQUIRED' requirement (§11 last line).
    """
    return obs.tag == "FINDING" and obs.risk_rating == "High" and obs.is_traceable()


def apply_output_classification(observations: list[Observation]) -> list[Observation]:
    """Runs once, late in the pipeline merge sequence (after suppression,
    after risk-elevation passes, so tag/risk_rating are final) — stamps
    consistency_type, sa_framework, source, recommended_audit_action and
    candidate_143_6 onto every observation."""
    for obs in observations:
        meta = _lookup(obs.check_id)
        consistency_type = meta.get("consistency_type", "OTHER")
        obs.consistency_type = consistency_type
        if "sa_framework" in meta:
            obs.sa_framework = dict(meta["sa_framework"])
        obs.source = _bucket_source(consistency_type, obs.evidence)
        if not obs.recommended_audit_action:  # never overwrite a rule that already set its own
            obs.recommended_audit_action = list(meta.get("recommended_action", []))
        obs.candidate_143_6 = is_candidate_143_6(obs)
    return observations
