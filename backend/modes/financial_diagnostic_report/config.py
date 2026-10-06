"""Settings for the Financial Diagnostic Report mode.

The report's figures come from as_db (`pipeline/xbrl_fetch/`, which reads its own
`XBRLAS_*` variables). This module owns two things: the request timeout the
`/xbrl/report/download` route waits on, and the generation endpoint the narrative
blocks call through `clients.chat`.

SECRET HANDLING
---------------
`GENERATION_API_KEY` is read from the environment only (the gateway loads the
gitignored root `.env` at start-up). It is held in a field excluded from `repr`,
and `Settings.__repr__` is overridden, so printing or logging a `Settings` object
- or having one appear in a traceback or debugger dump - cannot reveal it.
Nothing in this mode writes the key anywhere else.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlparse


def _int(key: str, default: int, *, low: int, high: int) -> int:
    """An integer setting, clamped. A malformed value falls back rather than
    raising: a typo in .env must not take a mode down at import time."""
    try:
        return max(low, min(high, int(os.environ.get(key, "").strip() or default)))
    except ValueError:
        return default


def _str(key: str) -> str:
    return (os.environ.get(key) or "").strip()


def _base_url(raw: str) -> str:
    """A usable http(s) base URL, or "" when unset/malformed (reported as not
    configured, never half-used). A trailing slash or a pasted `/v1` is dropped so
    the client can append the route itself."""
    raw = raw.strip().rstrip("/")
    if raw.endswith("/v1"):
        raw = raw[:-3]
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return raw


@dataclass(frozen=True)
class Settings:
    # How long one as_db report build may run before the request gives up.
    evaluate_timeout_seconds: int
    llm_url: str
    llm_model: str
    llm_timeout: int
    llm_max_tokens: int
    llm_retries: int
    llm_api_key: str = field(default="", repr=False)

    def __repr__(self) -> str:
        return (f"Settings(llm_url={self.llm_url!r}, llm_model={self.llm_model!r}, "
                f"llm_api_key={'<set>' if self.llm_api_key else '<unset>'}, ...)")

    __str__ = __repr__


def load() -> Settings:
    return Settings(
        evaluate_timeout_seconds=_int("FDR_EVALUATE_TIMEOUT_SECONDS", 180, low=10, high=900),
        llm_url=_base_url(_str("GENERATION_BASE_URL")),
        llm_model=_str("GENERATION_MODEL"),
        llm_timeout=_int("GENERATION_TIMEOUT", 120, low=5, high=600),
        llm_max_tokens=_int("GENERATION_MAX_OUTPUT_TOKENS", 1536, low=16, high=8192),
        llm_retries=_int("GENERATION_RETRIES", 2, low=0, high=5),
        llm_api_key=_str("GENERATION_API_KEY"),
    )
