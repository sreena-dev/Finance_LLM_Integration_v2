"""Turn a follow-up into a question that stands on its own.

WHY THIS IS NEEDED
------------------
`Orchestrator.answer(query, conn, conn_reports)` is stateless, and the branch
builds a fresh agent per request with `auto_save_chat_history=False`. "What about
the prior year?" therefore arrives with nothing to resolve "the prior year"
against, and the pipeline answers it as if it were the first thing ever asked.

THE PATTERN IS ALREADY PROVEN IN THIS REPO
-------------------------------------------
SAR Q&A does exactly this: `SARChatPipeline.ask` formats the last eight turns
and hands them to a Rewriter agent whose only job is to produce a standalone
question, *before* retrieval runs. This mirrors that. What differs is where it
lives — in the mode directory rather than inside `pipeline/` — which keeps the
vendored Financial_Statement tree byte-for-byte re-pullable and leaves
`Orchestrator.answer`'s signature (and its `python agent.py` self-test) untouched.

THE GUARDRAILS ARE NOT OPTIONAL
--------------------------------
`app/schemas.py` documents a measured case on THIS pipeline where a single
trailing space before a newline changed the agent's first tool choice and turned
twenty consecutive successes into three consecutive failures. A rewriter is a far
larger perturbation of that same input, so it is fenced in:

  * **Empty history returns the query byte-for-byte, with no model call.** First
    turns behave exactly as they do today. This alone means the feature cannot
    regress single-shot questions at all.
  * A rewrite that comes back empty, or much longer than the original, or that
    shares no content words with it, is discarded and the original stands.
  * Any failure at all — endpoint down, timeout, bad shape — degrades to the
    original question rather than failing the request.

It is also told, explicitly, to reproduce a company name exactly as it was
written earlier and never to expand it into a legal name. That rule is what stops
this feature from re-creating the entity bug the resolver was just fixed for:
`entity_resolution.py` would now cope either way, but the two must not disagree.
"""

from __future__ import annotations

import logging
import re
import threading

logger = logging.getLogger(__name__)

MAX_TURNS = 8
_MAX_GROWTH = 3.0          # a rewrite may not be more than 3x the original
_MAX_TOKENS = 300

_PROMPT = """You rewrite a follow-up question so it can be understood on its own.

You are given a CONVERSATION HISTORY and the LATEST QUESTION. Rewrite the latest
question as a single self-contained question, resolving anything it refers to
implicitly.

RULES
1. Carry forward the COMPANY and the FINANCIAL YEAR from earlier turns when the
   latest question does not name them.
2. Reproduce a company name EXACTLY as it appeared earlier. Never expand a short
   name into a full legal name, never add "Limited" or "Ltd", never abbreviate a
   long name, and never substitute a different company.
3. Resolve pronouns and ellipsis ("it", "that note", "the prior year", "and for
   2023?") into explicit words.
4. Keep the user's own wording wherever it is already explicit. This is a
   rewrite, not an improvement: do not add topics, do not add qualifiers, and do
   not make the question broader or narrower than it was.
5. If the latest question is already self-contained, return it UNCHANGED.
6. Output the rewritten question only. No preamble, no explanation, no answer,
   no quotes around it.

CONVERSATION HISTORY:
{history}

LATEST QUESTION:
{query}

REWRITTEN QUESTION:"""

_lock = threading.Lock()
_client = None
_unavailable = False


def format_history(turns: list[dict]) -> str:
    """The last few turns as plain text. Same shape as SAR's helper."""
    if not turns:
        return ""
    recent = turns[-MAX_TURNS:]
    return "\n".join(
        f"{str(t.get('role', 'user')).upper()}: {t.get('content', '')}"
        for t in recent
        if (t.get("content") or "").strip()
    )


def _get_client():
    """The pipeline's own LLM client, so this uses the same endpoint and auth.

    Built lazily and once. If it cannot be built, the flag stops us retrying on
    every single turn — a rewriter that is down should cost nothing per request.
    """
    global _client, _unavailable
    if _client is not None or _unavailable:
        return _client

    with _lock:
        if _client is not None or _unavailable:
            return _client
        try:
            import api_server as fs_api  # type: ignore
            _client = fs_api._orchestrator._make_llm_client(max_tokens=_MAX_TOKENS)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "FS rewriter unavailable (%s: %s); follow-ups will be sent "
                "as typed.", type(exc).__name__, exc,
            )
            _unavailable = True
        return _client


_WORD = re.compile(r"[A-Za-z0-9]{4,}")


def _is_sane(original: str, rewritten: str) -> bool:
    """Would this rewrite be safe to send in place of what the user typed?

    Three cheap checks that between them catch the ways a small model fails at
    this: returning nothing, answering the question instead of rewriting it, and
    drifting onto a different subject.
    """
    if not rewritten:
        return False
    if len(rewritten) > max(120, len(original) * _MAX_GROWTH):
        return False

    original_words = {w.lower() for w in _WORD.findall(original)}
    if not original_words:
        return True
    shared = original_words & {w.lower() for w in _WORD.findall(rewritten)}
    return bool(shared)


def rewrite(query: str, history: list[dict]) -> str:
    """A standalone version of `query`, or `query` itself.

    Never raises. Returning the original is always a correct outcome — it is
    what the mode did before this existed.
    """
    query = (query or "").strip()
    if not query:
        return query

    formatted = format_history(history or [])
    if not formatted:
        # The fast path, and the reason this cannot regress first questions.
        return query

    client = _get_client()
    if client is None:
        return query

    prompt = _PROMPT.format(history=formatted, query=query)
    try:
        response = client.generate(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
    except Exception as exc:  # noqa: BLE001 - degrade, never fail the request
        logger.warning("FS rewrite failed (%s: %s); sending the question as "
                       "typed.", type(exc).__name__, exc)
        return query

    text = _extract(response).strip().strip('"').strip()
    if not _is_sane(query, text):
        if text:
            logger.info("FS rewrite rejected as unsound: %r -> %r", query, text)
        return query

    if text != query:
        logger.info("FS rewrite: %r -> %r", query, text)
    return text


def _extract(response) -> str:
    """Pull the text out of whatever shape the client returned.

    Written defensively on purpose: this is the one place that consumes a
    vendored client's response, and an unexpected shape must degrade to "no
    rewrite" rather than raise into the request.
    """
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        for key in ("content", "response", "text", "answer"):
            value = response.get(key)
            if isinstance(value, str) and value.strip():
                return value
        choices = response.get("choices")
        if isinstance(choices, list) and choices:
            message = (choices[0] or {}).get("message") or {}
            if isinstance(message.get("content"), str):
                return message["content"]
    for attr in ("content", "text"):
        value = getattr(response, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    return ""
