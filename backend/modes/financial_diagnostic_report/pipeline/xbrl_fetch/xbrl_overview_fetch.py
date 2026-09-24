"""
Fetch routines for Block 1: Company Overview.

Read-only queries against as_db, in the same single-round-trip-per-concern style as
the rest of this package. Entity-series resolution is NOT duplicated here — it reuses
`xbrl_signal_fetch.resolve_entity_series`, which already does the doc_id -> entity_cin
-> sibling-filings join this block also needs, so the two callers can never disagree
about how many filings an entity has.
"""
from __future__ import annotations
from typing import Any

from . import xbrl_conn as DB
from .xbrl_signal_fetch import resolve_entity_series
from .xbrl_overview_concepts import FRAMEWORK_DISCLOSURE_CONCEPT

__all__ = ["resolve_entity_series", "fetch_namespace_distribution", "fetch_framework_disclosure"]


_NAMESPACE_SQL = """
    SELECT namespace, COUNT(*) AS n
    FROM financial_facts
    WHERE doc_id = %(doc_id)s
    GROUP BY namespace
"""


def fetch_namespace_distribution(doc_id: str) -> dict[str, int]:
    """{namespace: fact_count} for this filing — the structural basis for the
    Reporting Framework row's label and confidence."""
    rows = DB.query(_NAMESPACE_SQL, {"doc_id": doc_id})
    return {r["namespace"]: r["n"] for r in rows if r.get("namespace")}


_FRAMEWORK_DISCLOSURE_SQL = """
    SELECT text
    FROM disclosures
    WHERE doc_id = %(doc_id)s AND concept_name = %(concept)s
    LIMIT 1
"""


def fetch_framework_disclosure(doc_id: str) -> str | None:
    """The entity's own Ind-AS-compliance statement, verbatim, if this filing has
    one — the "read from the filing" quote for the Reporting Framework row."""
    row = DB.one(_FRAMEWORK_DISCLOSURE_SQL, {"doc_id": doc_id, "concept": FRAMEWORK_DISCLOSURE_CONCEPT})
    text = (row or {}).get("text")
    return text.strip() if text else None
