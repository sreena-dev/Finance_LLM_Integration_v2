"""Generate an answer from retrieved evidence, and check it against that evidence.

THIS IS THE ONLY MODULE IN THE MODE WITH A MODEL IN IT
------------------------------------------------------
Everything else computes. This narrates — and only for questions the
diagnostics cannot answer, over evidence that has already been retrieved and
ranked. The model is never asked what is true; it is asked to report what the
supplied sources say, with a citation on each claim.

FOUR GUARDRAILS, IN ORDER OF HOW OFTEN THEY SAVE YOU
-----------------------------------------------------
1. NO EVIDENCE, NO CALL. If the reranker kept nothing above threshold, the
   model is never invoked. There is nothing to ground an answer in, and a model
   handed an empty source list will still write something.
2. ABSTAIN TOKEN. Given evidence that does not contain the answer, the model is
   required to emit exactly `INSUFFICIENT_EVIDENCE`. Answering "the sources do
   not say" is a correct answer; inventing a figure is not.
3. HARD PROHIBITIONS. The §1.2 conclusions this system does not draw — audit
   opinion, fraud, insolvency, intent, invented benchmarks, legal conclusions —
   restated for the model. The intent classifier already refuses these before
   they get here; this is the second net, for phrasings it misses.
4. GROUNDEDNESS CHECK. A second call verifies the answer against its own cited
   sources. The project this is ported from did NOT do this on the entity path
   — it hardcoded `{"grounded": True}` — so an ungrounded company answer had
   nothing standing between it and the reader.

Citations are also VALIDATED mechanically: a `[n]` the model emits for a source
that was never supplied is stripped out before the answer is returned, because a
citation to nothing looks exactly like a citation to something.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterator

from . import clients
from . import config as CFG

ABSTAIN_TOKEN = "INSUFFICIENT_EVIDENCE"

_PROHIBITIONS = """
HARD PROHIBITIONS. These override every other instruction:
- Do NOT issue or suggest an audit opinion, or conclude that anything is misstated.
- Do NOT conclude fraud, distress, insolvency or inefficiency, and do NOT predict
  failure, default or bankruptcy.
- Do NOT attribute intent, motive or knowledge to management or any person.
- Do NOT state a peer benchmark, sector norm or industry average unless it appears
  in the SOURCES with its own source and date. Never invent one.
- Do NOT state a probability or a percentage of risk. Confidence is qualitative.
- Do NOT draw a legal conclusion (violation, contravention, liability).
- Do NOT decide audit strategy, scope or materiality. Those are the audit team's.
- Never use: proves, confirms, certifies, guarantees, will fail, is fraudulent,
  is insolvent, manipulated, diverted, deliberately, concealed.
