"""Deriving a section total that the statement never prints.

A Schedule III Division I balance sheet routinely carries no "Total current
assets" and no "Total current liabilities" row -- only one grand ``Total`` per
side. Every liquidity ratio was then reported as uncomputable ("the required
components ... were not found on the face of the extracted statements") while
the components sat right there on the face of the statement, correctly
extracted.

The fixture below is the real OD-SPSU-SO-032 2022-23 balance sheet as the
ingestion pipeline actually extracted it -- including the two things that make
it hard, both of which are OCR artifacts rather than anything unusual about the
filing:

* the ``(2) Current assets`` heading arrived merged onto the tail of another
  row, so it is not at the start of any label;
* the section enumerators are ``(3)`` and ``(4)``, not the ``(1)``/``(2)`` the
  old heading patterns hard-coded.

Run with::

    cd backend
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 venv/Scripts/python -m pytest \
        modes/financial_statement/upload/test_section_totals.py -q
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
E = tools_fs.RatioExtractionEngine


# The real extraction, trimmed to the columns that matter.
OD_BALANCE_SHEET = """| Particulars | Note No. | 31st March, 2023 | 31st March, 2022 |
| --- | --- | --- | --- |
| I. Equity and Liabilities | | | |
| (1) Shareholders' funds | | | |
| (a) Share capital | 1 | 15,00,000.00 | 15,00,000.00 |
| (b) Reserves and surplus | 2 | 38,81,031.72 | -25,460.00 |
| (3) Non-current liabilities | | | |
| (a) Long-term borrowings | | | |
| (b) Refundable Grant | 3 | 15,00,34,815.00 | |
| (c) Long-term provisions | | | |
| (4) Current liabilities | | | |
| (a) Short-term borrowings (b) Trade payables | 4 | 34,011.00 | |
| (c) Other current liabilities | 5 | 22,69,36,581.10 | 25,000.00 |
| (d) Short-term provisions | 6 | 5,11,11,345.00 | |
| Total | | 43,34,97,783.82 | 14,99,540.00 |
| II. Assets | | | |
| (1) Non-current assets | | | |
| (a) Property, Plant and Equipment [and Intangible assets] | 7 | 3,10,253.00 | |
| (b) Non-current investments | | | |
| (c) Long-term loans and advances (d) Other non-current assets (2) Current assets | | | |
| (a) Current investments (b) Cash and cash equivalents | 8 | 25,46,94,119.72 | 14,99,540.00 |
| (c) Trade receivables | 9 | 23,61,395.00 | |
| (d)Short-term loans and advances | 10 | 1,37,30,656.00 | |
| (e) Other current assets | 11 | 16,24,01,360.10 | |
| TOTAL | | 43,34,97,783.82 | 14,99,540.00 |
"""

CURRENT_ASSETS = 433187530.82
CURRENT_LIABILITIES = 278081937.10


def test_current_assets_are_summed_when_no_subtotal_is_printed():
    rows = E.parse_table_md(OD_BALANCE_SHEET)
    value, source = E._derive_section_total(rows, E._CURRENT_ASSETS_SECTION_RE)

    assert value == pytest.approx(CURRENT_ASSETS, abs=0.01)
    # Cross-check against the statement's own arithmetic: total assets less
    # non-current assets must give the same figure.
    assert value == pytest.approx(433497783.82 - 310253.00, abs=0.01)
    assert "derived" in source.label
    assert "Trade receivables" in source.note


def test_current_liabilities_are_summed_when_no_subtotal_is_printed():
    rows = E.parse_table_md(OD_BALANCE_SHEET)
    value, source = E._derive_section_total(rows, E._CURRENT_LIABS_SECTION_RE)

    assert value == pytest.approx(CURRENT_LIABILITIES, abs=0.01)
    assert "Short-term provisions" in source.note


def test_the_current_ratio_is_computable_from_the_derived_totals():
    rows = E.parse_table_md(OD_BALANCE_SHEET)
    ca, _ = E._derive_section_total(rows, E._CURRENT_ASSETS_SECTION_RE)
    cl, _ = E._derive_section_total(rows, E._CURRENT_LIABS_SECTION_RE)
    assert ca / cl == pytest.approx(1.558, abs=0.001)


def test_a_heading_merged_into_another_row_is_still_found():
    """The (2) Current assets heading arrives as the tail of a row about
    long-term loans. Matching only at the start of a label misses it."""
    rows = E.parse_table_md(OD_BALANCE_SHEET)
    merged = [r for r in rows if "long-term loans" in r.label]
    assert merged, "fixture must keep the merged heading row"
    assert E._CURRENT_ASSETS_SECTION_RE.search(merged[0].label)


def test_non_current_sections_are_not_mistaken_for_current_ones():
    """"non-current assets" and "other current assets" both contain the words.
    Neither is the section heading, and matching either would sum the wrong
    block -- silently, and with a plausible-looking answer."""
    assert not E._CURRENT_ASSETS_SECTION_RE.search("(1) non-current assets")
    assert not E._CURRENT_ASSETS_SECTION_RE.search("(e) other current assets")
    assert not E._CURRENT_LIABS_SECTION_RE.search("(3) non-current liabilities")
    assert E._CURRENT_ASSETS_SECTION_RE.search("(2) current assets")
    assert E._CURRENT_LIABS_SECTION_RE.search("(4) current liabilities")


def test_a_withheld_component_refuses_the_sum():
    """THE safety property. A sum across a withheld figure is wrong by exactly
    the missing amount and looks entirely plausible -- worse than declining."""
    withheld = OD_BALANCE_SHEET.replace(
        "| 22,69,36,581.10 |",
        '| [unreadable: page 1, table t1, row "Other current liabilities", col "2023"] |',
    )
    rows = E.parse_table_md(withheld)
    value, source = E._derive_section_total(rows, E._CURRENT_LIABS_SECTION_RE)

    assert value is None, "summed over a withheld figure"
    assert source is None


def test_a_recovered_component_still_refuses_the_sum():
    """THE regression this feature must never reopen. A `[recovered ...]`
    marker shows a second reader's figure that the arithmetic did NOT
    confirm, so it is exactly as un-computable as a plain `[unreadable: ...]`
    one -- Row.withheld must be True for both. If this test ever fails, the
    fix shipped a section total that is silently wrong by exactly the
    recovered amount and presents it as complete, which is the one failure
    mode the whole recovery feature exists to avoid."""
    recovered = OD_BALANCE_SHEET.replace(
        "| 22,69,36,581.10 |",
        '| [recovered 22,69,36,581.10; second read, confidence medium, '
        'column total does not confirm it: page 1, table t1, '
        'row "Other current liabilities", col "2023"] |',
    )
    rows = E.parse_table_md(recovered)
    row = next(r for r in rows if "other current liabilities" in r.label)
    assert row.withheld is True
    assert row.recovered is True

    value, source = E._derive_section_total(rows, E._CURRENT_LIABS_SECTION_RE)
    assert value is None, "summed over a recovered-but-unconfirmed figure"
    assert source is None


def test_a_printed_subtotal_still_wins():
    """The derivation is a fallback only. A statement that prints its own
    subtotal must be read, not recomputed -- the printed figure is the
    audited one, and any difference between them is a finding rather than
    something to paper over."""
    printed = """| Particulars | Note No. | 2023 |
