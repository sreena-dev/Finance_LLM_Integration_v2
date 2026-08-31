"""
FDR output contract — the shapes §14.3 requires, and the intermediate results that build them.

WHY THESE TYPES, AND WHY NOW
----------------------------
The walking skeleton (ROADMAP M3) runs the whole FDR pipeline with every diagnostic
abstaining. That is not a placeholder: §4.4 makes the pre-analysis validation note and the
"diagnostics not run, and why" list mandatory WHATEVER the input grade, so a report that
honestly says "I could not evaluate any of this, here is exactly why" is already a valid
FDR. Everything after M3 replaces an evaluator, never the report shape.

The two rules these types exist to enforce mechanically:

  §4.4  CONFIDENCE IS NOT SEVERITY. Severity is how bad the risk is if real; confidence is
        how sure we are the signal is real and correctly measured. They are separate
        fields, they never derive from each other, and a high-severity/low-confidence item
        is reported as important-if-true rather than suppressed or promoted.

  §17.3 LEADS, NOT FINDINGS. Nothing here can express a conclusion. There is no `verdict`,
        no probability, and no field an LLM writes a number into.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any

# ---- signal outcome vocabulary ---------------------------------------------------
FIRED = "FIRED"                    # the rule ran and the signal is present
NOT_FIRED = "NOT_FIRED"            # the rule ran and the signal is absent — a real negative
ABSTAIN = "ABSTAIN"                # the rule could not run; reason mandatory
NOT_APPLICABLE = "NOT_APPLICABLE"  # the concept does not exist in this framework (§7.5 of
                                   # the ratio design; P8 — "not applicable" != "not found")
SUPPRESSED = "SUPPRESSED"          # normal for this business model (§13.1, §15.2), and the
                                   # suppression is STATED, never silent
SIGNAL_STATUSES = frozenset({FIRED, NOT_FIRED, ABSTAIN, NOT_APPLICABLE, SUPPRESSED})

# ---- cluster outcome vocabulary --------------------------------------------------
RAISED = "RAISED"
NOT_RAISED = "NOT_RAISED"
CLUSTER_ABSTAIN = "ABSTAIN"
CLUSTER_NOT_APPLICABLE = "NOT_APPLICABLE"
CLUSTER_STATUSES = frozenset({RAISED, NOT_RAISED, CLUSTER_ABSTAIN, CLUSTER_NOT_APPLICABLE})

# ---- why a diagnostic produced no measurement ------------------------------------
# Two materially different things that a single "not evaluated" count merges, and the
# merge misleads in a specific direction: it reports the system as broken when part of
# the number is work deliberately not yet done.
#
#   DATA   the diagnostic is built and the figures are not there  -> extraction/binder work
#   SCOPE  the figures may be there; the diagnostic is not built  -> roadmap, not a defect
#
# §4.4 requires the coverage note to state what could not be evaluated AND why. A reader
# cannot act on "18 not evaluated"; they can act on "10 blocked by data, 8 not yet built".
BLOCKED_DATA = "DATA"
BLOCKED_SCOPE = "SCOPE"

# Neither of these is a failure of any kind, and neither is counted as one. They are
# decided from the framework and the business model alone, before any figure is read.
NOT_APPLICABLE_FRAMEWORK = "NOT_APPLICABLE_FRAMEWORK"
SUPPRESSED_BUSINESS_MODEL = "SUPPRESSED_BUSINESS_MODEL"


# ---- confidence (App H) — always qualitative, never a fabricated probability ------
HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"
CONFIDENCE = frozenset({HIGH, MEDIUM, LOW})


@dataclass(frozen=True)
class SignalResult:
    """One signal, evaluated. `confidence` is None unless the signal actually ran.

    `formula`, `ar_source` and `proxies_used` travel WHATEVER the status. That is the
    point of carrying them here rather than only on a result that ran: an abstain that
    says "not evaluated" is a shrug, and an abstain that says "here is the derivation,
    here is the schedule it reads, here is what was missing" is a work instruction. §4.4
    requires the second.
    """
    signal_id: str
    status: str
    reason: str = ""                       # mandatory for ABSTAIN / NOT_APPLICABLE / SUPPRESSED
    severity: str | None = None            # of the risk — copied from the registry when FIRED
    confidence: str | None = None          # App H — how sure we are; None when nothing ran
    confidence_basis: str = ""
    observation: str = ""                  # §14.2 step 1 — what was seen, in figures
    trace: str = ""                        # the arithmetic, reconstructable by a reviewer
    evidence: tuple[str, ...] = ()         # where to look: table / page / row
    missing_inputs: tuple[str, ...] = ()   # what would have to exist for this to run
    formula: str = ""                      # the stated derivation (derivations.py)
    ar_source: tuple[str, ...] = ()        # where in the annual report the inputs live
    proxies_used: tuple[str, ...] = ()     # stand-ins actually applied, never silent
    reconciling_items: tuple[str, ...] = ()  # eliminate these before the signal is raised
    # WHY a result is not a measurement, as a closed code plus structured detail.
    # `reason` is prose for a reader; `reason_code` is the same fact for a query. Without
    # the code, "which missing figure blocks the most diagnostics across the corpus" is a
    # regular expression over English; with it, it is a GROUP BY.
    reason_code: str = ""
    reason_detail: dict[str, Any] = field(default_factory=dict)
    blocked_by: str = ""                   # DATA | SCOPE | "" — see `blocked_kind`

    def __post_init__(self) -> None:
        if self.status not in SIGNAL_STATUSES:
            raise ValueError(f"{self.signal_id}: bad status {self.status!r}")
        if self.status in (ABSTAIN, NOT_APPLICABLE, SUPPRESSED) and not self.reason:
            raise ValueError(f"{self.signal_id}: {self.status} requires a reason (§4.4)")
        if self.confidence is not None and self.confidence not in CONFIDENCE:
            raise ValueError(f"{self.signal_id}: bad confidence {self.confidence!r}")
        if self.status in (ABSTAIN, NOT_APPLICABLE) and self.confidence is not None:
            raise ValueError(
                f"{self.signal_id}: a diagnostic that did not run carries no confidence — "
                f"reporting one would imply an assessment that was never made"
            )

    @property
    def ran(self) -> bool:
        return self.status in (FIRED, NOT_FIRED)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ClusterPackage:
    """The §10.3 package. Every field the spec lists is present, or explicitly unavailable."""
    cluster_id: str
    theme: str
    status: str
    reason: str = ""
    contributing_signals: tuple[SignalResult, ...] = ()
    alt_explanations: tuple[str, ...] = ()
    affected_assertions: tuple[str, ...] = ()
    regularity_matters: tuple[str, ...] = ()
    inherent_risk: str | None = None
    significant_risk: bool | None = None
    control_implications: str = ""
    planning_significance: str = ""
    recommended_response: dict[str, str] = field(default_factory=dict)   # nature/timing/extent
    specialist_referral: tuple[str, ...] = ()
    evidence_request: tuple[str, ...] = ()
    diagnostic_confidence: str | None = None
    confidence_basis: str = ""
    corroboration: str = "standalone"
    # The §10.2 interactions that BEAR on this theme, already resolved against what the run
    # found (`interactions.py`). Only live and latent edges appear here; a declared
    # interaction that is genuinely absent belongs in the report-level map, not on a card
    # that a reader takes as this theme's planning package.
    interactions: tuple[str, ...] = ()
    # The one-line provenance stamp under the cluster title: which schedules the present
    # signals were read from, and how much of the theme actually fired. It is deliberately
    # made of what the engine measured rather than an authored strapline — a cluster raised
    # on one signal out of four should LOOK thinner than one raised on four, in the header,
    # before anybody opens it.
    anchor: tuple[str, ...] = ()
    materiality_basis: tuple[str, ...] = ()
    priority_rank: int | None = None
    priority_reasoning: str = ""
    unevaluated_signals: tuple[str, ...] = ()   # §4.4 — named, never quietly omitted
    interpretation_withheld: str = ""
    # ^ §2.1 — "beginning interpretation with ratios, before business understanding, is
    #   prohibited." Set when no business profile was formed. It is not a stylistic caveat:
    #   without the business model the system cannot tell a risk from a normal feature of
    #   the entity (§15.2 suppression literally reads the business model), so a cluster
    #   raised without one is uninterpretable by construction.

    def __post_init__(self) -> None:
        if self.status not in CLUSTER_STATUSES:
            raise ValueError(f"{self.cluster_id}: bad status {self.status!r}")
        if self.status != RAISED and not self.reason:
            raise ValueError(f"{self.cluster_id}: {self.status} requires a reason (§4.4)")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["contributing_signals"] = [s.to_dict() for s in self.contributing_signals]
        return d


@dataclass
class DiagnosticNotRun:
    """§4.4 — 'the model states which diagnostic is weakened or unavailable as a result'.

    `formula` and `ar_source` turn this list from an apology into a work list: a reviewer
    reading it can go to the named schedule, pull the figures, and compute the stated
    derivation by hand. That is the difference between a diagnostic that is unavailable
    and one that is merely un-automated.
    """
    diagnostic: str
    reason: str
    impact: str = ""            # which cluster or question is weakened by its absence
    formula: str = ""           # how it would be derived
    ar_source: tuple[str, ...] = ()   # where in the annual report to read the inputs
    reason_code: str = ""       # the closed code — queryable across the corpus
    blocked_by: str = ""        # DATA (extraction ticket) | SCOPE (roadmap item)
    reason_detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Coverage:
    """Block 1 — coverage, limitations and the mandatory pre-analysis validation note."""
    entity: str
    periods: tuple[str, ...]
    flavor: str = "unknown"                       # standalone | consolidated
    input_quality_grade: str | None = None        # A-E, from `grading.grade`
    grade_basis: str = ""
    # The components behind the letter. §9.4's guardrail against a bare composite applies
    # to the grade as much as to a distress score: a letter a reader cannot decompose is a
    # letter they can only accept or ignore, never challenge.
    grade_components: tuple[dict[str, Any], ...] = ()
    grade_ceiling: str | None = None              # what the grade permits a diagnostic to claim
    optional_inputs: tuple[str, ...] = ()         # TB | FSA | AR | peer | budget | regulatory
    statements_received: tuple[str, ...] = ()
    statements_missing: tuple[str, ...] = ()
    comparable_years: int = 0
    validation_note: str = ""
    limitations: tuple[str, ...] = ()
    review_mode: bool = False               # signals asserted for review, not measured
    assumed_signals: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BusinessProfile:
    """Block 3 (§5). Everything optional — M5 populates it; until then it is honestly empty."""
    model: str | None = None
    model_confidence: str | None = None
    revenue_model: str | None = None
    cost_structure: str | None = None
    financing: str | None = None
    value_drivers: tuple[str, ...] = ()
    sector: str | None = None
    sector_confidence: str | None = None
    inherent_risk_expectations: tuple[str, ...] = ()
    basis: str = ""
    # §18.2 — the classification must be attributable. `operator` is a stated human
    # decision, `model` an automatic one, and the two are never rendered as the same
    # thing: a reviewer challenging a suppressed diagnostic has to know which it was.
    classification_source: str = ""      # operator | classifier | "" when not formed
    confirmed: bool = False              # a human has signed off on this classification
    evidence: tuple[str, ...] = ()       # what in the filing the classification was read from
    disagreement: str = ""               # set when operator and classifier differ

    @property
    def formed(self) -> bool:
        return self.model is not None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# §17.3 standing caveats. Present in every FDR, whatever else is.
STANDING_CAVEATS: tuple[str, ...] = (
    "The outputs are leads for audit attention, not findings, opinions or conclusions.",
    "Composite indicators are adapted for diagnostic screening, not predictive.",
    "Benchmarks are used only where supplied with a source and date; none is ever assumed.",
    "The audit team decides the audit strategy, scope and materiality; this report informs "
    "but does not decide the plan.",
    "A standalone report is presented with the same confidence in its own reasoning as a "
    "corroborated one.",
)


def _jsonable(v: Any) -> Any:
    """Tuples are the right shape in Python and the wrong shape in JSON.

    Normalising here rather than at the call site means the payload survives a
    dumps/loads round trip unchanged — which is what makes §16 reproducibility checkable
    by comparing two payloads rather than two renders.
    """
    if isinstance(v, tuple):
        return [_jsonable(x) for x in v]
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    return v


@dataclass
class FDRReport:
    coverage: Coverage
    business_profile: BusinessProfile
    # Block 2's headline reads (headline.TileValue), computed from the panel. Typed as
    # Any for the same reason `priorities` is: `model.py` is the output CONTRACT and must
    # not import the modules that produce the results, or the contract starts depending on
    # its own implementations.
    headline: tuple[Any, ...] = ()
    clusters: tuple[ClusterPackage, ...] = ()
    # The §10.2 matrix resolved for this run (`interactions.InteractionMap`) — every
    # declared edge with its state, the reinforcing complexes, and the block-6 lead note.
    # It sits on the report rather than only on the clusters because a complex is a
    # statement about a GROUP of themes, and there is no single cluster it belongs to.
    interactions: Any = None
    priorities: tuple[Any, ...] = ()                    # priority.Priority, ranked
    below_the_line: tuple[Any, ...] = ()                # priority.BelowLine — excluded by capacity
    capacity: dict[str, Any] = field(default_factory=dict)   # the §11.2 arithmetic, disclosed
    shortlist: tuple[str, ...] = ()                     # ranked cluster ids
    diagnostics_not_run: tuple[DiagnosticNotRun, ...] = ()
    caveats: tuple[str, ...] = STANDING_CAVEATS
    versions: dict[str, str] = field(default_factory=dict)   # §16 reproducibility

    def raised(self) -> tuple[ClusterPackage, ...]:
        return tuple(c for c in self.clusters if c.status == RAISED)

    def to_dict(self) -> dict[str, Any]:
        """The §14.3 machine-readable schema."""
        return _jsonable({
            "entity": self.coverage.entity,
            "periods": self.coverage.periods,
            "input_quality_grade": self.coverage.input_quality_grade,
            "optional_inputs": self.coverage.optional_inputs,
            "business_profile": self.business_profile.to_dict(),
            "headline": [t.to_dict() for t in self.headline],
            "risk_clusters": [c.to_dict() for c in self.clusters],
            "risk_interactions": (self.interactions.to_dict() if self.interactions
                                  else {"edges": [], "complexes": [], "note": ""}),
            "diagnostics_not_run": [d.to_dict() for d in self.diagnostics_not_run],
            "caveats": self.caveats,
            "coverage": self.coverage.to_dict(),
            "shortlist": self.shortlist,
            "priorities": [p.to_dict() for p in self.priorities],
            "below_the_line": [b.to_dict() for b in self.below_the_line],
            "capacity": self.capacity,
            "versions": self.versions,
        })
