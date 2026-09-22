"""The durable half of the upload store.

These are the only tests here that need a real database, and they SKIP rather
than pass when one is not configured -- a persistence test that silently
passes without a database is worse than no test, because it reports the
property it exists to prove without ever checking it.

Every row written uses a user_id namespaced to this run and is deleted in
teardown, so running against a live `finance_llm` cannot collide with, read,
or disturb anyone's real uploads.

WHAT THESE PIN DOWN
-------------------
The property that did not exist before: an extraction survives the Redis cache
expiring. The source PDF is dropped when the ingestion job ends, so before
this, an expired cache meant the user had to re-upload and pay for a full
re-conversion. Test `test_a_document_survives_the_cache_disappearing` is that
property, directly.

Run with::

    cd backend
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 venv/Scripts/python -m pytest \
        modes/financial_statement/upload/test_pgstore.py -q
"""

from __future__ import annotations

import os
import sys
import uuid

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_MODE = os.path.dirname(_HERE)
_BACKEND = os.path.dirname(os.path.dirname(_MODE))
for _path in (os.path.join(_MODE, "pipeline"), _BACKEND):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from modes.financial_statement.upload import pgstore  # noqa: E402
from modes.financial_statement.upload import schema as schema_mod  # noqa: E402
from modes.financial_statement.upload import store as store_mod  # noqa: E402
from modes.financial_statement.upload.store import UploadedDocument  # noqa: E402


def _database_available() -> bool:
    try:
        from app.auth.db import dsn, ping
        if not dsn():
            return False
        reachable, _ = ping()
        return reachable
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not _database_available(),
    reason="no platform database configured (set FINANCE_DSN) — persistence "
           "is skipped rather than silently reported as passing",
)


@pytest.fixture
def owner():
    """A user id unique to this run, with every row removed afterwards."""
    user_id = f"test-{uuid.uuid4()}"
    yield user_id
    with _cursor() as cur:
        cur.execute(
            "DELETE FROM public.financial_statement_live_ingestion "
            "WHERE user_id = %s",
            (user_id,),
        )


def _cursor():
    from app.auth.db import db_cursor
    return db_cursor(dict_rows=False)


def _doc(user_id, conversation_id="c1", doc_id="up_a", company="Startup Odisha",
         financial_year="2023-24", with_children=True):
    tables, texts, pages = [], [], []
    if with_children:
        tables = [{
            "table_id": "t1",
            "table_title": "Balance Sheet as at 31st March, 2024",
            "table_description": "Share capital; Reserves and surplus",
            "table_md": "| Particulars | 2024 | 2023 |\n| --- | --- | --- |\n"
                        "| Share capital | 15,00,000 | 15,00,000 |",
            "page_ocr_start": 1,
            "page_ocr_end": 1,
            "financial_stmt_type": "balance_sheet",
            "unit": "INR",
            "currency": "INR",
            # The list/dict shapes that have to survive jsonb intact.
            "note_refs": ["3", "4"],
            "is_financial": True,
            "bbox": [10.5, 20.25, 300.0, 400.75],
            "confidence": 0.93,
            "vlm_agreement": 0.98,
            "findings": [{"row_label": "Trade payables", "reasons": ["readers_disagree"]}],
            "footings": [{"subtotal_label": "Total", "passed": True}],
            "source_file": "SO_2023-24_SFS.pdf",
        }]
        texts = [{
            "chunk_id": "x1",
            "content": "Notes to the standalone financial statements.",
            "page_ocr_start": 2,
            "chunk_type": "heading",
            "section": "Notes",
            "section_breadcrumb": ["Notes to the standalone financial statements"],
            "note_refs": [],
            "source_file": "SO_2023-24_SFS.pdf",
        }]
        pages = [{
            "page_no": 1,
            "image_jpeg_b64": "aGVsbG8=",
            "width_px": 1654,
            "height_px": 2338,
        }]

    return UploadedDocument(
        doc_id=doc_id,
        user_id=user_id,
        conversation_id=conversation_id,
        filename="SO_2023-24_SFS.pdf",
        document={"fy_start": 2023, "fy_end": 2024, "sha256": "a" * 64,
                  "total_pages_pdf": 22},
        identification={"entity_name": company, "financial_year": financial_year,
                        "framework": "Ind AS", "fy_confidence": "high"},
        quality={"grade": "good", "low_grade": "fair", "unreadable_cells": [],
                 "failed_footings": [], "vlm_used": True},
        tables=tables,
        texts=texts,
        pages=pages,
        uploaded_at=1700000000.0,
    )


