"""bge-m3 query embedding client."""

from __future__ import annotations

import requests

from yukta_rag.core.config import EMBEDDING_MODEL, EMBEDDING_URL


def embed_query(text: str) -> list[float]:
    """Embed a single query string into its 1024-dim bge-m3 vector."""
    resp = requests.post(
        EMBEDDING_URL,
        json={"model": EMBEDDING_MODEL, "input": [text]},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of strings into 1024-dim bge-m3 vectors (order preserved).

    Used when ingesting uploaded-PDF page chunks. Mirrors the call shape proven
    in embed_and_ingest.py: the response ``data`` may arrive out of order, so it
    is re-sorted by ``index`` before the embeddings are returned.
    """
    if not texts:
        return []
    resp = requests.post(
        EMBEDDING_URL,
        json={"model": EMBEDDING_MODEL, "input": texts},
        timeout=120,
    )
    resp.raise_for_status()
    data = sorted(resp.json()["data"], key=lambda d: d["index"])
    return [d["embedding"] for d in data]


def to_vector_literal(text: str) -> str:
    """Embed ``text`` and return a pgvector literal string ``[v1,v2,...]``."""
    return "[" + ",".join(str(x) for x in embed_query(text)) + "]"
