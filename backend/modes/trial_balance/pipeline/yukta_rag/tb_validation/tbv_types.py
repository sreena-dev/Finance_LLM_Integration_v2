"""Shared rule-result shape for every Layer-1 (and L-1..3) check."""

from __future__ import annotations

from yukta_rag.tb_validation.tbv_config import HIGH_PRIORITY_MULTIPLE

PASS = "PASS"
WARNING = "WARNING"
HALT = "HALT"
SKIPPED = "SKIPPED"


def result(rule_id: str, status: str, message: str, **details) -> dict:
    """{rule_id, status, message, details} — the one shape every rule
    function returns, PASS|WARNING|HALT|SKIPPED only."""
    return {"rule_id": rule_id, "status": status, "message": message, "details": details}


def compute_variance(base: float, target: float,
                     materiality_pct: float | None) -> tuple[float, float | None, str]:
    """Shared base->target variance + materiality-banded flag. Used for both
    cross-year (PY closing -> CY closing) and single-table (opening ->
    closing) movement — same bands, same semantics, different two values."""
    variance = target - base
    variance_pct = (variance / base * 100) if base != 0 else None
    if materiality_pct is None:
        flag = "NO_THRESHOLD"
    elif base == 0 and target != 0:
        flag = "NEW_ENTRY"
    elif base != 0 and target == 0:
        flag = "DROPPED"
    elif variance == 0:
        flag = "NO_CHANGE"
    elif variance_pct is not None and abs(variance_pct) > materiality_pct * HIGH_PRIORITY_MULTIPLE:
        flag = "HIGH_PRIORITY"
    elif variance_pct is not None and abs(variance_pct) > materiality_pct:
        flag = "MEDIUM"
    else:
        flag = "LOW"
    return round(variance, 2), (round(variance_pct, 2) if variance_pct is not None else None), flag
