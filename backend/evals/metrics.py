"""The metric set: which evaluators run, and what each one is fed.

Two families, kept apart on purpose:

  LLM-judged  — a model's opinion. Recorded with annotator_kind="LLM" so the
                Phoenix UI shows it as a judgement, not a fact.
  Code-based  — deterministic rules. annotator_kind="CODE". These cost nothing,
                never flake, and catch the failures that matter most in an audit
                product (an answer with no citation, an empty response, a run
                that hit the tool-iteration ceiling).

Every judged metric declares what it needs. A trace missing those fields is
skipped for that metric rather than judged on blanks — a faithfulness score
computed against empty context is worse than no score, because it looks like a
measurement.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Callable

from .traces import Trace

logger = logging.getLogger(__name__)


# ===========================================================================
# LLM judge
# ===========================================================================

def build_judge():
    """An LLM handle pointed at the same vLLM the pipelines generate with.

    Same model as the product, by choice: it needs no extra deployment or key,
    and the endpoint is already proven reachable from this network. The tradeoff
    is worth stating — a model judging its own family's output is a weaker
    check than an independent judge, and shared load means eval runs compete
    with live traffic. Point ARTHA_EVAL_* at something else to separate them.
    """
    from phoenix.evals.llm import LLM

    base = (os.environ.get("ARTHA_EVAL_LLM_BASE_URL") or os.environ["GENERATION_BASE_URL"]).rstrip("/")
    model = os.environ.get("ARTHA_EVAL_LLM_MODEL") or os.environ.get("GENERATION_MODEL", "")
    if not base.endswith("/v1"):
        base = base + "/v1"
    client_kwargs = {"base_url": base, "api_key": os.environ.get("ARTHA_EVAL_LLM_API_KEY", "not-needed")}
    return LLM(
        provider="openai",
        model=model,
        sync_client_kwargs=client_kwargs,
        async_client_kwargs=client_kwargs,
    )


@dataclass
class JudgedMetric:
    """One LLM-judged metric and the mapping from a Trace to its inputs."""

    name: str
    evaluator_cls: str          # attribute name in phoenix.evals.metrics
    build_input: Callable[[Trace], dict[str, Any] | None]
    description: str


def _needs(*values: str) -> bool:
    return all(v and v.strip() for v in values)


# --- RAG / grounding -------------------------------------------------------

def _faithfulness_input(t: Trace) -> dict[str, Any] | None:
    # Requires retrieved context; a run that answered from the model's own
    # weights has nothing to be faithful *to*.
    if not _needs(t.question, t.answer, t.context):
        return None
    return {"input": t.question, "output": t.answer, "context": t.context}


def _qa_input(t: Trace) -> dict[str, Any] | None:
    if not _needs(t.question, t.answer):
        return None
    return {"input": t.question, "output": t.answer}


def _document_relevance_input(t: Trace) -> dict[str, Any] | None:
    if not _needs(t.question, t.context):
        return None
    return {"input": t.question, "document_text": t.context}


# --- Agent behaviour -------------------------------------------------------

def _tool_selection_input(t: Trace) -> dict[str, Any] | None:
    if not (t.question.strip() and t.available_tools and t.tool_calls):
        return None
    # Serialised, not passed as lists: this evaluator's schema types both fields
    # as `str`, and handing it a list fails pydantic validation rather than
    # being coerced — which shows up only as a per-trace warning, so the metric
    # silently produces nothing at all.
    return {
        "input": t.question,
        "available_tools": ", ".join(t.available_tools),
        "tool_selection": ", ".join(t.tools_called),
    }


JUDGED_METRICS: list[JudgedMetric] = [
    JudgedMetric("faithfulness", "FaithfulnessEvaluator", _faithfulness_input,
                 "Is the answer supported by the retrieved evidence?"),
    JudgedMetric("hallucination", "HallucinationEvaluator", _qa_input,
                 "Does the answer assert anything the run does not support?"),
    JudgedMetric("correctness", "CorrectnessEvaluator", _qa_input,
                 "Does the answer actually address the question asked?"),
    JudgedMetric("document_relevance", "DocumentRelevanceEvaluator", _document_relevance_input,
                 "Is the retrieved context relevant to the question?"),
    JudgedMetric("tool_selection", "ToolSelectionEvaluator", _tool_selection_input,
                 "Were the right tools chosen from those available?"),
    JudgedMetric("conciseness", "ConcisenessEvaluator", _qa_input,
                 "Response quality: is the answer free of padding?"),
    JudgedMetric("refusal", "RefusalEvaluator", _qa_input,
                 "Did the pipeline decline a question it should have answered?"),
]


# ===========================================================================
# Code-based metrics — deterministic, free, no model involved
# ===========================================================================

# The pipelines are instructed to cite: [Section, Page N], (CARO Clause vii),
# [tool:name]. An audit answer with no citation at all is the failure mode this
# product most needs to catch, and it needs no judge to detect.
_CITATION = re.compile(r"\[[^\]]{3,120}\]|\((?:Basis for Opinion|CARO[^)]{0,60}|Annexure[^)]{0,40})\)", re.I)

# Phrases that mean the run failed while still returning HTTP 200 — the answer
# is present but is an apology, so a naive "did it respond" check passes.
_NON_ANSWER = re.compile(
    r"\b(i (?:do not|don't) have (?:access|enough)|unable to (?:answer|determine|find)|"
    r"no (?:relevant )?(?:information|data|context) (?:was )?(?:found|available)|"
    r"maximum iterations|could not be completed)\b",
    re.I,
)


@dataclass
class CodeMetric:
    name: str
    fn: Callable[[Trace], tuple[float, str, str] | None]   # -> (score, label, explanation)
    description: str


def _has_citation(t: Trace) -> tuple[float, str, str] | None:
    if not t.answer.strip():
        return None
    n = len(_CITATION.findall(t.answer))
    ok = n > 0
    return (1.0 if ok else 0.0,
            "cited" if ok else "uncited",
            f"{n} citation marker(s) found in the answer." if ok
            else "No citation markers found — an audit answer should reference its source.")


def _answered(t: Trace) -> tuple[float, str, str] | None:
    if not t.answer.strip():
        return (0.0, "empty", "The run produced no answer text.")
    if _NON_ANSWER.search(t.answer):
        return (0.0, "non_answer",
                "The response is a refusal or failure notice rather than an answer.")
    return (1.0, "answered", "The run returned substantive answer text.")


def _tool_efficiency(t: Trace) -> tuple[float, str, str] | None:
    """Repeated identical tool calls are the signature of a stuck agent.

    This is the failure that exhausted MAX_TOOL_ITERATIONS in production: the
    same retrieval tool invoked seven times in a row, never converging. Scoring
    it deterministically means it shows up as a metric rather than as an
    incident.
    """
    if not t.tool_calls:
        return None
    total = len(t.tool_calls)
    distinct = len({(tc.name, tc.arguments) for tc in t.tool_calls})
    repeats = total - distinct
    score = distinct / total
    if repeats == 0:
        return (1.0, "efficient", f"{total} tool call(s), none repeated.")
    return (score, "repeated_calls",
            f"{repeats} of {total} tool call(s) repeated an identical name+arguments pair.")


def _latency_budget(t: Trace) -> tuple[float, str, str] | None:
    budget_ms = float(os.environ.get("ARTHA_EVAL_LATENCY_BUDGET_MS", 120_000))
    if t.latency_ms <= 0:
        return None
    ok = t.latency_ms <= budget_ms
    return (1.0 if ok else 0.0,
            "within_budget" if ok else "over_budget",
            f"{t.latency_ms/1000:.1f}s against a {budget_ms/1000:.0f}s budget.")


CODE_METRICS: list[CodeMetric] = [
    CodeMetric("has_citation", _has_citation, "Pass/fail: the answer cites a source."),
    CodeMetric("answered", _answered, "Pass/fail: substantive answer, not a refusal."),
    CodeMetric("tool_efficiency", _tool_efficiency, "Fraction of tool calls that were not repeats."),
    CodeMetric("latency_budget", _latency_budget, "Pass/fail against the latency budget."),
]


def exact_match(expected: str) -> CodeMetric:
    """Exact-match scoring, for when a trace has a known expected answer.

    Provided as a factory rather than a fixed metric because nothing in a live
    trace carries ground truth — it needs a labelled dataset to compare against.
    """

    def _fn(t: Trace) -> tuple[float, str, str] | None:
        if not t.answer.strip():
            return None
        hit = t.answer.strip() == expected.strip()
        return (1.0 if hit else 0.0, "match" if hit else "mismatch",
                "Exact string match against the expected answer.")

    return CodeMetric("exact_match", _fn, "Pass/fail: answer equals the expected string.")


def matches_regex(name: str, pattern: str, flags: int = re.I) -> CodeMetric:
    """A custom pass/fail rule — e.g. 'every answer must name the entity'."""
    compiled = re.compile(pattern, flags)

    def _fn(t: Trace) -> tuple[float, str, str] | None:
        if not t.answer.strip():
            return None
        hit = bool(compiled.search(t.answer))
        return (1.0 if hit else 0.0, "pass" if hit else "fail", f"Pattern {pattern!r} {'matched' if hit else 'did not match'}.")

    return CodeMetric(name, _fn, f"Pass/fail: answer matches {pattern!r}.")
