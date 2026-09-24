"""
One round trip per document: every metric this registry needs, every period the
filing carries, in a single query. Measured at ~7ms for 26 concepts x 2 periods
against `as_db` — see `documentation/logic_documentation/bs_logic_spec/` for the
benchmark this number came from.

`has_dimensions = false` IS NOT AN OPTIMISATION — IT IS CORRECTNESS. 1.26M of the
1.83M rows in `financial_facts` are dimensioned (segment/related-party/PPE-class
breakdowns) and summing them alongside the undimensioned headline figure double-
counts it. Every fetch in this module filters on it explicitly rather than relying
on a caller to remember.
"""
from __future__ import annotations
from typing import Any

from . import xbrl_conn as DB
from .xbrl_concepts import all_concepts

_FETCH_SQL = """
    SELECT concept_name, fy_start, fy_end, value_numeric, unit
    FROM financial_facts
    WHERE doc_id = %(doc_id)s
      AND has_dimensions = false
      AND concept_name = ANY(%(concepts)s)
"""


def fetch_doc_metrics(doc_id: str) -> list[dict[str, Any]]:
    """Every row this registry's concepts touch for one filing. Empty list, never None,
    when the doc has no matching facts (a known-broken filing, or an unknown doc_id)."""
    return DB.query(_FETCH_SQL, {"doc_id": doc_id, "concepts": list(all_concepts())})


_DOC_META_SQL = """
    SELECT doc_id, entity_cin, filename, fy_start, fy_end,
           raw_meta->>'company_name' AS company_name,
           fact_count, numeric_fact_count, disclosure_count
    FROM documents
    WHERE doc_id = %(doc_id)s
"""


def fetch_doc_meta(doc_id: str) -> dict[str, Any] | None:
    """Filing-level metadata: entity name, CIN, reporting period. None if doc_id is
    not in as_db at all — distinct from a doc that exists but has zero facts."""
    return DB.one(_DOC_META_SQL, {"doc_id": doc_id})
