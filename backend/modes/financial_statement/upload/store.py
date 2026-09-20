"""Where an uploaded document lives, and for how long.

**Two stores, with different jobs.** Postgres is the system of record and holds
an extraction for ``ARTHA_FS_UPLOAD_RETENTION_DAYS`` (30); Redis is the hot
cache in front of it and holds one for ``ARTHA_FS_UPLOAD_TTL_SECONDS`` (2h).
See ``pgstore.py`` and ``schema.py``.

The original requirement — **an uploaded file itself is never stored anywhere**
— still holds exactly as before. The PDF is dropped the moment the ingestion
service's job ends (see ``ingestion/app/jobs.py``), and only the extracted
result ever reaches either store. What is persisted is markdown tables,
narrative chunks, page images and the quality report: what the tools read, not
the document.

**Why not Redis alone.** It was Redis alone, on the 2-hour TTL, and when that
lapsed there was no recovery path at all — the source PDF was already gone, so
the user had to re-upload and pay for a full re-conversion, minutes of OCR.
Simply raising the TTL does not work either: Redis runs ``maxmemory 3gb`` with
``maxmemory-policy noeviction`` and one document is several MB of mostly base64
page images, so a few dozen users' retained documents fill the instance and
Redis then *rejects new uploads* rather than evicting old ones.

**Lifetime.** A document is dropped when its conversation is deleted (the
primary path — ``drop_conversation`` below, called from ``DELETE
/conversations/{id}``, which now clears both stores), when its retention window
expires (``_maybe_sweep``), or — from the cache only — when the conversation
has been idle past the TTL or the user's resident documents exceed
``ARTHA_FS_UPLOAD_MAX_DOCS``. Falling out of the cache is no longer a loss: the
next read reloads from Postgres and warms Redis again.

Note what is *still not* kept in the model's context: the documents themselves.
The tools reach into this store on demand, exactly as they reach into Postgres
for a corpus document, so ten uploaded statements cost ten Redis keys rather
than ten documents' worth of prompt. What bounds the conversation is the
model's own window over its turns, which is the gateway's existing concern and
not this module's.

Scoping is per user AND per conversation, and the user id is baked into every
Redis key this module writes — the Redis-native equivalent of the discipline
``conversations.py`` applies to chat history, where ``user_id`` is in the WHERE
clause of every SQL query rather than checked afterwards in Python. Every key
is built through ``_doc_key``/``_conv_set_key``/``_user_cap_key`` below; nothing
else in this module is allowed to format one by hand.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import redis

logger = logging.getLogger(__name__)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


TTL_SECONDS = _int_env("ARTHA_FS_UPLOAD_TTL_SECONDS", 7200)
MAX_DOCS_PER_USER = _int_env("ARTHA_FS_UPLOAD_MAX_DOCS", 12)
MAX_UPLOAD_BYTES = _int_env("ARTHA_FS_UPLOAD_MAX_BYTES", 64 * 1024 * 1024)

# How long an extraction survives in POSTGRES, which is a different question
# from how long it stays hot in Redis. TTL_SECONDS above bounds the cache; this
# bounds the record. It cannot simply be TTL_SECONDS raised to 30 days: Redis
# runs `maxmemory 3gb` with `maxmemory-policy noeviction` and a document is
# several MB of mostly base64 page images, so a few dozen users' retained
# documents would fill the instance and Redis would then REJECT new uploads
# rather than evict old ones.
RETENTION_DAYS = _int_env("ARTHA_FS_UPLOAD_RETENTION_DAYS", 30)

# Whether Postgres is used at all. Off leaves the pre-existing Redis-only
# behaviour exactly as it was, which is what makes this safe to roll back
# without a code change if the platform database is unavailable.
PERSIST_TO_POSTGRES = (
    (os.getenv("ARTHA_FS_UPLOAD_PERSIST", "") or "1").strip().lower()
    not in ("0", "false", "no", "off")
)

# Whether a user may edit a cell the extraction flagged `[unreadable ...]` or
# `[recovered ...]`. Default OFF: this changes the trust model for every
# downstream answer (a figure with no machine evidence can enter the system),
# so the owner opts in deliberately rather than it landing on by rollout.
USER_EDITS_ENABLED = (
    (os.getenv("ARTHA_FS_UPLOAD_USER_EDITS", "") or "0").strip().lower()
    not in ("0", "false", "no", "off")
)

# At most one expiry sweep per process per this many seconds. The sweep is
# opportunistic (see _maybe_sweep) rather than scheduled, matching the
# self-healing style `list` and `_enforce_cap` already use rather than adding a
# scheduler this service does not have.
_SWEEP_INTERVAL_SECONDS = _int_env("ARTHA_FS_UPLOAD_SWEEP_SECONDS", 3600)
_last_sweep = 0.0
_sweep_lock = threading.Lock()

# How long the per-user cap-tracking ZSET (fs:cap:{user}) is allowed to sit
# untouched before Redis reclaims it on its own. Deliberately much longer than
# TTL_SECONDS: that constant bounds one IDLE CONVERSATION, but this key spans
# every conversation a user has ever uploaded into, so touching one active
# conversation must not reset the clock on an otherwise-idle sibling. This only
# guards against a user who never returns at all.
USER_CAP_TTL_SECONDS = max(TTL_SECONDS * 12, 86400)

# Extra ZRANGE candidates fetched per eviction pass, to absorb members whose
# content key already expired natively without a second round trip in the
# common case. Not load-bearing for correctness -- see _enforce_cap.
_CAP_STALE_MARGIN = 8

# Defensive cap on the in-process vector-index cache (see _index_cache below).
# Bounds worst-case backend memory even if a cleanup call is ever missed --
# e.g. a Redis key expiring natively with no delete()/drop_conversation() call
# to trigger _evict_index_cache.
_INDEX_CACHE_MAX_ENTRIES = 500


class UploadStoreError(RuntimeError):
    """The Redis-backed upload store could not be reached, or a command failed.

    Callers translate this into a 503 -- a store outage is a service
    availability problem, the same category ``AuthDBError`` already is for the
    accounts database, not a 404 or a 500 that reads as "this document doesn't
    exist" or "the server is broken."
    """


def _prefix() -> str:
    return os.getenv("ARTHA_REDIS_KEY_PREFIX", "fs")


def _doc_key(user_id: str, conversation_id: str, doc_id: str) -> str:
    """The one place a key touching a document's content is built.

    Every method below routes through this rather than hand-assembling a key —
    user_id is baked into the key itself, never checked only afterwards in
    Python, the same way conversations.py never trusts a WHERE clause a later
    refactor could accidentally drop.
    """
    return f"{_prefix()}:doc:{user_id}:{conversation_id}:{doc_id}"


def _conv_set_key(user_id: str, conversation_id: str) -> str:
    """SET of doc_ids resident in one conversation -- what `list()` reads
    instead of scanning every `doc:*` key in the store."""
    return f"{_prefix()}:conv:{user_id}:{conversation_id}"


def _user_cap_key(user_id: str) -> str:
    """ZSET, member -> score `uploaded_at`, spanning every conversation this
    user has documents in -- what MAX_DOCS_PER_USER eviction reads."""
    return f"{_prefix()}:cap:{user_id}"


def _cap_member(conversation_id: str, doc_id: str) -> str:
    """JSON rather than a delimited string: doc_id (`up_<sha256[:16]>`) and
    conversation_id (a uuid4) are very unlikely to collide with a plain
    delimiter today, but encoding the pair removes the possibility entirely
    rather than relying on that staying true forever."""
    return json.dumps([conversation_id, doc_id])


# ---------------------------------------------------------------------------
# The in-process vector-index cache
#
# UploadedDocument.index (built by ensure_index(), below) holds embeddings --
# not cleanly JSON-serializable, and not worth persisting to Redis even if it
# were: rebuilding it costs one embedding pass, and every document that has
# ever been narrative-searched would otherwise sit in Redis forever as dead
# weight. Today, before Redis, STORE.get()/.list() handed back the SAME Python
# object every call, so the built index survived for the rest of the
# conversation for free. Once documents round-trip through Redis as JSON,
# get()/list() deserialize a FRESH UploadedDocument on every call -- without
# this cache, ensure_index() would re-embed the same document on every tool
# call within a conversation, a real latency and cost regression. Keyed by
# doc_id, not by the (mutable, freshly-created-per-call) UploadedDocument
# instance, so it survives being looked up through many separate
# deserializations of the same document.
# ---------------------------------------------------------------------------

_index_cache: dict[str, Any] = {}
_index_cache_lock = threading.Lock()


def _evict_index_cache(doc_id: str) -> None:
    with _index_cache_lock:
        _index_cache.pop(doc_id, None)


@dataclass
class UploadedDocument:
    """One converted document, in the shape the FS tools already read."""

    doc_id: str
    user_id: str
    conversation_id: str
    filename: str
    document: dict[str, Any]
    identification: dict[str, Any]
    quality: dict[str, Any]
    tables: list[dict[str, Any]] = field(default_factory=list)
    texts: list[dict[str, Any]] = field(default_factory=list)
    #: One entry per kept page (blank/duplicate pages excluded), for the
    #: document pane's page-by-page view. Same lifecycle as `tables`/`texts`
    #: above — see the module docstring.
    pages: list[dict[str, Any]] = field(default_factory=list)
    uploaded_at: float = field(default_factory=time.time)
    #: Lazily-built vector index over this document's narrative chunks. NOT
    #: persisted to Redis — see the _index_cache block above. Built on the
    #: first narrative question and reused for the rest of the conversation.
    index: Any = None

    # ---- accessors the bridge uses ------------------------------------

    @property
    def company(self) -> str | None:
        return self.identification.get("entity_name") or self.document.get("company")

    @property
    def financial_year(self) -> str | None:
        return self.identification.get("financial_year")

    @property
    def fy_end(self) -> int | None:
        return self.document.get("fy_end")

    def financial_tables(self, statement_type: str | None = None) -> list[dict[str, Any]]:
        rows = [t for t in self.tables if not statement_type
                or t.get("financial_stmt_type") == statement_type]
        return sorted(rows, key=lambda t: (t.get("page_ocr_start") or 0, t.get("table_id") or ""))

    def narrative(self, chunk_types: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        """Narrative chunks in document order.

        Ordered by ``(page_ocr_start, chunk_id)`` because that is the tuple
        ``DocumentResolver._fetch_following_chunks`` compares against to take
        "everything strictly after the anchor". A different order here would
        assemble a passage out of the wrong paragraphs.
        """
        rows = [
            t for t in self.texts
            if chunk_types is None or t.get("chunk_type") in chunk_types
        ]
        return sorted(
            rows,
            key=lambda t: (t.get("page_ocr_start") or 0, str(t.get("chunk_id") or "")),
        )

    def ensure_index(self):
        """The document's vector index, built on first use and cached by
        doc_id rather than held only on this object.

        store.py now hands back a freshly-deserialized UploadedDocument on
        every get()/list() (see the _index_cache block above for why) — this
        cache is what makes the built index still survive across turns of the
        same conversation, exactly as it always has.
        """
        if self.index is None:
            with _index_cache_lock:
                self.index = _index_cache.get(self.doc_id)
        if self.index is None:
            from .embeddings import DocumentIndex

            self.index = DocumentIndex()
            with _index_cache_lock:
                if len(_index_cache) >= _INDEX_CACHE_MAX_ENTRIES:
                    _index_cache.pop(next(iter(_index_cache), None), None)
                _index_cache[self.doc_id] = self.index
        return self.index

    def summary(self) -> dict[str, Any]:
        """The compact form listed in the UI and returned by the list tool."""
        quality = self.quality or {}
        return {
            "doc_id": self.doc_id,
            "filename": self.filename,
            "company": self.company,
            "financial_year": self.financial_year,
            "fy_confidence": self.identification.get("fy_confidence"),
            "framework": self.identification.get("framework"),
            "framework_division": self.identification.get("framework_division"),
            "statement_flavour": self.identification.get("statement_flavour"),
            "flavour_confidence": self.identification.get("flavour_confidence"),
            "pages": self.document.get("total_pages_pdf"),
            "tables": len(self.tables),
            "grade": quality.get("grade"),
            "low_grade": quality.get("low_grade"),
            "unreadable_cells": len(quality.get("unreadable_cells") or []),
            #: Of the cells still counted above, how many carry a second
            #: reader's figure that the arithmetic did NOT confirm -- these
            #: are the "[recovered ...]" ones, still un-computable, but shown
            #: rather than blank. Does NOT include an arithmetic-promoted
            #: figure: that one is no longer withheld at all, so it is not in
            #: `unreadable_cells` either.
            "recovered_cells": sum(
                1 for c in (quality.get("recovered_cells") or []) if not c.get("promoted")
            ),
            "failed_footings": len(quality.get("failed_footings") or []),
            #: Figures a user typed after reading the scan (see edits.py).
            #: Counted separately from `unreadable_cells`/`recovered_cells`
            #: above, which an edit removes the cell from -- so this is the
            #: only place the total number of user-supplied figures shows up.
            "user_entered_cells": len(
                [e for e in (quality.get("user_edits") or []) if e.get("active", True)]
            ),
            "vlm_used": quality.get("vlm_used"),
            "uploaded_at": self.uploaded_at,
        }


def _serializable(document: UploadedDocument) -> dict[str, Any]:
    """Every field except `index` -- a plain dict, not `dataclasses.asdict()`,
    which would deep-copy the page images and table snippets (multi-MB, per
    `emit.build_page_image`'s sizing note) for no benefit here."""
    return {
        "doc_id": document.doc_id,
        "user_id": document.user_id,
        "conversation_id": document.conversation_id,
        "filename": document.filename,
        "document": document.document,
        "identification": document.identification,
        "quality": document.quality,
        "tables": document.tables,
        "texts": document.texts,
        "pages": document.pages,
        "uploaded_at": document.uploaded_at,
    }


def _deserialize(raw: str) -> UploadedDocument:
    return UploadedDocument(**json.loads(raw))


class DocumentStore:
    """Redis-backed store of uploaded documents, scoped per user and per
    conversation.

    Deliberately synchronous (plain `def`, not `async def`): `adapter.py`'s
    `run_query` — the caller with the most call sites into this class — is
    itself synchronous, invoked from an async route via
    `asyncio.to_thread(adapter.run_query, ...)`, and has no event loop of its
    own to `await` on. Route handlers that call this class directly instead
    wrap each call in `await asyncio.to_thread(...)`, the same pattern
    `router.py` already uses for the equally-blocking calls into
    `conversations.py`.
    """

    def __init__(self) -> None:
        self._client_lock = threading.Lock()
        self._client: redis.Redis | None = None

    def _redis(self) -> redis.Redis:
        """Lazily-constructed, module-lifetime client.

        Mirrors `auth/db.py`'s `_get_pool()` in spirit — construct once,
        behind a lock, on first use — though there is no separate pool object
        to hold here the way psycopg2 needs one: redis-py's own `Redis` object
        already pools connections internally.
        """
        if self._client is not None:
            return self._client
        with self._client_lock:
            if self._client is not None:
                return self._client
            url = os.environ.get("ARTHA_REDIS_URL", "")
            if not url:
                raise UploadStoreError(
                    "ARTHA_REDIS_URL is not set. Leave the upload feature off "
                    "by clearing ARTHA_INGEST_URL instead of leaving this "
                    "half-configured."
                )
            try:
                self._client = redis.Redis.from_url(
                    url,
                    decode_responses=True,
                    socket_connect_timeout=3,
                    socket_timeout=5,
                    retry_on_timeout=True,
                    health_check_interval=30,
                )
            except Exception as exc:  # noqa: BLE001
                raise UploadStoreError(f"Could not construct the Redis client: {exc}") from exc
            return self._client

    def ping(self) -> tuple[bool, str | None]:
        """Can the store actually be reached right now? Mirrors
        `auth/db.py::ping()`, used by `/upload/health`."""
        try:
            self._redis().ping()
            return True, None
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    # ---- writing -------------------------------------------------------

    def put(self, document: UploadedDocument) -> None:
        # Postgres FIRST, and a failure here fails the upload: it is the system
        # of record, and reporting a successful conversion for a document that
        # was never durably written would leave the user believing they had a
        # document that vanishes at the next cache expiry.
        _persist(document)

        r = self._redis()
        doc_key = _doc_key(document.user_id, document.conversation_id, document.doc_id)
        conv_key = _conv_set_key(document.user_id, document.conversation_id)
        cap_key = _user_cap_key(document.user_id)
        member = _cap_member(document.conversation_id, document.doc_id)
        try:
            pipe = r.pipeline()
            pipe.set(doc_key, json.dumps(_serializable(document)), ex=TTL_SECONDS)
            pipe.sadd(conv_key, document.doc_id)
            pipe.expire(conv_key, TTL_SECONDS)
            pipe.zadd(cap_key, {member: document.uploaded_at})
            pipe.expire(cap_key, USER_CAP_TTL_SECONDS)
            pipe.execute()
        except redis.exceptions.RedisError as exc:
            # The cache failing is NOT the upload failing, now that the
            # document is safe in Postgres -- the next read repopulates. Before
            # persistence existed this had to be fatal, because Redis was the
            # only copy.
            if PERSIST_TO_POSTGRES:
                logger.warning(
                    "cached %s to Redis failed (%s); serving it from Postgres",
                    document.doc_id, exc,
                )
                return
            raise UploadStoreError(f"Could not store {document.doc_id}: {exc}") from exc
        self._enforce_cap(document.user_id)
        _maybe_sweep()

    def update_cell(self, user_id: str, conversation_id: str, doc_id: str,
                     table_id: str, mutate):
        """Apply one cell edit (`edits.apply`) to a stored document.

        Postgres first, same reasoning as `put()`: it is the system of
        record, so `mutate` must run exactly once, against it. `mutate` is
        NOT idempotent -- it appends to `history` and checks `expected_cell`
        against the value it is given -- so once Postgres has applied it, the
        Redis cache is patched with the values just committed rather than
        calling `mutate` again.

        Returns `None` if the document or table does not exist (caller: 404).
        Raises whatever `mutate` raises (typically `edits.EditError`) with
        nothing written anywhere. Raises `UploadStoreError` only for a Redis
        failure in Redis-only mode -- with persistence on, a cache failure
        after the Postgres commit is logged and the edit still reports success
        (see below), exactly like `put()`.
        """
        if not _persistence_enabled():
            return self._update_cell_redis_only(user_id, conversation_id, doc_id,
                                                 table_id, mutate)

        from . import pgstore
        outcome = pgstore.update_table_cell(user_id, conversation_id, doc_id,
                                             table_id, mutate)
        if outcome is None:
            return None
        new_md, new_quality, result = outcome

        r = self._redis()
        doc_key = _doc_key(user_id, conversation_id, doc_id)
        try:
            raw = r.get(doc_key)
            if raw is not None:
                blob = json.loads(raw)
                for table in blob.get("tables") or []:
                    if table.get("table_id") == table_id:
                        table["table_md"] = new_md
                        break
                blob["quality"] = new_quality
                # KEEPTTL: an edit must not extend how long this document
                # stays cached -- only a fresh upload/reload resets that clock.
                r.set(doc_key, json.dumps(blob), keepttl=True)
            # else: the cache entry has already expired. Nothing to patch --
            # the next get() reloads from Postgres, which already holds the
            # edit, and rewarms the cache with it. Nothing lost.
        except redis.exceptions.RedisError as exc:
            # The edit is safely in Postgres. A stale cached blob (missing
            # this edit) must not outlive it, so drop the key rather than
            # leave it to expire naturally up to TTL_SECONDS later.
            logger.warning(
                "could not refresh cached %s after a cell edit (%s); dropping"
                " the cached copy so it reloads from Postgres", doc_id, exc,
            )
            try:
                r.delete(doc_key)
            except redis.exceptions.RedisError:
                pass
        return result

    def _update_cell_redis_only(self, user_id: str, conversation_id: str, doc_id: str,
                                 table_id: str, mutate):
        """`update_cell` when Postgres persistence is off. Redis is then the
        only copy, so the edit needs its own WATCH/MULTI transaction rather
        than the plain SET `put()` uses -- two edits racing the same document
        must not let the second one silently overwrite the first's write with
        a blob it built from data read before the first edit landed."""
        r = self._redis()
        doc_key = _doc_key(user_id, conversation_id, doc_id)
        for _attempt in range(5):
            pipe = r.pipeline()
            try:
                pipe.watch(doc_key)
                raw = pipe.get(doc_key)
                if raw is None:
                    pipe.unwatch()
                    return None
                blob = json.loads(raw)
                table_md = None
                target = None
                for table in blob.get("tables") or []:
                    if table.get("table_id") == table_id:
                        table_md = table.get("table_md") or ""
                        target = table
                        break
                if target is None:
                    pipe.unwatch()
                    return None
                new_md, new_quality, result = mutate(table_md, blob.get("quality") or {})
                target["table_md"] = new_md
                blob["quality"] = new_quality
                pipe.multi()
                pipe.set(doc_key, json.dumps(blob), keepttl=True)
                pipe.execute()
                return result
            except redis.exceptions.WatchError:
                continue
            except Exception:
                pipe.reset()
                raise
        raise UploadStoreError(
            f"Could not save the edit to {doc_id}: too many concurrent edits."
        )

    def delete(self, user_id: str, conversation_id: str, doc_id: str) -> bool:
        r = self._redis()
        try:
            pipe = r.pipeline()
            pipe.delete(_doc_key(user_id, conversation_id, doc_id))
            pipe.srem(_conv_set_key(user_id, conversation_id), doc_id)
            pipe.zrem(_user_cap_key(user_id), _cap_member(conversation_id, doc_id))
            existed, _removed_from_set, _removed_from_cap = pipe.execute()
        except redis.exceptions.RedisError as exc:
            raise UploadStoreError(f"Could not delete {doc_id}: {exc}") from exc
        # Delete the RECORD too, not just the cache. Leaving the row would
        # bring the document back on the next cache miss -- a delete the user
        # watched succeed, silently undone.
        removed = _unpersist(user_id, conversation_id, doc_id)
        _evict_index_cache(doc_id)
        return bool(existed) or removed

    def drop_conversation(self, user_id: str, conversation_id: str) -> int:
        """Called when a conversation is deleted. This is the primary
        eviction path — TTL below is only the backstop for conversations
        nobody ever deletes."""
        r = self._redis()
        conv_key = _conv_set_key(user_id, conversation_id)
        cap_key = _user_cap_key(user_id)
        # The record goes first and unconditionally: an empty Redis set means
        # the cache has expired, NOT that there is nothing to delete, and
        # returning early on it would leave every row behind for the full
        # retention window after the user deleted the conversation.
        persisted = _unpersist_conversation(user_id, conversation_id)
        try:
            doc_ids = r.smembers(conv_key)
            if not doc_ids:
                return persisted
            pipe = r.pipeline()
            for doc_id in doc_ids:
                pipe.delete(_doc_key(user_id, conversation_id, doc_id))
                pipe.zrem(cap_key, _cap_member(conversation_id, doc_id))
            pipe.delete(conv_key)
            pipe.execute()
        except redis.exceptions.RedisError as exc:
            raise UploadStoreError(
                f"Could not drop conversation {conversation_id}: {exc}"
            ) from exc
        for doc_id in doc_ids:
            _evict_index_cache(doc_id)
        return max(len(doc_ids), persisted)

    def touch(self, user_id: str, conversation_id: str) -> None:
        """Refresh the TTL on a conversation's documents.

        Refreshes every document key AND the conversation set in one pipeline
        so the whole conversation's documents keep living and dying on the
        same clock — Redis expires individual keys, not conversation buckets,
        and nothing else here re-derives that grouping.
        """
        r = self._redis()
        conv_key = _conv_set_key(user_id, conversation_id)
        try:
            doc_ids = r.smembers(conv_key)
            if not doc_ids:
                return
            pipe = r.pipeline()
            pipe.expire(conv_key, TTL_SECONDS)
            for doc_id in doc_ids:
                pipe.expire(_doc_key(user_id, conversation_id, doc_id), TTL_SECONDS)
            pipe.execute()
        except redis.exceptions.RedisError as exc:
            raise UploadStoreError(f"Could not refresh {conversation_id}: {exc}") from exc

    # ---- reading -------------------------------------------------------

    def list(self, user_id: str, conversation_id: str) -> list[UploadedDocument]:
        r = self._redis()
        conv_key = _conv_set_key(user_id, conversation_id)
        try:
            doc_ids = list(r.smembers(conv_key))
            if not doc_ids:
                # Nothing hot. That is not the same as nothing stored -- the
                # cache expires in hours and the record lives for weeks -- so
                # this is where a conversation resumed the next day, or after
                # a Redis restart, gets its documents back instead of the user
                # being told to upload them again.
                return self._rehydrate(user_id, conversation_id)
            keys = [_doc_key(user_id, conversation_id, d) for d in doc_ids]
            raws = r.mget(keys)
        except redis.exceptions.RedisError as exc:
            recovered = self._rehydrate(user_id, conversation_id)
            if recovered:
                logger.warning(
                    "Redis unavailable listing %s (%s); served %d document(s) "
                    "from Postgres instead", conversation_id, exc, len(recovered),
                )
                return recovered
            raise UploadStoreError(f"Could not list documents: {exc}") from exc

        documents: list[UploadedDocument] = []
        stale: list[str] = []
        for doc_id, raw in zip(doc_ids, raws):
            if raw is None:
                # The content key already expired natively; the SET doesn't
                # know yet. Self-heal here rather than a background sweep —
                # this, plus the equivalent in _enforce_cap, is what replaces
                # the old dict-backed store's lazy _expire_locked/_evict_locked:
                # most of that logic is now just Redis's own per-key EXPIRE.
                stale.append(doc_id)
                continue
            documents.append(_deserialize(raw))
        if stale:
            try:
                r.srem(conv_key, *stale)
            except redis.exceptions.RedisError:
                pass  # purely tidiness; the next list() for this user tries again
            # Those ids expired out of the cache but may still be within their
            # retention window, so recover them rather than reporting a
            # conversation that has quietly lost half its documents.
            known = {d.doc_id for d in documents}
            documents.extend(
                d for d in self._rehydrate(user_id, conversation_id)
                if d.doc_id not in known
            )
        return sorted(documents, key=lambda d: d.uploaded_at)

    def _rehydrate(self, user_id: str, conversation_id: str) -> list[UploadedDocument]:
        """Load from Postgres and warm the cache. [] if persistence is off.

        Never raises: this is a recovery path, and a platform-database problem
        here must degrade to "no uploads in scope" -- which the caller already
        handles and reports honestly -- rather than failing the question.
        """
        if not _persistence_enabled():
            return []
        try:
            from . import pgstore
            documents = pgstore.load(user_id, conversation_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not load uploads from Postgres: %s", exc)
            return []
        for document in documents:
            try:
                self._cache(document)
            except Exception:  # noqa: BLE001
                pass  # serving the document matters; caching it is an optimisation
        return documents

    def _cache(self, document: UploadedDocument) -> None:
        """Write one document into Redis only, without touching Postgres."""
        r = self._redis()
        pipe = r.pipeline()
        pipe.set(
            _doc_key(document.user_id, document.conversation_id, document.doc_id),
            json.dumps(_serializable(document)),
            ex=TTL_SECONDS,
        )
        conv_key = _conv_set_key(document.user_id, document.conversation_id)
        pipe.sadd(conv_key, document.doc_id)
        pipe.expire(conv_key, TTL_SECONDS)
        pipe.execute()

    def get(self, user_id: str, conversation_id: str, doc_id: str) -> UploadedDocument | None:
        r = self._redis()
        try:
            raw = r.get(_doc_key(user_id, conversation_id, doc_id))
        except redis.exceptions.RedisError as exc:
            raw = None
            if not _persistence_enabled():
                raise UploadStoreError(f"Could not read {doc_id}: {exc}") from exc
        if raw is not None:
            return _deserialize(raw)

        if not _persistence_enabled():
            return None
        try:
            from . import pgstore
            document = pgstore.load_one(user_id, conversation_id, doc_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not load %s from Postgres: %s", doc_id, exc)
            return None
        if document is not None:
            try:
                self._cache(document)
            except Exception:  # noqa: BLE001
                pass
        return document

    # ---- maintenance ---------------------------------------------------

    def _enforce_cap(self, user_id: str) -> None:
        """Oldest-first eviction once a user is over the resident-document cap.

        Not wrapped in a Lua script or WATCH/MULTI transaction: two uploads
        racing this method can, in the rare case, both see "room for one
        more" and both proceed, landing the user transiently one over cap
        until the next put() re-evaluates and corrects it. That is the same
        race property the old process-wide-lock dict store already had
        against a second put() arriving right after the lock released — not a
        regression — and a fully atomic check-and-evict is real added
        complexity (script deployment, a new failure mode if the script
        itself errors) for a bound that is a sizing guideline, not a security
        boundary.

        A failed eviction pass is logged and swallowed rather than raised: it
        must not fail the upload that triggered it. The user ends up
        transiently over cap, corrected on the next successful put().
        """
        r = self._redis()
        cap_key = _user_cap_key(user_id)
        try:
            count = r.zcard(cap_key)
            if count <= MAX_DOCS_PER_USER:
                return
            over = count - MAX_DOCS_PER_USER
            candidates = r.zrange(cap_key, 0, over + _CAP_STALE_MARGIN - 1)
            removed = 0
            for member in candidates:
                if removed >= over:
                    break
                conversation_id, doc_id = json.loads(member)
                pipe = r.pipeline()
                pipe.delete(_doc_key(user_id, conversation_id, doc_id))
                pipe.srem(_conv_set_key(user_id, conversation_id), doc_id)
                pipe.zrem(cap_key, member)
                existed, _srem, _zrem = pipe.execute()
                _evict_index_cache(doc_id)
                if existed:
                    removed += 1
                    logger.info("evicted uploaded document %s (per-user cap)", doc_id)
                # else: member was already stale (its content key had already
                # expired) — cleaned up as a side effect, but it doesn't count
                # toward `over`, since it wasn't occupying real room.
        except redis.exceptions.RedisError as exc:
            logger.warning("per-user cap eviction failed for %s: %s", user_id, exc)


STORE = DocumentStore()


# ---------------------------------------------------------------------------
# Request scope
# ---------------------------------------------------------------------------

@dataclass
class Scope:
    """Which uploaded documents the current request may see.

    A ``ContextVar`` rather than a parameter threaded through every call because
    the tool closures ``ToolRegistry.build_tools`` constructs are handed to the
    agent framework, which calls them with only the model's own arguments. There
    is no seam to pass this through, and a module global would leak one user's
    documents into another's request under concurrency.
    """

    user_id: str
    conversation_id: str
    documents: list[UploadedDocument] = field(default_factory=list)

    @property
    def packages(self) -> list["Package"]:
        return group_into_packages(self.documents)

    def by_id(self, doc_id: str | None):
        """The document or PACKAGE this doc_id names.

        Both are accepted because both are handed out: `_resolve_document`
        returns a package's synthetic id, while a question scoped to one
        uploaded file still resolves by that file's own id.
        """
        if not doc_id:
            return None
        for document in self.documents:
            if document.doc_id == doc_id:
                return document
        for package in self.packages:
            if package.doc_id == doc_id:
                return package
        return None

    def match(self, company: str | None, financial_year: str | None = None):
        """Uploaded documents matching a company and optionally a year.

        Matching is loose on both sides because prompt rule 16a tells the model
        to pass the company name exactly as the user typed it, and a user
        naming an uploaded file will as often type the filename, the entity, or
        neither.

        The YEAR is compared through ``_fy_key`` rather than by string
        equality, because the spellings genuinely differ across the callers and
        an exact comparison made the commonest multi-document question
        unanswerable. See ``_fy_key`` for the three real spellings involved.
        """
        if not self.documents:
            return []

        results = list(self.documents)
        if company:
            needle = str(company).strip().lower().replace("_", " ")
            if needle:
                narrowed = [
                    d for d in results
                    if needle in (d.company or "").lower()
                    or (d.company or "").lower() in needle
                    or needle in d.filename.lower()
                    or needle in d.doc_id.lower()
                ]
                # A company that matches nothing uploaded is not an error here:
                # the caller falls through to the corpus, which is where a
                # question about an entity the user never uploaded belongs.
                results = narrowed
        if financial_year and results:
            wanted = _fy_key(financial_year)
            if wanted is not None:
                exact = [d for d in results if _fy_key(d.financial_year) == wanted]
            else:
                # Not recognisable as a financial year at all. Fall back to the
                # literal comparison this used to do, so anything unusual keeps
                # behaving exactly as it did.
                literal = str(financial_year).strip()
                exact = [d for d in results if (d.financial_year or "") == literal]
            if exact:
                results = exact
        # Collapse into packages LAST, so filtering still happens per file but
        # what the caller gets back is one entry per filing.
        return group_into_packages(results)


def _persistence_enabled() -> bool:
    """Whether to durably store extractions at all.

    Two different situations, deliberately told apart:

    * **No platform database configured** (no FINANCE_DSN / ARTHA_DB_DSN).
      A deployment choice, not a fault. Uploads fall back to the Redis-only
      behaviour this feature had before persistence existed, which still
      works -- documents just live hours instead of weeks.
    * **Configured but failing.** A real fault, and `_persist` raises so it
      surfaces rather than silently costing the user their document.
    """
    if not PERSIST_TO_POSTGRES:
        return False
    try:
        from app.auth.db import dsn
        return bool(dsn())
    except Exception:  # noqa: BLE001
        return False


def _persist(document: "UploadedDocument") -> None:
    """Write the extraction to Postgres, the system of record.

    Raises `UploadStoreError` on failure -- deliberately fatal to the upload.
    A conversion reported as successful for a document that was never durably
    written leaves the user believing they have a document that will vanish
    at the next cache expiry, with no way to tell the difference until it does.
    """
    if not _persistence_enabled():
        return
    try:
        from . import pgstore
        pgstore.save(document, RETENTION_DAYS)
    except Exception as exc:  # noqa: BLE001
        raise UploadStoreError(
            f"Could not persist {document.doc_id} to the platform database: {exc}"
        ) from exc


def _unpersist(user_id: str, conversation_id: str, doc_id: str) -> bool:
    """Delete one record. Never raises -- the cache delete already succeeded."""
    if not _persistence_enabled():
        return False
    try:
        from . import pgstore
        return pgstore.delete(user_id, conversation_id, doc_id) > 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not delete %s from Postgres: %s", doc_id, exc)
        return False


def _unpersist_conversation(user_id: str, conversation_id: str) -> int:
    if not _persistence_enabled():
        return 0
    try:
        from . import pgstore
        return pgstore.drop_conversation(user_id, conversation_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "could not drop conversation %s from Postgres: %s", conversation_id, exc
        )
        return 0


def persistence_status() -> tuple[bool, str | None]:
    """(healthy, reason) for the durable store. Never raises.

    Persistence being switched OFF is reported as healthy: a deployment with no
    platform database is a supported configuration (documents live hours rather
    than weeks), not a fault. Configured-but-unreachable is a fault, because
    every upload from here on would be rejected.
    """
    if not _persistence_enabled():
        return True, None
    try:
        from . import pgstore
        return pgstore.ping()
    except Exception as exc:  # noqa: BLE001
        return False, f"uploaded-document persistence unavailable: {exc}"


def _maybe_sweep() -> None:
    """Delete expired records, at most once per process per interval.

    Opportunistic rather than scheduled, matching the self-healing style
    `list` and `_enforce_cap` already use. Never raises: housekeeping failing
    must not fail the upload that happened to trigger it.
    """
    global _last_sweep
    if not _persistence_enabled():
        return
    now = time.time()
    with _sweep_lock:
        if now - _last_sweep < _SWEEP_INTERVAL_SECONDS:
            return
        _last_sweep = now
    try:
        from . import pgstore
        pgstore.sweep_expired()
    except Exception as exc:  # noqa: BLE001
        logger.warning("expiry sweep failed: %s", exc)


#: Scan grades, best first. Used to report a package's WEAKEST member rather
#: than its first, so a clean set of statements cannot mask an illegible
#: annexure bound into the same filing.
_GRADE_ORDER = ("excellent", "good", "fair", "poor")


@dataclass
class Package:
    """Several uploaded files that are one filing.

    Specification section 4.1 treats the statements, the auditor's report and
    the CARO annexure as a single *package*, and the checks that matter most
    span them: section 13 cross-reads a CARO clause against the notes, and
    section 12 weighs the auditor's going-concern wording against the numbers.
    In this corpus those arrive as separate PDFs -- measured, the SFS and
    IARSFS files share no pages at all -- so without grouping, a CARO
    cross-check has only half its evidence.

    A package presents itself to the tools as ONE document with a synthetic
    ``doc_id``, while every chunk and table keeps its own ``source_file`` so a
    citation still names the file the reader has to open.
    """

    members: list[UploadedDocument]

    @property
    def doc_id(self) -> str:
        if len(self.members) == 1:
            return self.members[0].doc_id
        digest = hashlib.sha256(
            "|".join(sorted(m.doc_id for m in self.members)).encode()
        ).hexdigest()
        return f"pkg_{digest[:16]}"

    @property
    def filename(self) -> str:
        return " + ".join(m.filename for m in self.members)

    @property
    def company(self) -> str | None:
        for m in self.members:
            if m.company:
                return m.company
        return None

    @property
    def financial_year(self) -> str | None:
        for m in self.members:
            if m.financial_year:
                return m.financial_year
        return None

    @property
    def fy_end(self) -> int | None:
        ends = [m.fy_end for m in self.members if m.fy_end is not None]
        return max(ends) if ends else None

    @property
    def document(self) -> dict[str, Any]:
        return self.members[0].document if self.members else {}

    @property
    def identification(self) -> dict[str, Any]:
        return self.members[0].identification if self.members else {}

    @property
    def tables(self) -> list[dict[str, Any]]:
        return [t for m in self.members for t in m.tables]

    @property
    def texts(self) -> list[dict[str, Any]]:
        return [t for m in self.members for t in m.texts]

    def financial_tables(self, statement_type: str | None = None) -> list[dict[str, Any]]:
        rows = [
            t for t in self.tables
            if not statement_type or t.get("financial_stmt_type") == statement_type
        ]
        return sorted(rows, key=lambda t: (t.get("page_ocr_start") or 0, t.get("table_id") or ""))

    def narrative(self, chunk_types: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        rows = [
            t for t in self.texts
            if chunk_types is None or t.get("chunk_type") in chunk_types
        ]
        return sorted(
            rows,
            key=lambda t: (t.get("page_ocr_start") or 0, str(t.get("chunk_id") or "")),
        )

    def ensure_index(self):
        """One index for the whole package, held on its first member.

        Keyed there rather than per member so a question that searches across
        the statements and the auditor's report embeds each chunk once.
        """
        return self.members[0].ensure_index() if self.members else None

    def summary(self) -> dict[str, Any]:
        """The same shape ``UploadedDocument.summary()`` returns.

        A package is supposed to present itself to the tools as ONE document,
        and every other accessor here already does. This one was missing, so
        any caller that iterated packages and asked for a summary raised
        ``AttributeError`` -- which is what stopped `compare_uploaded_years`
        being able to report filings rather than files.

        Aggregated rather than delegated to the first member, because the
        numbers must describe the whole filing: quality is the WEAKEST member's
        (a clean set of statements does not make an illegible CARO annexure
        readable), and the counts are sums.
        """
        members = self.members or []
        quality = [m.quality or {} for m in members]

        def worst(key: str) -> str | None:
            grades = [q.get(key) for q in quality if q.get(key)]
            if not grades:
                return None
            # _GRADE_ORDER runs best -> worst, so the weakest is the HIGHEST
            # index. An unrecognised grade scores -1 so it never outranks a
            # real "poor" -- an unknown string must not silently become the
            # headline quality of the filing.
            return max(grades, key=lambda g: _GRADE_ORDER.index(g)
                       if g in _GRADE_ORDER else -1)

        first = members[0] if members else None
        identification = self.identification or {}
        return {
            "doc_id": self.doc_id,
            "filename": self.filename,
            "company": self.company,
            "financial_year": self.financial_year,
            "fy_confidence": identification.get("fy_confidence"),
            "framework": identification.get("framework"),
            "framework_division": identification.get("framework_division"),
            "statement_flavour": identification.get("statement_flavour"),
            "flavour_confidence": identification.get("flavour_confidence"),
            "pages": sum((m.document or {}).get("total_pages_pdf") or 0
                         for m in members) or None,
            "tables": len(self.tables),
            "grade": worst("grade"),
            "low_grade": worst("low_grade"),
            "unreadable_cells": sum(len(q.get("unreadable_cells") or [])
                                    for q in quality),
            "recovered_cells": sum(
                1 for q in quality for c in (q.get("recovered_cells") or [])
                if not c.get("promoted")
            ),
            "failed_footings": sum(len(q.get("failed_footings") or [])
                                   for q in quality),
            # Only true when EVERY member got a second read; "partly
            # corroborated" must not present as corroborated.
            "vlm_used": all(q.get("vlm_used") for q in quality) if quality else False,
            "uploaded_at": first.uploaded_at if first else None,
        }


def group_into_packages(documents: list[UploadedDocument]) -> list[Package]:
    """Group uploads that are the same filing.

    Grouped on entity AND financial year. Entity comparison is loose because the
    name is OCR'd from a scan and "IDBI Trusteeship Services Ltd" on one sheet is
    "IDBI Trusteeship Services Limited" on another; requiring equality would
    split a package that is obviously one filing. A document whose year could
    not be read is never grouped -- guessing which filing it belongs to is
    exactly the kind of silent mistake that puts one year's CARO against another
    year's notes.
    """
    packages: list[Package] = []
    for document in documents:
        placed = False
        if document.financial_year and document.company:
            for package in packages:
                # Compared through _fy_key for the same reason Scope.match is:
                # two files of ONE filing can be read with different spellings
                # of the same year ("2023-24" on the statements, "2023-2024" on
                # the auditor's report), and a string comparison would split
                # the package in two -- leaving a CARO cross-check with half
                # its evidence, which is the exact failure packaging exists to
                # prevent.
                if _fy_key(package.financial_year) != _fy_key(document.financial_year):
                    continue
                if _same_entity(package.company, document.company):
                    package.members.append(document)
                    placed = True
                    break
        if not placed:
            packages.append(Package(members=[document]))
    return packages


_LEGAL_NOISE = re.compile(
    r"\b(limited|ltd|private|pvt|corporation|corpn|company|co)\b\.?", re.I
)


#: A financial year written as a span: "2023-24", "2023-2024", "2023/24",
#: optionally prefixed "FY". The en/em dashes appear because OCR and the model
#: both produce them where a filing printed a hyphen.
_FY_SPAN_RE = re.compile(r"(\d{4})\s*[-/–—]\s*(\d{2,4})")
#: "FY24" -- a two-digit year, meaning the year it ENDS in.
_FY_SHORT_RE = re.compile(r"^\s*fy\s*(\d{2})\s*$", re.I)
_FY_BARE_RE = re.compile(r"\b(\d{4})\b")


def _fy_key(value: str | None) -> tuple[int, int] | None:
    """A financial year reduced to ``(start_year, end_year)``, or None.

    This exists because three parts of the system spell the same year three
    different ways, and they were being compared with ``==``:

    * ingestion stores ``"2023-24"`` (``identify.py``),
    * ``entity_resolution._fy_label`` hands the model back ``"FY2023-24"``,
    * ``TrendAnalysisTools._resolve_reports`` generates ``f"{y}-{y+1}"``,
      i.e. ``"2023-2024"``.

    None of those three match each other as strings. The measured consequence
    was not a near-miss but a loop: ``Scope.match`` drops the year filter when
    nothing matches exactly, so every document came back, the caller saw more
    than one and asked the user which year they meant -- and the year it
    suggested failed the same way when the model repeated it back. The
    multi-year trend tool could therefore never return a figure for an
    uploaded document at all.

    Normalising here, at the single point every path funnels through, fixes all
    three without editing the vendored pipeline. **The stored value is never
    rewritten** -- ``financial_year`` keeps whatever ``identify`` read off the
    statement, and this is only ever used for comparison.

    A bare four-digit year is read as the year the period ENDS in ("FY2024" is
    2023-24), which is the Indian convention and matches the ``fy_end`` column
    already used everywhere else.
    """
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None

    span = _FY_SPAN_RE.search(text)
    if span:
        start = int(span.group(1))
        tail = span.group(2)
        # "2023-2024" gives the end outright; "2023-24" needs its century, and
        # "1999-00" has to roll forward rather than land in 1900.
        end = int(tail) if len(tail) == 4 else (start // 100) * 100 + int(tail)
        if end < start:
            end += 100
        return (start, end)

    short = _FY_SHORT_RE.match(text)
    if short:
        end = 2000 + int(short.group(1))
        return (end - 1, end)

    bare = _FY_BARE_RE.search(text)
    if bare:
        end = int(bare.group(1))
        return (end - 1, end)

    return None


def _same_entity(a: str | None, b: str | None) -> bool:
    """Entity comparison that survives OCR variance and legal-suffix drift."""
    if not a or not b:
        return False

    def key(name: str) -> str:
        stripped = _LEGAL_NOISE.sub(" ", name.lower())
        return re.sub(r"[^a-z0-9]+", "", stripped)

    ka, kb = key(a), key(b)
    if not ka or not kb:
        return False
    return ka == kb or ka in kb or kb in ka


_SCOPE: contextvars.ContextVar[Scope | None] = contextvars.ContextVar(
    "fs_upload_scope", default=None
)


def set_scope(scope: Scope | None):
    return _SCOPE.set(scope)


def reset_scope(token) -> None:
    try:
        _SCOPE.reset(token)
    except (ValueError, LookupError):
        # Reset from a different context than the set. Nothing to undo.
        pass


def current_scope() -> Scope | None:
    return _SCOPE.get()


def scope_for(user_id: str, conversation_id: str | None) -> Scope:
    documents = STORE.list(user_id, conversation_id) if conversation_id else []
    return Scope(user_id=user_id, conversation_id=conversation_id or "", documents=documents)
