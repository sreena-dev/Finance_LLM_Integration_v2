"""
End-to-end demo/test of the enterprise ratio pipeline on a REAL filing (ONGC
FY2024-25, finance_llm). Shows: deterministic binding + provenance, arithmetic
tie-out validation, ratios computed with traces, and SAFE ABSTAIN where inputs
aren't present (truncated BS -> no current liabilities; no COGS line).

Scenario B additionally paraphrases the revenue label to exercise the SEMANTIC
fallback + arithmetic gate (bge-m3 embedder injected from rag/).

Run:  python -m fs_db.test_ratio_binding          (needs finance_llm reachable)
"""
import copy
import sys
sys.stdout.reconfigure(encoding="utf-8")

from . import db
from .md_parser import parse_table_md
from . import ratio_pipeline as RP

DOC = "ONGC_2024_2025"
ONLY = ["net_profit_margin", "operating_profit_margin", "interest_coverage", "roe",
        "roa", "proprietary_ratio", "total_assets_turnover",
        "current_ratio", "roce", "inventory_turnover", "debt_to_equity"]


def _embedder():
    """Inject rag's bge-m3 as embed_fn (glue lives in the test, not in fs_db)."""
    try:
        import os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "rag"))
        from embedder import embed
        return lambda texts: embed(list(texts))
    except Exception as e:
        print(f"(semantic tier unavailable: {e.__class__.__name__}) — lexical only\n")
        return None


def show(rep: RP.RatioReport, title: str):
    print(f"\n================  {title}  ================")
    for stmt in ("BS", "PL"):
        b = rep.binding[stmt]
        print(f"\n[{stmt}] bound {len(b.trusted())}/{len(b.bound)} trusted; "
              f"abstained {len(b.abstained)}")
        for k, ln in b.bound.items():
            flag = "trusted" if ln.trusted() else "UNTRUSTED"
            extra = f" (sem {ln.confidence:.2f}, validated={ln.validated})" if ln.provenance == "semantic" else ""
            print(f"    {k:26s} {ln.value:>16,.2f}  [{ln.provenance}/{flag}]{extra}  <- '{ln.label}'")
        for v in b.validations:
            det = v.get("inputs", v.get("reason", ""))
            print(f"    tie-out {v['check']:16s} {v['status']}  {det if v['status']=='FAIL' else ''}")

    print(f"\n  COMPUTED ({len(rep.computed)}):")
    for c in rep.computed:
        print(f"    [OK]      {c.name:34s} {c.result:>12,.4f} {c.unit:5s}  | {c.trace}")
    print(f"\n  ABSTAINED ({len(rep.abstained)}) - safe, with reason:")
    for c in rep.abstained:
        print(f"    [ABSTAIN] {c.name:34s} {c.trace}")


def _load_by_title(doc_id, title_rx, exclude_rx, statement):
    """Select a statement by TITLE (reliable) — NOT financial_stmt_type, which is
    mislabeled in this DB (ONGC's real P&L is tagged 'balance_sheet'; a retained-
    earnings statement is tagged 'profit_loss'). Mirrors company_qa's selection."""
    rows = db.query(
        """SELECT table_id, table_title, page_pdf_start, table_md
             FROM table_chunks
            WHERE doc_id=%(d)s AND table_md IS NOT NULL
              AND table_title ~* %(inc)s AND table_title ~* 'standalone'
              AND table_title !~* %(exc)s
            ORDER BY length(table_title) ASC LIMIT 1""",
        {"d": doc_id, "inc": title_rx, "exc": exclude_rx})
    if not rows:
        return None
    r = rows[0]
    print(f"  [{statement}] selected '{r['table_title'][:60]}' ({r['table_id']})")
    return parse_table_md(r["table_md"], table_id=r["table_id"],
                          statement=statement, page=r["page_pdf_start"])


def main():
    ok, msg = db.ping()
    if not ok:
        print(f"finance_llm unreachable: {msg}. Cannot run."); return 1
    print("selecting statements by TITLE (financial_stmt_type is unreliable here):")
    bs = _load_by_title(DOC, "balance sheet", "(restat|reconcil|five year|segment)", "BS")
    pl = _load_by_title(DOC, "statement of profit|profit and loss",
                        "(retained|comprehensive|restat|reconcil|segment|five year)", "PL")
    if not bs or not pl:
        print("Could not locate ONGC BS/PL by title."); return 1

    # ---- Scenario A: deterministic (lexical + structural), no embedder ----
    repA = RP.compute_from_parsed(bs, pl, entity=DOC, only=ONLY)
    show(repA, "A · DETERMINISTIC (rules + structure + arithmetic gate)")

    # ---- Scenario B: paraphrased revenue label -> semantic fallback + gate ----
    embed_fn = _embedder()
    if embed_fn:
        pl2 = copy.deepcopy(pl)
        renamed = False
        for r in pl2.rows:
            if r.label and r.label.strip().lower().startswith("revenue from operation"):
                r.label = "Income from sale of products and services"   # lexical rules miss this
                renamed = True
        print(f"\n\n(scenario B: renamed revenue label -> "
              f"'Income from sale of products and services'; renamed={renamed})")
        repB = RP.compute_from_parsed(bs, pl2, entity=DOC,
                                      only=["net_profit_margin", "operating_profit_margin"],
                                      embed_fn=embed_fn)
        rv = repB.binding["PL"].bound.get("revenue")
        print(f"revenue now bound via: {rv.provenance if rv else 'NONE'} "
              f"(validated={rv.validated if rv else '-'})  value={rv.value if rv else '-'}")
        show(repB, "B · SEMANTIC FALLBACK for paraphrased label (validated by income tie-out)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
