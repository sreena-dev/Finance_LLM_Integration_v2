"""
Conformance against the hand-authored reference report — the acceptance test for the matrix.

WHAT THIS TESTS, AND WHY IT IS NOT AN INTEGRATION TEST
------------------------------------------------------
`../SRS/fdr_report_ca.html` is a hand-written ONGC FY2025-26 report, kept as the
presentation target for the audit-planning matrix. Seven themes, each with its affected
assertions, its specialist referral and its candidate response. It is a picture, and a
picture cannot fail — so it drifts from the engine silently and nobody finds out until
somebody compares them by eye.

This turns it into a test of ONE question: CAN THE ENGINE EXPRESS EACH ROW AT ALL?

That is deliberately not "does the engine produce these figures for ONGC". Figures need the
corpus, a live database and a panel, and a test that needs those cannot run in the same
place as the rest of `fdr`, which is stdlib-only by design. It is also the less useful
question right now: the engine's real gap against the reference was never arithmetic, it
was VOCABULARY AND STRUCTURE — two of the seven themes had no cluster to live in, and three
more could only be named in generic Appendix D terms.

So each reference row is checked for:

    HOME        a cluster or sector theme whose subject matter is that row's
    ASSERTIONS  the row's affected assertions are all carried by that home
    SPECIALIST  the row's specialist referral is expressible
    REACHABLE   at least one contributing signal has an implemented rule

The first three are structural and must pass. REACHABLE is REPORTED, not asserted: a row
that cannot fire yet is a known state of the build, and failing the suite for it would mean
the suite goes red for as long as note extraction is outstanding — which trains people to
ignore it. The count is printed instead, so a drop is visible without being fatal.
"""
from __future__ import annotations

from . import assertions as A
from . import clusters as CL
from . import rules as R
from . import sector_themes as ST
from . import signals as SG

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        _fails.append(msg)


# =====================================================================================
# The reference report's seven rows, transcribed from `SRS/fdr_report_ca.html`.
#
# `home` is (business_model, cluster_id, sector_theme_id_or_None). The business model is
# `petroleum` throughout because the reference entity is an exploration-and-production
# company — which is the point of the sector overlay: the same clusters read differently
# for a manufacturer, and the reference row titles are the petroleum reading.
# =====================================================================================

REFERENCE_ROWS: tuple[dict, ...] = (
    {
        "rank": 1,
        "theme": "Reserve-linked depletion & impairment estimate quality",
        "home": ("petroleum", "RC-CAP", "ST-OG-DEPL"),
        "assertions": frozenset({A.VALUATION_AND_ALLOCATION, A.ACCURACY}),
        "specialist_words": ("reservoir", "valuation"),
    },
    {
        "rank": 2,
        "theme": "Decommissioning / site-restoration provisioning",
        "home": ("petroleum", "RC-EST", None),
        "assertions": frozenset({A.COMPLETENESS, A.VALUATION_AND_ALLOCATION}),
        "specialist_words": ("actuarial",),
    },
    {
        "rank": 3,
        "theme": "Contingent-liability & arbitration exposure",
        "home": ("petroleum", "RC-CONT", None),
        "assertions": frozenset({A.COMPLETENESS, A.PRESENTATION, A.CLASSIFICATION}),
        "specialist_words": ("legal",),
    },
    {
        "rank": 4,
        "theme": "Exploratory-well capitalisation & CWIP ageing",
        "home": ("petroleum", "RC-CAP", "ST-OG-EXPL"),
        "assertions": frozenset({A.EXISTENCE, A.VALUATION_AND_ALLOCATION, A.CLASSIFICATION}),
        "specialist_words": ("reservoir",),
    },
    {
        "rank": 5,
        "theme": "Revenue & receivable quality",
        "home": ("petroleum", "RC-REC", None),
        "assertions": frozenset({A.EXISTENCE, A.CUTOFF, A.VALUATION_AND_ALLOCATION}),
        "specialist_words": (),
    },
    {
        "rank": 6,
        "theme": "Investment-income dependency & FVOCI volatility",
        "home": ("petroleum", "RC-INV", None),
        "assertions": frozenset({A.VALUATION_AND_ALLOCATION, A.OCCURRENCE, A.CLASSIFICATION}),
        "specialist_words": ("valuation",),
    },
    {
        "rank": 7,
        "theme": "Administered-price & government-support dependency",
        "home": ("petroleum", "RC-DEP", "ST-OG-PRICE"),
        "assertions": frozenset({A.ACCURACY, A.OCCURRENCE}),
        "specialist_words": ("regulatory",),
    },
)


