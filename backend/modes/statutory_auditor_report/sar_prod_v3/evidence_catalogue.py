"""
evidence_catalogue.py — Evidence Request Catalogue
=====================================================
Gap-closure Phase 3, Gap #9. See ../GAP_CLOSURE_LOG.md for full rationale
and revert steps.

WHY THIS EXISTS
---------------
Source spec §42 defines eight issue-type -> evidence-request mappings
(modified opinion, going concern, CARO default/statutory dues, IFC/audit
trail, C&AG directions, related parties, grants/scheme funds, litigation).
Before this module, several Gap-closure Phase 2 rules
(consistency_engine.py) already attached an ad-hoc `evidence_required`
list per rule, but there was no single catalogue backing them, no
guarantee every check_id got one, and no reusable classification from
free text to an issue type for checks that don't yet set anything
(pre-flight, formal checks, applicability pointers).

`EVIDENCE_CATALOGUE` below is the wiki's own §42 lists, verbatim, keyed by
issue type. `classify_issue_type()` is a keyword classifier over an
observation's component + observation text. `enrich_evidence_required()`
is the pipeline-facing entry point: it fills in `evidence_required` for
any observation that doesn't already have one (rule-specific lists set by
consistency_engine.py etc. are never overwritten — this only fills gaps).

KEYWORD CHOICES — read before extending
------------------------------------------
Keyword lists are deliberately narrower than a naive reading of each
category name would suggest. "regulatory non-compliance" is matched by the
phrase "non-compliance with" / "contravention of", not the bare word
"regulatory" — PRE-04's own component name is literally "Report on Other
Legal and Regulatory Requirements", which would false-positive-match every
single report if "regulatory" alone were a keyword. Keep this in mind
before adding a new keyword: test it against the existing check
descriptions in check_registry.py first.
"""

from __future__ import annotations

from sar_prod_v3.observation import Observation

EVIDENCE_CATALOGUE: dict[str, list[str]] = {
    "modified_opinion": [
        "Quantification working for the modification",
        "Management's response to the modification",
        "Audit adjustment (if any) arising from the modification",
        "Note reconciliation for the affected line item(s)",
    ],
    "going_concern": [
        "Management's going-concern assessment",
        "Cash-flow forecasts",
        "Funding letters",
        "Loan covenants",
        "Board minutes",
        "Post-year financing arrangements",
    ],
    "caro_default_statutory_dues": [
        "Loan statements",
        "Lender confirmations",
        "Statutory-dues reconciliation",
        "Challans",
        "Tax orders",
        "Dispute status",
    ],
    "ifc_audit_trail": [
        "IFC testing summary",
        "ITGC report",
        "Audit-trail logs",
        "Access-rights matrix",
        "Manual-journal dump",
    ],
    "cag_directions": [
        "Actual C&AG directions/sub-directions for the assignment year",
        "Auditor's response to each direction",
        "Impact workings",
        "Supporting schedules",
    ],
    "related_parties": [
        "Related-party transaction register",
        "Board/committee approvals",
        "Related-party contracts",
        "Confirmations",
        "Arm's-length assessment",
        "Sections 177/188 workings",
    ],
    "grants_scheme_funds": [
        "Sanction order",
        "Terms and conditions of the grant/scheme",
        "Utilisation certificate",
        "Interest accounting on unspent balances",
        "Bank statements",
        "Unspent-balance schedule",
    ],
    "litigation": [
        "Legal opinions",
        "Case status updates",
        "Management's assessment",
        "Provision working",
        "Board/Audit Committee minutes",
    ],
}

# (issue_type, [keyword substrings — case-insensitive, any-match]). Order
# matters only in that the first matching category wins; kept deliberately
# non-overlapping in practice (see module docstring on keyword choices).
_KEYWORD_RULES: list[tuple[str, list[str]]] = [
    ("going_concern", ["going concern", "murgc", "material uncertainty related to going concern"]),
    ("caro_default_statutory_dues", ["statutory due", "clause (vii)", "loan default", "clause (ix)", "loan/borrowing default"]),
    ("ifc_audit_trail", ["audit trail", "internal financial control", "ifc", "rule 11(g)"]),
    ("cag_directions", ["c&ag direction", "section 143(5)", "comptroller and auditor general"]),
    ("related_parties", ["related part", "rpt", "clause (xiii)", "section 177", "section 188"]),
    ("grants_scheme_funds", ["grant", "subsidy", "scheme fund"]),
    ("litigation", ["litigation", "contingent liabilit"]),
    ("modified_opinion", ["qualified opinion", "adverse opinion", "disclaimer", "modified opinion", "basis for opinion", "basis for qualified"]),
]


def classify_issue_type(text: str) -> str | None:
    lower = f" {(text or '').lower()} "
    for issue_type, keywords in _KEYWORD_RULES:
        if any(kw in lower for kw in keywords):
            return issue_type
    return None


def evidence_for(issue_type: str) -> list[str]:
    return list(EVIDENCE_CATALOGUE.get(issue_type, []))


def enrich_evidence_required(observations: list[Observation]) -> list[Observation]:
    """Fills `evidence_required` for any observation that doesn't already
    have one. Never overwrites a rule-specific list a check already set
    (consistency_engine.py's rules, for instance, already set their own,
    more targeted lists) — this only covers the gap for checks that raise
    an observation without one (pre-flight, formal checks, applicability
    pointers, and any future check that forgets to)."""
    for obs in observations:
        if obs.evidence_required:
            continue
        issue_type = classify_issue_type(f"{obs.component} {obs.observation}")
        if issue_type:
            obs.evidence_required = evidence_for(issue_type)
    return observations
