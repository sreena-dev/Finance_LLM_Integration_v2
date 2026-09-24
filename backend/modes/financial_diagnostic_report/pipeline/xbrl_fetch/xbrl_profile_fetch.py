"""
Fetch routines for Block 3: Business Profile.

Performs targeted single round-trip queries against `as_db` for:
1. Grounded numerical metrics from `financial_facts` (has_dimensions = false)
2. Narrative and explanatory text blocks from `disclosures`
3. Filing metadata from `documents`
"""
from __future__ import annotations
from typing import Any

from . import xbrl_conn as DB
from .xbrl_profile_concepts import (
    all_profile_numeric_concepts,
    all_profile_disclosure_concepts,
)

_NUMERIC_FETCH_SQL = """
    SELECT concept_name, fy_start, fy_end, value_numeric, unit
    FROM financial_facts
    WHERE doc_id = %(doc_id)s
      AND has_dimensions = false
      AND concept_name = ANY(%(concepts)s)
"""


def fetch_profile_metrics(doc_id: str) -> list[dict[str, Any]]:
    """Fetches numerical facts needed to ground the business profile."""
    return DB.query(
        _NUMERIC_FETCH_SQL,
        {"doc_id": doc_id, "concepts": list(all_profile_numeric_concepts())}
    )


_DISCLOSURE_FETCH_SQL = """
    SELECT concept_name, section_title, fy_start, fy_end, text, disclosure_category
    FROM disclosures
    WHERE doc_id = %(doc_id)s
      AND concept_name = ANY(%(concepts)s)
"""


def fetch_profile_disclosures(doc_id: str) -> list[dict[str, Any]]:
    """Fetches qualitative text blocks from disclosures table for the business profile."""
    return DB.query(
        _DISCLOSURE_FETCH_SQL,
        {"doc_id": doc_id, "concepts": list(all_profile_disclosure_concepts())}
    )


_DOC_META_SQL = """
    SELECT doc_id, entity_cin, filename, fy_start, fy_end,
           raw_meta->>'company_name' AS company_name
    FROM documents
    WHERE doc_id = %(doc_id)s
"""


def fetch_profile_meta(doc_id: str) -> dict[str, Any] | None:
    """Filing-level metadata for entity identification."""
    return DB.one(_DOC_META_SQL, {"doc_id": doc_id})

