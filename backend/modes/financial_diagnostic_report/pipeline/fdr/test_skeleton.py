"""
Hermetic regression over the walking skeleton (ROADMAP M3). No DB, no model, no network.

    python -m fdr.test_skeleton

The gate M3 has to pass is not "it produces output" — it is that the output is HONEST when
nothing could be computed. These tests pin the four ways that can silently break:

  1. a cluster going quiet instead of saying it was not checked (§4.4);
  2. an empty result reading as a clean result (§2.3);
  3. a suppressed or inapplicable signal disappearing instead of being stated (§15.2);
  4. a confidence rating attached to a diagnostic that never ran (§4.4).
"""
from __future__ import annotations
import json

from . import clusters as CL
from . import signals as SG
from .assemble import build_report
from .model import (
    BusinessProfile, SignalResult, ABSTAIN, NOT_APPLICABLE, SUPPRESSED, FIRED,
    RAISED, CLUSTER_ABSTAIN, CLUSTER_NOT_APPLICABLE, STANDING_CAVEATS,
)
from .render import render
from . import safe_language

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        _fails.append(msg)


def _bare() -> object:
    return build_report("Test Entity", ("FY2023-24", "FY2024-25"), comparable_years=2)


# ---- structure ------------------------------------------------------------------

def test_every_cluster_produces_a_package() -> None:
    """§4.4 — a cluster that goes quiet is indistinguishable from a clean one."""
    rep = _bare()
    check(len(rep.clusters) == len(CL.CLUSTERS),
          f"{len(rep.clusters)} packages for {len(CL.CLUSTERS)} clusters")
    ids = {c.cluster_id for c in rep.clusters}
    check(ids == set(CL.BY_ID), "a cluster is missing from the report")


def test_nothing_fires_without_a_panel() -> None:
    rep = _bare()
    check(not rep.raised(), "a cluster was raised with no panel and no data")
    check(rep.shortlist == (), "a shortlist was produced with nothing raised")


def test_unchecked_is_not_reported_as_clean() -> None:
    """§2.3 — absence of evidence is not evidence of absence."""
    rep = _bare()
    for c in rep.clusters:
        check(c.status == CLUSTER_ABSTAIN,
              f"{c.cluster_id} status {c.status} — with no panel it must be ABSTAIN")
        check("NOT checked" in c.reason or "not cleared" in c.reason,
              f"{c.cluster_id} reason does not say the theme was unchecked")


def test_diagnostics_not_run_covers_every_abstain() -> None:
    rep = _bare()
    abstained = {s.signal_id for c in rep.clusters
                 for s in c.contributing_signals if s.status == ABSTAIN}
    listed = {d.diagnostic.split(" ")[0] for d in rep.diagnostics_not_run}
    check(abstained == listed,
          f"diagnostics-not-run misses {sorted(abstained - listed)} / "
          f"invents {sorted(listed - abstained)}")


def test_not_run_entries_state_their_impact() -> None:
    """§4.4 — 'states which diagnostic is weakened or unavailable as a result'."""
    rep = _bare()
    for d in rep.diagnostics_not_run:
        check(bool(d.reason.strip()), f"{d.diagnostic}: no reason")
        check(bool(d.impact.strip()), f"{d.diagnostic}: no stated impact")


def test_validation_note_is_present() -> None:
    """§4.4 — mandatory whatever the grade."""
    rep = _bare()
    check(len(rep.coverage.validation_note) > 200, "validation note is missing or trivial")
    check("business understanding" in rep.coverage.validation_note.lower(),
          "validation note does not address business understanding (§2.1)")


def test_grade_is_unassessed_not_defaulted() -> None:
    """A defaulted grade would cap nothing while looking as though it had."""
    rep = _bare()
    check(rep.coverage.input_quality_grade is None, "an input quality grade was invented")
    check(bool(rep.coverage.grade_basis), "no basis stated for the missing grade")


def test_caveats_always_present() -> None:
    rep = _bare()
    check(rep.caveats == STANDING_CAVEATS, "§17.3 standing caveats were altered or dropped")


# ---- suppression and applicability -----------------------------------------------

def test_suppression_is_stated_not_silent() -> None:
    """§15.2, and the §20 power-utility worked example."""
    rep = build_report("Utility", ("FY2024-25",),
                       business_profile=BusinessProfile(model="power_utilities",
                                                        model_confidence="LOW"))
    s05 = [s for c in rep.clusters for s in c.contributing_signals if s.signal_id == "S05"]
    check(len(s05) == 1 and s05[0].status == SUPPRESSED,
          "S05 was not suppressed for a power utility")
    check("normal feature" in s05[0].reason, "suppression carries no explanation")
    text = render(rep)
    check("suppressed" in text.lower(), "suppression does not appear in the rendered report")


