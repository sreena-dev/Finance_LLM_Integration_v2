"""Tests for taxonomy_resolver.py's EXACT -> ALIAS -> CANDIDATE_AUTO ->
DETERMINISTIC_RULE resolution order and its semantic-validation gating.
Mocks backend.tools.taxonomy_repository's DB-backed functions so these
run without the taxonomy_node/taxonomy_alias/taxonomy_embedding tables
being populated.
"""

from modes.trial_balance.pipeline.tools import taxonomy_repository, taxonomy_resolver
from modes.trial_balance.pipeline.tools.tb_models import GroupingHint, TBRow


def _tb_row(gl_code, gl_name):
    return TBRow(gl_code=gl_code, gl_name=gl_name, opening=0.0, debit=0.0, credit=0.0, closing=0.0)


def test_exact_step_resolves_with_no_db_call(monkeypatch):
    """A hint that already supplies a valid (sub_head_1, sub_head_2) pair
    resolves via EXACT -- never reaches ALIAS/CANDIDATE_AUTO, which would
    otherwise need the DB."""
    called = {"alias": False, "candidates": False, "embedding": False}
    monkeypatch.setattr(taxonomy_resolver.repo, "match_alias", lambda *a, **k: called.update(alias=True) or None)
    monkeypatch.setattr(taxonomy_resolver.repo, "get_candidates_with_scores", lambda *a, **k: called.update(candidates=True) or [])
    monkeypatch.setattr(taxonomy_resolver.repo, "get_candidates_with_scores_embedding", lambda *a, **k: called.update(embedding=True) or [])

    tb_row = _tb_row("1000", "Freehold Land")
    hint = GroupingHint(gl_code="1000", known_fields={"sub_head_1": "Property, Plant and Equipment", "sub_head_2": "Land"})
    resolution = taxonomy_resolver.resolve_grouping_evidence(tb_row, hint, "IND_AS")

    assert resolution.status == "RESOLVED"
    assert resolution.method == "EXACT"
    assert called == {"alias": False, "candidates": False, "embedding": False}
    # Gap 2 (resolver audit trail): a RESOLVED outcome carries the node's own
    # identity, not just its text fields.
    assert resolution.taxonomy_node_id is not None
    assert resolution.taxonomy_version_id is not None


def test_alias_step_used_when_no_direct_fields_and_gated_by_semantic_validation(monkeypatch):
    """ALIAS resolves via match_alias, but only if the resolved node
    doesn't semantically contradict the evidence text -- a node whose
    account_type contradicts a liability-signaling GL name must be
    rejected even though the alias lookup itself succeeded."""
    fake_node = {"id": 1, "bs_pl": "BS", "main_head": "Trade payables", "sub_head_1": "Trade payables", "sub_head_2": "Total outstanding dues", "account_type": "Asset"}
    monkeypatch.setattr(taxonomy_resolver.repo, "match_alias", lambda text, std: fake_node)
    monkeypatch.setattr(taxonomy_resolver.repo, "get_candidates_with_scores_embedding", lambda *a, **k: [])
    monkeypatch.setattr(taxonomy_resolver.repo, "get_candidates_with_scores", lambda *a, **k: [])

    tb_row = _tb_row("2000", "Rent Payable")
    hint = GroupingHint(gl_code="2000", hint_text="Rent Payable")
    resolution = taxonomy_resolver.resolve_grouping_evidence(tb_row, hint, "IND_AS")

    # "payable" is a liability signal, but the aliased node claims Asset --
    # semantic validation must reject this, falling through to
    # CANDIDATE_AUTO (which also returns nothing here), ending UNRESOLVED.
    assert resolution.status == "UNRESOLVED"


def test_candidate_auto_embedding_first_then_ilike_fallback(monkeypatch):
    """Embedding retrieval is tried first; only falls back to the
    keyword/ILIKE-equivalent path when embedding returns nothing."""
    node = {"id": 2, "bs_pl": "BS", "main_head": "Non-current assets", "sub_head_1": "Property, Plant and Equipment", "sub_head_2": "Land", "account_type": "Asset"}
    monkeypatch.setattr(taxonomy_resolver.repo, "match_alias", lambda *a, **k: None)
    monkeypatch.setattr(taxonomy_resolver.repo, "get_candidates_with_scores_embedding", lambda *a, **k: [(node, 0.9), (node, 0.5)])
    ilike_called = {"v": False}
    monkeypatch.setattr(taxonomy_resolver.repo, "get_candidates_with_scores", lambda *a, **k: ilike_called.update(v=True) or [])
    monkeypatch.setattr(taxonomy_resolver.repo, "resolve_active_scope", lambda *a, **k: 1)

    tb_row = _tb_row("3000", "Freehold Land")
    hint = GroupingHint(gl_code="3000", hint_text="Land and Buildings")
    resolution = taxonomy_resolver.resolve_grouping_evidence(tb_row, hint, "IND_AS")

    assert resolution.status == "RESOLVED"
    assert resolution.method == "CANDIDATE_AUTO"
    assert ilike_called["v"] is False


