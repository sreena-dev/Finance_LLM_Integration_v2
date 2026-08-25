"""
CLI:  python -m fs_db <command>

  ping                     check DB connectivity
  map                      print the finance_llm -> flow schema mapping + live coverage
  docs [COMPANY]           list documents (optionally filtered)
  run  DOC_ID [--json]     run the full flow on a document
  run  --company C --fy Y  resolve the document by entity + fiscal-year-end, then run
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from . import db, schema as S, repository as R, flow

try:                                    # Windows consoles default to cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


# ---------------------------------------------------------------- report ----
def _fmt_findings(findings: list[dict]) -> str:
    by_check: dict[str, list[dict]] = defaultdict(list)
    for f in findings:
        by_check[f["check"]].append(f)
    lines = []
    for check, fs in by_check.items():
        c = Counter(f["status"] for f in fs)
        head = f"  [{check}]  " + "  ".join(f"{k}:{v}" for k, v in sorted(c.items()))
        lines.append(head)
        for f in fs:
            if f["status"] in ("FAIL",) or f["tag"] in ("FINDING", "RISK_FLAG") and f["status"] != "PASS":
                sev = f.get("severity", "")
                lines.append(f"      [X] {f['observation']}  [{sev}]")
                if f.get("trace"):
                    lines.append(f"          trace: {f['trace']}")
                for e in f.get("evidence", []):
                    lines.append(f"          -> evidence: {e}")
                src = f.get("source") or {}
                loc = ", ".join(f"{k}={v}" for k, v in src.items() if k in ("table_id", "page", "note"))
                if loc:
                    lines.append(f"          @ {loc}")
    return "\n".join(lines)


def render(res) -> str:
    d = res.to_dict()
    L = []
    L.append("=" * 78)
    L.append(f"FS AUDIT FLOW — {d['company']}  {d['period']}   ({d['doc_id']})")
    L.append("=" * 78)
    q = d["quality"] or {}
    L.append(f"\n[1] DOCUMENT QUALITY: {q.get('verdict')}   "
             f"(ocr {q.get('ocr_pages')}/{q.get('pdf_pages')} pp, "
             f"{q.get('total_tables')} tables)")
    for fl in q.get("flags", []):
        L.append(f"      ! {fl}")

    L.append("\n[2] STATEMENT COVERAGE")
    for c in d["coverage"]:
        mark = "[y]" if c["tables"] else "[n]"
        L.append(f"      {mark} {c['statement']:22} {c['tables']} table(s)")

    L.append("\n[3] RECALL (Part A)")
    for r in d["recall"]:
        mark = "[y]" if r["present"] else "[n]"
        extra = (f"{r['line_count']} lines, periods={r['periods']}" if r["periods"]
                 else f"{r['line_count']} note schedules" if r["statement"] == "Notes to Accounts"
                 else "")
        L.append(f"      {mark} {r['statement']:32} {extra}")

    fs = d["findings"]
    passes = sum(1 for f in fs if f["status"] == "PASS")
    fails = sum(1 for f in fs if f["status"] == "FAIL")
    abst = sum(1 for f in fs if f["status"] == "ABSTAIN")
    L.append(f"\n[4] CHECKS (compliance + arithmetic)   "
             f"facts={d['facts_count']}   PASS:{passes}  FAIL:{fails}  ABSTAIN:{abst}")
    L.append(_fmt_findings(fs))

    if d["caveats"]:
        L.append("\n[caveats]")
        for c in d["caveats"]:
            L.append(f"      - {c}")
    L.append("=" * 78)
    return "\n".join(L)


# ---------------------------------------------------------------- commands --
def cmd_ping(_):
    ok, msg = db.ping()
    print(("OK  " if ok else "FAIL ") + msg)
    return 0 if ok else 1


def cmd_map(_):
    print(f"finance_llm @ {S.__doc__.splitlines()[0] if S.__doc__ else ''}")
    mapping = [
        ("documents", "req1 entity/period, req2 quality", "company, fy, pages_ocr/pdf, total_tables"),
        ("table_chunks (typed)", "Part A recall + req3 arithmetic", "financial_stmt_type + table_md (BS/PL/CF/SOCE)"),
        ("table_chunks (untyped, is_financial)", "req3a note-to-face", "note schedules; note no. in title"),
        ("text_chunks", "req5 auditor report / CARO (later)", "narrative; auditor/CARO/KAM sections"),
        ("ind_as_chunks / _documents", "Part A compliance (Ind AS)", "40 standards, bge-m3 embedded"),
    ]
    print("\n  finance_llm table                     ->  flow use")
    print("  " + "-" * 74)
    for tbl, use, note in mapping:
        print(f"  {tbl:37} ->  {use}\n        {note}")
    print("\n  live counts:")
    for tbl in (S.DOCUMENTS, S.TABLE_CHUNKS, S.IND_AS_CHUNKS):
        n = db.one(f"SELECT count(*) n FROM {tbl}")["n"]
        print(f"      {tbl:20} {n:>10,}")
    for stmt in S.PRIMARY_STMTS:
        n = db.one(f"SELECT count(*) n FROM {S.TABLE_CHUNKS} "
                   f"WHERE {S.TBL['stmt_type']}=%s", (stmt,))["n"]
        print(f"      stmt {stmt:20} {n:>6,} tables")
    return 0


def cmd_docs(a):
    for r in R.list_documents(a.company, limit=a.limit):
        print(f"  {r['doc_id']:34} {r['company']:24} FY{r['fy_start']}-{r['fy_end']}")
    return 0


def cmd_run(a):
    doc_id = a.doc_id
    if not doc_id and a.company and a.fy:
        d = R.resolve(company=a.company, fy_end=a.fy)
        if not d:
            print(f"no document for company={a.company} fy_end={a.fy}")
            return 1
        doc_id = d["doc_id"]
    if not doc_id:
        print("provide a DOC_ID or --company C --fy Y")
        return 1
    if a.trace:                       # opt into Phoenix tracing for this run
        os.environ["FSDB_TRACE_ENABLED"] = "1"
    res = flow.run(doc_id, flavor="consolidated" if a.consolidated else "standalone")
    if a.json:
        print(json.dumps(res.to_dict(), indent=2, default=str))
    else:
        print(render(res))
    return 0


def cmd_sch3(a):
    """The eleven Schedule III mandatory ratios — always eleven rows, computed or not."""
    from . import schedule3
    doc_id = a.doc_id
    if not doc_id and a.company and a.fy:
        d = R.resolve(company=a.company, fy_end=a.fy)
        if not d:
            print(f"no document for company={a.company} fy_end={a.fy}")
            return 1
        doc_id = d["doc_id"]
    if not doc_id:
        print("provide a DOC_ID or --company C --fy Y")
        return 1
    rep = schedule3.analyse(doc_id, flavor="consolidated" if a.consolidated else "standalone")
    print(json.dumps(rep.to_dict(), indent=2, default=str) if a.json
          else schedule3.render_text(rep))
    return 0


def cmd_appendixg(a):
    """The sixteen FDR Appendix G diagnostics — always sixteen rows, computed or not.

    Same contract as `cmd_sch3`: a CLOSED set, rendered in the specification's own order,
    where an unavailable row carries its reason instead of a blank. A row is only ever
    missing a value because the filing did not disclose the figure — never because the
    catalog lacks the formula."""
    from . import appendix_g as AG
    doc_id = a.doc_id
    if not doc_id and a.company and a.fy:
        d = R.resolve(company=a.company, fy_end=a.fy)
        if not d:
            print(f"no document for company={a.company} fy_end={a.fy}")
            return 1
        doc_id = d["doc_id"]
    if not doc_id:
        print("provide a DOC_ID or --company C --fy Y")
        return 1
    flavor = "consolidated" if a.consolidated else "standalone"
    rep = AG.analyse(doc_id, flavor=flavor)
    if a.json:
        print(json.dumps(rep.to_dict(), indent=2, default=str))
    elif a.markdown:
        print(AG.render_markdown(rep))     # byte-identical to what the API returns
    else:
        print(AG.render_text(rep))
        if a.trace:
            for r in rep.rows:
                if r.status == "OK":
                    print(f"  [{r.key}] {r.trace}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="fs_db", description="FS audit flow over finance_llm")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ping").set_defaults(func=cmd_ping)
    sub.add_parser("map").set_defaults(func=cmd_map)
    d = sub.add_parser("docs"); d.add_argument("company", nargs="?"); d.add_argument("--limit", type=int, default=25); d.set_defaults(func=cmd_docs)
    r = sub.add_parser("run")
    r.add_argument("doc_id", nargs="?")
    r.add_argument("--company"); r.add_argument("--fy", type=int)
    r.add_argument("--consolidated", action="store_true", help="use consolidated (default: standalone)")
    r.add_argument("--trace", action="store_true", help="emit Phoenix spans for this run (project fs-db-audit)")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd_run)
    s3 = sub.add_parser("sch3", help="the 11 Schedule III mandatory ratios (always 11 rows)")
    s3.add_argument("doc_id", nargs="?")
    s3.add_argument("--company"); s3.add_argument("--fy", type=int)
    s3.add_argument("--consolidated", action="store_true")
    s3.add_argument("--json", action="store_true")
    s3.set_defaults(func=cmd_sch3)
    ag = sub.add_parser("appendixg", help="the 16 FDR Appendix G diagnostics (always 16 rows)")
    ag.add_argument("doc_id", nargs="?")
    ag.add_argument("--company"); ag.add_argument("--fy", type=int)
    ag.add_argument("--consolidated", action="store_true")
    ag.add_argument("--trace", action="store_true", help="print each diagnostic's full trace")
    ag.add_argument("--markdown", action="store_true",
                    help="render exactly what the API returns for an Appendix G question")
    ag.add_argument("--json", action="store_true")
    ag.set_defaults(func=cmd_appendixg)

    args = p.parse_args(argv)
    return args.func(args)
