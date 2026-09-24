"""Which accounting framework a filing was prepared under, read from the filing.

WHY THIS IS A READ AND NOT A CLASSIFICATION
-------------------------------------------
Every set of Indian financial statements says which framework it follows, in
almost the same words, because the sentence is compelled: the audit opinion has
to name the framework the statements were prepared under, and the basis-of-
preparation note repeats it. Measured over this corpus, 49 of 50 entities state
it explicitly.

That makes this a pattern match over text the filing volunteers, not a judgement
about the entity — so it is deterministic, it cites the sentence it read, and it
abstains rather than guessing. No model is involved and none is wanted: a model
asked "which framework?" would answer confidently for the one filing that does
not say, which is the only case where the answer matters.

WHAT IT DOES NOT DO
-------------------
It does not decide whether the framework was applied CORRECTLY, or whether the
statements comply with it. That is the Financial Statement Analysis
specification's work. This answers one question — which rulebook do these
statements claim to follow — because format-specific reads downstream assume an
answer to it, and an assumption nobody wrote down is the kind that goes wrong
silently.

WHY IT LIVES HERE AND NOT IN `pipeline/fs_db/`
----------------------------------------------
`pipeline/` is vendored verbatim so it can be re-pulled without a merge
conflict. This is mode-level enrichment over the same database, so it sits
outside that tree and reads through it.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, asdict
from typing import Any

from .engine import ensure_importable

VERSION = "fdr-framework-1.0.0"

IND_AS = "IND_AS"
AS = "AS"
UNDETERMINED = "UNDETERMINED"

LABEL = {
    IND_AS: "Ind AS",
    AS: "Accounting Standards (pre-Ind AS)",
    UNDETERMINED: "Not stated in the filing",
}

# What each framework changes about how the statements are read. Carried with the
# detection because the point of knowing the framework is what follows from it,
# and a label with no consequence attached invites the reader to skip it.
MEANING = {
    IND_AS: "Prepared under the Indian Accounting Standards notified under section 133 "
            "of the Companies Act, 2013. The Schedule III Division II presentation "
            "applies, so the current/non-current split, the ageing schedules and the "
            "statutory ratio note are all expected.",
    AS: "Prepared under the pre-Ind AS Accounting Standards. Several diagnostics in "
        "this report assume a Division II presentation and read differently here — "
        "the ageing schedules and the statutory ratio note may not be present at all.",
    UNDETERMINED: "The filing does not state its framework in the text that was read, "
                  "so format-specific checks proceed on the presentation as printed "
                  "rather than on a stated basis.",
}

# Ordered: the first pattern that matches decides, so the most specific and least
# ambiguous evidence is tried first. Each carries the confidence it earns —
# naming the rules is a stronger read than a bare mention of the standards.
_PATTERNS: tuple[tuple[str, str, str, str], ...] = (
    # The rules are named in either order — "Ind AS ... under section 133" and
    # "Companies (Indian Accounting Standards) Rules, 2015 ... ('Ind AS')" are
    # both definitive, and both are common. Matching only the first ordering
    # recorded a definitive filing at the confidence of an incidental mention.
    (IND_AS, "HIGH",
     r"Companies\s*\(\s*Indian Accounting Standards?\s*\)\s*Rules,?\s*2015",
     "names the Companies (Indian Accounting Standards) Rules, 2015"),
    (IND_AS, "HIGH",
     r"Indian Accounting Standard[s]?\s*\(?\s*[\"“']?Ind[\s-]?AS[\"”']?\s*\)?"
     r"[^.]{0,160}?(?:section\s*133|Rules,?\s*2015)",
     "names Ind AS together with section 133 or the 2015 Rules"),
    (AS, "HIGH",
     r"Companies\s*\(\s*Accounting Standards\s*\)\s*Rules,?\s*2006",
     "names the Companies (Accounting Standards) Rules, 2006"),
    (IND_AS, "MEDIUM",
     r"\bInd[\s-]AS\b",
     "refers to Ind AS"),
    (AS, "MEDIUM",
     r"Accounting Standards[^.]{0,120}?section\s*211",
     "refers to the standards under section 211 of the 1956 Act"),
)

_MAX_FILINGS = 5          # newest first; a framework is stable, so this is plenty
_CONTEXT = 240            # characters of the quoted sentence kept as evidence


@dataclass(frozen=True)
class Detection:
    """One entity's framework, and the sentence that says so."""
    framework: str
    label: str
    confidence: str | None            # HIGH | MEDIUM | None when undetermined
    basis: str                        # why this reading, in one clause
    meaning: str
    evidence: str = ""                # the filing's own words
    doc_id: str = ""
    page: int | None = None
    years_checked: int = 0
    # Set when filings in the same panel disagree. A framework transition inside
    # the comparative window is a real comparability problem, not a detail — it
    # is surfaced rather than resolved by taking the newest and moving on.
    mixed: str = ""

    @property
    def determined(self) -> bool:
        return self.framework != UNDETERMINED

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["determined"] = self.determined
        d["version"] = VERSION
        return d


_UNDETERMINED = Detection(
    framework=UNDETERMINED, label=LABEL[UNDETERMINED], confidence=None,
    basis="No statement of the applicable framework was found in this entity's filings.",
    meaning=MEANING[UNDETERMINED],
)

