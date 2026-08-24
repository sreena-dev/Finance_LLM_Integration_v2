"""Reciprocal Rank Fusion, domain boosts and near-duplicate dedup.

WHAT THIS REPLACES, AND WHY IT MATTERED
---------------------------------------
`retrieval.gather` used to concatenate the arms in a fixed priority order and
deduplicate by first-seen. That makes ARM ORDER the ranking: a chunk found by the
semantic arm, the keyword arm and the statement arm alike scored exactly the same
as one found by a single arm, and the only thing separating them was which arm
happened to run first. Agreement between independent retrievers is the single
strongest cheap relevance signal there is, and that design threw all of it away.

RRF (Cormack et al.) fixes it with one property: a document's score is the sum of
`1/(k + rank)` over every list it appears in. Appearing at rank 3 in two arms
beats appearing at rank 1 in one. It needs no score calibration between arms —
which matters here because the arms are not commensurable at all: pgvector
returns cosine distance, Postgres full-text returns `ts_rank_cd`, and the
statement arm returns nothing but an ordering. RRF compares only RANKS, so it can
fuse them without pretending their scores mean the same thing.

THE BOOSTS ARE DOMAIN KNOWLEDGE RRF CANNOT HAVE
------------------------------------------------
RRF is domain-blind. Two additions carry knowledge specific to this corpus:

  title match      a chunk whose SECTION TITLE matches the question, measured
                   against the title alone rather than title-concatenated-with-
                   body. Body text repeats a topic's vocabulary constantly, so a
                   bag-of-words score over the concatenation cannot tell
                   "Leases — measurement" from any other passage that mentions
                   leases. The title alone can.
  statement match  a figure question should reach a primary statement, and the
                   statement arm's hits are the ones that are one by construction.

Both are small additive terms on top of the fused rank, never replacements for
it: a boost tips a close call, it does not manufacture a result.
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable

# The RRF constant. 60 is the value from the original paper and the one the
# source project uses; it flattens the contribution of deep ranks so a document
# scraping in at rank 40 of one arm cannot outweigh agreement near the top.
RRF_K = 60

_STOPWORDS = {
    "a", "an", "the", "of", "to", "in", "on", "for", "and", "or", "is", "are",
    "was", "were", "what", "how", "why", "which", "who", "does", "do", "did",
    "shall", "must", "under", "this", "that", "these", "those", "with", "by",
    "as", "at", "from", "required", "require", "requires", "about", "company",
}


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^0-9a-zA-Z]+", (text or "").lower())
            if len(t) > 1 and t not in _STOPWORDS}


def rrf(ranklists: Iterable[list[dict]], k: int = RRF_K) -> dict[tuple, dict]:
    """Fuse any number of ranked lists. Key is `(kind, id)` so the same chunk
    surfacing from several arms fuses into one entry that records all of them."""
    fused: dict[tuple, dict] = {}
    for ranklist in ranklists:
        for rank, item in enumerate(ranklist):
            key = (item.get("kind"), str(item.get("id")))
            entry = fused.get(key)
            if entry is None:
                entry = fused[key] = {**item, "rrf": 0.0, "arms": set()}
            entry["rrf"] += 1.0 / (k + rank + 1)
            if item.get("arm"):
                entry["arms"].add(item["arm"])
    return fused


def title_boost(query_tokens: set[str], item: dict, weight: float = 0.6) -> float:
    """Overlap coefficient between the question and the chunk's own title.

    Measured against the title ALONE. A full-text score over title-plus-body
    dilutes exactly the signal that discriminates here, because the body repeats
    the topic's vocabulary on every passage of the section.
    """
    title_tokens = _tokens(item.get("title") or "")
    if not title_tokens or not query_tokens:
        return 0.0
    overlap = len(query_tokens & title_tokens) / min(len(query_tokens), len(title_tokens))
    return overlap * weight


def statement_boost(item: dict, weight: float = 0.3) -> float:
    """A hit from the deterministic statement arm is a primary statement by
    construction — the strongest structural signal this corpus offers for a
    figure question."""
    arms = item.get("arms") or ()
    if "statements" in arms or "identity_anchor" in arms:
        return weight
    return 0.0


def agreement_boost(item: dict, weight: float = 0.15) -> float:
    """Independent arms agreeing is itself evidence.

    RRF already rewards this through the summed reciprocal ranks; this makes the
    effect explicit and slightly stronger, because on a single filing the arms
    return short lists where the rank differences RRF sees are small.
    """
    return weight * max(0, len(item.get("arms") or ()) - 1)


def fuse(ranklists: Iterable[list[dict]], query: str, *,
         qualitative: bool = False) -> list[dict]:
    """RRF the arms, apply the domain boosts, return one ranked list.

    THE RRF COMPONENT IS NORMALISED BEFORE THE BOOSTS ARE ADDED.
    Raw RRF scores on short per-filing lists land around 0.016, while a useful
    boost is a number like 0.4 — so adding them directly does not weight the
    boost, it OVERRIDES the ranking entirely. Measured on a live policy
    question, the flat statement boost put the balance sheet, cash-flow
    statement and P&L in the top four for "what is the leases accounting
    policy?", pushing the actual policy notes out of the rerank pool. Scaling
    the fused rank to [0, 1] first makes the boost weights mean what they look
    like they mean: a fraction of the best retrieval score, not a replacement
    for it.

    `fused_score` is kept separate from `score` so the reranker can blend into
    the latter without destroying the record of what retrieval alone thought —
    which is what makes a bad ranking diagnosable after the fact.
    """
    query_tokens = _tokens(query)
    fused = rrf(ranklists)
    if not fused:
        return []

    best_rrf = max(i["rrf"] for i in fused.values()) or 1.0

    for item in fused.values():
        item["arms"] = sorted(item["arms"]) if isinstance(item["arms"], set) else []
        item["rrf"] = round(item["rrf"], 6)
        item["rrf_norm"] = round(item["rrf"] / best_rrf, 4)
        item["title_boost"] = round(title_boost(query_tokens, item), 4)
        # Only for figure-shaped questions. A primary statement is the right
        # destination for "what were finance costs" and the wrong one for "what
        # is the leases accounting policy" — boosting it unconditionally was
        # answering the second question with the first question's evidence.
        item["statement_boost"] = 0.0 if qualitative else round(statement_boost(item), 4)
        item["agreement_boost"] = round(agreement_boost(item), 4)
        item["boost"] = round(
            item["title_boost"] + item["statement_boost"] + item["agreement_boost"], 4)
        item["fused_score"] = round(item["rrf_norm"] + item["boost"], 6)
        item["score"] = item["fused_score"]

    return sorted(fused.values(), key=lambda i: -i["score"])


def blend_rerank(ranked: list[dict], *, weight: float) -> list[dict]:
    """Fold cross-encoder scores INTO the fused score rather than replacing it.

    Replacing the order outright hands every decision to a general-purpose
    relevance model and discards what the domain already established — that this
    chunk is the balance sheet, that its title matches the question, that three
    arms found it. The cross-encoder is better at judging topical relevance and
    worse at knowing this corpus. Blending keeps both: a squashed sigmoid term
    refines the ordering, and a large enough logit difference still decides it.
    """
    for item in ranked:
        logit = item.get("rerank_score")
        if logit is None:
            continue
        item["rerank_prob"] = round(1.0 / (1.0 + math.exp(-logit)), 4)
        item["score"] = round(item.get("fused_score", 0.0)
                              + weight * item["rerank_prob"], 6)
    return sorted(ranked, key=lambda i: -i.get("score", 0.0))


def mmr_dedup(items: list[dict], threshold: float = 0.82) -> tuple[list[dict], int]:
    """Drop near-duplicate passages, keeping the higher-ranked one.

    Necessary the moment fusion and expansion exist: the same note reached
    through the semantic arm and the keyword arm, or a chunk and its own
    preceding-context window, are different rows carrying substantially the same
    text. Spending three of seven evidence slots on one passage is how an answer
    ends up narrower than the filing it was read from.

    Jaccard over token sets — no embeddings, so it costs nothing and cannot fail
    when the embedding endpoint is down.
    """
    kept: list[dict] = []
    kept_tokens: list[set[str]] = []
    dropped = 0

    for item in items:
        tokens = _tokens(item.get("content") or "")
        if not tokens:
            kept.append(item)
            kept_tokens.append(tokens)
            continue
        duplicate = False
        for existing in kept_tokens:
            if not existing:
                continue
            union = len(tokens | existing)
            if union and len(tokens & existing) / union >= threshold:
                duplicate = True
                break
        if duplicate:
            dropped += 1
            continue
        kept.append(item)
        kept_tokens.append(tokens)

    return kept, dropped