| --- | --- | --- |
| (2) Current assets | | |
| (a) Inventories | 5 | 100.00 |
| (b) Trade receivables | 6 | 200.00 |
| Total current assets | | 999.00 |
| Total assets | | 1,500.00 |
"""
    rows = E.parse_table_md(printed)
    values, _sources = E._extract_direct(
        rows, E.BALANCE_SHEET_LABELS, E.STATEMENT_BALANCE_SHEET
    )
    # The printed 999 is what the direct lookup finds; the components sum to
    # 300. The engine must prefer the printed one.
    assert values.get("current_assets_total_direct") == pytest.approx(999.00)


def test_total_assets_is_resolved_by_position_when_the_row_says_only_total():
    """Division I closes each side with a bare "Total" and never writes "Total
    assets". Which one is which can only be decided by where it sits -- and on
    this filing OCR split the label and its figures across two rows."""
    rows = E.parse_table_md(OD_BALANCE_SHEET)
    value, source = E._find_bare_total_after(rows, E._ASSETS_SIDE_RE, "assets")

    assert value == pytest.approx(433497783.82, abs=0.01)
    assert "assets side" in source.note


def test_total_equity_and_liabilities_is_resolved_by_position():
    """The mirror of total_assets's own fallback, on the OTHER side of the
    same balance sheet. Verified missing on the real filing: total_assets
    resolved this way while total_equity_and_liabilities, one balance-sheet
    side over, silently did not, because the fallback had only ever been
    built for the assets side."""
    rows = E.parse_table_md(OD_BALANCE_SHEET)
    value, source = E._find_bare_total_after(
        rows, E._EQUITY_LIABILITIES_SIDE_RE, "equity and liabilities"
    )

    assert value == pytest.approx(433497783.82, abs=0.01)
    assert "equity and liabilities side" in source.note


def test_equity_and_liabilities_total_is_the_first_one_not_the_second():
    """Proof this resolves by POSITION and not by coincidence: the two totals
    in the real fixture happen to be equal (the balance sheet balances), so a
    value-only assertion could pass even if this picked the assets-side Total
    by mistake. A fixture where the two sides genuinely differ is the only
    way to catch that."""
    md = """| Particulars | Note | 2023 |
