"""Validation rules: they check, and never change, a table."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.validate import (  # noqa: E402
    FootRow, NoteLink, check_balance_sheet, check_cross_table, check_footings, looks_like_total,
)


def rows(*specs):
    return [FootRow(label, kind, {2: value} if value is not ... else {}) for label, kind, value in specs]


def test_total_labels():
    assert looks_like_total("Total current assets") and looks_like_total("Grand Total")
    assert not looks_like_total("Share capital")


def test_a_subtotal_that_matches_its_items_passes():
    checks = check_footings("t1", 1, rows(("A", "item", 100.0), ("B", "item", 250.0), ("Total", "total", 350.0)), [2])
    assert len(checks) == 1 and checks[0].passed and checks[0].difference == 0
    assert checks[0].component_labels == ["A", "B"]


def test_a_wrong_total_fails_and_reports_both_numbers():
    checks = check_footings("t1", 1, rows(("A", "item", 100.0), ("B", "item", 250.0), ("Total", "total", 360.0)), [2])
    assert not checks[0].passed and checks[0].printed == 360.0 and checks[0].recomputed == 350.0


def test_a_grand_total_can_sum_earlier_subtotals():
    checks = check_footings("t1", 1, rows(
        ("A", "item", 100.0), ("B", "item", 200.0), ("Total 1", "subtotal", 300.0),
        ("C", "item", 50.0), ("Total 2", "subtotal", 50.0), ("Grand total", "total", 350.0),
    ), [2])
    assert [c.passed for c in checks] == [True, True, True]


def test_rounding_within_tolerance_passes():
    checks = check_footings("t1", 1, rows(("A", "item", 100.0), ("B", "item", 200.4), ("Total", "total", 300.0)), [2])
    assert checks[0].passed


def test_an_unread_component_makes_the_check_unprovable_not_failed():
    checks = check_footings("t1", 1, rows(("A", "item", 100.0), ("B", "item", None), ("Total", "total", 999.0)), [2])
    assert checks == []


def test_an_unread_total_is_not_checked():
    checks = check_footings("t1", 1, rows(("A", "item", 100.0), ("Total", "total", None)), [2])
    assert checks == []


def test_empty_cells_are_skipped_not_counted_as_zero():
    checks = check_footings("t1", 1, rows(("A", "item", 100.0), ("Heading", "heading", ...), ("Total", "total", 100.0)), [2])
    assert checks[0].passed


def test_balance_sheet_identity_passes_and_fails_per_column():
    ok = check_balance_sheet("t1", 1, rows(("Total assets", "total", 500.0), ("Total equity and liabilities", "total", 500.0)), [2])
    bad = check_balance_sheet("t1", 1, rows(("Total assets", "total", 500.0), ("Total equity and liabilities", "total", 480.0)), [2])
    assert ok[0].passed and not bad[0].passed and bad[0].difference == 20.0


def test_balance_sheet_check_needs_both_sides_and_readable_figures():
    assert check_balance_sheet("t1", 1, rows(("Total assets", "total", 500.0)), [2]) == []
    assert check_balance_sheet("t1", 1, rows(("Total assets", "total", None), ("Total equity and liabilities", "total", 5.0)), [2]) == []


def test_cross_table_link_pass_and_fail():
    ok = NoteLink("s", 1, "Reserves and surplus", "2", "2024", 250000.0, "n", 250000.0)
    bad = NoteLink("s", 1, "Refundable grant", "3", "2024", 90000.0, "n", 80000.0)
    checks = check_cross_table([ok, bad])
    assert checks[0].passed and not checks[1].passed
    assert "Note 3" in checks[1].subtotal_label
