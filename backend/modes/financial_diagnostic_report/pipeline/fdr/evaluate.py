"""
Signal evaluation — the stage the later milestones replace, and nothing else.

The pipeline shape is final from M3 onward. What changes is only how much of it can run:

  M3 (now)   no entity-year panel exists -> every computable signal ABSTAINs with the
             specific missing inputs named.
  M6-M9      the panel lands and the rules run; this module gains the rule callables.

TWO DECISIONS THAT DO NOT NEED THE PANEL, AND SO ARE REAL ALREADY
-----------------------------------------------------------------
NOT_APPLICABLE depends only on the reporting framework: `current_liabilities` does not
exist under Schedule III Division III, so a liquidity signal there is not a failed lookup
(P8 / ratio design §7.5). SUPPRESSED depends only on the business model: high receivables
in a tariff-based utility are a feature, not a risk (§13.1, and the §20 worked example).

Both are therefore decided here, today, from the business profile and the framework alone
— and both are STATED in the output. A silently suppressed signal is indistinguishable
from a missed one, which is the failure §15.2 is guarding against.
"""
from __future__ import annotations
from typing import Protocol

from . import derivations as DV
from . import grading as GR
from . import signals as SG
from . import tracing as T
from .model import (
    SignalResult, ABSTAIN, NOT_APPLICABLE, SUPPRESSED, FIRED, NOT_FIRED,
    BLOCKED_DATA, BLOCKED_SCOPE, NOT_APPLICABLE_FRAMEWORK, SUPPRESSED_BUSINESS_MODEL,
)


def _derivation_fields(sig: SG.Signal) -> dict[str, object]:
    """The stated derivation and its annual-report sources, for EVERY result.

    Attached before the status is known, so a suppressed, inapplicable or abstained
    signal carries the same method statement as one that fired. A reviewer challenging a
    result and a reviewer chasing a missing note both need the same two facts: how it
    would have been computed, and which schedule it reads.
    """
    d = DV.BY_ID.get(sig.id)
    if d is None:                                   # unreachable: validated at import
        return {}
    return {
        "formula": d.formula,
        "ar_source": d.source_lines(),
        "reconciling_items": d.reconciling_items,
    }


class PanelLike(Protocol):
    """What a signal rule will need. Implemented by ROADMAP M6; nothing satisfies it yet."""
    years: tuple[str, ...]

    def get(self, key: str, year: str) -> float | None: ...


def _suppression_reason(sig: SG.Signal, business_model: str) -> str:
    return (
        f"Suppressed: this movement is a normal feature of a {business_model.replace('_', ' ')} "
        f"business model, not a risk in itself (§13.1, §15.2). Stated rather than dropped so "
        f"the reviewer can see it was considered and why it was set aside."
    )


def _not_applicable_reason(sig: SG.Signal, framework: str) -> str:
    return (
        f"Not applicable: the entity reports under {framework}, whose format does not use the "
        f"classification this signal depends on. This is a concept gap declared by the "
        f"framework, not a figure that could not be found."
    )


def _abstain_reason(sig: SG.Signal) -> str:
    what = "a multi-year comparable panel" if sig.is_trend else "the mapped statement figures"
    return (
        f"Not evaluated: {what} is not available. The entity-year panel is not built "
        f"(ROADMAP M6), so no rule could run."
    )


ASSUMED_MARKER = "ASSUMED PRESENT — REVIEW MODE, NOT A MEASUREMENT."


