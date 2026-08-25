"""Chat guardrails: keep the assistant within its scope.

Deterministic (regex) fast-path checks for prompt-injection and investment-advice
requests, a helper listing the companies actually in the corpus, and the canned
replies used when a guardrail fires. Scope (in-domain vs out-of-domain) is decided
by the planner LLM; the no-evidence case is decided by a grounding sentinel — both
handled in the pipeline. Nothing here calls the LLM.
"""

from __future__ import annotations

import re

from yukta_rag.core.db import get_connection

# --- prompt-injection / jailbreak ------------------------------------------------
INJECTION_RE = re.compile(
    r"ignore\s+(all\s+|the\s+|your\s+|previous\s+|prior\s+|above\s+)*"
    r"(instructions?|prompts?|rules?|context)"
    r"|disregard\s+(the\s+|all\s+|your\s+|previous\s+|above\s+)*(instructions?|rules?|prompt)"
    r"|(reveal|print|show|repeat|expose|leak)\s+(me\s+)?(your\s+|the\s+)*"
    r"(system\s+)?(prompt|instructions?|rules)"
    r"|what\s+(is|are)\s+your\s+(system\s+)?(prompt|instructions?)"
    r"|you\s+are\s+now\b|act\s+as\s+(a|an|the)\b|pretend\s+(to\s+be|you)"
    r"|developer\s+mode|jailbreak|\bDAN\b|bypass\s+(your\s+)?(rules|guardrails|filters)",
    re.I,
)

# --- investment / trading advice (kept tight: accounting 'provision' etc. excluded) ---
ADVICE_RE = re.compile(
    r"should\s+i\s+(buy|sell|invest|hold|purchase|book|exit|trade)"
    r"|(good|best|worth)\s+(stock|share|investment|buy|bet)\b"
    r"|worth\s+(buying|investing|selling|holding)"
    r"|(share|stock)\s+price\s+(target|forecast|prediction|outlook|tip)"
    r"|(target|predict|forecast)\s+(the\s+)?(share|stock)\s+price"
    r"|will\s+(the\s+)?(stock|share|price|it)\s+(rise|fall|go\s+up|go\s+down|increase|drop)"
    r"|recommend\s+(a\s+|any\s+|some\s+)?(stock|share|investment)"
    r"|is\s+it\s+a\s+(good|safe)\s+(investment|buy|stock|bet)",
    re.I,
)

_SCOPE_CAPABILITIES = (
    "I'm a financial-statement and audit-analysis assistant. I can help with:\n"
    "- figures and disclosures from the ingested company **annual reports** "
    "(revenue, profit, assets, borrowings, ratios, etc.)\n"
    "- **Indian Accounting Standards** (Ind AS), Schedule III, CARO, SA 700 and EAC opinions\n"
    "- **trial-balance / audit** analysis of a file you upload\n"
)

SCOPE_REPLY = (
    _SCOPE_CAPABILITIES
    + "\nYour question looks outside that scope, so I can't help with it. "
    "Please ask something about the reports, accounting standards, or a trial balance."
)

INJECTION_REPLY = (
    "I can't change my instructions or reveal my configuration. "
    + _SCOPE_CAPABILITIES
    + "\nAsk me a financial-statement, accounting-standard, or trial-balance question and I'll help."
)

ADVICE_REPLY = (
    "I can't provide investment advice, recommendations, or price predictions — this "
    "assistant does factual analysis, not advice, and nothing here is investment advice. "
    "I can, however, give you the reported figures and analysis from the annual reports "
    "(e.g. a company's revenue, profit, margins, leverage or year-over-year change). "
    "What would you like to look at?"
)

_companies_cache: list[str] | None = None


def available_companies() -> list[str]:
    """Distinct company names in the annual-report corpus (cached, best-effort)."""
    global _companies_cache
    if _companies_cache is not None:
        return _companies_cache
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT company FROM documents ORDER BY company")
                _companies_cache = [r[0].replace("_", " ") for r in cur.fetchall() if r[0]]
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - listing is a nicety, never fatal
        _companies_cache = []
    return _companies_cache


def no_evidence_reply() -> str:
    comps = available_companies()
    listed = ""
    if comps:
        head = ", ".join(comps[:12])
        more = f" and {len(comps) - 12} more" if len(comps) > 12 else ""
        listed = f"\n\nI currently have annual reports for: {head}{more}."
    return ("I couldn't find that in the available reports. It may be about a company or "
            "period not in the corpus, or a detail the reports don't cover. Try naming the "
            "company and financial year, or ask about the standards / a trial balance."
            + listed)


def check_input(question: str) -> dict | None:
    """Deterministic input guardrails. Returns a refusal record, or None to proceed."""
    if INJECTION_RE.search(question):
        return {"kind": "injection", "answer": INJECTION_REPLY}
    if ADVICE_RE.search(question):
        return {"kind": "advice", "answer": ADVICE_REPLY}
    return None
