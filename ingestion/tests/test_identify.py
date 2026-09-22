"""Identification tests, checked against the sample corpus.

The ``data/`` directory layout encodes the entity and financial year in the path
(``data/MH-CPSU-IAM-052/2022-23/MH-CPSU-ITSL-048_2022-23_SFS_....pdf``). That
layout is used **here, as ground truth to test against** -- and nowhere in the
service itself. See the module docstring in ``app/identify.py``.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.identify import (                              # noqa: E402
    _fy_from_reporting_date,
    classify_statement,
    detect_flavour,
    detect_framework,
    detect_units,
    identify,
)


# --------------------------------------------------------------------------
# Financial year
# --------------------------------------------------------------------------

def test_indian_fy_boundary():
    """The Indian year runs 1 April to 31 March, so a statement 'as at 31 March
    2023' closes FY 2022-23. Getting this backwards mislabels every upload by a
    year and silently misaligns multi-year comparison."""
    assert _fy_from_reporting_date(31, 3, 2023) == "2022-23"
    assert _fy_from_reporting_date(31, 3, 2022) == "2021-22"
    assert _fy_from_reporting_date(1, 4, 2022) == "2022-23"
    assert _fy_from_reporting_date(31, 3, 2000) == "1999-00"


# Transcribed from data/MH-CPSU-IAM-052/2022-23/..._SFS_....pdf, pages 3, 5, 10.
MH_2022_23 = [
    """IDBI Trusteeship Services Limited
Universal Insurance Building, Ground Floor, Sir P M Road, Fort, Mumbai - 400 001
Statement of changes in equity as at 31st March 2023
A. Equity share capital""",
    """IDBI Trusteeship Services Limited
Notes to balance sheet for the year ended 31st March, 2023
Note 10- Share capital
| Particulars | For the year ended 31st March 2022 | For the year ended 31st March 2023 |""",
    """IDBI Trusteeship Services Limited
