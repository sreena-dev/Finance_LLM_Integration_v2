"""Round-trips custom_field_1/2/3 (reserved/not-yet-defined columns that
already exist on tb_table/live_tb_table, see TB_ingestion/schema.sql) through
build_canonical_tb.py, then persist_canonical_tb_to_live.py, then a LIVE
read-back -- confirming they survive un-mutated when supplied and stay None
when absent, end to end across the three files this passthrough touches.

build_canonical_tb.py is tested here by constructing its manifest_file/
ground_truth_file inputs directly (its own documented contract), rather than
routing through process_input_documents/extract_grouping_mapping -- this
isolates the function under test from two unrelated tools' behavior, which
is deliberate: this test exists to pin down the custom-field passthrough
logic, not to re-verify the whole upload pipeline."""

import json

import polars as pl
import pytest

from modes.trial_balance.pipeline.tools import build_canonical_tb
from modes.trial_balance.pipeline.tools import persist_canonical_tb_to_live


@pytest.fixture
def manifest_and_ground_truth(tmp_path):
    """Hand-built manifest.json + TB parquet + column-mapping + ground-truth
    parquet, matching exactly what build_canonical_tb documents as its input
    contract (see that file's own docstring/reads)."""
    tb_df = pl.DataFrame(
        {
            "code": ["1001", "2001"],
            "name": ["Cash in Hand", "Trade Payable"],
            "opening_balance": [1000.0, -1000.0],
            "debit": [500.0, 0.0],
            "credit": [0.0, 500.0],
            "closing_balance": [1500.0, -1500.0],
        }
    )
    tb_parquet = tmp_path / "tb_sheet_001.parquet"
    tb_df.write_parquet(tb_parquet)

    column_mapping = {
        "gl_code": "code",
        "gl_name": "name",
        "opening_balance": "opening_balance",
        "debit": "debit",
        "credit": "credit",
        "closing_balance": "closing_balance",
    }
    mapping_path = tmp_path / "tb_column_mapping_1.json"
    with open(mapping_path, "w") as f:
        json.dump(column_mapping, f)

    manifest = {
        "worksheets": [
            {
                "role": "TRIAL_BALANCE",
                "parquet": str(tb_parquet),
                "column_mapping_file": str(mapping_path),
            }
        ]
    }
    manifest_path = tmp_path / "processing_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f)

    gt_df = pl.DataFrame(
        {
            "gl_code": ["1001", "2001"],
            "bs_pl": ["BS", "BS"],
            "main_head": ["Current assets", "Current liabilities"],
            "sub_head_1": ["Cash and Cash Equivalents", "Trade Payables"],
            "sub_head_2": ["Cash on hand", "Trade Payables"],
            "account_type": ["Asset", "Liability"],
            "mapped_status": ["MAPPED", "MAPPED"],
        }
    )
    gt_parquet = tmp_path / "grouping_ground_truth.parquet"
    gt_df.write_parquet(gt_parquet)

    return {"manifest_file": str(manifest_path), "ground_truth_file": str(gt_parquet)}


def test_build_canonical_tb_populates_supplied_custom_fields(manifest_and_ground_truth, tmp_path):
    result = build_canonical_tb(
        manifest_file=manifest_and_ground_truth["manifest_file"],
        ground_truth_file=manifest_and_ground_truth["ground_truth_file"],
        output_dir=str(tmp_path / "out"),
        company_details={
            "company_name": "Acme Test Co",
            "financial_year": "FY2025-26",
            "custom_field_1": "custom-value-1",
            "custom_field_2": "custom-value-2",
        },
    )
    assert result["execution_status"] == "SUCCESS"

    canonical_path = [a for a in result["artifacts"] if a.endswith("canonical_tb.parquet")][0]
    df = pl.read_parquet(canonical_path)

    assert set(df["custom_field_1"].to_list()) == {"custom-value-1"}
    assert set(df["custom_field_2"].to_list()) == {"custom-value-2"}
    assert set(df["custom_field_3"].to_list()) == {None}


def test_build_canonical_tb_leaves_custom_fields_null_when_not_supplied(manifest_and_ground_truth, tmp_path):
    result = build_canonical_tb(
        manifest_file=manifest_and_ground_truth["manifest_file"],
        ground_truth_file=manifest_and_ground_truth["ground_truth_file"],
        output_dir=str(tmp_path / "out"),
    )
    assert result["execution_status"] == "SUCCESS"

    canonical_path = [a for a in result["artifacts"] if a.endswith("canonical_tb.parquet")][0]
    df = pl.read_parquet(canonical_path)

    for col in ("custom_field_1", "custom_field_2", "custom_field_3"):
        assert set(df[col].to_list()) == {None}


def test_custom_fields_survive_end_to_end_into_live_db(db_available, manifest_and_ground_truth, tmp_path):
    if not db_available:
        pytest.skip("No reachable Postgres DB (settings.DB_*) -- skipping end-to-end LIVE round-trip.")

    test_doc_id = "PYTEST_CUSTOM_FIELD_E2E_DOC"
    try:
        build_result = build_canonical_tb(
            manifest_file=manifest_and_ground_truth["manifest_file"],
            ground_truth_file=manifest_and_ground_truth["ground_truth_file"],
            output_dir=str(tmp_path / "out"),
            tb_doc_id=test_doc_id,
            company_details={"custom_field_1": "e2e-value-1"},
        )
        canonical_path = [a for a in build_result["artifacts"] if a.endswith("canonical_tb.parquet")][0]

        persist_result = persist_canonical_tb_to_live(canonical_tb_file=canonical_path, tb_doc_id=test_doc_id)
        assert persist_result["execution_status"] == "SUCCESS"

        from modes.trial_balance.pipeline.db import db_cursor

        with db_cursor() as cur:
            cur.execute(
                "SELECT gl_code, custom_field_1 FROM live_tb_table WHERE tb_doc_id = %s ORDER BY gl_code",
                (test_doc_id,),
            )
            rows = [dict(r) for r in cur.fetchall()]

        assert len(rows) == 2
        assert all(r["custom_field_1"] == "e2e-value-1" for r in rows)
    finally:
        from modes.trial_balance.pipeline.db import db_cursor

        with db_cursor(dict_rows=False) as cur:
            cur.execute("DELETE FROM live_tb_table WHERE tb_doc_id = %s", (test_doc_id,))
            cur.execute("DELETE FROM live_document_table WHERE tb_doc_id = %s", (test_doc_id,))
