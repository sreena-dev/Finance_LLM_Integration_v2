"""Integration test for classify_document.py's resolution-metadata wiring
(Gap 2): a CASE_3 row resolved via the taxonomy_resolver's EXACT step ends up
with its resolver audit trail (resolution_method/taxonomy_node_id/
taxonomy_version_id) attached to the final NormalizedRow, not discarded.
Gated on db_available since the resolver hits the real shared taxonomy DB.
"""

import pytest

from modes.trial_balance.pipeline.tools.classify_document import classify_tb_document
from modes.trial_balance.pipeline.tools.input_grouping_shape import GroupingShape
from modes.trial_balance.pipeline.tools.tb_models import GroupingHint, TBRow


class _FakeLLMClient:
    def generate(self, *args, **kwargs):
        raise AssertionError("LLM should not be called for an EXACT-resolved hint")


def test_case_3_exact_resolved_row_carries_resolver_audit_trail(db_available):
    if not db_available:
        pytest.skip("No reachable Postgres DB (settings.DB_*) -- skipping.")

    tb_rows = [TBRow(gl_code="1001", gl_name="Freehold Land", opening=0.0, debit=1000.0, credit=0.0, closing=1000.0)]
    # No structured sub_head_1/sub_head_2 columns -> CASE_3 (HIERARCHICAL_TRAIL),
    # but the hint's free text is specific enough for the ALIAS/EXACT-shaped
    # taxonomy_resolver step to resolve it before any LLM call.
    grouping_hints = {
        "1001": GroupingHint(
            gl_code="1001",
            known_fields={"sub_head_1": "Property, Plant and Equipment", "sub_head_2": "Land"},
        ),
    }

    result = classify_tb_document(
        tb_rows, grouping_hints, GroupingShape.HIERARCHICAL_TRAIL, "IND_AS", _FakeLLMClient(),
        accept_data_quality_risk=True,
    )

    assert result.quality_tier.tier == "CASE_3"
    [row] = result.rows
    assert row.mapped_status == "MAPPED"
    assert row.trace.resolution_method == "EXACT"
    assert row.trace.taxonomy_node_id is not None
    assert row.trace.taxonomy_version_id is not None
