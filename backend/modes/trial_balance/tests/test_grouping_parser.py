"""Standalone tests for the grouping-file (FSLI/chart-of-accounts) parser
rigidity fix (no pytest dependency — run directly:
`python backend/tests/test_grouping_parser.py`, or inside the backend
container: `docker compose exec backend python /app/tests/test_grouping_parser.py`).

Covers:
  - Flat two-column layout with non-exact-match headers ("GL Code"/"Mapped To").
  - Line-item-heading layout (FSLI name as its own row, accounts listed below
    it until the next heading), per the user's worked example.
  - A genuinely malformed file — confirms a helpful GroupingParseError with a
    non-empty preview is raised instead of a bare, undiagnosable failure.
  - Regression: an existing well-formed flat file (exact original headers)
    still parses identically to before this fix.
"""
import io
import pathlib
import sys

# PATCHED for this integration: the vendored `yukta_rag` package lives under
# this mode's pipeline/ dir, not at a repo-root "backend". Resolved from
# __file__ so the file runs directly from any cwd and under pytest alike.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "pipeline"))

import pandas as pd

from yukta_rag.audit.audit_grouping import GroupingParseError, parse_grouping_file


def _xlsx_bytes(rows: list[list]) -> bytes:
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, header=False, index=False, engine="openpyxl")
    return buf.getvalue()


def test_flat_format_non_exact_headers():
    """Fixture (i) — 'GL Code'/'Mapped To' instead of 'Account Code'/'FSLI'."""
    rows = [
        ["GL Code", "Mapped To"],
        ["2010100", "Trade Payables"],
        ["2010200", "Trade Payables"],
        ["3010210", "Property, Plant and Equipment"],
    ]
    override = parse_grouping_file(_xlsx_bytes(rows))
    assert override["2010100"] == "Trade Payables"
    assert override["2010200"] == "Trade Payables"
    assert override["3010210"] == "Property, Plant and Equipment"
    print("PASS: flat format resolves with non-exact synonym headers ('GL Code'/'Mapped To')")


def test_flat_format_header_not_on_row_zero():
    """Header-row rigidity fix — a title row sits above the real header."""
    rows = [
        ["GAIL (India) Limited — Chart of Accounts FY2024-25", "", ""],
        ["", "", ""],
        ["Account Code", "Account Name", "FSLI"],
        ["5410046", "Salaries and Wages", "Employee Benefits Expense"],
        ["5410047", "Bonus", "Employee Benefits Expense"],
    ]
    override = parse_grouping_file(_xlsx_bytes(rows))
    assert override["5410046"] == "Employee Benefits Expense"
    assert override["Bonus"] == "Employee Benefits Expense"
    print("PASS: header row found even when not row 0 (title row above it)")


def test_line_item_heading_format():
    """Fixture (ii) — the user's exact worked example: FSLI name as its own
    row, member accounts listed underneath until the next heading."""
    rows = [
        ["Account Code", "Account Name"],
        ["", "Trade Payables"],
        ["GAIL/2010100", "Sundry Creditors - Local"],
        ["GAIL/2010200", "Sundry Creditors - Import"],
        ["", "Property, Plant and Equipment"],
        ["GAIL/3010210", "GB-Plant & Machinery"],
        ["GAIL/3010220", "GB-Building"],
    ]
    override = parse_grouping_file(_xlsx_bytes(rows))
    assert override["GAIL/2010100"] == "Trade Payables"
    assert override["GAIL/2010200"] == "Trade Payables"
    assert override["Sundry Creditors - Local"] == "Trade Payables"
    assert override["GAIL/3010210"] == "Property, Plant and Equipment"
    assert override["GAIL/3010220"] == "Property, Plant and Equipment"
    print("PASS: line-item-heading layout correctly assigns accounts to their heading")


def test_malformed_file_raises_with_preview():
    """Fixture (iii) — genuinely malformed: no recognizable code/name/group
    columns at all. Must raise GroupingParseError with a usable preview,
    not a bare unhelpful ValueError."""
    rows = [
        ["Random Col A", "Random Col B", "Random Col C"],
        ["foo", "bar", "baz"],
        ["1", "2", "3"],
    ]
    try:
        parse_grouping_file(_xlsx_bytes(rows))
        raise AssertionError("expected GroupingParseError, got success")
    except GroupingParseError as exc:
        assert exc.preview is not None
        assert "sheets" in exc.preview
        assert len(exc.preview["sheets"]) >= 1
        assert len(exc.preview["sheets"][0]["rows"]) >= 1
        print("PASS: malformed file raises GroupingParseError with a non-empty row preview")


def test_regression_existing_wellformed_flat_file():
    """Regression — the exact original headers ('Account Code'/'Account
    Name'/'FSLI') at row 0 must still resolve identically."""
    rows = [
        ["Account Code", "Account Name", "FSLI"],
        ["1001", "Cash in hand", "Cash and Cash Equivalents"],
        ["2001", "Trade Payables - Local", "Trade Payables"],
    ]
    override = parse_grouping_file(_xlsx_bytes(rows))
    assert override["1001"] == "Cash and Cash Equivalents"
    assert override["Cash in hand"] == "Cash and Cash Equivalents"
    assert override["2001"] == "Trade Payables"
    print("PASS: pre-existing well-formed flat file still parses identically")


