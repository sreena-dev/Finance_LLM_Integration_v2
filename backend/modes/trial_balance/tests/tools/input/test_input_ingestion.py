"""Tests for backend/tools/input.py -- preview_excel_data.

process_input_documents (the old worksheet-classifier + manual-column-mapping upload
ingestion tool) was retired -- ingest_tb_to_live (backend/tools/db_bridge.py) is now
the single entry point for both live and uploaded TB+Grouping files. Its own two-file
(Scenario B) shape is exercised in tests/tools/db_bridge/test_ingest_tb_to_live_end_to_end.py.
"""

import json

import openpyxl
import pytest

from modes.trial_balance.pipeline.tools import preview_excel_data

TB_HEADER = ["GL Code", "GL Name", "Opening Balance", "Debit", "Credit", "Closing Balance"]

# Header cells are normalised (trimmed + lower-cased) during ingestion, so this is
# what the detected column names actually look like downstream.
TB_HEADER_NORMALISED = [h.lower() for h in TB_HEADER]

TB_ROWS = [
    ("1001", "Cash in Hand", 100000.0, 50000.0, 20000.0, 130000.0),
    ("1002", "Bank Account - Current", 2000000.0, 1500000.0, 800000.0, 2700000.0),
    ("1101", "Trade Receivables", 3500000.0, 4200000.0, 3900000.0, 3800000.0),
    ("2001", "Trade Payables", -1800000.0, 900000.0, 1100000.0, -2000000.0),
    ("3001", "Share Capital", -5000000.0, 0.0, 0.0, -5000000.0),
    ("4001", "Revenue from Operations", -9000000.0, 0.0, 12000000.0, -21000000.0),
]

GROUPING_HEADER = ["GL Code", "GL Name", "FS Head"]

GROUPING_ROWS = [
    ("1001", "Cash in Hand", "Current Assets"),
    ("1002", "Bank Account - Current", "Current Assets"),
    ("1101", "Trade Receivables", "Current Assets"),
    ("2001", "Trade Payables", "Current Liabilities"),
    ("3001", "Share Capital", "Equity"),
    ("4001", "Revenue from Operations", "Revenue"),
]


def _xlsx(path, sheets):
    """sheets: list of (sheet_title, list_of_row_tuples). First sheet replaces the default."""
    wb = openpyxl.Workbook()
    for i, (title, rows) in enumerate(sheets):
        ws = wb.active if i == 0 else wb.create_sheet()
        ws.title = title
        for r in rows:
            ws.append(list(r))
    wb.save(path)
    return path


@pytest.fixture
def tb_file(tmp_path):
    return _xlsx(tmp_path / "tb.xlsx", [("TB", [TB_HEADER, *TB_ROWS])])


@pytest.fixture
def grouping_file(tmp_path):
    return _xlsx(tmp_path / "grouping.xlsx", [("Grouping", [GROUPING_HEADER, *GROUPING_ROWS])])


# ── preview_excel_data ──────────────────────────────────────────────────────


