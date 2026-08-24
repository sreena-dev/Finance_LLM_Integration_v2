"""
FDR command line.

    python -m fdr contract              the signal contract Layers 1-4 must satisfy (M1)
    python -m fdr contract --gaps       only the outstanding audit-authoring work
    python -m fdr derivations           how each signal is derived, and from which
                                        annual-report statement, note or schedule
    python -m fdr derivations --signal S15         one derivation in full
    python -m fdr headline              the executive-dashboard tile registry: what each
                                        headline read is, which direction is declared
                                        adverse for it, and which entities it applies to
    python -m fdr headline --model petroleum       the registry as one model would see it
    python -m fdr skeleton "Entity" [FY...]        the walking-skeleton report (M3)
    python -m fdr skeleton "Entity" FY --json      the §14.3 machine-readable payload
    python -m fdr skeleton "Entity" FY --model power_utilities --framework sch3_div3
    python -m fdr skeleton "Entity" FY --assume S02,S05 [--shortlist 3] [--capacity 9]
    python -m fdr skeleton "Entity" FY --assume S02,S05 --matrix csv > matrix.csv
    python -m fdr skeleton "Entity" FY --assume S02,S05 --matrix md
    python -m fdr test                             all nine suites, incl. §18 conformance

Add --trace to any command to export OpenTelemetry spans to Phoenix (project
'fdr-planning'). Off by default; when off no OTel package is imported at all.

`skeleton` reads no database and calls no model. Every diagnostic abstains, and the report
states exactly why — which §4.4 makes a valid FDR, not a placeholder. `--model` and
`--framework` are real: suppression and applicability are decided from the business model
and the reporting framework alone, without any panel, so they produce genuine output today.

`--assume` is REVIEW MODE. It asserts that the named signals are present, so the audit side
can read and correct the planning packages, rankings and matrix rows the system would
produce — before any engine exists to produce them. The output is banner-marked throughout
and says nothing about the entity named on it.
"""
from __future__ import annotations
import json
import sys

from . import clusters as CL
from . import signals as SG
from . import assertions as A
from . import planning as PL
from .assemble import build_report
from .model import BusinessProfile
from .render import render
from .safe_language import assert_safe
from . import tracing as T


def _bar(title: str) -> str:
    return f"\n{title}\n{'-' * len(title)}"


