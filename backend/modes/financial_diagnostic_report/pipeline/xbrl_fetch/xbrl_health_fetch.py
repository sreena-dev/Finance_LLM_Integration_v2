"""
xbrl_health_fetch.py — Database fetch routines for Block 4: Financial Health Summary.

Performs targeted queries against `as_db` for:
1. Grounded financial facts across comparative periods (has_dimensions = false)
2. Dimensional turnover shares (PercentageToTotalTurnoverOfCompany)
3. Qualitative disclosures: main products, NIC codes, ratio & realization notes
4. Document metadata (CIN, company name, reporting period)
"""
from __future__ import annotations
from typing import Any

from . import xbrl_conn as DB
from .xbrl_health_concepts import (
    all_health_numeric_concepts,
    all_health_disclosure_concepts,
    BUSINESS_NUMERIC_CONCEPTS,
)

_DOC_META_SQL = """
    SELECT doc_id, entity_cin, filename, fy_start, fy_end,
           raw_meta->>'company_name' AS company_name,
           fact_count, numeric_fact_count, disclosure_count
    FROM documents
    WHERE doc_id = %(doc_id)s
"""


def fetch_health_meta(doc_id: str) -> dict[str, Any] | None:
    """Filing metadata for entity identification."""
    return DB.one(_DOC_META_SQL, {"doc_id": doc_id})


_HEALTH_FACTS_SQL = """
    SELECT concept_name, fy_start, fy_end, value_numeric, unit, decimals
    FROM financial_facts
    WHERE doc_id = %(doc_id)s
      AND has_dimensions = false
      AND concept_name = ANY(%(concepts)s)
    ORDER BY fy_end DESC, concept_name ASC
"""


def fetch_health_facts(doc_id: str) -> list[dict[str, Any]]:
    """Grounded balance-sheet and P&L numeric facts."""
    return DB.query(_HEALTH_FACTS_SQL, {
        "doc_id": doc_id,
        "concepts": list(all_health_numeric_concepts()),
    })


_TURNOVER_FACTS_SQL = """
    SELECT concept_name, dimensions, dimension_name, value_numeric, unit
    FROM financial_facts
    WHERE doc_id = %(doc_id)s
      AND concept_name = ANY(%(concepts)s)
"""


def fetch_business_turnover_facts(doc_id: str) -> list[dict[str, Any]]:
    """Dimensional product/service turnover percentages."""
    return DB.query(_TURNOVER_FACTS_SQL, {
        "doc_id": doc_id,
        "concepts": list(BUSINESS_NUMERIC_CONCEPTS),
    })


_HEALTH_DISCLOSURES_SQL = """
    SELECT concept_name, section_title, fy_start, fy_end, text, disclosure_category
    FROM disclosures
    WHERE doc_id = %(doc_id)s
      AND concept_name = ANY(%(concepts)s)
"""


def fetch_health_disclosures(doc_id: str) -> list[dict[str, Any]]:
    """Qualitative disclosures for business activities, products, and notes."""
    return DB.query(_HEALTH_DISCLOSURES_SQL, {
        "doc_id": doc_id,
        "concepts": list(all_health_disclosure_concepts()),
    })