| --- | --- | --- |
| I. Equity and Liabilities | | |
| (1) Shareholders' funds | | |
| (a) Share capital | 1 | 100.00 |
| Total | | 100.00 |
| II. Assets | | |
| (1) Non-current assets | | |
| (a) Property, Plant and Equipment | 2 | 250.00 |
| TOTAL | | 250.00 |
"""
    rows = E.parse_table_md(md)
    value, _ = E._find_bare_total_after(
        rows, E._EQUITY_LIABILITIES_SIDE_RE, "equity and liabilities"
    )
    assert value == pytest.approx(100.00)


def test_the_equity_side_total_is_not_mistaken_for_total_assets():
    """The first bare Total closes equity and liabilities. Picking it would be
    right only by accident -- on a balance sheet that balances."""
    rows = E.parse_table_md(OD_BALANCE_SHEET)
    marker = [i for i, r in enumerate(rows) if E._ASSETS_SIDE_RE.search(r.label)]
    assert marker, "fixture must contain the assets-side heading"
    first_total = next(i for i, r in enumerate(rows)
                       if E._BARE_TOTAL_RE.match((r.label or "").strip()))
    assert first_total < marker[0], "fixture must have a Total before the assets side"

    # The resolver must skip it and take the one after the marker.
    value, _ = E._find_bare_total_after(rows, E._ASSETS_SIDE_RE, "assets")
    assert value is not None


def test_plain_share_capital_is_read_as_equity_share_capital():
    """Division I writes "Share capital"; only Ind AS reliably says "Equity
    share capital"."""
    rows = E.parse_table_md(OD_BALANCE_SHEET)
    values, _ = E._extract_direct(rows, E.BALANCE_SHEET_LABELS, E.STATEMENT_BALANCE_SHEET)
    assert values.get("equity_share_capital") == pytest.approx(1500000.00)