def test_not_applicable_is_distinct_from_not_found() -> None:
    """P8 / §7.5 — a concept gap declared by the framework is not a failed lookup."""
    rep = build_report("NBFC", ("FY2024-25",), framework="sch3_div3")
    for sid in ("S03", "S14"):
        r = [s for c in rep.clusters for s in c.contributing_signals if s.signal_id == sid]
        check(len(r) == 1 and r[0].status == NOT_APPLICABLE,
              f"{sid} should be NOT_APPLICABLE under Division III, got "
              f"{r[0].status if r else 'missing'}")
    listed = {d.diagnostic.split(" ")[0] for d in rep.diagnostics_not_run}
    check("S03" not in listed,
          "a NOT_APPLICABLE concept was reported as a diagnostic that could not run")


def test_setaside_signals_are_named_in_the_reason() -> None:
    rep = build_report("Utility", ("FY2024-25",),
                       business_profile=BusinessProfile(model="power_utilities",
                                                        model_confidence="LOW"))
    rec = [c for c in rep.clusters if c.cluster_id == "RC-REC"][0]
    check("S05" in rec.reason, "the suppressed signal is not named in the cluster reason")


# ---- the confidence / severity separation ----------------------------------------

def test_confidence_cannot_attach_to_a_diagnostic_that_did_not_run() -> None:
    """§4.4 — reporting a confidence implies an assessment that was never made."""
    try:
        SignalResult(signal_id="S01", status=ABSTAIN, reason="x", confidence="HIGH")
    except ValueError:
        pass
    else:
        _fails.append("SignalResult accepted a confidence on an ABSTAIN")


def test_abstain_requires_a_reason() -> None:
    for status in (ABSTAIN, NOT_APPLICABLE, SUPPRESSED):
        try:
            SignalResult(signal_id="S01", status=status)
        except ValueError:
            continue
        _fails.append(f"SignalResult accepted {status} with no reason")


def test_no_confidence_anywhere_in_the_skeleton() -> None:
    rep = _bare()
    for c in rep.clusters:
        check(c.diagnostic_confidence is None,
              f"{c.cluster_id} carries a confidence with no diagnostic behind it")


# ---- output contracts -------------------------------------------------------------

def test_all_ten_blocks_render() -> None:
    text = render(_bare())
    for n, title in enumerate([
        "COVERAGE AND LIMITATIONS", "EXECUTIVE DASHBOARD", "BUSINESS PROFILE",
        "FINANCIAL HEALTH SUMMARY", "KEY TRENDS AND STRUCTURAL DRIFT", "RISK CLUSTERS",
        "AUDIT-PLANNING MATRIX", "TOP FOCUS AREAS",
        "EVIDENCE MATRIX AND DIAGNOSTICS NOT RUN", "PLANNING SUMMARY",
    ], start=1):
        check(f"{n}. {title}" in text, f"block {n} ({title}) missing from the render")


def test_json_matches_the_spec_schema() -> None:
    """§14.3 — the machine-readable payload."""
    d = _bare().to_dict()
    for k in ("entity", "periods", "input_quality_grade", "optional_inputs",
              "business_profile", "risk_clusters", "diagnostics_not_run", "caveats"):
        check(k in d, f"§14.3 key {k!r} missing")
    check(json.loads(json.dumps(d, ensure_ascii=False)) == d, "payload is not JSON round-trippable")


def test_safe_language_lint_passes_on_our_own_output() -> None:
    """The lint must be clean on text we generate, or it will be switched off."""
    for rep in (_bare(),
                build_report("U", ("FY2024-25",), framework="sch3_div3"),
                build_report("V", ("FY2024-25",),
                             business_profile=BusinessProfile(model="power_utilities",
                                                              model_confidence="LOW"))):
        bad = safe_language.lint(render(rep))
        check(not bad, f"safe-language lint failed on our own render: {bad[:3]}")


def test_safe_language_lint_actually_catches_things() -> None:
    """A lint that never fires is decoration."""
    for phrase in ("This proves the balance is overstated.",
                   "The entity is insolvent.",
                   "Management manipulated the provision.",
                   "Funds have been diverted.",
                   "There is a 70% probability of default."):
        check(bool(safe_language.lint(phrase)), f"lint missed: {phrase!r}")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    if _fails:
        print(f"FAIL - {len(_fails)} problem(s) in {len(tests)} checks:")
        for f in _fails:
            print(f"  - {f}")
        return 1
    print(f"OK - {len(tests)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
