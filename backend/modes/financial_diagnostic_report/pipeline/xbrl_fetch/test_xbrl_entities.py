"""
Hermetic tests for `xbrl_entities.list_entities()` — no DB, no network.
`xbrl_conn.query` is monkeypatched so these run against synthetic rows shaped
exactly like as_db's own columns.
"""
from __future__ import annotations
from datetime import date

from . import xbrl_conn as DB
from . import xbrl_entities as XE


def _row(doc_id: str, company_name: str, fy_start, fy_end, fact_rows: int = 5) -> dict:
    return {
        "doc_id": doc_id,
        "entity_cin": "U40101OR1995SGC003963",
        "company_name": company_name,
        "fy_start": fy_start,
        "fy_end": fy_end,
        "actual_fact_rows": fact_rows,
    }


def test_filing_type_detected_from_doc_id_text():
    assert XE._filing_type("...IND-AS Consolidated_BalanceSheet 09-05-2023.xml") == "Consolidated"
    assert XE._filing_type("...IND-AS Standalone_BalanceSheet 14-05-2023.xml") == "Standalone"
    assert XE._filing_type("U40101OR1995SGC003963_2021_2022") is None


def test_duplicate_company_and_fy_get_disambiguated_when_untyped(monkeypatch):
    rows = [
        _row("...Consolidated_BalanceSheet.xml", "ODISHA HYDRO", date(2021, 4, 1), date(2022, 3, 31)),
        _row("...Standalone_BalanceSheet.xml", "ODISHA HYDRO", date(2021, 4, 1), date(2022, 3, 31)),
        _row("U40101OR1995SGC003963_2021_2022", "ODISHA HYDRO", date(2021, 4, 1), date(2022, 3, 31)),
    ]
    monkeypatch.setattr(DB, "query", lambda sql: rows)

    entities = XE.list_entities()

    assert [e["filing_type"] for e in entities] == ["Consolidated", "Standalone", "filing 1"]
    # Every row is now distinguishable by (company_name, fy_label, filing_type).
    labels = {(e["company_name"], e["fy_label"], e["filing_type"]) for e in entities}
    assert len(labels) == 3


def test_unique_company_and_fy_gets_no_disambiguation_suffix(monkeypatch):
    rows = [_row("some_doc.xml", "SOLO ENTITY", date(2023, 4, 1), date(2024, 3, 31))]
    monkeypatch.setattr(DB, "query", lambda sql: rows)

    entities = XE.list_entities()

    assert entities[0]["filing_type"] is None
