"""Tests for the narrative half of the upload bridge.

Run with::

    cd backend
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 venv/Scripts/python -m pytest \
        modes/financial_statement/upload/test_narrative.py -q

Like ``test_bridge.py`` these import the real ``tools_fs`` and pass a connection
that raises if anything touches it, so a test fails on the *attempt* to query
Postgres rather than on the shape of a result.

The fixtures are two files -- a statements file and an auditor's report -- because
that is how this corpus arrives (measured: the SFS and IARSFS share no pages) and
because the checks that matter most read across both.
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

tools_fs = pytest.importorskip("tools_fs")

from modes.financial_statement.upload import bridge, narrative, store  # noqa: E402

SFS_FILE = "ITSL_2022-23_SFS.pdf"
IAR_FILE = "ITSL_2022-23_IARSFS.pdf"
CRUMB = ["Standalone Financial Statements", "Notes to the Standalone Financial Statements"]
CONSOLIDATED_CRUMB = ["Consolidated Financial Statements", "Notes to the Consolidated Financial Statements"]


class ExplodingConnection:
    """Fails loudly if any code path tries to reach Postgres."""

    def cursor(self, *args, **kwargs):
        raise AssertionError("SQL was attempted for an uploaded document")

    def rollback(self):
        return None


def chunk(cid, page, ctype, content, section, src=SFS_FILE, crumb=None):
    return dict(chunk_id=cid, page_ocr_start=page, chunk_type=ctype, content=content,
                section=section, title=section,
                section_breadcrumb=crumb if crumb is not None else CRUMB,
                source_file=src)


@pytest.fixture
def installed(monkeypatch):
    """Replace all narrative readers with recorders, then install the bridge."""
    calls: list[str] = []

    def recorder(name, result):
        def _fn(*args, **kwargs):
            calls.append(name)
            return result
        return _fn

    specs = [
        (tools_fs.DocumentResolver, "_fetch_following_chunks", "ffc", []),
        (tools_fs.DocumentResolver, "_find_compliance_passage", "fcp", "CORPUS"),
        (tools_fs.DisclosureSearchTools, "_find_heading_anchor", "fha", None),
        (tools_fs.DisclosureSearchTools, "search_company_disclosures", "scd", "CORPUS"),
        (tools_fs.AccountingPolicyTools, "_find_heading_by_keywords", "fhk", None),
        (tools_fs.AccountingPolicyTools, "_find_heading_by_embedding", "fhe", None),
        (tools_fs.ReportReferenceTools, "lookup_report_reference", "lrr", "CORPUS"),
        (tools_fs.ExecutiveSummaryTools, "_narrative", "nar", "CORPUS NARRATIVE"),
        (tools_fs.AuditorReportTools, "_find_clause_evidence", "fce", None),
        (tools_fs.GoingConcernTools, "_auditor_flagged", "af", None),
        (tools_fs.AuditRiskTools, "_match_note_tables", "mnt", []),
    ]
    # The recorders must keep the real parameter names: the bridge's contract
    # check rejects anything else, which is exactly what it is for.
    import inspect
    for owner, attribute, label, result in specs:
        original = getattr(owner, attribute)
        params = list(inspect.signature(original).parameters)
        src = f"def _fn({', '.join(params)}):\n    _calls.append({label!r})\n    return _result\n"
        namespace: dict = {"_calls": calls, "_result": result}
        exec(src, namespace)
        monkeypatch.setattr(owner, attribute, staticmethod(namespace["_fn"]))

    bridge._installed = False
    bridge.install(
        tools_fs.ComplianceTools, tools_fs.UnitResolver, tools_fs.DocumentResolver,
        tools_fs.SourceRef, tool_registry=tools_fs.ToolRegistry,
        narrative_targets={n: getattr(tools_fs, n) for n in [
            "DisclosureSearchTools", "AccountingPolicyTools", "ReportReferenceTools",
            "ExecutiveSummaryTools", "AuditorReportTools", "GoingConcernTools",
            "AuditRiskTools"]},
    )
    yield calls
    bridge._installed = False


@pytest.fixture
def package():
    """A statements file and an auditor's report, uploaded together."""
    sfs = store.UploadedDocument(
        doc_id="up_sfs", user_id="u", conversation_id="c", filename=SFS_FILE,
        document={"fy_start": 2022, "fy_end": 2023, "company": "IDBI Trusteeship Services Ltd"},
        identification={"entity_name": "IDBI Trusteeship Services Ltd", "financial_year": "2022-23"},
        quality={},
        tables=[dict(
            table_id="up_sfs_t1",
            table_title="Note 1 (a) - Property, plant and equipment",
            table_description="Gross carrying amount; Additions; Disposals",
            table_md="| x | 64,777 |", page_ocr_start=5, financial_stmt_type=None,
            toc_section="Note 1 (a) - Property, plant and equipment",
            source_file=SFS_FILE, is_financial=True)],
        texts=[
            chunk("up_sfs_p0003_c0001", 3, "heading", "Statement of Compliance", "Statement of Compliance"),
            chunk("up_sfs_p0003_c0002", 3, "text",
                  "These financial statements have been prepared in accordance with Indian "
                  "Accounting Standards (Ind AS).", "Statement of Compliance"),
            chunk("up_sfs_p0004_c0003", 4, "heading", "2.11 TAXATION:", "2.11 TAXATION:"),
            chunk("up_sfs_p0004_c0004", 4, "text",
                  "Income tax comprises current and deferred tax.", "2.11 TAXATION:"),
            chunk("up_sfs_p0005_c0005", 5, "heading", "NOTE 27 PROVISIONS", "NOTE 27 PROVISIONS"),
        ])

    iar = store.UploadedDocument(
        doc_id="up_iar", user_id="u", conversation_id="c", filename=IAR_FILE,
        document={"fy_start": 2022, "fy_end": 2023, "company": "IDBI Trusteeship Services Limited"},
        identification={"entity_name": "IDBI Trusteeship Services Limited", "financial_year": "2022-23"},
        quality={}, tables=[],
        texts=[
            chunk("up_iar_p0002_c0001", 2, "heading", "Material Uncertainty Related to Going Concern",
                  "Material Uncertainty Related to Going Concern", IAR_FILE),
            chunk("up_iar_p0003_c0003", 3, "text",
                  "In our opinion the company has not defaulted. We report that the audit trail "
                  "feature was not enabled for part of the year.",
                  "Annexure B to the Independent Auditors Report", IAR_FILE),
        ])

    store.STORE.put(sfs)
    store.STORE.put(iar)
    token = store.set_scope(store.scope_for("u", "c"))
    yield store.current_scope().packages[0]
    store.reset_scope(token)
    store.STORE.drop_conversation("u", "c")