def test_candidate_auto_falls_back_to_ilike_when_embedding_inconclusive(monkeypatch):
    node = {"id": 3, "bs_pl": "BS", "main_head": "Non-current assets", "sub_head_1": "Property, Plant and Equipment", "sub_head_2": "Land", "account_type": "Asset"}
    monkeypatch.setattr(taxonomy_resolver.repo, "match_alias", lambda *a, **k: None)
    # Below the similarity floor -> embedding path returns None, must fall through.
    monkeypatch.setattr(taxonomy_resolver.repo, "get_candidates_with_scores_embedding", lambda *a, **k: [(node, 0.1)])
    monkeypatch.setattr(taxonomy_resolver.repo, "get_candidates_with_scores", lambda *a, **k: [(node, 3)])
    monkeypatch.setattr(taxonomy_resolver.repo, "resolve_active_scope", lambda *a, **k: 1)

    tb_row = _tb_row("3001", "Freehold Land")
    hint = GroupingHint(gl_code="3001", hint_text="Land")
    resolution = taxonomy_resolver.resolve_grouping_evidence(tb_row, hint, "IND_AS")

    assert resolution.status == "RESOLVED"
    assert resolution.method == "CANDIDATE_AUTO"


def test_no_resolution_falls_through_to_unresolved(monkeypatch):
    monkeypatch.setattr(taxonomy_resolver.repo, "match_alias", lambda *a, **k: None)
    monkeypatch.setattr(taxonomy_resolver.repo, "get_candidates_with_scores_embedding", lambda *a, **k: [])
    monkeypatch.setattr(taxonomy_resolver.repo, "get_candidates_with_scores", lambda *a, **k: [])

    tb_row = _tb_row("4000", "Mystery Account")
    hint = GroupingHint(gl_code="4000", hint_text="???")
    resolution = taxonomy_resolver.resolve_grouping_evidence(tb_row, hint, "IND_AS")

    assert resolution.status == "UNRESOLVED"
    assert resolution.method == "UNMAPPED"


# Gap 3: build_taxonomy_tree's sector filter -- currently inert in production (no
# compiled taxonomy has a node with `sector` set) but must exclude a sector-tagged
# node by default, matching TB_normalization_v1's own repository.py.

def test_build_taxonomy_tree_excludes_sector_tagged_nodes_by_default(monkeypatch):
    rows = (
        {"id": 1, "bs_pl": "BS", "main_head": "Non-current assets", "sub_head_1": "PPE",
         "sub_head_2": "Land", "sub_head_1_norm": "ppe", "sub_head_2_norm": "land", "sector": None},
        {"id": 2, "bs_pl": "BS", "main_head": "Oil & Gas Reserves", "sub_head_1": "Reserves",
         "sub_head_2": "Proved reserves", "sub_head_1_norm": "reserves", "sub_head_2_norm": "proved reserves",
         "sector": "oil_gas"},
    )
    monkeypatch.setattr(taxonomy_repository, "resolve_active_scope", lambda *a, **k: 1)
    monkeypatch.setattr(taxonomy_repository, "_nodes", lambda *a, **k: rows)

    tree = taxonomy_repository.build_taxonomy_tree("IND_AS")
    main_heads = {n["main_head"] for n in tree}
    assert "Non-current assets" in main_heads
    assert "Oil & Gas Reserves" not in main_heads


def test_build_taxonomy_tree_includes_sector_tagged_nodes_when_requested(monkeypatch):
    rows = (
        {"id": 2, "bs_pl": "BS", "main_head": "Oil & Gas Reserves", "sub_head_1": "Reserves",
         "sub_head_2": "Proved reserves", "sub_head_1_norm": "reserves", "sub_head_2_norm": "proved reserves",
         "sector": "oil_gas"},
    )
    monkeypatch.setattr(taxonomy_repository, "resolve_active_scope", lambda *a, **k: 1)
    monkeypatch.setattr(taxonomy_repository, "_nodes", lambda *a, **k: rows)

    tree = taxonomy_repository.build_taxonomy_tree("IND_AS", sectors=("oil_gas",))
    assert {n["main_head"] for n in tree} == {"Oil & Gas Reserves"}