Balance Sheet as at 31st March, 2023
(Amount in '000)
prepared in accordance with Indian Accounting Standards (Ind AS)
Other Comprehensive Income for the year""",
]

# Transcribed from data/OD-SPSU-SO-032/2021-22/..._SFS_....PDF, pages 6 and 10.
OD_2021_22 = [
    """Startup Odisha
(A Company Registered under section 8 of The Companies Act, 2013)
Balance Sheet as at 31st March, 2022
(Amount in INR)""",
    """We have audited the internal financial controls over financial reporting of
M/S. STARTUP ODISHA as on 31st March 2022 in conjunction with our audit of the
standalone financial statements. Significant Accounting Policies.
Accounting Standard - 2 per the Companies (Accounting Standards) Rules""",
]


def test_fy_matches_directory_ground_truth():
    assert identify(MH_2022_23).financial_year == "2022-23"
    assert identify(OD_2021_22).financial_year == "2021-22"


def test_fy_prefers_the_statement_title_over_a_comparative_column():
    """The comparative column header carries the prior year in exactly the same
    form as the current one, so a naive scan is a coin flip. Page 2 of the MH
    fixture has '31st March 2022' in a column header and '31st March, 2023' in
    the title; the title must win."""
    ident = identify(MH_2022_23)
    assert ident.financial_year == "2022-23"
    assert ident.fy_confidence == "high"
    assert any("statement title" in e for e in ident.fy_evidence)


def test_entity_comes_from_the_repeated_page_header():
    assert identify(MH_2022_23).entity_name == "IDBI Trusteeship Services Limited"


# --------------------------------------------------------------------------
# Framework
# --------------------------------------------------------------------------

def test_ind_as_detected_from_policy_socie_and_oci():
    ident = identify(MH_2022_23)
    assert ident.framework == "Ind AS"
    assert ident.framework_division == "II"
    assert ident.framework_confidence == "high"


def test_as_detected_for_a_division_i_filing():
    ident = identify(OD_2021_22)
    assert ident.framework == "AS"
    assert ident.framework_division == "I"


def test_as_short_form_is_case_sensitive():
    """'AS 22' is a standard; 'as at' is English. Matching the short form
    case-insensitively fires on almost every page of every filing."""
    assert detect_framework("Balance Sheet as at 31 March 2023, as on that date")[0] is None
    assert detect_framework("Deferred tax per AS-22 and the Companies (Accounting Standards) Rules")[0] == "AS"


def test_banks_and_insurers_do_not_use_schedule_iii():
    """Running Schedule III checks against an IRDAI or BR Act filing produces a
    page of findings that are all artefacts of the wrong rulebook."""
    bank, _, _, _, conflicts = detect_framework(
        "presented under the Third Schedule to the Banking Regulation Act, 1949"
    )
    assert bank is not None and "Banking" in bank
    assert conflicts

    ins, _, _, _, _ = detect_framework("prepared in the formats prescribed by IRDAI")
    assert ins is not None and "Insurance" in ins


# --------------------------------------------------------------------------
# Statement classification and units
# --------------------------------------------------------------------------

def test_statement_types_match_the_existing_tool_vocabulary():
    """These four strings are the keys of
    ``ComplianceTools._STATEMENT_TITLE_KEYWORDS``. Emitting anything else means
    ``_find_statement_tables`` finds no statements at all."""
    assert classify_statement("Balance Sheet as at 31st March, 2022") == "balance_sheet"
    assert classify_statement("Statement of Profit and Loss") == "profit_loss"
    assert classify_statement("Cash Flow Statement") == "cash_flow"
    assert classify_statement("Statement of changes in equity as at 31st March 2023") == "statement_of_equity"
    # A Section 8 (not-for-profit) company files this INSTEAD of a Statement of
    # Profit and Loss, because it has no profit to report -- verified on a real
    # corpus filing (Startup Odisha) whose materiality/tie-out tools came back
    # empty for "Revenue from operations" despite the row being extracted
    # cleanly, because this table was never tagged financial_stmt_type
    # "profit_loss" at all and so was invisible to every tool that looks for it.
    assert classify_statement("Income & Expenditure Account for the year ended 31st March 2023") == "profit_loss"
    assert classify_statement("Income and Expenditure Account") == "profit_loss"
    # A note schedule is not a face statement.
    assert classify_statement("Note 1 (a) - Property, plant and equipment") is None


def test_units_are_read_but_never_guessed():
    assert detect_units("(Amount in '000)") == ("thousand", None)
    assert detect_units("(Amount in INR)") == (None, "INR")
    assert detect_units("Rs. in crore") == ("crore", "INR")
    # The important one: silence stays silence. UnitResolver is explicit that a
    # guessed scale is worse than an absent one, because the same digits mean
    # different things three orders of magnitude apart.
    assert detect_units("Particulars | Amount") == (None, None)


# --------------------------------------------------------------------------
# Entity detection, against what real OCR output looks like
# --------------------------------------------------------------------------

# Each string is one PAGE, as docling exports it: a markdown heading for the
# running header, then the lines beneath it.
MH_PAGES = ["""## IDBI Trusteeship Services Ltd
Universal Insurance Building, Ground Floor, Sir P M Road, Fort, Mumbai - 400 001
Balance Sheet as at 31st March, 2023"""] * 3

OD_PAGES = ["""## Startup Odisha
(A Company Registered under section 8 of The Companies Act, 2013)
2nd Floor, Tower-A, Odisha Startup Incubation Centre(O-Hub)"""] * 3

AUDITOR_PAGES = ["""## K SWAIN & CO
Chartered Accountants
Annexure "A" to the Independent Auditors' Report"""] * 2


def test_entity_from_a_repeated_header_with_a_legal_suffix():
    name, evidence = identify(MH_PAGES).entity_name, None
    assert name == "IDBI Trusteeship Services Ltd"


def test_entity_without_a_legal_suffix_is_still_found():
    """OD-SPSU-SO-032 is a section 8 company registered simply as 'Startup
    Odisha'. Requiring a legal suffix leaves every such entity unidentified, and
    section 8 companies are a real part of this corpus."""
    assert identify(OD_PAGES).entity_name == "Startup Odisha"


def test_a_parenthetical_description_is_not_the_entity():
    """'(A Company Registered under section 8 of The Companies Act, 2013)' sits
    under the name on every OD sheet and matches the legal-suffix rule on the
    word 'Company'. It must not outrank the heading above it."""
    assert "Registered under" not in (identify(OD_PAGES).entity_name or "")


def test_the_auditors_letterhead_is_not_the_entity():
    """An audit firm's letterhead is two lines -- the firm, then 'Chartered
    Accountants'. Filtering only the same line returns the auditor as the
    reporting entity, which is wrong in a way that looks entirely plausible."""
    assert identify(AUDITOR_PAGES).entity_name is None
    assert identify(AUDITOR_PAGES + OD_PAGES).entity_name == "Startup Odisha"


# --------------------------------------------------------------------------
# Statement flavour (standalone vs consolidated)
# --------------------------------------------------------------------------

def test_a_negated_consolidation_disclaimer_is_read_as_standalone():
    """The most common real filing has no subsidiaries at all. It says so once,
    using the word "consolidated" -- "the Company does not have any subsidiary
    ... and hence consolidated financial statements have not been prepared" --
    and never uses the word "standalone" anywhere, because there is nothing to
    distinguish from. A bare word count reads that single sentence as evidence
    FOR "consolidated" and returns exactly the wrong flavour."""
    text = (
        "Balance Sheet as at 31st March, 2025\n"
        "The Company does not have any subsidiary, associate or joint venture "
        "and hence consolidated financial statements have not been prepared."
    )
    flavour, confidence, evidence = detect_flavour(text)
    assert flavour == "standalone"
    assert confidence != "low" or "negated" in evidence[0]


def test_a_negation_wrapped_across_lines_is_still_recognised():
    """Docling's page markdown wraps prose at the line, not at the sentence --
    a negation phrase and the word "consolidated" it governs routinely land on
    different lines of the SAME sentence. An earlier version of this fix split
    on bare "\\n" as a sentence boundary and silently stopped recognising the
    negation the moment it was word-wrapped, which is the normal case, not the
    exception."""
    text = (
        "Balance Sheet as at 31st March, 2025\n"
        "The Company does not have any subsidiary, associate or joint venture and\n"
        "hence provisions relating to consolidated financial statements are not\n"
        "applicable to the Company."
    )
    flavour, _, _ = detect_flavour(text)
    assert flavour == "standalone"


def test_a_genuinely_consolidated_filing_is_not_flipped_by_the_negation_fix():
    text = (
        "Consolidated Balance Sheet as at 31st March, 2025\n"
        "The Group comprises the Company and its three subsidiaries.\n"
        "Consolidated Statement of Profit and Loss for the year ended 31st March, 2025"
    )
    flavour, confidence, _ = detect_flavour(text)
    assert flavour == "consolidated"
    assert confidence == "high"


def test_a_statement_title_flavour_word_outranks_body_prose():
    """'Consolidated Balance Sheet' in a heading is the entity's own
    declaration and must win over incidental body-text mentions of the other
    word, exactly as detect_financial_year prefers a title date over a
    comparative-column date."""
    text = (
        "Consolidated Balance Sheet as at 31st March, 2025\n"
        "This standalone note is presented for information only. "
        "This standalone note is presented for information only."
    )
    flavour, confidence, evidence = detect_flavour(text)
    assert flavour == "consolidated"
    assert confidence == "medium"
    assert any("statement title" in e for e in evidence)


def test_two_titled_mentions_reach_high_confidence():
    text = (
        "Standalone Balance Sheet as at 31st March, 2025\n"
        "Standalone Statement of Profit and Loss for the year ended 31st March, 2025"
    )
    flavour, confidence, _ = detect_flavour(text)
    assert flavour == "standalone"
    assert confidence == "high"


def test_no_flavour_wording_at_all_is_reported_as_unknown_not_guessed():
    flavour, confidence, evidence = detect_flavour("Balance Sheet as at 31st March, 2025")
    assert flavour is None
    assert confidence == "low"
    assert evidence


def test_flavour_reaches_identify():
    text = "The Company does not have any subsidiary and hence consolidated financial statements have not been prepared."
    ident = identify([text])
    assert ident.statement_flavour == "standalone"
    assert ident.flavour_evidence


def test_a_note_heading_is_not_the_statement_it_mentions():
    """'Notes to balance sheet for the year ended 31st March, 2023' heads a
    share-capital note page in this corpus. Classifying it as the balance sheet
    puts the wrong table in front of every tie-out check."""
    from app.identify import classify_statement_from_page
    assert classify_statement_from_page(
        "## IDBI Trusteeship Services Ltd\nNotes to balance sheet for the year ended 31st March, 2023"
    ) is None
    assert classify_statement_from_page(
        "## IDBI Trusteeship Services Ltd\nBalance Sheet as at 31st March, 2023"
    ) == "balance_sheet"
    # Same guard, new pattern: a notes page referencing the Income and
    # Expenditure Account must not be classified as the statement itself.
    assert classify_statement_from_page(
        "## Startup Odisha\nNotes to Income and Expenditure Account for the year ended 31st March, 2023"
    ) is None
    assert classify_statement_from_page(
        "## Startup Odisha\nIncome & Expenditure Account for the year ended 31st March 2023"
    ) == "profit_loss"


# ---------------------------------------------------------------------------
# A 120-page annual report mentions old financial years far more often than the
# current one (tax disputes, dividend history). Real case: "SFS BS FY 24-25.pdf"
# was labelled 2017-18 with high confidence.
# ---------------------------------------------------------------------------

def test_old_years_in_notes_do_not_outvote_the_reporting_year():
    from app.identify import detect_financial_year

    pages = [
        "## Balance Sheet as at 31st March, 2025\n| Particulars | 31 March 2025 | 31 March 2024 |",
        "| Income tax Act, 1961 | Income Tax | FY 2017-18 | Pending |\n"
        "| Income tax Act, 1961 | Income Tax | FY 2017-18 | Pending |\n"
        "| Income tax Act, 1961 | Interest | FY 2017-18 | Pending |",
        "Disputes were raised for financial year 2017-18 and financial year 2017-18 again.\n"
        "The final dividend paid during the financial year 2017-18 was declared earlier.",
    ]
    fy, confidence, _ = detect_financial_year(pages)
    assert fy == "2024-25"
    assert confidence in ("high", "medium")


def test_prose_only_mentions_never_give_high_confidence():
    from app.identify import detect_financial_year

    fy, confidence, evidence = detect_financial_year(
        ["Dividends were paid in financial year 2017-18.", "See financial year 2018-19 for details."]
    )
    assert fy == "2018-19" and confidence == "low"
    assert "no dated statement heading" in evidence[0]


def test_an_income_and_expenditure_heading_with_a_year_still_counts():
    from app.identify import detect_financial_year

    fy, confidence, _ = detect_financial_year(
        ["## Income & Expenditure Account for FY 2023-24 From 1st April, 2023 to 31st March,2024"]
    )
    assert fy == "2023-24" and confidence in ("high", "medium")
