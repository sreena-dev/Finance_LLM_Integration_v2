"""Several uploaded documents in one conversation, and comparing across them.

No test exercised a 2+ document path before this file, which is how the
following shipped: the same financial year is spelled three different ways by
three different parts of the system, and they were compared with ``==``.

    ingestion stores              "2023-24"   (identify.py)
    entity_resolution._fy_label   "FY2023-24"
    TrendAnalysisTools            "2023-2024" (f"{y}-{y+1}")

None of those match each other as strings. The consequence was not a near-miss
but a LOOP: `Scope.match` drops the year filter when nothing matches exactly,
so every document came back, the caller saw more than one and asked the user
which year they meant -- and the year it suggested failed identically when the
model repeated it back. `get_multi_year_trend`, the only tool that computes
cross-year deltas, could therefore never return a figure for an upload.

Run with::

    cd backend
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 venv/Scripts/python -m pytest \
        modes/financial_statement/upload/test_multi_document.py -q
"""

from __future__ import annotations

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_MODE = os.path.dirname(_HERE)
_BACKEND = os.path.dirname(os.path.dirname(_MODE))
for _path in (os.path.join(_MODE, "pipeline"), _BACKEND):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from modes.financial_statement.upload import bridge  # noqa: E402
from modes.financial_statement.upload import store as store_mod  # noqa: E402
from modes.financial_statement.upload import tools as tools_mod  # noqa: E402
from modes.financial_statement.upload.store import (  # noqa: E402
    Scope,
    UploadedDocument,
    _fy_key,
    group_into_packages,
)


def _doc(doc_id, company, financial_year, filename=None, fy_end=None):
    """One uploaded document, with only the fields matching reads."""
    if fy_end is None and financial_year:
        key = _fy_key(financial_year)
        fy_end = key[1] if key else None
    return UploadedDocument(
        doc_id=doc_id,
        user_id="u1",
        conversation_id="c1",
        filename=filename or f"{doc_id}.pdf",
        document={"fy_end": fy_end, "fy_start": (fy_end - 1) if fy_end else None},
        identification={"entity_name": company, "financial_year": financial_year},
        quality={},
    )


def _scope(*documents):
    return Scope(user_id="u1", conversation_id="c1", documents=list(documents))


# --------------------------------------------------------------------------
# _fy_key -- the three real spellings
# --------------------------------------------------------------------------

@pytest.mark.parametrize("spelling", [
    "2023-24",       # what ingestion stores
    "FY2023-24",     # what entity_resolution._fy_label hands the model back
    "2023-2024",     # what TrendAnalysisTools._resolve_reports generates
    "FY 2023-24",
    "2023/24",
    "FY24",
    "2024",          # a bare year means the year it ENDS in, per Indian usage
])
def test_every_real_spelling_of_one_year_agrees(spelling):
    assert _fy_key(spelling) == (2023, 2024), spelling


def test_adjacent_years_stay_distinct():
    """The normalisation must not be so loose that it merges two filings."""
    assert _fy_key("2022-23") != _fy_key("2023-24")
    assert _fy_key("FY2022-23") != _fy_key("2023-2024")


def test_a_century_rollover_does_not_land_in_the_past():
    assert _fy_key("1999-00") == (1999, 2000)


def test_something_that_is_not_a_year_is_not_invented_into_one():
    for junk in ("", None, "not a year", "Particulars", "Note No."):
        assert _fy_key(junk) is None, junk


# --------------------------------------------------------------------------
# Scope.match -- the loop this closes
# --------------------------------------------------------------------------

