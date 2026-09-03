"""
The specification's own test suite — §18.1 evaluation cases and §18.3 acceptance criteria.

    python -m fdr.test_spec_cases

WHY THIS IS A SEPARATE SUITE
----------------------------
`test_registry`, `test_skeleton` and `test_planning` test what the code does. This suite
tests what the SPECIFICATION says the model must do, case by case, with each test named
for the case it implements. When the spec is revised, this is the file that has to change,
and the diff is legible to the audit side rather than only to an engineer.

BLOCKED CASES ARE REPORTED, NOT OMITTED
---------------------------------------
Two of the eight §18.1 cases cannot run until later milestones land. They are declared in
`BLOCKED` and printed on every run with what they are waiting for. A suite that silently
drops the cases it cannot run reports a clean pass over a partial test — which is the
exact failure mode this whole system is built to avoid.
"""
from __future__ import annotations
import re

from . import clusters as CL
from . import planning as PL
from . import priority as PR
from . import safe_language
from . import signals as SG
from .assemble import build_report
from .model import BusinessProfile, FIRED, SUPPRESSED, RAISED, HIGH
from .render import render

_fails: list[str] = []

# §18.1 cases that cannot be exercised yet, and what they wait for.
BLOCKED: tuple[tuple[str, str, str], ...] = (
    ("§18.1 Contradiction",
     "FDR signal contradicted by trial-balance or auditor-report analysis; the model should "
     "retain and investigate, not average.",
     "ROADMAP M12 — the integration framework consumes no corroboration input yet, so no "
     "contradiction can arise to be retained."),
)


def check(cond: bool, msg: str) -> None:
    if not cond:
        _fails.append(msg)


def _utility(**kw):
    return BusinessProfile(model="power_utilities", model_confidence="LOW",
                           revenue_model="tariff-based", **kw)


def _run(assume: str = "", profile: BusinessProfile | None = None, **kw):
    return build_report(
        "Case Entity", ("FY2022-23", "FY2023-24", "FY2024-25"), comparable_years=3,
        assume_fired=frozenset(a for a in assume.split(",") if a),
        business_profile=profile, **kw,
    )


def _panel(series: dict[str, list[float]]):
    """A three-year panel of CONFIRMED figures — the spec cases that need real arithmetic."""
    from .panel import Panel, PanelCell

    years = ("FY2022-23", "FY2023-24", "FY2024-25")
    cells = {}
    for key, values in series.items():
        for i, v in enumerate(values):
            y = years[i]
            cells[(key, y)] = PanelCell(
                value=float(v), fy_label=y, period_end=f"{y[2:6]}-03-31",
                unit_confidence="HIGH", verify_verdict="CONFIRMED", unit_scale="lakh",
                source_label=key, doc_id="CASE")
    return Panel("Case Entity", "standalone", years, cells)


def test_case_decomposition_reclassifies_a_leverage_driven_roe_gain() -> None:
    """§18.1 / §8.2 — 'always decompose before flagging'.

    An ROE improvement carried entirely by the equity multiplier is not a performance
    result. The decomposition is what moves it from a performance story to a solvency one,
    and the observable consequence is WHICH CLUSTER it joins: S13 belongs to RC-FUND, so a
    leverage-driven gain raises funding and solvency and a margin-driven gain does not
    raise anything.
    """
    # Operating factors flat, equity halved: ROE 20% -> 40% on leverage alone.
    lev = _run(panel=_panel({
        "pat": [100.0, 100.0, 100.0], "revenue": [1000.0, 1000.0, 1000.0],
        "total_assets": [1000.0, 1000.0, 1000.0], "total_equity": [500.0, 350.0, 250.0]}))
    s13 = _signal(lev, "S13")
    check(s13 is not None and s13.status == FIRED,
          "a leverage-driven ROE gain did not raise S13")
    if s13 is not None and s13.status == FIRED:
        check("attributable to leverage" in s13.observation,
              "S13 fired without attributing the movement to leverage rather than to "
              "operating performance — the decomposition is the whole point")
        fund = next(c for c in lev.clusters if c.cluster_id == "RC-FUND")
        check(fund.status == RAISED,
              "a leverage-driven ROE gain did not reach the funding and solvency cluster")

    # Same ROE improvement, carried by margin. Not a solvency story, and must not be one.
    marg = _run(panel=_panel({
        "pat": [100.0, 150.0, 200.0], "revenue": [1000.0, 1000.0, 1000.0],
        "total_assets": [1000.0, 1000.0, 1000.0], "total_equity": [500.0, 500.0, 500.0]}))
    s13m = _signal(marg, "S13")
    check(s13m is not None and s13m.status != FIRED,
          "a margin-driven ROE improvement was reported as a leverage signal")


