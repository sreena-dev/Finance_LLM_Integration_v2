"""
Deterministic checks for md_parser (run: python -m fs_db.test_md_parser). No DB.

Focus: RAGGED tables, where a row is extracted with fewer cells than the table's profiled
width. Column roles are profiled from the full-width rows, so a short row's content sits
left of those positions and is read from the wrong cells. Measured on the live corpus this
affects ~10% of parsed statements (25 of 257 across 140 documents), clustering by company
because a filing's layout repeats year to year (GAIL, HUDCO, IIFCL).

The fixture below is GAIL Gas 2024-25's real shape: 5-cell line rows (serial | label | note
| 2 figures) against 3-cell sub-total rows (label | 2 figures) and 1-cell section headers.
Before the fix that filing bound 0 of 20 balance-sheet keys and computed 8 ratios; after, 10
keys and 18 ratios, with assets_split and equity_split PASSING.

A blanket right-alignment of every short row was tried previously and measured WORSE (249
ratios vs 294 on the ONGC set). The regression guard for that is `test_full_width_untouched`
plus `test_short_row_with_values_untouched`: the rescue may only ever fire on a row the
profiled columns already read as EMPTY.
"""
import sys
sys.path.insert(0, __file__.rsplit("fs_db", 1)[0])

from fs_db.md_parser import parse_table_md, parse_num, _realign_short_row
from fs_db.binding import _strip

# CPSU / DPE sign notation: the sign is a MARKER outside the figure. Every one of these
# parsed to None before the fix — the figure was dropped, not mis-signed, so the row read
# as empty and its ratios abstained silently.
SIGNED = """| Particulars | Note | As at 31 March 2025 | As at 31 March 2024 |
| --- | --- | --- | --- |
| (+) Additions during the year | 5 | (+) 1,234.50 | 1,100.00 |
| (-) Payment during year | 6 | (-)119,947.74 | (-) Rs. 96.73 |
| (-) Write back | 6 | (874.25) | 12.00 |
| Deferred tax charge | 7 | –1,234.50 | 0.86 (-) |
| Development | 8 | (-)0.86 | +45.20 |
| Profit (+) / Loss (-) | | 500.00 | 400.00 |"""

# GAIL Gas 2024-25 balance-sheet shape, trimmed. Header is 4 cells; line rows are 5.
RAGGED = """| Particulars | Note | As at 31 st March 2025 | As at 31 st March 2024 |
| --- | --- | --- | --- |
| ASSETS |
| Non Current Assets |
| (a) | Property, Plant and Equipment | 3A | 2,913.62 | 2,616.63 |
| (b) | Right of Use Assets | 3B | 151.63 | 123.11 |
| Total Non Current Assets (A) | 5,157.55 | 4,720.98 |
| Current Assets |
| (a) | Inventories | 10 | 13.49 | 12.70 |
| (iii) | Cash and Cash Equivalents | 11A | 212.47 | 407.85 |
| Total Current Assets (B) | 1,729.77 | 1,295.80 |
| TOTAL ASSETS (A+B) | 6,887.32 | 6,016.78 |"""


def _by_label(pt, text):
    return next((r for r in pt.rows if text.lower() in (r.label or "").lower()), None)


