"""Tests for the silent-empty-filter guard.

The property under test, and the reason this module exists:

    **No lookup may return nothing without leaving a reason.**

An empty result is indistinguishable from a document that genuinely lacks the
disclosure. Prompt rule 16 stops the model calling that a disclosure failure, but
the auditor still silently loses the analysis, and nothing anywhere reports a
problem. The adversarial cases below each strip one structural field that the
ingestion service is supposed to populate, which is how the original bug arose.

Run with::

    cd backend
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 venv/Scripts/python -m pytest \
        modes/financial_statement/upload/test_sieve.py -q
"""

from __future__ import annotations

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_MODE = os.path.dirname(_HERE)
_BACKEND = os.path.dirname(os.path.dirname(_MODE))
for path in (os.path.join(_MODE, "pipeline"), _BACKEND):
    if path not in sys.path:
        sys.path.insert(0, path)

os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("REPORTS_DB_PASSWORD", "test")

from modes.financial_statement.upload import (  # noqa: E402
    coverage, diagnostics, narrative, store,
)
from modes.financial_statement.upload.sieve import (  # noqa: E402
    PRECISION, SAFETY, Stage, sieve,
)

STANDALONE = ["Standalone Financial Statements"]
NOTES = ["Standalone Financial Statements", "Notes to the Standalone Financial Statements"]
CONSOLIDATED = ["Consolidated Financial Statements"]


def chunk(cid, page, ctype, content, crumb=NOTES):
    return dict(chunk_id=cid, page_ocr_start=page, chunk_type=ctype, content=content,
                section=content[:40], title=content[:40],
                section_breadcrumb=crumb, source_file="f.pdf")


def document(texts, tables=None, doc_id="up_t", conversation="cv"):
    return store.UploadedDocument(
        doc_id=doc_id, user_id="u", conversation_id=conversation, filename="f.pdf",
        document={"fy_start": 2022, "fy_end": 2023, "company": "X Ltd"},
        identification={"entity_name": "X Ltd", "financial_year": "2022-23"},
        quality={}, tables=tables or [], texts=texts)


# --------------------------------------------------------------------------
# The sieve itself
# --------------------------------------------------------------------------

def test_a_precision_filter_relaxes_through_its_tiers():
    rows = [{"n": 1}, {"n": 2}]
    result = sieve(rows, [Stage(
        name="pick", kind=PRECISION, why="w",
        tiers=[lambda r: r["n"] > 10, lambda r: r["n"] > 1],
    )])
    assert [r["n"] for r in result.rows] == [2]
    assert result.relaxed == [("pick", 1)]
    assert "relaxed" in (result.caveat() or "")


def test_a_precision_filter_with_no_matching_tier_is_skipped():
    rows = [{"n": 1}]
    result = sieve(rows, [Stage(
        name="pick", kind=PRECISION, why="w", tiers=[lambda r: r["n"] > 10],
    )])
    assert result.rows == rows
    assert result.relaxed == [("pick", -1)]


def test_a_safety_filter_never_relaxes():
    """Relaxing this returns a wrong answer, not a vaguer one."""
    rows = [{"n": 1}]
    result = sieve(rows, [Stage(
        name="scope", kind=SAFETY, why="wrong scope", tiers=[lambda r: r["n"] > 10],
    )])
    assert result.rows == []
    assert result.blocked_by is not None
    assert "scope" in result.caveat()


def test_a_floored_precision_filter_blocks_rather_than_skipping():
    """Regression.

    Without `floor`, a stage whose every tier fails is dropped entirely — which
    hands back exactly what the filter existed to exclude. A document containing
    only `NOTE 27 PROVISIONS` failed both policy-numbering tiers and then had the
    whole stage skipped, returning the movement schedule as an accounting policy.
    """
    rows = [{"n": 1}]
    result = sieve(rows, [Stage(
        name="numbering", kind=PRECISION, why="w", floor=True,
        tiers=[lambda r: r["n"] > 10, lambda r: r["n"] > 5],
    )])
    assert result.rows == []
    assert result.blocked_by is not None


def test_an_empty_input_is_reported_as_such():
    result = sieve([], [Stage(name="x", kind=PRECISION, why="w", tiers=[lambda r: True])])
    assert result.no_candidates
    assert "no candidates" in result.caveat().lower()


# --------------------------------------------------------------------------
# The original bug, and its inverse
# --------------------------------------------------------------------------

def test_policy_lookup_survives_a_missing_notes_breadcrumb_and_says_so():
    """The bug that prompted all of this, now with a stated reason."""
    doc = document([
        chunk("c1", 10, "heading", "2.11 TAXATION:", STANDALONE),
        chunk("c2", 10, "text", "Income tax comprises current and deferred tax.", STANDALONE),
    ])
    token = diagnostics.start()
    try:
        found = narrative.find_heading_by_keywords(doc, ["taxation"])
        caveats = diagnostics.caveats()
    finally:
        diagnostics.reset(token)

    assert found is not None and found["content"] == "2.11 TAXATION:"
    assert "notes section" in caveats, "the relaxation must be reported, not silent"


def test_the_relaxation_does_not_readmit_a_movement_schedule():
    doc = document([chunk("c1", 5, "heading", "NOTE 27 PROVISIONS", STANDALONE)])
    assert narrative.find_heading_by_keywords(doc, ["provisions"]) is None


def test_a_blocked_safety_filter_explains_itself():
    """The failure mode this whole change exists to remove: nothing returned and
    nothing said. The truthiness of SieveResult reports whether rows were found,
    so a `if result` check here skipped the caveat in exactly the case that had
    one — which is how a block went silent once already."""
    doc = document([chunk("c1", 1, "heading", "Statement of Compliance", CONSOLIDATED)])
    token = diagnostics.start()
    try:
        assert narrative.find_compliance_passage(doc) is None
        caveats = diagnostics.caveats()
    finally:
        diagnostics.reset(token)
    assert "standalone scope" in caveats


