"""Recovery: a cell nothing could vouch for may still be shown, or even
promoted to a plain number, when a second reader's candidate is tried in the
column's own footing arithmetic. This is the core anti-hallucination
guarantee of the feature: **only arithmetic promotes a figure to something a
computation may use.** Everything else is display-only, visible to a human
inside a `[recovered ...]` marker, refused by every parser in the stack.

Run with::

    cd ingestion
    venv/Scripts/python -m pytest tests/test_recovery.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import derive_confidence  # noqa: E402
from app.numbers import parse_cell  # noqa: E402
from app.tables import parse_markdown_tables  # noqa: E402
from app.verify import apply_recoveries, draft_table, redact, resolve_recoveries, verify_table  # noqa: E402


def _disagreeing_table(investment_properties: str, surplus: str):
    """Share Capital + Investment Properties + Surplus = Total, 19,74,540.

    `investment_properties`/`surplus` are what DOCLING read for those two
    cells -- deliberately wrong in the tests below, so the baseline footing
    (no candidate) does NOT close, and only a correcting candidate can.
    """
    md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Share Capital | 15,00,000 |\n"
        f"| Investment Properties | {investment_properties} |\n"
        f"| Surplus | {surplus} |\n"
        "| Total | 19,74,540 |\n"
    )
    return parse_markdown_tables(md, 1)[0]


def _row_of(table, label: str) -> int:
    return next(r for r in range(len(table.rows)) if table.label(r) == label)


# ---------------------------------------------------------------------------
# Promotion: the sole unknown in a footing that then closes
# ---------------------------------------------------------------------------

def test_a_promoted_readers_disagree_cell_closes_the_footing_and_rewrites_the_cell():
    """Docling read 3,00,000 for Investment Properties; the VLM disagreed with
    5,00,000. Docling's own value does not foot; the VLM's candidate does --
    the arithmetic, not either reader, proves the VLM was right. The cell is
    PROMOTED: no finding, no marker, and the table's own text is rewritten to
    the value the arithmetic determined (apply_recoveries)."""
    table = _disagreeing_table(investment_properties="3,00,000", surplus="(25,460)")
    ip_row = _row_of(table, "Investment Properties")

    checks, findings = verify_table(
        table, 1,
        vlm_disagreements={(ip_row, 1)},
        vlm_cell_text={(ip_row, 1): "5,00,000"},
    )

    assert findings == [], [f.marker for f in findings]
    assert any(c.used_recovered for c in checks), [c.as_dict() for c in checks]
    assert table.rows[ip_row][1].strip() == "5,00,000"
    assert "[recovered" not in table.to_markdown()
    assert "3,00,000" not in table.to_markdown()


def test_marking_the_row_vlm_only_makes_the_same_promotion_structurally_impossible():
    """THE regression the OCR number-binding redesign exists to fix.

    `vlm_only_rows` alone -- no separate disagreement candidate -- is
    exactly how a wholesale VLM structure replacement used to mark EVERY
    row of a replaced table (`pipeline.py`'s old, now-deleted
    ``vlm_only = set(range(len(table.rows)))`` line): there is no docling
    counterpart left to disagree with, the table's own text already IS the
    VLM's read. Phase C's only available candidate for a `vlm_only_row`
    cell is then its OWN already-parsed text (`resolve_recoveries` builds
    `pc.raw` verbatim as the candidate) -- trying a cell's existing,
    non-footing value against the footing arithmetic a second time can
    never make it foot when it did not the first time. Promotion is
    therefore structurally impossible, and the cell is permanently capped
    display-only. This is exactly why the new OCR-number-binding path
    (`pipeline.py`'s Pass 2b, `vlm_read.select_structure`) does NOT mark a
    bound table's rows `vlm_only` -- doing so would silently cap every one
    of its cells at `[recovered ...; confidence low]` forever, the ceiling
    measured on a real document before this redesign."""
    table = _disagreeing_table(investment_properties="3,00,000", surplus="(25,460)")
    ip_row = _row_of(table, "Investment Properties")

    checks, findings = verify_table(table, 1, vlm_only_rows={ip_row})

    assert not any(c.used_recovered for c in checks)
    matching = [f for f in findings if f.row_label == "Investment Properties"]
    assert matching, "the cell should still be visible, just never promoted"
    assert matching[0].confidence != "high"
    assert table.rows[ip_row][1].strip() == "3,00,000"


def test_a_promoted_vlm_only_row_needs_no_call_and_needs_no_rewrite():
    """A vlm_only_row's text is already the VLM's own transcription --
    apply_recoveries must be a no-op on it (it is already correct), unlike
    the readers_disagree case above which genuinely needs its cell rewritten."""
    table = _disagreeing_table(investment_properties="ignored", surplus="(25,460)")
    # Simulate insert_unclaimed_rows having spliced in the VLM's own text --
    # here that text already parses cleanly, so the row participates in
    # Phase A's baseline footing directly (see test_golden.py's equivalent
    # scenario for why this does NOT exercise the NEW overlay pathway).
    ip_row = _row_of(table, "Investment Properties")
    table.rows[ip_row][1] = "5,00,000"

    _, findings = verify_table(table, 1, vlm_only_rows={ip_row})
    assert findings == []


# ---------------------------------------------------------------------------
# Display-only: a candidate that does not (or cannot) close the footing
# ---------------------------------------------------------------------------

def test_a_recovered_value_that_does_not_foot_stays_display_only():
    """The VLM's candidate (6,00,000) does not make the column foot either --
    neither reader is corroborated by arithmetic, so the cell stays visible
    but refused by computation: a `[recovered ...]` marker, not a promotion."""
    table = _disagreeing_table(investment_properties="3,00,000", surplus="(25,460)")
    ip_row = _row_of(table, "Investment Properties")

    _, findings = verify_table(
        table, 1,
        vlm_disagreements={(ip_row, 1)},
        vlm_cell_text={(ip_row, 1): "6,00,000"},
    )

    assert len(findings) == 1
    finding = findings[0]
    assert finding.recovered_text == "6,00,000"
    assert finding.recovered_value == 600000.0
    assert finding.marker.startswith("[recovered")
    assert finding.recovery_origin == "readers_disagree"

    redact(table, findings)
    assert table.rows[ip_row][1].startswith("[recovered")
    assert "6,00,000" in table.rows[ip_row][1]  # shown to a human
    # But never as a bare, computable number:
    assert parse_cell(table.rows[ip_row][1]).value is None


def test_two_recovered_values_in_one_footing_promote_neither():
    """Two candidates that TOGETHER make the column foot are NOT individually
    determined by that one equation -- one equation, two unknowns. Both must
    stay display-only, tagged with the more specific two-unknowns caveat.
    This is the hallucination path the feature must keep shut."""
    table = _disagreeing_table(investment_properties="3,00,000", surplus="(10,460)")
    ip_row = _row_of(table, "Investment Properties")
    surplus_row = _row_of(table, "Surplus")

    _, findings = verify_table(
        table, 1,
        vlm_disagreements={(ip_row, 1), (surplus_row, 1)},
        vlm_cell_text={(ip_row, 1): "5,00,000", (surplus_row, 1): "-25,460"},
    )

    assert len(findings) == 2, [f.marker for f in findings]
    origins = {f.row_label: f.recovery_origin for f in findings}
    assert origins["Investment Properties"] == "two_recovered_in_one_footing"
    assert origins["Surplus"] == "two_recovered_in_one_footing"
    # Neither cell's text was rewritten -- nothing was promoted.
    assert table.rows[ip_row][1].strip() == "3,00,000"
    assert table.rows[surplus_row][1].strip() == "(10,460)"


# ---------------------------------------------------------------------------
# The table is never mutated by the trial (overlay) footing pass itself
# ---------------------------------------------------------------------------

def test_the_table_is_never_mutated_by_a_non_promoted_candidate():
    """Trying a candidate in the footing arithmetic must never leave a trace
    in the table when it is not promoted -- only apply_recoveries (called
    only on the PROMOTED subset) is allowed to write anything."""
    table = _disagreeing_table(investment_properties="3,00,000", surplus="(25,460)")
    ip_row = _row_of(table, "Investment Properties")
    before = [list(row) for row in table.rows]

    draft = draft_table(table, 1, vlm_disagreements={(ip_row, 1)}, vlm_cell_text={(ip_row, 1): "6,00,000"})
    _checks, _findings, recovered = resolve_recoveries(
        draft, vlm_cell_text={(ip_row, 1): "6,00,000"},
    )

    assert recovered == []
    assert [list(row) for row in table.rows] == before


# ---------------------------------------------------------------------------
# closed_world_choice: a readers_disagree cell put to a forced choice
# between EXACTLY its two candidates (vlm_read.resolve_disagreement), rather
# than trusting the VLM's own reading directly.
# ---------------------------------------------------------------------------

def test_no_closed_world_choice_passed_keeps_the_original_behaviour():
    """The default (omitted entirely, None) must be byte-for-byte the
    pre-existing behaviour: trust vlm_cell_text directly."""
    table = _disagreeing_table(investment_properties="3,00,000", surplus="(25,460)")
    ip_row = _row_of(table, "Investment Properties")

    draft = draft_table(
        table, 1, vlm_disagreements={(ip_row, 1)}, vlm_cell_text={(ip_row, 1): "5,00,000"},
    )
    _checks, findings, recovered = resolve_recoveries(
        draft, vlm_cell_text={(ip_row, 1): "5,00,000"},
    )
    assert findings == []  # promoted -- same as the very first test in this file
    assert recovered and recovered[0].recovered_text == "5,00,000"


def test_a_resolved_closed_world_choice_becomes_the_candidate():
    """The closed-world resolver picked candidate B (the VLM's reading) --
    the SAME value vlm_cell_text already carried here, but arriving via a
    different, stronger-evidenced path (recovery_origin distinguishes it)."""
    table = _disagreeing_table(investment_properties="3,00,000", surplus="(25,460)")
    ip_row = _row_of(table, "Investment Properties")

    draft = draft_table(
        table, 1, vlm_disagreements={(ip_row, 1)}, vlm_cell_text={(ip_row, 1): "6,00,000"},
    )
    assert draft.disagreement_requests == [
        (ip_row, 1, "Investment Properties", "Amount", "3,00,000", "6,00,000")
    ]

    _checks, findings, recovered = resolve_recoveries(
        draft, vlm_cell_text={(ip_row, 1): "6,00,000"},
        closed_world_choice={(ip_row, 1): "6,00,000"},
    )
    assert len(findings) == 1
    assert findings[0].recovered_text == "6,00,000"
    assert findings[0].recovery_origin == "readers_disagree_resolved"


def test_an_uncertain_closed_world_choice_withholds_rather_than_falling_back():
    """THE property this mechanism exists for: a call WAS made and came back
    UNCERTAIN (key present, value None) -- this must NOT fall back to
    trusting vlm_cell_text directly. The cell stays withheld with no
    recovered text at all, not silently kept at the VLM's own reading."""
    table = _disagreeing_table(investment_properties="3,00,000", surplus="(25,460)")
    ip_row = _row_of(table, "Investment Properties")

    draft = draft_table(
        table, 1, vlm_disagreements={(ip_row, 1)}, vlm_cell_text={(ip_row, 1): "6,00,000"},
    )
    _checks, findings, recovered = resolve_recoveries(
        draft, vlm_cell_text={(ip_row, 1): "6,00,000"},
        closed_world_choice={(ip_row, 1): None},
    )
    assert recovered == []
    assert len(findings) == 1
    assert findings[0].recovered_text is None
    assert findings[0].marker.startswith("[unreadable:")


def test_a_closed_world_choice_can_still_be_promoted_by_arithmetic():
    """The resolved candidate is not capped at display-only just because it
    came from the closed-world path -- if it happens to close a footing, it
    is promoted exactly like any other candidate."""
    table = _disagreeing_table(investment_properties="3,00,000", surplus="(25,460)")
    ip_row = _row_of(table, "Investment Properties")

    draft = draft_table(
        table, 1, vlm_disagreements={(ip_row, 1)}, vlm_cell_text={(ip_row, 1): "5,00,000"},
    )
    _checks, findings, recovered = resolve_recoveries(
        draft, vlm_cell_text={(ip_row, 1): "5,00,000"},
        closed_world_choice={(ip_row, 1): "5,00,000"},
    )
    assert findings == []
    assert recovered and recovered[0].confidence == "high"


# ---------------------------------------------------------------------------
# Confidence is derived, and an ungrounded read can never reach "medium"
# ---------------------------------------------------------------------------

def test_an_ungrounded_display_only_recovery_is_low_confidence():
    table = _disagreeing_table(investment_properties="3,00,000", surplus="(25,460)")
    ip_row = _row_of(table, "Investment Properties")

    _, findings = verify_table(
        table, 1,
        vlm_disagreements={(ip_row, 1)},
        vlm_cell_text={(ip_row, 1): "6,00,000"},
        alignment_grounded=False,
    )
    assert findings[0].confidence == "low"
    assert "ungrounded_read" in findings[0].confidence_basis


def test_a_grounded_display_only_recovery_with_good_agreement_is_medium_confidence():
    table = _disagreeing_table(investment_properties="3,00,000", surplus="(25,460)")
    ip_row = _row_of(table, "Investment Properties")

    _, findings = verify_table(
        table, 1,
        vlm_disagreements={(ip_row, 1)},
        vlm_cell_text={(ip_row, 1): "6,00,000"},
        alignment_grounded=True,
        alignment_agreement=0.95,
    )
    assert findings[0].confidence == "medium"


def test_derive_confidence_never_reaches_medium_without_proof_of_sight():
    """Direct unit test of the rubric itself: footing_determined is the only
    path to "high"; short of that, an ungrounded, unanchored read is "low"
    no matter how clean its own parse is."""
    band, basis = derive_confidence(
        footing_determined=False, grounded=False, agreement=None,
        rescue_anchored=None, clean_parse=True,
    )
    assert band == "low"
    assert "ungrounded_read" in basis

    band, _ = derive_confidence(
        footing_determined=False, grounded=False, agreement=None,
        rescue_anchored=True, clean_parse=True,
    )
    assert band == "medium"  # a rescue's own anchor check is also proof of sight

    band, _ = derive_confidence(
        footing_determined=True, grounded=False, agreement=None,
        rescue_anchored=None, clean_parse=False,
    )
    assert band == "high"  # arithmetic alone is sufficient, nothing else required


# ---------------------------------------------------------------------------
# Rescue requests: never raised for a cell the baseline footing already fixed
# ---------------------------------------------------------------------------

def test_no_rescue_is_requested_for_a_cell_the_baseline_footing_already_resolved():
    md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Share Capital | 15,00,000 |\n"
        "| Investment Properties | 5,00,000 |\n"
        "| Surplus | (25,460) |\n"
        "| Total | 19,74,540 |\n"
    )
    table = parse_markdown_tables(md, 1)[0]
    draft = draft_table(table, 1)
    assert draft.rescue_requests == []


def test_a_rescue_request_is_raised_for_a_genuinely_unreadable_cell():
    table = _disagreeing_table(investment_properties="8Z5'E", surplus="(25,460)")
    ip_row = _row_of(table, "Investment Properties")
    draft = draft_table(table, 1)
    requested = {(r, c) for r, c, _label, _col in draft.rescue_requests}
    assert (ip_row, 1) in requested


# ---------------------------------------------------------------------------
# Sign integrity: a clipped parenthesis must never become a signed figure on
# the second reader's word alone. Measured on a real filing: an asset printed
# +12,859 came back from the vision model as "(12,859" (opening paren, no
# closer), parsed NEGATIVE, and was shown as a recovered figure.
# ---------------------------------------------------------------------------

def _sign_table(investment_properties: str, total: str = "20,25,460"):
    """Share Capital + Investment Properties + Surplus = Total.
    15,00,000 + 5,00,000 + 25,460 = 20,25,460."""
    md = (
        "| Particulars | Amount |\n"
        "| --- | --- |\n"
        "| Share Capital | 15,00,000 |\n"
        f"| Investment Properties | {investment_properties} |\n"
        "| Surplus | 25,460 |\n"
        f"| Total | {total} |\n"
    )
    return parse_markdown_tables(md, 1)[0]


def test_a_clipped_parenthesis_whose_positive_reading_foots_is_settled_positive():
    """THE regression. Only the positive reading closes the footing, so the
    arithmetic settles it: promoted as a plain +5,00,000, and the table's own
    cell rewritten so it cannot re-parse negative downstream."""
    table = _sign_table(investment_properties="1,00,000")
    row = _row_of(table, "Investment Properties")

    checks, findings = verify_table(
        table, 1,
        vlm_disagreements={(row, 1)},
        vlm_cell_text={(row, 1): "(5,00,000"},
    )

    assert findings == [], [f.marker for f in findings]
    assert any(c.used_recovered for c in checks)
    cell = parse_cell(table.rows[row][1])
    assert cell.value == 500000.0
    assert not cell.sign_uncertain
    assert "(" not in table.rows[row][1]


def test_a_clipped_parenthesis_whose_negative_reading_foots_is_settled_negative():
    """The mirror: 15,00,000 - 5,00,000 + 25,460 = 10,25,460, so here the
    NEGATIVE reading is the one the arithmetic proves -- written back balanced."""
    table = _sign_table(investment_properties="1,00,000", total="10,25,460")
    row = _row_of(table, "Investment Properties")

    _, findings = verify_table(
        table, 1,
        vlm_disagreements={(row, 1)},
        vlm_cell_text={(row, 1): "(5,00,000"},
    )

    assert findings == []
    cell = parse_cell(table.rows[row][1])
    assert cell.value == -500000.0
    assert not cell.sign_uncertain          # balanced now: a stated negative


def test_a_clipped_parenthesis_no_footing_can_settle_is_withheld_never_signed():
    """Neither sign closes the column (the total is unrelated), so the
    arithmetic cannot tell them apart. Withheld -- and crucially NO
    `[recovered ...]` figure at a guessed sign."""
    table = _sign_table(investment_properties="1,00,000", total="99,99,999")
    row = _row_of(table, "Investment Properties")

    draft = draft_table(table, 1, vlm_disagreements={(row, 1)},
                        vlm_cell_text={(row, 1): "(5,00,000"})
    _, findings, recovered = resolve_recoveries(
        draft, vlm_cell_text={(row, 1): "(5,00,000"})

    assert recovered == []
    assert [f.row_label for f in findings] == ["Investment Properties"]
    assert findings[0].recovered_text is None
    redact(table, findings)
    assert table.rows[row][1].startswith("[unreadable:")
    assert "-5" not in table.to_markdown() and "(5,00,000" not in table.to_markdown()


def test_a_clipped_parenthesis_where_both_signs_close_is_withheld():
    """A zero-adjacent case: both signs of 0 cannot be told apart. Two signs
    closing is as undecidable as none -- refusing is the only honest answer."""
    md = (
        "| Particulars | Amount |\n| --- | --- |\n"
        "| Share Capital | 15,00,000 |\n"
        "| Investment Properties | 1,00,000 |\n"
        "| Total | 15,00,000 |\n"
    )
    table = parse_markdown_tables(md, 1)[0]
    row = _row_of(table, "Investment Properties")
    draft = draft_table(table, 1, vlm_disagreements={(row, 1)},
                        vlm_cell_text={(row, 1): "(0"})
    _, findings, recovered = resolve_recoveries(draft, vlm_cell_text={(row, 1): "(0"})
    assert recovered == []
    assert findings and findings[0].recovered_text is None


def test_a_sign_uncertain_candidate_is_never_emitted_as_a_recovered_figure():
    """The exact defect, end to end: no `[recovered` marker and no negative
    figure may exist anywhere when the arithmetic cannot settle the sign."""
    table = _sign_table(investment_properties="1,00,000", total="99,99,999")
    row = _row_of(table, "Investment Properties")
    _, findings = verify_table(
        table, 1, vlm_disagreements={(row, 1)}, vlm_cell_text={(row, 1): "(12,859"})
    assert all(f.recovered_text is None for f in findings)
    assert "[recovered" not in "".join(f.marker for f in findings)


# ---- context sanity: withhold-only ------------------------------------------

def test_a_recovered_negative_total_is_withheld_not_flipped():
    md = (
        "| Particulars | Amount |\n| --- | --- |\n"
        "| Share Capital | 15,00,000 |\n"
        "| Total | 1,00 |\n"
    )
    table = parse_markdown_tables(md, 1)[0]
    row = _row_of(table, "Total")
    draft = draft_table(table, 1, vlm_disagreements={(row, 1)},
                        vlm_cell_text={(row, 1): "(15,00,000)"})
    _, findings, recovered = resolve_recoveries(draft, vlm_cell_text={(row, 1): "(15,00,000)"})
    assert recovered == []
    assert findings and findings[0].recovered_text is None
    assert "negative_total" in findings[0].reasons
    # The no-correction invariant: no positive counterpart appeared anywhere.
    assert "15,00,000" not in "".join(f.marker for f in findings)


def test_a_recovered_negative_beside_a_comparable_positive_in_the_other_year_is_withheld():
    md = (
        "| Particulars | 2025 | 2024 |\n| --- | --- | --- |\n"
        "| Capital work in progress | 1,000 | 12,000 |\n"
        "| Other assets | 5,000 | 6,000 |\n"
    )
    table = parse_markdown_tables(md, 1)[0]
    row = _row_of(table, "Capital work in progress")
    cand = {(row, 1): "(12,859)"}
    draft = draft_table(table, 1, vlm_disagreements={(row, 1)}, vlm_cell_text=cand)
    _, findings, recovered = resolve_recoveries(draft, vlm_cell_text=cand)
    assert recovered == []
    assert "sign_disagrees_across_years" in findings[0].reasons


def test_a_genuine_loss_of_a_different_magnitude_is_not_mistaken_for_a_sign_error():
    """A profit-to-loss swing usually changes magnitude, so a negative beside
    a positive of a very different size is left alone (shown, caveated)."""
    md = (
        "| Particulars | 2025 | 2024 |\n| --- | --- | --- |\n"
        "| Profit for the year | 1,000 | 900,00 |\n"
        "| Other | 5,000 | 6,000 |\n"
    )
    table = parse_markdown_tables(md, 1)[0]
    row = _row_of(table, "Profit for the year")
    cand = {(row, 1): "(4,00,00,000)"}
    draft = draft_table(table, 1, vlm_disagreements={(row, 1)}, vlm_cell_text=cand)
    _, findings, _ = resolve_recoveries(draft, vlm_cell_text=cand)
    assert findings and findings[0].recovered_text == "(4,00,00,000)"


def test_a_context_doubt_never_vetoes_a_figure_the_arithmetic_proved():
    """Footing is self-evidencing: a heuristic must not withhold a figure the
    column's own arithmetic settled."""
    table = _sign_table(investment_properties="1,00,000", total="10,25,460")
    row = _row_of(table, "Investment Properties")
    _, findings = verify_table(
        table, 1, vlm_disagreements={(row, 1)}, vlm_cell_text={(row, 1): "(5,00,000"})
    assert findings == []
    assert parse_cell(table.rows[row][1]).value == -500000.0