def evaluate_signal(
    sig: SG.Signal,
    *,
    business_model: str | None = None,
    framework: str | None = None,
    panel: PanelLike | None = None,
    assume_fired: frozenset[str] = frozenset(),
    grade: str | None = None,
) -> SignalResult:
    """Evaluate one signal. Order matters: applicability, then relevance, then the rule.

    `assume_fired` is the review mode described in ROADMAP M10: it asserts a signal is
    present so the audit side can read the planning package and matrix row the system
    WOULD produce, before any engine exists to produce it. An assumed signal carries no
    figures, no trace and no confidence, and every one of them says so in its own text —
    an assumed result must never be mistakable for a measured one.
    """
    dv = _derivation_fields(sig)

    # 1. Does the concept exist in this entity's reporting framework?
    if framework and framework in sig.not_applicable_frameworks:
        return SignalResult(
            signal_id=sig.id,
            status=NOT_APPLICABLE,
            reason=_not_applicable_reason(sig, framework),
            reason_code=NOT_APPLICABLE_FRAMEWORK,
            reason_detail={"framework": framework},
            **dv,
        )

    # 2. Is it meaningful for this business model? (§15.2 false-positive reduction)
    if business_model and business_model in sig.suppressed_for:
        return SignalResult(
            signal_id=sig.id,
            status=SUPPRESSED,
            reason=_suppression_reason(sig, business_model),
            reason_code=SUPPRESSED_BUSINESS_MODEL,
            reason_detail={"business_model": business_model},
            **dv,
        )

    # 3. Review mode — asserted, not measured, and labelled as such everywhere it appears.
    if sig.id in assume_fired:
        return SignalResult(
            signal_id=sig.id,
            status=FIRED,
            reason=ASSUMED_MARKER,
            severity=sig.default_severity,
            confidence=None,          # nothing was measured, so nothing is known
            confidence_basis="No confidence: this signal was asserted for review, not evaluated.",
            observation=f"{ASSUMED_MARKER} No figures underlie this. Asserted so the planning "
                        f"package and matrix row for '{sig.title}' can be reviewed.",
            missing_inputs=sig.inputs,
            **dv,
        )

    # 4. Run the rule — not possible until the panel exists.
    if panel is None:
        from . import rules as R
        return SignalResult(
            signal_id=sig.id,
            status=ABSTAIN,
            reason=_abstain_reason(sig),
            missing_inputs=sig.inputs,
            reason_code=R.PANEL_ABSENT,
            blocked_by=BLOCKED_DATA,
            **dv,
        )

    # 5. A panel exists — run the rule, if one has been written for this signal.
    #    A signal with no rule yet abstains NAMING that fact, rather than being silently
    #    absent: "we have the figures but not the diagnostic" and "we have neither" are
    #    different coverage statements and §4.4 requires the difference to be visible.
    from . import rules as R

    outcome = R.run(sig.id, panel)
    if outcome is None:
        d = DV.BY_ID.get(sig.id)
        note_only = sig.availability == SG.NOTE
        return SignalResult(
            signal_id=sig.id,
            status=ABSTAIN,
            reason=(f"Not evaluated: no rule is implemented for this signal. "
                    + ("It is derived from note-level disclosure, which the fact layer does "
                       "not extract; the derivation and its source schedules are stated "
                       "against this entry so the figures can be pulled by hand."
                       if note_only else
                       "The panel carries the figures it would need; the diagnostic itself "
                       "is outstanding (ROADMAP M7-M9).")),
            missing_inputs=tuple(d.note_inputs) if (note_only and d) else (),
            # SCOPE, not DATA. Counting these as coverage failures would report the system
            # as broken for work that was deliberately deferred and is declared as such.
            reason_code=R.NEEDS_NOTE_EXTRACTION if note_only else R.RULE_NOT_IMPLEMENTED,
            blocked_by=BLOCKED_SCOPE,
            **dv,
        )

    if not outcome.ran:
        return SignalResult(
            signal_id=sig.id,
            status=ABSTAIN,
            # The rule's own reason is already self-describing and names the failing key
            # and years; prefixing "Not evaluated:" onto "Not computed. …" just doubles it.
            reason=(outcome.reason if outcome.reason.startswith("Not computed")
                    else f"Not evaluated: {outcome.reason}"),
            missing_inputs=tuple(outcome.missing),
            reason_code=outcome.code,
            reason_detail=outcome.detail,
            blocked_by=BLOCKED_SCOPE if outcome.code in R.SCOPE_CODES else BLOCKED_DATA,
            **dv,
        )

    # §4.3 — a low Input Quality Grade caps the confidence of EVERY diagnostic, whatever
    # the rule concluded about its own measurement. Applied here, once, after the rule has
    # had its say: the rule reasons about the figures it was given, the grade reasons about
    # where those figures came from, and neither is qualified to do the other's job.
    #
    # A cap only ever costs. A rule that already capped itself for a proxy is not charged
    # twice — `grading.cap` is a min against the ceiling, not a second deduction — and the
    # basis says which of the two bound, so the reader can tell a weak measurement from a
    # weak dataset.
    confidence, cbasis = outcome.confidence, outcome.confidence_basis
    if grade is not None and confidence is not None:
        capped = GR.cap(confidence, grade)
        if capped != confidence:
            cbasis += (f" Capped at {capped}: Input Quality Grade {grade} limits every "
                       f"diagnostic in this run, whatever this measurement's own quality.")
            confidence = capped

    return SignalResult(
        signal_id=sig.id,
        status=FIRED if outcome.fired else NOT_FIRED,
        # Severity is a property of the RISK and comes from the registry; confidence is a
        # property of THIS MEASUREMENT and comes from the rule. §4.4 — they are computed
        # in different places precisely so neither can inflate the other.
        severity=sig.default_severity if outcome.fired else None,
        confidence=confidence,
        confidence_basis=cbasis,
        observation=outcome.observation,
        trace=outcome.trace,
        evidence=tuple(outcome.evidence),
        # Which stand-ins were ACTUALLY applied on this entity's figures — a subset of the
        # derivation's declared proxies, because a proxy only exists where the real line
        # was absent. Declared-but-unused would overstate the degradation.
        proxies_used=tuple(outcome.proxies),
        **dv,
    )


def evaluate_all(
    *,
    business_model: str | None = None,
    framework: str | None = None,
    panel: PanelLike | None = None,
    assume_fired: frozenset[str] = frozenset(),
    grade: str | None = None,
) -> dict[str, SignalResult]:
    unknown = assume_fired - set(SG.BY_ID)
    if unknown:
        raise ValueError(f"unknown signal id(s) in assume_fired: {sorted(unknown)}")

    with T.span("evaluate signals", input={
        "signals": len(SG.SIGNALS), "business_model": business_model,
        "framework": framework, "panel": panel is not None,
        "assumed": sorted(assume_fired), "input_quality_grade": grade,
    }) as sp:
        results = {
            s.id: evaluate_signal(s, business_model=business_model, framework=framework,
                                  panel=panel, assume_fired=assume_fired, grade=grade)
            for s in SG.SIGNALS
        }
        by_status: dict[str, list[str]] = {}
        for r in results.values():
            by_status.setdefault(r.status, []).append(r.signal_id)
        for status, ids in by_status.items():
            sp.set(f"fdr.signals.{status.lower()}", ids)
            sp.set(f"fdr.signals.{status.lower()}.count", len(ids))
        # The missing inputs are the actionable half: they say what the panel must supply.
        missing = sorted({k for r in results.values() if r.status == ABSTAIN
                          for k in r.missing_inputs})
        sp.set("fdr.missing_inputs", missing)
        sp.set("fdr.missing_inputs.count", len(missing))
        sp.set_output({s: len(i) for s, i in by_status.items()})
        return results
