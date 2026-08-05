"""Builds the OpenAI-compatible gemma LLM client for Yukta agents."""

from __future__ import annotations

from yukta.core.Clients.remote_client import RemoteEndpointClient

from yukta_rag.core.config import (
    GENERATION_BASE_URL,
    GENERATION_MAX_OUTPUT_TOKENS,
    GENERATION_MODEL,
    GENERATION_TIMEOUT,
)


def build_llm(temperature: float = 0.2, max_tokens: int = GENERATION_MAX_OUTPUT_TOKENS,
             timeout: int | None = None) -> RemoteEndpointClient:
    """Return a RemoteEndpointClient pointed at the gemma endpoint.

    ``RemoteEndpointClient`` appends ``/v1/chat/completions`` to ``base_url``,
    so we strip a trailing ``/v1`` from the configured URL to avoid ``/v1/v1``.
    ``max_tokens`` is set explicitly so a low server-side default can't clip a
    long synthesis answer. ``timeout`` defaults to GENERATION_TIMEOUT (raised
    above the client's 300s default so a slow local model doesn't fail
    mid-generation with ReadTimeout) but callers making many small, bounded-output
    calls in a row (e.g. per-year statement extraction) can pass a tighter one.
    """
    base = GENERATION_BASE_URL.rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")].rstrip("/")
    return RemoteEndpointClient(
        model_name=GENERATION_MODEL,
        base_url=base,
        api_key="not-needed",
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout if timeout is not None else GENERATION_TIMEOUT,
    )
