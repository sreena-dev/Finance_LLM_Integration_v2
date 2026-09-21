"""Tests for backend/tools/db_bridge.py's pure helpers and its LIVE-staging tools.

`_normalized_rows_to_canonical_dicts` is the boundary between the native
classification engine's NormalizedRow output and the 16-column canonical
shape every downstream tool expects. Template/grouping-file parsing itself
(what used to be `_fy_period_date`/`_parse_company_details`/
`_template_rows_to_tb_rows_and_hints`) now lives in the ported input layer
(backend/tools/document_metadata.py, input_scenario_a.py, and friends) --
see tests/tools/test_input_dispatch.py for end-to-end coverage of that, and
tests/tools/test_document_metadata.py for the company-details/date-parsing
unit coverage that used to live here.

The tools that write to LIVE staging are gated on the `db_available`
fixture and skip cleanly when no Postgres is reachable.
"""

import json

import pytest

from modes.trial_balance.pipeline.tools.db_bridge import _normalized_rows_to_canonical_dicts
from modes.trial_balance.pipeline.tools.tb_models import MAPPED, RowTrace, TBRow


class TestNormalizedRowsToCanonicalDicts:
    """Projects the classification engine's NormalizedRow output onto the
    existing 16-column canonical shape every downstream tool (validate_layer1_tb,
    build_data_sufficiency_grade, ...) already expects -- unchanged by this
    engine swap."""

    def test_projects_all_16_canonical_columns(self):
        from modes.trial_balance.pipeline.tools.canonical_schema import CANONICAL_TB_ALL_COLUMNS
        from modes.trial_balance.pipeline.tools.tb_models import NormalizedRow

        tb_row = TBRow(gl_code="2001", gl_name="Freehold Land", opening=0.0, debit=100.0, credit=0.0, closing=100.0)

        row = NormalizedRow.from_tb_row(
            tb_row, bs_pl="BS", main_head="Non-current assets", sub_head_1="Property, Plant and Equipment",
            sub_head_2="Land", account_type="Asset", mapped_status=MAPPED,
            trace=RowTrace(resolved_at_step="step2_direct"),
        )

        out = _normalized_rows_to_canonical_dicts("DOC1", [row])
        assert len(out) == 1
        assert set(out[0].keys()) == set(CANONICAL_TB_ALL_COLUMNS)
        assert out[0]["tb_doc_id"] == "DOC1"
        assert out[0]["gl_code"] == "2001"
        assert out[0]["mapped_status"] == "MAPPED"
        assert out[0]["custom_field_1"] is None


# ── LIVE-staging tools (DB-gated) ───────────────────────────────────────────


class TestDeleteDbDocument:
    def test_deleting_an_unknown_document_succeeds_and_reports_no_row_removed(self, db_available):
        """Idempotent by design: deleting something that is not there is not an error,
        but the response must say plainly that nothing was removed."""
        if not db_available:
            pytest.skip("No reachable Postgres DB (settings.DB_*) -- skipping.")

        from modes.trial_balance.pipeline.tools import delete_db_document

        result = delete_db_document(tb_doc_id="NO_SUCH_DOC_FOR_TESTS_12345")
        assert result["execution_status"] == "SUCCESS"
        assert "document row removed: False" in result["message"]

    def test_delete_never_touches_main_tables(self, db_available):
        """delete_db_document is scoped to LIVE staging only. MAIN
        (document_table/tb_table) is the audited source of record and must be
        unreachable from this tool -- asserted against the shipped SQL itself."""
        import inspect

        from modes.trial_balance.pipeline.tools import delete_db_document

        source = inspect.getsource(delete_db_document.__wrapped__)
        assert "live_tb_table" in source
        assert "live_document_table" in source
        # No un-prefixed MAIN table in any DELETE.
        assert "FROM tb_table" not in source
        assert "FROM document_table" not in source


class TestListDbDocuments:
    def test_lists_documents_and_reports_a_count(self, db_available):
        if not db_available:
            pytest.skip("No reachable Postgres DB (settings.DB_*) -- skipping.")

        from modes.trial_balance.pipeline.tools import list_db_documents

        result = list_db_documents()
        assert result["execution_status"] == "SUCCESS"
        assert isinstance(result["documents"], list)
        assert "document(s)" in result["message"]

    def test_filtering_by_an_unknown_entity_returns_an_empty_list_not_an_error(self, db_available):
        if not db_available:
            pytest.skip("No reachable Postgres DB (settings.DB_*) -- skipping.")

        from modes.trial_balance.pipeline.tools import list_db_documents

        result = list_db_documents(entity_id="NO_SUCH_ENTITY_FOR_TESTS_12345")
        assert result["execution_status"] == "SUCCESS"
        assert result["documents"] == []


class TestLoadTbFromDb:
    def test_unknown_doc_id_fails_with_a_clear_message(self, db_available, tmp_path):
        if not db_available:
            pytest.skip("No reachable Postgres DB (settings.DB_*) -- skipping.")

        from modes.trial_balance.pipeline.tools import load_tb_from_db

        result = load_tb_from_db(tb_doc_id="NO_SUCH_DOC_FOR_TESTS_12345", output_dir=str(tmp_path))
        assert result["execution_status"] == "FAILED"
        assert "NO_SUCH_DOC_FOR_TESTS_12345" in result["message"]
        # A missing document is an expected condition, not a crash.
        assert "Traceback" not in json.dumps(result)