# --------------------------------------------------------------------------
# Round trip
# --------------------------------------------------------------------------

def test_the_schema_is_idempotent():
    """Called on every save, so it must be free to repeat."""
    schema_mod.ensure_schema(force=True)
    schema_mod.ensure_schema(force=True)
    assert set(schema_mod.existing_tables()) == set(schema_mod.TABLES)


def test_a_document_round_trips_field_for_field(owner):
    original = _doc(owner)
    pgstore.save(original, retention_days=30)

    loaded = pgstore.load_one(owner, "c1", "up_a")

    assert loaded is not None
    assert loaded.doc_id == original.doc_id
    assert loaded.filename == original.filename
    assert loaded.company == original.company
    assert loaded.financial_year == original.financial_year
    assert loaded.document == original.document
    assert loaded.identification == original.identification
    assert loaded.quality == original.quality
    assert loaded.uploaded_at == original.uploaded_at


def test_the_markdown_the_tools_actually_read_survives(owner):
    """`table_md` is the interchange format the whole check library reads. If
    only one field survived, it would have to be this one."""
    pgstore.save(_doc(owner), retention_days=30)

    loaded = pgstore.load_one(owner, "c1", "up_a")

    assert loaded.tables[0]["table_md"].startswith("| Particulars | 2024 | 2023 |")
    assert loaded.tables[0]["financial_stmt_type"] == "balance_sheet"


def test_list_and_dict_shapes_survive_jsonb_rather_than_becoming_strings(owner):
    """A list stored as its repr comes back as a string that still looks
    plausible in a log and breaks the first caller that indexes it."""
    pgstore.save(_doc(owner), retention_days=30)

    table = pgstore.load_one(owner, "c1", "up_a").tables[0]
    text = pgstore.load_one(owner, "c1", "up_a").texts[0]

    assert table["note_refs"] == ["3", "4"]
    assert table["bbox"] == [10.5, 20.25, 300.0, 400.75]
    assert table["findings"][0]["reasons"] == ["readers_disagree"]
    assert table["footings"][0]["passed"] is True
    assert text["section_breadcrumb"] == ["Notes to the standalone financial statements"]


def test_children_are_all_present(owner):
    pgstore.save(_doc(owner), retention_days=30)

    loaded = pgstore.load_one(owner, "c1", "up_a")

    assert len(loaded.tables) == 1
    assert len(loaded.texts) == 1
    assert len(loaded.pages) == 1
    assert loaded.pages[0]["image_jpeg_b64"] == "aGVsbG8="


def test_a_document_with_no_children_does_not_break(owner):
    pgstore.save(_doc(owner, with_children=False), retention_days=30)

    loaded = pgstore.load_one(owner, "c1", "up_a")

    assert loaded is not None
    assert loaded.tables == [] and loaded.texts == [] and loaded.pages == []


def test_resaving_replaces_rather_than_duplicates(owner):
    """doc_id is content-addressed, so the same file re-uploaded into the same
    conversation is exactly the same document -- and a re-ingestion after a
    pipeline improvement should supersede, not double."""
    pgstore.save(_doc(owner), retention_days=30)
    pgstore.save(_doc(owner), retention_days=30)

    assert len(pgstore.load(owner, "c1")) == 1
    assert len(pgstore.load_one(owner, "c1", "up_a").tables) == 1


# --------------------------------------------------------------------------
# Scoping -- the access control, not a convenience
# --------------------------------------------------------------------------

def test_another_users_document_is_invisible(owner):
    other = f"test-{uuid.uuid4()}"
    pgstore.save(_doc(owner), retention_days=30)
    try:
        assert pgstore.load(other, "c1") == []
        assert pgstore.load_one(other, "c1", "up_a") is None
    finally:
        pgstore.drop_conversation(other, "c1")


def test_another_conversation_of_the_same_user_is_invisible(owner):
    pgstore.save(_doc(owner, conversation_id="c1"), retention_days=30)

    assert pgstore.load(owner, "c2") == []