def _signal(rep, signal_id: str):
    for c in rep.clusters:
        for r in c.contributing_signals:
            if r.signal_id == signal_id:
                return r
    return None


# =================================================================================
# §18.1 — evaluation test cases
# =================================================================================

def test_case_1_business_model_first() -> None:
    """'The model forms a business understanding before interpreting ratios; penalise
    ratio-first outputs.' (§18.1, enforcing §2.1.)"""
    # No business understanding -> interpretation must be withheld, visibly.
    rep = _run("S02,S05")
    raised = rep.raised()
    check(bool(raised), "case needs at least one raised cluster")
    for c in raised:
        check(bool(c.interpretation_withheld),
              f"{c.cluster_id}: interpreted without a business understanding (§2.1)")
    text = render(rep)
    check("INTERPRETATION WITHHELD" in text,
          "a ratio-first output was produced with no visible §2.1 caveat")
    check("§2.1 PRECONDITION UNMET" in text,
          "the dashboard presents a triage order with no business understanding behind it")

    # With a business understanding -> the withholding lifts.
    rep2 = _run("S02", profile=BusinessProfile(model="manufacturing", model_confidence="LOW"))
    for c in rep2.raised():
        check(not c.interpretation_withheld,
              f"{c.cluster_id}: still withheld after a business profile was formed")


def test_case_2_adverse_ratio_explained_by_the_model() -> None:
    """'A ratio looks adverse but the business model explains it; the model should not flag
    it as a risk.' (§18.1; the §20 power-utility worked example.)"""
    rep = _run("S05", profile=_utility())
    s05 = [s for c in rep.clusters for s in c.contributing_signals if s.signal_id == "S05"][0]
    check(s05.status == SUPPRESSED,
          f"S05 fired for a tariff-based utility (status {s05.status}) — §20 says it must not")
    check("normal feature" in s05.reason, "the suppression gives no business-model reason")
    rec = CL.BY_ID["RC-REC"]
    pkg = [c for c in rep.clusters if c.cluster_id == "RC-REC"][0]
    check(pkg.status != RAISED,
          "the receivable cluster was raised on a signal the business model explains")
    check("S05" in pkg.reason, "the suppressed signal is not accounted for in the cluster reason")


def test_case_3_weak_signals_combine() -> None:
    """'Individually weak signals that form a strong cluster; the model should raise the
    cluster, not the isolated signals.' (§18.1, §10.1 — the §20 infrastructure example.)"""
    # S09, S10, S15: individually mild, and the spec's own example of a combining set.
    rep = _run("S09,S10,S15", profile=BusinessProfile(model="infrastructure_epc",
                                                      model_confidence="LOW"))
    raised = rep.raised()
    check(len(raised) <= 2,
          f"{len(raised)} separate items raised from 3 combining signals — §10.1 requires "
          f"clustering into coherent themes, not a flag list")
    cap = [c for c in raised if c.cluster_id == "RC-CAP"]
    check(bool(cap), "the asset/capitalisation theme was not raised from its own signals")
    if cap:
        fired = [s for s in cap[0].contributing_signals if s.status == FIRED]
        check(len(fired) >= 2,
              "the cluster was raised on a single signal — the combining case is untested")
        p = [x for x in rep.priorities if x.cluster_id == "RC-CAP"][0]
        div = [d for d in p.dimensions if d.name == PR.TECHNIQUE_DIVERSITY][0]
        check(div.assessed and (div.score or 0) >= 2,
              "technique diversity does not reflect signals from independent layers (§11.1)")


