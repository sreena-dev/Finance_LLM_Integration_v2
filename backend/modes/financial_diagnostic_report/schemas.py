"""Request/response contracts for the Financial Diagnostic Report mode.

Defined here rather than added to `app/schemas.py` because this mode's query is
not the shared chat shape: it is scoped to an entity the operator selected, and
its response carries the provenance of the read and the classified intent, both
of which the generic `QueryResponse` has nowhere to put. Keeping them local also
means this mode can evolve its contract without touching a file three other
modes depend on.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


def _normalise(value: str) -> str:
    """Strip trailing whitespace per line, not just from the whole string.

    Same normalisation, and for the same measured reason, as
    `app.schemas.QueryRequest`: an invisible space before a newline survives
    `.strip()` and changes downstream tokenisation. This path has no LLM in it,
    but the text is matched against derived vocabularies where a stray token is
    equally capable of shifting a score across the margin.
    """
    return "\n".join(line.rstrip() for line in value.splitlines()).strip()


class FDRQueryRequest(BaseModel):
    """One question about one entity.

    `entity_id` is required and is never inferred from the question text. The UI
    has an entity picker, so the entity is a selection rather than a guess —
    which removes the entire class of failure where one entity's figures are
    served under another's name.
    """

    entity_id: str = Field(..., min_length=1, max_length=200)
    query: str = Field(..., min_length=1, max_length=2000)
    # Bypass the in-process latency cache and re-read the filings. For the case
    # where the corpus changed under a long-lived process.
    refresh: bool = False

    @field_validator("query")
    @classmethod
    def _clean_query(cls, value: str) -> str:
        return _normalise(value)

    @field_validator("entity_id")
    @classmethod
    def _clean_entity(cls, value: str) -> str:
        return value.strip()


class FDRQueryResponse(BaseModel):
    mode: str
    entity_id: str
    query: str
    # overview | cluster | signal | figure | coverage | blocked
    # | ambiguous | refused | unsupported
    kind: str
    answer: str
    rows: list[dict] = []
    # Where the figures came from and how old the read is. Present on every
    # answer that touched the corpus, so a stale read is visible in the response
    # rather than inferred from its absence.
    provenance: dict = {}
    # Numbered sources behind a generated answer, each with the filing, the
    # chunk it came from, its page and its rerank score. Empty on the
    # deterministic path, which cites the statement inside its own prose.
    sources: list[dict] = []
    # The classified subject, its closed reason code, and the evidence it was
    # read from — so a wrong answer can be diagnosed as a wrong reading rather
    # than guessed at.
    intent: dict = {}
    elapsed_seconds: float = 0.0


class FDREntity(BaseModel):
    entity_id: str
    filings: int
    first_fy: int | None = None
    last_fy: int | None = None
    first_fy_label: str = ""
    last_fy_label: str = ""
    trend_capable: bool = False
    min_trend_years: int = 3


class FDREntitiesResponse(BaseModel):
    entities: list[FDREntity]
    count: int
