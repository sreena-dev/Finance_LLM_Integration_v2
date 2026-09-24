"""
Output contract for the S01-S27 signal library on the XBRL (`as_db`) path.

This is a SEPARATE, self-contained contract from `pipeline/fdr/model.py` — the two FDR
paths (fs_db panel-based vs. as_db XBRL-fact-based) are kept fully independent per an
explicit decision on this codebase: the fs_db path's `SignalResult`/threshold/materiality
tables are not imported here, so a change on one path cannot silently alter the other.
The *shape* of the contract mirrors `pipeline/fdr/model.py` deliberately, because the
FDR spec's own rules (confidence != severity, an abstain must carry a reason) are true
regardless of which database backs the diagnostic.

THE TWO RULES THIS FILE ENFORCES MECHANICALLY (same as the fs_db path, see spec):
  Sec 4.4  CONFIDENCE IS NOT SEVERITY. Severity is a property of the risk (from the
           registry); confidence is a property of THIS measurement (data completeness,
           dimension coverage). They are separate fields and never derive from each other.
  Sec 17.3 LEADS, NOT FINDINGS. No field expresses a conclusion, a probability, or
           anything an LLM writes a verdict into. This module only carries measurements.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any

# ---- signal outcome vocabulary (closed set) ----------------------------------------
FIRED = "FIRED"                    # the rule ran and the signal condition held
NOT_FIRED = "NOT_FIRED"            # the rule ran and the signal condition did not hold
ABSTAIN = "ABSTAIN"                # the rule could not run on this filing; reason mandatory
NOT_APPLICABLE = "NOT_APPLICABLE"  # the underlying data shape does not exist in as_db at
                                   # all (e.g. no dimensioned CWIP ageing schedule) —
                                   # distinct from ABSTAIN, which is "not this filing"
SIGNAL_STATUSES = frozenset({FIRED, NOT_FIRED, ABSTAIN, NOT_APPLICABLE})

# ---- why a rule did not produce a measurement (closed set, queryable) --------------
# Mirrors the DATA/SCOPE split used on the fs_db path: a reader (or a GROUP BY) needs to
# tell "this filing lacks the data" apart from "this shape of data doesn't exist in
# as_db at all, for any filing" — the second is not a defect to chase per-entity.
PANEL_TOO_SHORT = "PANEL_TOO_SHORT"          # fewer fy_ends for this entity_cin than
                                              # the signal's window_years requires
INPUT_NEVER_BOUND = "INPUT_NEVER_BOUND"      # required concept has zero rows for this doc_id
INPUT_ZERO_DENOMINATOR = "INPUT_ZERO_DENOMINATOR"
DIMENSION_ABSENT_IN_DB = "DIMENSION_ABSENT_IN_DB"   # as_db carries no dimensioned rows
                                              # for this concept at all (confirmed by query,
                                              # not assumed) — always NOT_APPLICABLE, never
                                              # retried per-filing
NOT_COMPUTABLE = "NOT_COMPUTABLE"            # inputs present; arithmetic undefined (e.g.
                                              # negative denominator)

REASON_CODES = frozenset({
    PANEL_TOO_SHORT, INPUT_NEVER_BOUND, INPUT_ZERO_DENOMINATOR,
    DIMENSION_ABSENT_IN_DB, NOT_COMPUTABLE,
})

# ---- confidence (qualitative only, never a fabricated probability) -----------------
HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"
CONFIDENCE = frozenset({HIGH, MEDIUM, LOW})


@dataclass(frozen=True)
class SignalResult:
    """One S01-S27 signal, evaluated against one `doc_id`.

    `confidence` is None unless the signal actually ran (FIRED/NOT_FIRED) — an abstain
    that also carried a confidence would imply an assessment that was never made.
    """
    signal_id: str
    cluster_id: str
    status: str
    reason: str = ""                        # mandatory for ABSTAIN / NOT_APPLICABLE
    reason_code: str = ""                   # one of REASON_CODES; "" when the rule ran
    severity: str | None = None             # of the risk if FIRED — from the registry
    confidence: str | None = None
    confidence_basis: str = ""
    observation: str = ""                   # what was seen, in figures, one sentence
    trace: str = ""                         # the arithmetic, reconstructable by hand
    formula: str = ""                       # the stated derivation
    source_trace: tuple[str, ...] = ()      # table + concept_name(s) the figure came from
    missing_inputs: tuple[str, ...] = ()
    proxy_used: str = ""                    # stated single-period proxy, if the registry
                                             # declared one for this signal (never silent)
    is_by_nature_material: bool = False     # Sec 2.4/10.6 — set by xbrl_signal_materiality,
                                             # never by the rule itself

    def __post_init__(self) -> None:
        if self.status not in SIGNAL_STATUSES:
            raise ValueError(f"{self.signal_id}: bad status {self.status!r}")
        if self.status in (ABSTAIN, NOT_APPLICABLE) and not self.reason:
            raise ValueError(f"{self.signal_id}: {self.status} requires a reason (spec Sec 4.4)")
        if self.status in (ABSTAIN, NOT_APPLICABLE) and self.reason_code not in REASON_CODES:
            raise ValueError(
                f"{self.signal_id}: {self.status} requires a reason_code from REASON_CODES"
            )
        if self.confidence is not None and self.confidence not in CONFIDENCE:
            raise ValueError(f"{self.signal_id}: bad confidence {self.confidence!r}")
        if self.status in (ABSTAIN, NOT_APPLICABLE) and self.confidence is not None:
            raise ValueError(
                f"{self.signal_id}: a signal that did not run carries no confidence — "
                f"reporting one would imply a measurement that was never made"
            )

    @property
    def ran(self) -> bool:
        return self.status in (FIRED, NOT_FIRED)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Outcome:
    """A rule function's raw verdict, before it is wrapped into a `SignalResult`.

    Kept distinct from `SignalResult` for the same reason the fs_db path splits
    `rules.Outcome` from `model.SignalResult`: a rule computes a verdict from facts; the
    engine (`xbrl_signal_engine.py`) is the only place that attaches the registry's static
    metadata (cluster_id, severity, spec_basis) to it. Rules stay pure functions of the
    fact maps they're given — no registry lookups inside a rule.
    """
    fired: bool | None            # None = could not run (maps to ABSTAIN/NOT_APPLICABLE)
    observation: str = ""
    trace: str = ""
    formula: str = ""
    source_trace: tuple[str, ...] = ()
    confidence: str | None = None
    confidence_basis: str = ""
    missing_inputs: tuple[str, ...] = ()
    reason: str = ""
    reason_code: str = ""
    proxy_used: str = ""
    not_applicable: bool = False   # True => engine maps to NOT_APPLICABLE, not ABSTAIN

    @property
    def ran(self) -> bool:
        return self.fired is not None


def abstain(reason: str, reason_code: str, *,
            missing_inputs: tuple[str, ...] = (),
            source_trace: tuple[str, ...] = ()) -> Outcome:
    if reason_code not in REASON_CODES:
        raise ValueError(f"bad reason_code {reason_code!r}")
    return Outcome(
        fired=None, reason=reason, reason_code=reason_code,
        missing_inputs=missing_inputs, source_trace=source_trace,
    )


def not_applicable(reason: str, *, source_trace: tuple[str, ...] = ()) -> Outcome:
    """Sec DIMENSION_ABSENT_IN_DB and similar — the data shape doesn't exist in as_db at
    all, confirmed by a direct query, not per-filing missing data. Never retried."""
    return Outcome(
        fired=None, reason=reason, reason_code=DIMENSION_ABSENT_IN_DB,
        source_trace=source_trace, not_applicable=True,
    )
