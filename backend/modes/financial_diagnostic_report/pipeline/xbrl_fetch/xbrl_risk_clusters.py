"""
Block 6: Key Risk Clusters with Interactions (Spec v3.0 §10, §14.1 Row 6, Appendices D & E).

Converts diagnostic signals and filing disclosures into prioritised, assertion-linked
risk clusters and evaluates cross-cutting interactions (reinforcing vs. offsetting).
Provides dual execution: LLM synthesis with robust deterministic fallback.

This module is the entry point (`build_risk_clusters`) only. Signal detection and
interaction evaluation live in `xbrl_risk_signals.py`, deterministic cluster synthesis
in `xbrl_risk_deterministic.py`, and text/formatting/linting helpers in
`xbrl_risk_text.py` — split out because each grew into its own concern, not because
any of the logic itself changed. Everything previously importable from here (including
the private `_format_inr`/`_clean_quote`/`_extract_qualification_snippet` helpers the
test suite reaches into) is re-exported below so no caller needs to change.
"""
from __future__ import annotations
import json
import logging
import re
from typing import Any

from . import xbrl_risk_prompt as P
from .xbrl_risk_text import (
    _format_inr,
    _clean_quote,
    _extract_qualification_snippet,
    _PROHIBITED_REPLACEMENTS,
    lint_risk_clusters,
)
from .xbrl_risk_signals import detect_signals_and_metrics, evaluate_risk_interactions
from .xbrl_risk_deterministic import deterministic_risk_clusters
from . import xbrl_risk_concepts as C

logger = logging.getLogger(__name__)

__all__ = [
    "detect_signals_and_metrics",
    "evaluate_risk_interactions",
    "deterministic_risk_clusters",
    "lint_risk_clusters",
    "build_risk_clusters",
    "_format_inr",
    "_clean_quote",
    "_extract_qualification_snippet",
]


def build_risk_clusters(
    doc_id: str,
    company_name: str,
    cin: str,
    fy_label: str,
    metric_rows: list[dict[str, Any]],
    disclosure_rows: list[dict[str, Any]],
    *,
    use_llm: bool = True,
    chat_fn: Any = None,
) -> dict[str, Any]:
    """
    Builds the complete Block 6 Key Risk Clusters with Interactions payload.
    Supports LLM generation with automatic deterministic fallback.
    """
    signals, lookup = detect_signals_and_metrics(metric_rows, disclosure_rows)
    interactions = evaluate_risk_interactions(signals, lookup)

    formed = False
    payload_data: dict[str, Any] = {}
    reason = ""

    if use_llm and chat_fn is None:
        reason = "LLM synthesis bypassed — chat function was not configured. Deterministic risk engine used."
    elif use_llm:
        try:
            # Format signals block
            sig_lines = [f"- [{s['cluster_id']}] {s['signal']} (trace: {s['source_trace']})" for s in signals]
            signals_block = "DIAGNOSTIC SIGNALS DETECTED:\n" + "\n".join(sig_lines) if sig_lines else "DIAGNOSTIC SIGNALS: (None flagged)"

            # Format interactions block
            inter_lines = [f"- {i['cluster_a']} <-> {i['cluster_b']}: {i['relationship']} ({i['rationale']})" for i in interactions]
            interactions_block = "OBSERVED RISK INTERACTIONS:\n" + "\n".join(inter_lines) if inter_lines else "OBSERVED INTERACTIONS: (None detected)"

            # Format disclosure passages, prioritizing auditor qualification / adverse observations
            passages = []
            sorted_disclosures = sorted(
                disclosure_rows,
                key=lambda d: 0 if any(w in (d.get("concept_name") or "").lower() for w in ("auditor", "qualif", "reserv", "adverse")) else 1
            )
            for idx, d in enumerate(sorted_disclosures[:8], 1):
                txt = (d.get("text") or "").strip()
                cname = d.get("concept_name") or ""
                if len(txt) > 20:
                    is_qual = any(w in cname.lower() for w in ("auditor", "qualif", "reserv", "adverse"))
                    snippet = _extract_qualification_snippet(txt, 250) if is_qual else _clean_quote(txt, 250)
                    passages.append(f"[{idx}] Concept: {cname}:\n{snippet}")
            passages_block = "RETRIEVED DISCLOSURES:\n" + "\n\n".join(passages) if passages else "RETRIEVED DISCLOSURES: (None)"

            user_turn = P.build_user_prompt(
                company_name=company_name,
                cin=cin,
                fy_label=fy_label,
                signals_block=signals_block,
                interactions_block=interactions_block,
                passages_block=passages_block,
            )
            messages = [
                {"role": "system", "content": P.SYSTEM_PROMPT},
                {"role": "user", "content": user_turn},
            ]
            raw_response = chat_fn(messages, max_tokens=1500, temperature=0.0)

            # Strip possible markdown code fences ```json ... ```
            clean_json = re.sub(r'^```(?:json)?\s*', '', raw_response.strip())
            clean_json = re.sub(r'\s*```$', '', clean_json).strip()

            parsed = json.loads(clean_json)
            if "risk_clusters" in parsed and isinstance(parsed["risk_clusters"], list):
                payload_data = lint_risk_clusters(parsed)
                formed = True
            else:
                reason = "LLM response lacked required risk_clusters key; fell back to deterministic synthesis."
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Risk Clusters LLM synthesis bypassed: {type(exc).__name__}: {exc}")
            reason = "LLM synthesis unavailable; using deterministic risk clustering engine."

    if not formed:
        fallback = deterministic_risk_clusters(
            company_name=company_name,
            cin=cin,
            signals=signals,
            lookup=lookup,
            interactions=interactions,
            disclosure_rows=disclosure_rows,
        )
        payload_data = lint_risk_clusters(fallback)
        formed = True

    clusters_list = payload_data.get("risk_clusters", [])
    present_ids = {c.get("id") for c in clusters_list if isinstance(c, dict)}
    for cid in C.CLUSTER_IDS:
        if cid not in present_ids:
            clusters_list.append({
                "id": cid,
                "theme": C.CLUSTER_NAMES[cid],
                "raised": False,
                "reason": C.UNRAISED_REASONS.get(cid, "No contributing anomaly signals detected in reported figures or disclosures."),
                "contributing_signals": [],
                "alt_explanations": [],
                "affected_assertions": list(C.CLUSTER_ASSERTIONS[cid]),
                "inherent_risk": "low",
                "significant_risk": False,
                "control_implications": "Standard internal financial control (IFC) testing under regular audit cycle.",
                "recommended_response": {
                    "nature": "Standard analytical procedures and management inquiries",
                    "timing": "Normal year-end schedule",
                    "extent": "Standard sample size under normal materiality thresholds",
                },
                "specialist_referral": "None",
                "evidence_request": "Routine audit lead schedules under standard audit plan.",
                "diagnostic_confidence": "high",
                "priority_rank": None,
                "priority_reasoning": "Not raised; financial indicators and disclosures remain within normal operational parameters.",
            })

    return {
        "doc_id": doc_id,
        "company_name": company_name,
        "cin": cin,
        "fy_label": fy_label,
        "formed": formed,
        "risk_clusters": clusters_list,
        "interactions": payload_data.get("interactions", []),
        "diagnostics_not_run": [],
        "caveats": [],
        "reason": reason,
    }
