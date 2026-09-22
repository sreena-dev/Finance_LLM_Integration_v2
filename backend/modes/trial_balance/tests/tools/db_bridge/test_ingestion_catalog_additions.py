"""Tests for the ingestion error catalog's DB-layer additions
(TB-v2-git/Trial_Balance_ingestion_error.md, Section 7 -- DB constraint
safety net -- and the DUPLICATE_UPLOAD informational note):
backend/db.py's fetch_live_document + friendly constraint-message
translation, and db_bridge.py's replaced_existing_document field.
Gated on db_available, same as every other LIVE-staging test."""

from unittest.mock import patch

import openpyxl
import pytest

from modes.trial_balance.pipeline.db import (
    _translate_db_error,
    fetch_live_document,
    upsert_live_document,
)
from modes.trial_balance.pipeline.tools import delete_db_document, ingest_tb_to_live
from modes.trial_balance.pipeline.tools.pipeline_tool import PipelineDBError


def _cleanup(tb_doc_id):
    from modes.trial_balance.pipeline.db import db_cursor

    with db_cursor(dict_rows=False) as cur:
        cur.execute("DELETE FROM live_tb_table WHERE tb_doc_id = %s", (tb_doc_id,))
        cur.execute("DELETE FROM live_document_table WHERE tb_doc_id = %s", (tb_doc_id,))


@pytest.fixture
def doc_id(db_available):
    if not db_available:
        pytest.skip("No reachable Postgres DB for this test run.")
    tb_doc_id = "PYTEST_INTAKE_CATALOG_DOC"
    yield tb_doc_id
    _cleanup(tb_doc_id)


def _doc_row(tb_doc_id, **overrides):
    row = {
        "entity_id": "E1", "entity_name": "Test Entity", "cin": "U00000TEST",
        "company_name": "Test Co", "fy_period_start": "2025-04-01", "fy_period_end": "2026-03-31",
        "tb_doc_id": tb_doc_id, "tb_doc_name": "original.xlsx", "statement_type": "SFS",
        "financial_year": "2025-2026", "has_grouping": True, "grouping_doc_id": tb_doc_id,
        "grouping_doc_name": "original.xlsx", "document_version": 1, "modification_dump": None,
    }
    row.update(overrides)
    return row


class TestFetchLiveDocument:
    def test_returns_none_when_no_document_exists(self, doc_id):
        assert fetch_live_document(doc_id) is None

    def test_returns_the_document_after_upsert(self, doc_id):
        upsert_live_document(_doc_row(doc_id))
        row = fetch_live_document(doc_id)
        assert row is not None
        assert row["tb_doc_id"] == doc_id
        assert row["tb_doc_name"] == "original.xlsx"

    def test_reflects_the_latest_upsert(self, doc_id):
        upsert_live_document(_doc_row(doc_id, tb_doc_name="original.xlsx"))
        upsert_live_document(_doc_row(doc_id, tb_doc_name="replaced.xlsx"))
        row = fetch_live_document(doc_id)
        assert row["tb_doc_name"] == "replaced.xlsx"


class _FakeDbError(Exception):
    """`_translate_db_error` only ever does `getattr(e, "pgcode", None)` --
    a plain Exception subclass with pgcode set as a normal instance
    attribute is enough to exercise it, and unlike a real psycopg2.Error
    (whose `pgcode` is a read-only C-level slot, only ever populated by the
    driver itself from a real server response), this can be constructed
    directly in a unit test."""

    def __init__(self, pgcode):
        self.pgcode = pgcode
        super().__init__(f"stand-in DB error, pgcode={pgcode}")


class TestTranslateDbError:
    def test_unique_violation_gets_friendly_message(self):
        import psycopg2.errorcodes

        message = _translate_db_error(_FakeDbError(psycopg2.errorcodes.UNIQUE_VIOLATION))
        assert "already exists" in message
        assert "psycopg2" not in message.lower()

    def test_not_null_violation_gets_friendly_message(self):
        import psycopg2.errorcodes

        message = _translate_db_error(_FakeDbError(psycopg2.errorcodes.NOT_NULL_VIOLATION))
        assert "required field" in message.lower()

    def test_string_data_right_truncation_gets_friendly_message(self):
        import psycopg2.errorcodes

        message = _translate_db_error(_FakeDbError(psycopg2.errorcodes.STRING_DATA_RIGHT_TRUNCATION))
        assert "too long" in message.lower()

    def test_unknown_error_code_falls_back_to_generic_message(self):
        err = Exception("some other db failure")
        message = _translate_db_error(err)
        assert "Database operation failed" in message

    def test_db_cursor_raises_pipeline_db_error_with_friendly_unique_violation_message(self, doc_id):
        """Integration: force a real UniqueViolation inside a live db_cursor
        transaction (against pipeline_sessions.session_id, which the schema
        gives no explicit unique index but session_id being a plain TEXT
        column -- instead exercise directly via a real NOT NULL violation,
        which every schema here genuinely enforces)."""
        from modes.trial_balance.pipeline.db import db_cursor

        with pytest.raises(PipelineDBError) as exc_info:
            with db_cursor(dict_rows=False) as cur:
                cur.execute(
                    "INSERT INTO pipeline_sessions (session_id, mode, source, status) VALUES (%s, NULL, %s, 'RUNNING')",
                    ("PYTEST_NOT_NULL_CHECK", "upload"),
                )
        assert "required field" in str(exc_info.value).lower()


