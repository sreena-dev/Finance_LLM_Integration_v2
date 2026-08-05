"""The single source of truth for what modes exist and where they live.

The frontend renders its mode switcher from `GET /api/modes`, so adding a mode
here (plus its router) is all it takes for it to appear in the UI — there is no
second hard-coded list on the client to keep in sync.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from modes.financial_diagnostic_report import adapter as fdr_adapter
from modes.financial_statement import adapter as fs_adapter
from modes.statutory_auditor_report import adapter as sar_adapter
from modes.trial_balance import adapter as tb_adapter


@dataclass(frozen=True)
class Mode:
    id: str
    label: str
    short_label: str
    description: str
    branch: str
    ui: str                       # "chat" | "report" | "trial-balance"
    base_path: str
    integrated: bool
    status: Callable[[], tuple[bool, str | None]]


MODES: list[Mode] = [
    Mode(
        id="statutory-auditor-report",
        label="Statutory Auditor's Report",
        short_label="Auditor's Report",
        description=(
            "Generate a full SAR review memorandum for an entity and financial "
            "year — opinion, CARO 2020, IFC and coherence checks."
        ),
        branch="Statutory_Auditor_Report",
        ui="report",
        base_path="/api/statutory-auditor-report",
        integrated=True,
        status=sar_adapter.status,
    ),
    Mode(
        id="financial-statement",
        label="Financial Statements",
        short_label="Financial Statements",
        description=(
            "Ask questions across financial statements, accounting standards "
            "and annual reports, answered with cited evidence."
        ),
        branch="Financial_Statement",
        ui="chat",
        base_path="/api/financial-statement",
        integrated=True,
        status=fs_adapter.status,
    ),
    Mode(
        id="trial-balance",
        label="Trial Balance",
        short_label="Trial Balance",
        description=(
            "Upload a trial balance to ask questions over it, run FSLI/risk "
            "audit analytics, or a deterministic PY-vs-CY validation gate."
        ),
        branch="Finance_llm_v2_naveen (main)",
        ui="trial-balance",
        base_path="/api/trial-balance",
        integrated=True,
        status=tb_adapter.status,
    ),
    Mode(
        id="financial-diagnostic-report",
        label="Financial Diagnostic Report",
        short_label="Diagnostic Report",
        description="Ratio, trend and risk diagnostics distilled into a review report.",
        branch="Financial_Diagnostic_Report",
        ui="chat",
        base_path="/api/financial-diagnostic-report",
        integrated=False,
        status=fdr_adapter.status,
    ),
]


def describe(mode: Mode, probe: bool = True) -> dict:
    """Serialise a mode for the API.

    `probe=False` skips the availability check, which is what you want on a
    cold page load: probing imports every pipeline and opens database
    connections, and that can take tens of seconds.
    """
    available, reason = (False, None)
    if probe:
        try:
            available, reason = mode.status()
        except Exception as exc:  # noqa: BLE001 - a broken probe must not 500 the list
            available, reason = False, f"{type(exc).__name__}: {exc}"

    return {
        "id": mode.id,
        "label": mode.label,
        "short_label": mode.short_label,
        "description": mode.description,
        "branch": mode.branch,
        "ui": mode.ui,
        "base_path": mode.base_path,
        "integrated": mode.integrated,
        "available": available,
        "reason": reason,
    }