# --------------------------------------------------------------------------
# Packaging
# --------------------------------------------------------------------------

def test_the_two_files_form_one_package(package):
    """Spec 4.1 treats the statements, auditor's report and CARO as one package,
    and the entity name differs between them only by its legal suffix."""
    assert len(package.members) == 2
    assert package.financial_year == "2022-23"
    assert package.doc_id.startswith("pkg_")


def test_a_document_with_no_readable_year_is_never_grouped():
    """Guessing which filing a year-less upload belongs to is how one year's
    CARO ends up cross-checked against another year's notes."""
    a = store.UploadedDocument(
        doc_id="a", user_id="u", conversation_id="c2", filename="a.pdf",
        document={}, identification={"entity_name": "X Ltd", "financial_year": "2022-23"}, quality={})
    b = store.UploadedDocument(
        doc_id="b", user_id="u", conversation_id="c2", filename="b.pdf",
        document={}, identification={"entity_name": "X Ltd", "financial_year": None}, quality={})
    packages = store.group_into_packages([a, b])
    assert len(packages) == 2


# --------------------------------------------------------------------------
# Fall-through
# --------------------------------------------------------------------------

def test_every_narrative_reader_falls_through_without_an_upload(installed):
    assert store.current_scope() is None
    conn = None
    tools_fs.DocumentResolver._fetch_following_chunks("corpus", {}, conn, 4)
    tools_fs.DocumentResolver._find_compliance_passage("corpus", conn)
    tools_fs.DisclosureSearchTools._find_heading_anchor("corpus", "[0.1]", conn)
    tools_fs.DisclosureSearchTools.search_company_disclosures("SAIL", "2023-24", "leases", conn)
    tools_fs.AccountingPolicyTools._find_heading_by_keywords("corpus", ["leases"], conn)
    tools_fs.AccountingPolicyTools._find_heading_by_embedding("corpus", "leases", conn)
    tools_fs.ReportReferenceTools.lookup_report_reference("SAIL", "2023-24", "Note 45", conn)
    tools_fs.ExecutiveSummaryTools._narrative("corpus", conn)
    tools_fs.AuditorReportTools._find_clause_evidence("corpus", ["x"], conn)
    tools_fs.GoingConcernTools._auditor_flagged("corpus", ["x"], conn)
    tools_fs.AuditRiskTools._match_note_tables("corpus", ["x"], conn)
    assert installed == ["ffc", "fcp", "fha", "scd", "fhk", "fhe", "lrr",
                         "nar", "fce", "af", "mnt"]


