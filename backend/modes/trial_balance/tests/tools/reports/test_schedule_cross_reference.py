"""The cross-referencing invariant: every Excel sheet must be named in the Word document.

This is asserted rather than left as a convention because the failure is silent and
the two files are written by different tools. A reader is either told "the full
population is in the workbook" with no sheet named, or the document cites a sheet that
was never written because its backing artifact was absent. Neither raises anything.

The mechanism under test:

    build_excel_report  --writes-->  excel_schedule_manifest.json  --read-by-->  build_docx_report

Sheets are conditional on their source artifact existing, so the manifest records what
was ACTUALLY created and the Word writer renders references from that. The two tests
that matter are therefore symmetric: no sheet unreferenced, and no reference dangling.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from docx import Document
from openpyxl import load_workbook

from modes.trial_balance.router import _run_core_analytics_chain
from modes.trial_balance.pipeline.tools import (
    MANIFEST_FILENAME,
    SCHEDULES,
    read_manifest,
    reference_line,
    validate_registry,
)
from modes.trial_balance.pipeline.tools import build_docx_report
from modes.trial_balance.pipeline.tools import build_excel_report


@pytest.fixture(scope="module")
def reported_run(tmp_path_factory):
    """One full analytics chain plus both report writers, reused across the module.

    Module-scoped deliberately: the chain runs ~25 tools and both writers, which is
    slow enough that per-test setup would discourage adding cases here.
    """
    from modes.trial_balance.tests.conftest_phase2 import SCREEN_ROWS, write_canonical

    d = tmp_path_factory.mktemp("reported_run")
    tb = write_canonical(d / "canonical_tb.parquet", SCREEN_ROWS)
    (d / "layer1_results.json").write_text(json.dumps([
        {"rule": "TB-000", "rule_name": "Sign", "status": "PASS", "message": "positive = debit."},
        {"rule": "TB-026", "rule_name": "Scale", "status": "WARNING", "message": "Scale not declared."},
    ]), encoding="utf-8")

    with patch("modes.trial_balance.router.get_agent") as m:
        m.return_value.llm_client = None
        _run_core_analytics_chain(str(tb), str(d))

    excel = build_excel_report(canonical_tb_file=str(tb), output_dir=str(d))
    docx = build_docx_report(canonical_tb_file=str(tb), output_dir=str(d))
    return d, excel, docx


def _docx_text(path) -> str:
    """All text in the document -- paragraphs and table cells alike. A reference
    rendered into a table cell counts; reading paragraphs only would miss the
    schedule appendix entirely, which is a table."""
    doc = Document(str(path))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.extend(c.text for c in row.cells)
    return "\n".join(parts)


class TestRegistry:
    def test_registry_is_valid_for_excel(self):
        """Sheet names over 31 chars, or containing : \\ / ? * [ ], make openpyxl raise
        part-way through writing rather than truncating."""
        assert validate_registry() == []

    def test_every_registry_entry_declares_its_section_and_artifact(self):
        for name, meta in SCHEDULES.items():
            assert isinstance(meta.get("section"), int), f"{name} has no section"
            assert meta.get("artifact"), f"{name} names no backing artifact"
            assert meta.get("contains"), f"{name} has no description for the Word index"


class TestManifest:
    def test_excel_writes_a_manifest(self, reported_run):
        d, excel, _ = reported_run
        assert excel["execution_status"] == "SUCCESS", excel.get("message")
        assert (d / MANIFEST_FILENAME).exists()

    def test_manifest_matches_the_workbook_exactly(self, reported_run):
        """The manifest is the Word writer's only view of the workbook. If it drifts
        from the actual sheets, every downstream guarantee here is worthless."""
        d, _, _ = reported_run
        manifest = read_manifest(d)
        wb = load_workbook(d / "TB_Audit.xlsx", read_only=True)
        assert set(wb.sheetnames) == {s["name"] for s in manifest["sheets"]}, (
            f"workbook has {sorted(wb.sheetnames)}, manifest has "
            f"{sorted(s['name'] for s in manifest['sheets'])}"
        )
        wb.close()

    def test_no_unregistered_sheets(self, reported_run):
        d, _, _ = reported_run
        assert read_manifest(d)["unregistered_sheets"] == []


class TestCrossReferenceInvariant:
    """The requirement: every Excel sheet referenced in the Word document, without fail."""

    def test_every_sheet_is_named_in_the_word_document(self, reported_run):
        d, _, docx = reported_run
        assert docx["execution_status"] == "SUCCESS", docx.get("message")
        text = _docx_text(d / "TB_Audit_Report.docx")

        wb = load_workbook(d / "TB_Audit.xlsx", read_only=True)
        sheets = list(wb.sheetnames)
        wb.close()

        missing = [name for name in sheets if name not in text]
        assert not missing, (
            f"{len(missing)} sheet(s) exist in the workbook but are never named in the Word "
            f"document: {missing}. Every schedule must be referenced -- add the sheet to "
            f"SCHEDULES with the section it supports, and the appendix will pick it up."
        )

    def test_no_reference_points_at_a_sheet_that_does_not_exist(self, reported_run):
        """The other half of the invariant. Citing an absent schedule sends a reader
        looking for something that was never written."""
        d, _, _ = reported_run
        text = _docx_text(d / "TB_Audit_Report.docx")
        wb = load_workbook(d / "TB_Audit.xlsx", read_only=True)
        actual = set(wb.sheetnames)
        wb.close()

        dangling = [
            name for name in SCHEDULES
            if name not in actual and f"'{name}'" in text
        ]
        assert not dangling, f"Word cites sheet(s) the workbook does not contain: {dangling}"

    def test_schedule_appendix_lists_every_sheet(self, reported_run):
        """Sections cite the schedules relevant to them; the appendix is the exhaustive
        index, so a sheet supporting no numbered section is still findable."""
        d, _, _ = reported_run
        text = _docx_text(d / "TB_Audit_Report.docx")
        assert "25. Detailed Appendices and Full TB Schedules" in text
        manifest = read_manifest(d)
        assert f"contains {len(manifest['sheets'])} schedule(s)" in text
        for s in manifest["sheets"]:
            assert s["contains"] in text, f"appendix omits the description for {s['name']}"

    def test_capped_sections_point_at_their_full_population(self, reported_run):
        """Sections 17 and 19 are capped by decision -- a real run produced 33 findings
        and 119 evidence requests. A cap without a pointer strands the reader."""
        d, _, _ = reported_run
        text = _docx_text(d / "TB_Audit_Report.docx")
        manifest = read_manifest(d)
        for section, sheet in ((17, "Findings Register"), (19, "Evidence Requests")):
            if any(s["name"] == sheet for s in manifest["sheets"]):
                assert sheet in text, f"section {section} does not name its {sheet} schedule"

    def test_reference_line_is_empty_when_no_sheet_backs_a_section(self):
        """Section 21 has no schedule and never will. It must read as complete in
        itself rather than as though a reference went missing."""
        manifest = {"workbook": "TB_Audit.xlsx", "sheets": [
            {"name": "Findings Register", "section": 17, "contains": "x", "rows": 5},
        ]}
        assert reference_line(manifest, 21) == ""
        assert "Findings Register" in reference_line(manifest, 17)

    def test_reference_survives_a_missing_manifest(self, tmp_path):
        """Word must still render when the workbook was not produced -- degraded, but
        never crashing and never citing a schedule."""
        manifest = read_manifest(tmp_path)
        assert manifest["sheets"] == []
        assert reference_line(manifest, 17) == ""


class TestPhase4Sections:
    @pytest.mark.parametrize("heading", [
        "12. Sensitive and Public-Sector Accounts",
        "13. Estimates and Judgemental Balances",
        "14. Journal and Fraud-Risk Indicators",
        "15. Disclosure-Risk Indicators",
        "16. Going-Concern Indicators",
        "17. Consolidated Audit Findings",
        "18. Recommended Audit Procedures",
        "19. Evidence Request List",
        "20. Management and Statutory-Auditor Questions",
        "21. Prior-Issue Follow-Up",
        "22. Suggested Audit Plan and Prioritisation",
        "23. Data Lineage and Methodology",
        "25. Detailed Appendices and Full TB Schedules",
    ])
    def test_section_present(self, reported_run, heading):
        d, _, _ = reported_run
        assert heading in _docx_text(d / "TB_Audit_Report.docx")

    def test_unavailable_sections_say_why_rather_than_vanishing(self, reported_run):
        """A silently absent section reads as 'nothing to report', which is the
        opposite of the truth."""
        d, _, _ = reported_run
        text = _docx_text(d / "TB_Audit_Report.docx")
        assert "No prior audit report" in text, "section 21 must state why it is not performed"
        assert "Journal-level analysis is not performed" in text
        assert "full disclosure checklist is not performed" in text.lower() or \
               "A full disclosure checklist is not performed" in text

    def test_pii_is_masked_in_the_rendered_document(self, reported_run):
        """sec 16.2 -- masking happens at the render boundary; the artifacts keep the
        real values."""
        d, _, _ = reported_run
        text = _docx_text(d / "TB_Audit_Report.docx")
        from modes.trial_balance.pipeline.tools import find_identifiers
        found = find_identifiers(text)
        assert not found, f"unmasked identifiers reached the document: {found}"