def test_tb_matching_headerless_file():
    """§0 — a grouping file with NO recognizable headers at all (meaningless
    'x'/'y'/'z' column labels). Must still resolve when the real trial
    balance's accounts are supplied, by matching cell values directly."""
    rows = [
        ["x", "y", "z"],
        ["9001", "Cash in hand", "Cash and Cash Equivalents"],
        ["9002", "Bank account - Current", "Cash and Cash Equivalents"],
        ["9003", "Bank account - Deposits", "Cash and Cash Equivalents"],
        ["9004", "Trade Payables - Local", "Trade Payables"],
        ["9005", "Trade Payables - Import", "Trade Payables"],
        ["9006", "Trade Payables - MSME", "Trade Payables"],
        ["9007", "Salaries Payable", "Employee Benefits Payable"],
        ["9008", "Bonus Payable", "Employee Benefits Payable"],
        ["9009", "PF Payable", "Employee Benefits Payable"],
    ]
    tb_accounts = [
        {"code": "9001", "name": "Cash in hand"},
        {"code": "9002", "name": "Bank account - Current"},
        {"code": "9003", "name": "Bank account - Deposits"},
        {"code": "9004", "name": "Trade Payables - Local"},
        {"code": "9005", "name": "Trade Payables - Import"},
        {"code": "9006", "name": "Trade Payables - MSME"},
        {"code": "9007", "name": "Salaries Payable"},
        {"code": "9008", "name": "Bonus Payable"},
        {"code": "9009", "name": "PF Payable"},
    ]
    data = _xlsx_bytes(rows)

    override = parse_grouping_file(data, tb_accounts=tb_accounts)
    assert override["9001"] == "Cash and Cash Equivalents"
    assert override["9004"] == "Trade Payables"
    assert override["9007"] == "Employee Benefits Payable"
    assert override["Bank account - Deposits"] == "Cash and Cash Equivalents"
    # the meaningless header row itself must never leak into the override
    assert "x" not in override and "y" not in override
    print("PASS: TB value-matching resolves a headerless file that keyword detection cannot")

    # Without TB context, this exact file has no recognizable keywords anywhere
    # and must fail cleanly (proves the matching pass is what makes it work,
    # not a coincidental keyword hit).
    try:
        parse_grouping_file(data)
        raise AssertionError("expected GroupingParseError without tb_accounts, got success")
    except GroupingParseError:
        print("PASS: same headerless file correctly fails without TB context (no keyword to fall back on)")


def test_tb_matching_agrees_with_keyword_detection():
    """§0 — when both TB-matching and keyword detection would succeed on the
    same well-formed file, they must produce the identical override (matching
    doesn't corrupt or diverge from already-correct keyword-based parsing)."""
    rows = [
        ["GL Code", "Mapped To"],
        ["2010100", "Trade Payables"],
        ["2010200", "Trade Payables"],
        ["3010210", "Property, Plant and Equipment"],
    ]
    tb_accounts = [{"code": "2010100"}, {"code": "2010200"}, {"code": "3010210"}]
    data = _xlsx_bytes(rows)

    without_tb = parse_grouping_file(data)
    with_tb = parse_grouping_file(data, tb_accounts=tb_accounts)
    assert without_tb == with_tb, f"matching diverged from keyword detection: {with_tb} != {without_tb}"
    assert with_tb["2010100"] == "Trade Payables"
    # the header row's own text must not leak into the override
    assert "GL Code" not in with_tb
    print("PASS: TB-matching agrees exactly with keyword-based detection on an already-working file")


def test_tb_matching_no_overlap_gives_specific_message():
    """§0/§4 — a file whose values match NONE of the given trial balance
    (simulating the real 'Groupings GAIL.xlsx'/FSV case where the file may
    not even be GL-code data for this TB). Must raise with the specific
    'doesn't match this trial balance' diagnostic, not a silent wrong guess."""
    rows = [
        ["FS Item", "Internal Code"],
        ["Ratio Input", "GL/GAIL_FSV/RATIO_INPT"],
        ["Lease Liability Paid", "LEASELIABP"],
        ["Working Capital Turns", "WCTURNS"],
    ]
    tb_accounts = [
        {"code": "9001", "name": "Cash in hand"},
        {"code": "9002", "name": "Trade Payables - Local"},
    ]
    try:
        parse_grouping_file(_xlsx_bytes(rows), tb_accounts=tb_accounts)
        raise AssertionError("expected GroupingParseError, got success")
    except GroupingParseError as exc:
        assert "match the selected trial balance" in str(exc)
        assert exc.preview is not None
        print("PASS: file with values matching none of the TB's accounts fails with the specific diagnostic")


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR: {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