# --------------------------------------------------------------------------
# The invariant, over adversarial shapes
# --------------------------------------------------------------------------

#: Each case strips one structural field the ingestion service is meant to
#: populate. These are the shapes that turn a filter into a silent eliminator.
ADVERSARIAL = {
    "no headings at all": [chunk("c1", 1, "text", "Some prose about taxation.", STANDALONE)],
    "no breadcrumbs": [
        dict(chunk("c1", 1, "heading", "2.11 TAXATION:"), section_breadcrumb=[]),
        dict(chunk("c2", 1, "text", "Income tax prose."), section_breadcrumb=[]),
    ],
    "no heading numbering": [
        chunk("c1", 1, "heading", "Taxation", STANDALONE),
        chunk("c2", 1, "text", "Income tax prose.", STANDALONE),
    ],
    "no page numbers": [
        dict(chunk("c1", 0, "heading", "2.11 TAXATION:"), page_ocr_start=None),
        dict(chunk("c2", 0, "text", "Income tax prose."), page_ocr_start=None),
    ],
    "everything consolidated": [
        chunk("c1", 1, "heading", "2.11 TAXATION:", CONSOLIDATED),
        chunk("c2", 1, "text", "Income tax prose.", CONSOLIDATED),
    ],
    "no narrative at all": [],
}


@pytest.mark.parametrize("name", sorted(ADVERSARIAL))
def test_no_lookup_returns_empty_without_a_reason(name):
    """The property. Every lookup either finds something or says why it did not."""
    doc = document(ADVERSARIAL[name], conversation=f"cv-{name}")
    anchor = chunk("anchor", 1, "heading", "2.11 TAXATION:", STANDALONE)

    lookups = {
        "policy by keyword": lambda: narrative.find_heading_by_keywords(doc, ["taxation"]),
        "compliance passage": lambda: narrative.find_compliance_passage(doc),
        "following chunks": lambda: narrative.fetch_following_chunks(doc, anchor),
    }

    for label, call in lookups.items():
        token = diagnostics.start()
        try:
            result = call()
            reason = diagnostics.caveats()
        finally:
            diagnostics.reset(token)

        empty = result is None or (isinstance(result, list) and not result)
        if empty:
            assert reason, (
                f"{label} returned nothing for the '{name}' document and left no "
                "reason — that is indistinguishable from the filing not containing it"
            )


# --------------------------------------------------------------------------
# Coverage
# --------------------------------------------------------------------------

def test_coverage_names_the_paths_that_cannot_work():
    doc = document(
        [chunk("c1", 1, "text", "Prose with no headings and no auditor voice.", STANDALONE)],
        tables=[dict(table_id="t1", table_title=None, table_description=None,
                     toc_section=None, page_ocr_start=1)],
    )
    report = coverage.assess(doc)
    unavailable = {p.name for p in report.unavailable}
    assert "accounting-policy lookup" in unavailable
    assert "auditor's report / CARO review" in unavailable
    assert "schedule notes / account-area review" in unavailable

    text = report.report()
    assert "NOT AVAILABLE" in text
    # The point of the section: a miss on these paths carries no information.
    assert "says nothing about whether the filing contains" in text


def test_coverage_is_quiet_when_everything_works():
    doc = document(
        [
            chunk("c1", 2, "heading", "Statement of Compliance", NOTES),
            chunk("c2", 2, "text", "Prepared in accordance with accounting standards.", NOTES),
            chunk("c3", 3, "heading", "2.11 TAXATION:", NOTES),
            chunk("c4", 9, "text", "In our opinion the company has not defaulted.", NOTES),
        ],
        tables=[dict(table_id="t1", table_title="Note 1 PPE",
                     table_description="Gross block", toc_section="Note 1 PPE",
                     page_ocr_start=5)],
    )
    report = coverage.assess(doc)
    assert report.unavailable == []
    assert "NOT AVAILABLE" not in report.report()


def test_coverage_tells_the_user_to_upload_the_auditors_report():
    """The workaround has to be actionable. In this corpus the auditor's report is
    a separate file, so CARO cannot be reviewed from the statements alone."""
    doc = document([chunk("c1", 1, "text", "Only statements prose here.", STANDALONE)])
    caro = [p for p in coverage.assess(doc).paths
            if p.name == "auditor's report / CARO review"][0]
    assert caro.state == coverage.UNAVAILABLE
    assert "IARSFS" in (caro.workaround or "")


def test_the_coverage_section_reaches_the_tool_report():
    """Regression.

    `coverage.assess` is only useful if its output actually lands in the string
    `get_extraction_quality_report` returns. An earlier patch wired the UI
    payload but silently failed to wire the report, so the model never saw it —
    which is the same class of silent gap this module exists to close.
    """
    from modes.financial_statement.upload import quality

    doc = document(
        [chunk("c1", 1, "text", "Prose only, no headings, no auditor voice.", STANDALONE)],
        tables=[dict(table_id="t1", table_title=None, table_description=None,
                     toc_section=None, page_ocr_start=1)],
        conversation="cv-report",
    )
    doc.quality = {
        "grade": "fair", "low_grade": "poor", "vlm_used": False,
        "notes": [], "unreadable_cells": [], "failed_footings": [], "pages": [],
    }
    report = quality.quality_report(doc)
    assert "WHAT CAN BE ASKED OF THIS DOCUMENT" in report
    assert "NOT AVAILABLE" in report
    assert "accounting-policy lookup" in report

    payload = quality.as_payload(doc)
    assert "accounting-policy lookup" in payload["coverage"]["unavailable"]
