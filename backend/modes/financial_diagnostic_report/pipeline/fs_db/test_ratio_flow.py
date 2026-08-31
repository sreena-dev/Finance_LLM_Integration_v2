"""
Hermetic regression test for the bind -> derive -> compute flow (no DB, no network,
no embedder). Inline mini BS + P&L with the tricky total-assets / total-current-assets
distinction and a full (non-truncated) liabilities side.

Run:  python fs_db/test_ratio_flow.py     (or: python -m fs_db.test_ratio_flow)
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))   # repo root -> `import fs_db.*`

from fs_db.md_parser import parse_table_md
from fs_db import ratio_pipeline as RP

BS_MD = """
| Particulars | Note | As at 31 March 2025 | As at 31 March 2024 |
| --- | --- | --- | --- |
| ASSETS | | | |
| (1) Non-current assets | | | |
| (a) Property, plant and equipment | 3 | 600 | 550 |
| Total non-current assets | | 600 | 550 |
| (2) Current assets | | | |
| (a) Inventories | 5 | 100 | 80 |
| (b) Trade receivables | 6 | 200 | 180 |
| (c) Cash and cash equivalents | 7 | 100 | 90 |
| Total current assets | | 400 | 350 |
| Total assets | | 1000 | 900 |
| EQUITY AND LIABILITIES | | | |
| EQUITY | | | |
| (a) Equity share capital | 8 | 300 | 300 |
| (b) Other equity | 9 | 250 | 200 |
| Total equity | | 550 | 500 |
| LIABILITIES | | | |
| (1) Non-current liabilities | | | |
| (a) Borrowings | 10 | 150 | 150 |
| Total non-current liabilities | | 150 | 150 |
| (2) Current liabilities | | | |
| (a) Trade payables | 11 | 300 | 250 |
| Total current liabilities | | 300 | 250 |
| Total equity and liabilities | | 1000 | 900 |
"""

PL_MD = """
| Particulars | Note | Year ended 31 March 2025 | Year ended 31 March 2024 |
| --- | --- | --- | --- |
| Revenue from operations | 12 | 2000 | 1800 |
| Other income | 13 | 100 | 90 |
| Total income | | 2100 | 1890 |
| Finance costs | 14 | 80 | 70 |
| Depreciation and amortisation | 15 | 120 | 110 |
| Total expenses | | 1600 | 1450 |
| Profit before tax | | 500 | 440 |
| Total tax expense | | 125 | 110 |
| Profit for the year | | 375 | 330 |
"""

EXPECT_OK = {          # ratio -> expected value (hand-computed)
    "net_profit_margin": 18.75,            # 375/2000*100
    "operating_profit_margin": 29.0,       # (500+80)/2000*100
    "interest_coverage": 7.25,             # 580/80
    "current_ratio": round(400 / 300, 4),  # 1.3333
    "roce": round(580 / 700 * 100, 4),     # 82.8571
    "roe": round(375 / 550 * 100, 4),      # 68.1818
    "proprietary_ratio": round(550 / 1000, 4),
    # the mini BS's "(a) Borrowings" line under non-current liabilities now binds
    # via the new `long_term_borrowings` LineSpec -> debt_to_equity computes instead
    # of abstaining (was in EXPECT_ABSTAIN before the binder gained borrowings support).
    "debt_to_equity": round(150 / 550, 4),         # 0.2727  (LT borrowings 150, no ST line)
    "equity_ratio": round(550 / (600 + 100), 4),   # 0.7857  (net_fixed_assets now binds too)
    "debt_ratio": round(150 / (600 + 100), 4),     # 0.2143
    "cash_ratio": round(100 / 300, 4),             # 0.3333  (cash_and_cash_equivalents fix)
    "quick_ratio": round((400 - 100) / 300, 4),    # 1.0
    "net_working_capital": 400 - 300,              # 100  (no short_term_borrowings line -> excl. 0)
}
EXPECT_ABSTAIN = {"inventory_turnover"}   # no Schedule III COGS lines (cost of materials /
                                          # purchases of stock-in-trade / changes in inventories)
                                          # in this mini P&L -> any_of correctly abstains


def main() -> int:
    bs = parse_table_md(BS_MD, table_id="mini_bs", statement="BS")
    pl = parse_table_md(PL_MD, table_id="mini_pl", statement="PL")
    rep = RP.compute_from_parsed(bs, pl, entity="MINI",
                                 only=list(EXPECT_OK) + list(EXPECT_ABSTAIN))
    fails = []

    # bindings all trusted, tie-outs pass
    for stmt in ("BS", "PL"):
        b = rep.binding[stmt]
        if len(b.trusted()) != len(b.bound):
            fails.append(f"{stmt}: untrusted bindings {set(b.bound) - set(b.trusted())}")
        for v in b.validations:
            if v["status"] == "FAIL":
                fails.append(f"{stmt}: tie-out {v['check']} FAILED {v.get('inputs')}")

    got = {c.key: c for c in rep.computed}
    ab = {c.key for c in rep.abstained}
    for k, want in EXPECT_OK.items():
        if k not in got:
            fails.append(f"{k}: expected computed, got abstain/absent")
        elif abs(got[k].result - want) > 0.01:
            fails.append(f"{k}: {got[k].result} != {want}")
    for k in EXPECT_ABSTAIN:
        if k not in ab:
            fails.append(f"{k}: expected ABSTAIN, but it computed")

    print(f"BS bound {len(rep.binding['BS'].bound)}, PL bound {len(rep.binding['PL'].bound)}; "
          f"computed {len(rep.computed)}, abstained {len(rep.abstained)}")
    if fails:
        print("\nFAILURES:")
        for f in fails:
            print("  -", f)
        return 1
    print("ALL PASS")
    for k in EXPECT_OK:
        print(f"  {k:26s} {got[k].result:>10.4f} {got[k].unit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
