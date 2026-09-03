"""
Orchestrator: run the whole flow for one document and assemble a FlowResult.

Sequence (matches the agreed scope):
  0. resolve entity/period      (req 1 minimal — from the registry)
  1. doc quality precheck       (req 2)
  2. statement coverage         (Part-B precheck)
  3. recall BS/P&L/CF/notes     (Part A)
  4. structural compliance      (Part A — vs Ind AS 1/7)
  5. arithmetic validation      (req 3 / Part B): bs_equation, footing, note_to_face, cash_flow
The flow is fail-soft: a missing statement disables its checks but never aborts the run.
"""
from __future__ import annotations
from . import repository as R, precheck, recall as _recall, compliance, arithmetic as A, schema as S
from .md_parser import to_facts
from .models import FlowResult
from .tracing import span, flush as trace_flush


def run(doc_id: str, flavor: str = "standalone") -> FlowResult:
    doc = R.get_document(doc_id)
    if not doc:
        raise ValueError(f"document not found: {doc_id}")

    res = FlowResult(doc_id=doc_id, company=doc.get("company"),
                     period=f"FY{doc.get('fy_start')}-{doc.get('fy_end')}")
    res.caveats.append(f"flavor: {flavor} financial statements")

    # Root span wraps the whole flow; each stage is a child (no-op when tracing off).
    with span("fs_db.audit_flow", kind="CHAIN",
              input={"doc_id": doc_id, "flavor": flavor}) as root:
        # 1-2. prechecks (quality + coverage)
        with span("precheck", input=doc_id) as sp:
            q = precheck.doc_quality(doc)
            res.quality = q.to_dict()
            cov, cov_findings = precheck.coverage_findings(doc_id)
            res.coverage = [{"statement": k, "tables": v} for k, v in cov.items()]
            sp.set_output({"quality": q.verdict, "coverage": cov})

        # 3. recall + fact table
        with span("recall", kind="RETRIEVER", input=doc_id) as sp:
            parsed, recalls = _recall.recall_statements(doc_id, flavor)
            res.recall = [r.to_dict() for r in recalls]
            facts = []
            for pt in parsed.values():
                if pt:
                    facts += to_facts(pt, doc_id)
            res.facts_count = len(facts)
            sp.set("facts_count", len(facts))
            sp.set_output({r["statement"]: r["present"] for r in res.recall})

        # 4. compliance (structural)
        with span("compliance", input=doc_id) as sp:
            findings = list(cov_findings)
            findings += compliance.structural_compliance(parsed)
            sp.set_output({"findings": len(findings)})

        # 5. arithmetic
        #   footing runs on ADDITIVE tables (balance sheet) only. The P&L and movement
        #   notes (PPE/CWIP) are subtraction/roll-forward chains — footing them with a
        #   generic "sum the lines" model would false-positive, so they are validated by
        #   the targeted CF reconciliation and the gated note-to-face tie instead.
        with span("arithmetic", input=doc_id) as sp:
            bs, pl, cf = parsed.get(S.STMT_BS), parsed.get(S.STMT_PL), parsed.get(S.STMT_CF)
            if bs:
                findings += A.check_balance_sheet_equation(bs, doc_id)
                findings += A.check_footing(bs)
                findings += A.check_note_to_face(bs, doc_id, flavor=flavor)
            if pl:
                findings += A.check_note_to_face(pl, doc_id, flavor=flavor)
            if cf:
                findings += A.check_cash_flow(cf)
            sp.set("checks_total", len(findings))
            sp.set("checks_failed", sum(1 for f in findings if f.status == "FAIL"))

        if q.verdict != "OK":
            res.caveats.append(f"document quality: {q.verdict} — {'; '.join(q.flags)}")
        for pt in parsed.values():
            if pt and pt.warnings:
                res.caveats.append(f"{pt.statement} parse: {'; '.join(pt.warnings)}")

        res.findings = [f.to_dict() for f in findings]
        root.set_output({"findings": len(res.findings), "facts": res.facts_count,
                         "quality": q.verdict})
    # Outside the `with`, so the root span has ENDED and the flush ships a complete trace.
    # The atexit shutdown already covers a CLI run; this covers a long-lived caller, where
    # otherwise nothing would leave the batch queue until the next flow finished.
    trace_flush()
    return res
