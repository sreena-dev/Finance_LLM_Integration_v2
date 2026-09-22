"""Verbatim row construction: builds MAPPED/UNMAPPED rows directly from a
client's own grouping fields, with NO taxonomy validation, NO resolver, NO
LLM, and NO derivation -- not even account_type, which is copied straight
from the client's own file if supplied. This is the zero-touch path
classify_document.py's Case 1/2 data-quality-tier routing uses for rows the
tier decision has already determined don't need the taxonomy layer at all
(Case 1's whole document, or Case 2's complete-hint subset -- see
quality_gate.py's classify_quality_tier).

Ported verbatim from TB_normalization_v1's core/verbatim.py.
"""

from __future__ import annotations

from modes.trial_balance.pipeline.tools.tb_models import MAPPED, UNMAPPED, NormalizedRow, RowTrace


def build_verbatim_rows(
    tb_rows: list,
    grouping_hints: dict,
    *,
    resolution_method: str = "CLIENT_VERBATIM",
    evidence: str = "Copied from grouping input; taxonomy validation skipped.",
) -> list:
    """One row per tb_row, in order. A row is MAPPED iff it has a grouping
    hint with at least one populated field; otherwise UNMAPPED -- never
    UNMATCHED, since there is no resolution attempt here to fail.
    bs_pl/main_head/sub_head_1/sub_head_2/account_type are copied straight
    from hint.known_fields, unvalidated -- account_type is NEVER derived
    here (a true zero taxonomy/DB touch, not even the account_type-from-
    bs_pl/main_head derivation every other path uses)."""
    rows: list = []
    for tb_row in tb_rows:
        hint = grouping_hints.get(tb_row.gl_code)
        fields = hint.known_fields if hint else {}
        has_mapping = bool(hint and fields)
        rows.append(NormalizedRow.from_tb_row(
            tb_row,
            bs_pl=fields.get("bs_pl"), main_head=fields.get("main_head"),
            sub_head_1=fields.get("sub_head_1"), sub_head_2=fields.get("sub_head_2"),
            account_type=fields.get("account_type"),
            mapped_status=MAPPED if has_mapping else UNMAPPED,
            trace=RowTrace(
                resolved_at_step="step2_direct" if has_mapping else "no_grouping_match",
                resolution_method=resolution_method if has_mapping else None,
                evidence=evidence if has_mapping else None,
            ),
            wording_source="client_original" if has_mapping else "system_canonical",
        ))
    return rows


__all__ = ["build_verbatim_rows"]
