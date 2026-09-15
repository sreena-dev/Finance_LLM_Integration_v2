"""
check_registry.py — Central check-ID registry
================================================
Gap-closure Phase 1, byproduct of Gap #1 (source spec §45, "Check-ID
Architecture"). See ../GAP_CLOSURE_LOG.md for rationale and revert steps.

Before this module, check IDs were scattered ad hoc across tool_sar.py
(`PRE-01`, `CHK-UDIN-01`, `CHK-COH-01`, ...) with no single place that says
what a given ID means, what module owns it, or what source-spec clause it
implements. The writer prompt (PROMPT.md) also *invents* IDs in its own
prose ("OPN-705-01", "CARO-IX-02") that have never existed anywhere in code
— those are a Phase-2 concern (they belong to the writer's own findings,
which don't flow through this registry yet; see observation.py's module
docstring).

This registry covers every check ID the deterministic (non-LLM) layer can
currently emit. `tests/test_check_registry.py` asserts that stays true —
it fails the moment a new check_id is introduced in tool_sar.py or
formal_review.py without a matching registry entry, so registry drift is
caught mechanically rather than relying on someone remembering to update
this file by hand.
"""

from __future__ import annotations

from typing import TypedDict


class CheckMeta(TypedDict, total=False):
    module: str
    description: str
    source_basis: str
    applicability_condition: str
    severity_default: str


# Pre-flight mandatory-component checks (PRE-01..PRE-09) share one shape and
# one source of truth: `tool_sar.CheckTools._MANDATORY_SECTIONS`. Rather than
# retype each description here (and risk the two drifting apart), this
# registry imports that dict and derives its own entries from it — see
# `_preflight_entries()` below. Import is late-bound as a function to avoid
# forcing tool_sar's import machinery to run merely by importing this module
# for something like `get_check_meta("CHK-UDIN-01")`.
def _preflight_entries() -> dict[str, CheckMeta]:
    from sar_prod_v3.tool_sar import CheckTools

    entries: dict[str, CheckMeta] = {}
    for check_id, cfg in CheckTools._MANDATORY_SECTIONS.items():
        entries[check_id] = CheckMeta(
            module="01_input_validator",
            description=f"Mandatory-component check: {cfg['component']}",
            source_basis="Source spec §8 (pre-analysis validation) / §56 (input control)",
            applicability_condition="Always run — every SAR package is expected to contain this component.",
            severity_default=cfg["tag"],
        )
    return entries


