"""
The entity/filing picker list — one row per ingested filing, for the UI to choose from.

Deliberately filing-level (`doc_id`), not entity-level: `as_db` carries 48 CINs with
more than one filing (same-year standalone/consolidated pairs, verified in the prior
session — none of them extend history to a second distinct year), so collapsing to one
row per CIN would hide a real choice the reviewer needs to make.
"""
from __future__ import annotations
import re
from typing import Any

from . import xbrl_conn as DB

# as_db has no dedicated standalone/consolidated column — the only place that
# distinction shows up is in some `doc_id`s' own filename text (e.g.
# "...IND-AS Consolidated_BalanceSheet..."). Detected here, not stored,
# because it is read straight off the one field that actually carries it.
_FILING_TYPE = re.compile(r"consolidated|standalone", re.IGNORECASE)


def _filing_type(doc_id: str) -> str | None:
    m = _FILING_TYPE.search(doc_id)
    return m.group(0).capitalize() if m else None

# `usable` is deliberately NOT `documents.fact_count > 0`. `fact_count` is the
# filing's OWN parse-time metadata — how many facts the XBRL instance was expected
# to yield — and the 8 known-broken filings in as_db still carry a large, nonzero
# `fact_count` (1,600-6,250) despite `financial_facts` holding zero actual rows for
# them (see xbrl_ingestion_integrity_check.md): the parse succeeded, the persist
# step to `financial_facts` did not. So usability is read from `financial_facts`
# itself — a LEFT JOIN against an aggregate, one query, not one query per document.
_LIST_SQL = """
    SELECT d.doc_id, d.entity_cin, d.raw_meta->>'company_name' AS company_name,
           d.fy_start, d.fy_end, COALESCE(f.n, 0) AS actual_fact_rows
    FROM documents d
    LEFT JOIN (
        SELECT doc_id, COUNT(*) AS n
        FROM financial_facts
        WHERE has_dimensions = false
        GROUP BY doc_id
    ) f ON f.doc_id = d.doc_id
    ORDER BY company_name NULLS LAST, d.fy_end DESC
"""


def list_entities() -> list[dict[str, Any]]:
    """Every filing in as_db. Filings with zero actual `financial_facts` rows (8,
    corpus-wide) are still listed rather than hidden — a reviewer choosing an
    entity should see it exists and why its dashboard would be empty, not have it
    silently disappear from the picker.

    Same company + same FY can legitimately appear more than once — a
    standalone and a consolidated filing for the same year are two different
    documents, not a duplicate (see this module's own docstring) — and a
    reviewer picking between rows that read identically cannot tell which is
    which. `filing_type` carries whatever the doc_id itself says
    (Standalone/Consolidated); it is `None` when the doc_id says neither,
    which is itself worth showing rather than guessing at.
    """
    rows = DB.query(_LIST_SQL)
    entities = [
        {
            "doc_id": r["doc_id"],
            "entity_cin": r["entity_cin"],
            "company_name": r["company_name"] or r["entity_cin"],
            "fy_label": f"FY{r['fy_start'].year if r['fy_start'] else r['fy_end'].year - 1}"
                        f"-{str(r['fy_end'].year)[-2:]}" if r["fy_end"] else "unknown",
            "filing_type": _filing_type(r["doc_id"]),
            "usable": r["actual_fact_rows"] > 0,
        }
        for r in rows
    ]

    # A filing_type of None still reads identically to a sibling row when one
    # exists (e.g. the third, unlabelled ODISHA HYDRO POWER doc_id alongside
    # its "Standalone" twin) — falling back to the doc_id itself is the only
    # thing guaranteed to differ between any two rows in this list.
    seen: dict[tuple[str, str], int] = {}
    for e in entities:
        key = (e["company_name"], e["fy_label"])
        seen[key] = seen.get(key, 0) + 1
    counts: dict[tuple[str, str], int] = {}
    for e in entities:
        key = (e["company_name"], e["fy_label"])
        if seen[key] > 1 and not e["filing_type"]:
            counts[key] = counts.get(key, 0) + 1
            e["filing_type"] = f"filing {counts[key]}"

    return entities