def contract_report(gaps_only: bool = False) -> str:
    out: list[str] = []
    face = [s for s in SG.SIGNALS if s.availability == SG.FACE]
    note = [s for s in SG.SIGNALS if s.availability == SG.NOTE]

    if not gaps_only:
        out.append("FDR SIGNAL CONTRACT — Appendix D")
        out.append(f"{len(SG.SIGNALS)} signals · {len(CL.CLUSTERS)} clusters · "
                   f"{len(face)} face-derivable · {len(note)} note-derivable")

        out.append(_bar("Clusters"))
        for c in CL.CLUSTERS:
            f = [s for s in c.signals if SG.BY_ID[s].availability == SG.FACE]
            out.append(f"{c.id}  {c.theme}")
            out.append(f"    signals      {len(c.signals)} ({len(f)} face, "
                       f"{len(c.signals) - len(f)} note): {', '.join(c.signals)}")
            out.append(f"    assertions   "
                       f"{', '.join(sorted(A.HUMAN_LABEL[x] for x in c.assertions))}")
            if c.regularity_matters:
                out.append(f"    regularity   "
                           f"{', '.join(sorted(A.HUMAN_LABEL[x] for x in c.regularity_matters))}")
            out.append(f"    specialist   {', '.join(c.specialist) or '—'}")
            out.append(f"    evidence     {len(c.evidence)} request(s)"
                       + ("  ** none in Appendix F **" if not c.evidence else ""))

        out.append(_bar("Signals"))
        for s in SG.SIGNALS:
            trend = f"{s.window}y" + (" trend" if s.is_trend else "")
            out.append(f"{s.id}  L{s.layer}  {s.availability:4}  {s.default_severity:6}  "
                       f"{trend:9}  {s.title}")
            out.append(f"      -> {', '.join(CL.clusters_for(s.id))}"
                       + (f"   suppressed for: {', '.join(sorted(s.suppressed_for))}"
                          if s.suppressed_for else "")
                       + (f"   N/A: {', '.join(sorted(s.not_applicable_frameworks))}"
                          if s.not_applicable_frameworks else ""))

        out.append(_bar("Inputs the panel must supply (ROADMAP M6)"))
        keys: dict[str, list[str]] = {}
        for s in SG.SIGNALS:
            for k in s.inputs:
                keys.setdefault(k, []).append(s.id)
        for k in sorted(keys):
            out.append(f"  {k:34} {', '.join(keys[k])}")

    out.append(_bar("Outstanding — audit authoring, not engineering"))
    todo = False
    for s in SG.SIGNALS:
        if s.needs_audit_authoring:
            todo = True
            out.append(f"  {s.id}  alternative explanations unwritten (§2.2) — {s.title}")
    for c in CL.CLUSTERS:
        if not c.evidence:
            todo = True
            out.append(f"  {c.id}  no evidence request — Appendix F has no row (§10.3)")
    for item in PL.proposed_items():
        todo = True
        out.append(f"  {item}")
    from . import derivations as DV
    for d in DV.DERIVATIONS:
        if d.origin == DV.PROPOSED:
            todo = True
            out.append(f"  {d.id}  derivation drafted from audit practice, not stated by the "
                       f"specification — {d.title}")
    if not todo:
        out.append("  none")
    out.append("\n  PROPOSED items are drafted in planning.py and marked as such wherever they "
               "render.\n  They are not presented as though the specification stated them.")

    out.append(_bar("Deferred to note-level extraction"))
    for s in note:
        out.append(f"  {s.id}  {s.title}  ->  {', '.join(CL.clusters_for(s.id))}")
    # EXTENSIONS. Everything outside the closed Appendix D set is listed on its own, because
    # the question an audit reviewer has about it is different: not "when will the data
    # arrive" but "do we want this diagnostic at all". Mixing the two lists would bury a
    # decision that is waiting on a person inside a queue that is waiting on engineering.
    ext_clusters = [c for c in CL.CLUSTERS if c.origin != "SPEC"]
    ext_signals = [s for s in SG.SIGNALS if "EXTENSION" in s.spec_basis]
    if ext_clusters or ext_signals:
        out.append(_bar("Outside Appendix D — awaiting audit sign-off (ROADMAP §16.3)"))
        for c in ext_clusters:
            out.append(f"  {c.id}  {c.theme}")
            out.append(f"        signals: {', '.join(c.signals)}")
            if c.note_only_basis:
                out.append("        no face-derivable signal: cannot fire until the note "
                           "schedules it names are extracted")
        if ext_signals:
            out.append("  signals: " + ", ".join(s.id for s in ext_signals))
        out.append("\n  These are PROPOSED. Declining them removes the cluster and its "
                   "signals and\n  returns the system to the Appendix D set exactly.")

    unreachable = [c.id for c in CL.CLUSTERS
                   if not any(SG.BY_ID[s].availability == SG.FACE for s in c.signals)]
    if unreachable:
        out.append(f"\n{', '.join(unreachable)} has no face-derivable signal and cannot be "
                   f"raised until note extraction lands;\nthe reason is recorded on the "
                   f"cluster rather than left to be discovered from an empty matrix.")
    else:
        out.append("\nEvery cluster retains at least one face-derivable signal, so none is "
                   "structurally unreachable in v1.")
    out.append("A cluster raised while a contributing signal could not be evaluated must "
               "say so (§4.4).")
    return "\n".join(out)


