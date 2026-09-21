"""Hot-state / cache / coordination plane (Valkey, RESP-compatible with redis-py).

Mirrors backend/db.py's shape: one module-level connection pool, small typed
helpers per use case, never raw client calls at call sites. Every helper
degrades gracefully -- a Valkey outage must never fail a tool call or an API
request, it only means whatever this call was caching/coordinating falls back
to its pre-Valkey behaviour (recompute, no loop guard, no restored session
memory) for that one call.

Replaces two in-process, per-worker Python dicts that don't survive a restart
or a multi-worker deployment:
  - backend/routes.py's `_PREVIEW_STORE`
  - backend/tools/pipeline_tool.py's `_FAILED_CALLS`
and adds two new capabilities that didn't exist before:
  - a content-fingerprinted result cache for backend/tools/chat.py
  - per-session agent conversation storage (see ValkeyChatStorageBackend below,
    used by backend/agent_memory.py to fix get_agent()'s previously-shared,
    cross-session memory object)
"""

import hashlib
import json
import logging
from typing import Any, Optional

import redis

from modes.trial_balance.pipeline.config import settings

logger = logging.getLogger(__name__)

_pool: Optional["redis.ConnectionPool"] = None


def _get_pool() -> Optional["redis.ConnectionPool"]:
    global _pool
    if not settings.VALKEY_ENABLED:
        return None
    if _pool is None:
        _pool = redis.ConnectionPool(
            host=settings.VALKEY_HOST,
            port=settings.VALKEY_PORT,
            db=settings.VALKEY_DB,
            password=settings.VALKEY_PASSWORD or None,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _pool


def get_valkey_client() -> Optional["redis.Redis"]:
    """None when Valkey is disabled by config; a client (which may still fail
    per-call with a ConnectionError, handled by every helper below) otherwise."""
    pool = _get_pool()
    if pool is None:
        return None
    return redis.Redis(connection_pool=pool)


def _safe(op_name: str, fn, default=None):
    """Run `fn` against a live client; on any connection problem (or when
    Valkey is disabled), log once at warning level and return `default`
    instead of propagating -- see module docstring on graceful degradation."""
    client = get_valkey_client()
    if client is None:
        return default
    try:
        return fn(client)
    except redis.exceptions.RedisError as e:
        logger.warning("[valkey] %s failed, degrading to no-op: %s", op_name, e)
        return default


# =============================================================================
# Preview store -- replaces backend/routes.py's `_PREVIEW_STORE` dict
# =============================================================================

_PREVIEW_TTL_SECONDS = 30 * 60


def preview_store_set(token: str, payload: dict, ttl_seconds: int = _PREVIEW_TTL_SECONDS) -> None:
    _safe("preview_store_set", lambda c: c.set(f"preview:{token}", json.dumps(payload), ex=ttl_seconds))


def preview_store_get(token: str) -> Optional[dict]:
    raw = _safe("preview_store_get", lambda c: c.get(f"preview:{token}"))
    return json.loads(raw) if raw else None


def preview_store_update(token: str, updates: dict, ttl_seconds: int = _PREVIEW_TTL_SECONDS) -> Optional[dict]:
    """Read-modify-write on the stored dict (mirrors `_PREVIEW_STORE[token][k] = v`
    call sites in routes.py). Not atomic across processes -- acceptable here since
    a single upload token is only ever driven by the one client that created it,
    never concurrently written by two requests."""
    entry = preview_store_get(token)
    if entry is None:
        return None
    entry.update(updates)
    preview_store_set(token, entry, ttl_seconds=ttl_seconds)
    return entry


def preview_store_delete(token: str) -> None:
    _safe("preview_store_delete", lambda c: c.delete(f"preview:{token}"))


# =============================================================================
# Failed-call guard -- replaces backend/tools/pipeline_tool.py's `_FAILED_CALLS`
#
# Per-key clearing (deliberate behavior fix from the original in-process dict,
# which cleared its ENTIRE contents on any unrelated tool success).
# =============================================================================

_FAILED_CALL_TTL_SECONDS = 10 * 60


def failed_calls_mark(key: str, message: str, ttl_seconds: int = _FAILED_CALL_TTL_SECONDS) -> None:
    _safe("failed_calls_mark", lambda c: c.set(f"failedcall:{key}", message, ex=ttl_seconds))


def failed_calls_check(key: str) -> Optional[str]:
    """Returns the prior failure message if this exact call is still guarded, else None."""
    return _safe("failed_calls_check", lambda c: c.get(f"failedcall:{key}"))


def failed_calls_clear(key: str) -> None:
    _safe("failed_calls_clear", lambda c: c.delete(f"failedcall:{key}"))


# =============================================================================
# Chat result cache -- backend/tools/chat.py (Phase 2)
#
# Cache key MUST include a content fingerprint (mtime_ns + size), not just a
# TTL, since a session's Parquet/JSON artifact can be regenerated mid-session;
# callers build that fingerprint into `cache_key` themselves (see
# backend/tools/chat_cache.py) so this module stays a plain key/value cache.
# =============================================================================

_CHAT_CACHE_TTL_SECONDS = 5 * 60


def chat_cache_get(cache_key: str) -> Optional[Any]:
    raw = _safe("chat_cache_get", lambda c: c.get(f"chatcache:{cache_key}"))
    return json.loads(raw) if raw else None


def chat_cache_set(cache_key: str, value: Any, ttl_seconds: int = _CHAT_CACHE_TTL_SECONDS) -> None:
    _safe("chat_cache_set", lambda c: c.set(f"chatcache:{cache_key}", json.dumps(value), ex=ttl_seconds))


def make_cache_key(*parts: Any) -> str:
    """Stable short key from arbitrary parts (tool name, file path, fingerprint,
    args) -- hashed so an oversized/odd-charactered args blob never produces an
    oversized or invalid Valkey key."""
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


# =============================================================================
# Per-session agent chat storage -- backend/agent_memory.py
#
# Implements yukta's BaseStorageBackend directly (see yukta/core/storage.py),
# so it plugs into yukta's own Memory/ChatManager persistence mechanism
# instead of a second, parallel conversation-log format.
# =============================================================================

_AGENT_CHAT_TTL_SECONDS = 2 * 60 * 60


class ValkeyChatStorageBackend:
    """yukta BaseStorageBackend backed by Valkey. Not subclassed from
    yukta.core.storage.BaseStorageBackend at import time (keeps this module
    free of a hard yukta import -- backend/db.py follows the same local-import
    convention for its own cross-module dependency); backend/agent_memory.py
    registers it as a duck-typed implementation, which BaseStorageBackend's
    ABC does not enforce at construction time provided every abstract method
    is present.
    """

    def save(self, session_id: str, data: dict) -> str:
        key = f"agentchat:{session_id}"
        _safe("agent_chat_save", lambda c: c.set(key, json.dumps(data), ex=_AGENT_CHAT_TTL_SECONDS))
        return key

    def load(self, session_id: str) -> Optional[dict]:
        raw = _safe("agent_chat_load", lambda c: c.get(f"agentchat:{session_id}"))
        return json.loads(raw) if raw else None

    def delete(self, session_id: str) -> bool:
        deleted = _safe("agent_chat_delete", lambda c: c.delete(f"agentchat:{session_id}"), default=0)
        return bool(deleted)

    def list_sessions(self) -> list:
        # Not needed by backend/agent_memory.py's per-session load/save flow, and
        # SCAN-ing the whole "agentchat:*" keyspace is a cost no caller here needs
        # to pay -- implemented only to satisfy BaseStorageBackend's interface.
        return []
