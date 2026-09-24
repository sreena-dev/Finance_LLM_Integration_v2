"""Tests for the upload bridge, run against the real vendored pipeline.

These import the actual ``tools_fs`` module rather than a stub, because the
whole point of the bridge is that the *existing* 8,378-line tool library reads
uploaded documents without being changed. A test against a mock would pass while
the real thing was broken.

Run with::

    cd backend
    DB_PASSWORD=x REPORTS_DB_PASSWORD=x venv/Scripts/python -m pytest \
        modes/financial_statement/upload/test_bridge.py -q

The two properties that matter:

1. **Fall-through is exact.** With no uploads in scope every call reaches the
   original function untouched, so a corpus question takes the same code path it
   took before this feature existed.
2. **No SQL is issued for an uploaded document.** Enforced by passing a
   connection object that raises if anything touches it -- which is a far
   stronger assertion than checking a return value, because it fails on the
   attempt rather than on the result.
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

# The branch's Config raises if these are unset, and it is evaluated at import.
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("REPORTS_DB_PASSWORD", "test")

tools_fs = pytest.importorskip("tools_fs")

from modes.financial_statement.upload import bridge, store  # noqa: E402


BALANCE_SHEET = """| Particulars | Note | As at 31 March 2023 | As at 31 March 2022 |
| --- | --- | --- | --- |
| Property, plant and equipment | 1 | 39,640 | 6,886 |
| Trade receivables | 5 | 12,45,000 | 8,10,000 |
| Cash and cash equivalents | 6 | 14,99,540 | 9,20,100 |
| Total current assets |  | 27,44,540 | 17,30,100 |
| Total assets |  | 27,84,180 | 17,36,986 |
| Total equity |  | 22,17,065 | 20,17,453 |
| Total current liabilities |  | 5,67,115 | 2,80,467 |
| Total equity and liabilities |  | 27,84,180 | 22,97,920 |"""

PROFIT_LOSS = """| Particulars | Note | 2022-23 | 2021-22 |
| --- | --- | --- | --- |
| Revenue from operations | 12 | 45,60,040 | 40,19,130 |
| Other income | 13 | 8,60,447 | 2,40,624 |
| Depreciation and amortisation | 1 | 3,279 | 1,942 |
| Finance costs | 14 | 1,20,000 | 98,000 |
| Profit before tax |  | 6,10,004 | 5,35,200 |
| Profit for the year |  | 4,56,004 | 4,01,200 |"""


class ExplodingConnection:
    """A connection that fails loudly if anything tries to use it.

    Passed wherever the real request would pass a live reports-DB connection.
    The tools check ``conn is None`` before doing anything, so the connection
    cannot simply be None -- but it must never actually be queried for an
    uploaded document.
    """

    def cursor(self, *args, **kwargs):
        raise AssertionError("SQL was attempted for an uploaded document")

    def rollback(self):
        return None


@pytest.fixture
def originals(monkeypatch):
    """Replace the five bridge targets with recorders, then install the bridge.

    Parameter names are copied verbatim from the vendored signatures; the
    bridge's contract check rejects anything else, which is exactly what it is
    for.
    """
    calls: list[str] = []

    def _find(doc_id, statement_type, conn_reports):
        calls.append("find")
        return "No standalone table was found for this document."

    def _cont(doc_id, after_table_id, anchor_page, conn_reports, limit=1):
        calls.append("cont")
        return []

    def _units(doc_id, conn_reports):
        calls.append("units")
        return {"scale": None, "currency": None, "label": None, "declared": False, "mixed": False}

    def _doc(company, financial_year, conn):
        calls.append("doc")
        return None

    def _fy(company, conn):
        calls.append("fy")
        return None

    monkeypatch.setattr(tools_fs.ComplianceTools, "_find_statement_tables", staticmethod(_find))
    monkeypatch.setattr(tools_fs.ComplianceTools, "_fetch_untitled_continuations", staticmethod(_cont))
    monkeypatch.setattr(tools_fs.UnitResolver, "resolve", staticmethod(_units))
    monkeypatch.setattr(tools_fs.DocumentResolver, "_resolve_document", staticmethod(_doc))
    monkeypatch.setattr(tools_fs.DocumentResolver, "latest_fy_end", staticmethod(_fy))

    # install() is idempotent by design, and monkeypatch restores the originals
    # after each test, so the flag has to be cleared for the next install to
    # wrap this test's recorders rather than silently keeping the previous set.
    bridge._installed = False
    bridge.install(tools_fs.ComplianceTools, tools_fs.UnitResolver,
                   tools_fs.DocumentResolver, tools_fs.SourceRef)
    yield calls
    bridge._installed = False


@pytest.fixture
def uploaded():
    document = store.UploadedDocument(
        doc_id="up_test", user_id="u1", conversation_id="c1",
        filename="ITSL_2022-23_SFS.pdf",
        document={"fy_start": 2022, "fy_end": 2023,
                  "company": "IDBI Trusteeship Services Limited"},
        identification={"entity_name": "IDBI Trusteeship Services Limited",
                        "financial_year": "2022-23"},
        quality={"grade": "good"},
        tables=[
            {"table_id": "up_test_t1", "table_title": "Balance Sheet as at 31 March 2023",
             "table_md": BALANCE_SHEET, "page_ocr_start": 2,
             "financial_stmt_type": "balance_sheet", "unit": "thousand",
             "currency": "INR", "is_financial": True},
            {"table_id": "up_test_t2", "table_title": "Statement of Profit and Loss",
             "table_md": PROFIT_LOSS, "page_ocr_start": 3,
             "financial_stmt_type": "profit_loss", "unit": "thousand",
             "currency": "INR", "is_financial": True},
            {"table_id": "up_test_t3", "table_title": None,
             "table_md": "| (continued) | 1 |", "page_ocr_start": 3,
             "financial_stmt_type": None, "unit": "thousand",
             "currency": "INR", "is_financial": True},
        ])
    store.STORE.put(document)
    token = store.set_scope(store.scope_for("u1", "c1"))
    yield document
    store.reset_scope(token)
    store.STORE.drop_conversation("u1", "c1")


# --------------------------------------------------------------------------
# Contract
# --------------------------------------------------------------------------

def test_contract_check_rejects_a_changed_signature():
    """A re-pull that renames a parameter must fail loudly at install.

    Declining silently would leave every log line claiming the feature shipped
    while uploads quietly went nowhere.
    """
    class Wrong:
        @staticmethod
        def _find_statement_tables(d, s, c):
            return ""

    bridge._installed = False
    with pytest.raises(bridge.BridgeContractError):
        bridge._verify(Wrong, "_find_statement_tables",
                       ("doc_id", "statement_type", "conn_reports"),
                       "ComplianceTools._find_statement_tables")


# --------------------------------------------------------------------------
# Fall-through
# --------------------------------------------------------------------------

def test_no_uploads_in_scope_falls_through_to_the_corpus(originals):
    assert store.current_scope() is None
    tools_fs.ComplianceTools._find_statement_tables("corpus_1", "balance_sheet", None)
    tools_fs.UnitResolver.resolve("corpus_1", None)
    tools_fs.DocumentResolver._resolve_document("SAIL", "2023-24", None)
    tools_fs.DocumentResolver.latest_fy_end("SAIL", None)
    assert originals == ["find", "units", "doc", "fy"]


def test_an_entity_that_was_not_uploaded_still_reaches_the_corpus(originals, uploaded):
    """Uploads take precedence only for documents actually uploaded. A question
    about a different entity belongs to the corpus."""
    tools_fs.DocumentResolver._resolve_document("Steel Authority of India", "2023-24", None)
    assert originals == ["doc"]


# --------------------------------------------------------------------------
# The upload path
# --------------------------------------------------------------------------

def test_uploaded_document_resolves_with_a_documents_row_shape(originals, uploaded):
    match = tools_fs.DocumentResolver._resolve_document("IDBI", "2022-23", None)
    assert match["doc_id"] == "up_test"
    assert match["fy_start"] == 2022 and match["fy_end"] == 2023
    # doc_name is rendered into every citation by prompt rule 18, and the
    # filename is what the user will recognise.
    assert match["doc_name"] == "ITSL_2022-23_SFS.pdf"
    assert originals == []


def test_statement_lookup_is_served_from_memory(originals, uploaded):
    text = tools_fs.ComplianceTools._find_statement_tables("up_test", "balance_sheet", None)
    assert "Total equity and liabilities" in text
    assert "[table_id: up_test_t1" in text
    assert originals == []


def test_a_user_entered_figure_carries_a_data_quality_note_into_the_tool_output(originals, uploaded):
    """Disclosure layer 2 of `edits.py`'s design (layer 1 is the inline
    `[user-entered]` tag already in `table_md` itself): a tool reading the
    table also gets a plain-sentence note naming the figure and its prior
    state, so a model that only skims the raw table text still sees it."""
    uploaded.quality = {
        "grade": "good",
        "user_edits": [{
            "table": "t1", "row_index": 0, "col_index": 2,
            "row_label": "Property, plant and equipment", "column": "As at 31 March 2023",
            "value": "39,640", "original_state": "unreadable", "active": True,
        }],
    }
    store.STORE.put(uploaded)
    store.set_scope(store.scope_for("u1", "c1"))

    text = tools_fs.ComplianceTools._find_statement_tables("up_test", "balance_sheet", None)

    assert "DATA QUALITY NOTE" in text
    assert "entered by the user" in text
    assert 'row "Property, plant and equipment"' in text
    assert originals == []


def test_a_missing_statement_uses_the_wording_downstream_code_keys_on(originals, uploaded):
    """``FinancialFactBase.load`` decides a statement was not found by testing
    for the substring 'No standalone'. A different sentence here would make it
    treat the miss as a parsed-but-empty table."""
    text = tools_fs.ComplianceTools._find_statement_tables("up_test", "cash_flow", None)
    assert text.startswith("No standalone")


def test_untitled_continuation_is_found(originals, uploaded):
    """A statement split across a page break arrives as a titled block plus an
    untitled continuation; without this the second half is simply missing."""
    rows = tools_fs.ComplianceTools._fetch_untitled_continuations(
        "up_test", "up_test_t1", 2, None, 1)
    assert [r["table_id"] for r in rows] == ["up_test_t3"]


def test_units_are_resolved_from_the_documents_own_tables(originals, uploaded):
    info = tools_fs.UnitResolver.resolve("up_test", None)
    assert info["declared"] is True
    assert info["scale"] == "thousand" and info["currency"] == "INR"
    line = tools_fs.UnitResolver.units_line("up_test", None)
    # This line is what prompt rule 20 requires the model to attach to every
    # figure it quotes.
    assert "thousand" in line and "UNITS:" in line


def test_units_are_never_guessed(originals, uploaded):
    """UnitResolver is explicit that a guessed scale is worse than an absent
    one, because the same digits mean different things three orders of
    magnitude apart."""
    for table in uploaded.tables:
        table["unit"] = None
        table["currency"] = None
    # The store snapshots a document into Redis on put() rather than keeping
    # a live reference to it, and the bound Scope is itself a snapshot taken
    # once by the `uploaded` fixture — neither updates just by mutating this
    # Python object, the way the old in-process dict store let a caller do by
    # accident. A real store requires both an explicit write-back AND
    # re-reading the scope that was bound before the mutation.
    store.STORE.put(uploaded)
    store.set_scope(store.scope_for("u1", "c1"))
    info = tools_fs.UnitResolver.resolve("up_test", None)
    assert info["declared"] is False and info["scale"] is None


# --------------------------------------------------------------------------
# The existing tool library, unmodified
# --------------------------------------------------------------------------

def test_ratio_extraction_engine_reads_an_uploaded_document(originals, uploaded):
    figures = tools_fs.RatioExtractionEngine.extract_all_figures("up_test", ExplodingConnection())
    assert figures["total_assets"] == 2784180.0
    assert figures["total_equity_and_liabilities"] == 2784180.0
    assert figures["revenue_from_operations"] == 4560040.0
    assert figures["profit_before_tax"] == 610004.0


#: A Schedule III Division I balance sheet: bare "Total" closing each side,
#: no "Total assets" / "Total equity and liabilities" printed anywhere --
#: verified real shape, transcribed from data/OD-SPSU-SO-032/2022-23's own
#: extraction. `BALANCE_SHEET` above never exercises the bare-Total fallback
#: at all, because it prints "Total equity and liabilities" explicitly.
DIVISION_I_BALANCE_SHEET = """| Particulars | Note No. | 31st March, 2023 | 31st March, 2022 |
| --- | --- | --- | --- |
| I. Equity and Liabilities | | | |
| (1) Shareholders' funds | | | |
| (a) Share capital | 1 | 15,00,000.00 | 15,00,000.00 |
| (b) Reserves and surplus | 2 | 38,81,031.72 | -25,460.00 |
| Total | | 43,34,97,783.82 | 14,99,540.00 |
| II. Assets | | | |
| (1) Non-current assets | | | |
| (a) Property, Plant and Equipment | 7 | 3,10,253.00 | |
| (2) Current assets | | | |
| (b) Cash and cash equivalents | 8 | 25,46,94,119.72 | 14,99,540.00 |
| TOTAL | | 43,34,97,783.82 | 14,99,540.00 |
"""


def test_total_equity_and_liabilities_and_total_liabilities_resolve_end_to_end(originals):
    """The fallback exercised through the real bridge + extract_all_figures,
    not just the bare `_find_bare_total_after` unit -- proof the fix reaches
    an uploaded document the way a real query actually would.

    total_liabilities has no printed row at all on this fixture (same as the
    real filing); it must come out as the derived difference between
    total_equity_and_liabilities and total_equity, not as None."""
    document = store.UploadedDocument(
        doc_id="up_div1", user_id="u1", conversation_id="c1",
        filename="OD_2022-23_SFS.pdf",
        document={"fy_start": 2022, "fy_end": 2023, "company": "Startup Odisha"},
        identification={"entity_name": "Startup Odisha", "financial_year": "2022-23"},
        quality={"grade": "fair"},
        tables=[{
            "table_id": "up_div1_t1", "table_title": "Balance Sheet as at 31st March, 2023",
            "table_md": DIVISION_I_BALANCE_SHEET, "page_ocr_start": 1,
            "financial_stmt_type": "balance_sheet", "unit": None,
            "currency": "INR", "is_financial": True,
        }],
    )
    store.STORE.put(document)
    token = store.set_scope(store.scope_for("u1", "c1"))
    try:
        figures = tools_fs.RatioExtractionEngine.extract_all_figures("up_div1", ExplodingConnection())
        assert figures["total_assets"] == pytest.approx(433497783.82, abs=0.01)
        assert figures["total_equity_and_liabilities"] == pytest.approx(433497783.82, abs=0.01)
        # Share capital (15,00,000) + reserves and surplus (38,81,031.72).
        assert figures["total_equity"] == pytest.approx(5381031.72, abs=0.01)
        assert figures["total_liabilities"] == pytest.approx(
            433497783.82 - 5381031.72, abs=0.01
        )
    finally:
        store.reset_scope(token)
        store.STORE.drop_conversation("u1", "c1")


def test_tie_out_checks_run_and_the_balance_sheet_balances(originals, uploaded):
    """The headline claim: the existing cross-statement check library runs
    against an uploaded scan with no change to its own code."""
    tools_fs.FinancialFactBase.reset()
    tools_fs.UnitResolver.reset()
    out = tools_fs.TieOutTools.run_tie_out_checks(
        "IDBI Trusteeship", "2022-23", "balance_sheet", ExplodingConnection())
    assert "Balance sheet equation" in out
    assert "**PASS**" in out
    assert "2,784,180.00" in out


def test_ratio_analysis_runs_against_an_uploaded_document(originals, uploaded):
    tools_fs.FinancialFactBase.reset()
    tools_fs.UnitResolver.reset()
    out = tools_fs.RatioTools.compute_ratio_analysis(
        "IDBI Trusteeship", "2022-23", "liquidity", ExplodingConnection())
    assert "doc_id=up_test" in out
    assert "UNITS:" in out


# --------------------------------------------------------------------------
# Store scoping and lifetime
# --------------------------------------------------------------------------

def test_documents_are_scoped_to_their_own_user_and_conversation(uploaded):
    assert store.STORE.get("u1", "c1", "up_test") is not None
    # Same document id, different user or conversation: not visible.
    assert store.STORE.get("u2", "c1", "up_test") is None
    assert store.STORE.get("u1", "c2", "up_test") is None


def test_deleting_a_conversation_drops_its_documents(uploaded):
    assert store.STORE.drop_conversation("u1", "c1") == 1
    assert store.STORE.list("u1", "c1") == []


# --------------------------------------------------------------------------
# Progressive/partial availability (Phase 2) -- a document the ingestion
# service is still converting, published early via `router.py`'s
# `/upload/{job_id}/events` "partial" branch.
# --------------------------------------------------------------------------

def test_ingestion_status_defaults_to_complete():
    # Every document built before this feature existed, and every document
    # built from a normal "result" event, has no "ingestion_status" key at
    # all -- must read as complete, not crash or read as partial.
    document = store.UploadedDocument(
        doc_id="up_x", user_id="u1", conversation_id="c1", filename="a.pdf",
        document={}, identification={}, quality={},
    )
    assert document.ingestion_status == "complete"


def test_ingestion_status_reads_partial_from_the_document_dict():
    document = store.UploadedDocument(
        doc_id="up_x", user_id="u1", conversation_id="c1", filename="a.pdf",
        document={"ingestion_status": "partial"}, identification={}, quality={},
    )
    assert document.ingestion_status == "partial"


def test_document_header_is_unchanged_for_a_complete_document(uploaded):
    header = bridge._document_header(uploaded)
    assert "STILL BEING INGESTED" not in header
    assert "doc_id=up_test" in header


def test_document_header_warns_when_the_document_is_partial():
    partial = store.UploadedDocument(
        doc_id="up_partial", user_id="u1", conversation_id="c1",
        filename="still-converting.pdf",
        document={"ingestion_status": "partial"},
        identification={}, quality={},
        tables=[{"table_id": "up_partial_t1", "table_md": "| a |"}],
    )
    header = bridge._document_header(partial)
    assert "STILL BEING INGESTED" in header
    assert "1 table(s) extracted so far" in header
    assert "doc_id=up_partial" in header