def _home_cluster(row: dict) -> CL.Cluster | None:
    return CL.BY_ID.get(row["home"][1])


def _home_theme(row: dict) -> ST.SectorTheme | None:
    tid = row["home"][2]
    return ST.BY_ID.get(tid) if tid else None


def test_every_reference_row_has_a_home() -> None:
    """The failure this exists to catch: a theme with nowhere in the engine to live."""
    for row in REFERENCE_ROWS:
        c = _home_cluster(row)
        check(c is not None,
              f"reference row {row['rank']} ({row['theme']}) maps to cluster "
              f"{row['home'][1]}, which does not exist")
        tid = row["home"][2]
        if tid:
            check(tid in ST.BY_ID,
                  f"reference row {row['rank']} needs sector theme {tid}, which does not exist")


def test_sector_themes_carry_the_reference_wording() -> None:
    """A row named after the generic cluster does not tell an audit team what to look at."""
    for row in REFERENCE_ROWS:
        t = _home_theme(row)
        if t is None:
            continue
        model = row["home"][0]
        check(model in t.business_models,
              f"{t.id} does not apply to {model}, but reference row {row['rank']} needs it to")
        # Not string equality: the reference is prose and the engine's wording is its own.
        # What must hold is that the theme is ABOUT the same thing, which the distinctive
        # nouns of the reference title establish.
        distinctive = [w for w in row["theme"].lower()
                       .replace("&", " ").replace("/", " ").split()
                       if len(w) > 5]
        overlap = [w for w in distinctive if w[:6] in t.theme.lower()]
        check(len(overlap) >= 2,
              f"{t.id} theme {t.theme!r} does not read as reference row {row['rank']} "
              f"({row['theme']!r}) — shared distinctive words: {overlap}")


# Assertions the reference report attaches to a row that its Appendix D cluster does NOT
# carry. Each is a real divergence between the reference and the specification's own column
# 3, and every one of them looks defensible on its face — a receivables theme plainly engages
# existence, which is why confirmations are the standard response. That is exactly why they
# are recorded here rather than silently added to `clusters.py`: widening a cluster's
# assertion set changes the matrix for every entity in the corpus and is the same class of
# decision as adding a cluster (ROADMAP §16 decision 3). The audit side decides; this table
# is the queue.
#
# The test below allows only the divergences listed here. A NEW one still fails the suite,
# which is the property that matters — the known set cannot quietly grow.
KNOWN_ASSERTION_DIVERGENCES: dict[tuple[str, str], str] = {
    ("RC-EST", A.COMPLETENESS): (
        "Reference row 2 (decommissioning) treats completeness of the obligation as being at "
        "risk. App D scopes RC-EST to valuation and accuracy — the question of whether a "
        "provision is COMPLETE arguably belongs to it as much as whether it is right."),
    ("RC-CAP", A.CLASSIFICATION): (
        "Reference row 4 puts the capitalise-versus-expense boundary under classification. "
        "App D scopes RC-CAP to existence, valuation and accuracy; classification is the "
        "assertion the boundary actually threatens."),
    ("RC-REC", A.EXISTENCE): (
        "Reference row 5 names existence, which is what a receivables confirmation tests. "
        "App D scopes RC-REC to occurrence, cut-off and valuation — occurrence covers the "
        "revenue side, and existence the balance, so the two are not interchangeable."),
    ("RC-DEP", A.ACCURACY): (
        "Reference row 7 reads administered pricing as an accuracy question about the "
        "realisation recorded. App D scopes RC-DEP to completeness and classification, which "
        "describes the grant side of the cluster but not the pricing side."),
    ("RC-DEP", A.OCCURRENCE): (
        "As above: reference row 7 attaches occurrence of revenue recognised under a notified "
        "price. App D's RC-DEP does not carry it."),
}


