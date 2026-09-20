"""Tests for the scoreboard itself.

The harness decides whether every future pipeline change was an improvement, so
a defect in it is worse than a defect in the thing it measures -- it would send
the work in the wrong direction while looking authoritative. These run in
milliseconds and need none of the heavy dependencies.

The WITHHELD-vs-WRONG case below is not hypothetical: the first version of
``locate`` scored a correctly-withheld figure as WRONG, because with no column
named it skipped the withheld current-year cell and compared the expectation
against the prior-year comparative beside it.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.accuracy.harness import (                     # noqa: E402
    Expectation,
    Outcome,
    propose,
    score,
)


TWO_COLUMN = {"tables": [{
    "page_ocr_start": 1,
    "table_md": """| Particulars | Note | 31st March, 2023 | 31st March, 2022 |
| --- | --- | --- | --- |
| I. Revenue from operations | 12 | 15,98,04,608.63 | |
| Finance costs | 15 | 6,276.63 | 460.00 |
| Total expenses | | [unreadable: page 1, table t1, row "Total expenses", col "2023"] | 25,460.00 |
""",
}]}


def _one(expectation: Expectation):
    card = score(TWO_COLUMN, [expectation])
    assert card.scored == 1
    return card.results[0]


def test_an_enumerator_on_the_extracted_label_still_matches():
    """The statement prints "I. Revenue from operations"; a human writing the
    expectation writes "Revenue from operations"."""
    r = _one(Expectation("Revenue from operations", 159804608.63, page=1, verified=True))
    assert r.outcome is Outcome.CORRECT


def test_a_withheld_figure_is_WITHHELD_and_never_WRONG():
    """The safety property of the scoreboard.

    WRONG is the outcome that gates every change, so it has to mean a genuine
    contradiction in the column being asked about. A figure the pipeline
    correctly declined to vouch for is a disclosed miss -- scoring it as the
    most severe outcome would penalise the system for behaving correctly and
    would push later work in exactly the wrong direction.
    """
    r = _one(Expectation("Total expenses", 156475608.63, page=1, verified=True))
    assert r.outcome is Outcome.WITHHELD


def test_a_genuine_contradiction_is_WRONG():
    r = _one(Expectation("Finance costs", 999.99, page=1, verified=True))
    assert r.outcome is Outcome.WRONG
    assert r.found == pytest.approx(6276.63)


def test_an_absent_row_is_MISSING_not_WITHHELD():
    """MISSING is silent loss and WITHHELD is disclosed loss. Every worst bug in
    this corpus produces the former, so they must not collapse together."""
    r = _one(Expectation("Depreciation", 863215.95, page=1, verified=True))
    assert r.outcome is Outcome.MISSING


def test_a_named_prior_year_column_is_scored_against_that_column():
    r = _one(Expectation("Finance costs", 460.00, page=1,
                         column_label="31st March, 2022", verified=True))
    assert r.outcome is Outcome.CORRECT


def test_an_unqualified_expectation_is_matched_leniently_across_columns():
    """An expectation that names no column is scored generously: a hit in any
    value column counts. That is deliberate -- it absorbs a column order the
    expectation did not anticipate, which is a harness artefact rather than an
    extraction defect -- and it is exactly why expectation files should name
    their column. `propose` always emits one for that reason.
    """
    r = _one(Expectation("Finance costs", 460.00, page=1, verified=True))
    assert r.outcome is Outcome.CORRECT


def test_a_named_column_catches_a_column_shift():
    """The reason lenient matching is confined to unqualified expectations.

    Here the extraction has shifted the years: the prior-year figure sits under
    the current-year heading. The expected current-year value still exists in
    the table -- one column to the right -- so a scorer that searched every
    column would find it and call this correct, hiding the very defect the
    harness exists to catch.
    """
    shifted = {"tables": [{
        "page_ocr_start": 1,
        "table_md": """| Particulars | 31st March, 2023 | 31st March, 2022 |
| --- | --- | --- |
| Finance costs | 460.00 | 6,276.63 |
""",
    }]}
    r = score(shifted, [Expectation("Finance costs", 6276.63, page=1,
                                    column_label="31st March, 2023",
                                    verified=True)]).results[0]
    assert r.outcome is Outcome.WRONG
    assert r.found == pytest.approx(460.00)


def test_unverified_expectations_are_not_scored_at_all():
    """A proposal records what the system DID, not what the page SAYS. Scoring
    one would certify today's bugs as correct and lock them in."""
    card = score(TWO_COLUMN, [
        Expectation("Finance costs", 6276.63, page=1, verified=True),
        Expectation("Finance costs", 123456.0, page=1, verified=False),
    ])
    assert card.scored == 1
    assert card.as_dict()["CORRECT"] == 1


