"""Tests for backend/tools/input_intake_checks.py -- the ingestion error
catalog's WARNING-only header/sheet/formula-error scan
(TB-v2-git/Trial_Balance_ingestion_error.md)."""

import openpyxl
import pytest

from modes.trial_balance.pipeline.tools.input_intake_checks import assess_ingestion_intake


def _codes(findings):
    return {f["code"] for f in findings}


def test_clean_workbook_has_no_findings(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "TB"
    ws.append(["GL Code", "GL Name", "Opening Balance", "Debit", "Credit", "Closing Balance"])
    ws.append(["1001", "Cash", 1000, 500, 0, 1500])
    ws.append(["1002", "Bank", 2000, 1000, 500, 2500])
    path = tmp_path / "clean.xlsx"
    wb.save(path)

    findings = assess_ingestion_intake(str(path))
    assert findings == []


def test_merged_header_cells_detected(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "TB"
    ws.append(["GL Code", "GL Name", "Opening Balance", "Debit", "Credit", "Closing Balance"])
    ws.append(["1001", "Cash", 1000, 500, 0, 1500])
    ws.merge_cells("A1:B1")
    path = tmp_path / "merged.xlsx"
    wb.save(path)

    findings = assess_ingestion_intake(str(path))
    assert "MERGED_HEADER_CELLS" in _codes(findings)
    assert all(f["severity"] == "WARNING" for f in findings)


def test_hidden_tb_shaped_sheet_detected(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Visible"
    ws.append(["Note", "Text"])
    ws.append(["1", "not a TB sheet"])

    hidden = wb.create_sheet("HiddenTB")
    hidden.append(["GL Code", "GL Name", "Closing Balance"])
    hidden.append(["2001", "Payable", -500])
    hidden.sheet_state = "hidden"

    path = tmp_path / "hidden.xlsx"
    wb.save(path)

    findings = assess_ingestion_intake(str(path))
    assert "HIDDEN_SHEET_WITH_DATA" in _codes(findings)
    hidden_finding = next(f for f in findings if f["code"] == "HIDDEN_SHEET_WITH_DATA")
    assert hidden_finding["sheet"] == "HiddenTB"


def test_protected_sheet_detected(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "TB"
    ws.append(["GL Code", "GL Name", "Closing Balance"])
    ws.append(["1001", "Cash", 1500])
    ws.protection.sheet = True
    path = tmp_path / "protected.xlsx"
    wb.save(path)

    findings = assess_ingestion_intake(str(path))
    assert "PROTECTED_SHEET" in _codes(findings)


def test_formula_error_value_detected(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "TB"
    ws.append(["GL Code", "GL Name", "Opening Balance", "Debit", "Credit", "Closing Balance"])
    ws.append(["1001", "Cash", 1000, 500, 0, 1500])
    ws["F3"] = "#REF!"
    path = tmp_path / "formula_error.xlsx"
    wb.save(path)

    findings = assess_ingestion_intake(str(path))
    assert "FORMULA_ERROR_VALUE" in _codes(findings)
    finding = next(f for f in findings if f["code"] == "FORMULA_ERROR_VALUE")
    assert "F3" in finding["message"]


def test_duplicate_header_name_detected(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "TB"
    ws.append(["GL Code", "GL Name", "GL Name", "Opening Balance", "Debit", "Credit", "Closing Balance"])
    ws.append(["1001", "Cash", "Cash dup", 1000, 500, 0, 1500])
    path = tmp_path / "dup_header.xlsx"
    wb.save(path)

    findings = assess_ingestion_intake(str(path))
    assert "DUPLICATE_HEADER_NAME" in _codes(findings)


def test_nonexistent_file_returns_no_findings_not_raise(tmp_path):
    path = tmp_path / "does_not_exist.xlsx"
    findings = assess_ingestion_intake(str(path))
    assert findings == []


def test_unreadable_format_returns_no_findings_not_raise(tmp_path):
    # A plain .csv isn't OLE2/zip-based, so load_all_sheets_any_format raises
    # UnsupportedWorkbookFormatError -- must be swallowed, not propagated
    # (this is a best-effort secondary scan, not a second gate).
    path = tmp_path / "tb.csv"
    path.write_text("GL Code,GL Name,Closing Balance\n1001,Cash,1500\n")
    findings = assess_ingestion_intake(str(path))
    assert findings == []
