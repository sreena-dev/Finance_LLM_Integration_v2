"""User-entered figures: scope guard, validation, apply/revert, advisory
footing, and re-upload carry-forward. Pure logic -- no store, no Redis.

Run with::
    cd backend
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 venv/Scripts/python.exe -m pytest modes/financial_statement/upload/test_edits.py -q
"""

from __future__ import annotations

import pytest

from modes.financial_statement.upload import edits


def _table_md(rows):
    header = "| Particulars | Amount |"
    sep = "| --- | --- |"
    return "\n".join([header, sep] + rows)


def _quality(unreadable=None, recovered=None, edits_=None):
    return {
        "unreadable_cells": unreadable or [],
        "recovered_cells": recovered or [],
        "user_edits": edits_ or [],
    }


def _finding(row, col, marker, **extra):
    return {"table_id": "t1", "row_index": row, "col_index": col,
            "row_label": "Revenue", "column": "Amount", "marker": marker, **extra}


UNREADABLE = '[unreadable: page 1, table t1, row "Revenue", col "Amount"]'
RECOVERED = '[recovered 5,000; second read, confidence low, it is the only read of this cell: page 1, table t1, row "Revenue", col "Amount"]'


# ---------------------------------------------------------------------------
# validate_value: stricter than float()
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("12859", "12859"),
    ("12,859", "12,859"),
    ("1,50,000.50", "1,50,000.50"),
    ("(5,000)", "(5,000)"),
    ("-5000", "(5000)"),
    ("₹ 1,234", "1,234"),
    (" 100 ", "100"),
])
def test_accepted_values_normalise(raw, expected):
    assert edits.validate_value(raw) == expected


@pytest.mark.parametrize("raw", [
    "nan", "inf", "-inf", "1e9", "-", "", "   ", "1|2", "[x]", "1\n2",
    "((5))", "(5", "5)", "-(5)", "(-5)", "x" * 25, "12.34.56", "abc",
])
def test_refused_values_raise_422(raw):
    with pytest.raises(edits.EditError) as exc:
        edits.validate_value(raw)
    assert exc.value.status == 422


# ---------------------------------------------------------------------------
# cell_state
# ---------------------------------------------------------------------------

def test_cell_states():
    assert edits.cell_state(UNREADABLE) == "unreadable"
    assert edits.cell_state(RECOVERED) == "recovered"
    assert edits.cell_state("12,859 [user-entered]") == "user_entered"
    assert edits.cell_state("12,859") == "clean"


# ---------------------------------------------------------------------------
# apply(): scope guard -- the server enforces this on stored state, never on
# what the client claims
# ---------------------------------------------------------------------------

def test_a_clean_cell_is_refused():
    md = _table_md(["| Revenue | 12,859 |"])
    with pytest.raises(edits.EditError) as exc:
        edits.apply(md, _quality(), doc_id="up_abc", stored_table_id="up_abc_t1",
                    request={"row_index": 0, "col_index": 1, "expected_cell": "12,859",
                             "action": "set", "value": "1"},
                    user_id="u1")
    assert exc.value.status == 409
    assert exc.value.code == "not_editable"


def test_a_promoted_figure_has_no_marker_and_is_refused_as_clean():
    """A promoted figure is a plain number with no marker at all -- it must
    be indistinguishable from an ordinary extracted cell to this guard."""
    md = _table_md(["| Revenue | 12,859 |"])
    q = _quality(recovered=[{"table_id": "t1", "row_index": 0, "col_index": 1,
                             "promoted": True, "recovered_value": 12859.0}])
    with pytest.raises(edits.EditError) as exc:
        edits.apply(md, q, doc_id="up_abc", stored_table_id="up_abc_t1",
                    request={"row_index": 0, "col_index": 1, "expected_cell": "12,859",
                             "action": "set", "value": "1"},
                    user_id="u1")
    assert exc.value.status == 409


