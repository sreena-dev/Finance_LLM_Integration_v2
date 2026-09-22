"""End-to-end test for Scenario D (multi-year-in-one-workbook detection)
against a real, synthetic openpyxl workbook -- two fiscal years as
separate sheets, one shared grouping sheet with no year in its own name."""

import openpyxl

from modes.trial_balance.pipeline.tools.input_dispatch import parse_tb_input


def _write_workbook(path, sheets: dict):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    wb.save(path)


def test_two_fiscal_years_in_one_workbook_produce_two_parsed_inputs(tmp_path):
    path = tmp_path / "multi_year_export.xlsx"
    _write_workbook(path, {
        "TB FY2023-24": [
            ["GL Code", "GL Name", "Opening Balance", "Debit", "Credit", "Closing Balance"],
            ["5001", "Freehold Land", 0, 1000, 0, 1000],
        ],
        "TB FY2024-25": [
            ["GL Code", "GL Name", "Opening Balance", "Debit", "Credit", "Closing Balance"],
            ["5001", "Freehold Land", 1000, 500, 0, 1500],
        ],
        "Grouping": [
            ["GL Code", "Main Head", "Sub Head 1", "Sub Head 2", "BS/PL"],
            ["5001", "Non-current assets", "Property, Plant and Equipment", "Land", "BS"],
        ],
    })

    results = parse_tb_input([path])

    assert len(results) == 2
    labels = {r.metadata.financial_year for r in results}
    assert labels == {"FY2023-24", "FY2024-25"}

    by_label = {r.metadata.financial_year: r for r in results}
    assert by_label["FY2023-24"].tb_rows[0].closing == 1000
    assert by_label["FY2024-25"].tb_rows[0].closing == 1500
    # The shared, undated Grouping sheet applies to both detected years.
    for r in results:
        assert r.grouping_hints["5001"].known_fields["sub_head_2"] == "Land"


def test_single_year_workbook_is_not_routed_through_scenario_d(tmp_path):
    """Only one distinct TB-role fiscal year detected -- falls through to
    the ordinary single-year dispatch (Scenario A/B/C), not Scenario D."""
    path = tmp_path / "single_year.xlsx"
    _write_workbook(path, {
        "TB": [
            ["GL Code", "GL Name", "Opening Balance", "Debit", "Credit", "Closing Balance"],
            ["6001", "Cash", 0, 600, 0, 600],
        ],
    })

    results = parse_tb_input([path])
    assert len(results) == 1
