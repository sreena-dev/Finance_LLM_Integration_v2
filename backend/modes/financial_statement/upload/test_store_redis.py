"""Behaviour that has no equivalent in the old dict-backed store's tests.

`test_bridge.py`/`test_narrative.py`/`test_query_route.py`/`test_page_routes.py`
already cover the store's *contract* (put/get/list/delete/drop_conversation,
scoping by user and conversation) against whatever backs it, and pass
unchanged against the Redis-backed store via `conftest.py`'s fake client.
What's new here is Redis-specific: TTL refresh, per-user cap eviction
(including the self-heal path for a ZSET member whose content already
expired), and the one behaviour genuinely at risk in a JSON-round-tripped
store — that the lazily-built vector index survives across the many
separately-deserialized `UploadedDocument` instances one conversation
produces.

Run with::

    cd backend
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 venv/Scripts/python -m pytest \
        modes/financial_statement/upload/test_store_redis.py -q
"""

from __future__ import annotations

import json
import os
import sys
import time
from unittest.mock import patch

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_MODE = os.path.dirname(_HERE)
_BACKEND = os.path.dirname(os.path.dirname(_MODE))
for path in (os.path.join(_MODE, "pipeline"), _BACKEND):
    if path not in sys.path:
        sys.path.insert(0, path)

from modes.financial_statement.upload import store  # noqa: E402


def _doc(user_id: str, conversation_id: str, doc_id: str, **kwargs) -> store.UploadedDocument:
    defaults = dict(
        filename=f"{doc_id}.pdf", document={}, identification={}, quality={},
    )
    defaults.update(kwargs)
    return store.UploadedDocument(
        doc_id=doc_id, user_id=user_id, conversation_id=conversation_id, **defaults
    )


# ---------------------------------------------------------------------------
# TTL
# ---------------------------------------------------------------------------

def test_put_applies_a_ttl_to_every_key_it_writes(_fake_redis):
    store.STORE.put(_doc("u1", "c1", "up_a"))
    assert _fake_redis.ttl(store._doc_key("u1", "c1", "up_a")) > 0
    assert _fake_redis.ttl(store._conv_set_key("u1", "c1")) > 0


def test_touch_refreshes_every_document_in_the_conversation(_fake_redis):
    """One conversation's documents live and die on the same clock -- Redis
    expires individual keys, not conversation buckets, so touch() has to
    refresh every document key it holds, not just the conversation set."""
    store.STORE.put(_doc("u1", "c1", "up_a"))
    store.STORE.put(_doc("u1", "c1", "up_b"))

    key_a = store._doc_key("u1", "c1", "up_a")
    key_b = store._doc_key("u1", "c1", "up_b")
    _fake_redis.expire(key_a, 5)  # simulate the conversation having gone idle
    _fake_redis.expire(key_b, 5)

    store.STORE.touch("u1", "c1")
    assert _fake_redis.ttl(key_a) > 5
    assert _fake_redis.ttl(key_b) > 5


def test_touch_on_an_unknown_conversation_is_a_no_op(_fake_redis):
    """No documents, nothing to refresh -- must not raise."""
    store.STORE.touch("u1", "c-does-not-exist")


# ---------------------------------------------------------------------------
# Per-user cap eviction
# ---------------------------------------------------------------------------

def test_oldest_documents_are_evicted_first_past_the_cap(_fake_redis):
    now = time.time()
    for i in range(store.MAX_DOCS_PER_USER + 5):
        store.STORE.put(_doc("u1", "c1", f"up_{i}", uploaded_at=now + i))

    remaining = store.STORE.list("u1", "c1")
    assert len(remaining) == store.MAX_DOCS_PER_USER
    # The survivors are exactly the newest MAX_DOCS_PER_USER uploads.
    kept_ids = {d.doc_id for d in remaining}
    assert kept_ids == {f"up_{i}" for i in range(5, store.MAX_DOCS_PER_USER + 5)}


def test_cap_is_tracked_across_conversations_for_the_same_user(_fake_redis):
    """The cap is per USER, not per conversation -- a user spread across many
    conversations still only gets MAX_DOCS_PER_USER resident documents."""
    now = time.time()
    for i in range(store.MAX_DOCS_PER_USER + 3):
        store.STORE.put(_doc("u1", f"c{i}", f"up_{i}", uploaded_at=now + i))

    total = sum(
        len(store.STORE.list("u1", f"c{i}"))
        for i in range(store.MAX_DOCS_PER_USER + 3)
    )
    assert total == store.MAX_DOCS_PER_USER


def test_a_stale_cap_entry_self_heals_without_counting_toward_eviction(_fake_redis):
    """A ZSET member whose content key already expired natively is cleaned up
    as a side effect of the next eviction pass, but must not count toward
    `over` -- it wasn't occupying real room, so sweeping it must not let an
    otherwise-evictable real document survive."""
    now = time.time()
    for i in range(store.MAX_DOCS_PER_USER):
        store.STORE.put(_doc("u1", "c1", f"up_{i}", uploaded_at=now + i))

    # Simulate native TTL expiry on the oldest survivor's content key, without
    # touching the ZSET -- exactly what Redis itself would leave behind.
    oldest_member = _fake_redis.zrange(store._user_cap_key("u1"), 0, 0)[0]
    conv_id, doc_id = json.loads(oldest_member)
    _fake_redis.delete(store._doc_key("u1", conv_id, doc_id))

    # One more upload pushes the user over the cap again, forcing another pass.
    store.STORE.put(_doc("u1", "c1", "up_trigger", uploaded_at=now + 100))

    zcard = _fake_redis.zcard(store._user_cap_key("u1"))
    assert zcard <= store.MAX_DOCS_PER_USER
    assert _fake_redis.zscore(store._user_cap_key("u1"), oldest_member) is None


