"""Tests for backend/tools/quality_gate.py's ingestion-error-catalog
additions (TB-v2-git/Trial_Balance_ingestion_error.md) -- all WARN-only,
per this module's own "only zero rows is FAIL" philosophy."""

from modes.trial_balance.pipeline.tools.quality_gate import run_input_quality_gate
from modes.trial_balance.pipeline.tools.tb_models import TBRow


def _row(gl_code, gl_name, opening=0.0, debit=0.0, credit=0.0, closing=0.0):
    return TBRow(gl_code=gl_code, gl_name=gl_name, opening=opening, debit=debit, credit=credit, closing=closing)


def test_clean_but_few_rows_warns_not_fails():
    # 2 rows < SUSPICIOUSLY_FEW_ROWS_THRESHOLD (5) -- status is WARN (not the
    # PASS a fully "clean" input would otherwise get), but never FAIL.
    rows = [_row("1001", "Cash", closing=100.0), _row("2001", "Payable", closing=-100.0)]
    report = run_input_quality_gate(rows, {})
    assert report.status == "WARN"
    assert report.missing_gl_name_codes == []
    assert report.value_too_long == []
    assert report.value_out_of_range_gl_codes == []
    assert report.suspiciously_few_rows is True


def test_missing_gl_name_warns_not_fails():
    rows = [_row("1001", ""), _row("2001", "Payable")]
    report = run_input_quality_gate(rows, {})
    assert report.status == "WARN"
    assert report.missing_gl_name_codes == ["1001"]


def test_value_too_long_warns():
    long_name = "X" * 300
    rows = [_row("1001", long_name)]
    report = run_input_quality_gate(rows, {})
    assert report.status == "WARN"
    assert len(report.value_too_long) == 1
    assert report.value_too_long[0]["field"] == "gl_name"
    assert report.value_too_long[0]["gl_code"] == "1001"


def test_value_out_of_range_warns():
    rows = [_row("1001", "Cash", closing=2 * 10 ** 15)]
    report = run_input_quality_gate(rows, {})
    assert report.status == "WARN"
    assert report.value_out_of_range_gl_codes == ["1001"]


def test_suspiciously_few_rows_flagged():
    rows = [_row("1001", "Cash")]
    report = run_input_quality_gate(rows, {})
    assert report.suspiciously_few_rows is True


def test_not_suspiciously_few_rows_when_five_or_more():
    rows = [_row(str(i), f"Account {i}") for i in range(5)]
    report = run_input_quality_gate(rows, {})
    assert report.suspiciously_few_rows is False


def test_blank_row_ratio_none_when_scanned_row_count_not_supplied():
    rows = [_row("1001", "Cash")]
    report = run_input_quality_gate(rows, {})
    assert report.blank_row_ratio is None


def test_high_blank_row_ratio_warns_when_supplied():
    rows = [_row("1001", "Cash"), _row("2001", "Payable")]
    report = run_input_quality_gate(rows, {}, scanned_row_count=10)  # 8/10 = 80% blank
    assert report.blank_row_ratio == 0.8
    assert any("no usable data" in w for w in report.warnings)


def test_low_blank_row_ratio_no_warning_text():
    rows = [_row(str(i), f"Account {i}") for i in range(9)]
    report = run_input_quality_gate(rows, {}, scanned_row_count=10)  # 1/10 = 10% blank
    assert report.blank_row_ratio == 0.1
    assert not any("scanned rows had no usable data" in w for w in report.warnings)


def test_zero_rows_still_fails_unaffected_by_new_checks():
    report = run_input_quality_gate([], {})
    assert report.status == "FAIL"
    assert report.missing_gl_name_codes == []
    assert report.suspiciously_few_rows is False