# --------------------------------------------------------------------------
# The three narrative-first playbooks
# --------------------------------------------------------------------------

def test_framework_compliance_passage_is_served_from_the_upload(installed, package):
    """Playbook 2 quotes this passage verbatim to decide the framework."""
    out = tools_fs.DocumentResolver._find_compliance_passage(
        package.doc_id, ExplodingConnection())
    assert "Indian Accounting Standards" in out
    assert "Statement of Compliance" in out
    assert installed == []


def test_caro_evidence_is_found_in_the_other_file_of_the_package(installed, package):
    """The audit-trail clause lives in the auditor's report; the notes live in the
    statements. Without packaging this check has only half its evidence."""
    found = tools_fs.AuditorReportTools._find_clause_evidence(
        package.doc_id, ["audit trail"], ExplodingConnection())
    assert found is not None
    label, extract, in_auditor_section = found
    assert "up_iar" in label, "the clause must be found in the auditor's report file"
    assert "not enabled for part of the year" in extract
    # The provenance flag playbook 8 relays: found under auditor's voice.
    assert in_auditor_section is True
    assert installed == []


def test_going_concern_prose_is_found(installed, package):
    """Playbook 9 ranks this above every computed indicator."""
    found = tools_fs.GoingConcernTools._auditor_flagged(
        package.doc_id, ["material uncertainty related to going concern"],
        ExplodingConnection())
    assert found is not None
    assert "Material Uncertainty" in found[1]
    assert installed == []


# --------------------------------------------------------------------------
# Policy notes and schedules
# --------------------------------------------------------------------------

def test_policy_note_lookup_uses_the_numbering_regex(installed, package):
    """`2.11 TAXATION:` is a policy sub-note; `NOTE 27 PROVISIONS` is a movement
    schedule. tools_fs separates them by the `N.M.` numbering, and conflating
    them returns a table of numbers where the policy text was asked for."""
    anchor = tools_fs.AccountingPolicyTools._find_heading_by_keywords(
        package.doc_id, ["taxation", "income tax"], ExplodingConnection())
    assert anchor is not None and anchor["content"] == "2.11 TAXATION:"

    # "provisions" must NOT match the NOTE 27 heading, which fails the regex.
    assert tools_fs.AccountingPolicyTools._find_heading_by_keywords(
        package.doc_id, ["provisions"], ExplodingConnection()) is None
    assert installed == []


def test_following_chunks_are_the_body_after_the_anchor(installed, package):
    anchor = tools_fs.AccountingPolicyTools._find_heading_by_keywords(
        package.doc_id, ["taxation"], ExplodingConnection())
    following = tools_fs.DocumentResolver._fetch_following_chunks(
        package.doc_id, anchor, ExplodingConnection(), 4)
    assert following, "the policy note's body must follow its heading"
    assert following[0]["content"].startswith("Income tax comprises")
    # Headings are excluded from a passage body (chunk_type IN ('text','list')).
    assert all(r["chunk_type"] in ("text", "list") for r in following)


def test_match_note_tables_finds_the_schedule(installed, package):
    """One helper behind get_schedule_note, get_audit_report_highlights and
    review_account_area — all three return nothing without it."""
    tables = tools_fs.AuditRiskTools._match_note_tables(
        package.doc_id, ["property, plant and equipment"], ExplodingConnection())
    assert [t["table_id"] for t in tables] == ["up_sfs_t1"]


