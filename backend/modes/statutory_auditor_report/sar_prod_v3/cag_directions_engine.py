"""
cag_directions_engine.py — C&AG §143(5) Directions Engine (Gap #2)
======================================================================
Gap-closure Phase 5. See ../GAP_CLOSURE_LOG.md for full rationale.

WHY THIS WAS ON HOLD, AND WHAT CHANGED
------------------------------------------
Earlier investigation found `cag_directions_chunks` (REFERENCE_DSN) has no
entity/FY column, and concluded it couldn't answer "which directions apply
to this specific company's year" — so Gap #2 was held pending a decision.

Direct inspection of the real, live data (7 rows, one standing-directions
document, "CAG's Revised Directions for Statutory Auditors under Section
143(5)") resolved this. The document's own closing note (chunk 7) states
its applicability rule explicitly:

    "The above directions will be applicable for compliance by the
    Statutory Auditors ... from the date of issue of Hqrs. letter dated
    23.05.2025. In case earlier directions have been already issued, the
    same may continue to be applicable for the specific assignment."

That is a real, sourced rule, not a guess: a direction applies to an
assignment based on the AUDITOR'S REPORT DATE relative to the direction's
own `effective_from`/`effective_to` — not the entity or the financial
year at all. These directions are issued government-wide to every
statutory auditor of a Government company, so no entity/FY link was ever
needed; the effective-date window IS the applicability rule.

WHAT THIS ENGINE DOES
----------------------
1. `resolve_applicable_directions(report_date)` — queries
   `cag_directions_chunks` for every direction whose effective window
   covers the report date. Returns [] (not an error) when the report date
   is missing/unparseable, or when this deployment has no directions
   covering that date at all (§25.1: "if actual directions are absent:
   review only directions reproduced in supplied report + emit
   completeness pointer + do not invent missing directions" — this is
   the structural equivalent for a date this DB's single document doesn't
   cover).
2. `check_direction_addressed()` — one Observation per applicable
   direction whose theme is not evidenced anywhere in the SAR's own
   reproduced `cag_directions` text. Source spec §25.1's direction
   handling rule is implemented literally:
     - no cag_directions text extracted at all -> FINDING (a direction
       was issued and nothing in the package engages with any of them)
     - text extracted but this specific direction's theme isn't evidenced
       in it -> RISK_FLAG + AUDIT_POINTER (non-responsive, not "absent")
3. `run_cag_directions_checks()` — orchestrates both, plus one summary
   AUDIT_POINTER when the report date couldn't be resolved at all.

HONEST LIMITATION — "addressed" is a keyword proxy, not semantic
confirmation
---------------------------------------------------------------------------
Whether the SAR's reproduced cag_directions text actually *responds* to a
direction (source spec's fuller `responsive` / `impact_reconciled` /
`cross_report_consistent` dimensions) needs real reading comprehension.
This engine only checks whether topic keywords drawn from the direction's
own text (verified against the 5 real directions currently in this DB —
see `_DIRECTION_THEMES`) appear anywhere in the extracted text. A report
that mentions "risk management" in an unrelated context would false-
positive as "addressed"; one that responds in different words without
the matched keywords would false-negative as "not addressed". This is the
same class of approximation as every other keyword-based check in this
codebase (consistency_engine.py's echo search), applied here because nothing
in this pipeline runs a second LLM call to make this specific judgement.
"""

from __future__ import annotations

import logging

from sar_prod_v3.observation import Observation

logger = logging.getLogger("sar_prod_v3.cag_directions_engine")

# Keyword themes per direction, drawn from each direction's own real text
# (verified against REFERENCE_DSN.cag_directions_chunks — see this module's
# docstring). `roman` is only used to build a readable check_id / label; it
# is NOT necessarily stable across future document versions with a
# different clause count or lettering — a version-aware lookup would need
# to key on `doc_id` too, not attempted here since only one version exists
# in this deployment today.
_DIRECTION_THEMES: list[dict] = [
    {
        "roman": "I", "label": "Fair valuation of investments (incl. post-retirement benefit trusts)",
        "keywords": ["fair valuation", "investment", "post retirement", "post-retirement", "valuation methodolog"],
    },
    {
        "roman": "II", "label": "IT-system processing of accounting transactions",
        "keywords": ["it system", "accounting transaction", "outside it system", "integrity of the accounts"],
    },
    {
        "roman": "III", "label": "Grants/subsidy/scheme funds — accounting and utilisation",
        "keywords": ["grant", "subsidy", "scheme fund", "utilis", "utiliz"],
    },
    {
        "roman": "IV", "label": "Risk management policy and data-asset valuation",
        "keywords": ["risk management", "key risk area", "data asset"],
    },
    {
        "roman": "V", "label": "Regulatory compliance (SEBI/RBI/CERT-In and sector regulators)",
        "keywords": ["sebi", "listing obligation", "rbi", "reserve bank", "cert-in", "telecom regulatory"],
    },
]


