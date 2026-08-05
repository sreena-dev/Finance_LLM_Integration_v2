"""Source-row dedup/serialization, copied from ``yukta_rag.chat.pipeline``.

PATCHED for this integration: ``chat/pipeline.py`` (the full chat/RAG feature)
was deliberately not vendored here — Trial Balance only ever needed these
three self-contained helper functions from it, and pulling in the whole
module would drag in ``agents/agents.py``, ``chat/guardrails.py`` and
``uploads/uploads.py`` for no reason. Copied verbatim rather than reimplemented
so behaviour (incl. the ind_as/eac/sa700/... source-tag formatting) matches
the original exactly.
"""

from __future__ import annotations


def _hashable(v):
    """Make a value usable in a set/dict key. Reference rows are SELECT * dicts, so a
    field can be a Postgres array (psycopg2 -> list) which is unhashable; coerce
    lists/dicts to tuples recursively, leave scalars as-is."""
    if isinstance(v, (list, tuple)):
        return tuple(_hashable(x) for x in v)
    if isinstance(v, dict):
        return tuple(sorted((k, _hashable(x)) for k, x in v.items()))
    return v


def _serialize_source(r: dict) -> dict:
    """Project a raw retrieval row into a compact JSON-safe source record."""
    if r.get("source") == "upload":
        return {
            "source": "upload",
            "label": r.get("filename") or "Uploaded document",
            "page": r.get("page_no"),
            "score": round(float(r.get("score", 0)), 4),
            "snippet": (r.get("text") or "")[:300],
        }
    if r.get("source") == "ind_as":
        return {
            "source": "ind_as",
            "label": f"Ind AS {r.get('standard_number')} — {r.get('section_title')}",
            "page": r.get("page_no"),
            "paragraph_no": r.get("paragraph_no"),
            "score": round(float(r.get("score", 0)), 4),
            "snippet": (r.get("text") or "")[:300],
        }
    if r.get("source") in ("eac", "sa700", "schedule_iii", "caro", "ind_as_appendix"):
        from yukta_rag.retrieval.reference_retrieval import _tag
        pg = r.get("page_no")
        return {
            "source": r["source"],
            "label": _tag(r).strip("[]"),
            "page": pg if isinstance(pg, int) else None,
            "score": round(float(r.get("score", 0)), 4),
            "snippet": (r.get("text") or "")[:300],
        }
    fy = f"FY{r.get('fy_start')}-{str(r.get('fy_end'))[-2:]}"
    return {
        "source": "annual_report",
        "label": f"{r.get('company')} {fy} ({r.get('kind')})",
        "page": r.get("page"),
        "section": r.get("section"),
        "score": round(float(r.get("score", 0)), 4),
        "snippet": (r.get("content") or "")[:300],
    }


def _dedupe_sources(rows: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in rows:
        key = _hashable((
            r.get("source"),
            r.get("company") or r.get("standard_number"),
            r.get("page") or r.get("page_no"),
            (r.get("content") or r.get("text") or "")[:40],
        ))
        if key in seen:
            continue
        seen.add(key)
        out.append(_serialize_source(r))
    return out