def derivations_report(argv: list[str]) -> str:
    """How every signal is derived and where in the annual report its inputs live."""
    from . import derivations as DV
    from . import rules as R

    only = _arg(argv, "--signal")
    if only:
        only = only.strip().upper()
        if only not in DV.BY_ID:
            raise SystemExit(f"unknown derivation {only!r}\n"
                             f"known: {', '.join(sorted(DV.BY_ID))}")

    out: list[str] = []
    if not only:
        m = DV.manifest()
        out.append("FDR DERIVATIONS — how each signal is computed, and from which "
                   "annual-report source")
        out.append(f"{m['signal_derivations']} signal derivations · {m['supporting']} "
                   f"supporting · {m['cross_cutting']} cross-cutting · "
                   f"{m['overlays']} sector overlay(s) · {DV.VERSION}")
        out.append(f"{len(R.IMPLEMENTED)} of {m['signal_derivations']} have a deterministic "
                   f"rule implemented: {', '.join(sorted(R.IMPLEMENTED))}")

    groups = ((("Signal derivations"), DV.SIGNAL_DERIVATIONS),
              ("Supporting derivations (not Appendix D signals)", DV.SUPPORTING_DERIVATIONS),
              ("Cross-cutting reads", DV.CROSS_CUTTING))

    for title, items in groups:
        shown = [d for d in items if not only or d.id == only]
        if not shown:
            continue
        if not only:
            out.append(_bar(title))
        for d in shown:
            has_rule = d.id in R.IMPLEMENTED
            state = ("rule implemented" if has_rule else
                     "no rule — derivation stated, computed by hand"
                     if d.kind == DV.SIGNAL else "reference")
            out.append(f"\n{d.id}  {d.title}"
                       + (f"   [{d.cluster}]" if d.cluster else "")
                       + f"   ({state})")
            out.append(f"    window       {d.window_years}y"
                       + (f"; needs {d.needs_prior_reports} prior annual report(s) beyond "
                          f"the current one" if d.needs_prior_reports else ""))
            out.append("    formula")
            out.append(_wrap_cli(d.formula, "      "))
            out.append("    read from")
            for s in d.source_lines():
                out.append(_wrap_cli(f"- {s}", "      "))
            if d.face_inputs:
                out.append(f"    requires     {', '.join(d.face_inputs)}")
            if d.optional_inputs:
                out.append(f"    uses if any  {', '.join(d.optional_inputs)}")
            if d.note_inputs:
                out.append(f"    note-level   {', '.join(d.note_inputs)}")
            for p in d.proxies:
                out.append("    PROXY")
                out.append(_wrap_cli(f"{p.instead_of} -> {p.use}. {p.why}. "
                                     f"{p.confidence_effect}", "      "))
            for x in d.reconciling_items:
                out.append(_wrap_cli(f"eliminate first: {x}", "      "))
            for n in d.notes:
                out.append(_wrap_cli(f"note: {n}", "      "))

    if not only:
        out.append(_bar("Schedule III mandatory schedules these derivations read"))
        out.append("Their absence from a filing is itself a reportable fact.")
        for did, where in DV.mandatory_schedules():
            out.append(f"  {did:12} {where}")

        out.append(_bar("Sector overlays — where a derivation is MODIFIED, not suppressed"))
        for ov in DV.OVERLAYS:
            out.append(f"\n{ov.id}  {', '.join(sorted(ov.business_models))}  "
                       f"-> modifies {', '.join(ov.affects)}")
            out.append(_wrap_cli(ov.instruction, "    "))
    return "\n".join(out)


def headline_report(argv: list[str]) -> str:
    """The tile registry — what block 2 shows, and on what basis.

    Parallel to `derivations`, and for the same reason: a figure on the first screen of
    every report is a decision somebody should be able to read and challenge without
    reading Python.
    """
    from . import headline as HL

    model = _arg(argv, "--model")
    if model and model not in SG.BUSINESS_MODELS:
        raise SystemExit(f"unknown business model {model!r}\n"
                         f"known: {', '.join(sorted(SG.BUSINESS_MODELS))}")

    m = HL.manifest()
    out = ["FDR EXECUTIVE DASHBOARD — the headline reads of block 2",
           f"{m['core']} core tile(s) on every entity · {m['overlays']} sector overlay(s) "
           f"· {HL.VERSION}",
           _wrap_cli(HL.ATTENTION_BASIS, "  ")]

    shown = HL.tiles_for(model) if model else HL.TILES
    if model:
        out.append(f"\nAs seen by a {model.replace('_', ' ')} entity "
                   f"({len(shown)} tile(s)).")

    for group, items in (("Core — every entity, no business model required",
                          [t for t in shown if t.is_core]),
                         ("Sector overlays — gated on the business model",
                          [t for t in shown if not t.is_core])):
        if not items:
            continue
        out.append(_bar(group))
        for t in items:
            out.append(f"\n{t.id}  {t.label}   [{t.unit}]")
            out.append(f"    formula      {t.formula}")
            out.append(f"    movement     {t.delta_kind}"
                       + (f"; attention at {t.attention}" if t.attention else ""))
            out.append(f"    salience     {t.salience}")
            out.append("    why it is on the first screen")
            out.append(_wrap_cli(t.basis, "      "))
            if not t.is_core:
                out.append(f"    applies to   {', '.join(sorted(t.business_models))}")
            if t.not_applicable_frameworks:
                out.append(f"    N/A under    "
                           f"{', '.join(sorted(t.not_applicable_frameworks))}")
            if t.context_over:
                out.append(f"    also read as a multiple of "
                           f"{' + '.join(t.context_over)} — {t.context_label}")
            if t.binder_required:
                out.append("    NOT BINDABLE TODAY")
                out.append(_wrap_cli(t.binder_required, "      "))
            for n in t.notes:
                out.append(_wrap_cli(f"note: {n}", "      "))

    items = HL.work_items()
    if items:
        out.append(_bar("Declared and not yet bindable — extraction work, not roadmap"))
        for tid, label, need in items:
            out.append(f"\n  {tid}  {label}")
            out.append(_wrap_cli(need, "      "))
    out.append(_bar("Canonical keys the dashboard reads"))
    out.append(_wrap_cli(", ".join(HL.keys_required()), "  "))
    return "\n".join(out)


