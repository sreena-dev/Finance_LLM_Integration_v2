"""Semantic evidence validation: closes the gap that taxonomy structural
validity alone cannot catch -- a resolution can point at a real, valid
taxonomy node and still be the WRONG node for that account.

Ported verbatim from TB_normalization_v1's taxonomy/semantic_validation.py.
Called from taxonomy_resolver.py's ALIAS and CANDIDATE_AUTO steps only.
EXACT is unconditionally trusted (an already-registered exact-snap match
carries no ambiguity for this check to catch).

Design: layered, independent, high-precision rules -- not one large keyword
blacklist. Each rule targets one specific, well-known accounting distinction
where a keyword-vs-node-text contradiction is a strong, low-false-positive
signal. Grow this list only from confirmed real conflicts."""

from __future__ import annotations

import re
from typing import Optional

# Reused, not reimplemented -- the same liability/asset keyword logic
# sanity_checks.py's already-validated post-hoc check uses (directional
# "deposit" handling, CC/OD exclusion baked in there). The one meaningful
# difference is this check is BLOCKING here, where sanity_checks.py's is
# informational-only post-hoc.
from modes.trial_balance.pipeline.tools._shared import is_bank_cash_named
from modes.trial_balance.pipeline.tools.sanity_checks import _ASSET_SIGNALS, _has_liability_signal


def _node_text(node: dict) -> str:
    return f"{node['main_head']} {node['sub_head_1']} {node['sub_head_2']}".lower()


def _account_type_contradiction(evidence_lower: str, node: dict) -> Optional[str]:
    account_type = node.get("account_type")
    if not account_type:
        return None
    is_liability_signal = _has_liability_signal(evidence_lower)
    is_asset_signal = any(term in evidence_lower for term in _ASSET_SIGNALS)
    if is_liability_signal and account_type == "Asset":
        return f"evidence carries a liability-signaling keyword but node.account_type={account_type!r}"
    if is_asset_signal and account_type == "Liability":
        return f"evidence carries an asset-signaling keyword but node.account_type={account_type!r}"
    return None


# Statutory-dues terms (TDS/GST/IGST/CGST/SGST/VAT/TCS) are near-universally
# classified under "duties and taxes"-style nodes -- never under a trade
# receivable/payable node, which is specifically for commercial/vendor-
# customer dues. Standard, well-known accounting boundary, safe as a hard
# rule.
_STATUTORY_DUES_RE = re.compile(r"\b(tds|gst|igst|cgst|sgst|vat|tcs)\b", re.IGNORECASE)
_TRADE_NODE_RE = re.compile(r"\btrade (receivable|payable)", re.IGNORECASE)


def _statutory_dues_vs_trade_conflict(evidence_lower: str, node: dict) -> Optional[str]:
    if _STATUTORY_DUES_RE.search(evidence_lower) and _TRADE_NODE_RE.search(_node_text(node)):
        return "evidence names a statutory-dues term (TDS/GST/etc.) but node is a trade receivable/payable node"
    return None


_CAPITAL_RESERVE_RE = re.compile(r"\bcapital reserve\b", re.IGNORECASE)


def _capital_reserve_conflict(evidence_lower: str, node: dict) -> Optional[str]:
    if _CAPITAL_RESERVE_RE.search(evidence_lower) and node.get("account_type") in ("Income", "Expense"):
        return f"evidence names 'capital reserve' (always Equity) but node.account_type={node.get('account_type')!r}"
    return None


_DEFERRED_TAX_RE = re.compile(r"\bdeferred tax\b", re.IGNORECASE)


def _deferred_tax_vs_trade_conflict(evidence_lower: str, node: dict) -> Optional[str]:
    if _DEFERRED_TAX_RE.search(evidence_lower) and _TRADE_NODE_RE.search(_node_text(node)):
        return "evidence names 'deferred tax' but node is a trade receivable/payable node"
    return None


# TB-R23: the gate that would have caught the EPIL GL 20950021 defect regardless of
# whether taxonomy_resolver.py's own query-construction fix (see _try_candidate_auto)
# is ever imperfect for some other account -- a bank/cash-named ledger (by literal
# "cash"/"bank" text or a known bank abbreviation like "SBI") resolving to an Income or
# Expense node is exactly as implausible, and exactly as catchable by this same
# keyword-vs-node-text pattern, as the capital-reserve and deferred-tax rules above.
def _bank_cash_vs_income_conflict(evidence_lower: str, node: dict) -> Optional[str]:
    account_type = node.get("account_type")
    if account_type not in ("Income", "Expense"):
        return None
    if is_bank_cash_named(evidence_lower):
        return f"evidence names a bank/cash ledger but node.account_type={account_type!r}"
    return None


_RULES = (
    _account_type_contradiction,
    _statutory_dues_vs_trade_conflict,
    _capital_reserve_conflict,
    _deferred_tax_vs_trade_conflict,
    _bank_cash_vs_income_conflict,
)


def validate_semantic_evidence(evidence_text: str, node: dict) -> bool:
    """True if no rule finds a contradiction between `evidence_text` (GL
    name + grouping hint text) and `node`. Conservative by design: absence
    of a contradiction is not proof of correctness, only absence of a
    known, high-confidence red flag -- a rejection filter, not a positive
    classifier."""
    evidence_lower = (evidence_text or "").lower()
    if not evidence_lower.strip():
        return True
    for rule in _RULES:
        if rule(evidence_lower, node) is not None:
            return False
    return True


def semantic_conflict_reason(evidence_text: str, node: dict) -> Optional[str]:
    evidence_lower = (evidence_text or "").lower()
    if not evidence_lower.strip():
        return None
    for rule in _RULES:
        reason = rule(evidence_lower, node)
        if reason is not None:
            return reason
    return None
