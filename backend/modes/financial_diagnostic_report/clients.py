"""HTTP client for the generation endpoint (OpenAI-compatible `/v1/chat/completions`).

Built on `urllib` on purpose: this mode makes one kind of call, and an SDK would
add a dependency to manage for no capability it uses.

FAILURE IS LOUD AND SPECIFIC
---------------------------
Every failure raises `EndpointError` naming what failed and why. Callers (the
narrative blocks) catch it and fall back to the deterministic path, logging the
reason - so "the model was down" is never confusable with "the model said nothing".

THE API KEY NEVER LEAVES THIS FILE
----------------------------------
It is attached as an `Authorization` header and nowhere else. Error details are
built from status codes and exception *types* - never from request headers or the
server's response body - and every message passes through `_redact` as a final
guard before it can reach a log line, an exception or an HTTP response.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from typing import Any

from . import config as CFG

# Transient: worth retrying. A 4xx other than 408/429 means the request itself is
# wrong (bad key, bad model) and repeating it cannot help.
_RETRY_STATUS = frozenset({408, 429, 500, 502, 503, 504})
_BACKOFF_SECONDS = 0.5
_PROBE_TIMEOUT_SECONDS = 5.0


class EndpointError(RuntimeError):
    """The generation endpoint failed. Carries text fit to show an operator."""

    def __init__(self, what: str, detail: str):
        self.what = what
        self.detail = detail
        super().__init__(f"{what}: {detail}")


def _fail(settings: CFG.Settings, detail: str) -> EndpointError:
    key = settings.llm_api_key
    if key:
        detail = detail.replace(key, "***")
    return EndpointError("generation endpoint", detail)


def _headers(settings: CFG.Settings) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"
    return headers


def _post_once(settings: CFG.Settings, payload: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        f"{settings.llm_url}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=_headers(settings), method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def chat(messages: list[dict], *, max_tokens: int | None = None,
         temperature: float = 0.0, timeout: float | None = None,
         retries: int | None = None) -> str:
    """One completion. Temperature 0 by default: on an audit surface the same
    evidence should give the same answer.

    Retries transient failures (timeouts, connection errors, 408/429/5xx) with
    exponential backoff; anything else fails immediately. A completion cut off at
    `max_tokens` raises rather than returning half a JSON document.
    """
    settings = CFG.load()
    if not settings.llm_url:
        raise _fail(settings, "not configured (set GENERATION_BASE_URL)")
    if not settings.llm_model:
        raise _fail(settings, "not configured (set GENERATION_MODEL)")

    payload = {"model": settings.llm_model, "messages": messages,
               "max_tokens": max_tokens or settings.llm_max_tokens,
               "temperature": temperature}

    attempts = (settings.llm_retries if retries is None else retries) + 1
    wait = settings.llm_timeout if timeout is None else timeout
    for attempt in range(1, attempts + 1):
        try:
            body = _post_once(settings, payload, wait)
        except urllib.error.HTTPError as exc:
            if exc.code in _RETRY_STATUS and attempt < attempts:
                time.sleep(_BACKOFF_SECONDS * 2 ** (attempt - 1))
                continue
            hint = " - check GENERATION_API_KEY" if exc.code in (401, 403) else ""
            raise _fail(settings, f"HTTP {exc.code}{hint}") from None
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
            if attempt < attempts:
                time.sleep(_BACKOFF_SECONDS * 2 ** (attempt - 1))
                continue
            reason = getattr(exc, "reason", exc)
            raise _fail(settings, f"unreachable ({type(reason).__name__})") from None
        except ValueError:
            raise _fail(settings, "response was not valid JSON") from None

        try:
            choice = body["choices"][0]
            content = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise _fail(settings, "unexpected response shape") from None
        if choice.get("finish_reason") == "length":
            raise _fail(settings, f"output truncated at max_tokens={payload['max_tokens']}")
        return content

    raise _fail(settings, "exhausted retries")


def probe() -> dict[str, Any]:
    """Reachability of the generation endpoint, for the health route. Never raises
    and never returns more than a reachable flag and a redacted reason."""
    try:
        # A health check must answer quickly whether or not the model does: a short
        # timeout and no retries, unlike a report build, which waits for the model.
        chat([{"role": "user", "content": "Reply with exactly: ok"}], max_tokens=16,
             timeout=_PROBE_TIMEOUT_SECONDS, retries=0)
        return {"reachable": True, "reason": None}
    except EndpointError as exc:
        return {"reachable": False, "reason": exc.detail}
    except Exception as exc:  # noqa: BLE001
        return {"reachable": False, "reason": type(exc).__name__}