def test_an_unreadable_cell_can_be_set():
    md = _table_md([f"| Revenue | {UNREADABLE} |"])
    q = _quality(unreadable=[_finding(0, 1, UNREADABLE)])
    new_md, new_q, result = edits.apply(
        md, q, doc_id="up_abc", stored_table_id="up_abc_t1",
        request={"row_index": 0, "col_index": 1, "expected_cell": UNREADABLE,
                 "action": "set", "value": "12,859"},
        user_id="u1",
    )
    assert result["unchanged"] is False
    assert "12,859 [user-entered]" in new_md
    assert new_q["unreadable_cells"] == []
    assert len(new_q["user_edits"]) == 1
    rec = new_q["user_edits"][0]
    assert rec["original_marker"] == UNREADABLE
    assert rec["original_state"] == "unreadable"
    assert rec["value"] == "12,859"
    assert rec["by"] == "u1"


def test_confirming_a_recovered_cell_uses_the_stored_value_never_the_clients():
    md = _table_md([f"| Revenue | {RECOVERED} |"])
    q = _quality(unreadable=[_finding(0, 1, RECOVERED, recovered_text="5,000", confidence="low")])
    new_md, new_q, result = edits.apply(
        md, q, doc_id="up_abc", stored_table_id="up_abc_t1",
        request={"row_index": 0, "col_index": 1, "expected_cell": RECOVERED,
                 "action": "confirm", "value": "999999"},  # ignored
        user_id="u1",
    )
    assert "5,000 [user-entered]" in new_md
    assert "999999" not in new_md


def test_confirm_on_an_unreadable_cell_is_refused():
    md = _table_md([f"| Revenue | {UNREADABLE} |"])
    q = _quality(unreadable=[_finding(0, 1, UNREADABLE)])
    with pytest.raises(edits.EditError) as exc:
        edits.apply(md, q, doc_id="up_abc", stored_table_id="up_abc_t1",
                    request={"row_index": 0, "col_index": 1, "expected_cell": UNREADABLE,
                             "action": "confirm"},
                    user_id="u1")
    assert exc.value.status == 409


def test_a_stale_expected_cell_is_refused_with_409():
    md = _table_md([f"| Revenue | {UNREADABLE} |"])
    q = _quality(unreadable=[_finding(0, 1, UNREADABLE)])
    with pytest.raises(edits.EditError) as exc:
        edits.apply(md, q, doc_id="up_abc", stored_table_id="up_abc_t1",
                    request={"row_index": 0, "col_index": 1, "expected_cell": "something else",
                             "action": "set", "value": "1"},
                    user_id="u1")
    assert exc.value.status == 409
    assert exc.value.code == "cell_changed"


def test_setting_the_same_value_twice_is_idempotent():
    md = _table_md([f"| Revenue | {UNREADABLE} |"])
    q = _quality(unreadable=[_finding(0, 1, UNREADABLE)])
    md2, q2, _ = edits.apply(
        md, q, doc_id="up_abc", stored_table_id="up_abc_t1",
        request={"row_index": 0, "col_index": 1, "expected_cell": UNREADABLE,
                 "action": "set", "value": "12,859"},
        user_id="u1",
    )
    md3, q3, result = edits.apply(
        md2, q2, doc_id="up_abc", stored_table_id="up_abc_t1",
        request={"row_index": 0, "col_index": 1, "expected_cell": "12,859 [user-entered]",
                 "action": "set", "value": "12,859"},
        user_id="u1",
    )
    assert result["unchanged"] is True
    assert md3 == md2


def test_reediting_a_user_entered_cell_updates_the_same_record():
    md = _table_md([f"| Revenue | {UNREADABLE} |"])
    q = _quality(unreadable=[_finding(0, 1, UNREADABLE)])
    md2, q2, _ = edits.apply(
        md, q, doc_id="up_abc", stored_table_id="up_abc_t1",
        request={"row_index": 0, "col_index": 1, "expected_cell": UNREADABLE,
                 "action": "set", "value": "12,859"}, user_id="u1")
    md3, q3, result = edits.apply(
        md2, q2, doc_id="up_abc", stored_table_id="up_abc_t1",
        request={"row_index": 0, "col_index": 1, "expected_cell": "12,859 [user-entered]",
                 "action": "set", "value": "13,000"}, user_id="u2")
    assert "13,000 [user-entered]" in md3
    assert len(q3["user_edits"]) == 1
    assert q3["user_edits"][0]["value"] == "13,000"
    assert len(q3["user_edits"][0]["history"]) == 2


