"""Wave 8 remark #13: mapping coverage measured by account COUNT alone can read very
differently from coverage measured by VALUE -- a handful of large unmapped balances
vs many small ones. build_normalisation_note now reports both."""

import json

from modes.trial_balance.pipeline.tools import build_normalisation_note


def test_mapped_percentage_by_value_differs_from_count_based_coverage(make_canonical_tb, tmp_path):
    # 4 of 5 accounts (80%) are mapped by count, but the one unmapped account carries
    # the overwhelming majority of the TB's value -- coverage by value should read far
    # lower than coverage by count, not identical to it.
    rows = [
        {"gl_code": "1", "gl_name": "Small Mapped 1", "closing_balance": 100.0, "mapped_status": "MAPPED", "main_head": "Current assets"},
        {"gl_code": "2", "gl_name": "Small Mapped 2", "closing_balance": 100.0, "mapped_status": "MAPPED", "main_head": "Current assets"},
        {"gl_code": "3", "gl_name": "Small Mapped 3", "closing_balance": 100.0, "mapped_status": "MAPPED", "main_head": "Current assets"},
        {"gl_code": "4", "gl_name": "Small Mapped 4", "closing_balance": 100.0, "mapped_status": "MAPPED", "main_head": "Current assets"},
        {"gl_code": "5", "gl_name": "Large Unmapped", "closing_balance": 9_996_000.0, "mapped_status": "UNMAPPED"},
    ]
    canonical_tb_file = make_canonical_tb(rows)

    result = build_normalisation_note(canonical_tb_file=str(canonical_tb_file), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"

    with open(tmp_path / "normalisation_note.json") as f:
        note = json.load(f)
    pop = note["row_population"]
    assert pop["mapped_percentage"] == 80.0
    assert pop["mapped_percentage_by_value"] < 1.0
