"""Where an uploaded document lives, and for how long.

Backed by Redis, not a database and not this process's own memory. The original
requirement — an uploaded file itself is never stored anywhere — still holds:
the PDF is dropped the moment the ingestion service's job ends (see
``ingestion/app/jobs.py``), and only the extracted result ever reaches here.
What changed is *where* that extracted result lives while its conversation is
alive: a plain in-process dict could not survive a gateway restart, which meant
restarting the gateway to deploy new code silently threw away every document a
user had uploaded moments before, with no way to recover it short of
re-uploading. Redis, with AOF persistence enabled (see ``docker-compose.yml``'s
``redis`` service), survives that — and survives a Redis restart too, which a
dict obviously never could.

**Lifetime is still the conversation's.** That answer to "how long should a
document stay analysable" has not changed: as long as the conversation it
belongs to is alive. A document is dropped when its conversation is deleted
(the primary path — ``drop_conversation`` below, called from ``DELETE
/conversations/{id}``), when the conversation has been idle past
``ARTHA_FS_UPLOAD_TTL_SECONDS`` (the backstop, enforced by Redis's own native
per-key ``EXPIRE`` rather than a lazy sweep), or when a user's resident
documents exceed ``ARTHA_FS_UPLOAD_MAX_DOCS`` and the oldest is evicted to make
room.

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
            "pages": self.document.get("total_pages_pdf"),
            "tables": len(self.tables),
            "grade": quality.get("grade"),
            "low_grade": quality.get("low_grade"),
            "unreadable_cells": len(quality.get("unreadable_cells") or []),
            "failed_footings": len(quality.get("failed_footings") or []),
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
            raise UploadStoreError(f"Could not store {document.doc_id}: {exc}") from exc
        self._enforce_cap(document.user_id)

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
        _evict_index_cache(doc_id)
        return bool(existed)

    def drop_conversation(self, user_id: str, conversation_id: str) -> int:
        """Called when a conversation is deleted. This is the primary
        eviction path — TTL below is only the backstop for conversations
        nobody ever deletes."""
        r = self._redis()
        conv_key = _conv_set_key(user_id, conversation_id)
        cap_key = _user_cap_key(user_id)
        try:
            doc_ids = r.smembers(conv_key)
            if not doc_ids:
                return 0
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
        return len(doc_ids)

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
                return []
            keys = [_doc_key(user_id, conversation_id, d) for d in doc_ids]
            raws = r.mget(keys)
        except redis.exceptions.RedisError as exc:
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
        return sorted(documents, key=lambda d: d.uploaded_at)

    def get(self, user_id: str, conversation_id: str, doc_id: str) -> UploadedDocument | None:
        r = self._redis()
        try:
            raw = r.get(_doc_key(user_id, conversation_id, doc_id))
        except redis.exceptions.RedisError as exc:
            raise UploadStoreError(f"Could not read {doc_id}: {exc}") from exc
        return _deserialize(raw) if raw is not None else None

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
            wanted = str(financial_year).strip()
            exact = [d for d in results if (d.financial_year or "") == wanted]
            if exact:
                results = exact
        # Collapse into packages LAST, so filtering still happens per file but
        # what the caller gets back is one entry per filing.
        return group_into_packages(results)


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
                if package.financial_year != document.financial_year:
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
