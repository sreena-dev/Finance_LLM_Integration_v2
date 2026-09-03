"""Turn raw Phoenix spans into the inputs the evaluators expect.

The evaluators want a flat record per unit of work — question, answer, the
context it was grounded in, which tools were available and which were called.
A trace holds all of that, but spread across spans of different shapes, so this
module reassembles it.

Span shapes this reads (all emitted by yukta's @trace_yukta decorators):

  agent:<name>   root of a run. `input.value` is the user's question,
                 `output.value` the final response, and
                 `yukta.agent.available_tools` the tool catalogue it chose from.
  tool:<name>    one tool call. `input.value` is the arguments,
                 `output.value` the result.
  <Client>.generate
                 one LLM hop, with `llm.token_count.*`.

Note what is *not* here: a RETRIEVER span. yukta emits retrieval as an ordinary
tool call whose result is an opaque string, so there are no per-document records
and no relevance scores to read. Context for the grounding metrics is therefore
reconstructed by concatenating the retrieval tools' outputs — good enough to
judge whether an answer is supported by what was retrieved, but not enough for
per-document precision/recall. Emitting real retriever spans from the retrieval
tools is what would close that gap.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# Tool names whose output is retrieved evidence rather than a computation.
# Substring match, because each mode names its own retrieval tools.
_RETRIEVAL_HINTS = (
    "retrieve",
    "search",
    "lookup",
    "summarize_annual_report",
    "get_audit_report",
    "highlights",
    "disclosures",
    "reference",
    "context",
)

MAX_CONTEXT_CHARS = 24_000


def _attr(attrs: dict[str, Any], *path: str) -> Any:
    """Read a dotted attribute that may be nested or flat.

    Phoenix returns some attributes nested ({"llm": {"token_count": {...}}}) and
    some flattened ("llm.token_count.total"), depending on how the exporter
    serialised them, so both forms have to be tried.
    """
    node: Any = attrs
    for key in path:
        if isinstance(node, dict) and key in node:
            node = node[key]
        else:
            node = None
            break
    if node is not None:
        return node
    return attrs.get(".".join(path))


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return str(value)


@dataclass
class ToolCall:
    span_id: str
    name: str
    arguments: str
    result: str

    @property
    def is_retrieval(self) -> bool:
        lowered = self.name.lower()
        return any(hint in lowered for hint in _RETRIEVAL_HINTS)


@dataclass
class Trace:
    """One agent run, flattened."""

    trace_id: str
    root_span_id: str
    agent_name: str
    question: str
    answer: str
    available_tools: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    status: str = ""

    @property
    def context(self) -> str:
        """Retrieved evidence, concatenated. Empty when nothing was retrieved."""
        chunks = [tc.result for tc in self.tool_calls if tc.is_retrieval and tc.result]
        if not chunks:
            return ""
        joined = "\n\n---\n\n".join(chunks)
        # Truncated because the judge model has a context window and these
        # results can run to tens of thousands of characters. Grounding is
        # judged against the head of the evidence rather than not at all.
        return joined[:MAX_CONTEXT_CHARS]

    @property
    def tools_called(self) -> list[str]:
        return [tc.name for tc in self.tool_calls]

    @property
    def is_evaluable(self) -> bool:
        """A run needs a question and an answer for any judged metric to mean
        anything. Everything else (context, tools) is optional and gates only
        the metrics that need it."""
        return bool(self.question.strip() and self.answer.strip())


def assemble(spans: list[dict[str, Any]]) -> list[Trace]:
    """Group spans into traces. Spans may arrive in any order."""
    by_trace: dict[str, list[dict[str, Any]]] = {}
    for span in spans:
        ctx = span.get("context") or {}
        trace_id = ctx.get("trace_id") or span.get("trace_id") or ""
        if trace_id:
            by_trace.setdefault(trace_id, []).append(span)

    traces: list[Trace] = []
    for trace_id, group in by_trace.items():
        root = next(
            (s for s in group if s.get("parent_id") is None and str(s.get("name", "")).startswith("agent:")),
            None,
        ) or next((s for s in group if s.get("parent_id") is None), None)
        if root is None:
            continue

        attrs = root.get("attributes") or {}
        ctx = root.get("context") or {}

        tools_attr = _attr(attrs, "yukta", "agent", "available_tools")
        available: list[str] = []
        if tools_attr:
            try:
                parsed = json.loads(tools_attr) if isinstance(tools_attr, str) else tools_attr
                available = [t.get("name", "") for t in parsed if isinstance(t, dict)]
            except Exception:  # noqa: BLE001
                available = []

        trace = Trace(
            trace_id=trace_id,
            root_span_id=ctx.get("span_id", ""),
            agent_name=str(_attr(attrs, "yukta", "agent", "name") or root.get("name", "")),
            question=_as_text(_attr(attrs, "input", "value")),
            answer=_as_text(_attr(attrs, "output", "value")),
            available_tools=available,
            latency_ms=float(_attr(attrs, "yukta", "latency_ms") or 0.0),
            status=str(root.get("status_code") or ""),
        )

        for span in group:
            name = str(span.get("name", ""))
            span_attrs = span.get("attributes") or {}
            span_ctx = span.get("context") or {}
            if name.startswith("tool:"):
                trace.tool_calls.append(
                    ToolCall(
                        span_id=span_ctx.get("span_id", ""),
                        name=str(_attr(span_attrs, "tool", "name") or name[5:]),
                        arguments=_as_text(_attr(span_attrs, "input", "value")),
                        result=_as_text(_attr(span_attrs, "output", "value")),
                    )
                )
            elif ".generate" in name:
                trace.prompt_tokens += int(_attr(span_attrs, "llm", "token_count", "prompt") or 0)
                trace.completion_tokens += int(_attr(span_attrs, "llm", "token_count", "completion") or 0)

        traces.append(trace)

    traces.sort(key=lambda t: t.trace_id)
    return traces
