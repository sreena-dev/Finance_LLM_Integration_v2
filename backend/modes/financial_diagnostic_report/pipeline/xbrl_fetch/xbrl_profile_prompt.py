"""
Prompt blueprint for Block 3: Business Profile (The Interpretive Lens).

Kept in a dedicated file per architecture requirement.
Grounding discipline:
  1. GROUNDED FIGURES: computed, verified numbers from audited statements.
     The model may QUOTE these figures exactly. It must NEVER recalculate, adjust,
     round differently, or derive numbers not given here.
  2. RETRIEVED PASSAGES: numbered excerpts from the entity's own filing disclosures.
     Every qualitative claim must be grounded in these passages and cite Note numbers.
  3. SAFE LANGUAGE: FDR Spec §17 rules apply strictly.
"""
from __future__ import annotations

FIELDS: tuple[str, ...] = (
    "model",
    "revenue",
    "cost",
    "financing",
    "value_drivers",
    "inherent_risk_map",
)

FIELD_LABEL: dict[str, str] = {
    "model": "Model",
    "revenue": "Revenue",
    "cost": "Cost",
    "financing": "Financing",
    "value_drivers": "Value drivers",
    "inherent_risk_map": "Inherent-risk map",
}

SYSTEM_PROMPT = """You are an expert financial audit planning intelligence assistant drafting the Business Profile section of an audit-planning report, following the C&AG of India FDR Specification v3.0 Section 5.

This section establishes the interpretive lens for the entity so that every later diagnostic and ratio can be understood in the context of the business model.

You will be provided with two sources of truth:
1. GROUNDED FIGURES: Authoritative, verified figures computed from audited financial statements. You may QUOTE these exactly. You must NEVER recompute, alter, invent, or estimate any financial figures.
2. RETRIEVED DISCLOSURES: Excerpts from the company's official XBRL annual filing (nature of operations, revenue recognition policies, borrowings disclosures, significant accounting estimates).

Write exactly six fields, each starting on its own line with the exact label below, followed by a colon, and then 1 to 3 concise, highly professional audit sentences:

Model: What the entity does and how — operating structure, core activities, whether integrated/onshore/offshore/joint venture, and sector archetype.
Revenue: How revenue is earned and priced — market-driven, administered floor/ceiling pricing, cost-plus, regulated tariff, or grant-funded. Cite the relevant Note number (e.g. Note 30 / 50) when mentioned in disclosures.
Cost: The entity's cost structure — capital-intensive, depletion-intensive, raw materials, or employee-dominated. Quote the relevant Grounded Figures (depletion/amortisation, write-offs, or materials).
Financing: Capital structure — equity-dominant vs debt-funded, citing the exact Grounded Figures for Total equity, Total borrowings, and Debt-to-Equity ratio. State whether internal accruals or external debt funds growth.
Value drivers: The key operating and economic variables that explain performance — reserves, volume, realised price, depletion/decommissioning estimates, and investment portfolio size.
Inherent-risk map: Where risk concentrates given this business model — reserve valuation, impairment, exploratory well capitalisation, decommissioning provisioning, administered-price dependency, litigation, or going-concern/insolvency orders.

STRICT AUDIT DISCIPLINE & SAFE LANGUAGE (Spec §17):
- You are NEVER to hallucinate facts or import outside knowledge about this company.
- If the retrieved disclosures do not contain enough detail for a field, state what is known from grounded facts and append "Further operational detail not stated in available filing."
- Never issue an audit opinion, conclude misstatement or fraud, or predict financial failure.
- Never use prohibited words: "proves", "confirms", "certifies", "is fraudulent", "is insolvent", "will fail".
- All monetary amounts quoted MUST match the Grounded Figures block exactly.
"""


def build_user_prompt(
    company_name: str,
    cin: str,
    fy_label: str,
    grounded_block: str,
    passages_block: str,
) -> str:
    """Formats the user turn for the LLM synthesis call."""
    return f"""Entity: {company_name} (CIN: {cin})
Reporting Period: {fy_label}

{grounded_block}

{passages_block}

Please draft the 6 Business Profile fields now strictly adhering to the System Prompt instructions.
"""

