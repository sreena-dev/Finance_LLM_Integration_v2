"""
Hermetic regression over the Input Quality Grade. No DB, no model, no network.

    python -m fdr.test_grading

Every test is a constructed coverage dict and an expected letter, because `grade` is a
pure function of one. Three properties matter more than the individual bands:

  WORST WINS      the grade is the worst component, never an average. A test asserts that
                  four A components cannot lift one D, because the moment that stops being
                  true the grade starts hiding exactly what it exists to surface.
  ONLY CAPS       the grade lowers confidence and can never raise it, and never touches
                  whether a signal fired. Severity and confidence stay independent (§4.4).
  DECOMPOSABLE    every letter arrives with the counts that produced it. A bare letter is
                  a §9.4 composite by another name.
"""
from __future__ import annotations

from . import grading as G
from .model import HIGH, MEDIUM, LOW

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        _fails.append(msg)


def cov(**over: object) -> dict:
    """A clean, complete, fully-confirmed dataset. Each test degrades one thing."""
    base = {
        "facts_total": 200, "facts_trusted": 200, "withheld_contradicted": 0,
        "unit_promotions": 0, "withheld_no_scale": 0, "filings_failed": 0,
        "statements": ["balance_sheet", "profit_loss", "cash_flow", "statement_of_equity"],
    }
    base.update(over)
    return base


def g(coverage: dict, years: int = 5, model: str | None = "manufacturing",
      conf: str | None = HIGH) -> G.Grade:
    return G.grade(coverage, comparable_years=years,
                   business_model=model, model_confidence=conf)


# ---- the ladder --------------------------------------------------------------------

def test_a_clean_dataset_grades_a() -> None:
    r = g(cov())
    check(r.letter == G.A, f"a clean complete dataset must grade A, got {r.letter}")
    check(r.confidence_ceiling == HIGH, "A must permit HIGH confidence")


def test_contradicted_figures_cost_the_a() -> None:
    r = g(cov(facts_trusted=190, withheld_contradicted=10))
    check(r.letter == G.B, f"95% confirmed with contradictions must grade B, got {r.letter}")
    check("Data integrity" in r.binding, "data integrity must be named as binding")


def test_integrity_below_three_quarters_grades_d() -> None:
    r = g(cov(facts_trusted=140, withheld_contradicted=60))
    check(r.letter == G.D, f"70% confirmed must grade D, got {r.letter}")
    check(r.confidence_ceiling == LOW, "D must cap confidence at LOW")


def test_a_missing_core_statement_grades_d() -> None:
    r = g(cov(statements=["balance_sheet", "profit_loss"]))
    check(r.letter == G.D, f"a missing Cash Flow Statement must grade D, got {r.letter}")


def test_a_missing_soce_costs_only_the_a() -> None:
    r = g(cov(statements=["balance_sheet", "profit_loss", "cash_flow"]))
    check(r.letter == G.B, f"SOCE absent alone must grade B, got {r.letter}")


def test_below_the_trend_floor_grades_d() -> None:
    r = g(cov(), years=2)
    check(r.letter == G.D, f"two comparable years must grade D, got {r.letter}")


def test_exactly_the_trend_floor_grades_c() -> None:
    r = g(cov(), years=3)
    check(r.letter == G.C, f"exactly three years must grade C, got {r.letter}")
    check(r.confidence_ceiling == MEDIUM, "C must cap confidence at MEDIUM")


def test_a_failed_filing_costs_at_least_a_c() -> None:
    r = g(cov(filings_failed=1))
    check(r.letter == G.C, f"a filing that could not be read must grade C, got {r.letter}")


def test_no_business_model_costs_a_c() -> None:
    r = g(cov(), model=None, conf=None)
    check(r.letter == G.C, f"an unformed business model must grade C, got {r.letter}")


def test_no_coverage_at_all_grades_e() -> None:
    r = G.grade(None)
    check(r.letter == G.E, f"a run with no coverage must grade E, got {r.letter}")
    check(r.confidence_ceiling is None, "E must withdraw reliance, not cap it")


def test_withheld_for_no_scale_costs_a_c() -> None:
    r = g(cov(withheld_no_scale=3))
    check(r.letter == G.C,
          f"figures dropped for want of a scale must grade C, got {r.letter}")


# ---- the properties that matter more than the bands --------------------------------

def test_the_worst_component_wins_and_is_never_averaged() -> None:
    """Four sound dimensions must not outvote one broken one."""
    r = g(cov(facts_trusted=100, withheld_contradicted=100))   # 50% -> D
    check(r.letter == G.D,
          f"one D component must set the grade against four A/B ones, got {r.letter}")
    check(r.binding == ("Data integrity",),
          f"the binding component must be named, got {r.binding}")


def test_every_component_is_reported_whatever_the_letter() -> None:
    r = g(cov())
    check(len(r.components) == 5, f"all five §4.3 components must report, got {len(r.components)}")
    check(all(c.detail for c in r.components), "every component must state its basis")
    check(all(c.figures for c in r.components), "every component must carry its counts")


def test_the_grade_never_raises_confidence() -> None:
    for letter in G.GRADES:
        for measured in (LOW, MEDIUM, HIGH):
            out = G.cap(measured, letter)
            order = {LOW: 0, MEDIUM: 1, HIGH: 2}
            check(out is None or order[out] <= order[measured],
                  f"grade {letter} raised {measured} to {out}")


def test_an_unmeasured_confidence_stays_unmeasured() -> None:
    """An assumed or abstained signal has no confidence; a cap must not invent one."""
    for letter in G.GRADES:
        check(G.cap(None, letter) is None,
              f"grade {letter} manufactured a confidence out of None")


def test_e_withdraws_reliance_rather_than_capping_it() -> None:
    check(G.cap(HIGH, G.E) is None, "E must withdraw confidence, not lower it to LOW")


def test_the_basis_states_that_the_worst_component_bound_it() -> None:
    r = g(cov(filings_failed=1))
    check("WORST" in r.basis, "the basis must say the grade is the worst, not the average")
    check(r.confidence_ceiling in r.basis or "capped" in r.basis.lower(),
          "the basis must state what the grade does to confidence")


def test_every_band_carries_a_basis_and_an_origin() -> None:
    for b in G.BANDS:
        check(bool(b.basis), f"band {b.key} has no basis")
        check(b.origin in (G.SPEC, G.PROPOSED), f"band {b.key} has an unknown origin")


def test_the_manifest_carries_the_version_and_the_bands() -> None:
    m = G.manifest()
    check(m["version"] == G.VERSION, "the manifest must carry the grading version")
    check(set(m["bands"]) == {b.key for b in G.BANDS},
          "the manifest must carry every band, since a change re-grades every entity")


def test_note_level_scope_is_not_graded() -> None:
    """The rule that decides eligibility: what is constant across the corpus is excluded.

    Note extraction is unbuilt for every entity alike. If it reached the grade, all 346
    would carry the same letter and the grade would discriminate nothing.
    """
    import inspect
    src = inspect.getsource(G)
    check("signals" not in src.split("THE RULE THAT DECIDES")[-1].split('"""')[0].lower()
          or "eight signals" in src,
          "the eligibility rule must remain documented in the module docstring")
    r = g(cov())
    check(r.letter == G.A,
          "a dataset complete on the face must reach A despite note extraction being "
          "unbuilt; otherwise the grade is measuring the roadmap, not the entity")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    if _fails:
        print(f"FAILED {len(_fails)} of {len(tests)}")
        for f in _fails:
            print("  -", f)
        return 1
    print(f"ok — {len(tests)} grading tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
