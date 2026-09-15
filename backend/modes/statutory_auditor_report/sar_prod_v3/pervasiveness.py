"""
pervasiveness.py — Pervasiveness detection + modification-quantification cross-check
========================================================================================
Gap-closure Phase 3, Gap #8. See ../GAP_CLOSURE_LOG.md for full rationale,
and the important caveat below about the EXTRACTOR_MAIN schema change this
depends on.

WHY THIS EXISTS
---------------
Source spec §14 (Pervasiveness Detection) and §15 (Modified Opinion
Quantification Logic) were entirely unimplemented before this module: the
opinion classification engine (EXTRACTOR_MAIN) captured only the opinion
type, never the pervasiveness cues or the quantified amount §13.2's own
mapping table depends on, and nothing reconciled a stated amount against
the fetched financial-statement tables.

IMPORTANT — THIS REQUIRES A PROMPT.md SCHEMA CHANGE THAT IS UNVALIDATED
AGAINST A LIVE MODEL
------------------------------------------------------------------------
This module reads `merged_json["modification"]`, a new block added to
EXTRACTOR_MAIN's JSON schema in PROMPT.md (see that file's diff) with
`quantified_amount`, `affected_line_items`, `affected_note_ref`,
`tax_effect_quote`, and 5 boolean `pervasiveness_cues`. Every function
here is written defensively — `(merged_json.get("modification") or {})`
throughout — so a report generated with the OLD prompt (before this
schema addition) or an LLM that ignores the new instruction and omits the
block entirely produces a package with no `modification` data, and every
function below simply returns None (no crash, no observation, exactly as
if pervasiveness detection weren't implemented at all). This sandbox has
no live model to confirm the extractor actually populates the new fields
well — treat that as unverified until confirmed against a real run (see
GAP_CLOSURE_LOG.md, "What's tested").

WHAT THIS DOES
--------------
`check_opinion_type_vs_pervasiveness()` — cross-checks the extractor's
stated opinion type (Qualified/Adverse) against how many of the 5
pervasiveness cues it itself recorded for the same modification. Per §14:
"the LLM should detect, but not independently adjudicate, pervasiveness"
— this NEVER overrides the stated opinion, it only raises a RISK_FLAG when
a Qualified opinion carries a high cue count (which the SA 705 decision
table would ordinarily associate with Adverse/Disclaimer instead), for
human review.

`check_modification_amount_reconciles()` — when a quantified amount and
affected line item(s) were extracted, checks whether that amount appears
in the already-fetched financial-statement tables. A mismatch is an
AUDIT_POINTER (not a FINDING) — it may be a formatting/rounding artefact
of markdown table extraction, not a genuine discrepancy; source spec §15
says "reconcile the amount with financial statements", not "conclude
misstatement from a text-match miss".
"""

from __future__ import annotations

from sar_prod_v3.observation import Observation


def count_pervasiveness_cues(modification: dict) -> int:
    cues = (modification or {}).get("pervasiveness_cues") or {}
    return sum(1 for v in cues.values() if v)


def check_opinion_type_vs_pervasiveness(merged_json: dict) -> Observation | None:
    opinion = merged_json.get("opinion") or {}
    opinion_type = (opinion.get("type") or "").lower()
    if opinion_type not in {"qualified", "adverse"}:
        return None  # only meaningful for a misstatement/scope-limitation modification

    modification = merged_json.get("modification") or {}
    cue_count = count_pervasiveness_cues(modification)
    if cue_count == 0:
        return None  # no cues recorded (old-schema package, or genuinely none present)

    if opinion_type == "qualified" and cue_count >= 3:
        return Observation(
            check_id="PERV-01",
            component="Pervasiveness — Opinion Type vs. Recorded Cues",
            tag="RISK_FLAG", risk_rating="High",
            observation=(
                f"The opinion is Qualified, but {cue_count} of 5 pervasiveness cues were recorded "
                f"for the underlying modification (source spec §14). Per the SA 705 decision "
                f"table, a pervasive effect would ordinarily point to Adverse or Disclaimer rather "
                f"than Qualified. This is a cross-check for human review, not a re-classification "
                f"of the auditor's stated opinion."
            ),
            evidence=f"Opinion type: qualified | Pervasiveness cues recorded: {cue_count}/5",
            evidence_required=["Auditor's own pervasiveness assessment / working papers"],
        )
    return None


def check_modification_amount_reconciles(merged_json: dict, financial_tables: dict) -> Observation | None:
    modification = merged_json.get("modification") or {}
    amount = modification.get("quantified_amount")
    line_items = modification.get("affected_line_items") or []
    if amount is None or not line_items:
        return None

    haystack = "\n".join(
        (tbl or {}).get("table_md", "") for tbl in (financial_tables or {}).values() if isinstance(tbl, dict)
    ).replace(",", "")
    amount_str = str(amount).replace(",", "")
    if not amount_str or amount_str in haystack:
        return None  # reconciles (or nothing to compare) — nothing to flag

    return Observation(
        check_id="PERV-02",
        component="Modification Amount vs. Financial Statements",
        tag="AUDIT_POINTER", risk_rating="Information request only",
        observation=(
            f"The modification's quantified amount ({amount}) was not found verbatim in the "
            f"supplied financial-statement tables for the affected line item(s) "
            f"({', '.join(str(li) for li in line_items)}). This may be a formatting or rounding "
            f"difference in extraction, not a genuine reconciliation gap — verify against the "
            f"source note before treating as a discrepancy."
        ),
        evidence=f"Quantified amount: {amount} | Affected line items: {line_items}",
        evidence_required=["FS note reconciliation working", "auditor's quantification working paper"],
    )


def run_pervasiveness_checks(merged_json: dict, financial_tables: dict) -> list[Observation]:
    observations = []
    for result in (
        check_opinion_type_vs_pervasiveness(merged_json),
        check_modification_amount_reconciles(merged_json, financial_tables),
    ):
        if result is not None:
            observations.append(result)
    return observations
