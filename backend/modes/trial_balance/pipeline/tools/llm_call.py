"""LLM call helpers shared by classification.py/staged_narrowing.py/
validation_gate.py: extracting text from either response shape TB-v2-git's
own LLM client can return, and a retry-once-on-completely-empty-batch
wrapper.

Ported from TB_normalization_v1's core/llm_client.py.
"""

from __future__ import annotations

from typing import Callable, TypeVar

T = TypeVar("T")

# One retry, not a general N-attempt loop. Targets one specific failure
# mode (temperature=0 does not guarantee a reproducible response on a real
# vLLM serving stack -- floating-point non-associativity from continuous/
# dynamic batching can occasionally return a completely empty result even
# though the same prompt resolves fine on retry) -- never retries a
# partial result.
EMPTY_RESULT_RETRIES = 1


def read_content(response) -> str:
    """Extracts the text payload from either a dict-shaped response
    (`{"content": "..."}`) or an object with a `.content` attribute --
    modes.trial_balance.pipeline.agent.SafeVLLMClient.generate() returns the latter. Returns ""
    for anything else."""
    if isinstance(response, dict):
        return response.get("content", "") or ""
    return getattr(response, "content", "") or ""


def call_with_empty_batch_retry(call_once: Callable[[], dict], *, max_retries: int = EMPTY_RESULT_RETRIES) -> dict:
    """Runs `call_once` (which must itself never raise -- callers wrap
    their own LLM call + parse + validate in a try/except and return {} on
    any failure) up to `max_retries + 1` times, stopping as soon as a
    non-empty dict comes back. Retries ONLY on a completely empty result --
    never on a partial one, and never more than `max_retries` times."""
    result: dict = {}
    for _ in range(max_retries + 1):
        result = call_once()
        if result:
            return result
    return result
