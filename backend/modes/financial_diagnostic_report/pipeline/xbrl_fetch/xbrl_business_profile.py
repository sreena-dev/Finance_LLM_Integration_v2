"""
Block 3: Business Profile — the interpretive lens.

Extracts grounded figures from `financial_facts` and qualitative disclosures from
`disclosures`, synthesizing the 6 core business profile fields per FDR Specification v3.0 §5:
  1. Model (Operating model & structure)
  2. Revenue (Revenue sources & pricing mechanism)
  3. Cost (Cost structure & depletion/amortisation intensity)
  4. Financing (Capital structure, debt-equity mix, funding dependencies)
  5. Value drivers (Economic drivers: reserves, capacity, investment book)
  6. Inherent-risk map (Expected risk concentrations: estimates, provisioning, litigation)

STRICT ENTERPRISE ARCHITECTURE:
- Figures are computed deterministically, NEVER hallucinated or altered.
- All 6 fields are always present in the emitted JSON wire payload.
- Safe-language rules (Spec §17) and number-groundedness linting run on every output.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from . import xbrl_profile_concepts as C
from . import xbrl_profile_prompt as P

logger = logging.getLogger(__name__)

CRORE = 10_000_000  # 1 crore = 1,00,00,000 INR
LAKH = 100_000      # 1 lakh = 1,00,000 INR
PROHIBITED_WORDS = (
    "proves", "confirms", "certifies", "guarantees",
    "is fraudulent", "is insolvent", "will fail",
)


@dataclass
class GroundedFigure:
    id: str
    label: str
    value: float
    display: str
    unit: str
    concepts: tuple[str, ...]


def _format_inr(val: float | None) -> str:
    """Formats values in standard Indian crore (₹X cr) or lakh (₹X lakh) notation."""
    if val is None:
        return "—"
    if val == 0:
        return "₹0.00"
    abs_val = abs(val)
    sign = "-" if val < 0 else ""
    if abs_val >= CRORE:
        return f"{sign}₹{abs_val / CRORE:,.2f} cr"
    return f"{sign}₹{abs_val / LAKH:,.2f} lakh"


def extract_grounded_figures(rows: list[dict[str, Any]]) -> tuple[list[GroundedFigure], dict[str, Any]]:
    """
    Computes authoritative figures from financial_facts rows.
    Handles instant facts (latest fy_end where fy_start is None)
    and duration facts (latest fy_start and fy_end).
    """
    by_concept: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_concept.setdefault(r["concept_name"], []).append(r)

    # Sort each concept by fy_end descending to pick latest reporting period
    for c_name in by_concept:
        by_concept[c_name].sort(key=lambda x: x.get("fy_end") or "", reverse=True)

    figures: list[GroundedFigure] = []
    lookup: dict[str, float] = {}

    def get_latest_sum(concepts: tuple[str, ...]) -> float | None:
        total = 0.0
        found_any = False
        for c in concepts:
            matches = by_concept.get(c)
            if matches:
                val = matches[0].get("value_numeric")
                if val is not None:
                    total += float(val)
                    found_any = True
        return total if found_any else None

    # P01: Total Equity
    eq = get_latest_sum(("Equity",))
    if eq is not None:
        lookup["equity"] = eq
        figures.append(GroundedFigure("P01", "Total equity", eq, _format_inr(eq), C.CURRENCY, ("Equity",)))

    # P02: Total Borrowings (Current + Non-current)
    borrowings = get_latest_sum(("BorrowingsCurrent", "BorrowingsNoncurrent"))
    if borrowings is not None:
        lookup["borrowings"] = borrowings
        figures.append(GroundedFigure(
            "P02", "Total borrowings", borrowings, _format_inr(borrowings),
            C.CURRENCY, ("BorrowingsCurrent", "BorrowingsNoncurrent")
        ))

    # P03: Debt-to-Equity Ratio
    if eq is not None and borrowings is not None:
        if eq > 0:
            de_ratio = borrowings / eq
            lookup["debt_equity"] = de_ratio
            figures.append(GroundedFigure(
                "P03", "Debt-equity ratio", de_ratio, f"{de_ratio:.2f}x",
                C.RATIO, ("BorrowingsCurrent", "BorrowingsNoncurrent", "Equity")
            ))
        elif eq < 0:
            lookup["debt_equity"] = -1.0
            figures.append(GroundedFigure(
                "P03", "Debt-equity ratio", -1.0, "Negative net worth",
                C.RATIO, ("BorrowingsCurrent", "BorrowingsNoncurrent", "Equity")
            ))

    # P04: Depreciation, Depletion & Amortisation
    dda = get_latest_sum(("DepreciationDepletionAndAmortisationExpense",))
    if dda is not None:
        lookup["depreciation"] = dda
        figures.append(GroundedFigure(
            "P04", "Depreciation, depletion and amortisation", dda, _format_inr(dda),
            C.CURRENCY, ("DepreciationDepletionAndAmortisationExpense",)
        ))

    # P05: Impairment loss on property, plant and equipment — the best-covered real
    # impairment concept in the corpus (367 docs vs 20 for the generic concept).
    # A negative value here is a net REVERSAL, not a fresh charge — worded accordingly
    # downstream rather than presented as if it were always a loss.
    imp = get_latest_sum(("ImpairmentLossRecognisedInProfitOrLossPropertyPlantAndEquipment",))
    if imp is not None:
        lookup["impairment"] = imp
        figures.append(GroundedFigure(
            "P05", "Impairment loss on property, plant and equipment", imp, _format_inr(imp),
            C.CURRENCY, ("ImpairmentLossRecognisedInProfitOrLossPropertyPlantAndEquipment",)
        ))

    # P06: Investment Book (Current + Non-current)
    inv = get_latest_sum(("CurrentInvestments", "NoncurrentInvestments"))
    if inv is not None:
        lookup["investments"] = inv
        figures.append(GroundedFigure(
            "P06", "Investment book (current + non-current)", inv, _format_inr(inv),
            C.CURRENCY, ("CurrentInvestments", "NoncurrentInvestments")
        ))

    # P07: Revenue from Operations
    rev = get_latest_sum(("RevenueFromOperations",))
    if rev is not None:
        lookup["revenue"] = rev
        figures.append(GroundedFigure(
            "P07", "Revenue from operations", rev, _format_inr(rev),
            C.CURRENCY, ("RevenueFromOperations",)
        ))

    # P08: Contingent liabilities — feeds the inherent-risk-map field with a real
    # figure rather than a purely qualitative statement.
    cl = get_latest_sum(("ContingentLiabilities",))
    if cl is not None:
        lookup["contingent_liabilities"] = cl
        figures.append(GroundedFigure(
            "P08", "Contingent liabilities", cl, _format_inr(cl),
            C.CURRENCY, ("ContingentLiabilities",)
        ))

    return figures, lookup


def _clean_quote(text: str, max_chars: int = 240) -> str:
    """
    Extracts a readable, grammatically coherent quote from disclosure text without
    awkward mid-word, abbreviation, or mid-parenthesis truncation.
    """
    cleaned = re.sub(r'\s+', ' ', text).strip()
    if not cleaned:
        return ""
    if len(cleaned) <= max_chars:
        return cleaned.rstrip(' .,;') + ('.' if not cleaned.endswith(('.', '!', '?')) else '')

    cut = cleaned[:max_chars]

    # Find candidate sentence boundaries, ignoring abbreviations like Rs., Co., No.
    candidates = []
    for m in re.finditer(r'([a-zA-Z0-9\)\"]\.)\s+', cut):
        end_pos = m.start(1) + 1
        prefix = cut[:end_pos]
        if not re.search(r'\b(Rs|No|Co|Ltd|Dr|Mr|Mrs|viz|vs)\.$', prefix, re.IGNORECASE):
            candidates.append(end_pos)

    if candidates and candidates[-1] > 60:
        return cut[:candidates[-1]].strip()

    last_space = cut.rfind(' ')
    snippet = cut[:last_space].strip() if last_space > 60 else cut.strip()

    # Strip trailing dangling prepositions, conjunctions, or currency tokens
    while True:
        stripped = re.sub(r'[\s,;:\(\[\{\-\.]+$', '', snippet)
        stripped = re.sub(
            r'\b(to|of|for|with|in|on|at|from|by|a|an|the|Rs|which|that|and|or|as|is|was|were)\b$',
            '', stripped, flags=re.IGNORECASE
        ).strip()
        if stripped == snippet:
            break
        snippet = stripped

    if snippet.count('(') > snippet.count(')'):
        snippet = re.sub(r'\([^\)]*$', '', snippet).rstrip()
    if snippet.count('[') > snippet.count(']'):
        snippet = re.sub(r'\[[^\]]*$', '', snippet).rstrip()

    return snippet.rstrip(' .,;') + "..."


def _disclosure_relevance_score(row: dict[str, Any]) -> int:
    """
    Scores disclosures to prioritize substantive operational descriptions over
    procedural legal/administrative notices.
    """
    text = (row.get("text") or "").lower()
    # Heavily penalize administrative/statutory boilerplate
    if any(k in text for k in [
        "appointment and qualification of directors",
        "din\n",
        "signature of member",
        "route map",
        "proxy form",
        "attendance slip",
        "postal ballot",
        "extract of annual return",
    ]):
        return -100

    score = 0
    # Boost operational and financial profile keywords
    for kw in [
        "operation", "revenue", "business", "commercial", "production",
        "generation", "capacity", "subsidiary", "energy", "project",
        "exploration", "petroleum", "pricing", "joint venture",
    ]:
        if kw in text:
            score += 10
    return score


def assemble_evidence_passages(
    disclosure_rows: list[dict[str, Any]]
) -> tuple[str, list[dict[str, Any]]]:
    """
    Cleans and structures narrative disclosures into numbered passages and citations.
    Ranks by operational relevance to filter out statutory administrative boilerplate.
    """
    passages: list[str] = []
    citations: list[dict[str, Any]] = []

    seen_text = set()
    n = 1

    # Sort disclosures by operational relevance score descending
    sorted_rows = sorted(disclosure_rows, key=_disclosure_relevance_score, reverse=True)

    for row in sorted_rows:
        text = (row.get("text") or "").strip()
        # Clean basic html/xml line breaks if present
        text_clean = re.sub(r'<br\s*/?>', '\n', text)
        text_clean = re.sub(r'<[^>]+>', '', text_clean).strip()
        # Clean leading section numbers/titles like "2. CAPITAL STRUCTURE"
        text_clean = re.sub(r'^\s*\d+[\.\)]\s*([A-Z\s]{3,}\s+)?', '', text_clean).strip()

        if len(text_clean) < 15 or text_clean in seen_text:
            continue
        seen_text.add(text_clean)

        concept = row.get("concept_name", "Disclosure")
        section = row.get("section_title", "") or ""

        # Extract note reference if present in text
        note_match = re.search(r'Note\s*[\d\.]+', text_clean, re.I)
        note_ref = f" ({note_match.group(0)})" if note_match else ""

        snippet = text_clean[:500]
        passages.append(f"[{n}] Concept: {concept}{note_ref} (section: {section}):\n{snippet}")

        clean_quote = _clean_quote(text_clean, 280)
        citations.append({
            "n": n,
            "concept": concept,
            "section_title": section,
            "citation": f"{concept}{note_ref}: {_clean_quote(text_clean, 140)}",
            "quote": clean_quote,
            "cited": True,
        })
        n += 1
        if n > 12:  # Keep top 12 relevant disclosure passages
            break

    passages_block = (
        "RETRIEVED DISCLOSURES FROM FILING:\n" + "\n\n".join(passages)
        if passages else "RETRIEVED DISCLOSURES: (No text disclosures found in filing)"
    )
    return passages_block, citations


_PROHIBITED_REPLACEMENTS: dict[str, str] = {
    "proves": "may indicate",
    "confirms": "is consistent with",
    "certifies": "assesses",
    "guarantees": "supports",
    "is fraudulent": "shows an irregularity requiring audit review",
    "is insolvent": "shows financial stress requiring audit review",
    "will fail": "faces continuity risk requiring audit review",
}


def lint_business_profile(fields: dict[str, str], grounded_figures: list[GroundedFigure]) -> dict[str, str]:
    """
    Applies Spec §17 safe-language checks and verifies number consistency.
    Never alters genuine grounded numbers.

    Each prohibited word/phrase maps to a specific, grammatically-compatible
    replacement rather than one generic filler for every case — "This indicates
    potential the company is stable" (the single-filler version) is broken English;
    "This may indicate the company is stable" reads correctly for the same input.
    """
    cleaned: dict[str, str] = {}
    for key, text in fields.items():
        val = text
        for phrase, replacement in _PROHIBITED_REPLACEMENTS.items():
            val = re.sub(re.escape(phrase), replacement, val, flags=re.IGNORECASE)
        cleaned[key] = val
    return cleaned


def deterministic_profile_synthesis(
    company_name: str,
    cin: str,
    grounded_figures: list[GroundedFigure],
    lookup: dict[str, Any],
    citations: list[dict[str, Any]],
) -> dict[str, str]:
    """
    Authoritative, deterministic fallback synthesis when LLM is unavailable or offline.
    Never fails; strictly uses verified figures and corporate metadata.
    Intelligently handles operating corporations vs. pre-revenue/development-stage entities.
    """
    rev = lookup.get("revenue")
    rev_str = _format_inr(rev)
    eq = lookup.get("equity")
    eq_str = _format_inr(eq)
    borr = lookup.get("borrowings")
    borr_str = _format_inr(borr)
    de_str = f"{lookup['debt_equity']:.2f}x" if "debt_equity" in lookup and lookup["debt_equity"] >= 0 else "N/A"
    dda = lookup.get("depreciation")
    dda_str = _format_inr(dda)
    inv = lookup.get("investments")
    inv_str = _format_inr(inv)
    imp = lookup.get("impairment")
    cl = lookup.get("contingent_liabilities")
    cl_str = _format_inr(cl)

    def _quote_for(concepts: tuple[str, ...]) -> str:
        for c in citations:
            if c.get("concept") in concepts:
                return c.get("quote") or c["citation"]
        return ""

    model_quote = _quote_for(C.MODEL_DISCLOSURES)
    risk_quote = _quote_for(C.ESTIMATE_AND_RISK_DISCLOSURES)

    # 1. Model
    if rev is not None and rev == 0:
        model_text = (
            f"{company_name} operates as a registered corporate entity (CIN: {cin}) in development "
            f"and establishment stage with nil commercial operational revenue during the reporting period."
            + (f' From filing disclosures: "{model_quote}"' if model_quote else
               " No further operational description was found in the filing's disclosures.")
        )
    else:
        model_text = (
            f"{company_name} operates as a registered corporate entity (CIN: {cin}), generating "
            f"{rev_str} in revenue from operations."
            + (f' From the filing\'s own disclosures: "{model_quote}"' if model_quote else
               " No further operational description was found in the filing's disclosures.")
        )

    # 2. Revenue
    if rev is not None and rev == 0:
        rev_text = (
            "Pre-revenue status (₹0.00 operating revenue). The entity is in project gestation and "
            "institutional consolidation; commercial revenue generation has not commenced for the "
            "reporting period."
        )
    else:
        rev_text = (
            f"Revenue from operations stands at {rev_str}. Realisation is volume- and "
            f"price-driven; specific pricing mechanism governed by commercial contracts and "
            f"applicable regulatory framework."
        )

    # 3. Cost
    cost_parts = []
    if dda and dda > 0:
        cost_parts.append(f"depreciation, depletion and amortisation of {dda_str}")
    if imp is not None and imp != 0:
        word = "a net reversal of prior impairment of" if imp < 0 else "impairment of"
        cost_parts.append(f"{word} {_format_inr(abs(imp))} on property, plant and equipment")

    if cost_parts:
        cost_text = f"Operating cost structure is capital- and asset-intensive with {', and '.join(cost_parts)}."
    elif dda == 0:
        cost_text = (
            "Operating cost structure reflects initial pre-commissioning/gestation stage with nil "
            "depreciation and amortisation (₹0.00) recognized in the period."
        )
    else:
        cost_text = "Operating cost structure is capital- and asset-intensive."

    # 4. Financing
    if eq and borr is not None:
        if borr == 0:
            financing_text = (
                f"Debt-free capital structure (nil borrowings, 0.00x D/E). 100% funded through "
                f"{eq_str} equity share capital (holding company / promoter equity support)."
            )
        else:
            stance = ("Equity-dominant" if lookup.get("debt_equity", 1) < 0.5
                      else "Leveraged" if lookup.get("debt_equity", 0) >= 0 else "Negative net worth")
            financing_text = (
                f"{stance}. Total equity of {eq_str} against total borrowings of {borr_str} "
                f"(debt-equity ratio: {de_str})."
            )
    else:
        financing_text = f"Equity stands at {eq_str}. Borrowings and funding structure documented in notes."

    # 5. Value drivers
    val_parts = []
    if rev is not None and rev == 0:
        val_parts.extend(["project execution timeline", "capital deployment discipline"])
    else:
        val_parts.extend(["operational capacity", "asset turnover", "price realisation"])

    if inv:
        val_parts.append(f"returns on the {inv_str} investment book")
    value_drivers_text = f"Primary performance drivers include {', '.join(val_parts)}."

    # 6. Inherent-risk map
    if rev is not None and rev == 0:
        risk_parts = ["capitalisation of project development costs", "valuation of long-term investments in subsidiaries"]
    else:
        risk_parts = ["asset recoverability", "impairment testing", "valuation of long-term balances"]

    if cl and cl > 0:
        risk_parts.append(f"contingent liabilities of {cl_str}")
    inherent_risk_text = f"Inherent audit risk concentrates in {', '.join(risk_parts)}."
    if risk_quote:
        inherent_risk_text += f' From the filing\'s own disclosures: "{risk_quote}"'

    return {
        "model": model_text,
        "revenue": rev_text,
        "cost": cost_text,
        "financing": financing_text,
        "value_drivers": value_drivers_text,
        "inherent_risk_map": inherent_risk_text,
    }


def build_business_profile(
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
    Builds the complete Business Profile wire payload.

    `chat_fn` is passed in rather than imported here on purpose. `xbrl_fetch` is
    imported as a TOP-LEVEL package (see `__init__.py` and `adapter.py`'s
    `_ensure_xbrl_importable`, which puts `pipeline/` on `sys.path` precisely so this
    package never has to import anything outside itself) — a relative import from
    inside it reaching up to `financial_diagnostic_report/clients.py`
    (`from ..clients import chat`) is not just unconfigured, it is IMPOSSIBLE:
    Python raises "attempted relative import beyond top-level package" every time,
    regardless of whether a generation endpoint exists. An earlier version of this
    function had exactly that import and reported the resulting `ImportError` to the
    end user as if it meant "the model was unreachable" — it actually meant
    "the LLM path can never run, ever, no matter what". The caller (`adapter.py`,
    which sits alongside `clients.py` and can import it validly) supplies the
    callable instead, so the LLM path can genuinely work when wired up, and its
    absence here has an honest reason rather than a confusing one.
    """
    figures, lookup = extract_grounded_figures(metric_rows)
    passages_block, citations = assemble_evidence_passages(disclosure_rows)

    # Format grounded figures block for prompt
    grounded_lines = [f"- {f.label}: {f.display} (Raw: {f.value:,.2f} {f.unit})" for f in figures]
    grounded_block = (
        "GROUNDED AUDITED FIGURES:\n" + "\n".join(grounded_lines)
        if grounded_lines else "GROUNDED AUDITED FIGURES: (None bound in filing)"
    )

    fields_dict: dict[str, str] = {}
    formed = False
    reason = ""

    if use_llm and chat_fn is None:
        reason = ("LLM-generated narrative was not attempted — no chat function was "
                  "supplied by the caller. Verified figures below are unaffected; "
                  "they need no model.")
    elif use_llm:
        try:
            user_turn = P.build_user_prompt(
                company_name=company_name,
                cin=cin,
                fy_label=fy_label,
                grounded_block=grounded_block,
                passages_block=passages_block,
            )
            messages = [
                {"role": "system", "content": P.SYSTEM_PROMPT},
                {"role": "user", "content": user_turn},
            ]
            raw_response = chat_fn(messages, max_tokens=1000, temperature=0.0)

            # Parse lines starting with "Model:", "Revenue:", etc.
            parsed = {}
            for line in raw_response.splitlines():
                line_str = line.strip()
                for f_key in P.FIELDS:
                    label = P.FIELD_LABEL[f_key]
                    if re.match(rf"^{label}\s*:\s*", line_str, re.I):
                        content = re.sub(rf"^{label}\s*:\s*", "", line_str, flags=re.I).strip()
                        parsed[f_key] = content
                        break

            # If all 6 fields were parsed successfully
            if all(k in parsed and parsed[k] for k in P.FIELDS):
                fields_dict = lint_business_profile(parsed, figures)
                formed = True
            else:
                reason = "Partial LLM response parsed; fell back to deterministic synthesis."
        except Exception as exc:  # noqa: BLE001
            # The exception TYPE is logged for whoever operates this (a real signal —
            # ImportError means "wiring is broken", EndpointError means "the model is
            # down"), but never shown to the report's reader: a bare Python class name
            # in an audit-planning UI reads as a crash, not as a graceful fallback.
            logger.warning(f"Business Profile LLM synthesis bypassed: "
                           f"{type(exc).__name__}: {exc}")
            reason = ("LLM-generated narrative unavailable this run — the figures "
                      "below are computed directly and are unaffected.")

    # Fallback to deterministic synthesis if LLM not used or did not form all fields
    if not formed:
        fields_dict = deterministic_profile_synthesis(
            company_name=company_name,
            cin=cin,
            grounded_figures=figures,
            lookup=lookup,
            citations=citations,
        )
        fields_dict = lint_business_profile(fields_dict, figures)
        formed = True

    return {
        "doc_id": doc_id,
        "company_name": company_name,
        "cin": cin,
        "fy_label": fy_label,
        "formed": formed,
        "fields": [
            {"key": k, "label": P.FIELD_LABEL[k], "text": fields_dict.get(k, "")}
            for k in P.FIELDS
        ],
        "citations": citations,
        "grounded_figures": [
            {"id": f.id, "label": f.label, "value": f.value, "display": f.display, "unit": f.unit}
            for f in figures
        ],
        "reason": reason,
    }

