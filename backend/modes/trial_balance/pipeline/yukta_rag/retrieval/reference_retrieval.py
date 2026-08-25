"""Dense retrieval over the reference corpora (financial_llm / REFERENCE_DSN).

Newly-ingested authoritative references, each embedded with the same bge-m3 model
as the main corpora:
  * EAC opinions   — ICAI Expert Advisory Committee (eac_query, eac_general, eac_table)
  * SA 700         — auditor's report standard (sa_700_chunks)
  * Schedule III   — Companies Act presentation format (schedule_iii_chunks + tables)
  * CARO / CAG     — CARO 2020 and CAG directions (cag_caro_chunks, cag_directions_chunks)
  * Ind AS appendix — illustrative guidance appendices (ind_as_appendix)

Each ``retrieve_*`` does a cosine-distance search and returns rows tagged with a
``source`` and a citation-ready ``text``. Column lists are introspected once per
table (cached), so the code is robust to schema differences. Every query is wrapped
so an outage of the reference DB degrades to an empty result rather than breaking
the caller.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from yukta_rag.core.db import get_reference_connection
from yukta_rag.core.embeddings import to_vector_literal

logger = logging.getLogger("yukta_rag.retrieval.reference_retrieval")

# preferred text field per row, most-specific first (used for the excerpt body)
_TEXT_PRIORITY = ("chunk", "txt", "text", "content", "opinion", "points", "facts",
                  "query", "query_subject", "table_html", "table_content")

_col_cache: dict[str, list[str]] = {}


def _table_columns(table: str) -> list[str]:
    """Column names of ``table`` except the embedding vector (cached per process)."""
    if table in _col_cache:
        return _col_cache[table]
    conn = get_reference_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s AND table_schema = 'public' ORDER BY ordinal_position",
                (table,),
            )
            cols = [r[0] for r in cur.fetchall() if r[0] != "embedding"]
    finally:
        conn.close()
    _col_cache[table] = cols
    return cols


def _dense(table: str, vec: str, top_k: int) -> list[dict]:
    """Top-k rows of ``table`` by cosine similarity to ``vec`` (empty on any error)."""
    try:
        cols = _table_columns(table)
        if not cols:
            return []
        collist = ", ".join(f'"{c}"' for c in cols)
        sql = (f"SELECT {collist}, 1 - (embedding <=> %s::vector) AS score "
               f'FROM "{table}" WHERE embedding IS NOT NULL '
               f"ORDER BY embedding <=> %s::vector LIMIT %s")
        conn = get_reference_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (vec, vec, top_k))
                names = [d.name for d in cur.description]
                return [dict(zip(names, r)) for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - reference DB is optional context
        logger.warning("reference retrieval failed for %s: %s", table, exc)
        return []


def _body(row: dict) -> str:
    for c in _TEXT_PRIORITY:
        v = row.get(c)
        if v and str(v).strip():
            return str(v).strip()
    # fallback: the longest string value
    strings = [str(v) for v in row.values() if isinstance(v, str) and v.strip()]
    return max(strings, key=len) if strings else ""


def _merge_top(rows: list[dict], top_k: int) -> list[dict]:
    return sorted(rows, key=lambda r: r.get("score", 0.0), reverse=True)[:top_k]


# ---------------------------------------------------------------------------
# Per-corpus retrieval
# ---------------------------------------------------------------------------


def retrieve_eac(query: str, top_k: int = 4, vec: str | None = None) -> list[dict]:
    """EAC opinions: the committee's query/opinion plus the reasoning chunks."""
    vec = vec or to_vector_literal(query)
    rows = _dense("eac_query", vec, top_k) + _dense("eac_general", vec, top_k)
    for r in rows:
        r["source"] = "eac"
        r["text"] = _body(r)
    return _merge_top(rows, top_k)


def retrieve_sa700(query: str, top_k: int = 4, vec: str | None = None) -> list[dict]:
    """SA 700 (Revised) — forming an opinion and reporting on financial statements."""
    vec = vec or to_vector_literal(query)
    rows = _dense("sa_700_chunks", vec, top_k)
    for r in rows:
        r["source"] = "sa700"
        r["text"] = _body(r)
    return rows


