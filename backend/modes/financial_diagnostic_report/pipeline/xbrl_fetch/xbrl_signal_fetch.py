"""
Fetch routines for the S01-S27 signal library.

Deliberately does NOT edit `xbrl_risk_fetch.py` (Block 6's live production fetch) — this
is a new, additive entry point built for what this library specifically needs and that
file does not provide:

  1. a genuine multi-year series per ENTITY (`entity_cin`), not just the single `doc_id`
     passed in — resolved by joining `documents` on `entity_cin` and pulling every
     sibling filing's facts, so `xbrl_signal_rules.py` can tell "only one year exists"
     (an honest ABSTAIN) from "three years exist and they don't move" (a real NOT_FIRED);
  2. a dimensioned-fact path for the one signal (S08) confirmed to need it, filtered to
     the exact axis named in `xbrl_signal_derivations.py` — never a blanket
     `has_dimensions = true` scan.

Read-only, same connection discipline as the rest of this package (`xbrl_conn.py`
enforces `SET default_transaction_read_only = on` at the session level).
"""
from __future__ import annotations
from typing import Any

from . import xbrl_conn as DB
from . import xbrl_signal_registry as R
from . import xbrl_signal_derivations as D


_ENTITY_FOR_DOC_SQL = """
    SELECT entity_cin FROM documents WHERE doc_id = %(doc_id)s
"""

_SIBLING_DOCS_SQL = """
    SELECT doc_id, fy_start, fy_end
    FROM documents
    WHERE entity_cin = %(entity_cin)s
    ORDER BY fy_end ASC
"""

_FACE_FACTS_SQL = """
    SELECT doc_id, concept_name, fy_start, fy_end, value_numeric, unit
    FROM financial_facts
    WHERE doc_id = ANY(%(doc_ids)s)
      AND has_dimensions = false
      AND concept_name = ANY(%(concepts)s)
"""

_DIMENSIONED_FACTS_SQL = """
    SELECT doc_id, concept_name, fy_start, fy_end, value_numeric, unit,
           dimensions ->> %(axis)s AS member
    FROM financial_facts
    WHERE doc_id = ANY(%(doc_ids)s)
      AND has_dimensions = true
      AND concept_name = ANY(%(concepts)s)
      AND dimensions ? %(axis)s
"""

_DISCLOSURES_SQL = """
    SELECT doc_id, concept_name, section_title, fy_start, fy_end, text, disclosure_category
    FROM disclosures
    WHERE doc_id = ANY(%(doc_ids)s)
      AND concept_name = ANY(%(concepts)s)
"""


def resolve_entity_series(doc_id: str) -> list[dict[str, Any]]:
    """Every filing (`doc_id`, `fy_start`, `fy_end`) for the entity `doc_id` belongs to,
    oldest first. Length 1 for the overwhelming majority of as_db entities today — that
    is read as ground truth by the rules layer, never padded or assumed longer."""
    row = DB.one(_ENTITY_FOR_DOC_SQL, {"doc_id": doc_id})
    if row is None or not row.get("entity_cin"):
        return []
    return DB.query(_SIBLING_DOCS_SQL, {"entity_cin": row["entity_cin"]})


def fetch_face_fact_series(doc_ids: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
    """Undimensioned numeric facts for every concept the registry needs, across every
    filing in `doc_ids`. Shape: {concept_name: {fy_end_iso: {value, doc_id, unit}}}."""
    concepts = list(R.all_required_numeric_concepts())
    rows = DB.query(_FACE_FACTS_SQL, {"doc_ids": doc_ids, "concepts": concepts})
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows:
        if r.get("value_numeric") is None or r.get("fy_end") is None:
            continue
        cname = r["concept_name"]
        fy_key = str(r["fy_end"])
        out.setdefault(cname, {})[fy_key] = {
            "value": float(r["value_numeric"]),
            "doc_id": r["doc_id"],
            "unit": r.get("unit"),
        }
    return out


def fetch_related_party_receivables(doc_ids: list[str]) -> dict[str, float]:
    """S08 only — AmountsReceivableRelatedPartyTransactions, summed per doc_id over the
    confirmed `RelatedParty` axis. Returns {doc_id: total}."""
    deriv = D.get("S08")
    if not deriv.dimensioned or not deriv.axis:
        return {}
    rows = DB.query(_DIMENSIONED_FACTS_SQL, {
        "doc_ids": doc_ids,
        "concepts": ["AmountsReceivableRelatedPartyTransactions"],
        "axis": deriv.axis,
    })
    out: dict[str, float] = {}
    for r in rows:
        v = r.get("value_numeric")
        if v is None:
            continue
        out[r["doc_id"]] = out.get(r["doc_id"], 0.0) + float(v)
    return out


def fetch_disclosure_series(doc_ids: list[str]) -> list[dict[str, Any]]:
    concepts = list(R.all_required_disclosure_concepts())
    if not concepts:
        return []
    return DB.query(_DISCLOSURES_SQL, {"doc_ids": doc_ids, "concepts": concepts})


def fetch_all(doc_id: str) -> dict[str, Any]:
    """Single entry point the engine calls: resolves the entity's whole filing series,
    then pulls every fact/disclosure the registry needs across it in three round trips."""
    series = resolve_entity_series(doc_id)
    doc_ids = [row["doc_id"] for row in series] or [doc_id]
    return {
        "doc_id": doc_id,
        "series": series,                                   # ordered oldest -> newest
        "face_facts": fetch_face_fact_series(doc_ids),
        "related_party_receivables": fetch_related_party_receivables(doc_ids),
        "disclosures": fetch_disclosure_series(doc_ids),
    }
