"""
Hermetic regression over the signal contract. No DB, no model, no network.

    python -m fdr.test_registry

These tests encode the properties the contract exists to guarantee. If one fails, the
registry has drifted from Appendix D and the layers built against it are building the
wrong thing.
"""
from __future__ import annotations

from . import assertions as A
from . import clusters as CL
from . import signals as SG

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        _fails.append(msg)


def test_appendix_d_membership() -> None:
    """Appendix D names six clusters and twenty-two contributing signals.

    The registry may now hold MORE than that — the RC-CONT and RC-INV extension clusters
    and their signals (ROADMAP §16 decision 3). So the test no longer counts rows, which
    would only ever say "the number changed"; it asserts the two things that actually
    matter and that a count cannot express:

      1. every Appendix D cluster and signal is still present, under its own id. An
         extension must never be added by REPLACING specification content.
      2. everything beyond Appendix D declares itself as an extension. An addition that
         forgets to say so is indistinguishable, downstream, from something the
         specification asked for — which is exactly the confusion `--gaps` exists to
         prevent.
    """
    appendix_d_clusters = {"RC-WC", "RC-REC", "RC-CAP", "RC-FUND", "RC-EST", "RC-DEP"}
    appendix_d_signals = {f"S{i:02d}" for i in range(1, 23)}

    have_clusters = {c.id for c in CL.CLUSTERS}
    have_signals = {s.id for s in SG.SIGNALS}
    check(appendix_d_clusters <= have_clusters,
          f"Appendix D cluster(s) missing: {sorted(appendix_d_clusters - have_clusters)}")
    check(appendix_d_signals <= have_signals,
          f"Appendix D signal(s) missing: {sorted(appendix_d_signals - have_signals)}")

    for c in CL.CLUSTERS:
        if c.id in appendix_d_clusters:
            check(c.origin == "SPEC", f"{c.id} is Appendix D but is marked {c.origin!r}")
        else:
            check(c.origin == "PROPOSED",
                  f"{c.id} is not in Appendix D and must be marked PROPOSED, not {c.origin!r}")
    for s in SG.SIGNALS:
        if s.id in appendix_d_signals:
            continue
        check("EXTENSION" in s.spec_basis,
              f"{s.id} is not in Appendix D and must say so in spec_basis")


def test_ids_are_well_formed() -> None:
    for s in SG.SIGNALS:
        check(s.id.startswith("S") and s.id[1:].isdigit(), f"malformed signal id {s.id}")
    for c in CL.CLUSTERS:
        check(c.id.startswith("RC-"), f"malformed cluster id {c.id}")


def test_no_orphan_signals() -> None:
    """A diagnostic no cluster consumes is out of scope (ROADMAP §5)."""
    for s in SG.SIGNALS:
        check(bool(CL.clusters_for(s.id)), f"{s.id} is consumed by no cluster")


def test_every_cluster_is_reachable() -> None:
    """A cluster whose signals are all note-derived is dead until note extraction lands.

    Being dead is permitted; being SILENTLY dead is not. A cluster with no face signal must
    carry `note_only_basis` naming the schedules it waits on, so an unreachable theme is a
    recorded decision rather than a matrix row nobody noticed was missing.
    """
    for c in CL.CLUSTERS:
        face = [x for x in c.signals if SG.BY_ID[x].availability == SG.FACE]
        check(bool(face) or bool(c.note_only_basis),
              f"{c.id} has no face-derivable signal and no note_only_basis explaining why")


def test_trend_signals_declare_a_real_window() -> None:
    """§9.5 — a two-point movement is never presented as a trend."""
    for s in SG.SIGNALS:
        if s.is_trend:
            check(s.window >= 3, f"{s.id} claims a trend on a {s.window}-year window")


def test_assertions_are_known() -> None:
    for c in CL.CLUSTERS:
        check(c.assertions <= A.ASSERTIONS, f"{c.id} has an unknown assertion")
        check(bool(c.assertions), f"{c.id} maps to no assertion")
        check(c.regularity_matters <= A.REGULARITY_MATTERS,
              f"{c.id} has an unknown regularity matter")


