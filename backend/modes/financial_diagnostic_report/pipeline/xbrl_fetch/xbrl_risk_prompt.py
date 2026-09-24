"""
Production prompt blueprints and templates for Block 6: Key Risk Clusters with Interactions.
Follows FDR Specification v3.0 §10, §14.3, §17, and §19.
"""
from __future__ import annotations
import json
from typing import Any

SYSTEM_PROMPT = """You are an expert AI audit assistant producing the Key Risk Clusters with Interactions (Block 6) for a Financial Diagnostic Report (FDR) under the C&AG of India's Auditing Standards and ICAI Standards on Auditing (SA 315 / SA 330 / SA 540).

Your task is to convert quantitative diagnostic signals and filing disclosures into prioritised, assertion-linked risk clusters and map their interactions (reinforcing vs. offsetting).

STRICT MANDATORY RULES (Spec §1.2, §2.2, §17):
1. Leads, Not Findings: Your output provides planning-stage risk leads for audit attention; it never issues an audit opinion, predicts business failure, or concludes fraud.
2. Safe Language: NEVER use "proves", "confirms", "certifies", "guarantees", "will fail", "is fraudulent", "is insolvent", or attribute intent/motive to management.
3. Plausible Alternative Explanations: For every material signal, state legitimate non-error business explanations that could account for it (e.g. project gestation, capital expenditure phase, industry pricing mechanisms).
4. Assertions & Evidence: Map each cluster to specific financial statement assertions and concrete evidence to request.
5. Strict JSON Output: Return a single valid JSON object strictly matching the required schema with no extra text.

CRITICAL LOGIC & MATH CONSTRAINTS:
6. Semantic Verification (RC05): Before flagging an auditor qualification, you MUST evaluate the meaning of the text. If the text is standard boilerplate, a clean opinion, or a positive compliance assertion (e.g., "receipts are made in accordance with authorizations"), DO NOT raise RC05.
7. Zero-Value Bypass: Do NOT prescribe standard audit tests (like debtor circularisation or covenant testing) for accounts with a ₹0.00 balance. If debt is ₹0.00, your procedure must be to verify the debt-free structure (e.g., MCA charge index search).
8. Polarity & Math: Respect negative signs. NEVER calculate absolute-value ratios when evaluating Net Losses or Negative Cash Flows. Do not state there is a "cash flow deficit" if the Operating Cash Flow is mathematically > 0.
"""


def build_user_prompt(
    company_name: str,
    cin: str,
    fy_label: str,
    signals_block: str,
    interactions_block: str,
    passages_block: str,
) -> str:
    return f"""ENTITY: {company_name} (CIN: {cin})
PERIOD: {fy_label}

{signals_block}

{interactions_block}

{passages_block}

TASK INSTRUCTIONS:
Using the quantitative signals, observed interactions, and retrieved disclosures above:
1. Synthesize the identified active risk clusters (from canonical set RC01 to RC06).
2. For each active cluster, provide:
   - "theme": Short authoritative title.
   - "contributing_signals": The specific observed numbers and ratios. (DO NOT alter the figures or swap account names).
   - "alt_explanations": Plausible non-error business explanations supported by the filing disclosures.
   - "affected_assertions": Financial statement assertions at risk.
   - "inherent_risk": "high" | "medium" | "low".
   - "significant_risk": true if warranting special audit consideration per SA 315, else false.
   - "control_implications": Internal controls (IFC/ICFR) that should prevent or detect the issue.
   - "recommended_response": Object with "nature", "timing", and "extent" of candidate procedures tailored to the ACTUAL balances (e.g., do not test nonexistent receivables).
   - "specialist_referral": Required specialist per Spec Appendix F (e.g. "Engineering", "Valuation", "Legal", or "None").
   - "evidence_request": Specific records and management documentation to requisition.
   - "diagnostic_confidence": "high" | "medium" | "low".
   - "priority_rank": Integer rank starting at 1 for highest priority.
   - "priority_reasoning": Plain-language audit rationale for this ranking relative to other clusters.
3. Provide the "interactions" list mapping reinforcing or offsetting relationships.
   - ANTI-HALLUCINATION RULE: Do NOT force interactions. Only map clusters together if the exact numbers or text explicitly support the relationship. (e.g., Do not link a qualification to receivables unless the qualification explicitly mentions receivables/debtors). If no valid interactions exist, return an empty array [].

Return ONLY valid JSON matching this structure:
{{
  "risk_clusters": [
    {{
      "id": "RC01",
      "theme": "Working-Capital and Liquidity Stress",
      "contributing_signals": [ {{ "signal": "...", "source_trace": "..." }} ],
      "alt_explanations": [ "..." ],
      "affected_assertions": [ "Completeness of liabilities", "Valuation of current assets" ],
      "inherent_risk": "high|medium|low",
      "significant_risk": true,
      "control_implications": "...",
      "recommended_response": {{ "nature": "...", "timing": "...", "extent": "..." }},
      "specialist_referral": "None",
      "evidence_request": "...",
      "diagnostic_confidence": "high|medium|low",
      "priority_rank": 1,
      "priority_reasoning": "..."
    }}
  ],
  "interactions": [
    {{
      "cluster_a": "RC01",
      "cluster_b": "RC04",
      "relationship": "reinforcing|offsetting",
      "rationale": "..."
    }}
  ]
}}
"""