def test_the_same_pdf_uploaded_by_two_users_coexists(owner):
    """doc_id is `up_<sha256(pdf)[:16]>`, so two users uploading the same
    filing produce the SAME doc_id. As a primary key that would collide;
    the key is the (user, conversation, doc) triple."""
    other = f"test-{uuid.uuid4()}"
    pgstore.save(_doc(owner, doc_id="up_same"), retention_days=30)
    pgstore.save(_doc(other, doc_id="up_same"), retention_days=30)
    try:
        assert pgstore.load_one(owner, "c1", "up_same") is not None
        assert pgstore.load_one(other, "c1", "up_same") is not None
    finally:
        pgstore.drop_conversation(other, "c1")


# --------------------------------------------------------------------------
# Retention and deletion
# --------------------------------------------------------------------------

def test_an_expired_document_is_not_served_even_before_the_sweep_runs(owner):
    """Expiry is enforced by the query, not by the sweep having happened."""
    pgstore.save(_doc(owner), retention_days=30)
    with _cursor() as cur:
        cur.execute(
            "UPDATE public.financial_statement_live_ingestion "
            "SET expires_at = now() - interval '1 day' WHERE user_id = %s",
            (owner,),
        )

    assert pgstore.load(owner, "c1") == []
    assert pgstore.load_one(owner, "c1", "up_a") is None


def test_the_sweep_removes_expired_rows_and_their_children(owner):
    pgstore.save(_doc(owner), retention_days=30)
    with _cursor() as cur:
        cur.execute(
            "UPDATE public.financial_statement_live_ingestion "
            "SET expires_at = now() - interval '1 day' WHERE user_id = %s",
            (owner,),
        )

    pgstore.sweep_expired()

    with _cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM public.financial_statement_live_ingestion "
            "WHERE user_id = %s", (owner,),
        )
        assert cur.fetchone()[0] == 0
        # The cascade has to take the children with it, or the tables table
        # grows without bound and nothing ever points at the orphans.
        cur.execute(
            "SELECT count(*) FROM public.financial_statement_live_ingestion_tables t "
            "WHERE NOT EXISTS (SELECT 1 FROM public.financial_statement_live_ingestion p "
            "                  WHERE p.ingestion_id = t.ingestion_id)"
        )
        assert cur.fetchone()[0] == 0, "orphaned child rows left behind"


def test_the_sweep_leaves_live_rows_alone(owner):
    pgstore.save(_doc(owner), retention_days=30)

    pgstore.sweep_expired()

    assert pgstore.load_one(owner, "c1", "up_a") is not None


def test_deleting_one_document_leaves_the_others(owner):
    pgstore.save(_doc(owner, doc_id="up_a"), retention_days=30)
    pgstore.save(_doc(owner, doc_id="up_b"), retention_days=30)

    assert pgstore.delete(owner, "c1", "up_a") == 1

    remaining = pgstore.load(owner, "c1")
    assert [d.doc_id for d in remaining] == ["up_b"]


def test_dropping_a_conversation_removes_all_of_it(owner):
    pgstore.save(_doc(owner, doc_id="up_a"), retention_days=30)
    pgstore.save(_doc(owner, doc_id="up_b"), retention_days=30)

    assert pgstore.drop_conversation(owner, "c1") == 2
    assert pgstore.load(owner, "c1") == []


# --------------------------------------------------------------------------
# The property this whole workstream exists for
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# update_table_cell -- the narrow single-cell write path
# --------------------------------------------------------------------------

def test_update_table_cell_rewrites_table_md_and_quality_only(owner):
    pgstore.save(_doc(owner), retention_days=30)
    before = pgstore.load_one(owner, "c1", "up_a")
    expires_before = before.uploaded_at  # sanity: same object we compare below

    def mutate(table_md, quality):
        assert table_md.startswith("| Particulars | 2024 | 2023 |")
        new_md = table_md + "\n| Cash | 5,000 [user-entered] | - |"
        new_quality = dict(quality)
        new_quality["user_edits"] = [{"table": "t1", "value": "5000"}]
        return new_md, new_quality, {"cell": "5,000 [user-entered]"}

    new_md, new_quality, result = pgstore.update_table_cell(
        owner, "c1", "up_a", "t1", mutate,
    )

    assert result == {"cell": "5,000 [user-entered]"}
    assert new_md.endswith("5,000 [user-entered] | - |")
    assert new_quality["user_edits"] == [{"table": "t1", "value": "5000"}]

    reloaded = pgstore.load_one(owner, "c1", "up_a")
    assert reloaded.tables[0]["table_md"] == new_md
    assert reloaded.quality["user_edits"] == [{"table": "t1", "value": "5000"}]
    # Nothing else about the table changed.
    assert reloaded.tables[0]["financial_stmt_type"] == "balance_sheet"
    # uploaded_at (the parent-row field UI shows for retention) is untouched --
    # an edit must not read as a fresh upload.
    assert reloaded.uploaded_at == expires_before


