"""Taxonomy Resolution Layer: resolves one GL row's grouping evidence into a
controlled taxonomy node BEFORE the classification engine's own Step 2/3
LLM path runs.

Strict evidence/resolution layering (the load-bearing invariant of this
module): raw client grouping text (known_fields, hint_text) is only ever a
CANDIDATE to test against the real taxonomy -- never trusted output. The
only thing that ever comes back out of this module as a sub_head_1/
sub_head_2 string is a resolved node's own canonical text. An UNRESOLVED
result means "leave known_fields exactly as it already was," never "guess."

Resolution order, each step tried only if the previous one returned
UNRESOLVED: EXACT -> ALIAS -> CANDIDATE_AUTO -> DETERMINISTIC_RULE (stub).
A row that isn't resolved by any of these four falls through, unchanged, to
normalize.py's own Step 2/3 -- this module adds a faster front door, never a
second way to reach MAPPED.

Ported from TB_normalization_v1's taxonomy/resolver.py.
"""

from __future__ import annotations

import dataclasses

from modes.trial_balance.pipeline.tools import taxonomy_repository as repo
from modes.trial_balance.pipeline.tools._shared import Standard
from modes.trial_balance.pipeline.tools.semantic_validation import validate_semantic_evidence
from modes.trial_balance.pipeline.tools.tb_models import GroupingHint, TaxonomyResolution, TBRow

# CANDIDATE_AUTO policy, ILIKE fallback path: maximally conservative --
# auto-resolve only when exactly one candidate scores above zero.
_CANDIDATE_AUTO_TOP_K = 5

# CANDIDATE_AUTO policy, embedding-first path. These are a calibrated
# starting point (observed floor + safety pad on real data), not a
# precision-validated cutoff -- revisit once negative examples exist to
# validate precision at any particular threshold.
_CANDIDATE_AUTO_EMBEDDING_MIN_SIMILARITY = 0.60
_CANDIDATE_AUTO_EMBEDDING_MIN_MARGIN = 0.05


def _try_exact(hint: GroupingHint, standard: Standard):
    sub_head_1 = hint.known_fields.get("sub_head_1")
    sub_head_2 = hint.known_fields.get("sub_head_2")
    if not (sub_head_1 and sub_head_2):
        return None
    node = repo.snap_to_taxonomy(
        hint.known_fields.get("bs_pl"), hint.known_fields.get("main_head"), sub_head_1, sub_head_2, standard
    )
    if node is None:
        return None
    resolution = TaxonomyResolution(
        status="RESOLVED", method="EXACT", evidence=f"{sub_head_1} {sub_head_2}", confidence=1.0,
    )
    return resolution, node


def _try_alias(hint: GroupingHint, standard: Standard):
    candidate_text = hint.hint_text or " ".join(hint.known_fields.values())
    if not candidate_text.strip():
        return None
    node = repo.match_alias(candidate_text, standard)
    if node is None:
        return None
    if not validate_semantic_evidence(candidate_text, node):
        return None
    resolution = TaxonomyResolution(status="RESOLVED", method="ALIAS", evidence=candidate_text, confidence=0.95)
    return resolution, node


def _try_candidate_auto_embedding(query_text: str, standard: Standard):
    """Embedding-first path. Returns None (never a guess) if the embed
    call itself failed (get_candidates_with_scores_embedding already
    swallows that into []) or the top candidate doesn't clear BOTH the
    similarity floor AND the top-1/top-2 margin floor."""
    scored = repo.get_candidates_with_scores_embedding(query_text, standard, top_k=_CANDIDATE_AUTO_TOP_K)
    if not scored:
        return None
    top_node, top_score = scored[0]
    margin = (top_score - scored[1][1]) if len(scored) > 1 else top_score
    if top_score < _CANDIDATE_AUTO_EMBEDDING_MIN_SIMILARITY or margin < _CANDIDATE_AUTO_EMBEDDING_MIN_MARGIN:
        return None
    if not validate_semantic_evidence(query_text, top_node):
        return None
    resolution = TaxonomyResolution(status="RESOLVED", method="CANDIDATE_AUTO", evidence=query_text, confidence=top_score)
    return resolution, top_node


