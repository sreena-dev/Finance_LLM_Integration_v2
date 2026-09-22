"""Unit coverage for backend/valkey_client.py: preview store, failed-call guard
(per-key clearing), chat cache, agent chat storage backend, and graceful
degradation when Valkey is unreachable.

Uses the autouse `fake_valkey` fixture from tests/conftest.py for the
happy-path cases; the degradation cases construct their own unreachable
client so they exercise the real connection-error path, not fakeredis.
"""

from dataclasses import replace

import pytest

import modes.trial_balance.pipeline.valkey_client as vc

# Captured before any fixture monkeypatches vc.get_valkey_client (the autouse
# fake_valkey fixture in tests/conftest.py replaces it for every test) -- the
# degradation tests below restore this real implementation so they exercise
# an actual connection failure, not the fakeredis happy path.
_REAL_GET_VALKEY_CLIENT = vc.get_valkey_client


# ── Preview store ────────────────────────────────────────────────────────────

def test_preview_store_round_trip():
    vc.preview_store_set("tok1", {"file_path": "/x/y.xlsx", "filename": "y.xlsx"})
    assert vc.preview_store_get("tok1") == {"file_path": "/x/y.xlsx", "filename": "y.xlsx"}


def test_preview_store_missing_token_returns_none():
    assert vc.preview_store_get("does-not-exist") is None


def test_preview_store_update_merges_into_existing_entry():
    vc.preview_store_set("tok2", {"file_path": "/a.xlsx"})
    result = vc.preview_store_update("tok2", {"preview": {"ok": True}})
    assert result == {"file_path": "/a.xlsx", "preview": {"ok": True}}
    assert vc.preview_store_get("tok2") == {"file_path": "/a.xlsx", "preview": {"ok": True}}


def test_preview_store_update_on_missing_token_returns_none_and_writes_nothing():
    assert vc.preview_store_update("ghost", {"preview": {}}) is None
    assert vc.preview_store_get("ghost") is None


def test_preview_store_delete():
    vc.preview_store_set("tok3", {"a": 1})
    vc.preview_store_delete("tok3")
    assert vc.preview_store_get("tok3") is None


# ── Failed-call guard (per-key clearing) ────────────────────────────────────

def test_failed_calls_mark_and_check():
    assert vc.failed_calls_check("k1") is None
    vc.failed_calls_mark("k1", "boom")
    assert vc.failed_calls_check("k1") == "boom"


def test_failed_calls_clear_is_scoped_to_its_own_key():
    """The original in-process _FAILED_CALLS dict cleared its ENTIRE contents on
    any tool's success. This is the resolved behavior fix: clearing key A must
    never clear key B."""
    vc.failed_calls_mark("k_a", "a failed")
    vc.failed_calls_mark("k_b", "b failed")
    vc.failed_calls_clear("k_a")
    assert vc.failed_calls_check("k_a") is None
    assert vc.failed_calls_check("k_b") == "b failed"


# ── Chat cache ───────────────────────────────────────────────────────────────

def test_chat_cache_round_trip_with_complex_value():
    value = {"data": [{"gl_code": "1001", "closing_balance": 42.5}], "message": "ok"}
    key = vc.make_cache_key("chat_get_account_balance", "/tmp/canonical_tb.parquet", 123456, 789, "1001")
    assert vc.chat_cache_get(key) is None
    vc.chat_cache_set(key, value)
    assert vc.chat_cache_get(key) == value


def test_make_cache_key_changes_when_any_part_changes():
    """The core correctness property Phase 2 depends on: a fingerprint
    component (e.g. mtime_ns) change must change the key, so a regenerated
    artifact produces a fresh cache entry instead of stale data."""
    k1 = vc.make_cache_key("tool", "/path/file.parquet", 1000, 500, "argshash")
    k2 = vc.make_cache_key("tool", "/path/file.parquet", 2000, 500, "argshash")
    assert k1 != k2


# ── Agent chat storage backend ───────────────────────────────────────────────

def test_valkey_chat_storage_backend_round_trip():
    backend = vc.ValkeyChatStorageBackend()
    assert backend.load("sess-x") is None
    backend.save("sess-x", {"messages": [{"role": "user", "content": "hi"}]})
    assert backend.load("sess-x") == {"messages": [{"role": "user", "content": "hi"}]}


