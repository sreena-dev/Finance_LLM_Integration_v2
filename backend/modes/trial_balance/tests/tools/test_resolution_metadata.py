"""Tests for resolution_metadata.py::attach_resolution_metadata -- pure-function,
no DB needed (operates only on already-constructed NormalizedRow/TaxonomyResolution
objects).
"""

from modes.trial_balance.pipeline.tools.resolution_metadata import attach_resolution_metadata
from modes.trial_balance.pipeline.tools.tb_models import NormalizedRow, RowTrace, TaxonomyResolution


def _row(gl_code, mapped_status, resolved_at_step):
    return NormalizedRow(
        gl_code=gl_code, gl_name="x", opening=0.0, debit=0.0, credit=0.0, closing=0.0,
        bs_pl=None, main_head=None, sub_head_1=None, sub_head_2=None, account_type=None,
        mapped_status=mapped_status, trace=RowTrace(resolved_at_step=resolved_at_step),
    )


def test_resolver_method_wins_when_it_actually_resolved_the_row():
    row = _row("1001", "MAPPED", "step2_direct")
    resolution = TaxonomyResolution(
        status="RESOLVED", method="EXACT", evidence="Land", confidence=1.0,
        taxonomy_node_id=42, taxonomy_version_id=7,
    )
    [enriched] = attach_resolution_metadata([row], {"1001": resolution})

    assert enriched.trace.resolution_method == "EXACT"
    assert enriched.trace.taxonomy_node_id == 42
    assert enriched.trace.taxonomy_version_id == 7
    assert enriched.trace.confidence == 1.0
    assert enriched.trace.evidence == "Land"


def test_no_resolver_entry_falls_back_to_resolved_at_step():
    """A row the resolver never touched (no grouping hint at all, or CASE_1/2
    verbatim) must still get a non-None resolution_method reflecting whatever
    step actually produced the result -- never left None."""
    row = _row("1002", "MAPPED", "step3_single_shot")
    [enriched] = attach_resolution_metadata([row], {})

    assert enriched.trace.resolution_method == "LLM_SINGLE_SHOT"
    assert enriched.trace.taxonomy_node_id is None


def test_resolver_unresolved_but_row_later_mapped_via_repair_is_not_mislabeled():
    """The bug this module exists to fix: the resolver returned UNRESOLVED
    (method='UNMAPPED') for this GL code, but the row went on to reach
    mapped_status=MAPPED via the repair path -- resolution_method must reflect
    the repair, never the resolver's own failed attempt."""
    row = _row("1003", "MAPPED", "repair")
    resolution = TaxonomyResolution(status="UNRESOLVED", method="UNMAPPED", evidence="???")
    [enriched] = attach_resolution_metadata([row], {"1003": resolution})

    assert enriched.trace.resolution_method == "LLM_REPAIR"
    assert enriched.mapped_status == "MAPPED"


def test_terminal_unmapped_and_unmatched_rows_get_their_own_labels():
    unmapped = _row("1004", "UNMAPPED", "no_grouping_match")
    unmatched = _row("1005", "UNMATCHED", "unresolved")
    enriched = attach_resolution_metadata([unmapped, unmatched], {})

    assert enriched[0].trace.resolution_method == "UNMAPPED"
    assert enriched[1].trace.resolution_method == "UNMATCHED"


def test_rows_financial_and_taxonomy_text_fields_are_never_touched():
    row = NormalizedRow(
        gl_code="1006", gl_name="Freehold Land", opening=100.0, debit=50.0, credit=0.0, closing=150.0,
        bs_pl="BS", main_head="Non-current assets", sub_head_1="Property, Plant and Equipment",
        sub_head_2="Land", account_type="Asset", mapped_status="MAPPED",
        trace=RowTrace(resolved_at_step="step2_direct"),
    )
    [enriched] = attach_resolution_metadata([row], {})

    assert enriched.gl_name == "Freehold Land"
    assert enriched.opening == 100.0
    assert enriched.bs_pl == "BS"
    assert enriched.sub_head_2 == "Land"
    assert enriched.account_type == "Asset"
