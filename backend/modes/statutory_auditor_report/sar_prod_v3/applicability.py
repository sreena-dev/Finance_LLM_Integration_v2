"""
applicability.py — CARO / IFC / KAM applicability gates
==========================================================
Gap-closure Phase 2, Gap #4 ("Applicability Gates"). See
../GAP_CLOSURE_LOG.md for full rationale and revert steps.

WHY THIS EXISTS
---------------
Before this module, CARO, IFC and KAM analysis always ran in full,
regardless of whether any of the three actually applied to this entity for
this year. Source spec §22 states the CARO state machine explicitly:

    "The model must never run the full clause library when applicability
    is unresolved."

and §19.1 states the same for KAM:

    "IF KAM applicability == uncertain THEN AUDIT_POINTER AND do not
    create definitive missing-KAM finding."

Running everything unconditionally produces false-positive "missing
CARO clause" / "missing KAM" noise whenever the annexure is genuinely not
applicable rather than genuinely missing from the package — those are two
different facts and the wiki treats them as such (§8 annexure mapping;
§56 "gates CARO by applicability").

HOW APPLICABILITY IS RESOLVED HERE
------------------------------------
Three states per area, matching the wiki's own enum (§6 assignment
context contract): "applicable" | "not_applicable" | "uncertain".

  * CARO — "not_applicable" only when the package itself states so
    explicitly (a directly verifiable package-internal fact), or the CARO
    extractor's own `caro_applicable` field is False. "applicable" when
    CARO text was found and/or the extractor confirms applicability.
    Otherwise "uncertain" — CARO text simply wasn't found, and absence is
    not proof of non-applicability (it could equally be a package gap).
  * IFC — same shape. IFC is applicable to nearly every company reporting
    under s.143(3)(i), so "not_applicable" is rare and only set on an
    explicit statement in the package; anything else not found is
    "uncertain", never assumed not-applicable.
  * KAM — KAM is mandatory only for listed entities, and nothing in the
    current extractor schema establishes listed status (source spec's own
    `assignment_context.listed_status` resolver is not built — tracked as
    a further gap). So: "applicable" when a KAM section was actually
    found (obviously applicable then), otherwise always "uncertain" —
    deliberately never "not_applicable", because this module has no
    positive signal that would ever justify that conclusion.

IMPORTANT SCOPING NOTE — read before assuming this runs *before* extraction
------------------------------------------------------------------------------
Applicability here is resolved AFTER the CARO/IFC extractors have already
run, because the signals it uses (the extractor's own `caro_applicable`
field, whether CARO/IFC text was found at all) only exist once extraction
has happened. This module therefore cannot stop the extraction LLM calls
themselves from running on an inapplicable annexure — it gates what
DOWNSTREAM reasoning (coherence checks in tool_sar.py, consistency checks
in consistency_engine.py) is allowed to conclude from that extracted data.
That is a real, documented limitation relative to §22's diagram, which
depicts the gate upstream of clause execution — doing that would require
restructuring extraction into two sequential LLM calls (confirm
applicability, then conditionally extract clauses) and is left for a
future pass if the false-positive rate in practice justifies the extra
LLM round trip.
"""

from __future__ import annotations

import re
from typing import TypedDict

from sar_prod_v3.observation import Observation


class ApplicabilityResult(TypedDict):
    status: str  # "applicable" | "not_applicable" | "uncertain"
    basis: str


_CARO_NA_PATTERN = re.compile(
    r"(?i)(provisions of|requirements of)?\s*(the\s+)?"
    r"(companies\s+\(auditor[''s]?\s+report\)\s+order|caro)"
    r"[^.\n]{0,60}(not\s+applicable|do(es)?\s+not\s+apply|are\s+not\s+applicable)"
)

_IFC_NA_PATTERN = re.compile(
    r"(?i)(internal\s+financial\s+controls?)[^.\n]{0,80}"
    r"(not\s+applicable|not\s+required|is\s+not\s+applicable)"
    r"|not\s+required\s+to\s+report\s+under\s+section\s*143\s*\(\s*3\s*\)\s*\(\s*i\s*\)"
)