def test_resolve_grouping_hints_rewrites_known_fields_to_canonical_text():
    """resolve_grouping_hints must write the RESOLVED node's OWN
    sub_head_1/sub_head_2 strings into known_fields (via the EXACT step,
    which needs no DB mock), while preserving the client's original
    wording -- differently cased/spaced but normalization-equivalent to
    the real taxonomy entry -- in original_known_fields."""
    tb_rows = [_tb_row("5000", "Freehold Land")]
    original_fields = {"sub_head_1": "property, plant  and equipment", "sub_head_2": "  LAND  "}
    hints = {"5000": GroupingHint(gl_code="5000", known_fields=dict(original_fields))}
    enriched, resolutions = taxonomy_resolver.resolve_grouping_hints(tb_rows, hints, "IND_AS")

    assert resolutions["5000"].status == "RESOLVED"
    assert resolutions["5000"].method == "EXACT"
    enriched_hint = enriched["5000"]
    assert enriched_hint.known_fields["sub_head_1"] == "Property, Plant and Equipment"
    assert enriched_hint.known_fields["sub_head_2"] == "Land"
    assert enriched_hint.original_known_fields == original_fields


# TB-R23: the exact, triple-verified EPIL root-cause mechanism -- GL 20950021
# ("SBI- MUSCAT (US$) -R") and its sibling GL 20950022 ("...-P") share an identical,
# correct known_fields["main_head"] = "2.15 (i)" note, yet _try_candidate_auto used to
# build its search query from gl_name alone, silently dropping that shared hint, so the
# two rows resolved independently and diverged purely on the -R/-P suffix.

def test_candidate_auto_query_incorporates_known_fields_not_just_gl_name(monkeypatch):
    captured = {}

    def _fake_embedding(query_text, standard, top_k):
        captured["query_text"] = query_text
        return []

    monkeypatch.setattr(taxonomy_resolver.repo, "match_alias", lambda *a, **k: None)
    monkeypatch.setattr(taxonomy_resolver.repo, "get_candidates_with_scores_embedding", _fake_embedding)
    monkeypatch.setattr(taxonomy_resolver.repo, "get_candidates_with_scores", lambda *a, **k: [])

    tb_row = _tb_row("20950021", "SBI- MUSCAT (US$) -R")
    # main_head-only hint (sub_head_1/sub_head_2 absent) -- exactly the shape a bare
    # trailing-subtotal note reference produces, so EXACT can't fire and this falls
    # through to CANDIDATE_AUTO, same as the real defect.
    hint = GroupingHint(gl_code="20950021", known_fields={"main_head": "2.15 (i)"})
    taxonomy_resolver.resolve_grouping_evidence(tb_row, hint, "IND_AS")

    assert "2.15 (i)" in captured["query_text"], (
        f"known_fields was dropped from the search query: {captured['query_text']!r}"
    )
    assert "SBI- MUSCAT (US$) -R" in captured["query_text"]


def test_sibling_gl_codes_with_identical_hint_both_reach_candidate_auto_with_same_hint(monkeypatch):
    """Both legs of the -R/-P pair carry the SAME known_fields hint (as they do in the
    real client workbook, per direct verification) -- confirms the resolver treats them
    on equal footing rather than one silently losing the hint the other keeps."""
    captured_queries = []

    def _fake_embedding(query_text, standard, top_k):
        captured_queries.append(query_text)
        return []

    monkeypatch.setattr(taxonomy_resolver.repo, "match_alias", lambda *a, **k: None)
    monkeypatch.setattr(taxonomy_resolver.repo, "get_candidates_with_scores_embedding", _fake_embedding)
    monkeypatch.setattr(taxonomy_resolver.repo, "get_candidates_with_scores", lambda *a, **k: [])

    hint_r = GroupingHint(gl_code="20950021", known_fields={"main_head": "2.15 (i)"})
    hint_p = GroupingHint(gl_code="20950022", known_fields={"main_head": "2.15 (i)"})
    taxonomy_resolver.resolve_grouping_evidence(_tb_row("20950021", "SBI- MUSCAT (US$) -R"), hint_r, "IND_AS")
    taxonomy_resolver.resolve_grouping_evidence(_tb_row("20950022", "SBI- MUSCAT (US$) -P"), hint_p, "IND_AS")

    assert len(captured_queries) == 2
    assert all("2.15 (i)" in q for q in captured_queries)


