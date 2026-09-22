"""Which periods an uploaded document can actually be asked about.

The defect this pins down, reported from a live conversation: a user uploaded
Startup Odisha's 2023-24 filing and asked for the movement in Employee
benefits expenses versus the previous year. The answer was a refusal — "the
previous year's (2022-23) financial statements were not provided in the
uploads" — while the extracted note table on screen read:

    | PARTICULARS | FIGURES AS AT 31ST MARCH, 2024 | FIGURES AS AT 31ST MARCH, 2023 |
    | (a) Salary Expenses: | 2,10,63,434.00 | 1,60,03,755.00 |

Both figures were extracted, stored and rendered. Only the METADATA said one
year, because a document's `financial_year` is the year the filing is FOR, and
every surface that told the model what was in scope reported that instead of
what the data covers.

Every header string below is a real shape from the corpus, not an invented
one: the "Figures as at" wording is OD-SPSU-SO-032's, the bare "31st March
2025" is MH-CPSU-ITSL-048's, and the relative "Current Year"/"Previous Year"
pair is SK-SPSU-SSLSA-010's balance sheet.
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_MODE = os.path.dirname(_HERE)
_BACKEND = os.path.dirname(os.path.dirname(_MODE))
for _path in (os.path.join(_MODE, "pipeline"), _BACKEND):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from modes.financial_statement.upload import periods  # noqa: E402
from modes.financial_statement.upload.store import UploadedDocument  # noqa: E402


def _doc(tables, financial_year="2023-24", fy_end=2024):
    return UploadedDocument(
        doc_id="d1",
        user_id="u1",
        conversation_id="c1",
        filename="startup-odisha-2023-24.pdf",
        document={"fy_end": fy_end},
        identification={"financial_year": financial_year},
        quality={},
        tables=tables,
    )


def _table(header_cells, table_id="t1", rows=("| (a) Salary Expenses: | 2,10,63,434.00 | 1,60,03,755.00 |",)):
    header = "| " + " | ".join(header_cells) + " |"
    sep = "| " + " | ".join(["---"] * len(header_cells)) + " |"
    return {"table_id": table_id, "table_md": "\n".join([header, sep, *rows])}


# --------------------------------------------------------------------------
# The reported defect
# --------------------------------------------------------------------------

def test_a_single_filing_exposes_both_of_its_own_years():
    """THE regression. One uploaded document, two years of data."""
    document = _doc([_table([
        "PARTICULARS",
        "FIGURES AS AT 31ST MARCH, 2024",
        "FIGURES AS AT 31ST MARCH, 2023",
    ])])

    assert periods.years_available(document) == [2024, 2023]


def test_the_report_says_plainly_that_no_second_upload_is_needed():
    """The refusal was the failure, so the text has to contradict it directly
    rather than merely list the periods and hope."""
    document = _doc([_table([
        "PARTICULARS",
        "FIGURES AS AT 31ST MARCH, 2024",
        "FIGURES AS AT 31ST MARCH, 2023",
    ])])

    report = periods.report(document)

    assert "2024" in report and "2023" in report
    assert "no second upload" in report.lower() or "not required" in report.lower()
    assert "comparative column" in report


def test_the_document_year_is_not_mistaken_for_the_data_coverage():
    """A filing FOR 2023-24 whose tables carry 2024 and 2023 must report two
    periods, not one. This is the exact substitution that caused the bug."""
    document = _doc(
        [_table(["Particulars", "Note No.",
                 "Figures as at 31st March, 2024",
                 "Figures as at 31st March, 2023"])],
        financial_year="2023-24",
    )

    assert document.financial_year == "2023-24"
    assert len(periods.years_available(document)) == 2


# --------------------------------------------------------------------------
# Reading a period out of a header, and refusing to invent one
# --------------------------------------------------------------------------

def test_label_and_reference_columns_are_never_periods():
    """A note reference must not become a year -- that is how "Note No. 12"
    would turn into a period column."""
    for header in ("Particulars", "PARTICULARS", "Note No.", "Notes", "Appendix",
                   "Sr. No.", "Description", "Schedule", "#"):
        assert periods.period_for_header(header) is None, header


def test_a_cin_is_not_read_as_a_year():
    """'U91990OR2021NPL036045' appears on the very document that prompted this.
    A bare four-digit scan of it would yield 1990 and 2021 as 'periods'."""
    assert periods.period_for_header("U91990OR2021NPL036045") is None


def test_a_financial_year_span_resolves_to_its_closing_year():
    assert periods.period_for_header("FY 2023-24").year == 2024
    assert periods.period_for_header("FY 2023-2024").year == 2024
    assert periods.period_for_header("1999-00").year == 2000


def test_a_bare_printed_year_is_absolute():
    period = periods.period_for_header("31st March 2025")
    assert period is not None
    assert period.year == 2025
    assert period.kind == "absolute"


def test_relative_columns_resolve_against_the_documents_own_year():
    """SK-SPSU-SSLSA-010 prints 'Current Year' / 'Previous Year' with no date
    on the column at all. Refusing to report those because they lack a printed
    year would lose a real period of data."""
    current = periods.period_for_header("Current Year", document_year=2023)
    previous = periods.period_for_header("Previous Year", document_year=2023)

    assert current is not None and current.year == 2023
    assert previous is not None and previous.year == 2022
    assert previous.is_comparative
    assert previous.kind == "relative"


def test_a_relative_column_with_no_document_year_is_still_reported():
    period = periods.period_for_header("Previous Year", document_year=None)
    assert period is not None
    assert period.year is None
    assert period.is_comparative


def test_relative_columns_give_a_single_filing_two_periods():
    document = _doc(
        [_table(["Corpus/Capital Fund And Liabilities", "Appendix",
                 "Current Year", "Previous Year"])],
        financial_year="2022-23",
        fy_end=2023,
    )

    assert periods.years_available(document) == [2023, 2022]


# --------------------------------------------------------------------------
# Coverage, and not overstating it
# --------------------------------------------------------------------------

def test_the_same_year_spelled_two_ways_is_one_period():
    document = _doc([
        _table(["Particulars", "Figures as at 31st March, 2024"], table_id="t1"),
        _table(["Particulars", "31st March 2024"], table_id="t2"),
    ])

    coverage = periods.coverage(document)
    assert len(coverage) == 1
    assert coverage[0].period.year == 2024
    assert coverage[0].tables == 2


def test_coverage_counts_how_many_tables_carry_each_period():
    document = _doc([
        _table(["Particulars", "31st March 2024", "31st March 2023"], table_id="t1"),
        _table(["Particulars", "31st March 2024"], table_id="t2"),
    ])

    by_year = {c.period.year: c for c in periods.coverage(document)}
    assert by_year[2024].tables == 2
    assert by_year[2023].tables == 1


def test_a_document_with_only_one_period_says_so_without_promising_a_comparison():
    document = _doc([_table(["Particulars", "31st March 2024"])])

    report = periods.report(document)

    assert periods.years_available(document) == [2024]
    assert "no second upload" not in report.lower()


def test_a_document_whose_headers_yield_nothing_is_reported_as_unknown():
    """Never assume single-period coverage from an absence of evidence -- that
    assumption is the whole bug, in a different disguise."""
    document = _doc([_table(["Particulars", "Note No."])])

    report = periods.report(document)

    assert periods.years_available(document) == []
    assert "NONE" in report
    assert "do not assume" in report.lower()


def test_a_document_with_no_tables_at_all_does_not_crash():
    assert periods.years_available(_doc([])) == []
    assert "NONE" in periods.report(_doc([]))


def test_the_newest_dated_column_is_the_reporting_year_and_older_ones_comparatives():
    document = _doc([_table(["Particulars", "31st March 2024", "31st March 2023",
                             "31st March 2022"])])

    coverage = periods.coverage(document)

    assert [c.period.year for c in coverage] == [2024, 2023, 2022]
    assert not coverage[0].period.is_comparative
    assert coverage[1].period.is_comparative
    assert coverage[2].period.is_comparative