def test_preference_capital_is_not_read_as_equity_capital():
    """The guard on the widened pattern. Preference capital is a different
    figure and must not be picked up by a bare "share capital" match."""
    md = """| Particulars | Note | 2023 |
| --- | --- | --- |
| (1) Shareholders' funds | | |
| (a) Preference share capital | 1 | 700.00 |
| (b) Reserves and surplus | 2 | 50.00 |
| Total | | 750.00 |
"""
    rows = E.parse_table_md(md)
    values, _ = E._extract_direct(rows, E.BALANCE_SHEET_LABELS, E.STATEMENT_BALANCE_SHEET)
    assert values.get("equity_share_capital") is None


def test_a_withheld_total_row_is_not_used():
    withheld = OD_BALANCE_SHEET.replace(
        "| TOTAL | | 43,34,97,783.82 | 14,99,540.00 |",
        '| TOTAL | | [unreadable: page 1, table t1, row "TOTAL", col "2023"] | |',
    )
    rows = E.parse_table_md(withheld)
    value, _ = E._find_bare_total_after(rows, E._ASSETS_SIDE_RE, "assets")
    assert value is None


def test_a_recovered_total_row_is_not_used():
    """A `[recovered ...]` TOTAL is exactly as un-usable as a withheld one --
    and, worse than a hole, accepting it here would also make the function
    adopt the FOLLOWING row's numbers as the total (the same failure mode
    `test_a_withheld_total_row_is_not_used` guards against)."""
    recovered = OD_BALANCE_SHEET.replace(
        "| TOTAL | | 43,34,97,783.82 | 14,99,540.00 |",
        '| TOTAL | | [recovered 43,34,97,783.82; second read, confidence low, '
        'column total does not confirm it: page 1, table t1, row "TOTAL", col "2023"] | |',
    )
    rows = E.parse_table_md(recovered)
    value, _ = E._find_bare_total_after(rows, E._ASSETS_SIDE_RE, "assets")
    assert value is None


def test_total_equity_is_derived_when_shareholders_funds_has_no_subtotal():
    """"(1) Shareholders' funds" (share capital + reserves and surplus)
    routinely carries no printed subtotal either -- same disease as current
    assets/liabilities, same fix."""
    rows = E.parse_table_md(OD_BALANCE_SHEET)
    value, source = E._derive_section_total(rows, E._SHAREHOLDERS_FUNDS_SECTION_RE)

    assert value == pytest.approx(1500000.00 + 3881031.72, abs=0.01)
    assert "Reserves and surplus" in source.note


def test_surplus_deficit_terminology_resolves_profit_for_period():
    """Section 8 companies (non-profits) file on the same Schedule III format
    but call this line "Surplus/(Deficit) for the period" -- Startup Odisha's
    actual P&L. Same figure ratios need as "profit for the period"."""
    md = """| Particulars | Note | 2023 | 2022 |
| --- | --- | --- | --- |
| I. Revenue from operations | 12 | 15,98,04,608.63 | - |
| XI. Surplus/(Deficit) for the period (IX-X) | | 35,96,238.72 | -25,460.00 |
"""
    rows = E.parse_table_md(md)
    values, _ = E._extract_direct(rows, E.PROFIT_LOSS_LABELS, E.STATEMENT_PROFIT_LOSS)
    assert values.get("profit_for_period") == pytest.approx(3596238.72)


def test_merged_current_investments_row_does_not_leak_cash_into_investments():
    """"(a) Current investments (b) Cash and cash equivalents" is real merged
    OCR output. The visible figure belongs to cash (Current investments was
    blank on the scan); before this fix current_investments AND cash_and_bank
    both took it, which double-counted cash in every ratio that adds them
    (Cash Ratio, Basic Defense Interval) and made Capital Employed go negative
    by subtracting the same cash twice."""
    rows = E.parse_table_md(OD_BALANCE_SHEET)
    values, _ = E._extract_direct(rows, E.BALANCE_SHEET_LABELS, E.STATEMENT_BALANCE_SHEET)
    assert values.get("current_investments") is None
    assert values.get("cash_and_bank") == pytest.approx(254694119.72)

    nc_bounds = E._section_bounds(rows, E._NON_CURRENT_ASSETS_SECTION_RE)
    c_bounds = E._section_bounds(rows, E._CURRENT_ASSETS_SECTION_RE)
    cash_exclude = ["cash and cash equivalents", "cash & bank"]
    nc_inv, _ = E._sum_section_rows(rows, ["investments"], nc_bounds, "nc", exclude=cash_exclude)
    c_inv, _ = E._sum_section_rows(rows, ["investments"], c_bounds, "c", exclude=cash_exclude)
    assert nc_inv is None
    assert c_inv is None