def test_affected_assertions_are_carried() -> None:
    """§10.3 — the assertions at risk are a matrix column, not a narrative aside."""
    for row in REFERENCE_ROWS:
        c = _home_cluster(row)
        if c is None:
            continue
        for a in sorted(row["assertions"] - c.assertions):
            check((c.id, a) in KNOWN_ASSERTION_DIVERGENCES,
                  f"reference row {row['rank']} ({row['theme']}) names assertion {a!r} that "
                  f"{c.id} does not carry, and it is not a recorded divergence. Either add it "
                  f"to the cluster (an audit decision) or record why it differs.")


def test_recorded_divergences_are_still_real() -> None:
    """A divergence that has been resolved must be removed, or the queue rots."""
    for (cid, a), why in KNOWN_ASSERTION_DIVERGENCES.items():
        c = CL.BY_ID.get(cid)
        check(c is not None, f"divergence recorded against unknown cluster {cid}")
        if c is None:
            continue
        check(a not in c.assertions,
              f"{cid} now carries {a!r}; the recorded divergence is stale and should be deleted")
        check(len(why) > 60, f"divergence {cid}/{a} has no usable reason")


def test_specialist_referral_is_expressible() -> None:
    """§10.3 — a referral the engine cannot name cannot be planned for."""
    for row in REFERENCE_ROWS:
        c = _home_cluster(row)
        if c is None:
            continue
        t = _home_theme(row)
        available = " ".join(list(c.specialist) + list(t.specialist if t else ())).lower()
        for word in row["specialist_words"]:
            check(word in available,
                  f"reference row {row['rank']} refers to a {word!r} specialist; "
                  f"{c.id}{'/' + t.id if t else ''} names only {available!r}")


def test_extension_rows_are_marked_as_proposed() -> None:
    """Rows 3 and 6 exist only because of an extension that has not been signed off."""
    for row in REFERENCE_ROWS:
        c = _home_cluster(row)
        if c is None or c.id in {"RC-WC", "RC-REC", "RC-CAP", "RC-FUND", "RC-EST", "RC-DEP"}:
            continue
        check(c.origin == "PROPOSED",
              f"{c.id} backs reference row {row['rank']} and is outside Appendix D, so it "
              f"must be marked PROPOSED and listed by `--gaps`")


def _reachability() -> list[tuple[int, str, list[str], list[str]]]:
    out = []
    for row in REFERENCE_ROWS:
        c = _home_cluster(row)
        if c is None:
            continue
        t = _home_theme(row)
        sigs = list(t.signals) if t else list(c.signals)
        have = [s for s in sigs if s in R.IMPLEMENTED]
        want = [s for s in sigs if s not in R.IMPLEMENTED]
        out.append((row["rank"], row["theme"], have, want))
    return out


def main() -> int:
    _fails.clear()
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()

    reach = _reachability()
    live = [r for r in reach if r[2]]
    print(f"reference matrix: {len(REFERENCE_ROWS)} rows, "
          f"{len(live)} with at least one implemented rule")
    for rank, theme, have, want in reach:
        state = "REACHABLE" if have else "BLOCKED  "
        print(f"  {state} row {rank}: {theme}")
        print(f"      rules implemented: {have or '-'}; awaiting: {want or '-'}")

    if KNOWN_ASSERTION_DIVERGENCES:
        print(f"  {len(KNOWN_ASSERTION_DIVERGENCES)} assertion divergence(s) awaiting an "
              f"audit decision: "
              + ", ".join(f"{c}/{a}" for c, a in sorted(KNOWN_ASSERTION_DIVERGENCES)))

    if _fails:
        print(f"FAIL - {len(_fails)} problem(s) in {len(tests)} checks:")
        for f in _fails:
            print(f"  - {f}")
        return 1
    print(f"OK - {len(tests)} structural checks passed over "
          f"{len(REFERENCE_ROWS)} reference rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
