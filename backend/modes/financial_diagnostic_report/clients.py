"""HTTP clients for the embedding and generation endpoints.

Deliberately built on `urllib` rather than an SDK. Both services speak the
OpenAI-compatible shape, the calls are two POSTs, and the gateway already has
enough dependencies — an SDK here would add a version to manage for no
capability this mode uses.

FAILURE IS LOUD AND SPECIFIC
---------------------------
Every failure raises `EndpointError` carrying which endpoint failed and why, and
the caller turns that into a 503 with that text. This matters more than usual on
this mode: the deterministic path answers with no model at all, so "the LLM is
down" must never be reported as "the filings do not say" — those are opposite
statements about the entity and they must never be confusable.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Iterator

from . import config as CFG


class EndpointError(RuntimeError):
    """A model endpoint failed. Carries text fit to show an operator."""

    def __init__(self, what: str, detail: str):
        self.what = what
        self.detail = detail
        super().__init__(f"{what}: {detail}")


def _generation_headers() -> dict[str, str]:
    """Headers for the GENERATION endpoint only.

    That server authenticates; the embedding and reranker servers are separate
    hosts on the same network and neither asks for a key, so the header is not
    sent to them -- an unexpected bearer token is a thing a server is entitled to
    reject. The key is omitted entirely when unset rather than sent empty, which
    is what the other modes already do (see agent._make_llm_client).

    Without this the whole retrieval path failed with "unreachable
    (Unauthorized)": the diagnostics still answered, so the mode looked healthy,
    while every narrative question 401'd.
    """
    headers = {"Content-Type": "application/json"}
    key = (os.environ.get("GENERATION_API_KEY") or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _post(url: str, payload: dict, timeout: float,
          headers: dict[str, str] | None = None) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers=headers or {"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def embed(text: str) -> list[float]:
    """One query embedding from the bge-m3 endpoint.

    The corpus vectors were built with bge-m3 at 1024 dimensions, so this must
    stay the same model: an embedding from a different model is not merely
    worse, it is meaningless against these vectors. The dimension is checked for
    exactly that reason.
    """
    settings = CFG.load()
    if not settings.embedding_url:
        raise EndpointError("embedding endpoint", "not configured (set EMBEDDING_BASE_URL)")
    try:
        body = _post(f"{settings.embedding_url.rstrip('/')}/v1/embeddings",
                     {"model": settings.embedding_model, "input": text},
                     settings.embedding_timeout)
        vector = body["data"][0]["embedding"]
    except urllib.error.URLError as exc:
        raise EndpointError("embedding endpoint", f"unreachable ({exc.reason})") from exc
    except (KeyError, IndexError, ValueError) as exc:
        raise EndpointError("embedding endpoint", f"unexpected response ({exc})") from exc

    if len(vector) != 1024:
        raise EndpointError(
            "embedding endpoint",
            f"returned {len(vector)} dimensions; the corpus vectors are 1024-dim bge-m3, "
            f"so this model cannot be compared against them")
    return vector


def embed_many(texts: list[str]) -> list[list[float]]:
    """Embed several query phrasings in ONE round trip.

    Expansion multiplies the number of vectors needed per question, and issuing
    them one at a time turned a 0.3s stage into a multi-second one for no reason
    — the endpoint accepts a list. Same dimension check as `embed`, applied to
    the first vector.
    """
    if not texts:
        return []
    settings = CFG.load()
    if not settings.embedding_url:
        raise EndpointError("embedding endpoint", "not configured (set EMBEDDING_BASE_URL)")
    try:
        body = _post(f"{settings.embedding_url.rstrip('/')}/v1/embeddings",
                     {"model": settings.embedding_model, "input": texts},
                     settings.embedding_timeout)
        # `data` is not guaranteed to come back in request order, so it is
        # re-sorted by the index the API returns rather than trusted as-is.
        rows = sorted(body["data"], key=lambda d: d.get("index", 0))
        vectors = [r["embedding"] for r in rows]
    except urllib.error.URLError as exc:
        raise EndpointError("embedding endpoint", f"unreachable ({exc.reason})") from exc
    except (KeyError, IndexError, ValueError) as exc:
        raise EndpointError("embedding endpoint", f"unexpected response ({exc})") from exc

    if vectors and len(vectors[0]) != 1024:
        raise EndpointError(
            "embedding endpoint",
            f"returned {len(vectors[0])} dimensions; the corpus vectors are 1024-dim "
            f"bge-m3, so this model cannot be compared against them")
    return vectors


def to_pgvector(vector: list[float]) -> str:
    """pgvector's text input form. Cast `::vector` at the call site."""
    return "[" + ",".join(f"{v:.7f}" for v in vector) + "]"


def chat(messages: list[dict], *, max_tokens: int | None = None,
         temperature: float = 0.0) -> str:
    """One completion. Temperature 0 by default — on an audit surface the same
    question and the same evidence should give the same answer."""
    settings = CFG.load()
    if not settings.llm_url:
        raise EndpointError("generation endpoint", "not configured (set GENERATION_BASE_URL)")
    try:
        body = _post(
            f"{settings.llm_url.rstrip('/')}/v1/chat/completions",
            {"model": settings.llm_model, "messages": messages,
             "max_tokens": max_tokens or settings.llm_max_tokens,
             "temperature": temperature},
            settings.llm_timeout, headers=_generation_headers())
        return body["choices"][0]["message"]["content"] or ""
    except urllib.error.URLError as exc:
        raise EndpointError("generation endpoint", f"unreachable ({exc.reason})") from exc
    except (KeyError, IndexError, ValueError) as exc:
        raise EndpointError("generation endpoint", f"unexpected response ({exc})") from exc


def chat_stream(messages: list[dict], *, max_tokens: int | None = None,
                temperature: float = 0.0) -> Iterator[str]:
    """Token deltas as the model produces them.

    THIS is where streaming is real. The deterministic path has nothing to
    stream because its answer is computed whole; a generated answer genuinely
    arrives a piece at a time, so the pieces are passed straight through.
    """
    settings = CFG.load()
    if not settings.llm_url:
        raise EndpointError("generation endpoint", "not configured (set GENERATION_BASE_URL)")

    payload = {"model": settings.llm_model, "messages": messages,
               "max_tokens": max_tokens or settings.llm_max_tokens,
               "temperature": temperature, "stream": True}
    request = urllib.request.Request(
        f"{settings.llm_url.rstrip('/')}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=_generation_headers(), method="POST")

    try:
        with urllib.request.urlopen(request, timeout=settings.llm_timeout) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    delta = json.loads(data)["choices"][0].get("delta", {})
                except (KeyError, IndexError, ValueError):
                    continue
                piece = delta.get("content")
                if piece:
                    yield piece
    except urllib.error.URLError as exc:
        raise EndpointError("generation endpoint", f"unreachable ({exc.reason})") from exc


def probe() -> dict[str, Any]:
    """Reachability of both endpoints, for the health route. Never raises."""
    out: dict[str, Any] = {}
    try:
        embed("ping")
        out["embedding"] = {"reachable": True, "reason": None}
    except Exception as exc:  # noqa: BLE001
        out["embedding"] = {"reachable": False, "reason": str(exc)}
    try:
        chat([{"role": "user", "content": "ok"}], max_tokens=4)
        out["generation"] = {"reachable": True, "reason": None}
    except Exception as exc:  # noqa: BLE001
        out["generation"] = {"reachable": False, "reason": str(exc)}
    return out