def test_match_note_tables_can_match_the_derived_description(installed, package):
    """docling recovers no caption for most tables on a scan, so the description
    derived from the table's own row labels is what makes them findable."""
    tables = tools_fs.AuditRiskTools._match_note_tables(
        package.doc_id, ["gross carrying amount"], ExplodingConnection())
    assert [t["table_id"] for t in tables] == ["up_sfs_t1"]


# --------------------------------------------------------------------------
# Provenance and scoping
# --------------------------------------------------------------------------

def test_citations_name_the_source_file_within_a_package(installed, package):
    """Once two files are searched as one filing, "page 3" is ambiguous unless
    the citation says which document to open."""
    anchor = tools_fs.AccountingPolicyTools._find_heading_by_keywords(
        package.doc_id, ["taxation"], ExplodingConnection())
    following = tools_fs.DocumentResolver._fetch_following_chunks(
        package.doc_id, anchor, ExplodingConnection(), 4)
    assert SFS_FILE in following[0]["section"]


def test_consolidated_content_is_excluded():
    """Every narrative query sorts standalone before consolidated. A consolidated
    note answering a standalone question is a wrong answer, not a near miss."""
    doc = store.UploadedDocument(
        doc_id="up_c", user_id="u", conversation_id="c3", filename="cfs.pdf",
        document={}, identification={"entity_name": "X Ltd", "financial_year": "2022-23"},
        quality={},
        texts=[chunk("c1", 1, "heading", "Statement of Compliance",
                     "Statement of Compliance", "cfs.pdf", CONSOLIDATED_CRUMB)])
    assert narrative.find_compliance_passage(doc) is None


def test_executive_summary_narrative_is_empty_rather_than_invented(installed, package):
    """Section 6 is "the company's own business highlights". A standalone
    statements file has no MD&A, and generating one from the notes would be
    fabricating management narrative the filing never contained."""
    assert tools_fs.ExecutiveSummaryTools._narrative(
        package.doc_id, ExplodingConnection()) == ""


# --------------------------------------------------------------------------
# Real-document shapes
# --------------------------------------------------------------------------

def test_policy_lookup_survives_a_missing_notes_breadcrumb():
    """Regression, from a real scan.

    On the OD-SPSU filing docling recovers `## 2.11 TAXATION:` but emits no
    "Notes to the Standalone Financial Statements" heading on that page -- the
    marker is on an earlier sheet. Applying the breadcrumb filter
    unconditionally matched nothing, so `get_accounting_policy_note` reported no
    policy note for a document that plainly has one, which is exactly the
    "absence of evidence" mistake rule 16 forbids.
    """
    doc = store.UploadedDocument(
        doc_id="up_nb", user_id="u", conversation_id="c4", filename="od.pdf",
        document={}, identification={"entity_name": "Startup Odisha", "financial_year": "2021-22"},
        quality={},
        texts=[
            chunk("c1", 10, "heading", "2.11 TAXATION:", "2.11 TAXATION:", "od.pdf",
                  ["Standalone Financial Statements"]),
            chunk("c2", 10, "text", "Income tax comprises current and deferred tax.",
                  "2.11 TAXATION:", "od.pdf", ["Standalone Financial Statements"]),
        ])
    found = narrative.find_heading_by_keywords(doc, ["taxation", "income tax"])
    assert found is not None and found["content"] == "2.11 TAXATION:"


def test_the_numbering_regex_still_applies_without_a_breadcrumb():
    """Relaxing the breadcrumb filter must not also admit movement schedules."""
    doc = store.UploadedDocument(
        doc_id="up_nb2", user_id="u", conversation_id="c5", filename="od.pdf",
        document={}, identification={"entity_name": "X", "financial_year": "2021-22"},
        quality={},
        texts=[chunk("c1", 5, "heading", "NOTE 27 PROVISIONS", "NOTE 27 PROVISIONS",
                     "od.pdf", ["Standalone Financial Statements"])])
    assert narrative.find_heading_by_keywords(doc, ["provisions"]) is None