def test_proposals_are_always_unverified():
    candidates = propose(TWO_COLUMN)
    assert candidates
    assert all(c["verified"] is False for c in candidates)


def test_a_withheld_cell_is_never_proposed_as_ground_truth():
    """Proposing a marker as an expected value would be circular.

    The row itself may still be proposed from a column that DID read -- that is
    a real figure and a fair candidate. What must never happen is the withheld
    cell being offered as the truth for the column it was withheld in.
    """
    candidates = propose(TWO_COLUMN)
    assert all("unreadable" not in str(c["value"]) for c in candidates)
    withheld_column = [
        c for c in candidates
        if c["row_label"] == "Total expenses" and "2023" in (c["column_label"] or "")
    ]
    assert not withheld_column


RECOVERED_TABLE = {"tables": [{
    "page_ocr_start": 1,
    "table_md": """| Particulars | Note | 31st March, 2023 | 31st March, 2022 |
| --- | --- | --- | --- |
| I. Revenue from operations | 12 | 15,98,04,608.63 | |
| Finance costs | 15 | [recovered 6,276.63; second read, confidence medium, column total does not confirm it: page 1, table t1, row "Finance costs", col "2023"] | 460.00 |
""",
}]}


def test_a_recovered_cell_is_never_proposed_as_ground_truth():
    """Same circularity as a withheld cell: a human ticking `verified: true`
    on a proposal built from a `[recovered ...]` marker would enshrine an
    unconfirmed second-read guess as the expected figure."""
    candidates = propose(RECOVERED_TABLE)
    recovered_column = [
        c for c in candidates
        if c["row_label"] == "Finance costs" and "2023" in (c["column_label"] or "")
    ]
    assert not recovered_column


def test_a_recovered_cell_that_matches_scores_recovered_not_correct():
    expectation = Expectation(row_label="Finance costs", value=6276.63, column_label="2023", verified=True)
    card = score(RECOVERED_TABLE, [expectation])
    assert card.results[0].outcome is Outcome.RECOVERED
    assert card.results[0].found == pytest.approx(6276.63)
    assert not card.recovered_mismatched()


def test_a_recovered_cell_that_does_not_match_is_flagged_by_recovered_mismatched():
    """THE acceptance measurement: a recovered figure shown at some
    confidence band that turns out to be wrong must be distinguishable from
    one that turns out to be right -- not merely counted together as
    "recovered"."""
    expectation = Expectation(row_label="Finance costs", value=9999.99, column_label="2023", verified=True)
    card = score(RECOVERED_TABLE, [expectation])
    assert card.results[0].outcome is Outcome.RECOVERED
    assert card.recovered_mismatched() == card.results


def test_a_short_label_does_not_capture_a_row_that_merely_mentions_it():
    """Containment must mean "is nearly all of", not "appears anywhere in".

    Found by running the harness against a real filing: the expectation
    "Balance being excess of Expenditure over Income (B-A)" matched the blank
    section-header row "Income" at 0.95, because "income" is a substring of it.
    The harness then read that header's empty cells and reported MISSING for a
    figure that was extracted. In the other direction the same hole lets a short
    label like "Total" capture any row mentioning it and report a false WRONG --
    the outcome that gates every change in this project.
    """
    from tests.accuracy.harness import _label_score

    assert _label_score("Balance being excess of Expenditure over Income (B-A)", "Income") < 0.70
    assert _label_score("Total (A)", "Total expenses before extraordinary items") < 0.70
    # ...while the case containment exists for still works.
    assert _label_score("Revenue from operations", "I. Revenue from operations") >= 0.95
    assert _label_score("Fixed Assets", "Fixed Assets") == 1.0


def test_every_proposal_names_its_column():
    """Unqualified expectations are scored leniently, so anything written to a
    real expectation file should carry a column."""
    assert all(c["column_label"] for c in propose(TWO_COLUMN))


def test_a_page_number_is_a_hint_not_a_constraint():
    """A table split across a page break is emitted once, against the page it
    started on, so an expectation naming the continuation page must still find
    it rather than scoring a MISSING that is really a numbering artefact."""
    r = _one(Expectation("Finance costs", 6276.63, page=99, verified=True))
    assert r.outcome is Outcome.CORRECT