def resolve_caro_applicability(caro_json: dict, *, caro_text_found: bool, caro_raw_text: str) -> ApplicabilityResult:
    caro_json = caro_json or {}
    if _CARO_NA_PATTERN.search(caro_raw_text or ""):
        return {"status": "not_applicable", "basis": "The report states CARO 2020 is not applicable to the Company."}

    caro_applicable_field = caro_json.get("caro_applicable")
    if caro_applicable_field is False:
        return {
            "status": "not_applicable",
            "basis": "The CARO extraction step recorded caro_applicable=false from the annexure text.",
        }
    if caro_text_found:
        return {
            "status": "applicable",
            "basis": "A CARO 2020 annexure was identified and extracted from the supplied package.",
        }
    return {
        "status": "uncertain",
        "basis": (
            "No CARO 2020 annexure was identified in the supplied package, and no explicit "
            "non-applicability statement was found. Absence alone does not confirm CARO is "
            "inapplicable — it may instead be a gap in the supplied package."
        ),
    }


def resolve_ifc_applicability(ifc_json: dict, *, ifc_text_found: bool, ifc_raw_text: str) -> ApplicabilityResult:
    ifc_json = ifc_json or {}
    if _IFC_NA_PATTERN.search(ifc_raw_text or ""):
        return {
            "status": "not_applicable",
            "basis": "The report states reporting on Internal Financial Controls under s.143(3)(i) is not applicable.",
        }

    opinion_type = ((ifc_json.get("ifc_opinion") or {}).get("type") or "").lower()
    if ifc_text_found and opinion_type in {"unmodified", "qualified", "adverse", "disclaimer"}:
        return {"status": "applicable", "basis": f"An IFC opinion was identified: {opinion_type}."}
    return {
        "status": "uncertain",
        "basis": (
            "No IFC report/opinion was identified in the supplied package, and no explicit "
            "non-applicability statement was found. IFC reporting under s.143(3)(i) applies to "
            "most companies audited under the Companies Act 2013, so absence here more likely "
            "reflects an incomplete package than genuine non-applicability — treat as unresolved, "
            "not as confirmed non-applicability."
        ),
    }


def resolve_kam_applicability(main_json: dict) -> ApplicabilityResult:
    kam = (main_json or {}).get("key_audit_matters") or {}
    if kam.get("present"):
        return {"status": "applicable", "basis": "A Key Audit Matters section was identified in the report."}
    return {
        "status": "uncertain",
        "basis": (
            "No Key Audit Matters section was identified. KAM is mandatory only for listed "
            "entities, and listed status is not established from this package, so this must not "
            "be treated as a definitive missing-KAM finding."
        ),
    }


def resolve_applicability(
    merged_json: dict,
    *,
    caro_text_found: bool,
    caro_raw_text: str,
    ifc_text_found: bool,
    ifc_raw_text: str,
) -> dict[str, ApplicabilityResult]:
    """Single entry point the pipeline calls once per report. Returns
    {"caro": {...}, "ifc": {...}, "kam": {...}}, each an ApplicabilityResult.
    """
    return {
        "caro": resolve_caro_applicability(
            merged_json.get("CARO_2020", {}), caro_text_found=caro_text_found, caro_raw_text=caro_raw_text,
        ),
        "ifc": resolve_ifc_applicability(
            merged_json.get("IFC_REPORT", {}), ifc_text_found=ifc_text_found, ifc_raw_text=ifc_raw_text,
        ),
        "kam": resolve_kam_applicability(merged_json),
    }


_CHECK_IDS = {"caro": "APPL-CARO-01", "ifc": "APPL-IFC-01", "kam": "APPL-KAM-01"}
_COMPONENT_NAMES = {
    "caro": "CARO 2020 Applicability",
    "ifc": "Internal Financial Controls (s.143(3)(i)) Applicability",
    "kam": "Key Audit Matters Applicability",
}
_EVIDENCE_REQUIRED = [
    "Entity classification data for the assignment year (paid-up capital, turnover, borrowings, "
    "listed status) sufficient to confirm applicability against the relevant thresholds.",
]


def applicability_observations(applicability: dict[str, ApplicabilityResult]) -> list[Observation]:
    """One AUDIT_POINTER per area left "uncertain" — never a FINDING or
    RISK_FLAG, since an unresolved applicability question is by definition
    not something the supplied package alone can settle (source spec
    §19.1 / §22's own output for the "uncertain" branch)."""
    out: list[Observation] = []
    for area, result in applicability.items():
        if result["status"] != "uncertain":
            continue
        out.append(Observation(
            check_id=_CHECK_IDS[area],
            component=_COMPONENT_NAMES[area],
            tag="AUDIT_POINTER",
            risk_rating="Information request only",
            observation=result["basis"],
            evidence=result["basis"],
            evidence_required=list(_EVIDENCE_REQUIRED),
        ))
    return out