@pytest.mark.parametrize("asked", ["2023-24", "FY2023-24", "2023-2024", "2024"])
def test_a_year_resolves_to_one_document_however_it_is_spelled(asked):
    """THE regression. Each spelling must select exactly ONE of three uploads.

    Before this, only the first spelling matched; the others fell through to
    "no exact match", the year filter was dropped, all three documents came
    back, and the caller reported an ambiguity it had just been given the
    answer to.
    """
    scope = _scope(
        _doc("up_a", "Startup Odisha", "2021-22"),
        _doc("up_b", "Startup Odisha", "2022-23"),
        _doc("up_c", "Startup Odisha", "2023-24"),
    )

    matches = scope.match("Startup Odisha", asked)

    assert len(matches) == 1, [m.filename for m in matches]
    assert matches[0].doc_id == "up_c"


def test_the_label_handed_back_to_the_model_round_trips():
    """entity_resolution._fy_label prints FY2022-23. A model that obediently
    re-asks with exactly that string must now land on the document, rather
    than getting the same ambiguity message a second time."""
    scope = _scope(
        _doc("up_a", "Acme", "2022-23"),
        _doc("up_b", "Acme", "2023-24"),
    )

    assert len(scope.match("Acme", "FY2022-23")) == 1
    assert scope.match("Acme", "FY2022-23")[0].doc_id == "up_a"


def test_the_trend_tools_generated_label_resolves():
    """TrendAnalysisTools._resolve_reports builds f"{y}-{y+1}" -> '2022-2023'.
    That four-digit tail never matched the stored '2022-23'."""
    scope = _scope(
        _doc("up_a", "Acme", "2022-23"),
        _doc("up_b", "Acme", "2023-24"),
    )

    matches = scope.match("Acme", "2022-2023")
    assert len(matches) == 1
    assert matches[0].doc_id == "up_a"


def test_a_year_no_upload_covers_still_falls_back_rather_than_erroring():
    """Unchanged behaviour: an unmatched year keeps the looser company set, so
    the caller can report what IS uploaded instead of failing."""
    scope = _scope(
        _doc("up_a", "Acme", "2022-23"),
        _doc("up_b", "Acme", "2023-24"),
    )

    matches = scope.match("Acme", "2019-20")

    assert len(matches) == 2


def test_an_unparseable_year_falls_back_to_literal_comparison():
    scope = _scope(_doc("up_a", "Acme", "2022-23"))
    assert len(scope.match("Acme", "whenever")) == 1


# --------------------------------------------------------------------------
# Packaging across several files
# --------------------------------------------------------------------------

def test_three_years_of_one_entity_stay_three_separate_filings():
    """The multi-PDF comparison case. These must NOT collapse into one
    package -- they are three filings, and a trend needs all three."""
    packages = group_into_packages([
        _doc("up_a", "Startup Odisha", "2021-22"),
        _doc("up_b", "Startup Odisha", "2022-23"),
        _doc("up_c", "Startup Odisha", "2023-24"),
    ])

    assert len(packages) == 3
    assert sorted(p.financial_year for p in packages) == ["2021-22", "2022-23", "2023-24"]


def test_one_years_statements_and_auditors_report_become_one_filing():
    """What packaging exists for: section 13 cross-reads a CARO clause against
    the notes, and those arrive as separate PDFs."""
    packages = group_into_packages([
        _doc("up_sfs", "Acme Limited", "2023-24", filename="Acme_2023-24_SFS.pdf"),
        _doc("up_iar", "Acme Ltd", "2023-24", filename="Acme_2023-24_IARSFS.pdf"),
    ])

    assert len(packages) == 1
    assert len(packages[0].members) == 2


def test_one_filing_is_not_split_by_two_spellings_of_its_own_year():
    """The packaging half of the same defect: if the statements read
    '2023-24' and the auditor's report read '2023-2024', a string comparison
    made them two filings and a CARO cross-check lost half its evidence."""
    packages = group_into_packages([
        _doc("up_sfs", "Acme Limited", "2023-24"),
        _doc("up_iar", "Acme Limited", "2023-2024"),
    ])

    assert len(packages) == 1, [p.filename for p in packages]


