"""Dynamic, retrieval-driven citations for audit findings and gaps.

For each finding/gap/extracted risk item the scenario text is embedded once
(batched) and searched across ALL corpora — the Ind AS standards corpus
(FINANCE_DSN) and the reference corpora (Schedule III, CARO/CAG, SA 700, EAC
opinions, Ind AS appendices) — so every observation is mapped to whichever
authoritative documents are actually relevant, not to a predefined table.
Citations degrade to empty lists on any retrieval failure; they never break
the audit.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor

from yukta_rag.core.embeddings import embed_texts
from yukta_rag.retrieval.reference_retrieval import _tag, retrieve_reference
from yukta_rag.retrieval.retrieval import retrieve_ind_as

logger = logging.getLogger("yukta_rag.audit.audit_citations")

_STANDARD_RE = re.compile(r"ind\s*as[\s\-]*?(\d{1,3})", re.I)

_SNIPPET_CAP = 300


def parse_standard_number(text: str) -> list[int] | None:
    """Extract Ind AS numbers cited in ``text`` (e.g. "Ind AS 23" -> [23])."""
    nums = [int(m) for m in _STANDARD_RE.findall(text or "")]
    return sorted(set(nums)) or None


def scenario_query(item: dict) -> str:
    """Deterministic retrieval query for one finding/gap/risk item (~300 chars)."""
    parts = []
    for key in ("fsli", "name", "area", "account", "item", "statute"):
        v = item.get(key)
        if v and str(v).strip() and str(v) not in parts:
            parts.append(str(v).strip())
    for key in ("observation", "gap", "risk", "detail", "requirement"):
        v = item.get(key)
        if v and str(v).strip():
            parts.append(str(v).strip())
    text = "; ".join(parts)
    # strip amounts — figures add noise to a semantic search over standards text
    text = re.sub(r"[\d,]{4,}(\.\d+)?", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:300]


def _snippet(text: str) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    return t[:_SNIPPET_CAP] + (" …" if len(t) > _SNIPPET_CAP else "")


def _indas_citation(row: dict) -> dict:
    label = f"Ind AS {row.get('standard_number')}"
    if row.get("paragraph_no"):
        label += f", para {row['paragraph_no']}"
    if row.get("page_no") is not None:
        label += f", p{row['page_no']}"
    return {
        "source": "ind_as",
        "label": label,
        "standard_number": row.get("standard_number"),
        "paragraph_no": row.get("paragraph_no"),
        "page_no": row.get("page_no"),
        "snippet": _snippet(row.get("text", "")),
        "score": round(float(row.get("score") or 0.0), 4),
    }


def _scalar(v):
    """Reference-DB fields can be Postgres arrays -> lists; collapse to a scalar."""
    if isinstance(v, (list, tuple)):
        return v[0] if v else None
    return v


def _reference_citation(row: dict) -> dict:
    return {
        "source": row.get("source", "reference"),
        "label": _tag(row).strip("[]"),
        "standard_number": _scalar(row.get("standard_number")),
        "paragraph_no": _scalar(row.get("para_no") or row.get("paragraph_no")),
        "page_no": _scalar(row.get("page_no")),
        "snippet": _snippet(row.get("text", "")),
        "score": round(float(row.get("score") or 0.0), 4),
    }


def retrieve_citations(query: str, vec: str | None = None,
                       standard_numbers: list[int] | None = None,
                       indas_k: int = 3, per_corpus_k: int = 2,
                       corpora: list[str] | None = None,
                       min_score: float = 0.45,
                       max_citations: int = 5) -> list[dict]:
    """Citations for one scenario: Ind AS chunks + reference corpora, merged.

    ``corpora=["schedule_iii"]`` (etc.) restricts the reference fan-out;
    ``corpora=[]`` skips the reference DB entirely.
    """
    if not query:
        return []
    citations: list[dict] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_indas = pool.submit(retrieve_ind_as, query, indas_k, standard_numbers, vec)
        f_ref = (pool.submit(retrieve_reference, query, per_corpus_k, corpora, vec)
                 if corpora is None or corpora else None)
        try:
            citations += [_indas_citation(r) for r in f_indas.result()]
        except Exception as exc:  # noqa: BLE001 - citations are optional context
            logger.warning("Ind AS citation retrieval failed: %s", exc)
        if f_ref is not None:
            try:
                citations += [_reference_citation(r) for r in f_ref.result()]
            except Exception as exc:  # noqa: BLE001
                logger.warning("reference citation retrieval failed: %s", exc)
    citations = [c for c in citations if c["score"] >= min_score]
    seen: set = set()
    unique = []
    for c in sorted(citations, key=lambda c: c["score"], reverse=True):
        key = (c["source"], str(c.get("standard_number")), str(c.get("page_no")),
               c["snippet"][:40])
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique[:max_citations]


def citations_for_items(items: list[tuple[str, list[int] | None]],
                        max_items: int = 36,
                        corpora_per_item: list[list[str] | None] | None = None,
                        min_score: float = 0.45) -> list[list[dict]]:
    """Batched citations: one embedding call for all queries, parallel searches.

    ``items`` is ``[(query, restrict_standard_numbers_or_None), ...]``;
    ``corpora_per_item`` optionally restricts the reference corpora per item.
    Returns one citation list per item (same order); any per-item failure
    yields ``[]`` for that item only.
    """
    items = items[:max_items]
    if corpora_per_item is not None:
        corpora_per_item = corpora_per_item[:max_items]
    if not items:
        return []
    unique_queries = list(dict.fromkeys(q for q, _ in items if q))
    vec_by_query: dict[str, str] = {}
    try:
        vectors = embed_texts(unique_queries)
        vec_by_query = {
            q: "[" + ",".join(str(x) for x in v) + "]"
            for q, v in zip(unique_queries, vectors)
        }
    except Exception as exc:  # noqa: BLE001 - fall back to per-query embedding
        logger.warning("batch embedding for citations failed: %s", exc)

    def one(i: int) -> list[dict]:
        query, standards = items[i]
        corpora = corpora_per_item[i] if corpora_per_item is not None else None
        try:
            return retrieve_citations(query, vec=vec_by_query.get(query),
                                      standard_numbers=standards,
                                      corpora=corpora, min_score=min_score)
        except Exception as exc:  # noqa: BLE001
            logger.warning("citation retrieval failed for item %d: %s", i, exc)
            return []

    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(one, range(len(items))))
