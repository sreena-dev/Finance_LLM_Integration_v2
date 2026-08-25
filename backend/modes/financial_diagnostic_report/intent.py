"""What is this question about? — decided once, with a margin and a reason code.

THE ENTITY IS NOT IN QUESTION HERE
----------------------------------
The source project had to work out WHICH company a question was about, because
its composer had no entity picker. This mode does: the operator selects the
entity before asking, so the only thing left to decide is the SUBJECT — which
diagnostic, which risk theme, or which figure. That is a strictly smaller
decision, and removing the entity-resolution half removes the whole class of
failure where one entity's figures are served under another's name.

NO HAND-WRITTEN FINANCIAL VOCABULARY
------------------------------------
The classifier this replaces counted hits over a keyword list that contained
"raise", "coverage" and "blocked" — ordinary financial English — so a question
about raising borrowings was intercepted and answered as a risk-cluster query.
Every domain vocabulary below is DERIVED at import from the live registries:

    signal ids and titles     <- fdr.signals.SIGNALS
    cluster ids and themes    <- fdr.clusters.CLUSTERS
    canonical figure names    <- fs_db.binding.REGISTRY (key + concept)

and a word is allowed to select a subject only if it is DISTINCTIVE — if it
appears in the vocabulary of exactly one subject. A word shared by several can
never pick one of them, which is what stops "total" or "current" from deciding
anything. The only hand-written sets are grammatical or refer to the register
itself; a specific subject always outranks them.

A WIN NEEDS A MARGIN
--------------------
Scores are compared, and the leader must beat the runner-up by `_MARGIN`. Inside
the margin the answer is AMBIGUOUS and the user is asked, because two plausible
readings answered as one is the failure that looks most like success.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

# ---- kinds ----------------------------------------------------------------
OVERVIEW = "overview"        # what does this entity raise at all
CLUSTER = "cluster"          # one risk theme
SIGNAL = "signal"            # one diagnostic
FIGURE = "figure"            # one disclosed figure, across the panel's years
COVERAGE = "coverage"        # what can and cannot be evaluated, and why
BLOCKED = "blocked"          # which missing figures hold diagnostics back
AMBIGUOUS = "ambiguous"      # two readings, no margin — ask
REFUSED = "refused"          # a judgement this system does not make
DISCLOSURE = "disclosure"    # not a diagnostic — answered from the filing's own text
UNSUPPORTED = "unsupported"  # not a question this mode can serve at all

# ---- reason codes: closed, one per decision -------------------------------
SUBJECT_SIGNAL_ID = "FDR_SUBJECT_SIGNAL_ID"
SUBJECT_CLUSTER_ID = "FDR_SUBJECT_CLUSTER_ID"
SUBJECT_SIGNAL_WORDS = "FDR_SUBJECT_SIGNAL_WORDS"
SUBJECT_CLUSTER_WORDS = "FDR_SUBJECT_CLUSTER_WORDS"
SUBJECT_FIGURE_PHRASE = "FDR_SUBJECT_FIGURE_PHRASE"
SUBJECT_FIGURE_WORDS = "FDR_SUBJECT_FIGURE_WORDS"
INTENT_COVERAGE = "FDR_INTENT_COVERAGE"
INTENT_BLOCKED = "FDR_INTENT_BLOCKED"
INTENT_OVERVIEW = "FDR_INTENT_OVERVIEW"
AMBIGUOUS_SUBJECT = "FDR_AMBIGUOUS_SUBJECT"
REFUSED_JUDGEMENT = "FDR_REFUSED_JUDGEMENT"
UNINTELLIGIBLE = "FDR_UNINTELLIGIBLE"
UNSUPPORTED_SUBJECT = "FDR_UNSUPPORTED_SUBJECT"
ROUTE_DISCLOSURE = "FDR_ROUTE_DISCLOSURE"          # narrative question -> retrieval
ROUTE_DISCLOSURE_QUALITATIVE = "FDR_ROUTE_DISCLOSURE_QUALITATIVE"

REASON_CODES = frozenset({
    SUBJECT_SIGNAL_ID, SUBJECT_CLUSTER_ID, SUBJECT_SIGNAL_WORDS, SUBJECT_CLUSTER_WORDS,
    SUBJECT_FIGURE_PHRASE, SUBJECT_FIGURE_WORDS, INTENT_COVERAGE, INTENT_BLOCKED,
    INTENT_OVERVIEW, AMBIGUOUS_SUBJECT, REFUSED_JUDGEMENT, UNINTELLIGIBLE,
    UNSUPPORTED_SUBJECT, ROUTE_DISCLOSURE, ROUTE_DISCLOSURE_QUALITATIVE,
})

HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"

VERSION = "fdr-intent-1.0.0"

# A subject must beat the runner-up by this much to win outright.
_MARGIN = 2

# ---- the only hand-written sets, and why each is safe ---------------------
#
# These are grammatical, or they name the register itself. None of them is
# financial vocabulary, and every one of them is outranked by a matched subject,
# so none can intercept a question the way the old keyword list did.

# Nouns for the machinery. "Diagnostic" and "abstained" have no ordinary meaning
# in a question about a filing — which is exactly why they are safe and "raised"
# was not.
_REGISTER_NOUNS = frozenset({
    "signal", "signals", "diagnostic", "diagnostics", "abstain", "abstained",
    "abstaining", "unevaluated", "evaluated", "evaluate", "fired", "firing",
    "cluster", "clusters",
})

_COVERAGE_WORDS = frozenset({
    "why", "cannot", "unable", "missing", "unavailable", "readiness", "gaps",
})

_BLOCKED_WORDS = frozenset({
    "blocking", "blocked", "blockers", "holding", "fix", "next",
})

_OVERVIEW_WORDS = frozenset({
    "overview", "summary", "summarise", "summarize", "overall", "anything",
    "everything", "concerns", "concerning", "risks", "headline",
    # "raise" is the exact word whose presence in a flat keyword list caused the
    # original misroute — "which companies raised borrowings" was answered as a
    # risk-cluster query. It is safe HERE and nowhere else, because this set is
    # consulted only after every derived vocabulary has failed to match: in that
    # same question "borrowings" matches a figure first and never reaches this
    # line. Position in the order is what makes the word safe, not the word.
    "raise", "raises", "raised", "raising",
})

# §1.2 — conclusions this system does not draw. Refusing is not a limitation to
# be apologised for: an audit-planning tool that opined on investment merit
# would be making a judgement it has no basis for and no mandate to make.
_GREETINGS = frozenset({
    "hello", "hi", "hey", "yo", "thanks", "thank you", "ta", "ok", "okay",
    "test", "testing", "ping", "help",
})


_JUDGEMENT_PATTERNS = (
    re.compile(r"\b(should|shall)\s+(i|we|you)\b.*\b(invest|buy|sell|lend|approve)\b"),
    re.compile(r"\bis\s+(it|this|the\s+company|the\s+entity)\s+a?\s*(good|bad|safe|risky|"
               r"sound|solid|worthwhile)\b"),
    re.compile(r"\b(going\s+concern|solvent|insolvent|fraud(ulent)?)\b.*\?"),
    re.compile(r"\b(recommend|advise|opinion\s+on)\b.*\b(invest|purchase|acquisition)\b"),
    re.compile(r"\bwill\s+(it|they|the\s+company)\s+(fail|default|collapse|go\s+under)\b"),
)


# Function words, dropped before anything is scored.
#
# This is not a curated domain list — it is grammar, and it is load-bearing. A
# signal titled "...outpacing the revenue" is the ONLY registry entry whose
# vocabulary contains "the", which made "the" a perfectly distinctive selector
# for that one signal: "what is the weather today" scored it and came back
# ambiguous rather than unsupported. Distinctiveness is a good rule that says
# nothing about whether a word carries meaning, so the meaningless ones are
# removed before it is applied. Nothing financial appears below, and no word
# here can name a figure, a diagnostic or a theme.
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "that", "this", "these", "those",
    "are", "was", "were", "has", "have", "had", "its", "their", "his", "her",
    "not", "but", "any", "all", "can", "may", "might", "will", "would",
    "should", "could", "does", "did", "been", "being", "such", "than", "then",
    "there", "here", "when", "what", "which", "who", "whom", "how", "you",
    "your", "our", "out", "into", "onto", "per", "via", "also", "only", "own",
})


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", (text or "").lower())
            if len(w) > 2 and w not in _STOPWORDS]


def _distinctive(vocab: dict[str, set[str]]) -> dict[str, str]:
    """Word -> the single subject it identifies.

    A word claimed by more than one subject identifies none of them and is
    dropped. This is computed, not curated, so adding a signal or a figure
    re-derives the whole map and cannot leave a stale keyword behind.
    """
    counts: Counter = Counter()
    for words in vocab.values():
        counts.update(words)
    return {w: subject for subject, words in vocab.items()
            for w in words if counts[w] == 1}


@dataclass(frozen=True)
class Vocabulary:
    signal_ids: tuple[str, ...]
    signal_titles: dict[str, str]
    signal_words: dict[str, str]        # distinctive word -> signal id
    cluster_ids: tuple[str, ...]
    cluster_themes: dict[str, str]
    cluster_words: dict[str, str]       # distinctive word -> cluster id
    figure_keys: tuple[str, ...]
    figure_phrases: dict[str, str]      # "trade receivables" -> trade_receivables
    figure_words: dict[str, str]        # distinctive word -> canonical key


@lru_cache(maxsize=1)
def vocabulary() -> Vocabulary:
    """Read the live registries. Cached — the registries are import-time constants."""
    from . import engine as ENG

    # Path only, no connection: these registries are stdlib-only constants, so
    # the whole classifier works with no database configured.
    ENG.ensure_importable()

    from fdr import clusters as CL, signals as SG
    from fs_db import binding as B

    signal_titles = {s.id: s.title for s in SG.SIGNALS}
    signal_vocab = {s.id: set(_words(s.title)) for s in SG.SIGNALS}

    cluster_themes = {c.id: c.theme for c in CL.CLUSTERS}
    cluster_vocab = {c.id: set(_words(c.theme)) for c in CL.CLUSTERS}

    # Phrases are collected per key first, then filtered by the same
    # distinctiveness rule as words: a phrase two different figures both claim
    # identifies neither. Without that, "borrowings" — claimed by the long-term
    # and short-term specs alike — would silently resolve to whichever spec the
    # registry happened to list first.
    phrase_claims: dict[str, set[str]] = {}
    figure_vocab: dict[str, set[str]] = {}

    for spec in B.REGISTRY:
        # The key itself, spelled as English: `trade_receivables` -> "trade
        # receivables". Matched as a PHRASE, so neither word alone triggers it.
        spelled = spec.key.replace("_", " ")
        phrase_claims.setdefault(spelled, set()).add(spec.key)

        # `concept` is the spec's own natural-language gloss, comma-separated,
        # and it carries the synonyms a person actually types — "net worth",
        # "shareholders funds". Splitting it into phrases rather than only
        # harvesting its words is what lets a real synonym outrank an incidental
        # word hit somewhere else in the registry.
        for segment in spec.concept.split(","):
            cleaned = " ".join(_words(segment))
            if len(cleaned) > 4:
                phrase_claims.setdefault(cleaned, set()).add(spec.key)

        figure_vocab.setdefault(spec.key, set()).update(_words(spelled))
        figure_vocab[spec.key].update(_words(spec.concept))

    figure_phrases = {phrase: next(iter(keys))
                      for phrase, keys in phrase_claims.items() if len(keys) == 1}

    return Vocabulary(
        signal_ids=tuple(signal_titles),
        signal_titles=signal_titles,
        signal_words=_distinctive(signal_vocab),
        cluster_ids=tuple(cluster_themes),
        cluster_themes=cluster_themes,
        cluster_words=_distinctive(cluster_vocab),
        figure_keys=tuple(figure_vocab),
        figure_phrases=figure_phrases,
        figure_words=_distinctive(figure_vocab),
    )


@dataclass(frozen=True)
class Intent:
    kind: str
    reason_code: str
    confidence: str = MEDIUM
    signal_id: str | None = None
    cluster_id: str | None = None
    canonical_key: str | None = None
    detail: str = ""
    evidence: tuple[str, ...] = ()
    scores: dict[str, int] = field(default_factory=dict)
    alternatives: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "reason_code": self.reason_code,
            "confidence": self.confidence,
            "signal_id": self.signal_id,
            "cluster_id": self.cluster_id,
            "canonical_key": self.canonical_key,
            "detail": self.detail,
            "evidence": list(self.evidence),
            "scores": dict(self.scores),
            "alternatives": list(self.alternatives),
            "version": VERSION,
        }


def classify(question: str) -> Intent:
    """Decide the subject of one question. Decides nothing about the entity."""
    text = (question or "").strip()
    if len(text) < 3:
        return Intent(UNSUPPORTED, UNINTELLIGIBLE, LOW,
                      detail="That is too short to read as a question.")

    lowered = text.lower()

    # Conversational openers are not questions about a filing. Guarded here so
    # they get the capability list instead of a fruitless search of the annual
    # report — grammatical, like the stopword set, and containing nothing that
    # could name a figure, a diagnostic or a theme.
    if lowered.strip(" .!?") in _GREETINGS:
        return Intent(UNSUPPORTED, UNSUPPORTED_SUBJECT, HIGH,
                      detail="That is not a question about the selected entity.")

    for pattern in _JUDGEMENT_PATTERNS:
        if pattern.search(lowered):
            return Intent(
                REFUSED, REFUSED_JUDGEMENT, HIGH,
                detail="That asks for a conclusion this system does not draw. It plans "
                       "audit work from disclosed figures; it does not opine on "
                       "creditworthiness, investment merit, going concern or fraud.",
                evidence=(pattern.pattern,))

    # A question about the entity's NARRATIVE — its identity, an accounting
    # policy, an auditor's remark — is answered from the filing's text, not from
    # a diagnostic. Checked before subject scoring because such a question often
    # also contains a figure word ("what is the policy for trade receivables?"),
    # and answering that with a five-year series of receivables would be a
    # confident answer to a question nobody asked.
    from . import retrieval as RET

    if RET.is_qualitative(lowered):
        return Intent(DISCLOSURE, ROUTE_DISCLOSURE_QUALITATIVE, MEDIUM,
                      detail="Answered from the filing's narrative text.")

    vocab = vocabulary()
    words = _words(lowered)
    word_set = set(words)

    # ---- explicit ids win outright ---------------------------------------
    # A typed "S04" or "RC-WC" is not evidence to be weighed against other
    # evidence; it is the subject, named.
    signal_hit = re.search(r"\bS(\d{1,2})\b", text, re.IGNORECASE)
    if signal_hit:
        candidate = f"S{int(signal_hit.group(1)):02d}"
        if candidate in vocab.signal_titles:
            return Intent(SIGNAL, SUBJECT_SIGNAL_ID, HIGH, signal_id=candidate,
                          evidence=(signal_hit.group(0),))

    cluster_hit = re.search(r"\bRC[-_ ]?([A-Z]{2,5})\b", text, re.IGNORECASE)
    if cluster_hit:
        candidate = f"RC-{cluster_hit.group(1).upper()}"
        if candidate in vocab.cluster_themes:
            return Intent(CLUSTER, SUBJECT_CLUSTER_ID, HIGH, cluster_id=candidate,
                          evidence=(cluster_hit.group(0),))

    # ---- a named figure, matched as a phrase -----------------------------
    # Checked before word scoring because "trade receivables" is unambiguous
    # while "trade" and "receivables" separately are not.
    figure_phrase = None
    for phrase, key in vocab.figure_phrases.items():
        if len(phrase) > 4 and phrase in lowered:
            # Longest phrase wins: "total current assets" must beat "total assets".
            if figure_phrase is None or len(phrase) > len(figure_phrase[0]):
                figure_phrase = (phrase, key)

    # ---- score the subjects ----------------------------------------------
    scores: Counter = Counter()
    evidence: dict[str, list[str]] = {}

    def add(subject: str, points: int, why: str) -> None:
        scores[subject] += points
        evidence.setdefault(subject, []).append(why)

    if figure_phrase:
        # Weighted above any accumulation of single-word hits. Typing a figure's
        # full name is nearly as explicit as typing its id, and it must not lose
        # to two incidental words matched in some diagnostic's title.
        add(f"figure:{figure_phrase[1]}", 6, f"phrase '{figure_phrase[0]}'")

    for word in word_set:
        if word in vocab.signal_words:
            add(f"signal:{vocab.signal_words[word]}", 2, f"word '{word}'")
        if word in vocab.cluster_words:
            add(f"cluster:{vocab.cluster_words[word]}", 2, f"word '{word}'")
        if word in vocab.figure_words:
            add(f"figure:{vocab.figure_words[word]}", 1, f"word '{word}'")

    if scores:
        ranked = scores.most_common()
        top, top_score = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else 0

        if top_score - runner_up < _MARGIN and len(ranked) > 1:
            # Two readings and nothing to separate them. Naming both is the
            # actionable half — the user disambiguates in one word.
            tied = [s for s, n in ranked if n == top_score]
            return Intent(
                AMBIGUOUS, AMBIGUOUS_SUBJECT, LOW,
                detail="That could be read more than one way.",
                evidence=tuple(evidence.get(top, ())),
                scores=dict(scores),
                alternatives=tuple(_describe(s, vocab) for s in tied))

        kind, _, subject = top.partition(":")
        confidence = HIGH if top_score >= 4 else MEDIUM
        if kind == "signal":
            return Intent(SIGNAL, SUBJECT_SIGNAL_WORDS, confidence, signal_id=subject,
                          evidence=tuple(evidence[top]), scores=dict(scores))
        if kind == "cluster":
            return Intent(CLUSTER, SUBJECT_CLUSTER_WORDS, confidence, cluster_id=subject,
                          evidence=tuple(evidence[top]), scores=dict(scores))
        code = SUBJECT_FIGURE_PHRASE if figure_phrase else SUBJECT_FIGURE_WORDS
        return Intent(FIGURE, code, confidence, canonical_key=subject,
                      evidence=tuple(evidence[top]), scores=dict(scores))

    # ---- no subject named: fall back to the generic intents ---------------
    # Only reached when nothing specific matched, so these can no longer
    # intercept a question about a figure or a diagnostic.
    if word_set & _BLOCKED_WORDS:
        return Intent(BLOCKED, INTENT_BLOCKED, MEDIUM,
                      evidence=tuple(sorted(word_set & _BLOCKED_WORDS)))

    if (word_set & _COVERAGE_WORDS) or (word_set & _REGISTER_NOUNS):
        return Intent(COVERAGE, INTENT_COVERAGE, MEDIUM,
                      evidence=tuple(sorted((word_set & _COVERAGE_WORDS)
                                            | (word_set & _REGISTER_NOUNS))))

    if word_set & _OVERVIEW_WORDS:
        return Intent(OVERVIEW, INTENT_OVERVIEW, MEDIUM,
                      evidence=tuple(sorted(word_set & _OVERVIEW_WORDS)))

    # Nothing in the registries matched. That does NOT make it a bad question —
    # they cover 27 diagnostics and 46 line items, while an annual report holds a
    # great deal more. Rather than shrug, hand it to retrieval over this entity's
    # own filing, which either finds the answer or abstains on the evidence.
    return Intent(DISCLOSURE, ROUTE_DISCLOSURE, LOW,
                  detail="No diagnostic or bound figure matches; answering from the "
                         "filing's own text.")


def _describe(subject: str, vocab: Vocabulary) -> str:
    kind, _, ident = subject.partition(":")
    if kind == "signal":
        return f"{ident} — {vocab.signal_titles.get(ident, ident)}"
    if kind == "cluster":
        return f"{ident} — {vocab.cluster_themes.get(ident, ident)}"
    return ident.replace("_", " ")


def capabilities() -> list[str]:
    """What this surface can answer. Returned WITH every refusal, so a declined
    question is answered by telling the user what to ask instead."""
    return [
        "the risks raised for the selected entity — *\"what does this entity raise?\"*",
        "one risk theme — *\"is there working-capital stress?\"* or *\"RC-WC\"*",
        "one diagnostic — *\"is S04 firing?\"* or *\"how is operating cash flow?\"*",
        "one disclosed figure across the years — *\"trade receivables\"*",
        "what could not be evaluated, and why — *\"what is missing?\"*",
        "which figures hold diagnostics back — *\"what should I fix first?\"*",
        "anything else the filing states — *\"what is the registered office?\"*, "
        "*\"what is the leases accounting policy?\"* — read from the report text "
        "with citations",
    ]