def test_valkey_chat_storage_backend_delete():
    backend = vc.ValkeyChatStorageBackend()
    backend.save("sess-y", {"messages": []})
    assert backend.delete("sess-y") is True
    assert backend.load("sess-y") is None


def test_valkey_chat_storage_backend_isolated_per_session():
    backend = vc.ValkeyChatStorageBackend()
    backend.save("sess-A", {"messages": ["a"]})
    backend.save("sess-B", {"messages": ["b"]})
    assert backend.load("sess-A") == {"messages": ["a"]}
    assert backend.load("sess-B") == {"messages": ["b"]}


# ── Graceful degradation (real unreachable connection, not fakeredis) ───────

@pytest.fixture
def unreachable_valkey(monkeypatch):
    """Point at a port nothing listens on and force a fresh pool, so every
    helper call below exercises a REAL redis.exceptions.ConnectionError, not
    the fakeredis happy path the other tests in this file use. Patches
    vc.settings directly (the name valkey_client.py imported at module load
    time), not backend.config.settings -- patching the latter wouldn't be
    visible to valkey_client, which already holds its own reference."""
    monkeypatch.setattr(vc, "get_valkey_client", _REAL_GET_VALKEY_CLIENT)
    monkeypatch.setattr(vc, "settings", replace(vc.settings, VALKEY_PORT=1))
    monkeypatch.setattr(vc, "_pool", None)
    yield
    monkeypatch.setattr(vc, "_pool", None)


def test_preview_store_degrades_to_none_when_valkey_unreachable(unreachable_valkey):
    assert vc.preview_store_get("anything") is None
    vc.preview_store_set("anything", {"a": 1})  # must not raise


def test_failed_calls_degrade_to_no_guard_when_valkey_unreachable(unreachable_valkey):
    assert vc.failed_calls_check("k") is None
    vc.failed_calls_mark("k", "boom")  # must not raise
    assert vc.failed_calls_check("k") is None  # never actually persisted


def test_chat_cache_degrades_to_miss_when_valkey_unreachable(unreachable_valkey):
    assert vc.chat_cache_get("anykey") is None
    vc.chat_cache_set("anykey", {"x": 1})  # must not raise


def test_get_valkey_client_returns_none_when_disabled(monkeypatch):
    monkeypatch.setattr(vc, "get_valkey_client", _REAL_GET_VALKEY_CLIENT)
    monkeypatch.setattr(vc, "settings", replace(vc.settings, VALKEY_ENABLED=False))
    monkeypatch.setattr(vc, "_pool", None)
    assert vc.get_valkey_client() is None
    assert vc.preview_store_get("x") is None  # still degrades cleanly, not an error


def test_connection_pool_passes_configured_password(monkeypatch):
    """A shared/team Valkey instance runs with --requirepass (see
    docker-compose.storage.yml) -- this is the one thing standing between
    'works on my laptop' and 'actually authenticates against the shared
    instance', so it gets its own explicit regression test rather than
    relying on the real-Valkey verification pass alone."""
    monkeypatch.setattr(vc, "settings", replace(vc.settings, VALKEY_PASSWORD="secret123"))
    monkeypatch.setattr(vc, "_pool", None)

    captured = {}
    real_pool_cls = vc.redis.ConnectionPool

    class _CapturingPool:
        def __new__(cls, **kwargs):
            captured.update(kwargs)
            return real_pool_cls(**kwargs)

    monkeypatch.setattr(vc.redis, "ConnectionPool", _CapturingPool)
    vc._get_pool()
    assert captured["password"] == "secret123"


def test_connection_pool_omits_password_when_blank(monkeypatch):
    """Blank VALKEY_PASSWORD (the local-dev default) must become `None`, not
    an empty string -- redis-py sends an AUTH command for any truthy
    password, and an unauthenticated local Valkey would reject that."""
    monkeypatch.setattr(vc, "settings", replace(vc.settings, VALKEY_PASSWORD=""))
    monkeypatch.setattr(vc, "_pool", None)

    captured = {}
    real_pool_cls = vc.redis.ConnectionPool

    class _CapturingPool:
        def __new__(cls, **kwargs):
            captured.update(kwargs)
            return real_pool_cls(**kwargs)

    monkeypatch.setattr(vc.redis, "ConnectionPool", _CapturingPool)
    vc._get_pool()
    assert captured["password"] is None
