"""Hermetic tests for the markdown -> PDF renderer.

Run from `backend/`:
    python -m modes.financial_diagnostic_report.test_pdf

WHAT IS WORTH PINNING HERE
---------------------------
`pdf.py` is a rendering step, not a content decision. What can go wrong here
is specific to rendering: python-markdown silently swallowing a list into a
run-on paragraph (the "no blank line before a list" quirk — see
`pdf._blank_line_before_lists`), a "[n] citation" marker getting eaten by
markdown's own link syntax, or `pisa.CreatePDF` failing outright. No
Postgres, no model — a stub markdown string is enough to exercise all three.
"""
from __future__ import annotations

import sys

from . import pdf as PDF


def test_list_with_no_blank_line_is_still_a_list() -> list[str]:
    """`report.py` writes several "Label:\\n- item" blocks with no blank line
    in between (block 6's "**Contributing signals**", block 1's "Sources:").
    Left alone, python-markdown renders that as one paragraph with literal
    dashes — exactly the "run-on paragraph" failure this function exists to
    prevent."""
    text = "Sources:\n- [1] first citation\n- [2] second citation\n"
    fixed = PDF._blank_line_before_lists(text)
    failures = []
    if "Sources:\n\n- [1]" not in fixed:
        failures.append("no blank line was inserted before the list start")
    if fixed.count("- [1]") != 1 or fixed.count("- [2]") != 1:
        failures.append("a list item was duplicated or dropped")
    return failures


def test_blank_line_is_not_duplicated_when_already_present() -> list[str]:
    """A list that already has its blank line (block 2's dashboard "###
    Sources" section) must pass through with exactly one blank line, not two."""
    text = "### Sources\n\n- one\n- two\n"
    fixed = PDF._blank_line_before_lists(text)
    return ([] if "\n\n\n" not in fixed and fixed.count("\n\n") == 1
            else ["a blank line was added where one already existed"])


def test_consecutive_list_items_stay_one_list() -> list[str]:
    """The blank-line fix must trigger once, at the top of a list, not before
    every item — inserting a blank line between items would split one list
    into several single-item ones."""
    text = "Label\n- a\n- b\n- c\n"
    fixed = PDF._blank_line_before_lists(text)
    return ([] if fixed == "Label\n\n- a\n- b\n- c\n"
            else [f"blank lines were inserted mid-list: {fixed!r}"])


def test_bracket_citation_survives_html_escaping() -> list[str]:
    """§2/§10.4's "[n] source" convention — the same one `Sources`/`Provenance`
    render on screen (`FdrAnalysis.jsx`) — must reach the HTML `pisa` renders,
    unmangled, before it ever becomes PDF bytes. The PDF's own content stream
    is typically compressed (FlateDecode), so the bracket is checked at the
    HTML stage, which is the one representation both `to_pdf`'s callers and
    this test can inspect directly."""
    text = "Sources:\n- [1] \"Segment revenue\" (DOC.pdf, page 212)\n"
    html = PDF._html(text)
    return ([] if "[1] &quot;Segment revenue&quot;" in html or
            "[1] \"Segment revenue\"" in html
            else [f"the bracket citation did not survive markdown/HTML conversion: {html!r}"])


def test_full_document_renders_without_error() -> list[str]:
    """A representative document — heading, table, blockquote with a source
    line, and both blank-line-missing list shapes — must render end to end."""
    text = (
        "# Financial Diagnostic Report — TEST_ENTITY\n\n"
        "Audit-planning intelligence.\n\n"
        "## 1. Business profile\n\n"
        "**Entity** — TEST_ENTITY\n\n"
        "> The filing's own words.\n"
        ">\n"
        "> — DOC.pdf, page 12\n\n"
        "Sources:\n- [1] \"a row\" (DOC.pdf, page 4)\n\n"
        "## 2. Executive dashboard\n\n"
        "| Figure | Value | Movement |\n|---|---|---|\n"
        "| Revenue | 1,000 | +5% |\n"
    )
    try:
        pdf_bytes = PDF.to_pdf(text)
    except Exception as exc:  # noqa: BLE001 - the test IS the failure signal
        return [f"to_pdf raised {type(exc).__name__}: {exc}"]
    return [] if pdf_bytes.startswith(b"%PDF") and len(pdf_bytes) > 500 else \
        ["to_pdf returned no usable PDF bytes"]


def main() -> int:
    suites = (
        ("list with no blank line becomes a real list",
         test_list_with_no_blank_line_is_still_a_list),
        ("an existing blank line is not duplicated",
         test_blank_line_is_not_duplicated_when_already_present),
        ("consecutive list items stay one list",
         test_consecutive_list_items_stay_one_list),
        ("bracket citation survives HTML escaping",
         test_bracket_citation_survives_html_escaping),
        ("a full document renders without error",
         test_full_document_renders_without_error),
    )
    total = 0
    for name, suite in suites:
        failures = suite()
        total += len(failures)
        if failures:
            print(f"FAIL  {name}")
            for line in failures:
                print(f"        {line}")
        else:
            print(f"ok    {name}")
    print()
    print("FAILED" if total else f"PASSED — {len(suites)} suites")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