def test_revert_restores_the_exact_original_marker_and_reinserts_the_finding():
    md = _table_md([f"| Revenue | {UNREADABLE} |"])
    q = _quality(unreadable=[_finding(0, 1, UNREADABLE)])
    md2, q2, _ = edits.apply(
        md, q, doc_id="up_abc", stored_table_id="up_abc_t1",
        request={"row_index": 0, "col_index": 1, "expected_cell": UNREADABLE,
                 "action": "set", "value": "12,859"}, user_id="u1")
    md3, q3, result = edits.apply(
        md2, q2, doc_id="up_abc", stored_table_id="up_abc_t1",
        request={"row_index": 0, "col_index": 1, "expected_cell": "12,859 [user-entered]",
                 "action": "revert"}, user_id="u1")
    assert md3 == md
    assert q3["unreadable_cells"] == [_finding(0, 1, UNREADABLE)]
    assert all(not e.get("active", True) for e in q3["user_edits"])


def test_repeated_revert_is_a_noop():
    md = _table_md([f"| Revenue | {UNREADABLE} |"])
    q = _quality(unreadable=[_finding(0, 1, UNREADABLE)])
    md2, q2, _ = edits.apply(
        md, q, doc_id="up_abc", stored_table_id="up_abc_t1",
        request={"row_index": 0, "col_index": 1, "expected_cell": UNREADABLE,
                 "action": "set", "value": "12,859"}, user_id="u1")
    md3, q3, _ = edits.apply(
        md2, q2, doc_id="up_abc", stored_table_id="up_abc_t1",
        request={"row_index": 0, "col_index": 1, "expected_cell": "12,859 [user-entered]",
                 "action": "revert"}, user_id="u1")
    with pytest.raises(edits.EditError) as exc:
        edits.apply(md3, q3, doc_id="up_abc", stored_table_id="up_abc_t1",
                    request={"row_index": 0, "col_index": 1, "expected_cell": UNREADABLE,
                             "action": "revert"}, user_id="u1")
    assert exc.value.status == 409  # reverted record is inactive -> not_editable


# ---------------------------------------------------------------------------
# The tools' own parser sees a real, computable number
# ---------------------------------------------------------------------------

def test_tools_fs_parses_the_tagged_cell_as_a_plain_number():
    import tools_fs
    assert tools_fs._re_parse_number("12,859 [user-entered]") == 12859.0
    assert tools_fs._re_parse_number("(5,000) [user-entered]") == -5000.0


def test_the_cell_marker_regex_no_longer_matches_a_user_entered_cell():
    import tools_fs
    assert not tools_fs._CELL_MARKER_RE.search("12,859 [user-entered]")


# ---------------------------------------------------------------------------
# Advisory footing -- never blocks, and cannot produce a false "ties"
# ---------------------------------------------------------------------------

def test_footing_ties_when_the_entered_figure_closes_the_total():
    md = _table_md([
        "| Employee Benefit Expenses | 100 |",
        "| Finance Cost | 460 |",
        "| Other Expenses | 25,000 |",
        "| Total Expenditure | 25,560 |",
    ])
    result = edits.advisory_footing(md, 1, 1)
    assert result["verdict"] == "ties"


def test_footing_not_checkable_with_fewer_than_three_components():
    """Two line items summing to a total is too little to trust -- the design
    refuses rather than risk a coincidental match."""
    md = _table_md(["| Finance Cost | 460 |", "| Total Expenditure | 460 |"])
    assert edits.advisory_footing(md, 0, 1)["verdict"] == "not_checkable"


def test_footing_does_not_tie_is_advisory_worded_never_wrong():
    md = _table_md([
        "| Employee Benefit Expenses | 1 |",
        "| Finance Cost | 100 |",
        "| Other Expenses | 25,000 |",
        "| Total Expenditure | 25,460 |",
    ])
    result = edits.advisory_footing(md, 1, 1)
    assert result["verdict"] == "does_not_tie"
    assert "simple check" in result["note"]


def test_footing_not_checkable_with_no_total_below():
    md = _table_md(["| Finance Cost | 460 |"])
    assert edits.advisory_footing(md, 0, 1)["verdict"] == "not_checkable"