def retrieve_schedule_iii(query: str, top_k: int = 4, vec: str | None = None) -> list[dict]:
    """Schedule III presentation format (narrative chunks + tables)."""
    vec = vec or to_vector_literal(query)
    rows = _dense("schedule_iii_chunks", vec, top_k) + \
        _dense("schedule_iii_table_chunks", vec, max(2, top_k // 2))
    for r in rows:
        r["source"] = "schedule_iii"
        r["text"] = _body(r)
    return _merge_top(rows, top_k)


def retrieve_caro(query: str, top_k: int = 4, vec: str | None = None) -> list[dict]:
    """CARO 2020 and CAG directions."""
    vec = vec or to_vector_literal(query)
    rows = _dense("cag_caro_chunks", vec, top_k) + _dense("cag_directions_chunks", vec, top_k)
    for r in rows:
        r["source"] = "caro"
        r["text"] = _body(r)
    return _merge_top(rows, top_k)


def retrieve_ind_as_appendix(query: str, top_k: int = 4, vec: str | None = None) -> list[dict]:
    """Ind AS appendices — illustrative guidance / application examples."""
    vec = vec or to_vector_literal(query)
    rows = _dense("ind_as_appendix", vec, top_k)
    for r in rows:
        r["source"] = "ind_as_appendix"
        r["text"] = _body(r)
    return rows


_RETRIEVERS = {
    "eac": retrieve_eac,
    "sa700": retrieve_sa700,
    "schedule_iii": retrieve_schedule_iii,
    "caro": retrieve_caro,
    "ind_as_appendix": retrieve_ind_as_appendix,
}


def retrieve_reference(query: str, per_corpus_k: int = 3,
                       corpora: list[str] | None = None,
                       vec: str | None = None) -> list[dict]:
    """Fan out across the reference corpora in parallel; return merged tagged hits."""
    names = corpora or list(_RETRIEVERS)
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        results = pool.map(lambda n: _RETRIEVERS[n](query, per_corpus_k, vec=vec), names)
    merged: list[dict] = []
    for rs in results:
        merged.extend(rs)
    return merged


# ---------------------------------------------------------------------------
# Citation-tagged formatting (metadata in the tag)
# ---------------------------------------------------------------------------


def _tag(row: dict) -> str:
    src = row.get("source")
    dn = row.get("doc_name") or ""
    if src == "eac":
        period = row.get("opinion_period")
        subj = row.get("query_subject") or ""
        qn = row.get("query_no")
        parts = ["EAC opinion"]
        if qn is not None:
            parts.append(f"query {qn}")
        if period:
            parts.append(str(period))
        base = ", ".join(parts)
        return f"[{base}{(' — ' + subj[:60]) if subj else ''}]"
    if src == "sa700":
        para = row.get("para_no") or row.get("section_title") or ""
        return f"[SA 700{(', ' + str(para)) if para else ''}]"
    if src == "schedule_iii":
        bits = [b for b in (row.get("division_title"), row.get("part_id"),
                            row.get("section_title")) if b]
        return f"[Schedule III{(', ' + ' / '.join(map(str, bits))) if bits else ''}]"
    if src == "caro":
        ref = row.get("notification_no") or dn or "CARO/CAG"
        return f"[{dn or 'CARO 2020'}{(', ' + str(ref)) if ref and ref != dn else ''}]"
    if src == "ind_as_appendix":
        sec = row.get("section_title") or row.get("standard_number") or ""
        return f"[Ind AS appendix{(' — ' + str(sec)[:50]) if sec else ''}]"
    return f"[{dn or 'reference'}]"


def reference_blocks(rows: list[dict], cap: int = 1200) -> list[str]:
    """One citation-tagged block per reference hit (body capped)."""
    out = []
    for r in rows:
        body = (r.get("text") or "").strip()
        if len(body) > cap:
            body = body[:cap].rstrip() + " …"
        out.append(f"{_tag(r)}\n{body}")
    return out


def format_reference(rows: list[dict]) -> str:
    return "\n\n---\n\n".join(reference_blocks(rows)) if rows else "No reference results found."