def _try_candidate_auto_ilike(query_text: str, standard: Standard):
    """ILIKE fallback path: auto-resolve only when exactly one candidate
    scores above zero."""
    scored = repo.get_candidates_with_scores(query_text, standard, top_k=_CANDIDATE_AUTO_TOP_K)
    nonzero = [(node, score) for node, score in scored if score > 0]
    if len(nonzero) != 1:
        return None
    node, _score = nonzero[0]
    if not validate_semantic_evidence(query_text, node):
        return None
    resolution = TaxonomyResolution(status="RESOLVED", method="CANDIDATE_AUTO", evidence=query_text, confidence=None)
    return resolution, node


def _try_candidate_auto(tb_row: TBRow, hint: GroupingHint, standard: Standard):
    """Embedding-first, ILIKE as fallback -- tried whenever embedding
    retrieval is unavailable or ran but didn't clear the similarity/margin
    bar. Falling through to ILIKE after an inconclusive embedding result is
    a coverage gain, not a precision risk: never trade mapping correctness
    for a lower LLM-call count.

    TB-R23 correction: query_text used to be built from tb_row.gl_name + hint.hint_text
    ONLY, silently dropping hint.known_fields -- the exact defect behind EPIL GL 20950021
    ("SBI- MUSCAT (US$) -R") and its sibling GL 20950022 ("...-P") resolving to two
    different, contradictory FS Heads despite both carrying an identical, correct
    known_fields["main_head"] = "2.15 (i)" note from the client's own source file: that
    shared hint was never even reaching this search, so the two rows were free-text
    matched independently and diverged purely on their differing -R/-P suffix. Every
    other resolution step in this module (_try_exact, _try_alias) already consults
    known_fields; this brings _try_candidate_auto in line with them."""
    known_fields_text = " ".join(str(v) for v in hint.known_fields.values() if v)
    query_text = f"{tb_row.gl_name} {hint.hint_text or ''} {known_fields_text}".strip()
    if not query_text:
        return None

    embedding_outcome = _try_candidate_auto_embedding(query_text, standard)
    if embedding_outcome is not None:
        return embedding_outcome

    return _try_candidate_auto_ilike(query_text, standard)


# Wave 3 Fix 7: current/non-current is not an independent axis anywhere in this
# resolver -- it's a byproduct of which taxonomy node's identity won (two textually-
# identical "Financial Liabilities - Borrowings" sub_head_1 entries sit under
# different main_head parents, disambiguated only by sub_head_2). This is the first
# concrete, repeated pattern that justifies filling in the deterministic-rule stub:
# a bare borrowings ledger with no other resolvable hint, whose OWN name carries an
# unambiguous maturity keyword. Each sub_head_2 choice below is deliberately the one
# unambiguous (single-main_head-owner) option on its side, per schedule_iii's own
# taxonomy data -- "Deposits"/"Loans from related parties"/"Other loans" appear under
# BOTH Current and Non-current and are deliberately NOT used here, since picking one
# side for an inherently ambiguous sub_head_2 would be a guess, not a rule.
_NON_CURRENT_BORROWING_KEYWORDS = ("term loan", "bonds", "debenture", "deferred payment", "long term", "long-term")
_CURRENT_BORROWING_KEYWORDS = ("repayable on demand", "short term", "short-term", "cash credit", "overdraft")


