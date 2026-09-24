"""
xbrl_trend_fetch.py — Multi-period database fetch routines for Block 5.

Performs targeted single round-trip queries against `as_db` for:
1. Multi-period numerical facts from `financial_facts` (has_dimensions = false)
2. Document metadata from `documents`

Follows identical architectural rules to `xbrl_fetch.py`:
- Strict `has_dimensions = false` enforcement to prevent segment/class double counting.
- Clean separation between DB fetching and domain analytics.
"""
from __future__ import annotations
from typing import Any

from . import xbrl_conn as DB
from .xbrl_trend_concepts import all_trend_concepts


_DOC_META_SQL = """
    SELECT doc_id, entity_cin, filename, fy_start, fy_end,
           raw_meta->>'company_name' AS company_name,
           fact_count, numeric_fact_count, disclosure_count
    FROM documents
    WHERE doc_id = %(doc_id)s
"""


def fetch_trend_meta(doc_id: str) -> dict[str, Any] | None:
    """Filing-level metadata: entity name, CIN, reporting period."""
    return DB.one(_DOC_META_SQL, {"doc_id": doc_id})


_SINGLE_DOC_FACTS_SQL = """
    SELECT concept_name, fy_start, fy_end, value_numeric, unit
    FROM financial_facts
    WHERE doc_id = %(doc_id)s
      AND has_dimensions = false
      AND concept_name = ANY(%(concepts)s)
    ORDER BY fy_end ASC, concept_name ASC
"""

_ENTITY_MULTI_DOC_FACTS_SQL = """
    SELECT f.concept_name, f.fy_start, f.fy_end, f.value_numeric, f.unit
    FROM financial_facts f
    JOIN documents d ON d.doc_id = f.doc_id
    WHERE d.entity_cin = %(entity_cin)s
      AND f.has_dimensions = false
      AND f.concept_name = ANY(%(concepts)s)
    ORDER BY f.fy_end ASC, f.concept_name ASC
"""


def fetch_trend_facts(doc_id: str, fetch_entity_history: bool = True) -> list[dict[str, Any]]:
    """Fetches multi-period financial facts for the given filing.
    
    If fetch_entity_history is True and the document has an entity_cin, facts across
    all filings of that entity are retrieved to maximize longitudinal series length
    (up to 3+ years). Otherwise, falls back to facts within this single filing.
    """
    concepts = list(all_trend_concepts())
    meta = fetch_trend_meta(doc_id)
    if meta and meta.get("entity_cin") and fetch_entity_history:
        try:
            facts = DB.query(
                _ENTITY_MULTI_DOC_FACTS_SQL,
                {"entity_cin": meta["entity_cin"], "concepts": concepts},
            )
            if facts:
                return facts
        except Exception:
            pass  # fallback to single doc

    return DB.query(_SINGLE_DOC_FACTS_SQL, {"doc_id": doc_id, "concepts": concepts})

