"""normalize() -- the engine's public entry point, implementing the exact
resolution order:

1. No grouping-file entry for a TB row's gl_code at all -> terminal
   UNMAPPED directly. No LLM client is ever invoked for this row.
2. Grouping match + all 5 fields resolve directly (no LLM) -> straight to
   the Validation Gate as a provisionally-MAPPED row.
3. Grouping match, partial/failed direct resolution -> Step 3 queue,
   partitioned by vocab.needs_staged_narrowing into staged vs. single-shot
   classification. A row that resolves also becomes provisionally MAPPED
   and goes to the Validation Gate; a row the LLM (or staged narrowing)
   genuinely can't place is terminal UNMATCHED directly, without reaching
   the gate.
4. Every provisionally-MAPPED row (from step 2 or step 3) passes through
   the Validation Gate exactly once as the only path to a final MAPPED
   row anywhere in this engine.

No built-in assumption about "whole file" vs. "one row": a caller passes
the full parsed file's rows in one call, or can call this repeatedly with
a small-batch tb_rows list plus the same, precomputed GroupingHint mapping
-- grouping-hint construction is a pure function of the grouping data
alone, independent of which TB rows are in a given call.

Ported from TB_normalization_v1's core/orchestrator.py.
"""

from __future__ import annotations

from dataclasses import dataclass

from modes.trial_balance.pipeline.tools._shared import Standard
from modes.trial_balance.pipeline.tools.classification import classify_all
from modes.trial_balance.pipeline.tools.normalize_metrics import RunMetrics, compute_metrics
from modes.trial_balance.pipeline.tools.sanity_checks import find_account_type_keyword_conflicts, find_inconsistent_same_name_groups
from modes.trial_balance.pipeline.tools.staged_narrowing import classify_staged
from modes.trial_balance.pipeline.tools.taxonomy_repository import snap_to_taxonomy
from modes.trial_balance.pipeline.tools.tb_models import (
    UNMAPPED,
    UNMATCHED,
    ClassifyCandidate,
    GroupingHint,
    ProvisionalMappedRow,
    RowTrace,
    TBRow,
)
from modes.trial_balance.pipeline.tools.validation_gate import run_validation_gate
from modes.trial_balance.pipeline.tools.vocab import needs_staged_narrowing


@dataclass(frozen=True)
class SanityFlags:
    keyword_conflicts: list
    inconsistent_name_groups: dict


@dataclass(frozen=True)
class NormalizeResult:
    rows: list
    metrics: RunMetrics
    sanity_flags: SanityFlags


def _try_direct_resolution(tb_row: TBRow, hint: GroupingHint, standard: Standard):
    """Step 2: attempt to resolve all 5 taxonomy fields directly from
    whatever the grouping data's own structured columns supplied, with NO
    LLM call. Returns a ProvisionalMappedRow only when the supplied fields
    resolve to a real, exact taxonomy combination -- a partial or
    off-taxonomy structured value falls through to Step 3, never trusted
    verbatim."""
    fields = hint.known_fields
    sub_head_1 = fields.get("sub_head_1")
    sub_head_2 = fields.get("sub_head_2")
    if not (sub_head_1 and sub_head_2):
        return None
    snapped = snap_to_taxonomy(fields.get("bs_pl"), fields.get("main_head"), sub_head_1, sub_head_2, standard)
    if snapped is None:
        return None
    return ProvisionalMappedRow(
        tb_row=tb_row, bs_pl=snapped["bs_pl"], main_head=snapped["main_head"], sub_head_1=snapped["sub_head_1"],
        sub_head_2=snapped["sub_head_2"], resolved_at_step="step2_direct",
        # The client's own pre-rewrite wording for this GL code, so
        # validation_gate.py can display it instead of only ever showing
        # the canonical text `snapped` holds.
        client_fields=hint.original_known_fields or hint.known_fields,
    )