def test_case_4_decomposition_routes_to_solvency() -> None:
    """'A leverage-driven return-on-equity gain; the model should classify it as a solvency,
    not a performance, story.' (§18.1, §8.2.)

    The structural half is testable now: the signal must live in the funding/solvency
    cluster, carry borrowings assertions, and propose a borrowings response — never a
    profitability one. The behavioural half is in BLOCKED."""
    check(CL.clusters_for("S13") == ("RC-FUND",),
          f"S13 (leverage-driven ROE) routes to {CL.clusters_for('S13')} — §8.2 requires the "
          f"funding/solvency theme, not a performance one")
    rep = _run("S13", profile=BusinessProfile(model="manufacturing", model_confidence="LOW"))
    pkg = [c for c in rep.raised() if c.cluster_id == "RC-FUND"]
    check(bool(pkg), "a leverage-driven ROE did not raise the funding/solvency theme")
    if pkg:
        a = set(pkg[0].affected_assertions)
        check("completeness" in a and "classification" in a,
              f"assertions {sorted(a)} — §20 expects completeness/classification of borrowings")
        resp = pkg[0].recommended_response["nature"].lower()
        check("borrowing" in resp or "lender" in resp,
              "the proposed response does not address borrowings")
        check("margin" not in resp and "revenue" not in resp,
              "the response treats a solvency story as a performance one")


def test_case_5_confidence_versus_severity() -> None:
    """'A high-severity signal on a low-confidence input; the model should keep confidence low
    and request evidence.' (§18.1, §4.4, App H.)"""
    rep = _run("S02", profile=BusinessProfile(model="manufacturing", model_confidence="LOW"))
    s02 = [s for c in rep.clusters for s in c.contributing_signals if s.signal_id == "S02"][0]
    check(s02.severity == HIGH, "the case needs a high-severity signal")
    check(s02.confidence is None,
          "severity inflated confidence — a signal that was not measured has no confidence")
    pkg = [c for c in rep.raised() if c.cluster_id == "RC-WC"][0]
    check(pkg.diagnostic_confidence is None,
          "the cluster carries a confidence built on an unmeasured signal")
    check(bool(pkg.evidence_request),
          "no evidence was requested for a high-severity, low-confidence item — §4.4 requires "
          "stating what would raise confidence")
    text = render(rep)
    check("evidence to request" in text, "the evidence request does not reach the output")


def test_case_7_no_fabrication() -> None:
    """'No peer data supplied; the model must not invent a benchmark.' (§18.1, §13, §4.2.)"""
    rep = _run("S02,S05,S13,S21")
    text = render(rep)

    # No comparative claim may appear when no peer set was supplied.
    for pat in (r"\bpeer (?:range|set|average|median|group)\b",
                r"\bindustry (?:average|norm|standard|benchmark)\b",
                r"\bsector (?:average|norm|benchmark)\b",
                r"\bcompared (?:to|with) peers\b",
                r"\btypical(?:ly)? for the (?:sector|industry)\b"):
        check(not re.search(pat, text, re.I),
              f"a benchmark claim appears with no peer data supplied: /{pat}/")
    check(not rep.coverage.optional_inputs,
          "the run claims optional inputs that were never supplied")

    # No bare figure may appear as a diagnostic result. Section numbers and dimension
    # scores are the only numerals the skeleton legitimately prints.
    #
    # The §16 reproducibility block is excluded, and only that block. It is the last thing
    # the renderer emits and it carries version identifiers — `fdr-headline-1.0.0` — which
    # are not figures about the entity and cannot be mistaken for one. Scanning it was
    # catching the version of the module that states the figures rather than a figure.
    narrative = text.split("Versions (§16 reproducibility)")[0]
    for m in re.finditer(r"(?<![§\w.])\d[\d,]*\.\d+(?!\s*/\s*3)", narrative):
        frag = narrative[max(0, m.start() - 40):m.end() + 20]
        check("Score" in frag or "score" in frag,
              f"an unexplained figure appears in the narrative: {frag.strip()!r}")


def test_case_8_safe_language() -> None:
    """'A distress or earnings-management pattern; the model must use safe wording and draw no
    conclusion of distress or fraud.' (§18.1, §17.)"""
    for assume in ("S16,S17,S18", "S02,S03,S04", "S20,S21"):
        rep = _run(assume, profile=BusinessProfile(model="manufacturing", model_confidence="LOW"))
        bad = safe_language.lint(render(rep))
        check(not bad, f"safe-language lint failed for {assume}: {bad[:2]}")

    # The estimate cluster is the one §17.1 targets most directly.
    rep = _run("S16", profile=BusinessProfile(model="manufacturing", model_confidence="LOW"))
    pkg = [c for c in rep.raised() if c.cluster_id == "RC-EST"][0]
    blob = " ".join([pkg.planning_significance, pkg.recommended_response["nature"]]).lower()
    check("intent" in blob or "no inference" in blob,
          "the estimate cluster does not state that no inference about intent is drawn (§9.3)")


# =================================================================================
# §18.3 — acceptance criteria before deployment
# =================================================================================

