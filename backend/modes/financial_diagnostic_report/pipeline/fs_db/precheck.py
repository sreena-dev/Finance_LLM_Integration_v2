"""
Prechecks (req-2 / Part-B prechecks): document quality + statement coverage.
Cheap gates that run before arithmetic so we never validate on an incomplete package.
"""
from __future__ import annotations
from . import repository as R, schema as S
from .models import DocQuality, Finding


def doc_quality(doc: dict) -> DocQuality:
    q = DocQuality(
        doc_id=doc["doc_id"], ocr_pages=doc.get("pages_ocr"), pdf_pages=doc.get("pages_pdf"),
        total_tables=doc.get("total_tables"), total_chunks=doc.get("total_chunks"))
    op, pp = q.ocr_pages, q.pdf_pages
    if pp and op is not None:
        ratio = op / pp if pp else 0
        if ratio < 0.6:
            q.flags.append(f"OCR covered {op}/{pp} pages ({ratio:.0%}) — possible scan/extraction loss")
    if not q.total_tables:
        q.flags.append("no tables extracted from document")
    if q.total_chunks is not None and q.total_chunks < 20:
        q.flags.append(f"only {q.total_chunks} text chunks — document may be truncated")
    q.verdict = "FAIL" if any("no tables" in f for f in q.flags) else ("WARN" if q.flags else "OK")
    return q


def coverage_findings(doc_id: str) -> tuple[dict, list[Finding]]:
    """Which primary statements are present; missing BS/P&L/CF are flagged."""
    cov = R.statement_coverage(doc_id)
    findings: list[Finding] = []
    required = {S.STMT_BS: "Balance Sheet", S.STMT_PL: "Statement of P&L", S.STMT_CF: "Cash Flow Statement"}
    for stmt, name in required.items():
        if cov.get(stmt, 0) == 0:
            findings.append(Finding(
                tag="RISK_FLAG", check="coverage", area=stmt, status="FAIL", severity="HIGH",
                observation=f"{name} not found as a typed table in the package",
                evidence=[f"confirm whether {name} exists in the source PDF (may be an extraction/typing miss)"],
                source={"doc_id": doc_id}))
    return cov, findings
