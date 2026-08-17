"""
Deterministic checks for statement SELECTION (run: python -m fs_db.test_statement_selection).
No DB, no network — `_score_statement` is a pure function of a parsed table, so the rules
that decide WHICH table becomes "the balance sheet" are testable on synthetic markdown.

Why this file exists: the selector used to require the word "standalone" in the table
title, which matched nothing for 12 of 40 sampled companies (most unlisted PSUs print a
bare "Balance Sheet as at March 31, 2025"). Relaxing that filter alone is unsafe — notes
and five-year digests immediately win the title match — so the relaxation is paired with
structural scoring. Both halves are asserted here; loosening either one breaks a test
rather than silently feeding a note table into every ratio.
"""
import sys
sys.path.insert(0, __file__.rsplit("fs_db", 1)[0])

from fs_db.ratio_pipeline import _score_statement
from fs_db.md_parser import parse_table_md

_HDR = "| Particulars | Note | As at March 31, 2025 | As at March 31, 2024 |\n| --- | --- | --- | --- |"


def _bs(n_rows: int = 12, opener: bool = True, closer: bool = True) -> str:
    rows = ["| ASSETS |", "| Non Current Assets |"] if opener else []
    rows += [f"| (a) | Line item {i} | {i} | {100 + i}.00 | {90 + i}.00 |"
             for i in range(n_rows)]
    if closer:
        rows += ["| EQUITY AND LIABILITIES |", "| Total equity and liabilities | 5 | 999.00 | 888.00 |"]
    return _HDR + "\n" + "\n".join(rows)


def _pl(revenue: bool = True, profit: bool = True, n_rows: int = 12) -> str:
    rows = []
    if revenue:
        rows.append("| I | Revenue from operations | 20 | 1,000.00 | 900.00 |")
    rows += [f"| | Expense line {i} | {i} | {10 + i}.00 | {9 + i}.00 |" for i in range(n_rows)]
    rows.append("| | Total expenses | | 800.00 | 700.00 |")
    if profit:
        rows.append("| V | Profit before tax | | 200.00 | 180.00 |")
    return _HDR + "\n" + "\n".join(rows)


def _score(md: str, kind: str, title: str, flav: str = "standalone"):
    pt = parse_table_md(md, table_id="t", statement=kind)
    return _score_statement(pt, md, kind, title, flav)


def main() -> int:
    fails = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    # -- the bug this fix exists for: an untitled-flavor statement must be SELECTABLE --
    bare = _score(_bs(), "BS", "Balance Sheet as at March 31, 2025")
    check(bare is not None, "a bare 'Balance Sheet ...' title must not be rejected — this is "
                            "how most unlisted PSUs print it (RailTel, MECON, IOCL, IRFC)")

    # -- but a note stub must never win a statement slot --
    stub = _score(_HDR + "\n| Balance Sheet Items |\n| (a) | Something | 1 | 5.00 | 4.00 |",
                  "BS", "Balance Sheet Items")
    check(stub is None, "a 2-row 'Balance Sheet Items' stub must be REJECTED (Madras "
                        "Fertilizers picked exactly this when only the title was matched)")
    tax_note = _score(_pl(revenue=False, profit=False, n_rows=9), "PL",
                      "A. Tax recognized in Statement of Profit and Loss")
    real_pl = _score(_pl(), "PL", "Statement of Profit and Loss")
    check(tax_note is None or real_pl > tax_note,
          "a real P&L must outscore the 'Tax recognized in Statement of Profit and Loss' "
          "note that IREDA's P&L slot went to")

    # -- an explicitly flavor-tagged title still wins outright, so every filing that DOES
    #    tag its flavor selects exactly what it selected before this change --
    tagged = _score(_bs(), "BS", "STANDALONE BALANCE SHEET AS AT 31 MARCH 2025")
    check(tagged > bare, "an explicit 'standalone' tag must outrank an untagged title")
    # ...and it must not let a WEAK table beat a strong untagged one by tag alone is NOT
    # asserted: the tag is deliberately the strongest single signal (+6), because when an
    # entity publishes both flavors the tag is the only thing that distinguishes them.

    # -- multi-year digests lose (IOCL's P&L slot went to a 4-period "Summarised" table) --
    digest = _HDR.replace("| As at March 31, 2024 |",
                          "| As at March 31, 2024 | As at March 31, 2023 | As at March 31, 2022 |")
    digest = digest.replace("| --- | --- | --- | --- |", "| --- | --- | --- | --- | --- | --- |")
    digest += "\n" + "\n".join(f"| (a) | Line {i} | {i} | 1.00 | 2.00 | 3.00 | 4.00 |"
                              for i in range(12))
    d_score = _score(digest, "BS", "Summarised Balance Sheet")
    check(d_score is None or d_score < bare,
          "a 4-period multi-year digest must not outrank a 2-period statement")

    # -- structural signals actually move the score --
    check(_score(_bs(opener=True), "BS", "Balance Sheet")
          > _score(_bs(opener=False), "BS", "Balance Sheet"),
          "the ASSETS opener must add score")
    check(_score(_pl(revenue=True), "PL", "Statement of Profit and Loss")
          > _score(_pl(revenue=False), "PL", "Statement of Profit and Loss"),
          "a 'Revenue from operations' row must add score")

    # -- a HALF statement stays acceptable. ONGC 2020-21's consolidated balance sheet is
    #    titled on its Equity-and-Liabilities chunk with the Assets side in an untitled
    #    chunk BEFORE it; `_with_continuations` reaches backward for it, so rejecting a
    #    chunk for missing the opener would lose that filing entirely. --
    half = _score(_bs(opener=False, closer=True), "BS", "Balance Sheet")
    check(half is not None, "a half-statement chunk must remain selectable (ONGC 2020-21)")

    # -- consolidated vs standalone is asymmetric, enforced in SQL, documented here --
    check(_score(_bs(), "BS", "Consolidated Balance Sheet", flav="consolidated")
          > _score(_bs(), "BS", "Consolidated Balance Sheet", flav="standalone"),
          "the flavor bonus must key off the REQUESTED flavor")

    print(f"statement selection: {8 - len(fails)}/8 rule groups pass")
    if fails:
        print("\nFAILURES:")
        for f in fails:
            print("  -", f)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
