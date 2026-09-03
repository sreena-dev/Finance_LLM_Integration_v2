"""
Hermetic regression over the derivation registry and its delivery. No DB, no model.

    python -m fdr.test_derivations

Two properties are under test, and they are different questions:

  DECLARED   every signal states how it is derived and which annual-report statement,
             note or schedule its inputs come from, and that statement agrees with the
             signal registry it is a contract against.

  RETURNED   that declaration actually reaches the reader — on the SignalResult whatever
             its status, in the §14.3 JSON payload, in the rendered report, in the
             diagnostics-not-run list, and in the exported matrix.

The second is the one that rots silently. A derivation registry nobody's output reads is
documentation with extra steps, so most of the checks below are on the delivery path.
"""
from __future__ import annotations

from . import clusters as CL
from . import derivations as DV
from . import rules as R
from . import signals as SG
from .assemble import build_report
from .model import (
    BusinessProfile, ABSTAIN, SUPPRESSED, NOT_APPLICABLE, FIRED,
)

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        _fails.append(msg)


# ---- declared ---------------------------------------------------------------------

def test_every_signal_has_a_derivation() -> None:
    for s in SG.SIGNALS:
        d = DV.for_signal(s.id)
        check(d is not None, f"{s.id} has no derivation")
        if d is not None:
            check(bool(d.formula.strip()), f"{s.id} derivation states no formula")
            check(bool(d.sources), f"{s.id} derivation names no annual-report source")


def test_windows_agree_with_the_registry() -> None:
    """§9.5 is one guarantee and must be stated once, not twice differently."""
    for s in SG.SIGNALS:
        d = DV.for_signal(s.id)
        if d is not None:
            check(d.window_years == s.window,
                  f"{s.id}: derivation window {d.window_years}y vs registry {s.window}y")


def test_trend_derivations_declare_the_prior_reports_they_need() -> None:
    """An annual report carries two years. A three-year window needs one prior report."""
    for d in DV.SIGNAL_DERIVATIONS:
        if d.window_years >= 3:
            check(d.needs_prior_reports >= 1,
                  f"{d.id} is a trend derivation but claims to need no prior annual report")
        if d.window_years <= 2:
            check(d.needs_prior_reports == 0,
                  f"{d.id} is not a trend but claims to need a prior annual report")


def test_every_proxy_states_its_confidence_effect() -> None:
    """A stand-in that costs nothing is a stand-in nobody was told about."""
    for d in DV.DERIVATIONS:
        for p in d.proxies:
            check(bool(p.instead_of.strip()) and bool(p.use.strip()),
                  f"{d.id}: a proxy names no substitution")
            check(bool(p.why.strip()), f"{d.id}: a proxy gives no reason")
            check(bool(p.confidence_effect.strip()),
                  f"{d.id}: a proxy states no effect on confidence")


def test_note_derivations_name_what_an_extractor_must_supply() -> None:
    """'Deferred to note extraction' is only actionable if the note figures are named."""
    for s in SG.SIGNALS:
        if s.availability != SG.NOTE:
            continue
        d = DV.for_signal(s.id)
        check(d is not None and bool(d.note_inputs),
              f"{s.id} is note-derived but names no note-level input")


def test_supporting_derivations_attach_to_real_clusters() -> None:
    for d in DV.SUPPORTING_DERIVATIONS:
        check(d.cluster in CL.BY_ID, f"{d.id} names unknown cluster {d.cluster!r}")
        check(d.id not in SG.BY_ID,
              f"{d.id} shares an id with a signal — a supporting read is NOT an Appendix D "
              f"signal and must not be mistakable for one")


def test_supporting_derivations_are_outside_the_signal_registry() -> None:
    """Adding to the closed Appendix D set is an audit decision, not an engineering one.

    The registry may hold more than Appendix D's twenty-two — the S23-S27 extension block
    (ROADMAP 16, decision 3). What must remain true is the thing the count was standing in
    for: a SUPPORTING derivation must never have been quietly promoted into the signal set.
    A supporting read corroborates a cluster and is not a diagnostic in its own right, so
    promoting one changes what the system flags for every entity while looking like a
    refactor. That is checked directly here rather than inferred from a total.
    """
    for d in DV.SUPPORTING_DERIVATIONS + DV.CROSS_CUTTING:
        check(d.id not in SG.BY_ID,
              f"{d.id} is a supporting or cross-cutting read and must not appear in the "
              f"signal registry — promoting it is an audit decision, not an engineering one")

    appendix_d = {f"S{i:02d}" for i in range(1, 23)}
    for s in SG.SIGNALS:
        if s.id in appendix_d:
            check("EXTENSION" not in s.spec_basis,
                  f"{s.id} is an Appendix D signal and must not be marked EXTENSION")
        else:
            check("EXTENSION" in s.spec_basis,
                  f"{s.id} is outside Appendix D and must declare itself an extension in "
                  f"spec_basis, so `--gaps` can list it as awaiting sign-off")


def test_mandatory_schedules_are_enumerable() -> None:
    """Their ABSENCE from a filing is a reportable fact, so they cannot live in prose."""
    sched = DV.mandatory_schedules()
    check(bool(sched), "no Schedule III mandatory schedule is recorded by any derivation")
    ids = {d for d, _ in sched}
    for expect in ("S09", "D-ECL", "X-SCH3RATIO"):
        check(expect in ids, f"{expect} reads no mandatory schedule, but its derivation does")


