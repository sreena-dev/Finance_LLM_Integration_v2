"""
formal_review.py — Formal-checks wiring, review status and confidence
========================================================================
Gap-closure Phase 1, Gap #5 ("Wire the already-written formal checks +
provisional-memo status"). See ../GAP_CLOSURE_LOG.md for full rationale.

Before this module, three deterministic checks already existed in
tool_sar.py — `CheckTools.validate_udin_format`, `check_eom_closing_sentence`,
and (as of this same Phase-1 pass) `check_report_date_sequence` — but nothing
in the pipeline ever called them. UDIN validity, the SA 706.8 closing
sentence, and report-date-before-approval-date were being judged by the
writer LLM in prose instead of by the deterministic checks the wiki
specifies for exactly this purpose (source spec §29, §30, §35.1).

`check_kam_auditor_response` (SA 701's per-matter "how our audit addressed
this key audit matter" requirement) was added later, the same way, once the
main-text fetch bug that had been hiding real EoM/KAM content from these
checks was fixed — see GAP_CLOSURE_LOG.md.

This module is the pure-Python glue: it reads the merged extractor JSON,
calls the right CheckTools methods, and derives the two package-level
signals the wiki also specifies but the pipeline never computed —
`review_status` (§47: complete | provisional | blocked) and the confidence
downgrade that must follow from it (§29.1).

Deliberately kept free of any yukta/LLM/DB import — this is exactly the
part of "close gap #5" that has nothing to do with an LLM call, so it is
also exactly the part that can be unit-tested without a live model
endpoint or database connection (see tests/test_formal_review.py).

WHAT THIS DOES NOT COVER YET (tracked in GAP_CLOSURE_LOG.md as Phase 2)
--------------------------------------------------------------------------
  * `review_status` currently only reacts to: the main report text being
    unusable, mandatory FS tables missing, and UDIN. It does NOT yet react
    to CARO/IFC applicability being unresolved (source spec §22/§47's
    "CARO applicability uncertain -> suspend clause checks") — that is Gap
    #4 (Applicability Gates), a separate, larger piece of Phase 2.
  * Signed/final-state detection, scope-mismatch detection and page-
    continuity checks (source spec §8) are not implemented here or
    anywhere else yet.
"""

from __future__ import annotations

import logging
from typing import Any

from sar_prod_v3.observation import Observation, from_check_result

logger = logging.getLogger("sar_prod_v3.formal_review")


def _udin_checks(formal_checks: dict) -> list["Any"]:
    """Runs CheckTools.validate_udin_format once per auditor block.

    Joint/branch audits list more than one auditor (agent.py's writer prompt
    already asks the memo to "list FRN of each" under 2.1) — each gets its
    own check_id suffix so a joint-audit report doesn't collapse two
    independent UDIN findings into one. A report with no auditor block
    extracted at all still gets exactly one CHK-UDIN-01, run against an
    empty string, so "no UDIN could even be located" is itself a finding
    rather than a silent skip.
    """
    from sar_prod_v3.tool_sar import CheckTools

    auditors = formal_checks.get("auditors") or [{}]
    results = []
    for i, auditor in enumerate(auditors):
        cr = CheckTools.validate_udin_format(auditor.get("udin", "") or "")
        if i > 0:
            cr.check_id = f"{cr.check_id}-{i + 1}"
        results.append(cr)
    return results


def _eom_closing_sentence_check(main_json: dict) -> "Any | None":
    """Runs CheckTools.check_eom_closing_sentence — but only when the
    extractor found an Emphasis of Matter section at all. When EoM is
    absent, PRE-03 (pre-flight) already covers "no EoM identified" as its
    own AUDIT_POINTER; running this check too would raise a second,
    redundant observation about the same absence.
    """
    from sar_prod_v3.tool_sar import CheckTools

    eom = main_json.get("emphasis_of_matter") or {}
    if not eom.get("present"):
        return None

    # Reconstruct the best available text of the EoM section from what the
    # extractor already captured: every item's quote, plus the closing
    # sentence quote if the extractor found one. This is the same text the
    # regex-based check needs, without a second DB fetch or LLM call.
    parts = [item.get("quote", "") for item in (eom.get("items") or [])]
    if eom.get("closing_sentence_quote"):
        parts.append(eom["closing_sentence_quote"])
    eom_text = "\n".join(p for p in parts if p)

    return CheckTools.check_eom_closing_sentence(eom_text)


def _kam_auditor_response_check(main_json: dict) -> "Any | None":
    """Runs CheckTools.check_kam_auditor_response — but only when the
    extractor found a Key Audit Matters section at all. KAM presence itself
    is not this check's concern: applicability.resolve_kam_applicability
    (APPL-KAM-01) already covers "no KAM identified" without asserting a
    finding, since KAM is only mandatory for listed entities and listed
    status isn't established from this package (source spec §19.1). Running
    this check on an absent KAM section would raise a redundant/incorrect
    observation on top of that.
    """
    from sar_prod_v3.tool_sar import CheckTools

    kam = main_json.get("key_audit_matters") or {}
    if not kam.get("present"):
        return None
    return CheckTools.check_kam_auditor_response(kam.get("items") or [])


