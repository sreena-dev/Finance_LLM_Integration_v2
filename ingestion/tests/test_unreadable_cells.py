"""A value OCR destroyed must never reach the output looking like content.

Measured on MH-CPSU-ITSL-048 2024-25 SFS page 1: the "Investment Properties"
figure came back as `8Z5'E`. `parse_cell` correctly returned no value, but the
only flag it raised was the cosmetic `ocr_lookalike`, which `verify_table`
deliberately does not withhold on. So no finding was raised, the cell was never
redacted, and the raw string sat in the emitted table where a number belongs --
the core promise ("no unverified figure reaches the model") broken from the
other direction: not a wrong number, but garbage presented as if it were read.

Run with::

    cd ingestion
    venv/Scripts/python -m pytest tests/test_unreadable_cells.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.tables import parse_markdown_tables  # noqa: E402
from app.verify import draft_table, redact, resolve_recoveries, verify_table  # noqa: E402


def _table(value: str):
    md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Property, Plant & Equipment | 28,455 |\n"
        f"| Investment Properties | {value} |\n"
        "| Capital work in progress | 12,859 |\n"
        "| Other Intangible Assets | 2,524 |\n"
    )
    return parse_markdown_tables(md, 1)[0]


def _findings_for(table):
    _, findings = verify_table(table, 1)
    return [f for f in findings if f.row_label == "Investment Properties"]


def test_the_real_destroyed_value_is_withheld():
    """THE regression: the exact string docling produced on the live document."""
    findings = _findings_for(_table("8Z5'E"))

    assert len(findings) == 1
    assert "unreadable_text" in findings[0].reasons


def test_a_destroyed_value_is_redacted_to_a_marker():
    table = _table("8Z5'E")
    _, findings = verify_table(table, 1)
    redact(table, findings)

    row = next(r for r in range(len(table.rows)) if table.label(r) == "Investment Properties")
    assert table.rows[row][1].startswith("[unreadable:"), table.rows[row][1]
    assert "8Z5'E" not in table.to_markdown()


def test_lookalike_letters_standing_in_for_digits_are_withheld():
    """`S00` is a lookalike for 500 -- no digit parsed, but it tried to be one."""
    assert "unreadable_text" in _findings_for(_table("S00"))[0].reasons


def test_two_numbers_crushed_into_one_cell_are_withheld():
    """A merged cell holding two figures is not one figure, whatever else it is."""
    findings = _findings_for(_table("22,69,36,581.10 5,11,11,345.00"))

    assert findings, "a cell holding two numbers must not pass as a value"


def test_a_nil_dash_is_not_withheld():
    assert _findings_for(_table("-")) == []


def test_nil_written_out_is_not_withheld():
    assert _findings_for(_table("NIL")) == []


def test_pure_words_in_a_value_column_are_not_mistaken_for_a_broken_number():
    """No digit, no lookalike: this was never an attempt at a figure."""
    assert _findings_for(_table("Not applicable")) == []


def test_a_clean_number_is_not_withheld():
    assert _findings_for(_table("5,000")) == []


def test_a_marker_this_module_already_wrote_is_not_flagged_again():
    """Redaction output carries digits (page and table numbers); it must not be
    re-read as a second destroyed value on a later pass."""
    marker = '[unreadable: page 1, table t1, row "Investment Properties", col "Amount"]'
    assert _findings_for(_table(marker)) == []


def test_a_cell_the_number_binding_re_read_could_not_confirm_renders_unreadable():
    """A cell the OCR number-binding pipeline (pipeline.py's Pass 2b) tried
    its own targeted re-read on, which did NOT agree with the value already
    there -- `unsupported_by_ocr`. It must render exactly like any other
    withheld cell (`[unreadable: ...]`), never blank (an empty cell reads as
    a nil balance downstream) and never a `[recovered ...]` figure (there is
    no candidate for it: the re-read disagreed, it did not offer a number to
    show with a caveat)."""
    table = _table("28,455")
    row = next(r for r in range(len(table.rows)) if table.label(r) == "Investment Properties")

    draft = draft_table(table, 1, unsupported_cells={(row, 1)})
    _, findings, _ = resolve_recoveries(draft)

    matching = [f for f in findings if f.row_label == "Investment Properties"]
    assert len(matching) == 1
    assert "unsupported_by_ocr" in matching[0].reasons
    assert matching[0].recovered_text is None

    redact(table, matching)
    assert table.rows[row][1].startswith("[unreadable:")


def test_an_unsupported_cell_raises_no_rescue_request():
    """Its own targeted re-read already ran (in Pass 2b, before this table
    ever reaches draft_table) and disagreed -- requesting ANOTHER rescue
    here would double-spend budget on a call that already failed."""
    table = _table("28,455")
    row = next(r for r in range(len(table.rows)) if table.label(r) == "Investment Properties")

    draft = draft_table(table, 1, unsupported_cells={(row, 1)})

    assert (row, 1, "Investment Properties", "Amount") not in draft.rescue_requests


# --------------------------------------------------------------------------
# A line item that LOST its figures: an empty cell is normally invisible to
# verification, which made it indistinguishable from a section heading --
# silent data loss
# --------------------------------------------------------------------------

def _lost_table():
    return parse_markdown_tables(
        "| Particulars | Amount |\n| --- | --- |\n"
        "| EQUITY AND LIABILITIES |  |\n"
        "| (a) Long-term borrowings |  |\n"
        "| (b) Trade payables | 1,000.00 |\n", 1)[0]


def test_an_empty_cell_is_invisible_without_positive_evidence_of_loss():
    """The pre-existing behaviour, kept exactly: with nothing said about it,
    an empty cell is skipped -- which is what leaves a heading and a dropped
    line item indistinguishable."""
    table = _lost_table()
    draft = draft_table(table, 1)
    _, findings, _ = resolve_recoveries(draft)
    assert findings == []


def test_a_line_item_that_lost_its_figure_renders_unreadable_never_blank():
    table = _lost_table()
    draft = draft_table(table, 1, lost_figure_cells={(1, 1)})
    _, findings, _ = resolve_recoveries(draft)

    assert [f.row_label for f in findings] == ["(a) Long-term borrowings"]
    assert "figures_not_extracted" in findings[0].reasons
    assert findings[0].recovered_text is None

    redact(table, findings)
    # Not blank: an empty cell reads as a nil balance downstream.
    assert table.rows[1][1].startswith("[unreadable:")


def test_a_section_heading_stays_silent_when_it_is_not_named_as_lost():
    table = _lost_table()
    draft = draft_table(table, 1, lost_figure_cells={(1, 1)})
    _, findings, _ = resolve_recoveries(draft)
    assert all(f.row_label != "EQUITY AND LIABILITIES" for f in findings)
    assert table.rows[0][1] == ""


def test_a_lost_figure_earns_a_targeted_rescue_request():
    """No candidate exists for it, so it becomes an ordinary rescue request --
    the recovery ladder needs no new machinery."""
    table = _lost_table()
    draft = draft_table(table, 1, lost_figure_cells={(1, 1)})
    assert (1, 1, "(a) Long-term borrowings", "Amount") in draft.rescue_requests


def test_lost_figure_evidence_never_touches_a_cell_that_holds_a_figure():
    """The flag applies only to a cell that is actually EMPTY: naming a
    populated cell must neither withhold nor alter a clean figure."""
    table = _lost_table()
    draft = draft_table(table, 1, lost_figure_cells={(2, 1)})
    _, findings, _ = resolve_recoveries(draft)
    assert findings == []
    assert table.rows[2][1] == "1,000.00"
