"""DB-backed taxonomy access for the native classification engine.

Reads the EXISTING `taxonomy_version`/`taxonomy_node`/`taxonomy_alias`/
`taxonomy_embedding` tables -- the same compiled, standard Schedule III
taxonomy TB_normalization_v1 already ingested into this shared database.
These are reference/standard data (a fixed legal classification standard),
not either system's own business data, so both systems reading the same
compiled tables is the correct shape -- no separate TB-v2-git copy, no new
tables. This module only ever reads; compiling/re-compiling the taxonomy
remains TB_normalization_v1's job.

Ported from TB_normalization_v1's taxonomy/repository.py::PostgresTaxonomyRepository,
adapted to this codebase's functional style (module-level functions, not a
repository class).

Every query is scoped by the framework's ACTIVE version_id (resolve_active_scope) --
an AS query can never return an IND_AS node, and a stale/inactive version_id
simply returns no rows rather than silently falling back to "whatever is
active now". Node/alias rows for a version are fetched once and cached in
memory (same discipline as _shared.py's own @lru_cache'd JSON taxonomy) --
resolver.py's per-GL-row loop would otherwise re-query per row per method.
get_candidates_with_scores_embedding is NOT cached this way: it's a
similarity search against taxonomy_embedding, not a static lookup, so it
hits the DB every call.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Optional

from modes.trial_balance.pipeline.db import db_cursor
from modes.trial_balance.pipeline.tools._shared import Standard

_STOPWORDS = {"and", "of", "the", "for", "to", "in", "on", "a", "an", "other", "others", "account", "accounts"}


def _norm(value) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _tokenize(text: Optional[str]) -> list:
    words = re.findall(r"[a-zA-Z]+", (text or "").lower())
    seen: dict = {}
    for w in words:
        if len(w) > 2 and w not in _STOPWORDS:
            seen.setdefault(w, None)
    return list(seen)


class TaxonomyVersionNotFoundError(Exception):
    """No active taxonomy_version row exists for the requested framework."""


@lru_cache(maxsize=4)
def resolve_active_scope(standard: Standard) -> int:
    """The active version_id for this framework -- the only function
    allowed to look up "the active version" implicitly. Every other
    function in this module requires an already-resolved version_id (via
    this function), so a caller cannot accidentally query across an
    unversioned/ambiguous scope."""
    with db_cursor(dict_rows=False) as cur:
        cur.execute("SELECT id FROM taxonomy_version WHERE framework = %s AND is_active = true", (standard,))
        row = cur.fetchone()
    if row is None:
        raise TaxonomyVersionNotFoundError(f"No active taxonomy_version for framework={standard!r}")
    return row[0]


def _node_dict(row: dict) -> dict:
    return {
        "id": row["id"], "bs_pl": row["bs_pl"], "main_head": row["main_head"],
        "sub_head_1": row["sub_head_1"], "sub_head_2": row["sub_head_2"], "account_type": row["account_type"],
    }


@lru_cache(maxsize=8)
def _nodes(version_id: int) -> tuple:
    """Cached node rows for this version, fetched once per process
    lifetime. Call invalidate_taxonomy_repository_cache() if the taxonomy
    is recompiled while this process is still running."""
    with db_cursor(dict_rows=True) as cur:
        cur.execute(
            "SELECT id, bs_pl, main_head, sub_head_1, sub_head_2, account_type, "
            "sub_head_1_norm, sub_head_2_norm, sector FROM taxonomy_node WHERE version_id = %s",
            (version_id,),
        )
        return tuple(dict(r) for r in cur.fetchall())


@lru_cache(maxsize=8)
def _aliases(version_id: int) -> tuple:
    with db_cursor(dict_rows=True) as cur:
        cur.execute(
            """SELECT a.alias_text_norm, a.alias_text, a.alias_field, a.node_id,
                      n.bs_pl, n.main_head, n.sub_head_1, n.sub_head_2, n.account_type
               FROM taxonomy_alias a JOIN taxonomy_node n ON n.id = a.node_id
               WHERE a.version_id = %s""",
            (version_id,),
        )
        return tuple(dict(r) for r in cur.fetchall())


def invalidate_taxonomy_repository_cache() -> None:
    resolve_active_scope.cache_clear()
    _nodes.cache_clear()
    _aliases.cache_clear()


def snap_to_taxonomy(bs_pl, main_head, sub_head_1, sub_head_2, standard: Standard) -> Optional[dict]:
    """Validates a (sub_head_1, sub_head_2) selection against the closed
    taxonomy. An unambiguous match wins immediately, regardless of
    `main_head` (harmless echo for the ~90% of pairs with only one owner).
    Only when that's ambiguous or absent does `main_head` get used, to
    disambiguate -- and only if the caller actually supplied it as real
    evidence. `bs_pl` is accepted for interface symmetry only and never
    affects whether a selection validates."""
    version_id = resolve_active_scope(standard)
    s1, s2 = _norm(sub_head_1), _norm(sub_head_2)
    rows = [r for r in _nodes(version_id) if r["sub_head_1_norm"] == s1 and r["sub_head_2_norm"] == s2]
    if len(rows) == 1:
        row = rows[0]
    elif len(rows) > 1 and main_head:
        target = _norm(main_head)
        matches = [r for r in rows if _norm(r["main_head"]) == target]
        if len(matches) != 1:
            return None
        row = matches[0]
    else:
        return None
    return _node_dict(row)


def derive_account_type(bs_pl, main_head, standard: Standard) -> str:
    """account_type is already precomputed on taxonomy_node at compile
    time -- this is a lookup, not a re-derivation. Falls back to the
    alias table (main_head-scoped) for a caller's own structured wording
    that doesn't match any node's main_head verbatim. Anything matching
    neither returns "" rather than guessing."""
    version_id = resolve_active_scope(standard)
    for row in _nodes(version_id):
        if row["main_head"] == main_head:
            return row["account_type"]
    target = _norm(main_head)
    for alias_row in _aliases(version_id):
        if alias_row["alias_field"] == "main_head" and alias_row["alias_text_norm"] == target:
            return alias_row["account_type"]
    return ""


def build_taxonomy_tree(standard: Standard, sectors: tuple = ()) -> list:
    """One entry per main_head with its bs_pl and a nested list of
    sub_head_1 branches (each carrying only its own sub_head_2
    candidates). A sector-tagged node is excluded unless its sector is in
    `sectors` -- every real caller passes none, so sector-tagged nodes
    never leak into a classification tree by default."""
    version_id = resolve_active_scope(standard)
    tree: dict = {}
    for row in _nodes(version_id):
        if row["sector"] and row["sector"] not in sectors:
            continue
        node = tree.setdefault(row["main_head"], {"bs_pl": row["bs_pl"], "main_head": row["main_head"], "sub_heads": []})
        sub = next((s for s in node["sub_heads"] if s["sub_head_1"] == row["sub_head_1"]), None)
        if sub is None:
            sub = {"sub_head_1": row["sub_head_1"], "sub_head_2_options": []}
            node["sub_heads"].append(sub)
        sub["sub_head_2_options"].append(row["sub_head_2"])
    return list(tree.values())


def match_alias(text: str, standard: Standard) -> Optional[dict]:
    """Exact, enumerable alias_text_norm lookup -- never fuzzy matching.
    Every alias is main_head-scoped; refuses (returns None) whenever the
    aliased main_head has more than one distinct node in this version,
    rather than arbitrarily picking one."""
    version_id = resolve_active_scope(standard)
    target = _norm(text)
    row = None
    for alias_row in _aliases(version_id):
        if alias_row["alias_text_norm"] == target:
            row = alias_row
            break
    if row is None:
        return None

    distinct_nodes = sum(1 for r in _nodes(version_id) if r["main_head"] == row["main_head"])
    if distinct_nodes > 1:
        return None
    return {
        "id": row["node_id"], "bs_pl": row["bs_pl"], "main_head": row["main_head"],
        "sub_head_1": row["sub_head_1"], "sub_head_2": row["sub_head_2"], "account_type": row["account_type"],
    }


def get_candidates_with_scores(query_text: str, standard: Standard, top_k: int = 20) -> list:
    """Scores each node by how many query words appear (case-insensitive
    substring) in its own main_head/sub_head_1/sub_head_2 text, or in an
    alias pointing at its main_head. Answered from the cached node/alias
    rows, not a per-call SQL query. Returns [] if the query has no usable
    words or nothing scores above zero."""
    version_id = resolve_active_scope(standard)
    words = _tokenize(query_text)
    if not words:
        return []

    def _score(text: str) -> int:
        text = text.lower()
        return sum(1 for w in words if w in text)

    alias_score_by_node_id: dict = {}
    for alias_row in _aliases(version_id):
        alias_score = _score(alias_row["alias_text"])
        node_id = alias_row["node_id"]
        if alias_score > alias_score_by_node_id.get(node_id, 0):
            alias_score_by_node_id[node_id] = alias_score

    scored = []
    for row in _nodes(version_id):
        node_text = f"{row['main_head']} {row['sub_head_1']} {row['sub_head_2']}"
        score = _score(node_text) + alias_score_by_node_id.get(row["id"], 0)
        if score > 0:
            scored.append((score, row))

    scored.sort(key=lambda t: (-t[0], t[1]["main_head"], t[1]["sub_head_1"], t[1]["sub_head_2"]))
    return [(_node_dict(row), score) for score, row in scored[:top_k]]


def get_candidates_with_scores_embedding(query_text: str, standard: Standard, top_k: int = 20) -> list:
    """pgvector cosine-similarity candidate retrieval against the existing
    taxonomy_embedding table (already populated for the active version).
    Scores are cosine SIMILARITY in [-1, 1] -- NOT comparable to
    get_candidates_with_scores()'s integer substring-match counts. Returns
    [] (never raises into the caller) if no embedding client is
    configured/reachable, the query has no text, or the embed call itself
    fails."""
    from modes.trial_balance.pipeline.tools.embedding_client import EmbeddingConfigError, default_embedding_client

    if not (query_text or "").strip():
        return []
    version_id = resolve_active_scope(standard)
    try:
        client = default_embedding_client()
        vectors = client.embed([query_text])
    except (EmbeddingConfigError, Exception):
        return []
    if not vectors:
        return []
    query_vector = "[" + ",".join(repr(float(v)) for v in vectors[0]) + "]"
    with db_cursor(dict_rows=True) as cur:
        cur.execute(
            """SELECT n.id, n.bs_pl, n.main_head, n.sub_head_1, n.sub_head_2, n.account_type,
                      1 - (e.embedding <=> %s::vector) AS similarity
               FROM taxonomy_embedding e
               JOIN taxonomy_node n ON n.id = e.node_id
               WHERE e.version_id = %s AND n.version_id = %s
               ORDER BY e.embedding <=> %s::vector
               LIMIT %s""",
            (query_vector, version_id, version_id, query_vector, top_k),
        )
        rows = cur.fetchall()
    return [(_node_dict(r), float(r["similarity"])) for r in rows]
