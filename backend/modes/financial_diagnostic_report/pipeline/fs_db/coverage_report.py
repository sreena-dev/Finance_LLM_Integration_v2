"""
Coverage census — WHY each canonical input fails to bind, across the corpus.

Run:  python -m fs_db.coverage_report [--limit N] [--flavor standalone] [--json out.json]

This is the diagnostic that tells you where the ratio engine is actually losing figures,
as opposed to where it feels like it is. It was written because an eyeball reading of the
misses was wrong: the most frequently unbound row labels are `provisions`, `lease
liabilities` and `deferred tax` — lines NO ratio needs and for which no LineSpec exists —
while the keys that genuinely hurt (PPE, cash, trade receivables) were failing on filings
whose statutory caption was sitting right there in the parsed rows.

That distinction is the whole point of this report. For every REGISTRY key that failed to
bind it asks: was the caption PRESENT in the parsed labels?

  CAPTION PRESENT  -> the pattern would have matched; something upstream blocked it
                      (parse mangled the row, section tagging didn't fire, role
                      misclassified, or the value column read empty). Fix the parser or
                      the section rules — NOT the vocabulary.
  caption ABSENT   -> the filing genuinely words it differently, or doesn't print it.
                      Only these are candidates for a new pattern.

Read-only: opens no transaction, writes nothing, and never mutates the engine.
"""
from __future__ import annotations
import argparse
import collections
import json
import re
import sys

# Statutory caption per canonical key, used ONLY to classify a failure — never to bind.
# Deliberately looser than the LineSpec patterns (no anchors), because the question here is
# "is this concept printed anywhere on the statement", not "does the binder's rule match".
CAPTION = {
    "total_assets":                  r"total assets",
    "total_current_assets":          r"total current assets",
    "total_non_current_assets":      r"total non[ -]?current assets",
    "total_current_liabilities":     r"total current liabilit",
    "total_non_current_liabilities": r"total non[ -]?current liabilit",
    "total_equity":                  r"total equity|shareholders?.? funds?",
    "total_equity_and_liabilities":  r"total equity and liabilit",
    "equity_share_capital":          r"share capital",
    "reserves_and_surplus":          r"other equity|reserves",
    "inventories":                   r"inventor(y|ies)",
    "trade_receivables":             r"trade receivable",
    "cash_and_cash_equivalents":     r"cash and cash equivalent",
    "marketable_securities":         r"investments",
    "net_fixed_assets":              r"property,? plant and equipment|tangible assets",
    "trade_payables":                r"trade payables|sundry creditors",
    "long_term_borrowings":          r"borrowings",
    "short_term_borrowings":         r"borrowings",
    "revenue":                       r"revenue from operation|turnover",
    "other_income":                  r"other income",
    "total_income":                  r"total income",
    "cost_of_materials_consumed":    r"cost of materials consumed",
    "purchases_of_stock_in_trade":   r"purchase(s)? of stock",
    "changes_in_inventories":        r"changes? in inventor",
    "finance_costs":                 r"finance cost",
    "sga_expenses":                  r"other expenses",
    "depreciation":                  r"depreciat|amorti|deplet",
    "total_expenses":                r"total expenses",
    "pbt":                           r"profit before tax",
    "total_tax":                     r"tax expense",
    "pat":                           r"profit for the|profit after tax",
    "eps":                           r"earnings per|basic and diluted",
}

# Keys whose absence is NORMAL, not a defect: most standalone PSU filings genuinely have no
# non-controlling interests and no held-for-sale block. Counted separately so they don't
# swamp the report with expected misses.
EXPECTED_ABSENT = {"non_controlling_interests", "assets_held_for_sale",
                   "liabilities_held_for_sale"}

_STATEMENTS = {
    "BS": ("balance sheet",
           "(restat|reconcil|five year|segment|summaris|summariz)"),
    "PL": ("statement of profit|profit and loss",
           "(retained|comprehensive|restat|reconcil|segment|five year|summaris|summariz)"),
}


def _classify(key: str, labels_blob: str) -> str:
    if key in EXPECTED_ABSENT:
        return "expected-absent"
    rx = CAPTION.get(key)
    if rx is None:
        return "no-caption-defined"
    return "UPSTREAM (caption present)" if re.search(rx, labels_blob) else "vocabulary (caption absent)"


