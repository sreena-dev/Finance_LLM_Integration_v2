"""Evidence retrieval over one entity's filings.

WHAT THIS IS FOR
----------------
The deterministic path answers questions the diagnostics compute: whether a
signal fired, which figures are missing, what a bound line item was across the
years. It cannot answer anything the statements did not bind — the registered
office, the leases accounting policy, why an auditor added an emphasis of
matter. Those live in the report's narrative, and reaching them means search.

So this module exists for the questions the panel cannot serve, and only those.
A diagnostic verdict is never retrieved and never generated.

THE ARMS, AND WHY THERE ARE SEVERAL
-----------------------------------
No single retrieval method is reliable on this corpus, and they fail
differently, which is exactly why they are fused rather than chosen between:

  statement arm   the primary statements matched by their (very clean) titles,
                  flavour-preferred. Deterministic: a balance-sheet question
                  reaches the balance sheet even if every other arm misfires.
  identity anchor the corporate-information block, matched on CIN / registered
                  office / incorporation. An identity question has a known home,
                  and finding it should not depend on a model being up.
  semantic arm    pgvector over the 993k embedded narrative chunks. Finds
                  passages that answer the question in different words.
  keyword arm     Postgres full-text, title weighted above body. Catches exact
                  terms — a note reference, a statute, a defined term — which
                  embeddings routinely miss.

They are unioned into a candidate pool, deduplicated, and handed to the
cross-encoder, which is the only stage that judges relevance properly. The arms
optimise for RECALL; `rerank` supplies the precision.

A DEGRADED ARM IS NOT A FAILED QUERY
------------------------------------
The semantic arm needs the embedding endpoint. If it is down, the arm is skipped
and the others still answer — with the degradation named in the report, so a
thin result is explained rather than mysterious.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from . import clients
from . import config as CFG
from . import expansion as EXP
from . import fusion as FUSE

logger = logging.getLogger(__name__)

# The primary statements, by the titles this corpus actually uses.
_STMT_TITLE_PAT = r"(balance sheet|statement of profit|profit and loss|cash flow|changes in equity)"

# A question about who the entity IS, rather than a figure on the face of a
# statement. These are answered from narrative text, so the text arms lead.
_QUALITATIVE = re.compile(
    r"\b(legal name|name of the (company|entity)|\bcin\b|corporate identity|"
    r"registered office|address|incorporat|principal business|business activit|"
    r"nature of business|line of business|industry|sector|regulat|sebi|rbi|irdai|"
    r"listed|unlisted|subsidiar|holding company|group structure|board of directors|"
    r"managing director|key managerial|auditor'?s?\b|emphasis of matter|going concern|"
    r"related part(y|ies)|accounting polic|significant polic|basis of preparation|"
    r"contingent liabilit|commitment|litigation)", re.I)
# The trailing \b is deliberately ABSENT. Several alternatives above are
# truncated STEMS ("accounting polic", "incorporat", "subsidiar", "contingent
# liabilit") chosen so they match every inflection. A word boundary after the
# stem requires the word to end there — so "accounting policy", the exact phrase
# the stem exists to catch, never matched at all. Every truncated alternative in
# the pattern was dead. The source project's version carries the same closing
# \b and the same dead alternatives; this is a fix, not a port.
# NOTE — "overview", "describe", "profile" and "about the company" were in the
# source project's version of this pattern and are deliberately absent here.
# In that project they meant "tell me about this company"; in this mode
# "overview" is the name of a diagnostic answer ("what does this entity raise?"),
# so including them let a narrative regex intercept the mode's own core
# question. Nothing is lost by dropping them: a genuinely open question like
# "tell me about the company" matches no diagnostic either, so it reaches
# retrieval through the unmatched fall-through anyway.

_IDENTITY_ANCHOR = re.compile(
    r"\b(legal name|name of|\bcin\b|corporate identity|registered office|incorporat|"
    r"regulat|listed|unlisted|sebi|rbi|irdai)", re.I)   # no trailing \b — see above


def is_qualitative(query: str) -> bool:
    return bool(_QUALITATIVE.search(query or ""))


def _flavor(query: str) -> str:
    return "consolidated" if re.search(r"consolidat", query, re.I) else "standalone"


def _q(sql: str, params: tuple) -> list[dict]:
    from fs_db import db
    return db.query(sql, params)


# ---------------------------------------------------------------------------
# Which filing
# ---------------------------------------------------------------------------

def _fy_end_from(query: str) -> int | None:
    """The financial year the question names, as its END year.

    Indian financial years are written for the span they cover: "FY2024-25" and
    "2024-25" both end in 2025, while a bare "2025" is already the end year.
    Getting this wrong silently answers from the neighbouring filing, which is
    the kind of error that is completely invisible in a well-formatted answer.
    """
    spans = re.search(r"(?:FY\s*)?(\d{4})\s*[-/]\s*(\d{2,4})", query or "", re.I)
    if spans:
        return int(spans.group(1)) + 1
    bare = re.search(r"\b(19|20)(\d{2})\b", query or "")
    return int(bare.group(0)) if bare else None


def resolve_doc(entity_id: str, query: str) -> dict | None:
    """The filing this question should be answered from.

    The entity is never guessed — it comes from the picker. Only the YEAR is
    inferred, and only from the question naming one; with no year named the
    latest filing is used, and the answer says which.
    """
    fy_end = _fy_end_from(query)
    if fy_end:
        rows = _q("select doc_id, company, fy_start, fy_end from documents "
                  "where company = %s and fy_end = %s limit 1", (entity_id, fy_end))
        if rows:
            return {**rows[0], "year_source": "named in the question"}

    rows = _q("select doc_id, company, fy_start, fy_end from documents "
              "where company = %s order by fy_end desc limit 1", (entity_id,))
    if not rows:
        return None
    return {**rows[0], "year_source": ("latest filing (no year named)" if not fy_end
                                       else f"latest filing (no {fy_end} filing held)")}


def fy_label(row: dict) -> str:
    end = row.get("fy_end")
    return f"FY{int(end) - 1}-{str(int(end))[-2:]}" if end else "unknown"


# ---------------------------------------------------------------------------
# The arms
# ---------------------------------------------------------------------------

def _tables_by_title(doc_id: str, query: str, limit: int) -> list[dict]:
    return _q(f"""
        select table_id, table_title, financial_stmt_type, page_pdf_start, table_md
        from table_chunks
        where doc_id = %s and table_md is not null and table_title ~* %s
        order by (table_title ilike %s) desc,
                 (table_title ~* '(restat|reconcil|five year|segment)') asc,
                 length(table_title) asc
        limit %s
    """, (doc_id, _STMT_TITLE_PAT, f"%{_flavor(query)}%", limit))


def _tables_by_keyword(doc_id: str, query: str, limit: int) -> list[dict]:
    if not query.strip():
        return []
    return _q("""
        select table_id, table_title, financial_stmt_type, page_pdf_start, table_md
        from table_chunks
        where doc_id = %s and table_md is not null
          and (setweight(to_tsvector('english', coalesce(table_title,'')),'A') ||
               setweight(to_tsvector('english', coalesce(table_md,'')),'D'))
              @@ websearch_to_tsquery('english', %s)
        order by ts_rank_cd(
                   setweight(to_tsvector('english', coalesce(table_title,'')),'A') ||
                   setweight(to_tsvector('english', coalesce(table_md,'')),'D'),
                   websearch_to_tsquery('english', %s), 32) desc
        limit %s
    """, (doc_id, query, query, limit))


def _text_anchor(doc_id: str, limit: int = 3) -> list[dict]:
    return _q("""
        select chunk_id, section, title, page_pdf_start, content
        from text_chunks
        where doc_id = %s
          and (content ilike '%%registered office%%' or content ~* '\\mCIN\\M'
               or content ilike '%%corporate identity number%%'
               or content ilike '%%incorporated%%')
        order by length(content) asc
        limit %s
    """, (doc_id, limit))


def _text_semantic(doc_id: str, vector: str, limit: int) -> list[dict]:
    """Vector arm. Takes a PREPARED pgvector literal, not a query string, so the
    caller can embed every phrasing in one round trip instead of one each."""
    return _q("""
        select chunk_id, section, title, page_pdf_start, content
        from text_chunks
        where doc_id = %s and embedding is not null
          and chunk_type in ('text','list','subsection_summary','section_summary')
          and length(content) >= 80
        order by embedding <=> %s::vector
        limit %s
    """, (doc_id, vector, limit))


def _text_keyword(doc_id: str, query: str, limit: int) -> list[dict]:
    if not query.strip():
        return []
    return _q("""
        select chunk_id, section, title, page_pdf_start, content
        from text_chunks
        where doc_id = %s and content is not null
          and chunk_type in ('text','list','subsection_summary')
          and to_tsvector('english', content) @@ websearch_to_tsquery('english', %s)
        order by ts_rank_cd(to_tsvector('english', content),
                            websearch_to_tsquery('english', %s), 32) desc
        limit %s
    """, (doc_id, query, query, limit))


def _as_table(row: dict, arm: str = "") -> dict:
    return {"kind": "table", "id": str(row["table_id"]), "arm": arm,
            "title": row.get("table_title") or row.get("financial_stmt_type") or "table",
            "stmt_type": row.get("financial_stmt_type"),
            "page": row.get("page_pdf_start"), "content": row.get("table_md") or ""}


def _as_text(row: dict, arm: str = "") -> dict:
    return {"kind": "text", "id": str(row["chunk_id"]), "arm": arm,
            "title": row.get("section") or row.get("title") or "Report text",
            "stmt_type": None, "page": row.get("page_pdf_start"),
            "content": row.get("content") or ""}


# ---------------------------------------------------------------------------
# Gather
# ---------------------------------------------------------------------------

def gather(doc_id: str, query: str, *, expand: bool = True
           ) -> tuple[list[dict], dict[str, Any]]:
    """Every arm, over every phrasing, fused by Reciprocal Rank Fusion.

    Each arm returns a RANKED list and keeps it that way — the fusion needs the
    ranks, and the previous design threw them away by concatenating arms in a
    fixed priority order. A chunk found by three arms now outranks one found by
    one, which is the cheapest strong relevance signal available here.

    Arms are run once per query phrasing (`expansion.expand`), so a question
    asking about "debtors" also searches for "trade receivables" — the words the
    statements actually print.
    """
    settings = CFG.load()
    pool = settings.rerank_pool
    qualitative = is_qualitative(query)
    report: dict[str, Any] = {"arms": {}, "degraded": []}

    expansion = EXP.expand(query, enable=expand and settings.query_expansion)
    # Fan-out is capped. Every extra phrasing costs one vector query and one
    # full-text query per arm, and the marginal recall past the third falls off
    # sharply — the source project caps at the same number for the same reason.
    queries = expansion["queries"][: settings.max_query_variants]
    report["expansion"] = {"method": expansion["method"], "variants": expansion["variants"]}

    ranklists: list[list[dict]] = []

    def arm(name: str, rows: list[dict], mapper) -> None:
        """Record one ranked list. Order within it is the arm's own ranking."""
        mapped = [mapper(r, name) for r in rows]
        report["arms"][name] = report["arms"].get(name, 0) + len(mapped)
        if mapped:
            ranklists.append(mapped)

    # ---- deterministic arms: run once, on the original query only -----------
    # Neither depends on wording, so extra phrasings would return the same rows
    # and inflate their RRF contribution for no gain.
    try:
        if qualitative and _IDENTITY_ANCHOR.search(query or ""):
            arm("identity_anchor", _text_anchor(doc_id), _as_text)
    except Exception as exc:  # noqa: BLE001
        report["degraded"].append(f"identity anchor failed ({type(exc).__name__})")

    try:
        arm("statements", _tables_by_title(doc_id, query, 5 if not qualitative else 2),
            _as_table)
    except Exception as exc:  # noqa: BLE001
        report["degraded"].append(f"statement arm failed ({type(exc).__name__})")

    # ---- wording-sensitive arms: run per phrasing ---------------------------
    table_budget = 4 if qualitative else pool
    for phrasing in queries:
        try:
            arm("table_keyword", _tables_by_keyword(doc_id, phrasing, table_budget),
                _as_table)
        except Exception as exc:  # noqa: BLE001
            report["degraded"].append(f"table keyword arm failed ({type(exc).__name__})")

    # Every phrasing embedded in ONE call, then one vector query each.
    try:
        vectors = clients.embed_many(queries)
        for vector in vectors:
            arm("text_semantic",
                _text_semantic(doc_id, clients.to_pgvector(vector), pool), _as_text)
    except clients.EndpointError as exc:
        # The one arm with an external dependency. Named in the report so a thin
        # answer is explained by the outage rather than read as a thin filing.
        report["degraded"].append(f"semantic arm skipped — {exc.detail}")
    except Exception as exc:  # noqa: BLE001
        report["degraded"].append(f"semantic arm failed ({type(exc).__name__})")

    for phrasing in queries:
        try:
            arm("text_keyword", _text_keyword(doc_id, phrasing, pool), _as_text)
        except Exception as exc:  # noqa: BLE001
            report["degraded"].append(f"keyword arm failed ({type(exc).__name__})")
            break

    ranked = FUSE.fuse(ranklists, query, qualitative=qualitative)
    deduped, dropped = FUSE.mmr_dedup(ranked)

    report["candidates"] = len(deduped)
    report["fused_from"] = len(ranklists)
    report["near_duplicates_dropped"] = dropped
    report["qualitative"] = qualitative
    return deduped, report