def test_footing_never_blocks_a_set_even_when_it_does_not_tie():
    md = _table_md([
        "| Employee Benefit Expenses | 1 |",
        f"| Finance Cost | {UNREADABLE} |",
        "| Other Expenses | 25,000 |",
        "| Total Expenditure | 999 |",
    ])
    q = _quality(unreadable=[_finding(1, 1, UNREADABLE)])
    _, new_q, result = edits.apply(
        md, q, doc_id="up_abc", stored_table_id="up_abc_t1",
        request={"row_index": 1, "col_index": 1, "expected_cell": UNREADABLE,
                 "action": "set", "value": "460"}, user_id="u1")
    assert result["unchanged"] is False
    assert result["footing"]["verdict"] == "does_not_tie"
    assert new_q["user_edits"][0]["footing"]["verdict"] == "does_not_tie"


# ---------------------------------------------------------------------------
# cells_for_table / user_edits_note
# ---------------------------------------------------------------------------

def test_cells_for_table_addresses_by_coordinates_not_marker_text():
    """Two rows share an identical marker string (duplicate row labels) --
    coordinates, not text, must tell them apart."""
    md = _table_md([f"| Additions | {UNREADABLE} |", f"| Additions | {UNREADABLE} |"])
    q = _quality(unreadable=[_finding(0, 1, UNREADABLE), _finding(1, 1, UNREADABLE)])
    cells = edits.cells_for_table(q, "up_abc", "up_abc_t1", md)
    assert [(c["row_index"], c["col_index"]) for c in cells] == [(0, 1), (1, 1)]


def test_user_edits_note_names_the_figure_and_its_prior_state():
    md = _table_md([f"| Revenue | {UNREADABLE} |"])
    q = _quality(unreadable=[_finding(0, 1, UNREADABLE)])
    _, new_q, _ = edits.apply(
        md, q, doc_id="up_abc", stored_table_id="up_abc_t1",
        request={"row_index": 0, "col_index": 1, "expected_cell": UNREADABLE,
                 "action": "set", "value": "12,859"}, user_id="u1")
    note = edits.user_edits_note(new_q, "up_abc", "up_abc_t1")
    assert "DATA QUALITY NOTE" in note
    assert "12,859" in note and "unreadable" in note


def test_user_edits_note_is_empty_with_no_edits():
    assert edits.user_edits_note(_quality(), "up_abc", "up_abc_t1") == ""


# ---------------------------------------------------------------------------
# carry_forward: a re-upload of the same file must not silently wipe an edit
# ---------------------------------------------------------------------------

class _FakeDoc:
    def __init__(self, doc_id, tables, quality):
        self.doc_id = doc_id
        self.tables = tables
        self.quality = quality


def test_carry_forward_reapplies_when_the_marker_still_matches():
    old_q = _quality()
    md = _table_md([f"| Revenue | {UNREADABLE} |"])
    old_q["unreadable_cells"] = [_finding(0, 1, UNREADABLE)]
    _, old_q, _ = edits.apply(
        md, old_q, doc_id="up_abc", stored_table_id="up_abc_t1",
        request={"row_index": 0, "col_index": 1, "expected_cell": UNREADABLE,
                 "action": "set", "value": "12,859"}, user_id="u1")

    # A real re-ingestion of the same file produces its own quality, which --
    # since the extraction is deterministic and the marker still matches --
    # flags the SAME cell as unreadable again.
    new_quality = _quality(unreadable=[_finding(0, 1, UNREADABLE)])
    new_doc = _FakeDoc("up_abc", [{"table_id": "up_abc_t1", "table_md": md}], new_quality)
    applied, dropped = edits.carry_forward(old_q, new_doc)
    assert (applied, dropped) == (1, 0)
    assert "12,859 [user-entered]" in new_doc.tables[0]["table_md"]


def test_carry_forward_drops_an_edit_whose_marker_no_longer_matches():
    old_q = _quality()
    md = _table_md([f"| Revenue | {UNREADABLE} |"])
    old_q["unreadable_cells"] = [_finding(0, 1, UNREADABLE)]
    _, old_q, _ = edits.apply(
        md, old_q, doc_id="up_abc", stored_table_id="up_abc_t1",
        request={"row_index": 0, "col_index": 1, "expected_cell": UNREADABLE,
                 "action": "set", "value": "12,859"}, user_id="u1")

    changed_md = _table_md(["| Revenue | 999 |"])  # re-extraction now reads it cleanly
    new_doc = _FakeDoc("up_abc", [{"table_id": "up_abc_t1", "table_md": changed_md}], {})
    applied, dropped = edits.carry_forward(old_q, new_doc)
    assert (applied, dropped) == (0, 1)
    assert new_doc.tables[0]["table_md"] == changed_md  # untouched