def _wrap_cli(text: str, indent: str = "  ", width: int = 76) -> str:
    from .render import _wrap
    return _wrap(text, indent, width)


def _arg(argv: list[str], flag: str) -> str | None:
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def skeleton(argv: list[str]) -> str:
    positional = [a for i, a in enumerate(argv)
                  if not a.startswith("--")
                  and (i == 0 or not argv[i - 1].startswith("--"))]
    entity = positional[0] if positional else "Unnamed entity"
    periods = tuple(positional[1:])

    model = _arg(argv, "--model")
    if model and model not in SG.BUSINESS_MODELS:
        raise SystemExit(f"unknown business model {model!r}\n"
                         f"known: {', '.join(sorted(SG.BUSINESS_MODELS))}")
    framework = _arg(argv, "--framework")
    if framework and framework not in SG.FRAMEWORKS:
        raise SystemExit(f"unknown framework {framework!r}\n"
                         f"known: {', '.join(sorted(SG.FRAMEWORKS))}")

    assume = _arg(argv, "--assume")
    assume_fired = frozenset(a.strip().upper() for a in assume.split(",")) if assume else frozenset()
    unknown = assume_fired - set(SG.BY_ID)
    if unknown:
        raise SystemExit(f"unknown signal id(s) {sorted(unknown)}\n"
                         f"run `python -m fdr contract` for the list")

    size = _arg(argv, "--shortlist")
    cap = _arg(argv, "--capacity")

    profile = BusinessProfile(
        model=model,
        model_confidence="LOW" if model else None,
        basis="Supplied on the command line for demonstration; business-model classification "
              "is ROADMAP M5 and is not built.",
    ) if model else BusinessProfile()

    fmt = _arg(argv, "--matrix")

    # One span for the whole command. Without it, build_report closes its root before
    # render and the lint gate run, and a single invocation arrives in Phoenix as three
    # unrelated traces — which is worse than no tracing, because it looks like three runs.
    with T.span("fdr skeleton", input={
        "entity": entity, "periods": list(periods),
        "output": "json" if "--json" in argv else (fmt or "report"),
    }) as sp:
        rep = build_report(
            entity, periods,
            comparable_years=len(periods),
            business_profile=profile,
            framework=framework,
            assume_fired=assume_fired,
            shortlist_size=int(size) if size else None,
            capacity=int(cap) if cap else None,
        )
        sp.set("fdr.review_mode", rep.coverage.review_mode)
        sp.set("fdr.raised", [c.cluster_id for c in rep.raised()])

        if "--json" in argv:
            sp.set_output("json payload")
            return json.dumps(rep.to_dict(), indent=2, ensure_ascii=False)

        if fmt:
            from . import export
            if fmt not in ("csv", "md", "markdown"):
                raise SystemExit(f"unknown matrix format {fmt!r} — use csv or md")
            out = export.to_csv(rep) if fmt == "csv" else export.to_markdown(rep)
            assert_safe(out)   # the export goes through the same §17.2 gate as the report
            sp.set_output(f"matrix ({fmt})")
            return out

        text = render(rep)
        assert_safe(text)      # §17.2 — a failing report is not released
        sp.set_output(f"{len(text.splitlines())}-line report")
        return text


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    if "--trace" in argv:
        from . import tracing
        tracing.enable()

    cmd = argv[0] if argv else "contract"
    if cmd == "contract":
        print(contract_report(gaps_only="--gaps" in argv))
    elif cmd == "derivations":
        print(derivations_report(argv[1:]))
    elif cmd == "headline":
        print(headline_report(argv[1:]))
    elif cmd == "skeleton":
        print(skeleton(argv[1:]))
    elif cmd == "test":
        from . import (test_registry, test_derivations, test_rules, test_skeleton,
                       test_planning, test_spec_cases, test_tracing, test_headline,
                       test_interactions, test_reference_matrix)
        rc = 0
        for name, mod in (("registry", test_registry),
                          ("derivations", test_derivations),
                          ("rules", test_rules),
                          ("headline", test_headline),
                          ("skeleton", test_skeleton),
                          ("planning", test_planning),
                          ("interactions", test_interactions),
                          ("reference matrix", test_reference_matrix),
                          ("spec conformance", test_spec_cases),
                          ("tracing", test_tracing)):
            print(f"\n--- {name} ---")
            rc |= mod.main()
        return rc
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
