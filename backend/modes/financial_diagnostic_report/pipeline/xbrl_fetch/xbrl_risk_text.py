"""
Text/formatting helpers for Block 6 (Key Risk Clusters): Indian currency formatting,
quote extraction from filing disclosures, and the Spec §17 safe-language linter.

Split out of `xbrl_risk_clusters.py` because these are generic text utilities used
by both signal detection and cluster synthesis, not risk-clustering logic itself.
"""
from __future__ import annotations
import re
from typing import Any

_CRORE = 1e7
_LAKH = 1e5


def _format_inr(val: float | None) -> str:
    """Formats values in standard Indian crore (₹X cr) or lakh (₹X lakh) notation."""
    if val is None:
        return "N/A"
    if val == 0:
        return "₹0.00"
    abs_val = abs(val)
    sign = "-" if val < 0 else ""
    if abs_val >= _CRORE:
        return f"{sign}₹{abs_val / _CRORE:,.2f} cr"
    return f"{sign}₹{abs_val / _LAKH:,.2f} lakh"


def _clean_quote(text: str, max_chars: int = 240) -> str:
    """Extracts coherent snippet from text without mid-sentence truncation."""
    cleaned = re.sub(r'\s+', ' ', text).strip()
    if not cleaned:
        return ""
    if len(cleaned) <= max_chars:
        return cleaned.rstrip(' .,;') + ('.' if not cleaned.endswith(('.', '!', '?')) else '')

    cut = cleaned[:max_chars]
    candidates = [
        m.end(1) for m in re.finditer(r'([a-zA-Z0-9\)\"]\.)\s+', cut)
        if not re.search(r'\b(Rs|No|Co|Ltd|Dr|Mr|Mrs|viz|vs)\.$', cut[:m.end(1)], re.IGNORECASE)
    ]
    if candidates and candidates[-1] > 50:
        return cut[:candidates[-1]].strip()

    last_space = cut.rfind(' ')
    snippet = cut[:last_space].strip() if last_space > 50 else cut.strip()
    while True:
        stripped = re.sub(r'[\s,;:\(\[\{\-\.]+$', '', snippet)
        stripped = re.sub(r'\b(to|of|for|with|in|on|at|from|by|a|an|the|Rs|which|that|and|or|as|is|was|were)\b$', '', stripped, flags=re.IGNORECASE).strip()
        if stripped == snippet:
            break
        snippet = stripped
    return snippet.rstrip(' .,;') + "..."


def _extract_qualification_snippet(txt: str, max_chars: int = 240) -> str:
    """Extracts the substantive reservation or qualified opinion clause from audit report text."""
    cleaned = re.sub(r'\s+', ' ', txt).strip()
    if not cleaned:
        return ""
    # 1. Look for 'Basis for Qualified/Adverse Opinion' heading first
    m_basis = re.search(r'\bBasis for (?:Qualified|Adverse) Opinion[:\s\-]*(.+)', cleaned, re.IGNORECASE)
    if m_basis:
        substantive = m_basis.group(1).strip()
        m_inner = re.search(r'(?:Attention is drawn to|observations? and/or Notes|Note No\.\s*\d+|regarding\s+[A-Za-z]+|The Company has not made the Provision)(.+)', substantive, re.IGNORECASE)
        if m_inner:
            return _clean_quote(m_inner.group(0).strip(), max_chars)
        if not substantive.lower().startswith("paragraph"):
            return _clean_quote(substantive, max_chars)
    # 2. Look for note-level observation directly
    m_obs = re.search(r'(?:Note No\.\s*\d+\s+regarding|Attention is drawn to|regarding\s+advance|The Company has not made the Provision)(.+)', cleaned, re.IGNORECASE)
    if m_obs:
        return _clean_quote(m_obs.group(0).strip(), max_chars)
    # 3. If text starts with boilerplate "We have audited...", search for qualified opinion heading inside
    if re.match(r'^We have audited\b', cleaned, re.IGNORECASE):
        m_qual = re.search(r'\b(?:Qualified Opinion|qualification[s]?|reservation[s]?|adverse remark[s]?)\b[:\s\-]*(.+)', cleaned, re.IGNORECASE)
        if m_qual:
            sub = m_qual.group(1).strip()
            m_sub = re.search(r'(?:Basis for Qualified Opinion|Attention is drawn|Note No\.\s*\d+)(.+)', sub, re.IGNORECASE)
            if m_sub:
                return _clean_quote(m_sub.group(0).strip(), max_chars)
            return _clean_quote(sub, max_chars)
    return _clean_quote(cleaned, max_chars)


_PROHIBITED_REPLACEMENTS: dict[str, str] = {
    "proves": "may indicate",
    "confirms": "is consistent with",
    "certifies": "assesses",
    "guarantees": "supports",
    "is fraudulent": "shows an irregularity requiring audit review",
    "is insolvent": "shows financial stress requiring audit review",
    "will fail": "faces continuity risk requiring audit review",
}


def lint_risk_clusters(data: dict[str, Any]) -> dict[str, Any]:
    """Applies Spec §17 safe-language checks across narrative fields in risk clusters."""
    def _clean_str(val: str) -> str:
        s = val
        for phrase, rep in _PROHIBITED_REPLACEMENTS.items():
            s = re.sub(re.escape(phrase), rep, s, flags=re.IGNORECASE)
        return s

    clusters = data.get("risk_clusters") or []
    for c in clusters:
        for k in ["theme", "control_implications", "evidence_request", "priority_reasoning"]:
            if k in c and isinstance(c[k], str):
                c[k] = _clean_str(c[k])
        if "alt_explanations" in c and isinstance(c["alt_explanations"], list):
            c["alt_explanations"] = [_clean_str(e) for e in c["alt_explanations"] if isinstance(e, str)]
        if "recommended_response" in c and isinstance(c["recommended_response"], dict):
            for rk in ["nature", "timing", "extent"]:
                if rk in c["recommended_response"] and isinstance(c["recommended_response"][rk], str):
                    c["recommended_response"][rk] = _clean_str(c["recommended_response"][rk])

    return data
