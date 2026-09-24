"""
Block 1: Company Overview — entity identity, statement flavour and reporting
framework, read structurally from as_db (no LLM, no regex over prose, no fabricated
page numbers as_db does not carry).
"""
from __future__ import annotations
from typing import Any

from . import xbrl_overview_fetch as F
from .xbrl_entities import _filing_type
from .xbrl_overview_concepts import NAMESPACE_LABELS, IND_AS_NAMESPACE

HIGH, MEDIUM, LOW = "High", "Medium", "Low"

_FLAVOUR_MEANING = {
    "Standalone": "The entity's own figures, excluding subsidiaries and joint ventures.",
    "Consolidated": "Figures for the group as a whole, including subsidiaries and joint ventures.",
}


def _fy_short(fy_start, fy_end) -> str:
    if fy_end is None:
        return "unknown"
    start_year = fy_start.year if fy_start else fy_end.year - 1
    return f"FY{start_year}-{str(fy_end.year)[-2:]}"


def _entity_block(entity_cin: str, current_doc_id: str) -> dict[str, Any]:
    series = F.resolve_entity_series(current_doc_id)
    filings_read = len(series) or 1

    fy_ends = [row["fy_end"] for row in series if row.get("fy_end") is not None]
    distinct_years = len(set(fy_ends))
    comparable_years = max(0, distinct_years - 1)

    if series:
        period_label = _fy_short(series[0].get("fy_start"), series[0].get("fy_end"))
        if len(series) > 1:
            period_label = f"{_fy_short(series[0].get('fy_start'), series[0].get('fy_end'))} – " \
                            f"{_fy_short(series[-1].get('fy_start'), series[-1].get('fy_end'))}"
    else:
        period_label = "unknown"

    return {
        "filings_read": filings_read,
        "filings_label": f"{filings_read} filing{'s' if filings_read != 1 else ''}",
        "period_label": period_label,
        "comparable_years": comparable_years,
        "comparable_label": f"{comparable_years} comparable year{'s' if comparable_years != 1 else ''}",
    }


def _flavour_block(doc_id: str) -> dict[str, Any]:
    value = _filing_type(doc_id)
    return {
        "value": value or "Not stated in the filing's own identifier",
        "meaning": _FLAVOUR_MEANING.get(
            value,
            "The filing's own identifier does not state whether this is a standalone "
            "or consolidated statement; not inferred from anything else.",
        ),
    }


def _framework_block(doc_id: str) -> dict[str, Any]:
    namespaces = F.fetch_namespace_distribution(doc_id)
    total = sum(namespaces.values())
    ind_as_count = namespaces.get(IND_AS_NAMESPACE, 0)
    ind_as_share = (ind_as_count / total) if total else 0.0

    disclosure_text = F.fetch_framework_disclosure(doc_id)

    if total == 0:
        return {
            "state": "absent",
            "label": "Not determinable",
            "confidence": None,
            "detail": "No tagged facts were found for this filing to read a taxonomy "
                      "namespace from.",
            "evidence": None,
            "source": None,
        }

    label = NAMESPACE_LABELS.get(IND_AS_NAMESPACE, IND_AS_NAMESPACE) if ind_as_share > 0.5 \
        else "Framework not dominant in tagged facts"

    if disclosure_text:
        confidence = HIGH
        confidence_basis = "the filing's own Ind-AS-compliance disclosure"
    elif ind_as_share >= 0.9:
        confidence = MEDIUM
        confidence_basis = "the dominant XBRL taxonomy namespace across tagged facts"
    elif ind_as_share > 0.5:
        confidence = LOW
        confidence_basis = "a bare majority of tagged facts using the Ind AS namespace"
    else:
        confidence = LOW
        confidence_basis = "no dominant namespace and no compliance disclosure"

    detail = (
        f"{ind_as_share:.0%} of the {total:,} tagged facts in this filing use the Ind AS "
        f"XBRL taxonomy — read from financial_facts.namespace, not asserted."
    )

    return {
        "state": "read",
        "label": label,
        "confidence": confidence,
        "confidence_basis": f"Determined from {confidence_basis}.",
        "detail": detail,
        "evidence": disclosure_text,
        "source": {"doc_id": doc_id} if disclosure_text else None,
    }


def build_company_overview(doc_id: str, meta: dict[str, Any], company_name: str) -> dict[str, Any]:
    entity_cin = meta.get("entity_cin") or ""
    return {
        "doc_id": doc_id,
        "entity": {
            "name": company_name,
            **_entity_block(entity_cin, doc_id),
        },
        "flavour": _flavour_block(doc_id),
        "framework": _framework_block(doc_id),
    }
