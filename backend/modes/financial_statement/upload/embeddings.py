"""Semantic search over an uploaded document's narrative, without pgvector.

The corpus keeps a bge-m3 vector on every ``text_chunks`` row and the FS tools
search it with ``embedding <=> %s::vector``. An uploaded document lives in this
process's memory and has no index, so the same searches have to be answered
another way.

The approach is the cheap one that preserves behaviour: batch-embed a document's
narrative chunks the first time anyone asks a narrative question about it, cache
the vectors on the document, and do the cosine in numpy. A financial statement
runs to a few hundred chunks, which is one request and a few milliseconds of
arithmetic -- far below the cost of the conversion that produced them.

Two hard-won details are inherited rather than rediscovered:

* **The endpoint takes a list.** ``financial_diagnostic_report/clients.py``'s
  ``embed_many`` learned that issuing one request per string "turned a 0.3s
  stage into a multi-second one for no reason", and that ``data`` is **not
  guaranteed to come back in request order** -- it must be re-sorted by the
  ``index`` the API returns. Trusting the order silently pairs every chunk with
  another chunk's vector, which produces plausible-looking nonsense.

* **The URL is already complete.** ``adapter._normalise_embedding_url`` appends
  ``/v1/embeddings`` to ``tools_fs.Config.EMBEDDING_BASE_URL`` at load time,
  because this pipeline POSTs to it verbatim while SAR appends the path itself.
  Appending again here yields ``.../v1/embeddings/v1/embeddings``.

When the endpoint is unreachable the search degrades to token overlap rather
than failing. That is the posture ``adapter._install_reranker_fallback`` already
set for this mode -- an embedding outage should cost ranking quality, not the
answer -- and the caller is told so it can say the search was degraded.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

import requests

logger = logging.getLogger(__name__)

#: bge-m3. The corpus vectors are this width, and a model of any other width
#: cannot be compared against them -- the same guard clients.py applies.
EXPECTED_DIM = 1024
#: One request per this many chunks. Large enough that a whole filing is one or
#: two calls, small enough that a single failure does not lose everything.
BATCH = 64
TIMEOUT = 60.0

_WORD_RE = re.compile(r"[a-z0-9]+")
#: Words too common in a financial statement to carry any signal.
_STOP = {
    "the", "and", "for", "are", "was", "were", "with", "that", "this", "from",
    "has", "have", "had", "not", "any", "all", "its", "our", "which", "such",
    "been", "under", "shall", "may", "per", "than", "into", "other", "these",
    "company", "financial", "statements", "year", "ended", "note", "notes",
}


class EmbeddingUnavailable(RuntimeError):
    """The embedding endpoint could not be reached or answered unusably."""


def _endpoint() -> tuple[str, str]:
    from tools_fs import Config  # type: ignore

    url = (Config.EMBEDDING_BASE_URL or "").strip()
    if not url:
        raise EmbeddingUnavailable("EMBEDDING_BASE_URL is not configured.")
    return url, Config.EMBEDDING_MODEL


def embed_many(texts: list[str]) -> list[list[float]]:
    """Embed several strings, in as few round trips as possible."""
    if not texts:
        return []
    url, model = _endpoint()

    vectors: list[list[float]] = []
    for start in range(0, len(texts), BATCH):
        chunk = texts[start:start + BATCH]
        try:
            response = requests.post(url, json={"model": model, "input": chunk}, timeout=TIMEOUT)
            response.raise_for_status()
            body: dict[str, Any] = response.json()
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingUnavailable(f"embedding request to {url} failed: {exc}") from exc

        rows = body.get("data")
        if not isinstance(rows, list) or len(rows) != len(chunk):
            raise EmbeddingUnavailable(
                f"embedding endpoint returned {len(rows) if isinstance(rows, list) else 'no'} "
                f"vectors for {len(chunk)} inputs"
            )
        # Re-sort by the index the API reports. See the module docstring: the
        # response order is not the request order, and trusting it pairs every
        # chunk with a different chunk's vector.
        try:
            ordered = sorted(rows, key=lambda r: r.get("index", 0))
            vectors.extend(r["embedding"] for r in ordered)
        except (KeyError, TypeError) as exc:
            raise EmbeddingUnavailable(f"unexpected embedding response shape: {exc}") from exc

    if vectors and len(vectors[0]) != EXPECTED_DIM:
        raise EmbeddingUnavailable(
            f"embedding endpoint returned {len(vectors[0])} dimensions; the corpus "
            f"vectors are {EXPECTED_DIM}-dim bge-m3, so this model cannot be compared "
            "against them"
        )
    return vectors


def embed_one(text: str) -> list[float]:
    vectors = embed_many([text])
    if not vectors:
        raise EmbeddingUnavailable("the embedding endpoint returned nothing")
    return vectors[0]


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------

def _norm(vector: list[float]) -> float:
    return math.sqrt(sum(v * v for v in vector)) or 1.0


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (_norm(a) * _norm(b))


def _tokens(text: str) -> set[str]:
    return {
        w for w in _WORD_RE.findall((text or "").lower())
        if len(w) > 2 and w not in _STOP
    }


def keyword_score(query: str, text: str) -> float:
    """Token-overlap fallback, scaled into the same 0..1 range as cosine.

    Deliberately conservative. It is compared against the SAME threshold the
    vector path uses (0.55), and an overlap score that ran hot would let a weak
    keyword hit pass a gate calibrated for semantic similarity -- which is how a
    degraded search starts quietly returning the wrong note instead of saying it
    found nothing.
    """
    q, t = _tokens(query), _tokens(text)
    if not q or not t:
        return 0.0
    overlap = len(q & t)
    # Jaccard-ish, biased towards covering the query rather than matching length.
    return overlap / len(q)


class DocumentIndex:
    """Lazily-built vector index over one uploaded document's chunks.

    Held on the ``UploadedDocument`` so it survives across turns of the same
    conversation. ``degraded`` is what the caller surfaces to the model: it means
    the ranking came from token overlap, not embeddings, and the answer should
    say the search was less precise rather than presenting it as equivalent.
    """

    def __init__(self) -> None:
        self.vectors: dict[str, list[float]] = {}
        self.degraded = False
        self._attempted = False

    def ensure(self, chunks: list[dict]) -> None:
        """Embed anything not already embedded. Never raises."""
        if self._attempted and self.degraded:
            # One outage per document is enough; retrying on every question
            # adds a 60s timeout to each one.
            return
        pending = [c for c in chunks if c.get("chunk_id") not in self.vectors]
        if not pending:
            return
        self._attempted = True
        try:
            vectors = embed_many([c.get("content") or "" for c in pending])
        except EmbeddingUnavailable as exc:
            self.degraded = True
            logger.warning(
                "upload narrative search degraded to keyword matching (%s)", exc
            )
            return
        for chunk, vector in zip(pending, vectors):
            self.vectors[chunk["chunk_id"]] = vector
        self.degraded = False

    def rank(self, query: str, chunks: list[dict]) -> list[tuple[dict, float]]:
        """``(chunk, score)`` sorted best-first, by cosine or by overlap."""
        self.ensure(chunks)

        if not self.degraded and self.vectors:
            try:
                query_vector = embed_one(query)
            except EmbeddingUnavailable as exc:
                logger.warning("query embedding failed (%s); using keyword overlap", exc)
                self.degraded = True
            else:
                scored = [
                    (c, cosine(query_vector, self.vectors.get(c.get("chunk_id"), [])))
                    for c in chunks
                ]
                return sorted(scored, key=lambda p: p[1], reverse=True)

        scored = [(c, keyword_score(query, c.get("content") or "")) for c in chunks]
        return sorted(scored, key=lambda p: p[1], reverse=True)
