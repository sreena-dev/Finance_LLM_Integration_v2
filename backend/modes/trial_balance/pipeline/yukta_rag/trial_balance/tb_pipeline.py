"""Trial-balance analysis pipeline: Planner -> tools -> Analyst over one TB.

Agent 1 (Planner) picks the analysis steps. The pipeline then runs the chosen
deterministic ``tb_tools`` programmatically (they do all the arithmetic and record
structured results), optionally retrieves a few Ind AS excerpts for the risk
findings, and Agent 2 (Analyst) writes one report from those tool outputs. This
mirrors how ``pipeline.py`` runs its two agents and keeps the numbers exact and
the report reliable on a small local model. All figures originate in ``tb_tools``,
never the LLM.
"""

from __future__ import annotations

import re

from yukta_rag.core.llm import build_llm
from yukta_rag.chat.pipeline import _dedupe_sources
from yukta_rag.retrieval.retrieval import retrieve_ind_as
from yukta_rag.agents.tb_agents import (
    TB_STEPS,
    build_tb_analyst,
    build_tb_chat_analyst,
    build_tb_planner,
)
from yukta_rag.chat.memory import format_history
from yukta_rag.trial_balance.tb_tools import (
    _fmt_classification,
    _fmt_ratios,
    _fmt_risk,
    _fmt_statements,
    _fmt_tie_out,
    _fmt_variance,
    build_tb_tools,
    classify_tb,
    compute_ratios,
    compute_risk,
    compute_statements,
    compute_tie_out,
    compute_variance,
)
from yukta_rag.tools.tools import ind_as_blocks
from yukta_rag.trial_balance.trial_balance import get_trial_balance

# step -> (results key, formatter) for backfill and the deterministic fallback report
_STEP_KEY = {
    "validate_tie_out": ("tie_out", _fmt_tie_out),
    "classify_accounts": ("classification", _fmt_classification),
    "build_statements": ("statements", _fmt_statements),
    "compute_ratios": ("ratios", _fmt_ratios),
    "compute_variance": ("variance", _fmt_variance),
    "assess_risk": ("risk", _fmt_risk),
}

_INDAS_TOP_K = 4

# yukta appends a bracketed status marker when a response hit max_tokens and was
# auto-continued (or the continuation itself failed). Strip it from anything shown
# to the user; the ⚠️ variant means the narrative is truncated.
_YUKTA_MARKER_RE = re.compile(
    r"\s*\[(?:⚠️|✅)️?\s*Response (?:still incomplete|completed)[^\]]*\]\s*$")
_YUKTA_INCOMPLETE_RE = re.compile(r"\[⚠️️?\s*Response still incomplete[^\]]*\]")


def _clean_narrative(text: str) -> tuple[str, bool]:
    """Strip yukta continuation markers; return ``(cleaned, is_complete)``.

    ``is_complete`` is False when the model's answer was truncated at max_tokens
    and could not be continued — callers should fall back to the deterministic
    report rather than show a half-finished narrative.
    """
    t = (text or "").strip()
    incomplete = bool(_YUKTA_INCOMPLETE_RE.search(t))
    t = _YUKTA_MARKER_RE.sub("", t).strip()
    return t, not incomplete