def test_deterministic_rule_biases_term_loan_to_non_current(monkeypatch):
    # Wave 3 Fix 7: a bare borrowings ledger with unambiguous "term loan" evidence,
    # unresolved by EXACT/ALIAS/CANDIDATE_AUTO, must fall through to the deterministic
    # rule and bias toward the Non-current "Financial Liabilities - Borrowings" node.
    fake_node = {
        "id": 1, "bs_pl": "BS", "main_head": "Non-current liabilities",
        "sub_head_1": "Financial Liabilities - Borrowings", "sub_head_2": "Term loans - from banks",
        "account_type": "Liability",
    }
    monkeypatch.setattr(taxonomy_resolver.repo, "match_alias", lambda *a, **k: None)
    monkeypatch.setattr(taxonomy_resolver.repo, "get_candidates_with_scores", lambda *a, **k: [])
    monkeypatch.setattr(taxonomy_resolver.repo, "get_candidates_with_scores_embedding", lambda *a, **k: [])
    captured = {}

    def _fake_snap(bs_pl, main_head, sub_head_1, sub_head_2, standard):
        captured.update(bs_pl=bs_pl, main_head=main_head, sub_head_1=sub_head_1, sub_head_2=sub_head_2)
        return fake_node

    monkeypatch.setattr(taxonomy_resolver.repo, "snap_to_taxonomy", _fake_snap)
    monkeypatch.setattr(taxonomy_resolver.repo, "resolve_active_scope", lambda *a, **k: 1)

    tb_row = _tb_row("2100", "Term Loan from Bank")
    hint = GroupingHint(gl_code="2100", known_fields={})
    resolution = taxonomy_resolver.resolve_grouping_evidence(tb_row, hint, "IND_AS")

    assert resolution.status == "RESOLVED"
    assert resolution.method == "DETERMINISTIC_RULE"
    assert captured["main_head"] == "Non-current liabilities"
    assert captured["sub_head_2"] == "Term loans - from banks"


def test_deterministic_rule_biases_cash_credit_to_current(monkeypatch):
    fake_node = {
        "id": 2, "bs_pl": "BS", "main_head": "Current liabilities",
        "sub_head_1": "Financial Liabilities - Borrowings", "sub_head_2": "Loans repayable on demand - from banks",
        "account_type": "Liability",
    }
    monkeypatch.setattr(taxonomy_resolver.repo, "match_alias", lambda *a, **k: None)
    monkeypatch.setattr(taxonomy_resolver.repo, "get_candidates_with_scores", lambda *a, **k: [])
    monkeypatch.setattr(taxonomy_resolver.repo, "get_candidates_with_scores_embedding", lambda *a, **k: [])
    captured = {}

    def _fake_snap(bs_pl, main_head, sub_head_1, sub_head_2, standard):
        captured.update(main_head=main_head, sub_head_2=sub_head_2)
        return fake_node

    monkeypatch.setattr(taxonomy_resolver.repo, "snap_to_taxonomy", _fake_snap)
    monkeypatch.setattr(taxonomy_resolver.repo, "resolve_active_scope", lambda *a, **k: 1)

    tb_row = _tb_row("2200", "Cash Credit Facility - Bank")
    hint = GroupingHint(gl_code="2200", known_fields={})
    resolution = taxonomy_resolver.resolve_grouping_evidence(tb_row, hint, "IND_AS")

    assert resolution.status == "RESOLVED"
    assert resolution.method == "DETERMINISTIC_RULE"
    assert captured["main_head"] == "Current liabilities"
    assert captured["sub_head_2"] == "Loans repayable on demand - from banks"


def test_deterministic_rule_stays_unresolved_without_a_maturity_keyword(monkeypatch):
    # A bare "Borrowings" ledger with no maturity language at all -- e.g. EPIL's GL
    # 10710000 ("LOAN PAYBLE BANKS") -- must NOT be guessed; this is the honestly-
    # reported limitation the Wave 3 plan flagged up front.
    monkeypatch.setattr(taxonomy_resolver.repo, "match_alias", lambda *a, **k: None)
    monkeypatch.setattr(taxonomy_resolver.repo, "get_candidates_with_scores", lambda *a, **k: [])
    monkeypatch.setattr(taxonomy_resolver.repo, "get_candidates_with_scores_embedding", lambda *a, **k: [])

    tb_row = _tb_row("10710000", "LOAN PAYBLE BANKS")
    hint = GroupingHint(gl_code="10710000", known_fields={})
    resolution = taxonomy_resolver.resolve_grouping_evidence(tb_row, hint, "IND_AS")

    assert resolution.status == "UNRESOLVED"