def test_merged_short_term_borrowings_row_does_not_leak_trade_payables():
    """"(a) Short-term borrowings (b) Trade payables" is the same document's
    other merged row. The value is Trade payables (Short-term borrowings was
    blank); an unguarded "borrowings" match would misattribute it."""
    rows = E.parse_table_md(OD_BALANCE_SHEET)
    long_term, short_term, _, _ = E._extract_borrowings(rows)
    assert long_term is None
    assert short_term is None, "picked up Trade payables' value as a borrowing"


def test_current_assets_section_bounds_stop_at_the_merged_heading():
    """The heading arrives merged onto row (2)'s predecessor. Non-current
    assets must not run past it and absorb the whole current-assets section —
    which is exactly how the cash/investments collision above first
    surfaced: a runaway section boundary handed the merged row to the WRONG
    section's summation entirely, before the exclude guards even mattered."""
    rows = E.parse_table_md(OD_BALANCE_SHEET)
    nc_bounds = E._section_bounds(rows, E._NON_CURRENT_ASSETS_SECTION_RE)
    c_bounds = E._section_bounds(rows, E._CURRENT_ASSETS_SECTION_RE)

    assert nc_bounds is not None and c_bounds is not None
    assert nc_bounds[1] <= c_bounds[0], "non-current assets ran into the current-assets section"

    # "non-current investments" legitimately contains "current investments" as
    # a substring, so that phrase alone can't distinguish the sections —
    # "cash and cash equivalents" can, since nothing in the non-current-assets
    # rows mentions cash.
    nc_labels = [rows[i].label for i in range(*nc_bounds)]
    assert not any("cash and cash equivalents" in lbl for lbl in nc_labels), \
        "the current-assets section's own row leaked into non-current assets"


def test_a_bare_current_pattern_does_not_match_inside_non_current():
    """The regression that surfaced once heading matching moved from
    prefix-only to substring-anywhere: "current assets" is a literal substring
    of "non-current assets", and a bare pattern list had no way to tell them
    apart. The anchored regexes must."""
    assert not E._CURRENT_ASSETS_SECTION_RE.search("(1) Non-current assets")
    assert not E._CURRENT_LIABS_SECTION_RE.search("(3) Non-current liabilities")
    assert E._NON_CURRENT_ASSETS_SECTION_RE.search("(1) Non-current assets")
    assert E._NON_CURRENT_LIABS_SECTION_RE.search("(3) Non-current liabilities")


def test_a_section_with_nothing_in_it_yields_nothing():
    empty = """| Particulars | 2023 |
| --- | --- |
| (2) Current assets | |
| Total | 500.00 |
"""
    rows = E.parse_table_md(empty)
    value, source = E._derive_section_total(rows, E._CURRENT_ASSETS_SECTION_RE)
    assert value is None and source is None


# --------------------------------------------------------------------------
# EPS: recovered even when the "Earnings per share" heading itself was
# dropped by extraction, without ever guessing which of Basic/Diluted a
# lone value belongs to.
# --------------------------------------------------------------------------