def test_acceptance_traceability() -> None:
    """'Every ranked item traces to source data, diagnostics and reasoning.'"""
    rep = _run("S02,S05", profile=BusinessProfile(model="manufacturing", model_confidence="LOW"))
    for p in rep.priorities:
        pkg = [c for c in rep.clusters if c.cluster_id == p.cluster_id][0]
        fired = [s for s in pkg.contributing_signals if s.status == FIRED]
        check(bool(fired), f"{p.cluster_id}: ranked with no traceable contributing signal")
        for s in fired:
            check(s.signal_id in SG.BY_ID, f"{s.signal_id}: not in the signal registry")
            check(bool(s.observation), f"{s.signal_id}: no observation recorded")


def test_acceptance_explainability() -> None:
    """'Every ranked item carries the full reasoning chain of Section 16.'"""
    rep = _run("S02,S21", profile=BusinessProfile(model="manufacturing", model_confidence="LOW"))
    for p in rep.priorities:
        for required in ("Ranks", "dimension"):
            check(required in p.reasoning,
                  f"{p.cluster_id}: reasoning omits {required!r} (§16)")
        check(any(d.assessed for d in p.dimensions),
              f"{p.cluster_id}: ranked with no assessed dimension at all")
        for d in p.dimensions:
            check(bool(d.basis), f"{p.cluster_id}/{d.name}: no stated basis (§16)")


def test_acceptance_reproducibility() -> None:
    """'The same inputs and thresholds produce the same output.'"""
    a = _run("S02,S05,S21").to_dict()
    b = _run("S02,S05,S21").to_dict()
    check(a == b, "identical inputs produced different payloads")
    check("signal_contract" in a["versions"] and "planning_content" in a["versions"],
          "the payload does not stamp the versions needed to reproduce it")


def test_acceptance_non_duplication() -> None:
    """'The output does not reproduce Financial Statement Analysis compliance or disclosure
    content.' (§18.3, Appendix A.)"""
    text = render(_run("S02,S05,S13,S21"))
    for pat, owner in (
        (r"\bSchedule III (?:presentation|compliance|disclosure)\b", "FSA"),
        (r"\bthe eleven (?:statutory )?ratios\b", "FSA"),
        (r"\bdisclosure (?:is|was) (?:non-)?compliant\b", "FSA"),
        (r"\baccounting[- ]policy review\b", "FSA"),
        (r"\bgoing[- ]concern review\b", "FSA"),
    ):
        check(not re.search(pat, text, re.I),
              f"the FDR reproduces {owner}-owned content: /{pat}/ (Appendix A)")


def test_acceptance_safe_language() -> None:
    """'No prohibited wording; confidence qualitative; no attribution of intent.'"""
    text = render(_run("S02,S05,S13,S16,S21"))
    check(not safe_language.lint(text), "prohibited wording in the released report")
    check(not re.search(r"\bconfidence\s*[:=]\s*\d", text, re.I),
          "confidence is expressed numerically; §17.2 requires it to be qualitative")


def test_acceptance_confidence_stated_and_separated() -> None:
    """'Confidence stated and separated from severity throughout.'"""
    rep = _run("S02,S05", profile=BusinessProfile(model="manufacturing", model_confidence="LOW"))
    for c in rep.raised():
        check("confidence" in render(rep).lower(), "confidence is not stated in the output")
        for s in c.contributing_signals:
            if s.status == FIRED:
                check(not (s.confidence and s.confidence == s.severity and s.confidence_basis == ""),
                      f"{s.signal_id}: confidence mirrors severity with no separate basis")


# =================================================================================

def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()

    print(f"\n{'=' * 74}")
    print("FDR SPECIFICATION CONFORMANCE — §18.1 cases and §18.3 acceptance criteria")
    print("=" * 74)
    if _fails:
        print(f"FAIL - {len(_fails)} problem(s) in {len(tests)} checks:")
        for f in _fails:
            print(f"  - {f}")
    else:
        print(f"PASS - {len(tests)} spec checks")

    print(f"\nNOT YET TESTABLE ({len(BLOCKED)} of the 8 §18.1 cases):")
    for name, what, why in BLOCKED:
        print(f"  {name}")
        print(f"      case:    {what}")
        print(f"      blocked: {why}")
    print(f"\nThese are declared, not omitted. A pass above covers {8 - len(BLOCKED)} of "
          f"the 8 cases.")
    return 1 if _fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
