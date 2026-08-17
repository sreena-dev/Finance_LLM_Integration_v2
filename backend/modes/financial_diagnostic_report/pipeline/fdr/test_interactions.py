"""
Hermetic regression over the §10.2 risk-interaction engine.

    python -m fdr.test_interactions

What these pin down is the difference between a DECLARED interaction and a LIVE one. That
distinction is the whole reason this module exists, and it fails silently: an interaction
printed against a theme nobody raised looks exactly like a real one, reads as corroboration
in a planning meeting, and inflates a rank using evidence that was never found.

The properties, in the order they can go wrong:

  1. an interaction is claimed only where BOTH themes are raised;
  2. an unchecked counterpart produces LATENT — never LIVE (which would borrow weight from
     a diagnostic nobody ran) and never DORMANT (which would report an unchecked theme as
     an absent one, the §4.4 error);
  3. a reinforcing interaction actually MOVES the rank, because §10.2 says it must;
  4. an offsetting interaction moderates and never clears;
  5. a complex is only claimed over live reinforcing edges.
"""
from __future__ import annotations

from . import clusters as CL
from . import interactions as IX
from . import priority as PR
from .assemble import build_report
from .model import (
    BusinessProfile, ClusterPackage, RAISED, NOT_RAISED, CLUSTER_ABSTAIN,
    CLUSTER_NOT_APPLICABLE,
)

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        _fails.append(msg)


# A business model with no §15.2 suppressions in play, so the assumed signals reach their
# clusters and the test is about interactions rather than about suppression.
_MODEL = BusinessProfile(model="manufacturing", model_confidence="HIGH")


def _report(assume: set[str]):
    return build_report("Interaction Test", ("FY2024-25",), comparable_years=1,
                        business_profile=_MODEL, assume_fired=frozenset(assume))


def _pkg(cluster_id: str, status: str) -> ClusterPackage:
    """A minimal package. Resolution reads status and theme and nothing else."""
    return ClusterPackage(
        cluster_id=cluster_id, theme=CL.BY_ID[cluster_id].theme, status=status,
        reason="" if status == RAISED else "constructed for test",
    )


def _edge(imap: IX.InteractionMap, subject: str, counterpart: str):
    return next((e for e in imap.for_cluster(subject) if e.counterpart == counterpart), None)


# ---- resolution ------------------------------------------------------------------

def test_declared_matrix_is_reachable_from_both_ends() -> None:
    for i in CL.INTERACTIONS:
        check(i.involves(i.a) and i.involves(i.b),
              f"{i.a}/{i.b}: declared edge does not involve its own endpoints")


def test_both_raised_is_live() -> None:
    imap = IX.resolve([_pkg("RC-WC", RAISED), _pkg("RC-FUND", RAISED)])
    e = _edge(imap, "RC-WC", "RC-FUND")
    check(e is not None and e.state == IX.LIVE,
          "two raised themes with a declared edge did not resolve to LIVE")
    check(e is not None and e.live_reinforcing,
          "RC-WC/RC-FUND is declared reinforcing and did not resolve as such")


def test_unraised_subject_carries_no_interaction() -> None:
    """The regression this module was written for.

    Before resolution existed, every cluster carried "reinforces RC-FUND" whatever its own
    status — including clusters that were never raised, against counterparts that were
    never raised either.
    """
    for status in (NOT_RAISED, CLUSTER_ABSTAIN, CLUSTER_NOT_APPLICABLE):
        imap = IX.resolve([_pkg("RC-WC", status), _pkg("RC-FUND", RAISED)])
        e = _edge(imap, "RC-WC", "RC-FUND")
        check(e is not None and e.state == IX.DORMANT,
              f"a theme with status {status} claimed an interaction bearing on it")
        check(imap.statements_for("RC-WC") == (),
              f"a {status} theme carried interaction statements on its card")


def test_unchecked_counterpart_is_latent_not_live_and_not_dormant() -> None:
    imap = IX.resolve([_pkg("RC-WC", RAISED), _pkg("RC-FUND", CLUSTER_ABSTAIN)])
    e = _edge(imap, "RC-WC", "RC-FUND")
    check(e is not None and e.state == IX.LATENT,
          "a raised theme against an UNCHECKED counterpart must be LATENT")
    check(e is not None and not e.live_reinforcing,
          "a latent edge must not count as live reinforcement")
    check(e is not None and "NOT CHECKED" in e.statement,
          "the latent statement does not say the counterpart was not checked")


def test_checked_and_clear_counterpart_is_dormant() -> None:
    imap = IX.resolve([_pkg("RC-WC", RAISED), _pkg("RC-FUND", NOT_RAISED)])
    e = _edge(imap, "RC-WC", "RC-FUND")
    check(e is not None and e.state == IX.DORMANT,
          "a counterpart that was checked and clear must be DORMANT, not LATENT")


def test_dormant_edges_stay_off_the_card_but_stay_in_the_map() -> None:
    imap = IX.resolve([_pkg("RC-WC", RAISED), _pkg("RC-FUND", NOT_RAISED)])
    check(all("DORMANT" not in s for s in imap.statements_for("RC-WC")),
          "a genuinely absent interaction was rendered on the cluster's own package")
    check(any(e.state == IX.DORMANT for e in imap.edges),
          "dormant edges must remain in the report-level map, not be dropped")


# ---- the §10.2 consequence on ranking ---------------------------------------------

