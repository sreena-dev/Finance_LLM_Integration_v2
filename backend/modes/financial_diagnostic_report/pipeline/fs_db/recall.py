"""
Basic recall of the FS (Part A): retrieve & structure BS / P&L / Cash Flow / Notes
for an entity-period, as StatementRecall records the rest of the flow reads.
"""
from __future__ import annotations
from . import repository as R, schema as S
from .models import StatementRecall, ParsedTable


def recall_statements(doc_id: str, flavor: str = "standalone"
                      ) -> tuple[dict[str, ParsedTable | None], list[StatementRecall]]:
    parsed: dict[str, ParsedTable | None] = {}
    recalls: list[StatementRecall] = []
    names = {S.STMT_BS: "Balance Sheet", S.STMT_PL: "Statement of P&L",
             S.STMT_CF: "Cash Flow", S.STMT_SOCE: "Statement of Changes in Equity"}
    for stmt in S.PRIMARY_STMTS:
        pt = R.primary_statement(doc_id, stmt, flavor=flavor)
        parsed[stmt] = pt
        recalls.append(StatementRecall(
            statement=names[stmt], present=pt is not None,
            table_ids=[pt.table_id] if pt and pt.table_id else [],
            periods=pt.periods if pt else [],
            line_count=len([r for r in pt.rows if r.role != "header"]) if pt else 0,
            unit=pt.unit if pt else None))
    # (iv) notes to accounts — count distinct note schedules located
    notes = R.note_index(doc_id, flavor)
    recalls.append(StatementRecall(
        statement="Notes to Accounts", present=bool(notes),
        table_ids=[], periods=[], line_count=len(notes)))
    return parsed, recalls