def test_carry_forward_is_a_noop_with_no_prior_edits():
    new_doc = _FakeDoc("up_abc", [], {})
    assert edits.carry_forward(_quality(), new_doc) == (0, 0)


# ---------------------------------------------------------------------------
# quality.py: a user-entered figure is disclosed, never listed as withheld
# ---------------------------------------------------------------------------

def _document_with(quality, table_md):
    from modes.financial_statement.upload.store import UploadedDocument

    return UploadedDocument(
        doc_id="up_abc", user_id="u", conversation_id="c", filename="f.pdf",
        document={}, identification={"entity_name": "X Ltd", "financial_year": "2023-24"},
        quality=quality,
        tables=[{"table_id": "up_abc_t1", "table_md": table_md,
                 "page_ocr_start": 1, "financial_stmt_type": "balance_sheet"}],
    )


def test_quality_report_discloses_a_user_entered_figure_and_drops_it_from_withheld():
    from modes.financial_statement.upload import quality

    q = _quality()
    q["unreadable_cells"] = [_finding(0, 1, UNREADABLE, page_no=3)]
    md = _table_md([f"| Revenue | {UNREADABLE} |"])
    new_md, new_q, _ = edits.apply(
        md, q, doc_id="up_abc", stored_table_id="up_abc_t1",
        request={"row_index": 0, "col_index": 1, "expected_cell": UNREADABLE,
                 "action": "set", "value": "12,859"}, user_id="u1")

    report = quality.quality_report(_document_with(new_q, new_md))

    assert "ENTERED BY THE USER" in report
    assert 'row "Revenue", column "Amount" = 12,859' in report
    assert "(previously unreadable" in report
    # Not listed as still withheld -- edits.apply already removed the finding.
    assert "COULD NOT BE READ RELIABLY" not in report
    assert "computed before user entries" in report


def test_quality_report_no_figures_withheld_branch_still_names_user_edits():
    """With nothing left in `unreadable_cells`, the old wording claimed every
    figure was machine-confirmed -- true before this feature, false once one
    of them is a user's typed figure instead."""
    from modes.financial_statement.upload import quality

    q = _quality()
    q["unreadable_cells"] = [_finding(0, 1, UNREADABLE, page_no=1)]
    md = _table_md([f"| Revenue | {UNREADABLE} |"])
    new_md, new_q, _ = edits.apply(
        md, q, doc_id="up_abc", stored_table_id="up_abc_t1",
        request={"row_index": 0, "col_index": 1, "expected_cell": UNREADABLE,
                 "action": "set", "value": "12,859"}, user_id="u1")

    report = quality.quality_report(_document_with(new_q, new_md))

    assert "No figures remain withheld" in report
    assert "entered by the user" in report
    assert "were either read cleanly or confirmed by the arithmetic" not in report


def test_summary_counts_active_user_edits_and_excludes_reverted_ones():
    q = _quality()
    q["unreadable_cells"] = [_finding(0, 1, UNREADABLE, page_no=1)]
    md = _table_md([f"| Revenue | {UNREADABLE} |"])
    new_md, new_q, result = edits.apply(
        md, q, doc_id="up_abc", stored_table_id="up_abc_t1",
        request={"row_index": 0, "col_index": 1, "expected_cell": UNREADABLE,
                 "action": "set", "value": "12,859"}, user_id="u1")

    doc = _document_with(new_q, new_md)
    assert doc.summary()["user_entered_cells"] == 1

    reverted_md, reverted_q, _ = edits.apply(
        new_md, new_q, doc_id="up_abc", stored_table_id="up_abc_t1",
        request={"row_index": 0, "col_index": 1,
                 "expected_cell": result["cell"], "action": "revert"}, user_id="u1")

    doc2 = _document_with(reverted_q, reverted_md)
    assert doc2.summary()["user_entered_cells"] == 0