def test_a_document_whose_year_could_not_be_read_is_never_grouped():
    """Deliberate, and must stay: guessing which filing an undated file
    belongs to is how one year's CARO ends up against another year's notes."""
    packages = group_into_packages([
        _doc("up_a", "Acme", "2023-24"),
        _doc("up_b", "Acme", None),
    ])

    assert len(packages) == 2


def test_different_entities_are_never_grouped_even_in_the_same_year():
    packages = group_into_packages([
        _doc("up_a", "Acme Limited", "2023-24"),
        _doc("up_b", "Beta Corporation", "2023-24"),
    ])

    assert len(packages) == 2


# --------------------------------------------------------------------------
# The multi-year trend -- the only tool that computes a cross-year delta
# --------------------------------------------------------------------------

@pytest.fixture
def install_scope():
    """Put a scope in the ContextVar the bridge reads, and always take it out.

    The reset matters: `current_scope` is a ContextVar, so a scope left set by
    one test leaks into every test after it and they start passing or failing
    for the wrong reason.
    """
    tokens = []

    def _install(scope):
        tokens.append(store_mod.set_scope(scope))
        return scope

    yield _install
    for token in reversed(tokens):
        store_mod.reset_scope(token)


def test_a_trend_spans_every_uploaded_filing_not_just_two(install_scope):
    """THE regression for comparative questions.

    The original builds `range(latest - 2, latest)` -- exactly two labels --
    so a user who uploaded three filings got a two-year trend with no
    indication the third had been dropped.
    """
    def _original(company, financial_year, conn_reports):
        raise AssertionError("must not fall through to the corpus")

    wrapped = bridge._wrap_resolve_reports(_original)
    install_scope(_scope(
        _doc("up_a", "Startup Odisha", "2021-22"),
        _doc("up_b", "Startup Odisha", "2022-23"),
        _doc("up_c", "Startup Odisha", "2023-24"),
    ))

    rows, error = wrapped("Startup Odisha", "", conn_reports=None)

    assert error is None
    assert [r["fy_end"] for r in rows] == [2022, 2023, 2024], "all three, ascending"


def test_a_trend_never_abandons_the_series_to_ask_which_year(install_scope):
    """Previously any label matching more than one document returned
    format_ambiguous as the tool's ENTIRE output -- a clarifying question
    about the user's own files instead of the trend they asked for."""
    def _original(company, financial_year, conn_reports):
        raise AssertionError("must not fall through to the corpus")

    wrapped = bridge._wrap_resolve_reports(_original)
    install_scope(_scope(
        _doc("up_a", "Acme", "2022-23"),
        _doc("up_b", "Acme", "2023-24"),
    ))

    rows, error = wrapped("Acme", "FY2023-24", conn_reports=None)

    assert error is None
    assert rows is not None and len(rows) == 2


def test_a_company_with_no_uploads_still_falls_through_to_the_corpus(install_scope):
    """The bridge's central rule: uploads take precedence only on a match."""
    sentinel = ([{"doc_id": "corpus"}], None)

    def _original(company, financial_year, conn_reports):
        return sentinel

    wrapped = bridge._wrap_resolve_reports(_original)
    install_scope(_scope(_doc("up_a", "Acme", "2023-24")))

    assert wrapped("Some Other Entity", "", conn_reports=None) is sentinel


def test_no_uploads_at_all_falls_through_untouched():
    sentinel = ([{"doc_id": "corpus"}], None)
    wrapped = bridge._wrap_resolve_reports(lambda c, f, r: sentinel)

    assert wrapped("Acme", "2023-24", conn_reports=None) is sentinel