# Non-pre-flight deterministic checks. Declared directly (they don't share a
# single config dict the way pre-flight does).
_STATIC_ENTRIES: dict[str, CheckMeta] = {
    "CHK-UDIN-01": CheckMeta(
        module="18_formal_validation_engine",
        description="UDIN presence and ICAI 18-character format validation.",
        source_basis="Source spec §29.1 (UDIN handling)",
        applicability_condition="Always run, once per auditor/firm listed in the report.",
        severity_default="AUDIT_POINTER",
    ),
    "CHK-EOM-01": CheckMeta(
        module="08_eom_other_matter_engine",
        description="SA 706.8 mandatory Emphasis-of-Matter closing sentence presence check.",
        source_basis="Source spec §17 (Emphasis of Matter engine); SA 706 paragraph 8",
        applicability_condition="Run only when the report contains an Emphasis of Matter section.",
        severity_default="FINDING",
    ),
    "CHK-DATE-01": CheckMeta(
        module="18_formal_validation_engine",
        description=(
            "Report-date sequencing: the auditor's report date must not precede the date "
            "the financial statements were approved by those charged with governance."
        ),
        source_basis="Source spec §30 (Date Logic); Companies Act 2013 s.134(1)",
        applicability_condition="Run whenever both the report date and the FS approval date were extracted.",
        severity_default="FINDING",
    ),
    "CHK-COH-01": CheckMeta(
        module="06_opinion_coherence_engine",
        description="Unmodified main opinion vs. one or more adverse/unfavourable CARO clauses.",
        source_basis="Source spec §13.3 (opinion immediate checks) / §26 (consistency matrix)",
        applicability_condition="Run whenever CARO clause data was extracted.",
        severity_default="High",
    ),
    "CHK-COH-02": CheckMeta(
        module="06_opinion_coherence_engine",
        description="Unmodified main opinion vs. a disclosed IFC material weakness.",
        source_basis="Source spec §13.3 (opinion immediate checks) / §24 (IFC review engine)",
        applicability_condition="Run whenever IFC data was extracted.",
        severity_default="High",
    ),
    "CHK-COH-03": CheckMeta(
        module="07_going_concern_engine",
        description="Unmodified main opinion, no going-concern disclosure, but distress signals present in the financial statements.",
        source_basis="Source spec §16 (Going-Concern Engine)",
        applicability_condition="Run whenever financial-statement ratios were computed.",
        severity_default="High",
    ),

    # --- Gap-closure Phase 2, Gap #4 (applicability.py) ---
    "APPL-CARO-01": CheckMeta(
        module="12_caro_applicability_engine",
        description="CARO 2020 applicability could not be confirmed from the supplied package.",
        source_basis="Source spec §22 (CARO Applicability State Machine)",
        applicability_condition="Run once per report; fires only when applicability resolves to 'uncertain'.",
        severity_default="AUDIT_POINTER",
    ),
    "APPL-IFC-01": CheckMeta(
        module="14_ifc_engine",
        description="IFC (s.143(3)(i)) applicability could not be confirmed from the supplied package.",
        source_basis="Source spec §22's CARO-analogous logic, applied to IFC",
        applicability_condition="Run once per report; fires only when applicability resolves to 'uncertain'.",
        severity_default="AUDIT_POINTER",
    ),
    "APPL-KAM-01": CheckMeta(
        module="09_kam_engine",
        description="Key Audit Matters applicability (listed-entity status) could not be confirmed from the supplied package.",
        source_basis="Source spec §19.1 (KAM applicability gate)",
        applicability_condition="Run once per report; fires only when no KAM section was identified.",
        severity_default="AUDIT_POINTER",
    ),

    # --- Gap-closure Phase 2, Gap #3 (consistency_engine.py) ---
    "CONS-CARO-IX-01": CheckMeta(
        module="16_consistency_matrix_engine",
        description="CARO (ix) default / (xix) adverse with no going-concern discussion in the main report.",
        source_basis="Source spec §26 rows 'MURGC<->CARO(xix)' and 'CARO(ix)<->Going concern'; matches EVAL-03",
        applicability_condition="Run only when CARO applicability is 'applicable'.",
        severity_default="High",
    ),
    "CONS-CARO-XI-01": CheckMeta(
        module="16_consistency_matrix_engine",
        description="CARO (xi) fraud flag with no echo of fraud / s.143(12) elsewhere in the report.",
        source_basis="Source spec §28 named silence scenario",
        applicability_condition="Run only when CARO applicability is 'applicable'.",
        severity_default="High",
    ),
    "CONS-CARO-XIII-01": CheckMeta(
        module="16_consistency_matrix_engine",
        description="CARO (xiii) related-party flag with no echo in KAM/EoM.",
        source_basis="Source spec §28 named silence scenario (significant related-party balances)",
        applicability_condition="Run only when CARO applicability is 'applicable'.",
        severity_default="Medium",
    ),
    "CONS-CARO-VII-01": CheckMeta(
        module="16_consistency_matrix_engine",
        description="CARO (vii) statutory-dues flag with no echo in Rule 11 / KAM / EoM.",
        source_basis="Source spec §26 row 'CARO(vii)<->Statutory-dues notes'",
        applicability_condition="Run only when CARO applicability is 'applicable'.",
        severity_default="Medium",
    ),
    "CONS-R11G-01": CheckMeta(
        module="16_consistency_matrix_engine",
        description="Rule 11(g) audit-trail adverse finding with an unmodified IFC opinion and no IT-control weakness noted.",
        source_basis="Source spec §26 row 'IFC<->Rule 11(g)'; matches EVAL-06",
        applicability_condition="Run only when IFC applicability is 'applicable'.",
        severity_default="High",
    ),
    "CONS-CAGDIR-01": CheckMeta(
        module="15_cag_directions_engine",
        description="C&AG directions present with a pending count but no reconciling text extracted.",
        source_basis="Source spec §25.1 (non-response rule) — lightweight stand-in pending Gap #2",
        applicability_condition="Run whenever cag_directions.present is true.",
        severity_default="AUDIT_POINTER",
    ),
    "CONS-KAM-01": CheckMeta(
        module="16_consistency_matrix_engine",
        description="KAM section exists but does not appear to cover an already-computed financial distress signal.",
        source_basis="Source spec §19 (compare high-risk FS areas against the KAM set)",
        applicability_condition="Run only when a KAM section was identified and at least one distress signal was computed.",
        severity_default="Medium",
    ),

    # --- Gap-closure Phase 3, Gap #8 (pervasiveness.py) ---
    "PERV-01": CheckMeta(
        module="13_caro_clause_engine",  # opinion classification / pervasiveness engine
        description="Qualified opinion with a high pervasiveness-cue count recorded for its modification.",
        source_basis="Source spec §14 (Pervasiveness Detection) / §13.2 (SA 705 decision table)",
        applicability_condition="Run only when opinion.type is qualified or adverse and modification.pervasiveness_cues data was extracted.",
        severity_default="High",
    ),
    "PERV-02": CheckMeta(
        module="06_opinion_coherence_engine",
        description="Modification's quantified amount not found verbatim in the fetched financial-statement tables.",
        source_basis="Source spec §15 (Modified Opinion Quantification Logic)",
        applicability_condition="Run only when a quantified amount and affected line item(s) were extracted.",
        severity_default="AUDIT_POINTER",
    ),

    # --- Gap-closure Phase 3, Gap #6 (prior_year_continuity.py) ---
    "PRIOR-OPN-01": CheckMeta(
        module="16_consistency_matrix_engine",
        description="Prior-year opinion type compared against the current-year opinion type.",
        source_basis="Source spec §26 row 'Prior modification <-> Current report: resolution'",
        applicability_condition="Run only when a prior-year trend record exists for this company (sar_results table).",
        severity_default="RISK_FLAG or AUDIT_POINTER depending on direction",
    ),

    # --- Gap-closure Phase 5, Gap #2 (cag_directions_engine.py) ---
    **{
        f"DIR-{roman}-01": CheckMeta(
            module="15_cag_directions_engine",
            description=f"C&AG §143(5) standing direction {roman}: not evidenced in the report's reproduced directions text.",
            source_basis="Source spec §25.1 (direction handling rule) / §25.2 (direction themes)",
            applicability_condition="Run only when a standing directions document is in effect for the report's date (REFERENCE_DSN.cag_directions_chunks).",
            severity_default="FINDING (no text at all) or RISK_FLAG (theme not evidenced)",
        )
        for roman in ("I", "II", "III", "IV", "V")
    },
}