class TestPreviewExcelData:
    def test_missing_file_raises_pipeline_file_error(self, tmp_path):
        result = preview_excel_data(excel_path=str(tmp_path / "nope.xlsx"), output_dir=str(tmp_path))
        assert result["execution_status"] == "FAILED"
        assert "not found" in result["message"].lower()

    def test_reads_columns_and_sample_rows_from_a_real_workbook(self, tb_file, tmp_path):
        result = preview_excel_data(excel_path=str(tb_file), output_dir=str(tmp_path))
        assert result["execution_status"] == "SUCCESS"

        data = json.loads((tmp_path / "preview_tb.json").read_text(encoding="utf-8"))
        assert data["columns"] == TB_HEADER_NORMALISED

        # KNOWN QUIRK, pinned deliberately: preview_excel_data renames the columns from
        # the detected header row but does NOT slice that row off the frame, so the
        # header repeats as sample_rows[0]. process_input_documents does slice it
        # (df.slice(data_start, ...)), so the parquet it writes is clean -- the quirk is
        # confined to the preview payload the mapping UI renders. Asserted as-is rather
        # than "fixed": the frontend consumes this shape today, and changing it is a UI
        # contract change, not a bug fix. Revisit with the frontend, not in isolation.
        assert len(data["sample_rows"]) == len(TB_ROWS) + 1
        assert data["sample_rows"][0]["gl code"] == "GL Code"
        assert data["sample_rows"][1]["gl code"] == "1001"
        assert data["sample_rows"][1]["gl name"] == "Cash in Hand"

    def test_declares_the_artifact_it_writes(self, tb_file, tmp_path):
        result = preview_excel_data(excel_path=str(tb_file), output_dir=str(tmp_path))
        assert len(result["artifacts"]) == 1
        assert result["artifacts"][0].endswith("preview_tb.json")

    def test_reads_csv_through_the_separate_csv_branch(self, tmp_path):
        csv_path = tmp_path / "tb.csv"
        lines = [",".join(TB_HEADER)] + [",".join(str(v) for v in r) for r in TB_ROWS]
        csv_path.write_text("\n".join(lines), encoding="utf-8")

        result = preview_excel_data(excel_path=str(csv_path), output_dir=str(tmp_path))
        assert result["execution_status"] == "SUCCESS"
        data = json.loads((tmp_path / "preview_tb.json").read_text(encoding="utf-8"))
        assert data["columns"] == TB_HEADER_NORMALISED

    def test_grouping_mode_extracts_structural_headers(self, grouping_file, tmp_path):
        """is_grouping=True harvests distinct non-numeric values from the first
        name/description-like column -- this is what the frontend shows as the
        detected FS-head list."""
        result = preview_excel_data(excel_path=str(grouping_file), is_grouping=True, output_dir=str(tmp_path))
        assert result["execution_status"] == "SUCCESS"

        data = json.loads((tmp_path / "preview_grouping.json").read_text(encoding="utf-8"))
        assert data["sample_headers"]
        assert "Cash in Hand" in data["sample_headers"]

    def test_non_grouping_mode_leaves_sample_headers_empty(self, tb_file, tmp_path):
        result = preview_excel_data(excel_path=str(tb_file), output_dir=str(tmp_path))
        assert result["execution_status"] == "SUCCESS"
        data = json.loads((tmp_path / "preview_tb.json").read_text(encoding="utf-8"))
        assert data["sample_headers"] == []

    def test_header_buried_under_a_title_block_is_still_found(self, tmp_path):
        """Real exports routinely carry a title/entity/period block above the header
        row. _find_header must skip it rather than treat row 1 as the header."""
        path = _xlsx(
            tmp_path / "titled.xlsx",
            [("TB", [["ACME Ltd"], ["Trial Balance for FY 2025-26"], [], TB_HEADER, *TB_ROWS])],
        )
        result = preview_excel_data(excel_path=str(path), output_dir=str(tmp_path))
        assert result["execution_status"] == "SUCCESS"
        data = json.loads((tmp_path / "preview_titled.json").read_text(encoding="utf-8"))
        assert "gl code" in data["columns"]
        assert "closing balance" in data["columns"]

    def test_duplicate_column_names_are_disambiguated(self, tmp_path):
        path = _xlsx(
            tmp_path / "dupes.xlsx",
            [("TB", [["GL Code", "Amount", "Amount"], ["1001", 10, 20]])],
        )
        result = preview_excel_data(excel_path=str(path), output_dir=str(tmp_path))
        assert result["execution_status"] == "SUCCESS"
        cols = json.loads((tmp_path / "preview_dupes.json").read_text(encoding="utf-8"))["columns"]
        assert len(cols) == len(set(cols)), f"duplicate column names survived: {cols}"

    def test_corrupt_workbook_fails_without_leaking_a_traceback(self, tmp_path):
        """Regression for the client-facing traceback leak: this tool builds its own
        error payload rather than letting the decorator handle it, and used to embed
        traceback.format_exc() -- absolute server paths included -- in the response
        returned to API callers."""
        bad = tmp_path / "corrupt.xlsx"
        bad.write_text("this is not an excel file at all", encoding="utf-8")

        result = preview_excel_data(excel_path=str(bad), output_dir=str(tmp_path))
        assert result["execution_status"] == "FAILED"

        blob = json.dumps(result)
        assert "Traceback (most recent call last)" not in blob
        assert ".py\"" not in blob
        # Still useful: the error class and a correlation handle for the server log.
        assert result["errors"][0]["type"] == "ParseError"
        assert len(result["errors"][0]["error_id"]) == 12
