"""Adapter for the `Financial_Diagnostic_Report` branch — scaffold, not yet integrated.

TO INTEGRATE:
  1. Vendor the branch code verbatim into
     backend/modes/financial_diagnostic_report/pipeline/
  2. Replace `run_query` below with a call into that pipeline.
  3. Return the QueryResponse shape — see modes/financial_statement/adapter.py.
If this mode turns out to be form-driven rather than chat-driven, switch its
`ui` field in app/registry.py to "report" and mirror the statutory-auditor
router instead.
"""

from __future__ import annotations

from app.errors import ModeNotIntegratedError

MODE_ID = "financial-diagnostic-report"
BRANCH = "Financial_Diagnostic_Report"


def status() -> tuple[bool, str | None]:
    return False, f"Not integrated yet — pending the '{BRANCH}' branch."


def run_query(query: str) -> dict:
    raise ModeNotIntegratedError(MODE_ID, BRANCH)
