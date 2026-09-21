"""Tests for the priority_companies suggestion table's pure helpers
(backend/scripts/ingest_priority_companies.py) and its merge-on-write behavior
(backend/db.py::learn_priority_company) -- the table backing the upload
picker's optional Company Details fields' autocomplete.

`learn_priority_company` writes to the real DB, gated on the `db_available`
fixture same as every other LIVE-adjacent test in this package; test rows are
removed in a `finally` block so a failed run doesn't leave test pollution.
"""

import pytest

from modes.trial_balance.pipeline.db import db_cursor, learn_priority_company, list_priority_companies
from modes.trial_balance.scripts.ingest_priority_companies import _normalize_cin, _parse_financial_years


class TestNormalizeCin:
    def test_not_applicable_normalizes_to_none(self):
        assert _normalize_cin("Not Applicable") is None
        assert _normalize_cin("not applicable") is None
        assert _normalize_cin("  NOT APPLICABLE  ") is None

    def test_real_cin_passes_through(self):
        assert _normalize_cin("U85110KA1992GOI013570") == "U85110KA1992GOI013570"

    def test_blank_or_none_normalizes_to_none(self):
        assert _normalize_cin(None) is None
        assert _normalize_cin("") is None
        assert _normalize_cin("   ") is None


class TestParseFinancialYears:
    def test_splits_and_sorts_descending(self):
        assert _parse_financial_years("2023-24, 2024-25") == ["2024-25", "2023-24"]

    def test_single_year(self):
        assert _parse_financial_years("2024-25") == ["2024-25"]

    def test_blank_or_none_is_empty(self):
        assert _parse_financial_years(None) == []
        assert _parse_financial_years("") == []


def _delete_test_company(name: str) -> None:
    with db_cursor() as cur:
        cur.execute("DELETE FROM priority_companies WHERE company_name = %s", (name,))


class TestLearnPriorityCompany:
    def test_inserts_a_brand_new_company(self, db_available):
        if not db_available:
            pytest.skip("No reachable Postgres DB (settings.DB_*) -- skipping.")
        name = "Test Learn New Corp Unit"
        try:
            learn_priority_company(name, cin="U11111MH2020GOI111111", financial_year="2024-25")
            rows = {r["company_name"]: r for r in list_priority_companies()}
            assert name in rows
            assert rows[name]["cin"] == "U11111MH2020GOI111111"
            assert rows[name]["financial_years"] == ["2024-25"]
        finally:
            _delete_test_company(name)

    def test_appends_new_financial_year_without_dropping_existing_ones(self, db_available):
        if not db_available:
            pytest.skip("No reachable Postgres DB (settings.DB_*) -- skipping.")
        name = "Test Learn Merge Corp Unit"
        try:
            learn_priority_company(name, cin="U22222MH2020GOI222222", financial_year="2023-24")
            learn_priority_company(name, financial_year="2024-25")
            rows = {r["company_name"]: r for r in list_priority_companies()}
            assert set(rows[name]["financial_years"]) == {"2023-24", "2024-25"}
        finally:
            _delete_test_company(name)

    def test_never_downgrades_an_existing_cin(self, db_available):
        if not db_available:
            pytest.skip("No reachable Postgres DB (settings.DB_*) -- skipping.")
        name = "Test Learn Cin Protect Corp Unit"
        try:
            learn_priority_company(name, cin="U33333MH2020GOI333333")
            # A later call with a different cin must NOT overwrite the first.
            learn_priority_company(name, cin="U99999MH2020GOI999999")
            rows = {r["company_name"]: r for r in list_priority_companies()}
            assert rows[name]["cin"] == "U33333MH2020GOI333333"
        finally:
            _delete_test_company(name)

    def test_blank_company_name_is_a_no_op(self, db_available):
        if not db_available:
            pytest.skip("No reachable Postgres DB (settings.DB_*) -- skipping.")
        before = len(list_priority_companies())
        learn_priority_company("")
        learn_priority_company("   ")
        assert len(list_priority_companies()) == before
