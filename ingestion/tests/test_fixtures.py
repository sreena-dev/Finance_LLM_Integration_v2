"""Chunk building, checked against real docling output rather than an imagined shape.

The bug these exist for: the narrative filters were tested against a hand-written
fixture that happened to contain a `## Notes to the Standalone Financial
Statements` heading. Real documents do not have one on the policy page, so the
filter eliminated every candidate on every real upload while the test passed. A
fixture written from imagination confirms the shape you imagined.

See `fixtures/README.md` for provenance and `fixtures/regenerate.py` to refresh.
"""

from __future__ import annotations

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from app.emit import build_text_records  # noqa: E402
from app.identify import identify  # noqa: E402

FIXTURES = os.path.join(_HERE, "fixtures")


def load(name: str) -> str:
    path = os.path.join(FIXTURES, name)
    if not os.path.exists(path):
        pytest.skip(f"fixture {name} is not present")
    with open(path, encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture
def od_policy_page() -> str:
    return load("od_2021_22_policy_page.md")


def test_real_output_yields_heading_chunks(od_policy_page):
    """Headings are the anchors every precise narrative lookup starts from."""
    records = build_text_records(
        "up_od", {10: od_policy_page},
        flavour="standalone", entity_name="Startup Odisha", source_file="od.pdf")
    headings = [r for r in records if r.chunk_type == "heading"]
    assert headings, "no heading chunks: every heading-anchored lookup would find nothing"
    assert any(h.content.startswith("2.11 TAXATION") for h in headings)


def test_the_page_header_does_not_become_a_section(od_policy_page):
    """docling exports the running entity header as `## Startup Odisha`. Treated
    as a heading it overwrites the real section on every chunk of the page."""
    records = build_text_records(
        "up_od", {10: od_policy_page},
        flavour="standalone", entity_name="Startup Odisha", source_file="od.pdf")
    assert all(r.content.strip() != "Startup Odisha"
               for r in records if r.chunk_type == "heading")


def test_policy_numbering_survives_into_the_heading(od_policy_page):
    """`AccountingPolicyTools` separates a policy sub-note from a NOTE n schedule
    with `^[0-9]+\\.[0-9]+\\.?\\s` against heading content. Strip the numbering
    anywhere in the pipeline and policy lookup stops working."""
    import re
    pattern = re.compile(r"^[0-9]+\.[0-9]+\.?\s")
    records = build_text_records(
        "up_od", {10: od_policy_page},
        flavour="standalone", entity_name="Startup Odisha", source_file="od.pdf")
    numbered = [r for r in records
                if r.chunk_type == "heading" and pattern.match(r.content)]
    assert numbered, "no N.M-numbered headings survived; policy lookup would be dead"


def test_this_real_page_has_no_notes_breadcrumb(od_policy_page):
    """The fact that broke the original filter, pinned as a test.

    If a future ingestion change starts producing a "notes to" root on this page,
    that is fine — but the *filter* must not go back to depending on it, because
    other real pages still will not have one.
    """
    records = build_text_records(
        "up_od", {10: od_policy_page},
        flavour="standalone", entity_name="Startup Odisha", source_file="od.pdf")
    crumbs = {" > ".join(r.section_breadcrumb).lower() for r in records}
    assert not any("notes to" in c for c in crumbs), (
        "this fixture is the no-breadcrumb case; if it now has one, add a fixture "
        "that does not, or the regression it guards is no longer covered"
    )


def test_page_numbers_are_populated(od_policy_page):
    """`_fetch_following_chunks` orders on `(page_ocr_start, chunk_id)`. An empty
    page number breaks passage assembly rather than degrading it."""
    records = build_text_records(
        "up_od", {10: od_policy_page},
        flavour="standalone", entity_name="Startup Odisha", source_file="od.pdf")
    assert all(r.page_ocr_start == 10 for r in records)


def test_chunk_ids_sort_in_document_order(od_policy_page):
    records = build_text_records(
        "up_od", {10: od_policy_page},
        flavour="standalone", entity_name="Startup Odisha", source_file="od.pdf")
    ids = [r.chunk_id for r in records]
    assert ids == sorted(ids)


def test_entity_is_read_from_the_real_header(od_policy_page):
    """A section 8 company with no legal suffix, from the real page."""
    assert identify([od_policy_page]).entity_name == "Startup Odisha"