def _try_deterministic_rule(tb_row: TBRow, hint: GroupingHint, standard: Standard):
    """Keyword-biased current/non-current classification for a borrowings ledger that
    EXACT/ALIAS/CANDIDATE_AUTO all failed to resolve. A genuine fallback, not a new
    precedence override -- only fires when nothing else already resolved this row, and
    only on an unambiguous maturity keyword. Returns None (never a guess) when no such
    keyword is present -- a bare "Borrowings" ledger with no maturity language at all
    stays UNRESOLVED here, same as before this rule existed."""
    evidence = f"{tb_row.gl_name} {hint.hint_text or ''}".lower()

    if any(k in evidence for k in _NON_CURRENT_BORROWING_KEYWORDS):
        main_head, sub_head_2 = "Non-current liabilities", "Term loans - from banks"
    elif any(k in evidence for k in _CURRENT_BORROWING_KEYWORDS):
        main_head, sub_head_2 = "Current liabilities", "Loans repayable on demand - from banks"
    else:
        return None

    node = repo.snap_to_taxonomy(
        hint.known_fields.get("bs_pl") or "BS", main_head, "Financial Liabilities - Borrowings", sub_head_2, standard
    )
    if node is None:
        return None
    resolution = TaxonomyResolution(
        status="RESOLVED", method="DETERMINISTIC_RULE", evidence=evidence, confidence=0.85,
    )
    return resolution, node


def _attach_node_identity(outcome: tuple, standard: Standard) -> tuple:
    """Populates TaxonomyResolution.taxonomy_node_id/taxonomy_version_id on a
    successful (resolution, node) outcome -- centralized here rather than in
    each _try_* function, since `node["id"]` and the active version_id are
    available identically at every one of them. TaxonomyResolution is frozen,
    hence dataclasses.replace()."""
    resolution, node = outcome
    resolution = dataclasses.replace(
        resolution, taxonomy_node_id=node["id"], taxonomy_version_id=repo.resolve_active_scope(standard),
    )
    return resolution, node


def _resolve_with_node(tb_row: TBRow, hint: GroupingHint, standard: Standard):
    for step in (_try_exact, _try_alias):
        outcome = step(hint, standard)
        if outcome is not None:
            return _attach_node_identity(outcome, standard)

    outcome = _try_candidate_auto(tb_row, hint, standard)
    if outcome is not None:
        return _attach_node_identity(outcome, standard)

    outcome = _try_deterministic_rule(tb_row, hint, standard)
    if outcome is not None:
        return _attach_node_identity(outcome, standard)

    unresolved = TaxonomyResolution(
        status="UNRESOLVED", method="UNMAPPED",
        evidence=hint.hint_text or " ".join(hint.known_fields.values()),
    )
    return unresolved, None


def resolve_grouping_evidence(tb_row: TBRow, hint: GroupingHint, standard: Standard) -> TaxonomyResolution:
    """Tries EXACT -> ALIAS -> CANDIDATE_AUTO -> DETERMINISTIC_RULE in
    order, stopping at the first RESOLVED result. Returns an UNRESOLVED
    TaxonomyResolution (never raises, never guesses) if none succeed."""
    resolution, _node = _resolve_with_node(tb_row, hint, standard)
    return resolution


def resolve_grouping_hints(
    tb_rows: list, grouping_hints: dict, standard: Standard,
) -> tuple:
    """Batch entry point, called before normalize(). For each TB row with a
    grouping hint, resolves taxonomy identity and, on success, writes the
    RESOLVED node's OWN sub_head_1/sub_head_2 strings into a new
    GroupingHint's known_fields -- never the raw client evidence text --
    while separately preserving that raw text in original_known_fields.
    Rows with no grouping hint at all, or whose resolution is UNRESOLVED,
    get their GroupingHint back unchanged.

    Returns (enriched_grouping_hints, resolution_by_gl_code)."""
    enriched_hints = dict(grouping_hints)
    resolutions: dict = {}

    for tb_row in tb_rows:
        hint = grouping_hints.get(tb_row.gl_code)
        if hint is None:
            continue

        resolution, node = _resolve_with_node(tb_row, hint, standard)
        resolutions[tb_row.gl_code] = resolution

        if node is None:
            continue

        enriched_hints[tb_row.gl_code] = GroupingHint(
            gl_code=tb_row.gl_code,
            known_fields={**hint.known_fields, "sub_head_1": node["sub_head_1"], "sub_head_2": node["sub_head_2"]},
            hint_text=hint.hint_text,
            original_known_fields=hint.known_fields,
        )

    return enriched_hints, resolutions
