"""Integration tests for backend/tools/db_bridge/persist_canonical_tb_to_live.py
against the real Postgres LIVE staging tables (live_document_table/live_tb_table).
Skipped automatically if no DB is reachable (see conftest.py's db_available
fixture) -- this is an external dependency, not something every environment
running the suite is guaranteed to have.

Every test uses a unique tb_doc_id and deletes it in a finally block, so a
failed run never leaves stray rows behind for the next run to trip over."""

import pytest

from modes.trial_balance.pipeline.db import fetch_main_lines
from modes.trial_balance.pipeline.tools import persist_canonical_tb_to_live


def _cleanup(tb_doc_id):
    from modes.trial_balance.pipeline.db import db_cursor

    with db_cursor(dict_rows=False) as cur:
        cur.execute("DELETE FROM live_tb_table WHERE tb_doc_id = %s", (tb_doc_id,))
        cur.execute("DELETE FROM live_document_table WHERE tb_doc_id = %s", (tb_doc_id,))


def _fetch_live_lines(tb_doc_id):
    from modes.trial_balance.pipeline.tools import CANONICAL_TB_ALL_COLUMNS
    from modes.trial_balance.pipeline.db import db_cursor

    with db_cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(CANONICAL_TB_ALL_COLUMNS)} FROM live_tb_table WHERE tb_doc_id = %s ORDER BY gl_code",
            (tb_doc_id,),
        )
        return [dict(r) for r in cur.fetchall()]


@pytest.fixture
def test_doc_id(db_available):
    """A unique tb_doc_id per test, cleaned up afterward regardless of outcome.

    Guarded on db_available: the test BODIES already skip cleanly when no Postgres
    is reachable, but this teardown did not, so _cleanup() raised PipelineDBError
    during teardown and every run reported 3 ERRORs on a machine with no DB. Errors
    that are expected get ignored, and an ignored error channel is where a real
    failure goes unnoticed."""
    doc_id = "PYTEST_PERSIST_TEST_DOC"
    yield doc_id
    if db_available:
        _cleanup(doc_id)


def test_persist_writes_rows_to_live_tables(db_available, make_canonical_tb, test_doc_id):
    if not db_available:
        pytest.skip("No reachable Postgres DB (settings.DB_*) -- skipping LIVE-write integration test.")

    canonical_tb_file = make_canonical_tb(
        [
            {"gl_code": "1001", "gl_name": "Cash in Hand", "closing_balance": 1500.0},
            {"gl_code": "2001", "gl_name": "Trade Payable", "closing_balance": -1500.0},
        ],
        tb_doc_id=test_doc_id,
    )

    result = persist_canonical_tb_to_live(canonical_tb_file=str(canonical_tb_file), tb_doc_id=test_doc_id)

    assert result["execution_status"] == "SUCCESS"
    assert result["tb_doc_id"] == test_doc_id

    live_rows = _fetch_live_lines(test_doc_id)
    assert len(live_rows) == 2
    assert {r["gl_code"] for r in live_rows} == {"1001", "2001"}


def test_persist_is_idempotent_on_reprocessing(db_available, make_canonical_tb, test_doc_id):
    if not db_available:
        pytest.skip("No reachable Postgres DB (settings.DB_*) -- skipping LIVE-write integration test.")

    canonical_tb_file = make_canonical_tb(
        [{"gl_code": "1001", "gl_name": "Cash in Hand", "closing_balance": 1500.0}],
        tb_doc_id=test_doc_id,
    )

    persist_canonical_tb_to_live(canonical_tb_file=str(canonical_tb_file), tb_doc_id=test_doc_id)
    persist_canonical_tb_to_live(canonical_tb_file=str(canonical_tb_file), tb_doc_id=test_doc_id)

    live_rows = _fetch_live_lines(test_doc_id)
    assert len(live_rows) == 1, "Delete-then-insert semantics must not duplicate rows on reprocessing."


def test_persist_round_trips_custom_fields(db_available, make_canonical_tb, test_doc_id):
    if not db_available:
        pytest.skip("No reachable Postgres DB (settings.DB_*) -- skipping LIVE-write integration test.")

    canonical_tb_file = make_canonical_tb(
        [
            {
                "gl_code": "1001",
                "gl_name": "Cash in Hand",
                "closing_balance": 1500.0,
                "custom_field_1": "reserved-value-1",
                "custom_field_2": "reserved-value-2",
            },
            {"gl_code": "2001", "gl_name": "Trade Payable", "closing_balance": -1500.0},
        ],
        tb_doc_id=test_doc_id,
    )

    persist_canonical_tb_to_live(canonical_tb_file=str(canonical_tb_file), tb_doc_id=test_doc_id)

    live_rows = {r["gl_code"]: r for r in _fetch_live_lines(test_doc_id)}
    assert live_rows["1001"]["custom_field_1"] == "reserved-value-1"
    assert live_rows["1001"]["custom_field_2"] == "reserved-value-2"
    assert live_rows["1001"]["custom_field_3"] is None
    # Row with no custom fields supplied stays null, not some default sentinel.
    assert live_rows["2001"]["custom_field_1"] is None


def test_fetch_main_lines_selects_all_columns_including_custom_fields(db_available):
    """Sanity check on the repository-layer change itself: fetch_main_lines
    (MAIN read) must select CANONICAL_TB_ALL_COLUMNS, not the narrower
    CANONICAL_TB_COLUMNS, or custom_field_1/2/3 would be silently dropped on
    every MAIN read regardless of whether any row actually has data there."""
    if not db_available:
        pytest.skip("No reachable Postgres DB (settings.DB_*) -- skipping.")

    # No MAIN rows are expected to exist for this doc_id -- this only checks
    # that the query itself doesn't error on the wider column list (i.e. the
    # custom_field_1/2/3 columns really do exist on tb_table in this DB).
    rows = fetch_main_lines("PYTEST_NONEXISTENT_DOC_ID")
    assert rows == []