def normalize(
    tb_rows: list, grouping_hints: dict, standard: Standard, llm_client=None,
    *, use_candidates: bool = True, use_candidate_id_contract: bool = False,
    use_adaptive_candidates: bool = True, use_hierarchical_candidate_context: bool = True,
) -> NormalizeResult:
    no_match_rows: list = []
    direct_rows: list = []
    needs_llm: list = []

    for tb_row in tb_rows:
        hint = grouping_hints.get(tb_row.gl_code)
        if hint is None:
            no_match_rows.append(tb_row)
            continue
        direct = _try_direct_resolution(tb_row, hint, standard)
        if direct is not None:
            direct_rows.append(direct)
        else:
            needs_llm.append((tb_row, hint))

    staged_candidates: list = []
    staged_lookup: dict = {}
    single_shot_candidates: list = []
    single_shot_lookup: dict = {}

    for tb_row, hint in needs_llm:
        known_main_head = hint.known_fields.get("main_head", "")
        known_bs_pl = hint.known_fields.get("bs_pl", "")
        if needs_staged_narrowing(tb_row.gl_name, hint.hint_text):
            staged_candidates.append(ClassifyCandidate(tb_row.gl_code, tb_row.gl_name, hint.hint_text, known_main_head, known_bs_pl))
            staged_lookup[tb_row.gl_code] = tb_row
        else:
            single_shot_candidates.append(ClassifyCandidate(tb_row.gl_code, tb_row.gl_name, hint.hint_text, known_main_head, known_bs_pl))
            single_shot_lookup[tb_row.gl_code] = tb_row

    llm_provisional: list = []
    unresolved_rows: list = []

    staged_matches = classify_staged(staged_candidates, standard, llm_client)
    for gl_code, tb_row in staged_lookup.items():
        match = staged_matches.get(gl_code)
        if match is None:
            unresolved_rows.append(tb_row)
            continue
        llm_provisional.append(
            ProvisionalMappedRow(
                tb_row=tb_row, bs_pl=match.bs_pl, main_head=match.main_head, sub_head_1=match.sub_head_1,
                sub_head_2=match.sub_head_2, resolved_at_step="step3_staged", staged_narrowing_triggered=True,
                retrieval_confidence=match.retrieval_confidence,
            )
        )

    single_shot_matches = classify_all(
        single_shot_candidates, standard, llm_client, use_candidates=use_candidates,
        use_candidate_id_contract=use_candidate_id_contract, use_adaptive_candidates=use_adaptive_candidates,
        use_hierarchical_candidate_context=use_hierarchical_candidate_context,
    )
    for gl_code, tb_row in single_shot_lookup.items():
        match = single_shot_matches.get(gl_code)
        if match is None:
            unresolved_rows.append(tb_row)
            continue
        llm_provisional.append(
            ProvisionalMappedRow(
                tb_row=tb_row, bs_pl=match.bs_pl, main_head=match.main_head, sub_head_1=match.sub_head_1,
                sub_head_2=match.sub_head_2, resolved_at_step="step3_single_shot",
                retrieval_confidence=match.retrieval_confidence,
            )
        )

    gated_rows = run_validation_gate(
        direct_rows + llm_provisional, standard, llm_client, use_candidates=use_candidates,
        use_candidate_id_contract=use_candidate_id_contract, use_adaptive_candidates=use_adaptive_candidates,
        use_hierarchical_candidate_context=use_hierarchical_candidate_context,
    )

    rows: list = []
    for tb_row in no_match_rows:
        rows.append(
            _from_tb_row_unmapped(tb_row, UNMAPPED, "no_grouping_match")
        )
    for tb_row in unresolved_rows:
        rows.append(
            _from_tb_row_unmapped(tb_row, UNMATCHED, "unresolved", staged_narrowing_triggered=tb_row.gl_code in staged_lookup)
        )
    rows.extend(gated_rows)

    order = {tb_row.gl_code: i for i, tb_row in enumerate(tb_rows)}
    rows.sort(key=lambda r: order.get(r.gl_code, len(order)))

    keyword_conflicts = find_account_type_keyword_conflicts(rows)
    inconsistent_groups = find_inconsistent_same_name_groups(rows)
    metrics = compute_metrics(rows)

    return NormalizeResult(
        rows=rows, metrics=metrics,
        sanity_flags=SanityFlags(keyword_conflicts=keyword_conflicts, inconsistent_name_groups=inconsistent_groups),
    )


def _from_tb_row_unmapped(tb_row: TBRow, mapped_status: str, resolved_at_step: str, *, staged_narrowing_triggered: bool = False):
    from modes.trial_balance.pipeline.tools.tb_models import NormalizedRow

    return NormalizedRow.from_tb_row(
        tb_row, bs_pl=None, main_head=None, sub_head_1=None, sub_head_2=None, account_type=None,
        mapped_status=mapped_status,
        trace=RowTrace(resolved_at_step=resolved_at_step, staged_narrowing_triggered=staged_narrowing_triggered),
    )