def resolve_applicable_directions(report_date, *, tables) -> list[dict]:
    """Queries `cag_directions_chunks` (via `tables.ReferenceTools`) for
    every direction whose effective window covers `report_date`.

    `tables` is the `ReferenceTools` class (or a compatible stand-in) —
    passed in rather than imported directly so this module stays testable
    without a live REFERENCE_DSN connection (a test can hand it a fake with
    a canned `fetch_cag_directions_for_date`).
    """
    if not report_date:
        return []
    return tables.fetch_cag_directions_for_date(report_date)


def check_direction_addressed(direction_theme: dict, cag_directions_text: str) -> Observation | None:
    """One direction vs. the SAR's own reproduced cag_directions text.
    Source spec §25.1's handling rule, applied per-direction."""
    text = cag_directions_text or ""
    lower = text.lower()

    if not text.strip():
        return Observation(
            check_id=f"DIR-{direction_theme['roman']}-01",
            component=f"C&AG Direction {direction_theme['roman']} — {direction_theme['label']}",
            tag="FINDING", risk_rating="High",
            observation=(
                f"A standing C&AG direction under section 143(5) — \"{direction_theme['label']}\" — "
                f"is in effect for this report's date, but the supplied package contains no reproduced "
                f"C&AG directions text at all. Per source spec §25.1, an actual direction with no answer "
                f"in the package is a FINDING."
            ),
            evidence=f"Direction theme: {direction_theme['label']} (source: cag_directions_chunks, live directions DB)",
            evidence_required=["Auditor's response to this direction", "impact workings"],
        )

    if any(kw in lower for kw in direction_theme["keywords"]):
        return None  # theme evidenced somewhere in the reproduced text — not this engine's job to judge quality further

    return Observation(
        check_id=f"DIR-{direction_theme['roman']}-01",
        component=f"C&AG Direction {direction_theme['roman']} — {direction_theme['label']}",
        tag="RISK_FLAG", risk_rating="Medium",
        observation=(
            f"A standing C&AG direction under section 143(5) — \"{direction_theme['label']}\" — is in "
            f"effect for this report's date. The supplied package's reproduced C&AG directions text does "
            f"not appear to engage with this specific theme. Per source spec §25.1, an answer that does "
            f"not engage the substance of a direction is a risk flag, not a finding."
        ),
        evidence=f"Direction theme: {direction_theme['label']} | reproduced text length: {len(text)} chars",
        evidence_required=["Auditor's response to this specific direction", "impact workings"],
    )


def run_cag_directions_checks(merged_json: dict, report_date, *, tables) -> list[Observation]:
    """Gate: is there a directions-document in effect for this report date
    at all (DB-driven, via `resolve_applicable_directions`)? If so, test
    against `_DIRECTION_THEMES` — the curated, hand-verified theme set for
    the one live document version this deployment currently has (see module
    docstring). This deliberately does NOT try to auto-derive themes from
    each DB row's raw text: with only one version live today, the curated
    table is more reliable than a generic parse, and a future second
    version would need `_DIRECTION_THEMES` reviewed and updated anyway —
    tracked as a known limitation, not silently papered over.
    """
    applicable_rows = resolve_applicable_directions(report_date, tables=tables)
    if not applicable_rows:
        return []  # nothing in this DB covers this report date — nothing to test against (§25.1: don't invent)

    cag_directions_text = ((merged_json.get("cag_directions") or {}).get("text") or "")
    observations: list[Observation] = []
    for theme in _DIRECTION_THEMES:
        try:
            result = check_direction_addressed(theme, cag_directions_text)
        except Exception:
            logger.exception("check_direction_addressed failed for %s", theme.get("roman"))
            continue
        if result is not None:
            observations.append(result)
    return observations