def test_reinforcement_moves_the_rank() -> None:
    """§10.2 — "a reinforcing interaction RAISES the cluster's planning priority".

    If this passes trivially the dimension is inert, so the check is that the SAME cluster
    on the SAME assumed signal scores higher when its counterpart is also raised.
    """
    alone = _report({"S02"})
    together = _report({"S02", "S13"})

    a = next((p for p in alone.priorities if p.cluster_id == "RC-WC"), None)
    b = next((p for p in together.priorities if p.cluster_id == "RC-WC"), None)
    check(a is not None and b is not None, "RC-WC was not ranked in both runs")
    if a and b:
        da = next(d for d in a.dimensions if d.name == PR.INTERACTION_PRESSURE)
        db = next(d for d in b.dimensions if d.name == PR.INTERACTION_PRESSURE)
        check((db.score or 0) > (da.score or 0),
              "a live reinforcing interaction did not raise interaction pressure")
        check((b.score or 0) > (a.score or 0),
              "§10.2 requires a reinforcing interaction to raise the planning priority; "
              "the cluster score did not move")


def test_offset_moderates_but_never_clears() -> None:
    rep = _report({"S02", "S20"})            # RC-WC raised, RC-DEP raised (offsetting)
    p = next((x for x in rep.priorities if x.cluster_id == "RC-WC"), None)
    check(p is not None, "RC-WC was not ranked")
    if p:
        d = next(x for x in p.dimensions if x.name == PR.INTERACTION_PRESSURE)
        check(d.score is not None and d.score >= 1,
              "an offsetting interaction scored a raised theme to nothing; §10.2 moderates, "
              "it does not clear")
        check("offset" in d.basis.lower(),
              "the offset was applied without being stated in the dimension basis")


def test_latent_adds_no_weight() -> None:
    """A latent edge is a coverage fact. Scoring it would let an unrun diagnostic rank."""
    rep = _report({"S02"})                    # every counterpart of RC-WC unchecked
    p = next((x for x in rep.priorities if x.cluster_id == "RC-WC"), None)
    check(p is not None, "RC-WC was not ranked")
    if p:
        d = next(x for x in p.dimensions if x.name == PR.INTERACTION_PRESSURE)
        check(d.score == 1,
              f"latent interactions moved interaction pressure to {d.score}; they must not")


def test_dimension_is_unassessed_when_the_matrix_was_not_resolved() -> None:
    """P4 — never fabricate a zero. An unresolved matrix is unknown, not absent."""
    d = PR._interaction_dimension("RC-WC", None)
    check(d.score is None,
          "an unresolved interaction matrix was scored rather than marked not assessed")


# ---- complexes --------------------------------------------------------------------

def test_complex_needs_live_reinforcing_edges() -> None:
    imap = IX.with_ranks(
        IX.resolve([_pkg("RC-WC", RAISED), _pkg("RC-FUND", CLUSTER_ABSTAIN)]), {"RC-WC": 1})
    check(imap.complexes == (),
          "a complex was claimed over a latent edge — an unchecked theme cannot be a member")


def test_complex_over_the_spec_triangle() -> None:
    """§10.2's own worked example: weak cash flow + rising leverage + receivables build-up."""
    rep = _report({"S02", "S13", "S05"})
    ids = {c.cluster_id for c in rep.raised()}
    check({"RC-WC", "RC-FUND", "RC-REC"} <= ids,
          f"the three spec themes did not all raise; got {sorted(ids)}")
    comps = rep.interactions.complexes
    check(len(comps) == 1, f"expected one complex, got {len(comps)}")
    if comps:
        cx = comps[0]
        check(set(cx.members) == {"RC-WC", "RC-FUND", "RC-REC"},
              f"complex members are {cx.members}")
        check(cx.dominant == cx.members[0],
              "the dominant member must be the highest-ranked one")
        check(rep.interactions.complex_for("RC-FUND") is not None,
              "a member cannot look up its own complex")
        check(len(cx.mechanisms) >= 2,
              "a complex spanning two declared edges must state both shared drivers")


def test_offsetting_edges_do_not_form_complexes() -> None:
    rep = _report({"S02", "S20"})            # RC-WC + RC-DEP, offsetting
    check(rep.interactions.complexes == (),
          "an offsetting pair was reported as a reinforcing complex")


# ---- the block-6 note -------------------------------------------------------------

def test_note_is_present_and_states_the_resolution_rule() -> None:
    for assume in ({"S02"}, {"S02", "S13"}, {"S02", "S13", "S05"}):
        note = _report(assume).interactions.note
        check(len(note) > 200, f"the block-6 interaction note is trivial for {assume}")
        check("both" in note.lower(),
              "the note does not state that an interaction needs BOTH themes raised")


def test_note_claims_nothing_when_nothing_is_raised() -> None:
    rep = build_report("Nothing Raised", ("FY2024-25",), business_profile=_MODEL)
    note = rep.interactions.note
    check("None is live" in note,
          "with no theme raised the note must say no interaction is live")
    check(all(e.state == IX.DORMANT for e in rep.interactions.edges),
          "an edge resolved live or latent with no raised cluster")


def test_every_package_carries_only_resolved_statements() -> None:
    rep = _report({"S02", "S13"})
    for c in rep.clusters:
        if c.status != RAISED:
            check(c.interactions == (),
                  f"{c.cluster_id} is {c.status} and still carries interaction statements")
        for s in c.interactions:
            check(s.startswith(("LIVE", "LATENT")),
                  f"{c.cluster_id} carries an unresolved interaction statement: {s[:60]}")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    if _fails:
        print(f"FAIL - {len(_fails)} problem(s) in {len(tests)} checks:")
        for f in _fails:
            print(f"  - {f}")
        return 1
    print(f"OK - {len(tests)} checks passed over {len(CL.INTERACTIONS)} declared "
          f"interactions across {len(CL.CLUSTERS)} clusters")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
