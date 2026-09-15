"""
public_sector_lens.py — Public-sector sensitivity tagging
============================================================
Gap-closure Phase 3, Gap #7. See ../GAP_CLOSURE_LOG.md for full rationale
and revert steps.

WHY THIS EXISTS
---------------
Source spec §33 (Public-Sector Sensitivity Engine): "The risk model must
permit elevation by nature/context, not only amount" — a small-value
matter can still be material to a C&AG review if it falls in a sensitive
category (grants, waivers, guarantees, IT/data, regulatory non-compliance,
related-party/Government-linked transactions, ...). Before this module,
`Observation.public_sector_lens` existed as a field (Gap-closure Phase 1)
but nothing populated it, and nothing implemented the "elevate by nature"
rule at all.

WHAT THIS DOES
--------------
`classify_public_sector_dimension()` matches an observation's text against
the §33 category keywords and returns a `public_sector_lens` dict
(`value` left null — this module does not compute quantum; `nature` names
the matched category; `context` is a one-line note; `regularity` /
`propriety` are flagged "requires_review" rather than judged, since only a
human reviewer can actually assess regularity/propriety).

`apply_public_sector_lens()` tags every matching observation and elevates
its risk rating one level (via `observation.elevate_risk_rating`) — but
only for observations that already carry a real risk rating (`High` /
`Medium` / `Low`). "Information request only" observations are left alone:
that rating means no risk conclusion is possible yet, not "risk one step
below Low", so elevating it would manufacture a severity judgement this
module has no basis for.

KEYWORD SCOPE — narrower than §33's own list, on purpose
------------------------------------------------------------
§33 lists 18 example categories; this module covers 6 groupings chosen for
being detectable from free text without over-firing on ordinary audit
language. Deliberately NOT included:
  * "losses" — too generic a word in financial-statement text (cash losses,
    current-year losses) to use as a keyword without swamping unrelated
    observations.
  * "value" — not a category, it's the wiki's own reminder that value alone
    isn't the only axis; nothing to match against.
  * bare "regulatory" — PRE-04's own component name is "Report on Other
    Legal and Regulatory Requirements", which every report has; matching
    on "non-compliance with" / "contravention of" instead avoids that.
Extend the keyword table only after checking it against existing
check_registry.py descriptions for exactly this kind of false-positive.
"""

from __future__ import annotations

from sar_prod_v3.observation import Observation, elevate_risk_rating

_SENSITIVE_KEYWORDS: dict[str, list[str]] = {
    "grants_subsidies": ["grant", "subsidy", "budgetary support", "scheme fund"],
    "waivers_writeoffs": ["waiver", "write-off", "write off", "ex-gratia"],
    "guarantees_comfort": ["guarantee", "letter of comfort"],
    "idle_stalled_assets": ["idle asset", "stalled project", "non-performing investment"],
    "it_data_cyber": ["data asset", "it system", "cyber", "audit trail"],
    "regulatory_noncompliance": ["non-compliance with", "noncompliance with", "contravention of", "violation of statute"],
    "related_party_govt": ["related part", "government-linked", "government linked"],
}


def classify_public_sector_dimension(text: str) -> dict | None:
    lower = (text or "").lower()
    for category, keywords in _SENSITIVE_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return {
                "value": None,
                "nature": category,
                "context": f"Matched public-sector-sensitive category: {category.replace('_', ' ')}.",
                "regularity": "requires_review",
                "propriety": "requires_review",
            }
    return None


def apply_public_sector_lens(observations: list[Observation]) -> list[Observation]:
    for obs in observations:
        lens = classify_public_sector_dimension(f"{obs.component} {obs.observation}")
        if lens is None:
            continue
        obs.public_sector_lens = lens
        if obs.risk_rating == "Information request only":
            continue
        elevated = elevate_risk_rating(obs.risk_rating)
        if elevated != obs.risk_rating:
            obs.caveats.append(
                f"Risk elevated from {obs.risk_rating} to {elevated}: this matter falls within a "
                f"public-sector-sensitive category ({lens['nature'].replace('_', ' ')}) — source "
                f"spec §33 requires materiality assessment by nature/context, not value alone."
            )
            obs.risk_rating = elevated
    return observations
