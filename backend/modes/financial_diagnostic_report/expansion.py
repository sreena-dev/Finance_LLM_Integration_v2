"""Query expansion — deterministic always, LLM only when the first pass is weak.

WHY EXPAND AT ALL
-----------------
A question and the filing that answers it are written by different people for
different purposes. The reader asks about "debtors"; the balance sheet says
"Trade Receivables". The reader asks about "subsequent measurement"; the policy
note says "measurement after recognition". A single-vector search bridges some
of that gap and a keyword search bridges almost none of it, so both arms are
handed several phrasings instead of one.

TWO TIERS, AND THE ORDER MATTERS
--------------------------------
  deterministic   always on, costs nothing, cannot fail. Synonyms plus a
                  question-word strip.
  LLM             OFF by default, and used only as a RETRY when the first
                  retrieval pass came back weak.

The source project measured recall@10 at 0.997 on its corpus without LLM
expansion and switched it off for exactly that reason: it added 3-5 seconds per
query to move a number that was already at ceiling. Running it unconditionally
is paying that cost on every question to rescue the few that need it. Running it
adaptively pays only on those few.

WHERE THE SYNONYMS COME FROM
----------------------------
Most of them are DERIVED, not written. `fs_db.binding.REGISTRY` already holds,
for every canonical line item, a natural-language `concept` gloss listing the
ways that line is phrased — that is a synonym dictionary for the statements,
maintained as a side effect of maintaining the binder, and it cannot drift out
of step with the corpus the way a hand-copied list would.

Only the NARRATIVE vocabulary is hand-written, because no registry covers it:
the words for policies, related parties, going concern and the like appear in
report prose and in no line-item spec. That set is small and explicitly listed.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

# Narrative vocabulary — the only hand-written half, and only for concepts that
# live in report prose rather than on the face of a statement. Ported from the
# source project's list, filtered to what an annual report actually contains.
_NARRATIVE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "accounting policy": ("significant accounting policies", "basis of preparation",
                          "material accounting policy information"),
    "related party": ("related parties", "key management personnel", "kmp"),
    "going concern": ("material uncertainty", "ability to continue as a going concern"),
    "contingent liability": ("contingent liabilities", "claims not acknowledged as debt",
                             "commitments"),
    "impairment": ("recoverable amount", "carrying amount", "cash-generating unit"),
    "ecl": ("expected credit loss", "loss allowance", "provision for doubtful debts"),
    "auditor": ("independent auditor's report", "auditor's responsibility",
                "key audit matters"),
    "emphasis of matter": ("emphasis of matter paragraph", "other matter"),
    "subsequent measurement": ("measurement after recognition", "cost model",
                               "fair value model", "revaluation model"),
    "registered office": ("corporate identity number", "corporate information"),
    "segment": ("operating segments", "reportable segments", "segment information"),
    "lease": ("right-of-use asset", "lease liability", "ind as 116"),
    "depreciation": ("useful life", "amortisation", "schedule ii"),
    "provision": ("provisions", "onerous contract", "restructuring"),
}

_QUESTION_WORDS = re.compile(
    r"\b(what|how|why|which|who|whom|when|does|do|did|is|are|was|were|the|a|an|"
    r"of|for|if|to|in|on|tell|me|about|please|show)\b", re.I)


@lru_cache(maxsize=1)
def _registry_synonyms() -> dict[str, tuple[str, ...]]:
    """Line-item synonyms, read out of the binder's own concept glosses.

    `LineSpec.concept` is comma-separated natural language written to describe
    how the corpus phrases that line — "Total shareholders equity, net worth,
    shareholders funds". Split on the commas and it is a synonym set, kept
    current by whoever maintains the binder rather than by anyone remembering
    this file exists.
    """
    from . import engine as ENG

    ENG.ensure_importable()
    from fs_db import binding as B

    out: dict[str, tuple[str, ...]] = {}
    for spec in B.REGISTRY:
        phrases = [p.strip().lower() for p in spec.concept.split(",") if len(p.strip()) > 4]
        spelled = spec.key.replace("_", " ")
        alternatives = tuple(dict.fromkeys(p for p in phrases if p != spelled))
        if alternatives:
            out[spelled] = alternatives
            # Reverse direction too: someone typing a synonym should also reach
            # the canonical spelling the statements actually print.
            for phrase in alternatives:
                out.setdefault(phrase, (spelled,))
    return out


def _strip_question_words(query: str) -> str:
    stripped = _QUESTION_WORDS.sub(" ", query.lower())
    return re.sub(r"\s+", " ", stripped).strip(" ?.!")


def deterministic(query: str, *, limit: int = 3) -> list[str]:
    """Extra phrasings for one query. Never empty of the original."""
    lowered = (query or "").lower()
    variants: list[str] = []

    for table in (_NARRATIVE_SYNONYMS, _registry_synonyms()):
        for term, synonyms in table.items():
            if term in lowered:
                variants.extend(synonyms)

    stripped = _strip_question_words(query)
    if stripped and stripped != lowered.strip():
        variants.append(stripped)

    # Deduplicate preserving order, drop anything that is just the original.
    seen: set[str] = {lowered.strip()}
    out: list[str] = []
    for variant in variants:
        key = variant.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(variant)
    return out[:limit]


def expand(query: str, *, enable: bool = True, limit: int = 3) -> dict[str, Any]:
    """The query plus its deterministic variants."""
    if not enable:
        return {"queries": [query], "variants": [], "method": "none"}
    variants = deterministic(query, limit=limit)
    return {"queries": [query, *variants], "variants": variants,
            "method": "deterministic" if variants else "none"}


def llm_variants(query: str, *, n: int = 2) -> list[str]:
    """Reformulations from the model. The ADAPTIVE tier — call only on a weak pass.

    Never raises: expansion is an optimisation, and a failed optimisation must
    leave the original query working rather than take the answer down.
    """
    from . import clients

    prompt = (
        "Rewrite this question about an Indian company's annual report into "
        f"{n} alternative search queries, using the terminology the report itself "
        "would use (statement line-item names, note headings, policy wording). "
        "Return one query per line, no numbering, no commentary.\n\n"
        f"question: {query}")
    try:
        raw = clients.chat(
            [{"role": "system",
              "content": "You rewrite search queries. You output nothing but the queries."},
             {"role": "user", "content": prompt}],
            max_tokens=120)
    except Exception:  # noqa: BLE001
        return []

    out = []
    for line in (raw or "").splitlines():
        cleaned = re.sub(r"^\s*[-*\d.)]+\s*", "", line).strip()
        if cleaned and cleaned.lower() != query.strip().lower():
            out.append(cleaned)
    return out[:n]