def main() -> int:
    fails = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    pt = parse_table_md(RAGGED, table_id="t", statement="BS")

    # -- the defect: sub-total rows carry the grand totals and must not be lost --
    for label, cur, prior in (("Total Non Current Assets", 5157.55, 4720.98),
                              ("Total Current Assets", 1729.77, 1295.80),
                              ("TOTAL ASSETS", 6887.32, 6016.78)):
        row = _by_label(pt, label)
        check(row is not None, f"row {label!r} lost entirely")
        if row is None:
            continue
        got = list(row.values.values())
        check(got == [cur, prior],
              f"{label!r} values {got} != [{cur}, {prior}] — short row read from the "
              f"columns profiled off the full-width rows")
        check(row.role in ("total", "sum"),
              f"{label!r} classified {row.role!r}, not a total — the row hierarchy and "
              f"every section sum depend on this")

    # -- one-cell section headers must keep their LABEL, or `_tag_sections` never fires and
    #    every section-scoped bind is filtered out (this is what killed cash / receivables /
    #    inventories / PPE on GAIL Gas, not any missing regex) --
    for hdr in ("ASSETS", "Non Current Assets", "Current Assets"):
        check(_by_label(pt, hdr) is not None,
              f"section header {hdr!r} lost its label — section tagging cannot fire")

    # -- full-width rows must be completely untouched --
    ppe = _by_label(pt, "Property, Plant and Equipment")
    check(ppe is not None and list(ppe.values.values()) == [2913.62, 2616.63],
          "a full-width line row was altered by the short-row rescue")
    check(ppe is not None and ppe.note == "3A", "full-width row lost its note reference")

    # -- REGRESSION GUARD for the blanket-realignment attempt that measured worse: a short
    #    row that ALREADY yields a value under the profiled columns must never be re-read --
    ok_short = parse_table_md(
        "| Particulars | Note | FY2025 | FY2024 |\n| --- | --- | --- | --- |\n"
        "| Alpha | 1 | 10.00 | 9.00 |\n| Beta | 2 | 20.00 |",
        table_id="t", statement="BS")
    beta = _by_label(ok_short, "Beta")
    check(beta is not None and beta.values.get("FY2025") == 20.00,
          "a short row that already parses must keep its profiled reading, not be shifted")

    # -- the helper itself: a text tail must not be mangled into figures --
    lbl, vals = _realign_short_row(["Total something", "1,000.00", "2,000.00"], 2)
    check(vals == [1000.0, 2000.0] and lbl == "Total something", "clean figure tail not read")
    lbl, vals = _realign_short_row(["Some note", "refer schedule", "n/a"], 2)
    check(vals is None, "a non-numeric tail must be left alone, not coerced")
    lbl, vals = _realign_short_row(["ASSETS"], 2)
    check(vals is None and lbl == "ASSETS",
          "a one-cell header must yield its label and no values")
    # an explicit nil in the tail is a disclosed absence, not a parse failure
    lbl, vals = _realign_short_row(["Loans", "-", "3.75"], 2)
    check(vals == [None, 3.75], f"explicit nil in the tail mishandled: {vals}")

    # ---- CPSU "(-)"/"(+)" sign notation ----------------------------------------------
    for cell, want in (("(-)0.86", -0.86), ("(+) 18.66", 18.66), ("(-) Rs. 96.73", -96.73),
                       ("(+)1,234.50", 1234.50), ("+1,234.50", 1234.50),
                       ("1,234.50 (-)", -1234.50), ("₹ (1,234.50)", -1234.50),
                       ("–1,234.50", -1234.50), ("−1,234.50", -1234.50),
                       # marker AND parens = the same sign printed twice, NOT a double
                       # negative (real: the lease-liability movement note)
                       ("(-) (119,947.74)", -119947.74)):
        check(parse_num(cell) == want,
              f"parse_num({cell!r}) = {parse_num(cell)!r}, want {want} — signed cell dropped")
    # a sign with no figure is a legend, not a number; existing readings must not move
    for cell in ("(-)", "(+)", "-", "–", "n/a", "", "Profit (+) / Loss (-)"):
        check(parse_num(cell) is None, f"parse_num({cell!r}) must stay None, got {parse_num(cell)!r}")
    for cell, want in (("(1,234.50)", -1234.50), ("- 45.20", -45.20), ("1,234.50", 1234.50),
                       ("(1)", -1.0), ("0", 0.0)):
        check(parse_num(cell) == want, f"parse_num({cell!r}) regressed to {parse_num(cell)!r}")

    ps = parse_table_md(SIGNED, table_id="t", statement="BS")
    for label, cur, prior in (("Additions during the year", 1234.50, 1100.00),
                              ("Payment during year", -119947.74, -96.73),
                              ("Write back", -874.25, 12.00),
                              ("Deferred tax charge", -1234.50, -0.86),
                              ("Development", -0.86, 45.20)):
        row = _by_label(ps, label)
        check(row is not None, f"signed row {label!r} lost")
        if row is not None:
            check(list(row.values.values()) == [cur, prior],
                  f"{label!r} values {list(row.values.values())} != [{cur}, {prior}]")
            check(row.role == "line", f"{label!r} classified {row.role!r}, not a line")
    # the note column must survive: signed figures must not be mistaken for note refs
    check(_by_label(ps, "Payment during year").note == "6", "signed row lost its note ref")

    # ---- the marker must not survive into the label the RULE layer matches on ---------
    for lab, want in (("(+) Additions during the year", "additions during the year"),
                      ("(-) Payment during year", "payment during year"),
                      ("(a) Property, Plant and Equipment", "property, plant and equipment"),
                      ("(ii) Trade payables", "trade payables")):
        check(_strip(lab) == want, f"_strip({lab!r}) = {_strip(lab)!r}, want {want!r} — "
                                   f"every LineSpec pattern is ^-anchored on this")

    print(f"md_parser ragged-row rescue: {pt.warnings}")
    if fails:
        print("\nFAILURES:")
        for f in fails:
            print("  -", f)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