def test_overlays_modify_rather_than_suppress() -> None:
    for ov in DV.OVERLAYS:
        check(bool(ov.affects), f"{ov.id} modifies nothing")
        check(all(a in DV.BY_ID for a in ov.affects),
              f"{ov.id} affects an unknown derivation")
        check(ov.business_models <= SG.BUSINESS_MODELS,
              f"{ov.id} names an unknown business model")


def test_every_face_signal_has_a_rule() -> None:
    """A face-derivable signal with a stated derivation and no rule is unfinished work."""
    face = {s.id for s in SG.SIGNALS if s.availability == SG.FACE}
    check(face <= R.IMPLEMENTED,
          f"face signals with a derivation but no rule: {sorted(face - R.IMPLEMENTED)}")


def test_no_rule_exists_for_a_note_signal() -> None:
    """A rule over figures the fact layer cannot supply would abstain forever, or guess."""
    note = {s.id for s in SG.SIGNALS if s.availability == SG.NOTE}
    check(not (note & R.IMPLEMENTED),
          f"rules exist for note-derived signals: {sorted(note & R.IMPLEMENTED)}")


# ---- returned ---------------------------------------------------------------------

def _report(**kw):
    return build_report("Test Entity", ("FY2022-23", "FY2023-24", "FY2024-25"),
                        comparable_years=3, **kw)


def test_derivation_travels_on_every_signal_status() -> None:
    """An abstain without the method is a shrug; §4.4 requires a work instruction."""
    rep = _report(business_profile=BusinessProfile(model="power_utilities",
                                                   model_confidence="LOW"),
                  framework="sch3_div3", assume_fired=frozenset({"S02"}))
    seen: dict[str, int] = {}
    for c in rep.clusters:
        for r in c.contributing_signals:
            seen[r.status] = seen.get(r.status, 0) + 1
            check(bool(r.formula),
                  f"{r.signal_id} ({r.status}) carries no formula")
            check(bool(r.ar_source),
                  f"{r.signal_id} ({r.status}) carries no annual-report source")
    for status in (ABSTAIN, SUPPRESSED, NOT_APPLICABLE, FIRED):
        check(seen.get(status, 0) > 0,
              f"the fixture produced no {status} result, so that path is untested")


def test_diagnostics_not_run_carry_the_method_and_the_source() -> None:
    rep = _report()
    check(bool(rep.diagnostics_not_run), "nothing abstained, so the list is untested")
    for d in rep.diagnostics_not_run:
        check(bool(d.formula), f"{d.diagnostic} says what it is but not how it is derived")
        check(bool(d.ar_source), f"{d.diagnostic} names no source to read the inputs from")
        check(bool(d.reason), f"{d.diagnostic} gives no reason (§4.4)")


def test_json_payload_carries_the_derivation() -> None:
    """§14.3 — the machine-readable half must not be poorer than the narrative half."""
    rep = _report()
    payload = rep.to_dict()
    sigs = [s for c in payload["risk_clusters"] for s in c["contributing_signals"]]
    check(bool(sigs), "payload carries no signals")
    for s in sigs:
        for field in ("formula", "ar_source", "proxies_used", "reconciling_items"):
            check(field in s, f"{s['signal_id']} payload is missing {field!r}")
        check(isinstance(s["ar_source"], list),
              f"{s['signal_id']}: ar_source must survive a JSON round trip as a list")
    not_run = payload["diagnostics_not_run"]
    check(all("formula" in d and "ar_source" in d for d in not_run),
          "diagnostics_not_run in the payload lost the derivation")


def test_rendered_report_shows_the_derivation_and_the_source() -> None:
    from .render import render
    from .safe_language import assert_safe

    rep = _report(assume_fired=frozenset({"S02", "S15"}))
    text = render(rep)
    assert_safe(text)                       # §17.2 is a hard gate, including on new prose
    for token in ("derivation", "read from", "would be derived as:",
                  "Schedule III mandatory schedule"):
        check(token in text, f"the rendered report never says {token!r}")


def test_exported_matrix_carries_the_method() -> None:
    from . import export

    cols = {k for k, _ in export.COLUMNS}
    for expect in ("derivations", "ar_sources", "proxies_applied", "observations"):
        check(expect in cols, f"the matrix has no {expect!r} column")

    rep = _report(assume_fired=frozenset({"S02"}))
    csv = export.to_csv(rep)
    check("How each signal was derived" in csv, "the CSV header lost the derivation column")
    md = export.to_markdown(rep)
    check("Annual-report source" in md, "the markdown export lost the source column")


def test_manifest_is_versioned() -> None:
    """§18.3 — a change to a derivation changes what the system reports, so runs stamp it."""
    m = DV.manifest()
    check(bool(m.get("version")), "the derivation registry is unversioned")
    check(m["signal_derivations"] == len(SG.SIGNALS),
          "the manifest count disagrees with the registry")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    if _fails:
        print(f"FAIL - {len(_fails)} problem(s) in {len(tests)} checks:")
        for f in _fails:
            print(f"  - {f}")
        return 1
    print(f"OK - {len(tests)} checks passed over {len(DV.DERIVATIONS)} derivations "
          f"({len(DV.SIGNAL_DERIVATIONS)} signal, {len(DV.SUPPORTING_DERIVATIONS)} "
          f"supporting, {len(DV.CROSS_CUTTING)} cross-cutting)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