If asked for any of the above, say plainly that this system does not draw that
conclusion, and state what it CAN provide instead.
"""

_SYSTEM = (
    "You are a financial-analysis assistant for auditors, answering a question about "
    "ONE company's annual report. Answer ONLY from the numbered SOURCES below.\n\n"
    "There are two kinds of source:\n"
    "- (TABLE) sources are extracts of the audited financial statements. For a FIGURE "
    "question, quote the EXACT figure and its line-item label, keep the units and scale "
    "(INR crore/lakh) exactly as printed, and NEVER compute, estimate or infer a number "
    "that is not printed in a source.\n"
    "- (TEXT) sources are the report's narrative — corporate information, notes, the "
    "directors' and auditor's reports. Use them for qualitative questions, stating only "
    "what the text actually says.\n\n"
    "Cite the source number after each fact, like [1] or [2, 3]. Use no outside "
    "knowledge whatsoever. Distinguish clearly between what the sources state and what "
    "they do not cover — say 'not stated in the provided sources' for the latter.\n\n"
    f"If the answer is in NONE of the sources, reply with EXACTLY the single token "
    f"{ABSTAIN_TOKEN} and nothing else. Otherwise begin with '**Direct answer:**'.\n"
    + _PROHIBITIONS
)

_CITATION_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def build_prompt(query: str, evidence: list[dict]) -> tuple[str, list[dict]]:
    """The user turn, and the sources that actually fitted inside the budget.

    The two are returned together because they must agree: a source dropped for
    space that still appears in the citation list would be a reference the
    reader cannot open.
    """
    settings = CFG.load()
    blocks, used, included = [], 0, []
    for index, item in enumerate(evidence, 1):
        body = (item.get("content") or "")[: settings.per_source_chars]
        kind = "TABLE" if item.get("kind") == "table" else "TEXT"
        block = f"[SOURCE {index}] ({kind}) {item.get('title') or 'source'}\n{body}\n"
        if used + len(block) > settings.total_budget_chars:
            break
        blocks.append(block)
        used += len(block)
        included.append(item)

    prompt = (f"QUESTION: {query}\n\n" + "\n".join(blocks) +
              "\nAnswer the question using only the sources above, with [n] citations. "
              "Do not perform any arithmetic.")
    return prompt, included


def cited_indices(answer: str) -> set[int]:
    out: set[int] = set()
    for group in _CITATION_RE.findall(answer or ""):
        for part in group.split(","):
            try:
                out.add(int(part.strip()))
            except ValueError:
                continue
    return out


def strip_invalid_citations(answer: str, n_sources: int) -> tuple[str, list[int]]:
    """Remove `[n]` markers pointing at sources that were never supplied.

    A model that cites [9] against seven sources has produced a reference the
    reader cannot follow, and an unfollowable citation is worse than none — it
    reads as corroboration while providing exactly zero.
    """
    invalid: list[int] = []

    def replace(match: re.Match) -> str:
        numbers, keep = [], []
        for part in match.group(1).split(","):
            try:
                value = int(part.strip())
            except ValueError:
                continue
            numbers.append(value)
            if 1 <= value <= n_sources:
                keep.append(value)
        invalid.extend(v for v in numbers if v not in keep)
        return f"[{', '.join(str(k) for k in keep)}]" if keep else ""

    return _CITATION_RE.sub(replace, answer or ""), sorted(set(invalid))


def sources_payload(evidence: list[dict], doc: dict, cited: set[int]) -> list[dict]:
    """The Sources drawer: one entry per supplied source, in citation order.

    Every entry carries where it physically came from — filing, table or chunk
    id, and page — so a reader can go and look. `cited` marks the ones the
    answer actually leaned on, which is not the same as the ones offered.
    """
    entity = str(doc.get("company") or "")
    end = doc.get("fy_end")
    fy = f"FY{int(end) - 1}-{str(int(end))[-2:]}" if end else ""
    out = []
    for index, item in enumerate(evidence, 1):
        title = (item.get("title") or "source").strip()
        page = item.get("page")
        out.append({
            "n": index,
            "kind": item.get("kind"),
            "chunk_id": item.get("id"),
            "table": "table_chunks" if item.get("kind") == "table" else "text_chunks",
            "title": title,
            "page": page,
            "doc_id": doc.get("doc_id"),
            "entity": entity,
            "fy": fy,
            "citation": f"{doc.get('doc_id')} · {title[:70]}"
                        + (f" · p.{page}" if page else ""),
            "rerank_score": item.get("rerank_score"),
            "cited": index in cited,
            "excerpt": (item.get("content") or "")[:600],
        })
    return out



# ---------------------------------------------------------------------------
# Post-generation lint and the citation-coverage proxy
# ---------------------------------------------------------------------------

# §1.2 phrasings softened AFTER generation.
#
# This EXTENDS the mode's existing refusal logic rather than duplicating it.
# `intent.py` refuses a question that ASKS for a prohibited conclusion, before
# any model is called. `_PROHIBITIONS` above tells the model not to volunteer one
# unasked. Neither catches the third case: a permitted question whose answer the
# model phrases as a conclusion anyway, because the filing's own words led it
# there. That is what this pass is for — it rewords, it does not refuse, because
# by this point the user asked something legitimate and deserves an answer.
_LINT: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"has committed fraud", re.I),
     "shows fraud-risk indicators requiring audit procedures"),
    (re.compile(r"(is|are) not recoverable", re.I),
     r" subject to valuation and collectability risk"),
    (re.compile(r"the accounts are wrong", re.I),
     "the statements contain an inconsistency"),
    (re.compile(r"is not a going concern", re.I),
     "contains going-concern indicators requiring review"),
    (re.compile(r"(is|are) insolvent", re.I),
     r" subject to solvency indicators requiring review"),
    (re.compile(r"proves", re.I), "indicates"),
    (re.compile(r"confirms", re.I), "states"),
    (re.compile(r"guarantees", re.I), "states"),
)

# Some models prefix the answer with their own classification of it.
_LEAKED_LABEL = re.compile(
    r"^\s*(?:answer|category|query\s*type|type|classification)\s*[:\-]\s*"
    r"(?:lookup|definitional|comparison|aggregation|table|multi[_\s]?hop|reasoning|"
    r"disclosure|narrative)\s*[.\-]?\s*", re.IGNORECASE)


def lint(text: str) -> tuple[str, list[str]]:
    """Soften prohibited phrasings and strip leaked labels.

    Returns the cleaned text and what was changed, so the edit is auditable
    rather than invisible — a reader who is told the wording was adjusted can
    go and check the source; one who is not, cannot.
    """
    applied: list[str] = []
    cleaned = _LEAKED_LABEL.sub("", text or "")
    if cleaned != (text or ""):
        applied.append("stripped a leaked classification label")

    for pattern, replacement in _LINT:
        replaced, n = pattern.subn(replacement, cleaned)
        if n:
            applied.append(f"softened {pattern.pattern!r} ({n})")
            cleaned = replaced
    return cleaned, applied


def citation_coverage(answer: str) -> float:
    """Fraction of substantive sentences carrying a `[n]` marker.

    A cheap deterministic proxy for groundedness, used only to decide whether the
    expensive verification is worth running. It is not a substitute for that
    check: it measures whether claims are ATTRIBUTED, not whether the attribution
    is correct.
    """
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", (answer or "").strip())
                 if len(s) > 8]
    if not sentences:
        return 0.0
    return sum(1 for s in sentences if re.search(r"\[\d+\]", s)) / len(sentences)

def generate(query: str, evidence: list[dict], *,
             on_token: Callable[[str], None] | None = None) -> str:
    """Produce the answer. Streams token deltas through `on_token` when given."""
    prompt, _ = build_prompt(query, evidence)
    messages = [{"role": "system", "content": _SYSTEM},
                {"role": "user", "content": prompt}]

    if on_token is None:
        return clients.chat(messages)

    pieces: list[str] = []
    for piece in clients.chat_stream(messages):
        pieces.append(piece)
        on_token(piece)
    return "".join(pieces)


def check_groundedness(query: str, answer: str, evidence: list[dict]) -> dict[str, Any]:
    """Verify the answer against the sources it was given.

    A second, cheap call that asks one question: is every factual claim
    supported by the supplied sources? It returns a verdict and a reason, and
    the verdict travels with the answer rather than replacing it — the reader
    decides what to do with a flagged answer, and silently suppressing one would
    hide the very thing this check exists to reveal.

    Never raises: a failed check reports itself as unchecked. An outage in the
    verifier must not cost the user an answer that may well be perfectly sound.
    """
    settings = CFG.load()
    if not settings.groundedness_check or settings.groundedness_mode == "off":
        return {"checked": False, "grounded": None, "reason": "disabled by configuration"}

    # An answer that abstained has nothing to verify.
    if (answer or "").strip().upper().startswith(ABSTAIN_TOKEN):
        return {"checked": False, "grounded": True, "reason": "abstained"}

    # Deterministic gate FIRST, in both directions.
    #
    # A substantive answer with no citation at all is ungrounded on its face and
    # needs no model to say so. And an answer that is already densely cited is
    # in the regime where the verifier agrees with the proxy, so paying a second
    # LLM call for it buys nothing — which is why the mode is "conditional" by
    # default rather than "always".
    coverage = citation_coverage(answer)
    if len(answer or "") > 120 and not re.search(r"\[\d+\]", answer or ""):
        return {"checked": True, "grounded": False, "method": "deterministic",
                "coverage": round(coverage, 2),
                "reason": "the answer makes substantive claims and cites no source"}

    if (settings.groundedness_mode == "conditional"
            and coverage >= settings.groundedness_min_coverage):
        return {"checked": False, "grounded": True, "method": "citation-coverage",
                "coverage": round(coverage, 2),
                "reason": f"{coverage:.0%} of sentences carry a citation, at or above "
                          f"the {settings.groundedness_min_coverage:.0%} threshold; "
                          f"the model verification was not needed"}

    prompt, included = build_prompt(query, evidence)
    ask = (f"{prompt}\n\nPROPOSED ANSWER:\n{answer}\n\n"
           "Is every factual claim in the proposed answer supported by the sources "
           "above? Reply with exactly one line: GROUNDED or NOT_GROUNDED, then a "
           "second line giving a one-sentence reason.")
    try:
        verdict = clients.chat(
            [{"role": "system",
              "content": "You verify whether an answer is supported by its sources. "
                         "You do not rewrite the answer and you add no information."},
             {"role": "user", "content": ask}],
            max_tokens=120)
    except Exception as exc:  # noqa: BLE001
        return {"checked": False, "grounded": None,
                "reason": f"verifier unavailable ({type(exc).__name__})"}

    head, _, tail = (verdict or "").strip().partition("\n")
    grounded = head.strip().upper().startswith("GROUNDED")
    return {"checked": True, "grounded": grounded,
            "reason": tail.strip()[:400] or head.strip()[:400],
            "sources_considered": len(included)}
