"""
chat_tools.py
=============
Retrieval tools for the SAR Chat Planner Agent.

These tools are called by the Planner agent inside SARChatPipeline (chat_agents.py).
They pull data from the production PostgreSQL + pgvector database.

RETRIEVAL BACKEND
-----------------
This module used to import `yukta_rag` — a retrieval package that physically
lived under the *old* Trial Balance mode
(`backend/modes/trial_balance/pipeline/yukta_rag/`). TB-v2 replaced that mode
wholesale and deleted the package, which took SAR chat offline at import time
even though nothing else here changed.

SAR already ships an equivalent, wired to the same `documents` / `text_chunks` /
`table_chunks` schema: `sar_prod_v3.tool_sar`. This module now builds on that —
no cross-mode dependency, and the same code path the SAR *report* pipeline
already exercises.

  * document resolution   → a company + FY lookup against `documents`
                            (mirrors FetchTools.resolve_doc_id, minus fy_end)
  * text retrieval        → pgvector similarity over `text_chunks.embedding`,
                            with FetchTools._fetch_text_chunks (heading + tsquery)
                            as the fallback when the embedding column or the
                            embedding endpoint is unavailable
  * financial tables      → FetchTools.fetch_financial_tables (BS / P&L / CF),
                            plus a direct `table_chunks` query for other types

Environment:
  FINANCE_DSN         — Postgres DSN for the annual-reports DB (see tool_sar._get_db_conn)
  EMBEDDING_BASE_URL  — embedding endpoint base URL (see tool_sar.ReferenceTools._embed)
  EMBEDDING_MODEL     — embedding model name

Reranker (optional):
  Set USE_RERANKER = True once a bge-reranker endpoint is deployed, and point
  RERANKER_URL (or RERANKER_BASE_URL) at it. When off, chunks are returned in
  similarity-score order.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Dict, List

import requests

from sar_prod_v3.tool_sar import FetchTools, ReferenceTools, _get_db_conn

logger = logging.getLogger("sar_prod.chat_tools")

# ---------------------------------------------------------------------------
# Reranker config
# ---------------------------------------------------------------------------
_RERANKER_BASE = os.getenv("RERANKER_BASE_URL", "").rstrip("/")
RERANKER_URL = os.getenv("RERANKER_URL") or (f"{_RERANKER_BASE}/rerank" if _RERANKER_BASE else "")
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "bge-reranker-v2-m3")

# How many chunks to pull from the DB before re-ranking (candidate pool).
_RETRIEVAL_POOL = 12
# How many chunks to keep after re-ranking (sent to the Planner).
_RERANK_TOP_K = 7

# Set to True once a reranker endpoint is deployed. When False, the reranker
# network call is skipped and chunks stay in similarity-score order.
USE_RERANKER = False

# Minimal English stop-word set — enough to keep a `to_tsquery` OR-expression
# from being dominated by function words. Not linguistically complete on
# purpose; Postgres' own 'english' dictionary still stems what survives.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are",
    "was", "were", "be", "been", "by", "as", "at", "it", "its", "this", "that",
    "these", "those", "with", "from", "what", "which", "who", "how", "does",
    "do", "did", "any", "has", "have", "had", "about", "into", "per", "vs",
}


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def _keywords(text: str, *, limit: int = 12) -> List[str]:
    """Alphanumeric tokens from `text`, stop-words and 1-2 char noise removed."""
    seen: set[str] = set()
    out: List[str] = []
    for tok in re.findall(r"[A-Za-z0-9]+", text.lower()):
        if len(tok) <= 2 or tok in _STOPWORDS or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
        if len(out) >= limit:
            break
    return out


def _to_tsquery(text: str) -> str | None:
    """Build a recall-oriented `to_tsquery` OR-expression, or None if empty."""
    toks = _keywords(text)
    return " | ".join(toks) if toks else None


# Curated topic -> section/title ILIKE patterns. Used as the primary route for
# _fetch_text_chunks (heading match) before the tsquery fallback kicks in.
_HEADING_ROUTES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("caro", "annexure a", "companies auditor", "auditor's report order"), "%CARO%"),
    (("ifc", "internal financial control", "icfr", "annexure b"), "%internal financial control%"),
    (("going concern", "material uncertainty", "sa 570"), "%going concern%"),
    (("emphasis of matter", "eom", "sa 706"), "%emphasis of matter%"),
    (("key audit matter", "kam", "sa 701"), "%key audit matter%"),
    (("basis for opinion",), "%basis for opinion%"),
    (("opinion",), "%opinion%"),
    (("rule 11", "rule11"), "%rule 11%"),
    (("143(3)", "section 143", "s.143"), "%143(3)%"),
    (("c&ag", "cag", "143(5)", "comptroller"), "%143(5)%"),
    (("directors report", "board report", "sa 720", "other information"), "%report%"),
)


def _heading_patterns(query: str) -> List[str]:
    q = query.lower()
    pats: List[str] = []
    for needles, pattern in _HEADING_ROUTES:
        if any(n in q for n in needles) and pattern not in pats:
            pats.append(pattern)
    return pats


def _enrich_query(query: str) -> str:
    """Widen Annexure queries so CARO / IFC vocabulary is present for retrieval."""
    enriched = query
    q_lower = query.lower()
    if "annexure a" in q_lower or "caro" in q_lower:
        if "caro" not in q_lower or "2020" not in q_lower:
            enriched += (
                " CARO 2020 Companies Auditor Report Order clauses property "
                "inventory loans statutory dues"
            )
    if "annexure b" in q_lower or "ifc" in q_lower:
        if "internal financial controls" not in q_lower:
            enriched += (
                " Internal Financial Controls IFC report section 143(3)(i) "
                "operating effectiveness"
            )
    return enriched


# ---------------------------------------------------------------------------
# Document resolution
# ---------------------------------------------------------------------------

def _coerce_fy(fy_start) -> int | None:
    """Best-effort int from whatever the Planner passed (int, '2023', 'FY2023')."""
    m = re.search(r"\d{4}", str(fy_start))
    return int(m.group()) if m else None


def _resolve_doc_ids(company: str, fy_start, *, limit: int = 4) -> List[str]:
    """doc_ids for a company + FY start.

    Mirrors FetchTools.resolve_doc_id's matching (case-insensitive, '_' == ' ')
    but keys on fy_start alone — the chat UI never carries fy_end — and returns
    every match (standalone / consolidated can be separate documents).
    """
    fy = _coerce_fy(fy_start)
    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            if fy is not None:
                cur.execute(
                    """
                    SELECT doc_id FROM documents
                    WHERE LOWER(REPLACE(company, '_', ' ')) = LOWER(REPLACE(%s, '_', ' '))
                      AND fy_start = %s
                    ORDER BY doc_id
                    LIMIT %s
                    """,
                    [company, fy, limit],
                )
                rows = cur.fetchall()
                if not rows:
                    cur.execute(
                        """
                        SELECT doc_id FROM documents
                        WHERE company ILIKE %s AND fy_start = %s
                        ORDER BY doc_id
                        LIMIT %s
                        """,
                        [f"%{company.strip()}%", fy, limit],
                    )
                    rows = cur.fetchall()
            else:
                cur.execute(
                    """
                    SELECT doc_id FROM documents
                    WHERE company ILIKE %s
                    ORDER BY fy_start DESC, doc_id
                    LIMIT %s
                    """,
                    [f"%{company.strip()}%", limit],
                )
                rows = cur.fetchall()
            return [r[0] for r in rows]
    except Exception as exc:
        logger.error("Document resolution failed for company=%s fy=%s: %s", company, fy_start, exc)
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Text retrieval — pgvector primary, keyword fallback
# ---------------------------------------------------------------------------

def _semantic_text_search(doc_ids: List[str], query: str, pool: int) -> List[Dict]:
    """Cosine-similarity search over `text_chunks.embedding`.

    Returns [] (not an error) when the embedding endpoint is down or the
    `embedding` column is absent in this deployment — the caller then relies on
    the keyword fallback.
    """
    if not doc_ids:
        return []
    vec = ReferenceTools._embed(query)
    if not vec:
        logger.info("No query embedding (endpoint unset/unreachable) — keyword fallback only.")
        return []

    conn = _get_db_conn()
    try:
        vec_literal = "[" + ",".join(str(v) for v in vec) + "]"
        ph = ",".join(["%s"] * len(doc_ids))
        sql = f"""
            SELECT content, section, title, page_pdf_start, chunk_id, toc_section,
                   1 - (embedding <=> %s::vector) AS score
            FROM text_chunks
            WHERE doc_id IN ({ph})
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """
        with conn.cursor() as cur:
            cur.execute(sql, [vec_literal] + doc_ids + [vec_literal, pool])
            rows = cur.fetchall()
        return [
            {
                "section": r[1] or r[5] or "Unknown",
                "page": r[3] or 0,
                "kind": "text",
                "content": r[0] or "",
                "score": round(float(r[6] or 0.0), 4),
                "chunk_id": r[4],
            }
            for r in rows
            if (r[0] or "").strip()
        ]
    except Exception as exc:
        logger.warning("text_chunks vector search unavailable (%s) — keyword fallback.", exc)
        return []
    finally:
        conn.close()


def _matches_heading(text: str, patterns: List[str]) -> bool:
    """True if `text` satisfies any of the SQL ILIKE '%phrase%' patterns."""
    t = (text or "").lower()
    return any(p.strip("%").lower() in t for p in patterns)


def _keyword_text_search(doc_ids: List[str], query: str, pool: int) -> List[Dict]:
    """Heading + full-text fallback via FetchTools._fetch_text_chunks (per doc)."""
    heading_pats = _heading_patterns(query)
    tsq = _to_tsquery(query)
    out: List[Dict] = []
    for doc_id in doc_ids:
        try:
            rows = FetchTools._fetch_text_chunks(
                doc_id,
                heading_patterns=heading_pats or None,
                tsquery=tsq,
                limit=pool,
            )
        except Exception as exc:
            logger.error("keyword text search failed for doc_id=%s: %s", doc_id, exc)
            continue
        for r in rows:
            row = (list(r) + [None] * 6)[:6]
            content, section, title, page, chunk_id, toc = row
            if not (content or "").strip():
                continue
            # A row that hit a curated heading route (e.g. "%emphasis of
            # matter%") is a precise structural match for a named section —
            # score it above a typical semantic hit so it isn't squeezed out
            # of the top-K by unrelated-but-similar-sounding embedding
            # matches. Rows that only satisfied the generic tsquery fallback
            # stay low-confidence.
            is_heading_hit = bool(heading_pats) and _matches_heading(
                f"{section} {title} {toc}", heading_pats
            )
            out.append(
                {
                    "section": section or title or toc or "Unknown",
                    "page": page or 0,
                    "kind": "text",
                    "content": content or "",
                    "score": 0.8 if is_heading_hit else 0.5,
                    "chunk_id": chunk_id,
                }
            )
    return out


def _keyword_table_search(doc_ids: List[str], query: str, pool: int) -> List[Dict]:
    """Full-text match over `table_chunks` title/description for the query."""
    if not doc_ids:
        return []
    tsq = _to_tsquery(query)
    if not tsq:
        return []
    conn = _get_db_conn()
    try:
        ph = ",".join(["%s"] * len(doc_ids))
        sql = f"""
            SELECT table_title, table_description, table_md, page_pdf_start, table_id, unit, currency
            FROM table_chunks
            WHERE doc_id IN ({ph})
              AND to_tsvector(
                    'english',
                    COALESCE(table_title, '') || ' ' || COALESCE(table_description, '')
                  ) @@ to_tsquery('english', %s)
            ORDER BY page_pdf_start ASC
            LIMIT %s
        """
        with conn.cursor() as cur:
            cur.execute(sql, list(doc_ids) + [tsq, pool])
            rows = cur.fetchall()
        out: List[Dict] = []
        for title, description, table_md, page, table_id, unit, currency in rows:
            body = (table_md or description or "").strip()
            if not body:
                continue
            out.append(
                {
                    "section": title or "Table",
                    "page": page or 0,
                    "kind": "table",
                    "content": body,
                    "score": 0.45,
                    "table_id": table_id,
                    "unit": unit or "",
                    "currency": currency or "",
                }
            )
        return out
    except Exception as exc:
        logger.warning("table_chunks keyword search failed (%s).", exc)
        return []
    finally:
        conn.close()


def _dedupe(chunks: List[Dict]) -> List[Dict]:
    """Drop repeats, keeping the highest-scoring copy of each chunk."""
    best: Dict[object, Dict] = {}
    for c in chunks:
        key = c.get("chunk_id") or c.get("table_id") or (c.get("kind"), (c.get("content") or "")[:120])
        if key not in best or c.get("score", 0) > best[key].get("score", 0):
            best[key] = c
    return list(best.values())


# ---------------------------------------------------------------------------
# Reranker
# ---------------------------------------------------------------------------

def _rerank(query: str, chunks: List[Dict], top_k: int = _RERANK_TOP_K) -> List[Dict]:
    """Re-rank retrieved chunks, or sort by score when the reranker is off."""
    if not chunks:
        return chunks

    if not USE_RERANKER or not RERANKER_URL:
        return sorted(chunks, key=lambda c: c.get("score", 0), reverse=True)[:top_k]

    texts = [c.get("content") or "" for c in chunks]
    try:
        resp = requests.post(
            RERANKER_URL,
            json={"model": RERANKER_MODEL, "query": query, "documents": texts},
            timeout=30,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        scored = sorted(results, key=lambda r: r.get("relevance_score", 0), reverse=True)
        reranked = [chunks[r["index"]] for r in scored[:top_k] if r["index"] < len(chunks)]
        logger.info("Reranked %d chunks -> kept top %d", len(chunks), len(reranked))
        return reranked
    except Exception as exc:
        logger.warning("Reranker unavailable (%s) — falling back to score order.", exc)
        return sorted(chunks, key=lambda c: c.get("score", 0), reverse=True)[:top_k]


# ---------------------------------------------------------------------------
# Financial tables
# ---------------------------------------------------------------------------

_FS_TYPE_ALIASES = {
    "balance_sheet": "balance_sheet",
    "balance sheet": "balance_sheet",
    "bs": "balance_sheet",
    "profit_loss": "profit_loss",
    "profit and loss": "profit_loss",
    "p&l": "profit_loss",
    "pl": "profit_loss",
    "income_statement": "profit_loss",
    "cash_flow": "cash_flow",
    "cash flow": "cash_flow",
    "cf": "cash_flow",
    "statement_of_equity": "statement_of_equity",
    "statement of changes in equity": "statement_of_equity",
    "soce": "statement_of_equity",
}


def _query_tables_by_type(doc_ids: List[str], fs_type: str) -> List[Dict]:
    """Direct `table_chunks` fetch for one financial-statement type."""
    if not doc_ids:
        return []
    conn = _get_db_conn()
    try:
        ph = ",".join(["%s"] * len(doc_ids))
        sql = f"""
            SELECT table_title, table_md, table_description, unit, currency, page_pdf_start
            FROM table_chunks
            WHERE doc_id IN ({ph})
              AND is_financial = TRUE
              AND financial_stmt_type = %s
            ORDER BY page_pdf_start ASC
        """
        with conn.cursor() as cur:
            cur.execute(sql, list(doc_ids) + [fs_type])
            rows = cur.fetchall()
        out: List[Dict] = []
        for title, table_md, description, unit, currency, page in rows:
            body = (table_md or description or "").strip()
            if not body:
                continue
            out.append(
                {
                    "section": title or fs_type.replace("_", " ").title(),
                    "page": page or 0,
                    "table_md": f"**{title}**\n{table_md}" if title and table_md else body,
                    "unit": unit or "",
                    "currency": currency or "",
                }
            )
        return out
    except Exception as exc:
        logger.error("Table fetch failed for type=%s: %s", fs_type, exc)
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# SARQATools — wired to the Planner's tool processor
# ---------------------------------------------------------------------------

class SARQATools:
    """
    Provides the two retrieval tools given to the Planner agent.

    Tool 1: retrieve_sar_context     — text + table retrieval over the annual report
    Tool 2: retrieve_financial_tables — pinned financial statement tables (BS, P&L, CF)
    """

    def retrieve_sar_context(self, company: str, fy_start: int, query: str) -> str:
        """
        Retrieves relevant text/table chunks from the company's annual report.

        Pipeline: resolve doc_ids -> pgvector similarity (+ keyword fallback)
                  -> dedupe -> optional rerank -> top-K, text/table budgeted.

        Args:
            company:  Company name exactly as in the query context (e.g. "Coal India", "SAIL", "ONGC").
            fy_start: Starting year of the financial period (e.g. 2023 for FY 2023-24).
            query:    Focused search phrase for the specific topic.

        Returns:
            JSON string: a list of chunks with section/page citations, or {"error": ...}.
        """
        enriched_query = _enrich_query(query)
        logger.info(
            "retrieve_sar_context: company=%s fy_start=%s query='%s' (enriched='%s')",
            company, fy_start, query, enriched_query,
        )
        try:
            doc_ids = _resolve_doc_ids(company, fy_start)
            if doc_ids:
                logger.info("Resolved %d doc(s) for %s FY %s: %s", len(doc_ids), company, fy_start, doc_ids)
            else:
                logger.warning("No documents resolved for '%s %s'.", company, fy_start)
                return json.dumps(
                    {"message": f"No ingested document found for {company} FY starting {fy_start}."}
                )

            semantic = _semantic_text_search(doc_ids, enriched_query, _RETRIEVAL_POOL)
            keyword = _keyword_text_search(doc_ids, enriched_query, _RETRIEVAL_POOL)
            tables = _keyword_table_search(doc_ids, enriched_query, _RETRIEVAL_POOL)

            text_hits = _dedupe(semantic + keyword)
            table_hits = _dedupe(tables)

            # text + tables share _RERANK_TOP_K slots; tables get <= 40%.
            max_tables = max(1, round(_RERANK_TOP_K * 0.4))
            top_tables = sorted(table_hits, key=lambda r: r.get("score", 0), reverse=True)[:max_tables]
            text_k = _RERANK_TOP_K - len(top_tables)
            reranked_text = _rerank(enriched_query, text_hits, top_k=text_k)

            top_chunks = reranked_text + top_tables
            logger.info(
                "Chunk budget: %d text + %d table(s) = %d total",
                len(reranked_text), len(top_tables), len(top_chunks),
            )

            if not top_chunks:
                return json.dumps(
                    {"message": "No matching passages found in the report for this query."}
                )

            formatted = [
                {
                    "section": c.get("section", "Unknown"),
                    "page": c.get("page", 0),
                    "kind": c.get("kind", "text"),
                    "content": c.get("content", ""),
                    "score": round(c.get("score", 0), 4),
                }
                for c in top_chunks
            ]
            return json.dumps(formatted, indent=2)

        except Exception as exc:
            logger.error("Error in retrieve_sar_context: %s", exc)
            return json.dumps({"error": f"Failed to retrieve context: {exc}"})

    def retrieve_financial_tables(self, company: str, fy_start: int, statement_type: str) -> str:
        """
        Retrieves a specific financial statement table (Balance Sheet, P&L, Cash Flow,
        Statement of Changes in Equity).

        Use when the question requires specific financial figures, totals, or ratios.

        Args:
            company:        Company name (e.g. "Coal India", "SAIL", "ONGC").
            fy_start:       Starting year of the financial period (e.g. 2023).
            statement_type: One of "balance_sheet", "profit_loss", "cash_flow", "statement_of_equity".

        Returns:
            JSON string: a list of markdown tables, or a {"message": ...} note.
        """
        norm = _FS_TYPE_ALIASES.get((statement_type or "").strip().lower())
        logger.info(
            "retrieve_financial_tables: company=%s fy_start=%s type=%s (norm=%s)",
            company, fy_start, statement_type, norm,
        )
        try:
            doc_ids = _resolve_doc_ids(company, fy_start)
            if not doc_ids:
                return json.dumps(
                    {"message": f"No ingested document found for {company} FY starting {fy_start}."}
                )

            formatted_tables: List[Dict] = []

            # Fast path for the three types FetchTools pins directly.
            if norm in {"balance_sheet", "profit_loss", "cash_flow"}:
                res = FetchTools.fetch_financial_tables(doc_ids[0])
                if res.is_usable():
                    tbl = (res.data or {}).get(norm)
                    if tbl:
                        formatted_tables.append(
                            {
                                "section": tbl.get("table_title", norm),
                                "page": tbl.get("page_no", 0),
                                "table_md": tbl.get("table_md", ""),
                                "unit": tbl.get("unit", ""),
                                "currency": tbl.get("currency", ""),
                            }
                        )

            # Fallback / other types (e.g. statement_of_equity): query directly,
            # across every resolved doc.
            if not formatted_tables:
                target = norm or (statement_type or "").strip().lower()
                formatted_tables = _query_tables_by_type(doc_ids, target)

            if not formatted_tables:
                return json.dumps({"message": "No financial tables found for the specified parameters."})

            return json.dumps(formatted_tables, indent=2)

        except Exception as exc:
            logger.error("Error in retrieve_financial_tables: %s", exc)
            return json.dumps({"error": f"Failed to retrieve tables: {exc}"})
