"""Native embedding client for TB-v2-git's own classification engine.

Ported from TB_normalization_v1's taxonomy/embedding_client.py, reading
TB-v2-git's own settings instead of a second .env file -- this repo has no
runtime dependency on TB_normalization_v1's config or process.

Only call site: backend/tools/taxonomy_repository.py's
get_candidates_with_scores_embedding (query-time, once per GL row).
Compiling/embedding the taxonomy itself is TB_normalization_v1's job --
this repo only ever reads the taxonomy_embedding table it already
populated, never writes to it.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass

from modes.trial_balance.pipeline.config import settings


class EmbeddingConfigError(Exception):
    """EMBEDDING_BASE_URL is not configured."""


class EmbeddingDimensionError(Exception):
    """The embedding endpoint returned a vector whose length doesn't match
    EMBEDDING_DIM -- caught here rather than silently stored, since a
    dimension mismatch would corrupt every later similarity comparison."""


@dataclass(frozen=True)
class EmbeddingClient:
    base_url: str
    model: str
    api_key: str
    dim: int
    timeout: float = 30.0

    def embed(self, texts: list[str]) -> list[list[float]]:
        """One HTTP call for the whole batch (not one per text)."""
        if not texts:
            return []
        payload = json.dumps({"input": texts, "model": self.model}).encode("utf-8")
        request = urllib.request.Request(
            self.base_url,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        by_index = {item["index"]: item["embedding"] for item in data["data"]}
        vectors = [by_index[i] for i in range(len(texts))]
        for vector in vectors:
            if len(vector) != self.dim:
                raise EmbeddingDimensionError(
                    f"Expected {self.dim}-dim embeddings from {self.model!r}, got {len(vector)}."
                )
        return vectors


def default_embedding_client() -> EmbeddingClient:
    if not settings.EMBEDDING_BASE_URL:
        raise EmbeddingConfigError("EMBEDDING_BASE_URL is not configured.")
    return EmbeddingClient(
        base_url=settings.EMBEDDING_BASE_URL,
        model=settings.EMBEDDING_MODEL,
        api_key=settings.EMBEDDING_API_KEY,
        dim=settings.EMBEDDING_DIM,
        timeout=settings.HTTP_TIMEOUT,
    )
