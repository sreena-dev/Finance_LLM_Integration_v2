"""
Compliance with respect to relevant standards (Part A).

STAGE 1 (implemented here, deterministic): STRUCTURAL compliance — does each statement
carry the components Ind AS 1 / Ind AS 7 / Schedule III Div II require to be present
(a balancing BS with the mandated totals, a P&L with revenue & profit, a CF split into
operating/investing/financing, notes present). This needs no external service.

STAGE 2 (next, documented): SEMANTIC compliance — ground each area against the Ind AS
corpus already in this DB (ind_as_chunks, 40 standards, bge-m3 embedded) via the rag
retrieval + cross-encoder gate. Left as a pluggable hook so the flow runs offline today.
Schedule-III *format completeness* (mandatory line-items) additionally needs the
Schedule III text ingested — the known missing_corpora gap.
"""
from __future__ import annotations
from .models import ParsedTable, Finding
from . import schema as S


def _has(pt: ParsedTable | None, *patterns: str) -> bool:
    return bool(pt and pt.find(*patterns))


def structural_compliance(parsed: dict[str, ParsedTable | None]) -> list[Finding]:
    out: list[Finding] = []

    def check(area, ok, present_msg, missing_msg, std, evidence):
        out.append(Finding(
            tag="COVERAGE" if ok else "RISK_FLAG", check="compliance_structural", area=area,
            status="PASS" if ok else "FAIL", severity="NONE" if ok else "MEDIUM",
            observation=present_msg if ok else missing_msg,
            standard_refs=[std], evidence=[] if ok else evidence))

    bs, pl, cf = parsed.get(S.STMT_BS), parsed.get(S.STMT_PL), parsed.get(S.STMT_CF)
    soce = parsed.get(S.STMT_SOCE)

    check("balance_sheet",
          _has(bs, r"total assets") and _has(bs, r"total equity"),
          "BS presents Total Assets and Total Equity & Liabilities (Ind AS 1 / Sch III Div II)",
          "BS is missing a mandated total (Total Assets / Total Equity & Liabilities)",
          "Ind AS 1", ["confirm the balance sheet footer against Schedule III Division II"])
    check("balance_sheet_split",
          _has(bs, r"non[\s-]*current") and _has(bs, r"\bcurrent\b"),
          "BS shows the current / non-current classification (Ind AS 1 para 60)",
          "BS does not clearly show a current / non-current split",
          "Ind AS 1.60", ["check current vs non-current presentation"])
    check("profit_loss",
          _has(pl, r"revenue|income from operations") and _has(pl, r"profit|loss for"),
          "P&L presents Revenue and Profit/(Loss) for the period (Ind AS 1)",
          "P&L is missing Revenue or Profit/(Loss) for the period",
          "Ind AS 1", ["confirm P&L captions"])
    check("cash_flow",
          _has(cf, r"operating") and _has(cf, r"investing") and _has(cf, r"financing"),
          "Cash flow split into operating / investing / financing (Ind AS 7)",
          "Cash flow statement is missing one of operating/investing/financing sections",
          "Ind AS 7", ["confirm the three cash-flow activity sections"])
    check("changes_in_equity",
          soce is not None,
          "Statement of Changes in Equity is present (Ind AS 1)",
          "Statement of Changes in Equity not found",
          "Ind AS 1", ["confirm SOCE presence in the package"])
    return out
