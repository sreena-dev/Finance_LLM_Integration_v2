"""Hermetic tests for the intent classifier — no database, no network, no model.

Run from `backend/`:
    python -m modes.financial_diagnostic_report.test_intent

These are the tests worth having on this surface. The classifier is the only
component here that GUESSES: everything downstream reads a value out of an
evaluated report, so it is right or it raises, while a misread question produces
a confident answer to something nobody asked. That is the failure mode that
looks most like success, so it is the one pinned down here.

The vocabulary is derived from the live registries, which means these tests also
fail if a registry change makes a word stop selecting what it used to — which is
the point. A silent vocabulary regression is exactly what this catches.
"""

from __future__ import annotations

import sys

from . import intent as IT

# (question, expected kind, expected subject or None)
CASES: tuple[tuple[str, str, str | None], ...] = (
    # -- explicit ids are the subject, named; nothing is weighed --------------
    ("S04", IT.SIGNAL, "S04"),
    ("is S04 firing?", IT.SIGNAL, "S04"),
    ("why is S03 not evaluated", IT.SIGNAL, "S03"),
    ("RC-WC", IT.CLUSTER, "RC-WC"),
    ("tell me about RC-FUND", IT.CLUSTER, "RC-FUND"),

    # -- a figure named in full outranks incidental word hits ----------------
    ("revenue", IT.FIGURE, "revenue"),
    ("trade receivables", IT.FIGURE, "trade_receivables"),
    ("total assets", IT.FIGURE, "total_assets"),
    # The longest matching phrase wins, or this collapses to `total_assets`.
    ("total current assets", IT.FIGURE, "total_current_assets"),
    ("cash and cash equivalents", IT.FIGURE, "cash_and_cash_equivalents"),
    # A synonym from the spec's own `concept` gloss, not from a curated list.
    ("net worth", IT.FIGURE, "total_equity"),
    # "long"/"term" both hit a diagnostic's title; the exact figure name must
    # still win, which is what the phrase weight is for.
    ("long term borrowings", IT.FIGURE, "long_term_borrowings"),

    # -- themes and diagnostics by their own words ---------------------------
    ("is there working capital stress?", IT.CLUSTER, "RC-WC"),
    ("how is operating cash flow", IT.SIGNAL, "S04"),

    # -- generic intents, reached only when nothing specific matched ---------
    ("what are the risks", IT.OVERVIEW, None),
    ("what is missing?", IT.COVERAGE, None),
    ("what should I fix first?", IT.BLOCKED, None),

    # -- §1.2: conclusions this system does not draw -------------------------
    ("should I invest in this company?", IT.REFUSED, None),
    ("is this a good company", IT.REFUSED, None),
    ("is the company solvent?", IT.REFUSED, None),

    # -- narrative questions go to the retrieval path -----------------------
    # These name no diagnostic and no bound figure, which does not make them bad
    # questions — an annual report holds far more than 27 diagnostics and 46
    # line items. They are answered from the filing's own text, with citations,
    # and abstain on the evidence when it does not contain the answer.
    ("what is the registered office?", IT.DISCLOSURE, None),
    ("what is the CIN", IT.DISCLOSURE, None),
    ("what is the leases accounting policy?", IT.DISCLOSURE, None),
    ("who are the directors", IT.DISCLOSURE, None),
    ("was there an emphasis of matter", IT.DISCLOSURE, None),

    # "overview" appears in the narrative regex this mode borrowed, and must NOT
    # win there: in this mode it names the diagnostic overview. Regression test.
    ("give me an overview", IT.OVERVIEW, None),
    ("what does this entity raise?", IT.OVERVIEW, None),

    # -- not a question about the entity at all -----------------------------
    ("hello", IT.UNSUPPORTED, None),
    ("", IT.UNSUPPORTED, None),
)


def _subject(decision: IT.Intent) -> str | None:
    return decision.signal_id or decision.cluster_id or decision.canonical_key


def test_cases() -> list[str]:
    failures = []
    for question, expected_kind, expected_subject in CASES:
        decision = IT.classify(question)
        if decision.kind != expected_kind:
            failures.append(
                f"{question!r}: expected kind {expected_kind}, got {decision.kind} "
                f"({decision.reason_code})")
            continue
        if expected_subject is not None and _subject(decision) != expected_subject:
            failures.append(
                f"{question!r}: expected subject {expected_subject}, "
                f"got {_subject(decision)}")
    return failures


def test_stopwords_select_nothing() -> list[str]:
    """A function word must never be a distinctive selector.

    This is a regression test with a real failure behind it. One diagnostic's
    title is the only registry entry containing "the", which made "the" a
    perfectly distinctive selector for that one diagnostic — so "what is the
    weather today" scored it and came back AMBIGUOUS instead of UNSUPPORTED.
    Distinctiveness says nothing about whether a word carries meaning.
    """
    vocab = IT.vocabulary()
    failures = []
    for word in IT._STOPWORDS:
        for name, table in (("signal", vocab.signal_words),
                            ("cluster", vocab.cluster_words),
                            ("figure", vocab.figure_words)):
            if word in table:
                failures.append(f"stopword {word!r} selects {name} {table[word]}")
    return failures


def test_reason_codes_are_closed() -> list[str]:
    """Every decision carries a code from the declared set.

    The set is what makes "how often does this misread a question?" a query over
    logs rather than an anecdote, and an undeclared code silently falls out of
    every such count.
    """
    failures = []
    for question, _, _ in CASES:
        code = IT.classify(question).reason_code
        if code not in IT.REASON_CODES:
            failures.append(f"{question!r}: undeclared reason code {code!r}")
    return failures


def test_ambiguity_names_its_alternatives() -> list[str]:
    """An ambiguous reading must say what the choices are.

    Returning "that could mean two things" without naming them leaves the user
    guessing at the disambiguation, which is worse than picking one.
    """
    failures = []
    for question, _, _ in CASES:
        decision = IT.classify(question)
        if decision.kind == IT.AMBIGUOUS and not decision.alternatives:
            failures.append(f"{question!r}: ambiguous with no alternatives offered")
    return failures


def test_refusal_never_reads_the_corpus() -> list[str]:
    """A refusal, an ambiguity or an unsupported question must be answerable
    with no evaluation — `answers.build` is called without one, and must not
    raise. This is what lets the adapter answer them before touching the
    database."""
    from . import answers as ANS

    failures = []
    for question in ("should I invest in this company?", "hello", "asdf"):
        try:
            answer = ANS.build(IT.classify(question))
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{question!r}: raised {type(exc).__name__}: {exc}")
            continue
        if not answer.text.strip():
            failures.append(f"{question!r}: produced an empty answer")
    return failures


def main() -> int:
    suites = (
        ("classification", test_cases),
        ("stopwords select nothing", test_stopwords_select_nothing),
        ("reason codes are closed", test_reason_codes_are_closed),
        ("ambiguity names alternatives", test_ambiguity_names_its_alternatives),
        ("refusal needs no corpus", test_refusal_never_reads_the_corpus),
    )
    total = 0
    for name, suite in suites:
        failures = suite()
        total += len(failures)
        if failures:
            print(f"FAIL  {name}")
            for line in failures:
                print(f"        {line}")
        else:
            print(f"ok    {name}")
    print()
    print("FAILED" if total else f"PASSED — {len(CASES)} classifications, 5 suites")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