# Detection is stable for an entity and costs a text scan, so it is remembered
# for the life of the process. Unlike the evaluation cache this holds no figures
# — it is one label per entity — so it does not expire on a TTL.
_cache: dict[str, Detection] = {}
_lock = threading.Lock()


_RANK = {"HIGH": 2, "MEDIUM": 1}


def _snippet(content: str, start: int, end: int) -> str:
    """A quotable span: no half-words at either edge, and a sentence start if
    there is one close by.

    A quotation that opens mid-word ("...quired and give a true and fair view")
    reads as a broken extract rather than as the filing's words, which is the
    opposite of what quoting it is for.
    """
    lo = max(0, start)
    hi = min(len(content), end)
    window = content[lo:hi]

    # Prefer a real sentence start within the leading third of the window.
    sentence = re.search(r"(?<=[.;])\s+(?=[A-Z(\"“])", window[: max(1, len(window) // 3)])
    if sentence:
        window = window[sentence.end():]
    elif lo > 0 and not content[lo - 1].isspace():
        # Mid-word: drop the fragment rather than quote half of it.
        space = window.find(" ")
        window = window[space + 1:] if space != -1 else window

    # Same at the tail: end on a whole word, and close a dangling clause with an
    # ellipsis so the reader knows the sentence continued.
    if hi < len(content) and not content[hi].isspace():
        space = window.rfind(" ")
        if space != -1:
            window = window[:space]
    window = " ".join(window.split()).strip(" ,;")
    if hi < len(content):
        window += "…"
    return window


def _scan(content: str) -> tuple[str, str, str, str] | None:
    """The STRONGEST matching pattern wins: (framework, confidence, basis, evidence).

    Not the first. A filing that mentions "Ind AS" in passing on one page and
    names section 133 on another would otherwise be recorded at the confidence
    of whichever page was read first, which is an accident of ordering rather
    than a property of the filing.
    """
    best: tuple[str, str, str, str] | None = None
    for framework, confidence, pattern, basis in _PATTERNS:
        match = re.search(pattern, content, re.I | re.S)
        if not match:
            continue
        if best is not None and _RANK[confidence] <= _RANK[best[1]]:
            continue
        best = (framework, confidence, basis,
                _snippet(content, match.start() - 60, match.end() + _CONTEXT))
        if confidence == "HIGH":
            break                      # nothing outranks it; stop looking
    return best


def detect(entity_id: str, *, refresh: bool = False) -> Detection:
    """Read the framework off this entity's filings, newest first.

    Returns `UNDETERMINED` rather than a guess when nothing states it — the one
    entity in this corpus that does not say is precisely the one where an
    invented answer would do damage.
    """
    key = (entity_id or "").strip()
    if not key:
        return _UNDETERMINED
    if not refresh:
        with _lock:
            hit = _cache.get(key)
        if hit is not None:
            return hit

    ensure_importable()
    from fs_db import db

    try:
        docs = db.query(
            "select doc_id from documents where company = %s "
            "order by fy_end desc limit %s", (key, _MAX_FILINGS))
    except Exception:  # noqa: BLE001 - a failed read is undetermined, not an outage
        return _UNDETERMINED

    found: list[tuple[str, str, str, str, str, int | None]] = []
    for row in docs:
        doc_id = row["doc_id"]
        try:
            chunks = db.query(
                "select content, page_ocr_start from text_chunks "
                "where doc_id = %s and content ~* %s limit 8",
                (doc_id, r"Accounting Standard"))
        except Exception:  # noqa: BLE001
            continue
        # Best hit ACROSS the filing's candidate chunks, for the same reason
        # `_scan` takes the best within one: which page happened to be read
        # first is not evidence about the filing.
        best: tuple[str, str, str, str, str, int | None] | None = None
        for chunk in chunks:
            hit = _scan(chunk.get("content") or "")
            if not hit:
                continue
            framework, confidence, basis, evidence = hit
            if best is not None and _RANK[confidence] <= _RANK[best[1]]:
                continue
            best = (framework, confidence, basis, evidence, doc_id,
                    chunk.get("page_ocr_start"))
            if confidence == "HIGH":
                break
        if best is not None:
            found.append(best)

    if not found:
        result = Detection(**{**asdict(_UNDETERMINED), "years_checked": len(docs)})
    else:
        # The newest filing decides what the report says the framework IS; the
        # rest are consulted only to notice a transition inside the window.
        framework, confidence, basis, evidence, doc_id, page = found[0]
        others = {f for f, *_ in found}
        mixed = ""
        if len(others) > 1:
            mixed = (f"Filings in this window do not agree on the framework "
                     f"({', '.join(sorted(LABEL[f] for f in others))}). A change of "
                     f"framework inside the comparative period affects whether the "
                     f"years can be compared at all.")
        result = Detection(
            framework=framework, label=LABEL[framework], confidence=confidence,
            basis=f"Read from the filing, which {basis}.",
            meaning=MEANING[framework], evidence=evidence, doc_id=doc_id, page=page,
            years_checked=len(docs), mixed=mixed)

    with _lock:
        _cache[key] = result
    return result


def invalidate(entity_id: str | None = None) -> int:
    with _lock:
        if entity_id is None:
            n = len(_cache)
            _cache.clear()
            return n
        return 1 if _cache.pop(entity_id, None) is not None else 0