class TBAnalysisPipeline:
    """Two-agent analysis over a stored trial balance."""

    def __init__(self):
        # 3500 output tokens (like the audit pipeline): a comparison of two ~1000-
        # account TBs needs a long report, and the default 1536 truncates it.
        self.llm = build_llm(max_tokens=3500)
        self.planner = build_tb_planner(self.llm)
        self.analyst = build_tb_analyst(self.llm)
        self.chat_analyst = build_tb_chat_analyst(self.llm)

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _summary(tb: dict) -> str:
        pr = tb.get("parse_report", {})
        periods = tb.get("periods", [])
        return (
            f"Trial balance summary: {len(periods)} period(s) "
            f"[{', '.join(periods)}], {pr.get('n_accounts')} accounts, "
            f"balance mode={pr.get('balance_mode')}, "
            f"account-type column={'yes' if pr.get('has_type_column') else 'no'}."
        )

    @staticmethod
    def _run_tools(tb: dict, steps: list[str]) -> tuple[dict, list[str]]:
        """Execute the planned tools deterministically; return (results, text blocks)."""
        results: dict = {}
        tools = {t.name: t for t in build_tb_tools(tb, results)}
        blocks = [tools[s].function() for s in steps if s in tools]
        return results, blocks

    @staticmethod
    def _backfill(tb: dict, steps: list[str], results: dict) -> None:
        """Guarantee every planned step's structured result exists (belt-and-suspenders)."""
        if any(s in steps for s in ("classify_accounts", "build_statements",
                                    "compute_ratios", "compute_variance", "assess_risk")):
            results.setdefault("classification", classify_tb(tb))
        cls = results.get("classification")
        if "validate_tie_out" in steps:
            results.setdefault("tie_out", compute_tie_out(tb))
        if "build_statements" in steps:
            results.setdefault("statements", compute_statements(tb, cls))
        if "compute_ratios" in steps:
            results.setdefault("ratios", compute_ratios(tb, cls, results.get("statements")))
        if "compute_variance" in steps:
            results.setdefault("variance", compute_variance(tb, cls))
        if "assess_risk" in steps:
            results.setdefault("risk", compute_risk(tb, cls))

    @staticmethod
    def _indas_context(results: dict, collector: list) -> str:
        """Retrieve a few Ind AS excerpts relevant to the risk findings (best-effort)."""
        risk = results.get("risk")
        if not risk or not risk.get("risks"):
            return ""
        cats = {r["category"] for r in risk["risks"]}
        topic_map = {
            "liquidity": "presentation of current assets and liabilities and liquidity",
            "solvency": "borrowings and financial liabilities disclosure",
            "profitability": "recognition of income and expenses, profit or loss",
            "variance": "impairment of assets and significant changes",
            "tie_out": "presentation of financial statements",
            "statement_integrity": "presentation of financial statements",
        }
        query = "; ".join(topic_map[c] for c in cats if c in topic_map) or \
            "presentation of financial statements"
        try:
            rows = retrieve_ind_as(query, _INDAS_TOP_K)
        except Exception:  # noqa: BLE001 - Ind AS lookup is optional context
            return ""
        collector.extend({**r, "source": "ind_as"} for r in rows)
        return "\n\n".join(ind_as_blocks(rows))

    @staticmethod
    def _fallback_report(steps: list[str], results: dict) -> str:
        """Deterministic Markdown report assembled straight from the computed results."""
        parts = ["# Trial Balance Analysis\n"]
        for step in steps:
            key, fmt = _STEP_KEY[step]
            if results.get(key) is not None:
                parts.append(fmt(results[key]))
        return "\n\n".join(parts)

    # -- public API ---------------------------------------------------------

    def ask_tb(self, doc_id: str, question: str, history: list[dict] | None = None) -> dict:
        """Answer a specific question about one stored TB, conversationally (follow-ups).

        Computes the full deterministic analysis (all applicable tools) as grounding
        facts, then lets the chat analyst answer the current question using those facts,
        optional Ind AS excerpts and the conversation so far. No arithmetic in the model.
        """
        q = (question or "").strip()
        if not q:
            raise ValueError("question must not be empty")
        tb = get_trial_balance(doc_id)
        if tb is None:
            raise ValueError(f"no stored trial balance for doc_id {doc_id!r}")

        summary = self._summary(tb)
        # every applicable step -> deterministic facts (variance needs two periods)
        steps = [s for s in TB_STEPS
                 if s != "compute_variance" or len(tb.get("periods", [])) >= 2]
        results, blocks = self._run_tools(tb, steps)
        self._backfill(tb, steps, results)
        facts = "\n\n".join(blocks) if blocks else self._fallback_report(steps, results)

        collector: list = []
        indas = self._indas_context(results, collector)
        indas_block = f"\n\n=== Indian Accounting Standards excerpts ===\n{indas}" if indas else ""
        hist = format_history(history or [])
        hist_block = f"\n\n=== Conversation so far ===\n{hist}" if hist else ""

        analyst_input = (
            f"{summary}\n\n=== Computed analysis of this trial balance (use figures verbatim) "
            f"===\n{facts}{indas_block}{hist_block}\n\n"
            f"=== User question ===\n{q}\n\nAnswer this question about the trial balance."
        )
        try:
            run = self.chat_analyst.run(analyst_input, reset_conversation=True)
            answer, complete = _clean_narrative(run.get("response") or "")
        except Exception:  # noqa: BLE001 - never fail the request on an agent hiccup
            answer, complete = "", True
        if len(answer) < 20 or not complete:  # thin or truncated -> deterministic fallback
            answer = ("I couldn't produce a narrative answer, so here is the computed "
                      "analysis:\n\n" + self._fallback_report(steps, results))
        return {"doc_id": doc_id, "answer": answer, "sources": _dedupe_sources(collector)}

