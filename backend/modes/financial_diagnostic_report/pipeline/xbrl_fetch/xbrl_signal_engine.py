"""
The S01-S27 signal engine — the single entry point this library exposes.

Given a `doc_id`, this: fetches the entity's whole filing series and every fact/
disclosure the registry needs (`xbrl_signal_fetch.fetch_all`), runs every registry entry
through its rule function (`xbrl_signal_rules.RULES`), attaches the registry's static
metadata (cluster_id, severity) and the materiality profile
(`xbrl_signal_materiality.py`) to each result, and returns the closed `SignalResult` list.

This module is intentionally standalone: nothing in `xbrl_risk_clusters.py`,
`xbrl_risk_signals.py`, or the live `/xbrl/risk-clusters` route imports or is imported by
it. It can be exercised and reviewed on its own before any decision is made about
wiring it into cluster synthesis.
"""
from __future__ import annotations
from typing import Any

from . import xbrl_signal_fetch as FETCH
from . import xbrl_signal_registry as REGISTRY
from . import xbrl_signal_rules as RULES
from . import xbrl_signal_materiality as MATERIALITY
from .xbrl_signal_model import SignalResult, FIRED, NOT_FIRED, ABSTAIN, NOT_APPLICABLE


def _to_signal_result(signal: REGISTRY.XbrlSignal, outcome: RULES.Outcome) -> SignalResult:
    if not outcome.ran:
        status = NOT_APPLICABLE if outcome.not_applicable else ABSTAIN
        return SignalResult(
            signal_id=signal.id,
            cluster_id=signal.cluster_id,
            status=status,
            reason=outcome.reason,
            reason_code=outcome.reason_code,
            source_trace=outcome.source_trace,
            missing_inputs=outcome.missing_inputs,
            formula=outcome.formula,
            proxy_used=outcome.proxy_used,
        )

    status = FIRED if outcome.fired else NOT_FIRED
    is_by_nature = False
    if status == FIRED:
        is_by_nature = bool(MATERIALITY.by_nature_basis(signal.id))

    return SignalResult(
        signal_id=signal.id,
        cluster_id=signal.cluster_id,
        status=status,
        severity=signal.severity if status == FIRED else None,
        confidence=outcome.confidence,
        confidence_basis=outcome.confidence_basis,
        observation=outcome.observation,
        trace=outcome.trace,
        formula=outcome.formula,
        source_trace=outcome.source_trace,
        proxy_used=outcome.proxy_used,
        is_by_nature_material=is_by_nature,
    )


def evaluate_signals(doc_id: str) -> list[SignalResult]:
    """Runs every S01-S27 signal against `doc_id`'s entity and returns the full,
    closed-set result list — including ABSTAIN/NOT_APPLICABLE entries, never silently
    dropped (spec Sec 4.4: every diagnostic not run is stated, with why)."""
    fetched = FETCH.fetch_all(doc_id)
    results: list[SignalResult] = []
    for signal in REGISTRY.all_signals():
        rule_fn = RULES.RULES.get(signal.id)
        if rule_fn is None:
            raise RuntimeError(f"{signal.id}: registered in xbrl_signal_registry but has "
                               f"no rule function in xbrl_signal_rules.RULES")
        outcome = rule_fn(fetched)
        results.append(_to_signal_result(signal, outcome))

    # S09's ageing sub-branch: always NOT_APPLICABLE, reported alongside the S09 face-
    # ratio verdict rather than folded into it (the two ask genuinely different questions).
    ageing_outcome = RULES.SUB_BRANCHES["S09_ageing"](fetched)
    results.append(SignalResult(
        signal_id="S09_ageing",
        cluster_id=REGISTRY.get("S09").cluster_id,
        status=NOT_APPLICABLE,
        reason=ageing_outcome.reason,
        reason_code=ageing_outcome.reason_code,
        source_trace=ageing_outcome.source_trace,
    ))

    return results


def evaluate_signals_as_dicts(doc_id: str) -> list[dict[str, Any]]:
    return [r.to_dict() for r in evaluate_signals(doc_id)]


def fired_signals(doc_id: str) -> list[SignalResult]:
    return [r for r in evaluate_signals(doc_id) if r.status == FIRED]


def materially_admissible_signals(doc_id: str) -> list[SignalResult]:
    """FIRED signals that pass the Sec 10.6 materiality gate — either explicitly
    by-nature, or otherwise admissible on VALUE/CONTEXT alone by virtue of having fired
    against a stated numeric threshold (the threshold itself IS the value/context
    admission for signals without a by-nature override; a genuinely by-nature-only
    signal like S17/S22 fires regardless of size)."""
    return [
        r for r in fired_signals(doc_id)
        if MATERIALITY.is_admissible(r.signal_id, fired=True)
    ]
