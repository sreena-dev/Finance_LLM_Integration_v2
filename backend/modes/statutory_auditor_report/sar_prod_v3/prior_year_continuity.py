"""
prior_year_continuity.py — Prior-year opinion & observation continuity
==========================================================================
Gap-closure Phase 3, Gap #6. See ../GAP_CLOSURE_LOG.md for full rationale
and revert steps.

WHY THIS EXISTS
---------------
`FetchTools.fetch_prior_year_result()` (tool_sar.py) has existed since
before any gap-closure work started, and the pipeline has always called
it (Step 2) — but nothing ever consumed its result, and nothing ever
wrote a result for it to find, because the `sar_results` table it queries
has never existed in this deployment (its own `information_schema.tables`
EXISTS check always returned false). Source spec §26 lists two rows this
blocked entirely: "Prior modification <-> Current report: resolution" and
"Prior C&AG comment <-> Current package: resolution/continuation", plus
an explicit elevation rule: "Prior-year unresolved C&AG comment repeated
current year -> elevate one risk level."

THIS MODULE PROVIDES BOTH HALVES
---------------------------------
1. `build_prior_year_summary()` — a compact record (NOT the full report)
   saved after every run via the new `FetchTools.save_sar_result()`
   (tool_sar.py), so next year's run has something to compare against.
   `FetchTools.save_sar_result()` creates the `sar_results` table itself
   (`CREATE TABLE IF NOT EXISTS`) — there is no migration path in this
   sandbox to run one separately, so the write path is self-bootstrapping:
   the very first time a report is generated after this change, the table
   is created and the record written; from the second run for the same
   entity onward, `fetch_prior_year_result` starts finding something.
2. `check_opinion_trend()` — a new Observation (or None) comparing the
   prior year's opinion type to the current one (source spec's "Prior
   modification <-> Current report" row).
3. `elevate_recurring_observations()` — matches the CURRENT run's
   observations against the PRIOR run's stored high-risk observations by
   `check_id`, and elevates risk one level on a match (source spec's
   explicit elevation rule; the closest deterministic proxy for "the same
   C&AG comment recurring" available without semantic text matching).

WHAT "MATCHING BY check_id" DOES AND DOESN'T CATCH
------------------------------------------------------
A recurring `CONS-CARO-IX-01` (CARO default + silent going concern, both
years) is caught. A recurring substantive issue that happens to route
through a different check between years (e.g. a going-concern matter that
was RISK_FLAG via CHK-COH-03 last year and CONS-CARO-IX-01 this year) is
NOT caught — check_id identity is a precise but narrow proxy for "the same
matter". Documented as a known limitation rather than attempting fuzzy
text matching, which would need a materially different (and much less
predictable) design.
"""

from __future__ import annotations

import logging

from sar_prod_v3.observation import Observation, elevate_risk_rating

logger = logging.getLogger("sar_prod_v3.prior_year_continuity")

_MODIFIED_TYPES = {"qualified", "adverse", "disclaimer"}


def build_prior_year_summary(merged_json: dict, observations: list[Observation], review_status: str) -> dict:
    """Compact record to persist via FetchTools.save_sar_result(). Only
    what check_opinion_trend() / elevate_recurring_observations() need next
    year — not the full merged JSON or the rendered memorandum."""
    opinion_type = (merged_json.get("opinion") or {}).get("type", "unclear")
    high_risk = [
        {
            "check_id": o.check_id, "component": o.component, "tag": o.tag,
            "risk_rating": o.risk_rating, "observation": o.observation,
        }
        for o in observations
        if o.tag == "FINDING" or o.risk_rating == "High"
    ]
    return {
        "opinion_type": opinion_type,
        "review_status": review_status,
        "high_risk_observations": high_risk,
    }


def check_opinion_trend(merged_json: dict, prior_result: dict | None) -> Observation | None:
    """Source spec §26 row 'Prior modification <-> Current report'."""
    if not prior_result:
        return None
    prior_opinion = (prior_result.get("opinion_type") or "").lower()
    current_opinion = ((merged_json.get("opinion") or {}).get("type") or "").lower()
    if not prior_opinion or not current_opinion:
        return None

    if prior_opinion in _MODIFIED_TYPES and current_opinion in _MODIFIED_TYPES:
        return Observation(
            check_id="PRIOR-OPN-01",
            component="Continuity — Prior-Year Opinion Trend",
            tag="RISK_FLAG", risk_rating="Medium",
            observation=(
                f"The prior-year opinion was {prior_opinion} and the current-year opinion is "
                f"also {current_opinion}. A modification persisting across years warrants "
                f"confirmation that the underlying matter — and any change in its extent — has "
                f"been separately assessed rather than simply carried forward."
            ),
            evidence=f"Prior-year opinion: {prior_opinion} | Current-year opinion: {current_opinion}",
            evidence_required=["Prior-year auditor's report", "management's explanation for the continuing modification"],
        )
    if prior_opinion in _MODIFIED_TYPES and current_opinion not in _MODIFIED_TYPES:
        return Observation(
            check_id="PRIOR-OPN-01",
            component="Continuity — Prior-Year Opinion Trend",
            tag="AUDIT_POINTER", risk_rating="Information request only",
            observation=(
                f"The prior-year opinion was {prior_opinion}; the current-year opinion is "
                f"{current_opinion}. Confirm the resolution of the prior-year matter is "
                f"documented (e.g. in the current report's Other Matter or Basis section) rather "
                f"than simply having gone unmentioned."
            ),
            evidence=f"Prior-year opinion: {prior_opinion} | Current-year opinion: {current_opinion}",
            evidence_required=["Prior-year auditor's report", "documentation of how the prior-year matter was resolved"],
        )
    return None  # both unmodified, or current was modified and prior wasn't — nothing to flag here


def elevate_recurring_observations(observations: list[Observation], prior_result: dict | None) -> list[Observation]:
    """Source spec: 'Prior-year unresolved C&AG comment repeated current
    year -> elevate one risk level.'"""
    if not prior_result:
        return observations
    prior_ids = {o.get("check_id") for o in (prior_result.get("high_risk_observations") or [])}
    for obs in observations:
        if obs.check_id not in prior_ids:
            continue
        elevated = elevate_risk_rating(obs.risk_rating)
        if elevated != obs.risk_rating:
            obs.caveats.append(
                f"Risk elevated from {obs.risk_rating} to {elevated}: this matter (check_id "
                f"{obs.check_id}) also appeared as a high-risk observation in the prior-year "
                f"review — recurring unresolved matters are elevated one risk level."
            )
            obs.risk_rating = elevated
        else:
            obs.caveats.append(
                f"This matter (check_id {obs.check_id}) also appeared as a high-risk observation "
                f"in the prior-year review."
            )
    return observations