# The real OD-SPSU-SO-032 2022-23 P&L (Income & Expenditure Account) tail, as
# extracted: the "XII. Earnings per equity share:" heading itself did not
# survive extraction at all -- not merged onto another row, simply absent --
# so the OLD anchored-only search found nothing and both EPS figures were
# silently discarded despite one being printed right there in the table.
EPS_NO_HEADING = """| Particulars | Note | 2023 | 2022 |
| --- | --- | --- | --- |
| XI. Surplus/(Deficit) for the period (IX-X) | | 35,96,238.72 | -25,460.00 |
| (1) Basic | | | |
| (2) Diluted | | 23.97 | -0.17 |
"""


def test_eps_is_recovered_when_the_heading_row_was_dropped_entirely():
    """The heading-anchored search alone finds nothing here -- there is no
    "Earnings per share" row left to anchor to. The fallback must still
    recover whichever of Basic/Diluted actually carries a value, reading it
    exactly as printed rather than discarding it for lack of an anchor."""
    rows = E.parse_table_md(EPS_NO_HEADING)
    basic, diluted, basic_src, diluted_src = E._extract_eps(rows)

    assert basic is None  # the (1) Basic row genuinely carries no value
    assert diluted == pytest.approx(23.97)
    assert diluted_src is not None
    assert "no 'Earnings per share' heading" in diluted_src.note


def test_a_recovered_eps_row_is_skipped():
    """EPS is quoted verbatim into an answer, so a figure the arithmetic
    never confirmed must never reach it -- the fallback's `if row.withheld:
    continue` guard must fire for a `[recovered ...]` Diluted row exactly as
    it would for a plain `[unreadable: ...]` one."""
    recovered = EPS_NO_HEADING.replace(
        "| (2) Diluted | | 23.97 | -0.17 |",
        '| (2) Diluted | | [recovered 23.97; second read, confidence low, '
        'it is the only read of this cell: page 1, table t1, row "(2) Diluted", '
        'col "2023"] | -0.17 |',
    )
    rows = E.parse_table_md(recovered)
    basic, diluted, _basic_src, diluted_src = E._extract_eps(rows)
    assert diluted is None
    assert diluted_src is None


def test_the_heading_anchored_path_still_wins_when_a_heading_is_present():
    """The fallback must never fire, let alone override, when the anchored
    search already has a heading to work from -- it exists only to cover the
    heading's absence, not to second-guess a successful anchored read."""
    md = """| Particulars | Note | 2023 |
| --- | --- | --- |
| XI. Surplus/(Deficit) for the period | | 100.00 |
| Earnings per equity share: | | |
| (1) Basic | | 12.34 |
| (2) Diluted | | 12.34 |
"""
    rows = E.parse_table_md(md)
    basic, diluted, basic_src, diluted_src = E._extract_eps(rows)
    assert basic == pytest.approx(12.34)
    assert diluted == pytest.approx(12.34)
    assert "sub-row beneath" in basic_src.note


def test_a_bare_eps_row_pattern_does_not_match_unrelated_rows():
    """The fallback's whole safety rests on the pattern being unambiguous on
    its own -- it must not fire on a row that merely mentions "basic" or
    "diluted" as part of something else."""
    assert not E._BARE_EPS_ROW_RE["basic"].match("basic salary")
    assert not E._BARE_EPS_ROW_RE["basic"].match("basic earnings per share")
    assert not E._BARE_EPS_ROW_RE["diluted"].match("diluted weighted average shares")
    assert E._BARE_EPS_ROW_RE["basic"].match("(1) basic")
    assert E._BARE_EPS_ROW_RE["diluted"].match("(2) diluted")
    assert E._BARE_EPS_ROW_RE["basic"].match("basic")


def test_eps_fallback_finds_nothing_when_neither_row_has_a_value():
    md = """| Particulars | 2023 |
| --- | --- |
| (1) Basic | |
| (2) Diluted | |
"""
    rows = E.parse_table_md(md)
    basic, diluted, basic_src, diluted_src = E._extract_eps(rows)
    assert basic is None and diluted is None
