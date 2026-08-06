"""
chat_agents.py
==============
Multi-Agent SAR Q&A Chat Pipeline — Production Version.

Architecture (3 steps):
  Step 1  SAR_REWRITER   — rephrases raw user query into a standalone question (no tools)
  Step 2  SAR_QA_PLANNER — calls DB retrieval tools, synthesises a draft answer
  Step 3  SAR_VALIDATOR  — applies safe-wording guardrails before returning to the user

Usage:
  from Prod.chat_agents import SARChatPipeline
  from Prod.agent import _build_llm          # reuse the same LLM builder

  pipeline = SARChatPipeline(llm_client=_build_llm(), enable_tracing=False)

  answer = pipeline.ask(
      user_query="[Context: Company = ONGC, FY starting 2024] What is the audit opinion?",
      history=[]          # list of {"role": "user"/"assistant", "content": "..."}
  )

Environment variables (read at startup — see .env):
  GENERATION_BASE_URL   — vLLM / OpenAI-compatible endpoint URL
  GENERATION_MODEL      — model name (e.g. gemma-4-26b-a4b-it)
  FINANCE_LLM_DSN       — PostgreSQL DSN for the annual-reports DB
  EMBEDDING_URL         — bge-m3 embedding server URL
  PHOENIX_ENDPOINT      — Arize Phoenix OTLP trace endpoint (optional)

Tracing:
  Set enable_tracing=True to send every LLM hop to Phoenix.
  Requires: opentelemetry-sdk, opentelemetry-exporter-otlp-proto-http,
            opentelemetry-semantic-conventions.
  If packages are missing, tracing is silently disabled — the pipeline still runs.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Dict, List

# ---------------------------------------------------------------------------
# Path bootstrap — works on any machine, no hardcoded user paths.
# ---------------------------------------------------------------------------
_HERE    = Path(__file__).resolve().parent          # Finance_llm_v2/Prod/
_BACKEND = _HERE.parent.parent / "backend"          # Finance_llm_v2/backend/
_VENDOR  = _BACKEND / "vendor"                      # Finance_llm_v2/backend/vendor/

for _p in (_BACKEND, _VENDOR):
    _ps = str(_p)
    if _ps not in sys.path:
        sys.path.insert(0, _ps)

# ---------------------------------------------------------------------------
# Yukta framework imports (safe now that vendor/ is on sys.path)
# ---------------------------------------------------------------------------
from yukta import AgentConfig, SystemPrompt, create_agent, ToolProcessor  # type: ignore
from yukta.tools.tool import Tool, ToolParameter                           # type: ignore

# ---------------------------------------------------------------------------
# Local imports (same Prod/ folder)
# ---------------------------------------------------------------------------
from sar_prod_v3.chat_prompts import (
    ORCHESTRATOR_REWRITE_PROMPT,
    ORCHESTRATOR_VALIDATE_PROMPT,
    PLANNER_PROMPT,
)
from sar_prod_v3.chat_tools import SARQATools

logger = logging.getLogger("sar_prod.chat_agents")

# ---------------------------------------------------------------------------
# Phoenix / OpenTelemetry tracing bootstrap
# ---------------------------------------------------------------------------
# Default endpoint reads from env var PHOENIX_ENDPOINT.
# Change in .env — do NOT hardcode the IP here.
PHOENIX_ENDPOINT = os.getenv("PHOENIX_ENDPOINT", "http://localhost:6006/v1/traces")


def setup_phoenix_tracing(endpoint: str = PHOENIX_ENDPOINT) -> bool:
    """
    Bootstrap OpenTelemetry → Arize Phoenix via OTLP/HTTP.
    Returns True on success, False (with a warning log) if packages are missing.
    """
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider = TracerProvider(resource=Resource.create({"service.name": "sar-chat-pipeline"}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)
        logger.info("Phoenix OTEL tracing active → %s", endpoint)
        return True
    except ImportError as exc:
        logger.warning("OTEL packages not installed — tracing disabled: %s", exc)
        return False
    except Exception as exc:
        logger.warning("Phoenix setup failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# SARChatPipeline
# ---------------------------------------------------------------------------

class SARChatPipeline:
    """
    Three-step agentic pipeline for SAR Q&A.

    Initialise once at server startup, then call .ask() per request.
    The LLM client is shared across all three agents.

    Args:
        llm_client:     Any Yukta-compatible LLM client (e.g. SafeVLLMClient from agent.py).
        enable_tracing: Set True to send traces to Phoenix (requires OTEL packages).
    """

    def __init__(self, llm_client, enable_tracing: bool = False):
        self.llm      = llm_client
        self.qa_tools = SARQATools()

        if enable_tracing:
            setup_phoenix_tracing()

        # Build agents once at init — not per request
        self.rewriter_agent  = self._build_rewriter_agent()
        self.planner_agent   = self._build_planner_agent()
        self.validator_agent = self._build_validator_agent()

    # ── Agent builders ────────────────────────────────────────────────────

    def _build_rewriter_agent(self):
        """Step 1 — pure LLM rewrite. No tools. max_iter=1, max_tokens=300."""
        return create_agent(
            name="SAR_REWRITER",
            system_prompt=SystemPrompt("SAR_REWRITER", ORCHESTRATOR_REWRITE_PROMPT),
            llm_client=self.llm,
            config=AgentConfig(
                temperature=0.0,
                max_tokens=300,
                max_iter=1,
                enable_logging=True,
            ),
        )

    def _build_planner_agent(self):
        """Step 2 — retrieval + synthesis agent with two DB tools."""
        processor = ToolProcessor()

        processor.add_tool(Tool(
            name="retrieve_sar_context",
            description=(
                "Searches the annual report and SAR database for relevant text passages. "
                "Use for audit opinion, EoM, KAM, CARO clauses, IFC, Going Concern, SA references, "
                "Section 143, notes to accounts, C&AG directions, Rule 11. "
                "Returns the top matching text chunks with section and page citations."
            ),
            parameters=[
                ToolParameter("company",  "string",  "Company name exactly as in the query context (e.g. 'ONGC')", required=True),
                ToolParameter("fy_start", "integer", "Financial year start as integer (e.g. 2023 for FY 2023-24)", required=True),
                ToolParameter("query",    "string",  "Focused search phrase for the specific topic", required=True),
            ],
            function=self.qa_tools.retrieve_sar_context,
        ))

        processor.add_tool(Tool(
            name="retrieve_financial_tables",
            description=(
                "Fetches a specific financial statement table (Balance Sheet, P&L, Cash Flow) "
                "when the question requires numerical figures, totals, or financial ratios. "
                "Use IN ADDITION to retrieve_sar_context when amounts are needed."
            ),
            parameters=[
                ToolParameter("company",        "string",  "Company name (e.g. 'ONGC')", required=True),
                ToolParameter("fy_start",       "integer", "Financial year start (e.g. 2023)", required=True),
                ToolParameter("statement_type", "string",
                              "One of: 'balance_sheet', 'profit_loss', 'cash_flow', 'statement_of_equity'",
                              required=True),
            ],
            function=self.qa_tools.retrieve_financial_tables,
        ))

        return create_agent(
            name="SAR_QA_PLANNER",
            system_prompt=SystemPrompt("SAR_QA_PLANNER", PLANNER_PROMPT),
            llm_client=self.llm,
            tools_processor=processor,
            config=AgentConfig(
                temperature=0.1,
                max_tokens=3000,
                max_iter=5,          # Up to 5 tool-call iterations per request
                enable_logging=True,
            ),
        )

    def _build_validator_agent(self):
        """Step 3 — safe-wording validation only. No tools. max_iter=1."""
        return create_agent(
            name="SAR_VALIDATOR",
            system_prompt=SystemPrompt("SAR_VALIDATOR", ORCHESTRATOR_VALIDATE_PROMPT),
            llm_client=self.llm,
            config=AgentConfig(
                temperature=0.0,
                max_tokens=3500,     # Must pass through long Planner answers unchanged
                max_iter=1,
                enable_logging=True,
            ),
        )

    # ── Conversation history helper ────────────────────────────────────────

    def format_history(self, history: List[Dict[str, str]]) -> str:
        """Returns the last 8 messages formatted as a plain-text string for the Rewriter."""
        if not history:
            return ""
        recent = history[-8:]
        return "\n".join(
            f"{m.get('role', 'user').upper()}: {m.get('content', '')}"
            for m in recent
        )

    # ── Public entry point ────────────────────────────────────────────────

    def ask(self, user_query: str, history: List[Dict[str, str]] = None) -> str:
        """
        Run the full 3-step pipeline for a single user turn.

        Args:
            user_query: The raw user question, optionally prefixed with
                        "[Context: Company = X, FY starting YYYY]".
            history:    Previous turns as a list of {"role": ..., "content": ...} dicts.

        Returns:
            The final validated answer string.
        """
        hist_str = self.format_history(history or [])

        # Step 1 — Rewrite -------------------------------------------------
        rewrite_input = user_query
        if hist_str:
            rewrite_input = f"CONVERSATION HISTORY:\n{hist_str}\n\nLATEST QUERY:\n{user_query}"

        rewrite_result = self.rewriter_agent.run(rewrite_input)
        clean_query    = rewrite_result.get("response", user_query).strip()
        logger.info("[REWRITER] '%s' → '%s'", user_query[:80], clean_query[:120])

        # Step 2 — Planner calls tools and synthesises ---------------------
        planner_result = self.planner_agent.run(clean_query)
        draft_answer   = planner_result.get("response", "No answer generated.")
        logger.info("[PLANNER] tool_calls=%s tokens=%s",
                    planner_result.get("tool_calls_count", "?"),
                    planner_result.get("total_tokens", "?"))

        # Step 3 — Validator applies guardrails ----------------------------
        validate_input = f"DRAFT ANSWER TO VALIDATE:\n\n{draft_answer}"
        final_result   = self.validator_agent.run(validate_input)
        final_answer   = final_result.get("response", draft_answer).strip()

        return final_answer
