"""Threads taxonomy_resolver.py's TaxonomyResolution audit data onto
NormalizedRow.trace, called from classify_document.py right after the
Case-driven routing produces its final `rows`, before the account_type
backfill -- this is the load-bearing choice that lets normalize.py and
validation_gate.py stay completely unmodified while every mapped row still
ends up with a taxonomy_node_id/resolution_method/etc. in its audit trail.

Uses dataclasses.replace() throughout (NormalizedRow/RowTrace are frozen) --
never mutates a row's financial or taxonomy-text fields, only trace.

Before this pass, resolution_method was ONLY populated from
resolution_by_gl (taxonomy_resolver.py's EXACT/ALIAS/CANDIDATE_AUTO/
DETERMINISTIC_RULE pre-pass). Two real bugs followed from that:
  1. A row the resolver never touched at all (no grouping hint, or the
     resolver ran but returned UNRESOLVED) kept resolution_method=None even
     when it went on to reach mapped_status=MAPPED via Step 2 direct or the
     Step 3 LLM path -- resolution_method must represent the final method
     responsible for the result.
  2. Worse: a row where the resolver ATTEMPTED resolution and returned
     UNRESOLVED (method="UNMAPPED") but then WAS mapped via Step 3/repair
     had its resolution_method overwritten to "UNMAPPED" despite
     mapped_status=MAPPED -- silently mislabeling an actually-MAPPED row.

Fix: only trust resolution_by_gl's method when status == "RESOLVED".
Otherwise (no resolver entry, or resolver returned UNRESOLVED),
resolved_at_step is the authoritative signal for the final method -- it is
set exactly once per row, by whichever code path actually produced the
final trace (normalize.py's Step 2, classification.py's Step 3,
staged_narrowing.py's Step 3, or validation_gate.py's repair path), and a
row that ends up UNMAPPED or UNMATCHED always has resolved_at_step
rewritten to "no_grouping_match" (UNMAPPED) or "unresolved" (UNMATCHED)
regardless of which step it started at (validation_gate.py never leaves a
stale MAPPED-shaped step on a row it demoted) -- so this mapping can never
mislabel a MAPPED row as UNMAPPED or vice versa.

Ported from TB_normalization_v1's observability/resolution_metadata.py.
"""

from __future__ import annotations

import dataclasses

from modes.trial_balance.pipeline.tools.tb_models import ResolvedAtStep, TaxonomyResolution

# GROUPING_AUTO is the umbrella term for the resolver's own EXACT/ALIAS/
# CANDIDATE_AUTO/DETERMINISTIC_RULE methods, which are kept as their
# specific values (more informative) rather than collapsed to a generic
# mapping.
_RESOLUTION_METHOD_BY_STEP: dict = {
    "step2_direct": "DIRECT",
    "step3_single_shot": "LLM_SINGLE_SHOT",
    "step3_staged": "LLM_STAGED",
    "repair": "LLM_REPAIR",
    "no_grouping_match": "UNMAPPED",
    "unresolved": "UNMATCHED",
}


def attach_resolution_metadata(rows: list, resolution_by_gl: dict) -> list:
    """Every row gets a resolution_method reflecting the final mechanism
    responsible for its result -- never left None, never mismatched against
    mapped_status. taxonomy_node_id/taxonomy_version_id/confidence/evidence
    are populated only when the resolver itself actually resolved this row
    (status == "RESOLVED"); they stay at RowTrace's defaults otherwise,
    since those fields are specifically the resolver's own audit data, not
    a property every resolution mechanism produces."""
    enriched: list = []
    for row in rows:
        resolution: TaxonomyResolution = resolution_by_gl.get(row.gl_code)
        if resolution is not None and resolution.status == "RESOLVED":
            new_trace = dataclasses.replace(
                row.trace,
                taxonomy_node_id=resolution.taxonomy_node_id,
                taxonomy_version_id=resolution.taxonomy_version_id,
                resolution_method=resolution.method,
                confidence=resolution.confidence,
                evidence=resolution.evidence,
            )
        else:
            resolved_at_step: ResolvedAtStep = row.trace.resolved_at_step
            new_trace = dataclasses.replace(
                row.trace, resolution_method=_RESOLUTION_METHOD_BY_STEP.get(resolved_at_step),
            )
        enriched.append(dataclasses.replace(row, trace=new_trace))
    return enriched


__all__ = ["attach_resolution_metadata"]
