"""
observation.py — Structured, traceable SAR observation model
================================================================
Gap-closure Phase 1, Gap #1 ("Structured, traceable observation register").
See ../GAP_CLOSURE_LOG.md for the full rationale, what this does and does
not change, and how to revert it.

WHY THIS EXISTS
---------------
Before this module, every deterministic check (CheckTools.* in tool_sar.py)
and the coherence-check step in the pipeline each built its own ad-hoc dict
shape for a "finding" — some carried an `evidence` string, none carried a
confidence rating, a financial-statement cross-reference, an evidence-request
list, or a public-sector lens. That made two requirements from the source
specification impossible to enforce mechanically, because there was no single
object either rule could run against:

  * Source-Line Lineage Contract (spec §11) — the cardinal rule that an
    observation with no source trace must be suppressed or downgraded to an
    information request.
  * Confidence-is-separate-from-severity (spec §38) — "a severe issue found
    in poor OCR may remain High risk but Low confidence."

This module defines that one object (`Observation`), plus the two pure
functions that enforce those rules (`suppress_untraceable`,
`apply_confidence_downgrade`). It does NOT change what any check detects —
only the shape its result is carried in from here on.

WHAT THIS DOES NOT DO YET (tracked as Phase 2 in GAP_CLOSURE_LOG.md)
---------------------------------------------------------------------
  * `report_reference` (page/paragraph) is not populated by any caller yet —
    the current DB layer concatenates chunks without preserving a per-quote
    page number at the point a CheckTools method runs. `quoted_report_text`
    (the `evidence` string) is what's available today and is what the
    lineage check runs against.
  * `public_sector_lens`, `safe_candidate_wording`, `evidence_required` are
    present on the object so downstream phases don't need another schema
    migration, but nothing populates them yet — they default to None / [].
  * The writer LLM's own prose findings (Part 2 of the memorandum) do not
    yet flow through this object — only the deterministic checks
    (pre-flight, coherence, formal) do. Folding the writer's findings into
    this schema is the "two-pass writer" work named as Phase 1b in the log,
    deliberately deferred because it changes LLM-facing prompts that cannot
    be validated without a live model endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

# Tags and risk ratings are intentionally plain strings, not an Enum — the
# wiki's own contract (source spec §35, §37, §39) defines them as string
# enums in JSON, and every existing call site (CheckTools, the writer
# prompt) already produces plain strings. An Enum here would just add a
# conversion step at every boundary for no behavioural gain.
VALID_TAGS = {"FINDING", "RISK_FLAG", "AUDIT_POINTER"}
VALID_RISK_RATINGS = {"High", "Medium", "Low", "Information request only"}
VALID_CONFIDENCE = {"High", "Medium", "Low"}

# Gap-closure Phase 4 (LLM_Output_Specification_CAG_Statutory_Auditor_Report_
# Review.md, §18's "Recommended Structured Observation Object"). See
# GAP_CLOSURE_LOG.md for the fields this adds and the honest limitations on
# each — several of these are best-effort approximations, not the precise
# per-document decomposition the spec's example implies.
VALID_CONSISTENCY_TYPES = {"FS_AR", "FS_CARO", "AR_CARO", "FS_AR_CARO", "OTHER"}
VALID_REVIEWER_STATUSES = {"Pending", "Accepted", "Modified", "Rejected", "Evidence Requested"}

# The wiki's cardinal lineage rule (§11): an observation is "traceable" if a
# reviewer could go find it in the source package from what's stored here.
# Any one of these three being non-empty counts — a page/paragraph reference
# is the strongest trace, a quoted extract is a real trace even without a
# page number, and an FS note/line-item reference is a trace into the other
# half of the package. This module's tests (tests/test_observation.py)
# document exactly which combinations count.
# NOTE: the dataclass field is named `evidence` (kept for backward
# compatibility with CheckResult/pre-Phase-1 call sites — see the
# `Observation` docstring); `quoted_report_text` is only a `to_dict()`-time
# alias pointing at the same value, so the lineage test below reads the
# real attribute name, not the alias.
_TRACE_FIELDS = ("evidence", "report_reference", "financial_statement_reference")


@dataclass
class Observation:
    """One structured SAR review finding — the wiki's §39 observation contract.

    Field names follow the source specification's `Machine-Readable
    Observation Contract` (§39) rather than the shorter ad-hoc keys the
    pre-Phase-1 code used, with one deliberate exception: `evidence` is kept
    (rather than renamed outright to `quoted_report_text`) because it is the
    field name every existing CheckTools.CheckResult already uses and every
    existing prompt-assembly call site (agent.py build_writer_user_message)
    already reads. `to_dict()` emits both names pointing at the same value
    so old and new consumers each find what they expect.
    """

    check_id: str
    component: str
    tag: str
    observation: str
    risk_rating: str = "Information request only"
    confidence: str = "High"
    evidence: str = ""
    report_reference: str = ""
    financial_statement_reference: str = ""
    expected_treatment: Optional[str] = None
    gap: Optional[str] = None
    audit_risk: Optional[str] = None
    public_sector_lens: Optional[dict] = None
    evidence_required: list[str] = field(default_factory=list)
    safe_candidate_wording: Optional[str] = None
    caveats: list[str] = field(default_factory=list)
    observation_id: str = ""

    # --- Gap-closure Phase 4 additions (output-spec §18) ---
    # entity / financial_year: denormalised onto every observation so an
    # exported row is self-contained for a dashboard/tracker — not known at
    # construction time (checks don't carry package context), filled in once
    # via stamp_entity_context() late in the pipeline merge sequence.
    entity: str = ""
    financial_year: str = ""
    # source: the spec's per-document-type reference arrays. Populated by
    # output_classification.apply_output_classification() from
    # consistency_type, NOT hand-set per check — see that module's docstring
    # for why this is a best-effort bucket of the single `evidence` string
    # rather than a true per-document decomposition.
    source: dict = field(default_factory=lambda: {"financial_statements": [], "audit_report": [], "caro": []})
    consistency_type: Optional[str] = None  # FS_AR | FS_CARO | AR_CARO | FS_AR_CARO | OTHER
    sa_framework: Optional[dict] = None  # {standard, nature, materiality, pervasiveness, opinion_coherence_issue}
    recommended_audit_action: list[str] = field(default_factory=list)
    candidate_143_6: bool = False
    reviewer_status: str = "Pending"
    reviewer_comments: str = ""

    def __post_init__(self) -> None:
        if self.tag not in VALID_TAGS:
            raise ValueError(f"Observation.tag must be one of {VALID_TAGS}, got {self.tag!r}")
        if self.risk_rating not in VALID_RISK_RATINGS:
            raise ValueError(
                f"Observation.risk_rating must be one of {VALID_RISK_RATINGS}, got {self.risk_rating!r}"
            )
        if self.confidence not in VALID_CONFIDENCE:
            raise ValueError(
                f"Observation.confidence must be one of {VALID_CONFIDENCE}, got {self.confidence!r}"
            )
        if self.consistency_type is not None and self.consistency_type not in VALID_CONSISTENCY_TYPES:
            raise ValueError(
                f"Observation.consistency_type must be one of {VALID_CONSISTENCY_TYPES} or None, "
                f"got {self.consistency_type!r}"
            )
        if self.reviewer_status not in VALID_REVIEWER_STATUSES:
            raise ValueError(
                f"Observation.reviewer_status must be one of {VALID_REVIEWER_STATUSES}, got {self.reviewer_status!r}"
            )

    def is_traceable(self) -> bool:
        """§11 lineage test: does at least one source-trace field carry content?"""
        return any((getattr(self, f) or "").strip() for f in _TRACE_FIELDS)

    def to_dict(self) -> dict[str, Any]:
        """Dict form used everywhere downstream (pipeline output, frontend, writer prompt).

        Keeps the pre-Phase-1 keys (`check_id`, `tag`, `component`, `observation`,
        `risk_rating`, `evidence`, `obs_id`) so nothing that already reads an
        observation dict — the frontend's ReportDocument.jsx, agent.py's
        build_writer_user_message — needs to change, and adds the new §39
        fields alongside them.
        """
        d = asdict(self)
        d["quoted_report_text"] = self.evidence  # §39 name, same value as `evidence`
        d["obs_id"] = self.observation_id  # pre-Phase-1 alias, several call sites used this key
        # Output-spec §18 wants `quoted_text` as an array (it envisions
        # multiple quotes — one per contributing document). We only ever
        # capture one `evidence` string per observation, so this is that
        # string wrapped as a single-element list, not a real multi-quote
        # decomposition — see GAP_CLOSURE_LOG.md, Phase 4 limitations.
        d["quoted_text"] = [self.evidence] if self.evidence else []
        return d


def from_check_result(
    cr: "Any",
    *,
    confidence: str = "High",
    evidence_required: list[str] | None = None,
) -> Observation:
    """Builds an Observation from a `tool_sar.CheckResult`.

    `tool_sar.CheckResult` is not imported here to avoid a circular import
    (tool_sar has no dependency on this module, and should not need one just
    to run a regex check) — this function is a structural adapter, called
    from the pipeline, which already imports both.
    """
    return Observation(
        check_id=cr.check_id,
        component=cr.component,
        tag=cr.tag,
        observation=cr.observation,
        risk_rating=cr.risk_rating,
        confidence=confidence,
        evidence=cr.evidence or "",
        evidence_required=list(evidence_required or []),
    )


def from_preflight_dict(pf: dict, *, confidence: str = "High") -> Observation:
    """Builds an Observation from one entry of `CheckTools.run_preflight_checks()['observations']`.

    Pre-flight entries report a *missing* component — there is nothing in the
    package to quote, by definition (that's what "missing" means), so they
    have no source trace and `suppress_untraceable` will (correctly) confirm
    their tag stays AUDIT_POINTER rather than downgrade anything.
    """
    return Observation(
        check_id=pf["check_id"],
        component=pf["component"],
        tag=pf["tag"],
        observation=pf["observation"],
        risk_rating=pf.get("risk_rating", "Information request only"),
        confidence=confidence,
    )


def suppress_untraceable(observations: list[Observation]) -> list[Observation]:
    """Enforces the §11 cardinal lineage rule.

    ``IF observation has no source trace THEN suppress observation OR
    downgrade to information request.``

    This implementation downgrades rather than drops: a FINDING or RISK_FLAG
    with no quote, page reference or FS cross-reference is retagged
    AUDIT_POINTER (never silently discarded — an untraceable signal is still
    a signal that something needs manual verification) and gets a caveat
    recorded so a reviewer sees *why* it was downgraded instead of assuming
    the check is broken.

    Pre-flight "component X was not identified" observations are exempt from
    the downgrade by construction: they are already AUDIT_POINTER (see
    `from_preflight_dict`), so this function is a no-op on them either way.
    """
    out: list[Observation] = []
    for obs in observations:
        if obs.tag != "AUDIT_POINTER" and not obs.is_traceable():
            obs.tag = "AUDIT_POINTER"
            obs.caveats.append(
                "Downgraded from a direct finding to an audit pointer: no quoted report "
                "text, report reference or financial-statement reference was captured for "
                "this check, so it cannot be independently verified from this observation "
                "alone (source spec §11)."
            )
        out.append(obs)
    return out


def apply_confidence_downgrade(observations: list[Observation], review_status: str) -> list[Observation]:
    """Applies the §29.1 rule: when the memorandum is provisional (report
    finality not confirmed — see formal_review.compute_review_status), every
    High-confidence observation is capped at Medium.

    Deliberately global rather than per-observation "is this report-dependent"
    filtering (spec §29.1's literal wording) — nearly every observation in a
    SAR review *is* report-dependent, and a per-observation classifier would
    be guesswork without more signal than Phase 1 has. Documented as a known
    simplification in GAP_CLOSURE_LOG.md.
    """
    if review_status != "provisional":
        return observations
    for obs in observations:
        if obs.confidence == "High":
            obs.confidence = "Medium"
    return observations


def stamp_entity_context(observations: list[Observation], *, entity: str, financial_year: str) -> list[Observation]:
    """Denormalises entity/FY onto every observation (output-spec §18) —
    checks don't know the package's entity/FY at construction time, so this
    is filled in once, late in the pipeline's merge sequence, rather than
    threaded through every check function's signature."""
    for obs in observations:
        obs.entity = entity
        obs.financial_year = financial_year
    return observations


def assign_observation_ids(observations: list[Observation], *, prefix: str = "SAR") -> list[Observation]:
    """Sequential IDs across the *whole* merged observation set (pre-flight +
    formal + coherence), not per-category — so `SAR-01`, `SAR-02`, ... map
    1:1 to the memorandum's numbered list regardless of which check produced
    each one.
    """
    for i, obs in enumerate(observations, 1):
        obs.observation_id = f"{prefix}-{i:02d}"
    return observations


# ---------------------------------------------------------------------------
# Shared risk-rating elevation ladder (Gap-closure Phase 3, Gaps #6 and #7)
# ---------------------------------------------------------------------------

_RISK_ORDER = ["Information request only", "Low", "Medium", "High"]


def elevate_risk_rating(risk_rating: str) -> str:
    """One-level elevation on the wiki's own risk-rating scale (§37),
    capped at High. Deliberately does NOT elevate "Information request
    only" — that rating means "no risk conclusion is possible without
    outside evidence", not "risk one step below Low"; elevating it would
    imply a severity judgement this module has no basis for. Callers that
    want to elevate should check for that case themselves if it matters
    (both current callers — prior_year_continuity.py's recurrence rule and
    public_sector_lens.py's nature/context rule — already guard on it).

    Shared here (not duplicated in each caller) because both cite the same
    kind of source-spec elevation instruction and should use one canonical
    ladder rather than two that could drift apart.
    """
    try:
        idx = _RISK_ORDER.index(risk_rating)
    except ValueError:
        return risk_rating
    if risk_rating == "Information request only":
        return risk_rating
    return _RISK_ORDER[min(idx + 1, len(_RISK_ORDER) - 1)]