def _report_date_sequence_check(formal_checks: dict) -> "Any | None":
    """Runs CheckTools.check_report_date_sequence — only when both dates
    were extracted. Skipped (not AUDIT_POINTER'd) rather than run against
    blanks: the "could not parse" branch of the check itself already
    produces an AUDIT_POINTER for that case, and running it needlessly on
    two empty strings would raise a pointer that says nothing an empty
    `formal_checks.report_date` didn't already say."""
    from sar_prod_v3.tool_sar import CheckTools

    report_date = formal_checks.get("report_date") or ""
    fs_approval_date = formal_checks.get("fs_approval_date") or ""
    if not report_date or not fs_approval_date:
        return None
    return CheckTools.check_report_date_sequence(fs_approval_date, report_date)


def run_formal_checks(merged_json: dict) -> dict:
    """Runs every wired formal check once against the merged extractor JSON.

    Returns:
        {
          "observations": [Observation, ...],   # FAILED checks only (passed=False)
          "summary": {                            # every check's pass/fail, for the memo's
              "udin": {...},                       # 1.3 Opinion/Formal Summary table — see
              "eom_closing_sentence": {...} | None, # agent.build_writer_user_message's new
              "kam_auditor_response": {...} | None, # FORMAL CHECKS SUMMARY block.
              "report_date_sequence": {...} | None,
          },
        }
    """
    formal_checks = merged_json.get("formal_checks") or {}

    udin_results = _udin_checks(formal_checks)
    eom_result = _eom_closing_sentence_check(merged_json)
    kam_result = _kam_auditor_response_check(merged_json)
    date_result = _report_date_sequence_check(formal_checks)

    observations: list[Observation] = []
    summary: dict[str, Any] = {}

    udin_summary = []
    for cr in udin_results:
        udin_summary.append({"passed": cr.passed, "observation": cr.observation})
        if not cr.passed:
            observations.append(from_check_result(cr))
    summary["udin"] = {
        "all_valid": bool(udin_results) and all(r["passed"] for r in udin_summary),
        "checks": udin_summary,
    }

    if eom_result is not None:
        summary["eom_closing_sentence"] = {"passed": eom_result.passed, "observation": eom_result.observation}
        if not eom_result.passed:
            observations.append(from_check_result(eom_result))
    else:
        summary["eom_closing_sentence"] = None

    if kam_result is not None:
        summary["kam_auditor_response"] = {"passed": kam_result.passed, "observation": kam_result.observation}
        if not kam_result.passed:
            observations.append(from_check_result(kam_result))
    else:
        summary["kam_auditor_response"] = None

    if date_result is not None:
        summary["report_date_sequence"] = {"passed": date_result.passed, "observation": date_result.observation}
        if not date_result.passed:
            observations.append(from_check_result(date_result))
    else:
        summary["report_date_sequence"] = None

    return {"observations": observations, "summary": summary}


def compute_review_status(
    *,
    main_text_usable: bool,
    fs_tables: dict,
    formal_summary: dict,
) -> tuple[str, list[str]]:
    """Derives the package-level `review_status` (source spec §47).

    Returns (status, reasons) where status is one of:
        "blocked"     — a mandatory input is missing outright; substantive
                         checks should not be relied on.
        "provisional" — the package is analysable but report finality is
                         not confirmed (UDIN unresolved).
        "complete"    — no blocking or finality issue detected by the
                         checks this function currently covers.

    NOTE: "complete" here means "no issue among the ones Phase 1 checks
    for" — it is not yet a full §8 pre-analysis validation verdict (see
    this module's docstring for what's deferred to Phase 2: applicability
    gates, signed/final-state detection, scope-mismatch detection).
    """
    reasons: list[str] = []

    if not main_text_usable:
        reasons.append(
            "The main auditor's report text could not be retrieved from the supplied "
            "package — this is a mandatory input (source spec §5.1)."
        )

    missing_fs = [t for t in ("balance_sheet", "profit_loss") if not (fs_tables or {}).get(t)]
    if missing_fs:
        reasons.append(
            f"Mandatory financial statement table(s) not retrieved: {', '.join(missing_fs)} "
            f"(source spec §5.1)."
        )

    if reasons:
        return "blocked", reasons

    if not formal_summary.get("udin", {}).get("all_valid"):
        reasons.append(
            "UDIN is absent, illegible or does not conform to the ICAI 18-character format "
            "for at least one auditor — report finality is not confirmed (source spec §29.1)."
        )
        return "provisional", reasons

    return "complete", reasons