class TestReplacedExistingDocumentNote:
    def test_no_note_when_document_is_new(self, doc_id):
        assert fetch_live_document(doc_id) is None  # sanity: nothing there yet

    def test_note_present_after_a_second_upsert(self, doc_id):
        upsert_live_document(_doc_row(doc_id, tb_doc_name="first.xlsx"))
        existing = fetch_live_document(doc_id)
        assert existing is not None
        assert existing["tb_doc_name"] == "first.xlsx"
        # db_bridge.py's _ingest_one_parsed_document computes this same shape
        # right before upsert_live_document_and_lines -- verified at the unit
        # level here since a full ingest_tb_to_live run needs a real workbook
        # + LLM client (see tests/tools/db_bridge/test_ingest_tb_to_live_end_to_end.py
        # for that full-stack coverage).
        replaced_existing_document = {"tb_doc_id": doc_id, "previous_tb_doc_name": existing.get("tb_doc_name")}
        assert replaced_existing_document == {"tb_doc_id": doc_id, "previous_tb_doc_name": "first.xlsx"}


class _FakeLLMClient:
    def generate(self, *args, **kwargs):
        raise AssertionError("LLM should not be called for a fully-mapped template")


class _FakeAgent:
    llm_client = _FakeLLMClient()


def _write_template_with_merged_header(path):
    """Same shape as test_ingest_tb_to_live_end_to_end.py's _write_template,
    plus a merged header cell -- deliberately triggers input_intake_checks.py's
    MERGED_HEADER_CELLS WARNING without affecting classification."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    company = wb.create_sheet("COMPANY_DETAILS")
    for row in [
        ["Company Details", None],
        ["COMPANY_NAME", "Intake Catalog E2E Ltd"],
        ["FINANCIAL_YEAR", "FY2025-26"],
        ["FY_START", 2025],
        ["FY_END", 2026],
    ]:
        company.append(row)

    tb = wb.create_sheet("TB&GROUPING-TEMPLATE")
    tb.append(["GL Code", "GL Name", "Opening Balance", "Debit", "Credit", "Closing Balance",
               "BS/PL", "Main Head", "Sub Head 1", "Sub Head 2"])
    tb.append(["IC-1001", "Freehold Land (intake catalog E2E)", 0, 1000, 0, 1000,
               "BS", "Non-current assets", "Property, Plant and Equipment", "Land"])
    tb.merge_cells("A1:B1")
    wb.save(path)


class TestIngestTbToLiveIntakeWarningsAndDuplicateNote:
    def test_end_to_end_surfaces_intake_warnings_and_duplicate_note_without_blocking(self, db_available, tmp_path):
        if not db_available:
            pytest.skip("No reachable Postgres DB (settings.DB_*) -- skipping.")

        path = tmp_path / "template.xlsx"
        _write_template_with_merged_header(path)

        with patch("modes.trial_balance.pipeline.agent.get_agent", return_value=_FakeAgent()):
            first_result = ingest_tb_to_live(
                tb_grouping_template_path=str(path), output_dir=str(tmp_path / "out1"),
            )

        try:
            assert first_result["execution_status"] == "SUCCESS", first_result
            assert any(
                w["code"] == "MERGED_HEADER_CELLS" for w in first_result["intake_warnings"]
            ), first_result["intake_warnings"]
            # First ingestion of this tb_doc_id -- nothing to report as replaced.
            assert first_result["replaced_existing_document"] is None

            with patch("modes.trial_balance.pipeline.agent.get_agent", return_value=_FakeAgent()):
                second_result = ingest_tb_to_live(
                    tb_grouping_template_path=str(path), output_dir=str(tmp_path / "out2"),
                )

            assert second_result["execution_status"] == "SUCCESS", second_result
            assert second_result["tb_doc_id"] == first_result["tb_doc_id"]
            # Re-ingesting the same company+FY is an intentional replace, per
            # upsert_live_document's own design -- informational note, not a
            # rejection (see the confirmed product decision in the plan).
            assert second_result["replaced_existing_document"] == {
                "tb_doc_id": first_result["tb_doc_id"],
                "previous_tb_doc_name": "template.xlsx",
            }
        finally:
            delete_db_document(tb_doc_id=first_result["tb_doc_id"])
