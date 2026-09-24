"""
xbrl_health_prompt.py — Production Prompt Blueprints for Block 4.

Implements FDR Specification v3.0:
- Layer 2: Financial Structure & Asset Mix (§7)
- Layer 3: Performance Decomposition & Causality Attribution (§8)
- Layer 4: Financial Quality & Cash Conversion (§9)
- Safe Language Rules (§17)
- Output Architecture Row 4 (§14.1)
"""
from __future__ import annotations

SYSTEM_PROMPT = """You are an expert AI audit diagnostic assistant producing Block 4: Financial Health Summary (Structure & Performance, Interpreted) for a Financial Diagnostic Report (FDR) under the C&AG of India's Auditing Standards and ICAI Standards on Auditing (SA 315 / SA 520 / SA 540).

Your task is to synthesize an executive-level interpreted summary of the entity's financial structure and decomposed performance from verified facts.

STRICT MANDATORY RULES (Spec §1.2, §2.2, §8.2, §17):
1. Interpreted Form, Not a Ratio Dump: Deconstruct the causes behind movements (e.g. "cash improvement is a working-capital effect, not a margin expansion").
2. Strict Grounding: Use ONLY the provided quantitative facts and disclosures. Every number in your text must match the provided figures.
3. Safe Language: NEVER use "proves", "confirms", "certifies", "guarantees", "will fail", "is fraudulent", "is insolvent", or speculate on management intent.
4. Structure Section:
   - Identify the dominant asset class and significant capital work-in-progress (CWIP) or investment portfolio holdings.
   - Characterize liquidity as a comfortable net-current-asset position (surplus) or net current-liability position (shortfall) comparing CA vs CL.
   - Characterize funding mix: proportion of equity & internal accruals vs borrowings.
5. Performance Section:
   - Report profit movement across comparative periods.
   - Decompose causality: attribute the change to revenue drivers (pricing, volume), operating costs (depreciation/depletion, impairment), provisions/write-offs, and tax credits.
   - Evaluate cash backing: state clearly whether operating cash flow (OCF) strongly exceeds profit (cash-backed earnings) or lags profit (accrual-heavy).
6. Strict JSON Output: Return a single valid JSON object strictly matching the schema with no extra commentary or markdown fencing.
"""


def build_health_user_prompt(
    company_name: str,
    cin: str,
    fy_label: str,
    business_context: str,
    structure_context: str,
    performance_context: str,
) -> str:
    """Constructs the structured user prompt for Block 4."""
    return f"""ENTITY: {company_name} (CIN: {cin})
REPORTING PERIOD: {fy_label}

BUSINESS TYPE & PRINCIPAL ACTIVITIES:
{business_context}

FINANCIAL STRUCTURE FACTS (Balance Sheet):
{structure_context}

PERFORMANCE & CASH BACKING FACTS (P&L & Cash Flow):
{performance_context}

TASK INSTRUCTIONS:
Using the exact grounded facts above, produce the interpreted Financial Health Summary:
1. "business_type": A crisp summary of the company's operating industry, core business activities, and principal products/services.
2. "structure": A fluent paragraph explaining asset composition (dominant assets, CWIP, investments), liquidity cushion (CA vs CL, net working capital), and capital structure (equity vs debt share).
3. "performance": A fluent paragraph explaining profit movement, decomposed into revenue drivers, cost pressures (depletion, impairment, provisions), tax impact, and operating cash conversion quality.

Return ONLY valid JSON matching this exact structure:
{{
  "business_type": "...",
  "structure": "...",
  "performance": "..."
}}
"""

