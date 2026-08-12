"""
chat_tools.py
=============
Retrieval tools for the SAR Chat Planner Agent.

These tools are called by the Planner agent inside SARChatPipeline (chat_agents.py).
They pull data from the production PostgreSQL + pgvector database.

DB Connection:
  Reads FINANCE_LLM_DSN from environment (via .env or shell).
  Falls through to the default dev DSN in backend/yukta_rag/core/config.py if not set.

Embedding:
  Reads EMBEDDING_URL and EMBEDDING_MODEL from environment.

Reranker (optional):
  Set USE_RERANKER = True once bge-reranker-v2-m3 is deployed.
  Set RERANKER_URL env var if the server is on a different host/port.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List

import requests

# ---------------------------------------------------------------------------
# Path bootstrap — put the directory that *contains* `yukta_rag` on sys.path.
#
# The retrieval package imports itself absolutely (`from yukta_rag.x import y`),
# so it has to be importable as a top-level name; adding its parent directory is
# the same mechanism modes/trial_balance/adapter.py already uses for it.
#
# In this repository that parent is modes/trial_balance/pipeline/, which is
# checked first. The upstream version only walked up the directory tree from
# this file and then looked for a sibling `Finance_llm_v2/backend` checkout —
# neither of which resolves here, because `yukta_rag` sits *below* a sibling
# mode rather than above this one. That made the import raise at module load,
# taking the whole SAR chat mode offline. The upward walk is kept afterwards so
# a standalone deployment that does vendor yukta_rag alongside still works.
#
# Note the cross-mode dependency this creates: SAR chat reuses the retrieval
# code vendored under Trial Balance. Nothing in Trial Balance is modified or
# imported besides `yukta_rag`, and that directory contains nothing else, so
# putting it on sys.path cannot shadow another mode's modules.
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent


def _find_yukta_rag_parent() -> Path | None:
    """Return the directory containing `yukta_rag/`, or None if not found."""
    # modes/statutory_auditor_report/sar_prod_v3 -> modes/
    _modes_dir = _HERE.parent.parent
    known = _modes_dir / "trial_balance" / "pipeline"
    if (known / "yukta_rag" / "__init__.py").exists():
        return known

    candidate = _HERE
    for _ in range(10):
        if (candidate / "yukta_rag" / "__init__.py").exists():
            return candidate
        candidate = candidate.parent
    return None


_YUKTA_BACKEND = _find_yukta_rag_parent()

if _YUKTA_BACKEND is None:
    raise ImportError(
        "chat_tools.py: cannot locate the yukta_rag package. Expected it at "
        "backend/modes/trial_balance/pipeline/yukta_rag, or in a directory "
        f"above {_HERE}."
    )

_VENDOR = _YUKTA_BACKEND / "vendor"
for _p in (_YUKTA_BACKEND, _VENDOR):
    _ps = str(_p)
    if _ps not in sys.path:
        sys.path.insert(0, _ps)

# ---------------------------------------------------------------------------
# Backend imports (safe now that sys.path is set up)
# ---------------------------------------------------------------------------
from yukta_rag.retrieval.retrieval import retrieve_annual_reports, resolve_documents  # type: ignore
from yukta_rag.core.config import EMBEDDING_URL, EMBEDDING_MODEL                      # type: ignore


logger = logging.getLogger("sar_prod.chat_tools")

# ---------------------------------------------------------------------------
# Reranker config
# Override RERANKER_URL env var if the bge-reranker server is on a different host.
# ---------------------------------------------------------------------------
_EMBED_BASE   = EMBEDDING_URL.rsplit("/embeddings", 1)[0]   # http://host:port/v1
RERANKER_URL  = os.getenv("RERANKER_URL", f"{_EMBED_BASE}/rerank")
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "bge-reranker-v2-m3")

# How many chunks to pull from DB before re-ranking (candidate pool)
_RETRIEVAL_POOL = 10
# How many chunks to keep after re-ranking (sent to the Planner)
_RERANK_TOP_K   = 7

# Set to True once bge-reranker-v2-m3 is deployed on your server.
# When False, skips the reranker network call and uses vector-score order.
USE_RERANKER = False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _rerank(query: str, chunks: List[Dict], top_k: int = _RERANK_TOP_K) -> List[Dict]:
    """Re-rank retrieved chunks using the bge-reranker endpoint."""
    if not chunks:
        return chunks

    if not USE_RERANKER:
        logger.debug("Reranker disabled (USE_RERANKER=False). Using vector-score order.")
        return sorted(chunks, key=lambda c: c.get("score", 0), reverse=True)[:top_k]

    texts = [c.get("content") or c.get("table_md") or "" for c in chunks]
    try:
        resp = requests.post(
            RERANKER_URL,
            json={"model": RERANKER_MODEL, "query": query, "documents": texts},
            timeout=30,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        scored  = sorted(results, key=lambda r: r.get("relevance_score", 0), reverse=True)
        reranked = [chunks[r["index"]] for r in scored[:top_k] if r["index"] < len(chunks)]
        logger.info("Reranked %d chunks → kept top %d", len(chunks), len(reranked))
        return reranked
    except Exception as exc:
        logger.warning("Reranker unavailable (%s) — falling back to vector-score order.", exc)
        return sorted(chunks, key=lambda c: c.get("score", 0), reverse=True)[:top_k]


# ---------------------------------------------------------------------------
# SARQATools — wired to the Planner's tool processor
# ---------------------------------------------------------------------------

class SARQATools:
    """
    Provides the two retrieval tools given to the Planner agent.

    Tool 1: retrieve_sar_context     — hybrid RAG (text + table) over the full annual report
    Tool 2: retrieve_financial_tables — pinned financial statement tables (BS, P&L, CF)
    """

    def retrieve_sar_context(self, company: str, fy_start: int, query: str) -> str:
        """
        Retrieves relevant text/table chunks from the company's annual report.

        Pipeline: resolve_documents → hybrid RAG (top-25 pool) → optional reranker → top-7

        Args:
            company:  Company name exactly as in the query context (e.g. "Coal India", "SAIL", "ONGC").
            fy_start: Starting year of the financial period (e.g. 2023 for FY 2023-24).
            query:    Focused search phrase for the specific topic.

        Returns:
            JSON string containing the retrieved chunks with section/page citations.
        """
        # Automatic query enrichment for Annexures (CARO 2020 / IFC)
        enriched_query = query
        q_lower = query.lower()
        if "annexure a" in q_lower or "caro" in q_lower:
            if "caro" not in q_lower or "2020" not in q_lower:
                enriched_query += " CARO 2020 Companies Auditor Report Order clauses property inventory loans statutory dues"
        if "annexure b" in q_lower or "ifc" in q_lower:
            if "internal financial controls" not in q_lower:
                enriched_query += " Internal Financial Controls IFC report section 143(3)(i) operating effectiveness"

        logger.info("retrieve_sar_context: company=%s fy_start=%s query='%s' (enriched='%s')", company, fy_start, query, enriched_query)
        try:
            doc_records = resolve_documents(f"{company} {fy_start}")
            doc_ids = [d[0] for d in doc_records] if doc_records else None
            if doc_ids:
                logger.info("Resolved %d doc(s) for %s FY %s", len(doc_ids), company, fy_start)
            else:
                logger.warning("No documents resolved for '%s %s'. Running global search.", company, fy_start)

            raw_results = retrieve_annual_reports(
                query=enriched_query,
                top_k=_RETRIEVAL_POOL,
                doc_ids=doc_ids,
            )

            # Dynamic budget: text + tables share _RERANK_TOP_K slots; tables get ≤40%.
            text_hits  = [r for r in raw_results if r.get("kind") == "text"]
            table_hits = [r for r in raw_results if r.get("kind") == "table"]

            max_tables = max(1, round(_RERANK_TOP_K * 0.4))
            max_text   = _RERANK_TOP_K - max_tables

            top_tables      = sorted(table_hits, key=lambda r: r.get("_boosted", r.get("score", 0)), reverse=True)[:max_tables]
            effective_text_k = max_text + (max_tables - len(top_tables))
            reranked_text   = _rerank(enriched_query, text_hits, top_k=effective_text_k)
            top_chunks      = reranked_text + top_tables

            logger.info("Chunk budget: %d text + %d table(s) = %d total",
                        len(reranked_text), len(top_tables), len(top_chunks))

            formatted = [
                {
                    "section": r.get("section", "Unknown"),
                    "page":    r.get("page", 0),
                    "kind":    r.get("kind", "text"),
                    "content": r.get("content") or r.get("table_md") or "",
                    "score":   round(r.get("_boosted", r.get("score", 0)), 4),
                }
                for r in top_chunks
            ]

            logger.info("Final context: %d chunks", len(formatted))
            return json.dumps(formatted, indent=2)

        except Exception as exc:
            logger.error("Error in retrieve_sar_context: %s", exc)
            return json.dumps({"error": f"Failed to retrieve context: {exc}"})

    def retrieve_financial_tables(self, company: str, fy_start: int, statement_type: str) -> str:
        """
        Retrieves a specific financial statement table (Balance Sheet, P&L, Cash Flow).

        Use when the question requires specific financial figures, totals, or ratios.

        Args:
            company:        Company name (e.g. "Coal India", "SAIL", "ONGC").
            fy_start:       Starting year of the financial period (e.g. 2023).
            statement_type: One of "balance_sheet", "profit_loss", "cash_flow", "statement_of_equity".

        Returns:
            JSON string containing the matching markdown tables.
        """
        logger.info("retrieve_financial_tables: company=%s fy_start=%s type=%s",
                    company, fy_start, statement_type)
        try:
            doc_records = resolve_documents(f"{company} {fy_start}")
            doc_ids = [d[0] for d in doc_records] if doc_records else None

            results = retrieve_annual_reports(
                query=f"{statement_type} {company} {fy_start}",
                top_k=3,
                doc_ids=doc_ids,
                financial_stmt_type=statement_type,
            )

            formatted_tables = [
                {
                    "section":   r.get("section", "Unknown Section"),
                    "page":      r.get("page", 0),
                    "table_md":  r.get("table_md", ""),
                    "unit":      r.get("unit", ""),
                    "currency":  r.get("currency", ""),
                }
                for r in results
                if r.get("kind") == "table" and (r.get("table_md") or r.get("content"))
            ]

            if not formatted_tables:
                return json.dumps({"message": "No financial tables found for the specified parameters."})

            return json.dumps(formatted_tables, indent=2)

        except Exception as exc:
            logger.error("Error in retrieve_financial_tables: %s", exc)
            return json.dumps({"error": f"Failed to retrieve tables: {exc}"})