def run(limit: int, flavor: str) -> dict:
    from . import db, ratio_pipeline as RP, binding as B

    docs = db.query("SELECT doc_id FROM documents ORDER BY doc_id")[:limit]
    stat = collections.defaultdict(collections.Counter)   # key -> reason -> n
    bound_n = collections.Counter()
    seen_stmt = collections.Counter()
    layer = collections.Counter()                          # provenance of successful binds
    tieouts = collections.Counter()
    no_statement = collections.Counter()
    per_doc = []

    for d in docs:
        doc_id = d["doc_id"]
        row = {"doc_id": doc_id, "bound": {}, "missing": {}}
        for kind, (inc, exc) in _STATEMENTS.items():
            try:
                pt = RP._statement_by_title(doc_id, inc, exc, kind, flavor)
            except Exception as e:                          # noqa: BLE001 - census must not die
                row.setdefault("errors", []).append(f"{kind}: {type(e).__name__}")
                continue
            if pt is None:
                no_statement[kind] += 1
                continue
            seen_stmt[kind] += 1
            rep = B.Resolver().bind_statement(pt, kind)
            blob = " | ".join((r.label or "") for r in pt.rows).lower()
            for k, ln in rep.bound.items():
                bound_n[k] += 1
                layer[ln.provenance + ("/trusted" if ln.trusted() else "/UNTRUSTED")] += 1
                row["bound"][k] = ln.value
            for k in rep.abstained:
                reason = _classify(k, blob)
                stat[k][reason] += 1
                row["missing"][k] = reason
            for v in rep.validations:
                tieouts[f"{v['check']}:{v['status']}"] += 1
        per_doc.append(row)

    return {"docs": len(docs), "statements_found": dict(seen_stmt),
            "no_statement": dict(no_statement), "bound": dict(bound_n),
            "missing": {k: dict(v) for k, v in stat.items()},
            "layer": dict(layer), "tieouts": dict(tieouts), "per_doc": per_doc}


def render(r: dict) -> None:
    n = r["docs"]
    print(f"documents scanned: {n}   BS found: {r['statements_found'].get('BS', 0)}   "
          f"PL found: {r['statements_found'].get('PL', 0)}   "
          f"no statement located: {r['no_statement']}")
    print()
    print("%-32s %6s  %-28s %-28s" % ("CANONICAL KEY", "BOUND", "UPSTREAM DEFECT", "VOCABULARY GAP"))
    print("-" * 100)
    keys = sorted(set(r["bound"]) | set(r["missing"]),
                  key=lambda k: -sum(r["missing"].get(k, {}).values()))
    up_tot = voc_tot = 0
    for k in keys:
        miss = r["missing"].get(k, {})
        up = miss.get("UPSTREAM (caption present)", 0)
        voc = miss.get("vocabulary (caption absent)", 0)
        exp = miss.get("expected-absent", 0)
        if k in EXPECTED_ABSENT:
            print("%-32s %6d  %-28s %s" % (k, r["bound"].get(k, 0), "-",
                                           f"(expected absent in {exp})"))
            continue
        up_tot += up
        voc_tot += voc
        print("%-32s %6d  %-28s %-28s" % (k, r["bound"].get(k, 0),
                                          f"{up}" if up else "", f"{voc}" if voc else ""))
    print("-" * 100)
    print("%-32s %6s  %-28s %-28s" % ("TOTAL", "", up_tot, voc_tot))
    print()
    print("WHERE TO SPEND EFFORT: %d misses are upstream (parser / section tagging / roles) "
          "vs %d that are\nvocabulary. Only the vocabulary column is fixable by adding "
          "patterns." % (up_tot, voc_tot))
    print()
    print("bind provenance:", dict(sorted(r["layer"].items())))
    print()
    fails = {k: v for k, v in r["tieouts"].items() if k.endswith(":FAIL")}
    print("tie-out FAILs:", dict(sorted(fails.items(), key=lambda kv: -kv[1])) or "none")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="fs_db binding coverage census")
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--flavor", default="standalone")
    ap.add_argument("--json", dest="json_out")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    r = run(a.limit, a.flavor)
    render(r)
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as fh:
            json.dump(r, fh, indent=1, default=str)
        print(f"\nwrote {a.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