def _build_registry() -> dict[str, CheckMeta]:
    registry: dict[str, CheckMeta] = dict(_STATIC_ENTRIES)
    try:
        registry.update(_preflight_entries())
    except Exception:
        # tool_sar couldn't be imported (e.g. a stripped-down environment) —
        # the static entries above still work; pre-flight metadata just
        # isn't available until tool_sar is importable. get_check_meta()
        # falls back to the "unregistered" shape for PRE-* ids in that case.
        pass
    return registry


CHECK_REGISTRY: dict[str, CheckMeta] = _build_registry()

_UNKNOWN: CheckMeta = CheckMeta(
    module="unknown",
    description="No registry entry for this check_id.",
    source_basis="unknown",
    applicability_condition="unknown",
    severity_default="unknown",
)


def get_check_meta(check_id: str) -> CheckMeta:
    """Registry lookup with a safe fallback — never raises, so a call site
    can log/display registry metadata without an extra existence check."""
    return CHECK_REGISTRY.get(check_id, _UNKNOWN)


def is_registered(check_id: str) -> bool:
    return check_id in CHECK_REGISTRY


def unregistered_check_ids(check_ids: list[str]) -> list[str]:
    """Given every check_id a pipeline run actually emitted, returns the
    ones with no registry entry — the drift guard used by
    tests/test_check_registry.py and available for a future audit-log pass
    (source spec §53 quality monitoring) to flag on real runs too."""
    return sorted({cid for cid in check_ids if not is_registered(cid)})
