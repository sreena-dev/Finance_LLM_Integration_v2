"""
xbrl_trend_prompt.py — Production Prompt Blueprints for Block 5.

Implements FDR Specification v3.0:
- Layer 2: Financial Structure & Asset Mix (§7)
- Layer 3: Performance Decomposition & Causality Attribution (§8)
- Layer 4: Financial Quality Trends (§9)
- Statistical Series Constraints (§9.5)
- Safe Language Rules (§17)
- Output Architecture Row 5 (§14.1)
"""
from __future__ import annotations
import json
from typing import Any

SYSTEM_PROMPT = """You are an expert AI audit diagnostic assistant producing Block 5: Key Trends & Structural Drift for a Financial Diagnostic Report (FDR) under the C&AG of India's Auditing Standards and ICAI Standards on Auditing (SA 315 / SA 520 / SA 540).

Your task is to analyze multi-period financial facts, common-size balance sheet/P&L drift, and extended DuPont profitability decompositions, identifying directional shifts and risk signals for audit planning.

STRICT MANDATORY RULES (Spec §1.2, §2.2, §17):
1. Leads, Not Findings: Your commentary identifies audit planning leads and anomalies; it never issues audit conclusions, asserts management wrongdoing, or guarantees outcomes.
2. Safe Language: NEVER use "proves", "confirms", "certifies", "guarantees", "will fail", "is fraudulent", "is insolvent", or speculate on intent.
3. Causality Attribution (Spec §8.2): Every material trend must be attributed to an explicit driver (e.g., volume contraction, customer mix, credit extension, margin pressure, leverage expansion, classification re-alignment). Never present a bare number without explaining its operational or structural driver.
4. Statistical Series Constraints (Spec §9.5):
   - With >= 3 periods: Formulate multi-year trend trajectories.
   - With 2 periods: Report comparative YoY movement; never call a two-point movement a "trend".
   - With 1 period: Do NOT fabricate a trend. State cleanly that the entity is in its initial reporting period and multi-year drift analysis requires at least two comparable periods.
5. The 4 Canonical Audit Trend Archetypes:
   - Receivables Divergence (S05): When receivables rise while revenue falls or lags; note turnover contraction. Audit Lead: "Divergence is a lead — timing, customer mix or collection pace."
   - Liquidity Mix Shift (S17): When cash/bank placements swing into other current financial assets or term deposits. Audit Lead: "A classification / placement shift to explain."
   - Provisions Volatility (S16): When allowances or impairment surge YoY. Audit Lead: "An estimate-behaviour signal to corroborate, never a conclusion on intent."
   - Coverage Direction Drift (S15): When DSCR/Interest coverage contracts on lower EBIT even if absolute coverage remains high. Audit Lead: "The direction, not the level, is the lead."
6. Strict JSON Output: Return a single valid JSON object strictly matching the schema with no extra commentary or markdown fencing outside the JSON.
"""


def build_trend_user_prompt(
    company_name: str,
    cin: str,
    fy_label: str,
    series_years: int,
    period_labels: list[str],
    common_size_block: str,
    dupont_block: str,
    signals_block: str,
) -> str:
    """Constructs the structured user prompt for Block 5."""
    return f"""ENTITY: {company_name} (CIN: {cin})
REPORTING PERIOD: {fy_label}
SERIES COVERAGE: {series_years} reporting period(s) [{', '.join(period_labels)}]

{common_size_block}

{dupont_block}

{signals_block}

TASK INSTRUCTIONS:
Using the quantitative multi-period time series, common-size drift, DuPont decomposition, and detected trend signals above:
1. Synthesize the primary directional drift narrative and executive summary lede.
2. Formulate diagnostic trend cards for each active signal and structural movement.
   - Each card must have: "signal_id", "title", "drift_type", "severity" ("high"|"medium"|"low"), "observation", "audit_lead", and "evidence_lead".
   - Enforce Causality Attribution: explain WHY the movement occurred based on the provided numbers.
3. Summarize the extended DuPont decomposition (Margin vs Turnover vs Leverage drivers).
4. If series_years == 1, populate single-period graceful disclosure and leave multi-year drift cards empty.

Return ONLY valid JSON matching this structure:
{{
  "doc_id": "...",
  "company_name": "{company_name}",
  "cin": "{cin}",
  "fy_label": "{fy_label}",
  "series_years": {series_years},
  "period_labels": {json.dumps(period_labels)},
  "summary_lede": "...",
  "dupont_narrative": "...",
  "structural_drift_cards": [
    {{
      "signal_id": "S05",
      "title": "Receivables Outpacing Revenue & Turnover Contraction",
      "drift_type": "operational_divergence",
      "severity": "medium",
      "observation": "...",
      "audit_lead": "...",
      "evidence_lead": "..."
    }}
  ],
  "common_size_highlights": [
    {{
      "item": "Property, Plant & Equipment",
      "category": "asset_mix",
      "drift_pp": -3.45,
      "direction": "contracting",
      "narrative": "..."
    }}
  ]
}}
"""