def test_the_comparison_tool_routes_a_trend_to_the_tool_that_computes_it(install_scope):
    """Rule 15 forbids the model subtracting two figures it got from two
    separate tool calls, so listing the documents and saying "call the company
    tools once per document" left a trend question with no legal answer path.
    The hand-off has to name `get_multi_year_trend`."""
    install_scope(_scope(
        _doc("up_a", "Startup Odisha", "2021-22"),
        _doc("up_b", "Startup Odisha", "2022-23"),
        _doc("up_c", "Startup Odisha", "2023-24"),
    ))

    out = tools_mod.compare_uploaded_years("revenue")

    assert "get_multi_year_trend" in out
    assert "rule 15" in out.lower()


def test_one_filings_several_files_do_not_look_like_duplicate_years(install_scope):
    """An SFS plus its auditor's report is ONE filing. Counting files made it
    look like two uploads claiming the same year and raised a warning telling
    the auditor to check for a superseded set that did not exist."""
    install_scope(_scope(
        _doc("up_sfs", "Acme Limited", "2023-24", filename="Acme_2023-24_SFS.pdf"),
        _doc("up_iar", "Acme Ltd", "2023-24", filename="Acme_2023-24_IARSFS.pdf"),
    ))

    out = tools_mod.compare_uploaded_years("")

    assert "same financial year" not in out
    assert "DIFFERENT entities" not in out


def test_a_legal_suffix_difference_is_not_a_different_entity(install_scope):
    """"Acme Ltd" on the balance sheet and "Acme Limited" on the auditor's
    report are one entity. Exact lowercase comparison flagged them as two and
    taught the reader to distrust a sound trend."""
    install_scope(_scope(
        _doc("up_a", "Acme Ltd", "2022-23"),
        _doc("up_b", "Acme Limited", "2023-24"),
    ))

    out = tools_mod.compare_uploaded_years("")

    assert "DIFFERENT entities" not in out


def test_genuinely_different_entities_are_still_flagged(install_scope):
    """The warning must not be blunted into uselessness by the fix above."""
    install_scope(_scope(
        _doc("up_a", "Acme Limited", "2022-23"),
        _doc("up_b", "Beta Corporation", "2023-24"),
    ))

    out = tools_mod.compare_uploaded_years("")

    assert "DIFFERENT entities" in out


def test_a_package_can_summarise_itself():
    """Package had no summary(), so any caller iterating filings rather than
    files raised AttributeError -- which is what kept compare_uploaded_years
    counting raw files in the first place."""
    packages = group_into_packages([
        _doc("up_sfs", "Acme Limited", "2023-24"),
        _doc("up_iar", "Acme Ltd", "2023-24"),
    ])
    assert len(packages) == 1

    summary = packages[0].summary()

    assert summary["company"] == "Acme Limited"
    assert summary["financial_year"] == "2023-24"
    assert summary["doc_id"] == packages[0].doc_id


def test_a_packages_quality_is_its_weakest_members_not_its_first():
    """A clean set of statements must not mask an illegible annexure bound
    into the same filing."""
    good = _doc("up_a", "Acme", "2023-24")
    good.quality = {"grade": "good", "low_grade": "good", "vlm_used": True}
    poor = _doc("up_b", "Acme", "2023-24")
    poor.quality = {"grade": "poor", "low_grade": "poor", "vlm_used": False}

    package = group_into_packages([good, poor])[0]
    summary = package.summary()

    assert summary["grade"] == "poor"
    assert summary["vlm_used"] is False, "partly corroborated is not corroborated"


def test_an_undated_upload_is_left_out_of_the_series(install_scope):
    """A filing whose year could not be read cannot be placed on a timeline,
    and guessing where it goes would misdate every delta after it."""
    wrapped = bridge._wrap_resolve_reports(lambda c, f, r: (None, "corpus"))
    install_scope(_scope(
        _doc("up_a", "Acme", "2022-23"),
        _doc("up_b", "Acme", "2023-24"),
        _doc("up_c", "Acme", None),
    ))

    rows, error = wrapped("Acme", "", conn_reports=None)

    assert error is None
    assert [r["fy_end"] for r in rows] == [2023, 2024]