def test_every_signal_declares_inputs() -> None:
    """The panel (M6) is built to satisfy these. An input-less signal cannot be produced."""
    for s in SG.SIGNALS:
        check(bool(s.inputs), f"{s.id} declares no inputs")
        check(len(set(s.inputs)) == len(s.inputs), f"{s.id} repeats an input")


def test_suppression_targets_are_known_business_models() -> None:
    """§13.1 — suppression is as important as emphasis, and must be explicit."""
    for s in SG.SIGNALS:
        check(s.suppressed_for <= SG.BUSINESS_MODELS, f"{s.id} suppresses for an unknown model")


def test_power_utility_receivables_are_suppressed() -> None:
    """The §20 worked example: high receivables in a tariff-based utility is not a risk."""
    check("power_utilities" in SG.BY_ID["S05"].suppressed_for,
          "S05 must be suppressed for power utilities (§20 worked example)")


def test_division_iii_exclusions() -> None:
    """Schedule III Division III has no current/non-current classification."""
    for sid in ("S03", "S14"):
        check("sch3_div3" in SG.BY_ID[sid].not_applicable_frameworks,
              f"{sid} must be NOT_APPLICABLE under sch3_div3")


def test_interactions_reference_real_clusters() -> None:
    known = set(CL.BY_ID)
    for i in CL.INTERACTIONS:
        check(i.a in known and i.b in known,
              f"interaction references unknown cluster {i.a}/{i.b}")
        check(i.kind in CL.INTERACTION_KINDS, f"interaction {i.a}/{i.b}: bad kind {i.kind}")
        check(bool(i.basis.strip()), f"interaction {i.a}/{i.b} carries no basis")


def test_every_interaction_states_a_mechanism() -> None:
    """§10.2 — an edge without a shared driver is a claim of correlation.

    The mechanism is what makes "reinforcing" a planning instruction rather than an
    adjective: it names the one balance, cash flow or estimate both themes rest on, which
    is what tells a team the two are tested together.
    """
    for i in CL.INTERACTIONS:
        check(len(i.mechanism.strip()) > 40,
              f"interaction {i.a}/{i.b}: mechanism is missing or too thin to plan against")


def test_one_relationship_per_pair() -> None:
    """A pair declared both reinforcing and offsetting would raise and moderate the same
    cluster on the same evidence, and the output could not say which to believe."""
    seen: dict[frozenset[str], str] = {}
    for i in CL.INTERACTIONS:
        check(i.pair not in seen,
              f"{i.a}/{i.b} declared twice ({seen.get(i.pair)} and {i.kind})")
        seen[i.pair] = i.kind


def test_interactions_are_symmetric_lookups() -> None:
    """`interactions_for` must find an edge from BOTH endpoints. An edge visible only from
    the side it happens to be declared on would silently halve the matrix."""
    for i in CL.INTERACTIONS:
        for endpoint in (i.a, i.b):
            found = [x for x in CL.interactions_for(endpoint) if x.pair == i.pair]
            check(len(found) == 1,
                  f"interaction {i.a}/{i.b} not reachable from {endpoint}")
        check(i.other(i.a) == i.b and i.other(i.b) == i.a,
              f"interaction {i.a}/{i.b}: other() is not symmetric")


def test_no_severity_masquerading_as_confidence() -> None:
    """§4.4 — confidence and severity are separate axes. The registry carries severity only."""
    for s in SG.SIGNALS:
        check(s.default_severity in SG.SEVERITIES, f"{s.id} has a bad severity")
        check(not hasattr(s, "confidence"),
              f"{s.id} carries a confidence — confidence is set per run, not per signal")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    if _fails:
        print(f"FAIL — {len(_fails)} problem(s) in {len(tests)} checks:")
        for f in _fails:
            print(f"  - {f}")
        return 1
    print(f"OK — {len(tests)} checks passed over "
          f"{len(SG.SIGNALS)} signals and {len(CL.CLUSTERS)} clusters")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