def test_update_table_cell_returns_none_for_an_unknown_document(owner):
    def mutate(table_md, quality):  # pragma: no cover -- must not be called
        raise AssertionError("mutate must not run when the document is missing")

    assert pgstore.update_table_cell(owner, "c1", "up_missing", "t1", mutate) is None


def test_update_table_cell_returns_none_for_an_unknown_table(owner):
    pgstore.save(_doc(owner), retention_days=30)

    def mutate(table_md, quality):  # pragma: no cover -- must not be called
        raise AssertionError("mutate must not run when the table is missing")

    assert pgstore.update_table_cell(owner, "c1", "up_a", "no-such-table", mutate) is None


def test_update_table_cell_reraises_mutate_errors_and_writes_nothing(owner):
    from modes.financial_statement.upload.edits import EditError

    pgstore.save(_doc(owner), retention_days=30)
    before = pgstore.load_one(owner, "c1", "up_a")

    def mutate(table_md, quality):
        raise EditError(409, "not_editable", "nope")

    with pytest.raises(EditError):
        pgstore.update_table_cell(owner, "c1", "up_a", "t1", mutate)

    after = pgstore.load_one(owner, "c1", "up_a")
    assert after.tables[0]["table_md"] == before.tables[0]["table_md"]
    assert after.quality == before.quality


def test_update_table_cell_does_not_extend_expiry(owner):
    """A `SELECT ... FOR UPDATE` plus two `UPDATE`s -- never an INSERT with a
    fresh `expires_at`. Confirmed by re-saving with a short retention first,
    editing, and checking the row is still due to expire on the original
    schedule rather than 30 days from the edit."""
    pgstore.save(_doc(owner), retention_days=1)
    with _cursor() as cur:
        cur.execute(
            "SELECT expires_at FROM public.financial_statement_live_ingestion "
            "WHERE user_id = %s AND doc_id = %s",
            (owner, "up_a"),
        )
        before = cur.fetchone()[0]

    pgstore.update_table_cell(
        owner, "c1", "up_a", "t1",
        lambda md, q: (md, q, None),
    )

    with _cursor() as cur:
        cur.execute(
            "SELECT expires_at FROM public.financial_statement_live_ingestion "
            "WHERE user_id = %s AND doc_id = %s",
            (owner, "up_a"),
        )
        after = cur.fetchone()[0]

    assert after == before


def test_a_document_survives_the_cache_disappearing(owner, monkeypatch):
    """THE point. Before this, an expired or flushed Redis meant the
    extraction was gone for good -- the source PDF is dropped when the
    ingestion job ends, so the only way back was re-uploading and paying for
    a full re-conversion.

    Persistence is re-enabled here deliberately: conftest turns it off for
    every other test in this directory so they need no database.
    """
    import fakeredis

    monkeypatch.setattr(store_mod, "PERSIST_TO_POSTGRES", True)
    store_mod.STORE._client = fakeredis.FakeRedis(decode_responses=True)

    store_mod.STORE.put(_doc(owner))
    assert len(store_mod.STORE.list(owner, "c1")) == 1

    # The cache goes away entirely -- a restart, an eviction, a TTL lapse.
    store_mod.STORE._client = fakeredis.FakeRedis(decode_responses=True)

    recovered = store_mod.STORE.list(owner, "c1")

    assert len(recovered) == 1, "the extraction did not survive the cache"
    assert recovered[0].tables[0]["table_md"].startswith("| Particulars")
    # And it is hot again, so the next read costs nothing.
    assert store_mod.STORE._client.exists(
        store_mod._doc_key(owner, "c1", "up_a")
    )
