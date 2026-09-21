"""Candidate-retrieval support for classification.py: confidence tiering
(driving adaptive per-row retrieval expansion), deterministic ranking, and
generic-bucket ("Other expenses", etc.) injection.

Ported from TB_normalization_v1's taxonomy/candidate_confidence.py,
taxonomy/candidate_ranking.py, and taxonomy/generic_buckets.py.

Taxonomy nodes are represented here as plain dicts (this codebase's existing
convention -- see _shared.py's snap_to_taxonomy). Since dicts aren't
hashable, candidate identity for dedup/ranking uses the tuple
`(bs_pl, main_head, sub_head_1, sub_head_2)` instead of the dict itself.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional

from modes.trial_balance.pipeline.tools._shared import Standard
from modes.trial_balance.pipeline.tools.taxonomy_repository import build_taxonomy_tree, derive_account_type

# ---- Confidence tiering (candidate_confidence.py) --------------------------

class RetrievalConfidence(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"  # no candidates retrieved at all


HIGH_SCORE_THRESHOLD = 0.55
HIGH_MARGIN_THRESHOLD = 0.05
MEDIUM_SCORE_THRESHOLD = HIGH_SCORE_THRESHOLD * 0.7


def classify_confidence(top_score: Optional[float], second_score: Optional[float]) -> RetrievalConfidence:
    """NONE only when nothing was retrieved at all. HIGH requires both a
    strong top score AND a real margin over the runner-up -- a strong top
    score with a near-tied second candidate is exactly the ambiguity
    adaptive expansion exists to resolve, not a confident result."""
    if top_score is None:
        return RetrievalConfidence.NONE
    margin = (top_score - second_score) if second_score is not None else top_score
    if top_score >= HIGH_SCORE_THRESHOLD and margin >= HIGH_MARGIN_THRESHOLD:
        return RetrievalConfidence.HIGH
    if top_score >= MEDIUM_SCORE_THRESHOLD:
        return RetrievalConfidence.MEDIUM
    return RetrievalConfidence.LOW


# 15 -> 30 -> 60 expansion ladder. HIGH stays at the existing per-row top_k
# (no expansion needed); NONE expands to the widest step.
EXPANSION_TOP_K = {
    RetrievalConfidence.HIGH: 15,
    RetrievalConfidence.MEDIUM: 30,
    RetrievalConfidence.LOW: 60,
    RetrievalConfidence.NONE: 60,
}


# ---- Deterministic ranking (candidate_ranking.py) --------------------------

# Injected generic-bucket candidates have no real retrieval score -- given a
# fixed, deliberately low score so genuine semantic matches always rank
# above them in a tie, while still surviving a reasonably-sized cap.
INJECTED_GENERIC_SCORE = 0.01

# Additive bump for a candidate matching a row's known_main_head/
# known_bs_pl -- small relative to typical embedding scores, a tie-breaker
# among close scores only, never enough to override a much stronger
# semantic match (a grouping file's own known_bs_pl can itself be wrong).
PARENT_MATCH_BOOST = 0.05

# Deliberately generous, not an active cap -- a tighter global top-N
# truncation was measured (in the ported original) to regress accuracy by
# dropping individual rows' correct candidates in favor of other rows'
# higher-scoring but irrelevant ones. Acts only as a ceiling against
# pathological growth.
MAX_UNION_SIZE = 500


def rank_and_cap_candidates(
    scored_nodes: dict,
    *,
    known_main_heads: frozenset = frozenset(),
    known_bs_pls: frozenset = frozenset(),
    max_size: int = MAX_UNION_SIZE,
) -> list:
    """`scored_nodes`: {identity_tuple: (node_dict, score)}. Combines each
    node's own retrieval/injection score with a parent-match boost, ranks
    descending, and truncates to `max_size`. Returns a list of node dicts."""
    ranked = []
    for identity, (node, base_score) in scored_nodes.items():
        _bs_pl, main_head, _sub_head_1, _sub_head_2 = identity
        boost = PARENT_MATCH_BOOST if (main_head in known_main_heads or node["bs_pl"] in known_bs_pls) else 0.0
        ranked.append((base_score + boost, node))

    ranked.sort(key=lambda t: t[0], reverse=True)
    return [node for _score, node in ranked[:max_size]]


# ---- Generic-bucket injection (generic_buckets.py) --------------------------

_GENERIC_RE = re.compile(r"^others?\b", re.IGNORECASE)


def _norm(text: str) -> str:
    return " ".join(text.strip().lower().split())


def is_generic_bucket(node: dict) -> bool:
    """True if this leaf's sub_head_2 reads as a catch-all/fallback bucket
    ("Other expenses", "Others (specify nature)", ...)."""
    return bool(_GENERIC_RE.match(node["sub_head_2"].strip()))


def all_generic_bucket_nodes(standard: Standard) -> list:
    """Every generic/catch-all leaf in this framework's taxonomy."""
    tree = build_taxonomy_tree(standard)
    nodes = []
    for main_head_entry in tree:
        bs_pl = main_head_entry.get("bs_pl", "")
        main_head = main_head_entry.get("main_head", "")
        account_type = derive_account_type(bs_pl, main_head, standard)
        for sub in main_head_entry.get("sub_heads", []):
            sub_head_1 = sub.get("sub_head_1", "")
            for sub_head_2 in sub.get("sub_head_2_options", []):
                if _GENERIC_RE.match(sub_head_2.strip()):
                    nodes.append({
                        "bs_pl": bs_pl, "main_head": main_head, "sub_head_1": sub_head_1,
                        "sub_head_2": sub_head_2, "account_type": account_type,
                    })
    return nodes


def filter_generic_buckets_by_bs_pl(generic_nodes: list, *, bs_pl: str) -> list:
    """Structural compatibility filter -- gated on bs_pl alone (never
    main_head), since a grouping file's own known_fields["bs_pl"]/
    known_fields["main_head"] can itself be wrong. Callers should derive
    `bs_pl` from the row's OWN retrieved-candidate evidence (majority bs_pl
    among what retrieval already found for this row), not from the
    grouping file's structured fields directly."""
    if not bs_pl:
        return []
    target = _norm(bs_pl)
    return [n for n in generic_nodes if _norm(n["bs_pl"]) == target]
