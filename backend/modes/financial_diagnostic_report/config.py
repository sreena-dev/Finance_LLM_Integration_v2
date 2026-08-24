"""Connection and tuning settings for the Financial Diagnostic Report mode.

WHY THIS FILE EXISTS RATHER THAN EDITING THE VENDORED PACKAGE
-------------------------------------------------------------
`pipeline/fs_db/` is vendored verbatim so it can be re-pulled from the source
repo without a merge conflict. It reads its connection from discrete `FSDB_PG_*`
environment variables, while this project configures every other mode with a
single DSN URL (`FINANCE_DSN`, `FINANCE_LLM_DSN`). Bridging those two
conventions here — by exporting the variables the vendored package expects,
before it is imported — keeps the vendored tree at ZERO patches.

WHICH DATABASE
--------------
The same `finance_llm` filings database the Statutory Auditor's Report mode
already uses. It holds `documents` (one row per filing) and `table_chunks` (the
extracted statement tables). No new database and no new required environment
variable: with `FINANCE_DSN` set — which it must be for SAR — this mode
connects. `FDR_DSN` overrides it for the case where the FDR is later pointed at
a different corpus.

WHAT IS DELIBERATELY ABSENT
---------------------------
No LLM endpoint, no embedding endpoint, no vector store, no local SQLite file.
This mode reads Postgres and evaluates pure functions over what it read. That
is the whole dependency list, and it is why the mode can be available while the
generation endpoints every other mode needs are down.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

# The environment variables the vendored `fs_db.config` reads.
_FSDB_VARS = ("FSDB_PG_HOST", "FSDB_PG_PORT", "FSDB_PG_DB", "FSDB_PG_USER",
              "FSDB_PG_PASSWORD", "FSDB_PG_TIMEOUT")


def _dsn() -> str | None:
    """The filings DSN, most specific first.

    `FDR_DSN` exists so this mode can be pointed at a different corpus without
    disturbing SAR; in the normal deployment it is unset and both modes read the
    one database.
    """
    for key in ("FDR_DSN", "FINANCE_DSN", "FINANCE_LLM_DSN"):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    return None


def _int(key: str, default: int, *, low: int, high: int) -> int:
    """An integer setting, clamped. A malformed value falls back rather than
    raising: a typo in .env must not take a mode down at import time."""
    try:
        return max(low, min(high, int(os.environ.get(key, "").strip() or default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    dsn: str | None
    # How many of an entity's most recent filings to read. The trend rules need
    # three comparable years (`fdr.panel.MIN_TREND_YEARS`); five is the spec's
    # default window and the point past which extra years cost time and change
    # almost nothing.
    max_years: int
    # standalone | consolidated. The panel refuses to mix them, so this picks
    # which series is built.
    flavor: str
    # Seconds an evaluated entity stays reusable in memory. This is a latency
    # cache over live data, NOT a store of record — see `engine.py`. Every
    # answer reports the age of what it read, so a stale read is visible rather
    # than silent.
    cache_ttl_seconds: int
    # Evaluated entities held in memory at once. Each is a report payload —
    # tens of KB — so this is a bound on memory, not on correctness.
    cache_max_entries: int
    # How long one entity's extraction may run before the request gives up.
    # Extraction is ~1.4s per filing, so five filings is well inside this; the
    # ceiling exists to fail a wedged database read rather than hold a worker.
    evaluate_timeout_seconds: int

    # ---- the retrieval path ------------------------------------------------
    # Only questions the deterministic path cannot answer reach these. A
    # diagnostic verdict is still computed, never retrieved and never generated.
    embedding_url: str
    embedding_model: str
    embedding_timeout: float
    llm_url: str
    llm_model: str
    llm_timeout: float
    llm_max_tokens: int
    # Evidence budget. Ported from the source project, which measured these
    # against the same corpus: 7 sources at 2,600 characters fits the context
    # with room for the answer, and a bigger budget bought no better answers.
    max_evidence: int
    per_source_chars: int
    total_budget_chars: int
    # Candidates gathered before reranking. The cross-encoder is the expensive
    # step and is ~linear in candidates, so the arms cast wide and the reranker
    # cuts down to `max_evidence`.
    rerank_pool: int
    rerank_max_len: int
    # A candidate whose cross-encoder logit is below this is not evidence. The
    # boundary sits near -1.0 on this corpus: measured here, a matching table
    # scored +5.4 and an unrelated governance passage -11.0. Set conservatively,
    # because false-abstaining on an answerable question is the worse failure.
    rerank_min_logit: float
    # How much the cross-encoder is allowed to move the fused ranking. Small on
    # purpose: a general-purpose relevance model refines a domain-aware ranking,
    # it does not replace it.
    rerank_blend: float
    rerank_enabled: bool
    # Verify the generated answer against its own cited sources with a second
    # model call. The source project asserted groundedness on this path without
    # checking it; this actually checks.
    # Retry a failed retrieval with LLM-reformulated queries. Adaptive, not
    # unconditional: it costs a round trip and only helps when the cheap pass
    # already failed.
    adaptive_expansion: bool
    query_expansion: bool
    # Total query phrasings sent to the arms, original included. Caps the search
    # fan-out: each one costs a vector query and a full-text query per arm.
    max_query_variants: int
    groundedness_check: bool
    # "always" | "conditional" | "off". Conditional skips the verifier when the
    # answer is already densely cited — the cheap deterministic proxy agrees with
    # it in that regime, and the call costs a second on every answer.
    groundedness_mode: str
    groundedness_min_coverage: float

    @property
    def configured(self) -> bool:
        return bool(self.dsn)

    @property
    def retrieval_configured(self) -> bool:
        return bool(self.embedding_url and self.llm_url)


def _first(*keys: str, default: str = "") -> str:
    """The first environment variable of these that is set and non-empty.

    The retrieval endpoints are already configured for other modes, under names
    those modes chose. Reading their variables rather than inventing new ones
    means this mode needs no new .env entries — and, more importantly, that it
    cannot end up pointed at a different embedding model than the one that built
    the corpus vectors because somebody updated one variable and not the other.
    """
    for key in keys:
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    return default


def _flag(key: str, default: bool) -> bool:
    raw = (os.environ.get(key) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, "").strip() or default)
    except ValueError:
        return default


def load() -> Settings:
    return Settings(
        dsn=_dsn(),
        max_years=_int("FDR_MAX_YEARS", 5, low=2, high=10),
        flavor=(os.environ.get("FDR_FLAVOR", "").strip() or "standalone"),
        cache_ttl_seconds=_int("FDR_CACHE_TTL_SECONDS", 900, low=0, high=86_400),
        cache_max_entries=_int("FDR_CACHE_MAX_ENTRIES", 24, low=1, high=512),
        evaluate_timeout_seconds=_int("FDR_EVALUATE_TIMEOUT_SECONDS", 180, low=10, high=900),

        embedding_url=_first("FDR_EMBEDDING_BASE_URL", "EMBEDDING_BASE_URL"),
        embedding_model=_first("FDR_EMBEDDING_MODEL", "EMBEDDING_MODEL", default="bge-m3"),
        embedding_timeout=_float("FDR_EMBEDDING_TIMEOUT", 20.0),
        llm_url=_first("FDR_GENERATION_BASE_URL", "GENERATION_BASE_URL", "LLM_BASE_URL"),
        llm_model=_first("FDR_GENERATION_MODEL", "GENERATION_MODEL", "LLM_MODEL_NAME",
                         default="gemma-4-26b-a4b-it"),
        llm_timeout=_float("FDR_GENERATION_TIMEOUT", 180.0),
        llm_max_tokens=_int("FDR_GENERATION_MAX_TOKENS", 1536, low=256, high=8192),

        max_evidence=_int("FDR_MAX_EVIDENCE", 7, low=1, high=20),
        per_source_chars=_int("FDR_PER_SOURCE_CHARS", 2600, low=500, high=20_000),
        total_budget_chars=_int("FDR_TOTAL_BUDGET_CHARS", 9000, low=1000, high=60_000),
        rerank_pool=_int("FDR_RERANK_POOL", 16, low=4, high=100),
        rerank_max_len=_int("FDR_RERANK_MAX_LEN", 256, low=64, high=512),
        rerank_min_logit=_float("FDR_RERANK_MIN_LOGIT", -1.0),
        rerank_blend=_float("FDR_RERANK_BLEND", 0.15),
        rerank_enabled=_flag("FDR_RERANK_ENABLED", True),
        adaptive_expansion=_flag("FDR_ADAPTIVE_EXPANSION", True),
        query_expansion=_flag("FDR_QUERY_EXPANSION", True),
        max_query_variants=_int("FDR_MAX_QUERY_VARIANTS", 3, low=1, high=8),
        groundedness_check=_flag("FDR_GROUNDEDNESS_CHECK", True),
        groundedness_mode=_first("FDR_GROUNDEDNESS_MODE", default="conditional"),
        groundedness_min_coverage=_float("FDR_GROUNDEDNESS_MIN_COVERAGE", 0.5),
    )


def export_fsdb_env(dsn: str) -> None:
    """Translate a DSN into the discrete variables the vendored `fs_db` reads.

    Called once, before `fs_db` is first imported. Existing `FSDB_PG_*` values
    win: an operator who set them explicitly is overriding the DSN on purpose,
    and silently discarding that would make the override look broken.
    """
    if any(os.environ.get(v) for v in _FSDB_VARS):
        return

    parsed = urlparse(dsn)
    if parsed.scheme not in ("postgres", "postgresql"):
        raise ValueError(
            f"FDR filings DSN must be a postgresql:// URL, got {parsed.scheme or 'no'} scheme"
        )
    if not parsed.hostname:
        raise ValueError("FDR filings DSN has no host")

    database = (parsed.path or "").lstrip("/")
    if not database:
        raise ValueError("FDR filings DSN names no database")

    os.environ["FSDB_PG_HOST"] = parsed.hostname
    os.environ["FSDB_PG_PORT"] = str(parsed.port or 5432)
    os.environ["FSDB_PG_DB"] = database
    # Credentials are percent-encoded in a URL; the driver wants them raw.
    os.environ["FSDB_PG_USER"] = unquote(parsed.username or "")
    os.environ["FSDB_PG_PASSWORD"] = unquote(parsed.password or "")
    os.environ.setdefault("FSDB_PG_TIMEOUT", "6")
