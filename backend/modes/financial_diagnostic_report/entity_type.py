"""What kind of entity this is — read from the filing, not classified.

WHERE THIS SITS AGAINST §5.1
-----------------------------
§5.1 asks for two different things under one name: the coarse LEGAL FORM (is
this a company, an autonomous body, a statutory corporation, a financial-sector
entity) and the fine-grained BUSINESS MODEL (manufacturing, petroleum, utilities,
eighteen more). The second genuinely needs judgement over messy text — segment
notes, the MD&A, what the entity actually does — and stays unbuilt (M5).

The first does not. A government company is REQUIRED to say so: Explanation to
section 2(45) of the Companies Act, 2013 defines the term, and the statutory
auditor's report has to confirm the entity meets it (C&AG appointment under
section 139(5) turns on this fact). Measured over this corpus, some form of
that statement is found for 37 of 50 entities. So — same as `framework.py` —
this is a read, not a classification: deterministic, cites the sentence it
found, and abstains on the rest rather than guessing.

WHY IT STOPS AT GOVERNMENT COMPANY
-----------------------------------
Two other categories were tried and dropped before shipping:

  NBFC / financial-sector status.  The obvious pattern — "registered under
  section 45-IA of the RBI Act" — is stated by MOST entities to say they are
  NOT one; the CARO clause asks every entity to confirm it either way. A naive
  match reads as confidently wrong more often than right. It needs a negation
  check this module does not yet do, so it abstains rather than ship that risk.

  Statutory corporation / autonomous body.  "Constituted under ... Act" reads
  as often about a departmental committee as about the entity's own legal
  origin (seen live: NTPC's audit committee "constituted under a new
  initiative"). No pattern found in this corpus was specific enough to trust.

Both are gaps, not wrong answers — an entity in neither category returns
UNDETERMINED, never COMPANY by default.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, asdict
from typing import Any

from .engine import ensure_importable

VERSION = "fdr-entity-type-1.0.0"

GOVERNMENT_COMPANY = "GOVERNMENT_COMPANY"
UNDETERMINED = "UNDETERMINED"

LABEL = {
    GOVERNMENT_COMPANY: "Government company",
    UNDETERMINED: "Not stated in the filing",
}

MEANING = {
    GOVERNMENT_COMPANY: "A company within the meaning of section 2(45) of the "
                        "Companies Act, 2013 — more than half its paid-up share "
                        "capital is held by the Central Government, a State "
                        "Government, or both. This is what brings the C&AG's "
                        "audit mandate into play, and it carries the dependency "
                        "lens (§3.3): the entity's position may reflect continued "
                        "government support rather than organic performance.",
    UNDETERMINED: "Neither a government-ownership statement nor any other "
                  "indication of legal form was found in the filing text that "
                  "was read.",
}

_RANK = {"HIGH": 2, "MEDIUM": 1}

# Ordered by specificity. The two HIGH patterns are near-verbatim legal or
# self-description language; the MEDIUM ones are reliable but indirect
# (governance mechanics rather than a direct declaration).
_PATTERNS: tuple[tuple[str, str, str, str], ...] = (
    (GOVERNMENT_COMPANY, "HIGH",
     r"Government Compan(?:y|ies)[^.]{0,80}section\s*2\s*\(\s*45\s*\)",
     "cites the section 2(45) definition of a Government company"),
    (GOVERNMENT_COMPANY, "HIGH",
     r"\(?\s*A\s+Government of India[^.)]{0,20}"
     r"(?:Navratna|Miniratna|Maharatna)[^.)]{0,20}"
     r"(?:Enterprise|Undertaking|PSU|Company)\s*\)?",
     "carries a \"Government of India ... Enterprise\" self-description"),
    (GOVERNMENT_COMPANY, "MEDIUM",
     r"(?:granted|status of|category)[^.]{0,50}(?:Navratna|Miniratna|Maharatna)",
     "states a Navratna/Miniratna/Maharatna rating granted to it"),
    (GOVERNMENT_COMPANY, "MEDIUM",
     r"\bis a Government Compan(?:y|ies)\b|Government Compan(?:y|ies) (?:under|within)\b",
     "states plainly that it is a Government company"),
    (GOVERNMENT_COMPANY, "MEDIUM",
     r"President of India[^.]{0,80}(?:appoint|exercised)|"
     r"appoint[^.]{0,80}President of India",
     "vests the power to appoint its Directors in the President of India"),
)

_MAX_FILINGS = 5
_CONTEXT = 220


@dataclass(frozen=True)
class Detection:
    entity_type: str
    label: str
    confidence: str | None
    basis: str
    meaning: str
    evidence: str = ""
    doc_id: str = ""
    page: int | None = None
    years_checked: int = 0

    @property
    def determined(self) -> bool:
        return self.entity_type != UNDETERMINED

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["determined"] = self.determined
        d["version"] = VERSION
        return d


_UNDETERMINED = Detection(
    entity_type=UNDETERMINED, label=LABEL[UNDETERMINED], confidence=None,
    basis="No statement of legal form was found in this entity's filings.",
    meaning=MEANING[UNDETERMINED],
)

_cache: dict[str, Detection] = {}
_lock = threading.Lock()


def _snippet(content: str, start: int, end: int) -> str:
    """Same discipline as `framework._snippet`: no half-words at either edge."""
    lo, hi = max(0, start), min(len(content), end)
    window = content[lo:hi]
    sentence = re.search(r"(?<=[.;])\s+(?=[A-Z(\"“])", window[: max(1, len(window) // 3)])
    if sentence:
        window = window[sentence.end():]
    elif lo > 0 and not content[lo - 1].isspace():
        space = window.find(" ")
        window = window[space + 1:] if space != -1 else window
    if hi < len(content) and not content[hi].isspace():
        space = window.rfind(" ")
        if space != -1:
            window = window[:space]
    window = " ".join(window.split()).strip(" ,;")
    if hi < len(content):
        window += "…"
    return window


def _scan(content: str) -> tuple[str, str, str, str] | None:
    best: tuple[str, str, str, str] | None = None
    for entity_type, confidence, pattern, basis in _PATTERNS:
        match = re.search(pattern, content, re.I | re.S)
        if not match:
            continue
        if best is not None and _RANK[confidence] <= _RANK[best[1]]:
            continue
        best = (entity_type, confidence, basis,
                _snippet(content, match.start() - 60, match.end() + _CONTEXT))
        if confidence == "HIGH":
            break
    return best


def detect(entity_id: str, *, refresh: bool = False) -> Detection:
    """Read the entity's legal form off its filings, newest first.

    Returns `UNDETERMINED` — not `GOVERNMENT_COMPANY` and not a generic
    "Company" — when nothing was found. §5.1's own rule for this step: abstain
    rather than guess. A default of "Company" would be silently wrong for the
    filings that state something more specific in words this pass does not yet
    catch, which is a gap to close, not a case to paper over with a fallback.
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
    except Exception:  # noqa: BLE001
        return _UNDETERMINED

    result = None
    for row in docs:
        doc_id = row["doc_id"]
        try:
            chunks = db.query(
                "select content, page_ocr_start from text_chunks "
                "where doc_id = %s and content ~* %s limit 10",
                (doc_id, r"Government Compan|Navratna|Miniratna|Maharatna|"
                        r"President of India"))
        except Exception:  # noqa: BLE001
            continue
        best = None
        best_page = None
        for chunk in chunks:
            hit = _scan(chunk.get("content") or "")
            if not hit:
                continue
            if best is not None and _RANK[hit[1]] <= _RANK[best[1]]:
                continue
            best, best_page = hit, chunk.get("page_ocr_start")
            if hit[1] == "HIGH":
                break
        if best is not None:
            entity_type, confidence, basis, evidence = best
            result = Detection(
                entity_type=entity_type, label=LABEL[entity_type],
                confidence=confidence, basis=f"Read from the filing, which {basis}.",
                meaning=MEANING[entity_type], evidence=evidence, doc_id=doc_id,
                page=best_page, years_checked=len(docs))
            break            # newest filing that answers decides

    if result is None:
        result = Detection(**{**asdict(_UNDETERMINED), "years_checked": len(docs)})

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