def test_list_self_heals_a_doc_id_whose_content_already_expired(_fake_redis):
    """Mirrors the cap-side self-heal, but for `list()`'s own secondary index
    -- the conversation SET is not itself Redis-expired, so a member whose
    content key vanished must be swept on read, not returned as a gap."""
    store.STORE.put(_doc("u1", "c1", "up_a"))
    _fake_redis.delete(store._doc_key("u1", "c1", "up_a"))

    assert store.STORE.list("u1", "c1") == []
    assert not _fake_redis.sismember(store._conv_set_key("u1", "c1"), "up_a")


# ---------------------------------------------------------------------------
# The in-process vector-index cache
# ---------------------------------------------------------------------------

def test_ensure_index_returns_the_same_object_across_separate_reads(_fake_redis):
    """The one behaviour genuinely at risk in a JSON-round-tripped store:
    get()/list() deserialize a FRESH UploadedDocument every call, so without
    the doc_id-keyed _index_cache, ensure_index() would rebuild (re-embed)
    the same document's index on every single tool call within a
    conversation -- a real latency and cost regression versus the old
    dict store, which handed back the same live object every time."""
    store.STORE.put(_doc("u1", "c1", "up_a", texts=[
        {"chunk_id": "x1", "page_ocr_start": 1, "content": "hello"},
    ]))

    with patch("modes.financial_statement.upload.embeddings.DocumentIndex") as fake_index:
        fake_index.side_effect = lambda: object()

        first = store.STORE.get("u1", "c1", "up_a")
        second = store.STORE.get("u1", "c1", "up_a")
        assert first is not second, "expected two separately-deserialized instances"

        idx1 = first.ensure_index()
        idx2 = second.ensure_index()
        assert idx1 is idx2, "expected the SAME index object via _index_cache"
        assert fake_index.call_count == 1, "expected exactly one build, not one per read"


def test_delete_evicts_the_index_cache_entry(_fake_redis):
    store.STORE.put(_doc("u1", "c1", "up_a"))
    with patch("modes.financial_statement.upload.embeddings.DocumentIndex") as fake_index:
        fake_index.side_effect = lambda: object()
        store.STORE.get("u1", "c1", "up_a").ensure_index()
    assert "up_a" in store._index_cache

    store.STORE.delete("u1", "c1", "up_a")
    assert "up_a" not in store._index_cache


def test_drop_conversation_evicts_every_member_s_index_cache_entry(_fake_redis):
    store.STORE.put(_doc("u1", "c1", "up_a"))
    store.STORE.put(_doc("u1", "c1", "up_b"))
    with patch("modes.financial_statement.upload.embeddings.DocumentIndex") as fake_index:
        fake_index.side_effect = lambda: object()
        store.STORE.get("u1", "c1", "up_a").ensure_index()
        store.STORE.get("u1", "c1", "up_b").ensure_index()
    assert {"up_a", "up_b"} <= store._index_cache.keys()

    store.STORE.drop_conversation("u1", "c1")
    assert "up_a" not in store._index_cache
    assert "up_b" not in store._index_cache


# ---------------------------------------------------------------------------
# update_cell -- Redis-only mode (Postgres is off for this whole directory,
# see conftest.py's `_no_postgres`), so this exercises `_update_cell_redis_only`
# -- the WATCH/MULTI path a real single-copy store needs.
# ---------------------------------------------------------------------------

def _table_doc(user_id, conv, doc_id, table_md):
    return _doc(user_id, conv, doc_id, tables=[{"table_id": "t1", "table_md": table_md}])


def test_update_cell_rewrites_table_md_and_quality_keeping_the_ttl(_fake_redis):
    store.STORE.put(_table_doc("u1", "c1", "up_a", "| A | B |\n| --- | --- |\n| 1 | 2 |"))
    key = store._doc_key("u1", "c1", "up_a")
    _fake_redis.expire(key, 5000)

    def mutate(table_md, quality):
        return table_md.replace("2", "2 [user-entered]"), {**quality, "user_edits": [1]}, "ok"

    result = store.STORE.update_cell("u1", "c1", "up_a", "t1", mutate)

    assert result == "ok"
    reloaded = store.STORE.get("u1", "c1", "up_a")
    assert reloaded.tables[0]["table_md"].endswith("2 [user-entered] |")
    assert reloaded.quality["user_edits"] == [1]
    # KEEPTTL: the edit did not reset -- and must not have extended -- the
    # cache's remaining lifetime.
    assert 0 < _fake_redis.ttl(key) <= 5000


def test_update_cell_returns_none_for_a_missing_document(_fake_redis):
    def mutate(table_md, quality):  # pragma: no cover
        raise AssertionError("must not run")

    assert store.STORE.update_cell("u1", "c1", "up_missing", "t1", mutate) is None


def test_update_cell_returns_none_for_a_missing_table(_fake_redis):
    store.STORE.put(_table_doc("u1", "c1", "up_a", "| A |\n| --- |\n| 1 |"))

    def mutate(table_md, quality):  # pragma: no cover
        raise AssertionError("must not run")

    assert store.STORE.update_cell("u1", "c1", "up_a", "no-such-table", mutate) is None


def test_update_cell_reraises_mutate_errors_and_writes_nothing(_fake_redis):
    from modes.financial_statement.upload.edits import EditError

    original_md = "| A |\n| --- |\n| 1 |"
    store.STORE.put(_table_doc("u1", "c1", "up_a", original_md))

    def mutate(table_md, quality):
        raise EditError(409, "not_editable", "nope")

    with pytest.raises(EditError):
        store.STORE.update_cell("u1", "c1", "up_a", "t1", mutate)

    assert store.STORE.get("u1", "c1", "up_a").tables[0]["table_md"] == original_md
