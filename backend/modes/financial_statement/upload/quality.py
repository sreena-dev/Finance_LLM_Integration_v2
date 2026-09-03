"""Rendering the extraction quality report for the model and for the UI.

Spec section 4.3 requires the package to be checked before analysis and the
failures to be *named*; section 15.3 requires an unreadable amount to be
classified as an extraction issue rather than a financial one. This module turns
the ingestion service's measurements into text that makes the model behave that
way, leaning on rules the FS prompt already enforces:

* Rule 16 -- a retrieval miss is not a finding. A page the OCR could not read is
  a coverage limitation, and saying so explicitly is what stops the model
  reporting an unreadable disclosure as an absent one.
* Rule 17 -- reproduce tool caveats. Every caveat emitted here is required to
  survive into the answer, which is why they are written as sentences an auditor
  can read rather than as codes.

The unreadable-cell list is deliberately printed in full rather than summarised.
A count tells the model that something was withheld; the list tells it *which
figures* it must not reason about, and that is the difference between a caveat
and a control.
"""

from __future__ import annotations

from typing import Any

from . import coverage as coverage_mod
from .store import UploadedDocument

#: Above this many withheld cells the list is truncated -- past a point it stops
#: informing the model and starts crowding out the statements themselves.
_MAX_LISTED_CELLS = 40


def _grade_sentence(grade: str, low_grade: str) -> str:
    if grade in ("excellent", "good") and low_grade in ("excellent", "good"):
        return "Scan quality is good across the document."
    if grade in ("excellent", "good"):
        return (
            f"Scan quality is {grade} overall, but the weakest page grades "
            f"{low_grade}. Figures taken from that page deserve more scrutiny."
        )
    return (
        f"Scan quality is {grade} overall and the weakest page grades {low_grade}. "
        "Treat every figure from this document as provisional until it is checked "
        "against the original."
    )


def quality_report(document: UploadedDocument) -> str:
    """The full extraction-quality report, as the tool returns it."""
    quality = document.quality or {}
    identification = document.identification or {}
    lines: list[str] = []

    lines.append(f"EXTRACTION QUALITY — {document.filename} (doc_id={document.doc_id})")
    lines.append("")

    # ---- what the document is -------------------------------------------
    year = identification.get("financial_year")
    confidence = identification.get("fy_confidence", "low")
    if year:
        lines.append(
            f"Financial year: {year} (inferred, confidence {confidence}). "
            "This was read from the statements themselves, not from the file name."
        )
        for evidence in (identification.get("fy_evidence") or [])[:3]:
            lines.append(f"  - {evidence}")
    else:
        lines.append(
            "Financial year: COULD NOT BE DETERMINED from the document. Ask the "
            "user which year this filing covers before comparing it with another."
        )

    entity = identification.get("entity_name")
    lines.append(f"Entity: {entity or 'could not be read from the document'}")

    framework = identification.get("framework")
    if framework:
        division = identification.get("framework_division")
        lines.append(
            f"Framework: {framework}"
            + (f", Schedule III Division {division}" if division else "")
            + f" (inferred, confidence {identification.get('framework_confidence', 'low')})."
        )
        for signal in (identification.get("framework_signals") or [])[:4]:
            lines.append(f"  - {signal}")
    else:
        lines.append(
            "Framework: COULD NOT BE DETERMINED. Run only framework-neutral "
            "checks and ask the user to confirm the reporting framework before "
            "testing presentation or disclosure."
        )

    for conflict in identification.get("unresolved_conflicts") or []:
        lines.append(f"  ! {conflict}")

    # ---- scan quality ----------------------------------------------------
    lines.append("")
    lines.append(_grade_sentence(quality.get("grade", "unspecified"),
                                 quality.get("low_grade", "unspecified")))

    pages = quality.get("pages") or []
    problem_pages = [p for p in pages if p.get("grade") in ("poor", "fair")]
    if problem_pages:
        lines.append("")
        lines.append("Pages needing attention:")
        for page in problem_pages[:15]:
            defects = "; ".join(
                d.get("detail", "") for d in (page.get("defects") or [])
                if d.get("code") not in ("preprocessed", "modest_resolution")
            )
            if not defects:
                continue
            lines.append(f"  - page {page.get('page_no')} ({page.get('grade')}): {defects}")

    for note in quality.get("notes") or []:
        lines.append(f"  * {note}")

    if not quality.get("vlm_used"):
        lines.append(
            "  * No second independent read was available for this document, so "
            "each figure rests on one reader plus its own arithmetic."
        )

    # ---- withheld figures ------------------------------------------------
    unreadable = quality.get("unreadable_cells") or []
    lines.append("")
    if not unreadable:
        lines.append(
            "No figures were withheld: every extracted amount was either read "
            "cleanly or confirmed by the arithmetic of the column it sits in."
        )
    else:
        lines.append(
            f"{len(unreadable)} figure(s) COULD NOT BE READ RELIABLY and have been "
            "withheld. They appear in the tables as [unreadable: ...] markers. "
            "Do not quote, infer, interpolate or reason about these values, and "
            "do not treat a withheld figure as a nil balance or as a disclosure "
            "failure by the entity — it is an extraction limitation:"
        )
        for cell in unreadable[:_MAX_LISTED_CELLS]:
            reasons = ", ".join(cell.get("reasons") or []) or "unclear"
            lines.append(
                f"  - page {cell.get('page_no')}, table {cell.get('table_id')}, "
                f"row \"{cell.get('row_label')}\", column \"{cell.get('column')}\" "
                f"(printed as \"{cell.get('raw')}\"; {reasons})"
            )
        if len(unreadable) > _MAX_LISTED_CELLS:
            lines.append(f"  - ... and {len(unreadable) - _MAX_LISTED_CELLS} more.")

    # ---- arithmetic ------------------------------------------------------
    failed = quality.get("failed_footings") or []
    lines.append("")
    if failed:
        lines.append(
            f"{len(failed)} printed total(s) did not add up to their own components. "
            "Each is either an extraction error or a genuine arithmetic problem in "
            "the filing, and the two cannot be told apart without the original. "
            "Report these as extraction issues requiring verification, not as "
            "established findings against the entity:"
        )
        for check in failed[:20]:
            printed = check.get("printed")
            recomputed = check.get("recomputed")
            detail = f"printed {printed:,.2f}" if isinstance(printed, (int, float)) else "printed —"
            if isinstance(recomputed, (int, float)):
                detail += f", components sum to {recomputed:,.2f}"
            else:
                detail += ", components could not be summed"
            lines.append(
                f"  - page {check.get('page_no')}, \"{check.get('subtotal_label')}\": {detail}"
            )
    else:
        lines.append(
            "Every printed subtotal and total that could be tested added up to its "
            "own components."
        )

    # ---- what this document can answer -----------------------------------
    #
    # Last, because it answers a different question from everything above it:
    # not "is this figure trustworthy" but "will a lookup that returns nothing
    # be telling me anything at all". Spec 4.3 asks for exactly this -- check
    # the inputs before analysis and restrict the applicable checks when one
    # fails -- and it is the difference between an auditor reading a silent miss
    # as an omission by the entity and reading it as a limit of the scan.
    coverage_report = coverage_mod.assess(document).report()
    if coverage_report:
        lines.append("")
        lines.append(coverage_report)

    return "\n".join(lines)


def document_list(documents: list[UploadedDocument]) -> str:
    """The uploaded-document inventory, as the list tool returns it."""
    if not documents:
        return (
            "No documents have been uploaded in this conversation. Answer from the "
            "corpus if the user named an entity, or ask them to upload the "
            "financial statements they want reviewed."
        )

    lines = [f"{len(documents)} document(s) uploaded in this conversation:", ""]
    for document in documents:
        summary = document.summary()
        year = summary["financial_year"] or "year unknown"
        confidence = summary["fy_confidence"] or "low"
        lines.append(
            f"- {summary['filename']} (doc_id={summary['doc_id']})\n"
            f"    entity: {summary['company'] or 'not read from the document'}\n"
            f"    financial year: {year} (inferred, confidence {confidence})\n"
            f"    framework: {summary['framework'] or 'not determined'}"
            + (f" Division {summary['framework_division']}" if summary.get("framework_division") else "")
            + f"\n    {summary['pages']} page(s), {summary['tables']} table(s), "
              f"scan quality {summary['grade']} (weakest page {summary['low_grade']})\n"
            f"    {summary['unreadable_cells']} figure(s) withheld as unreadable, "
            f"{summary['failed_footings']} total(s) did not foot"
        )
    lines.append("")
    lines.append(
        "These are the ONLY documents in scope for this conversation. Any figure "
        "you quote about them must come from them, and a comparison across years "
        "may only use documents listed above."
    )
    return "\n".join(lines)


def as_payload(document: UploadedDocument) -> dict[str, Any]:
    """The JSON the UI renders as its quality panel."""
    return {
        **document.summary(),
        "identification": document.identification,
        "quality": document.quality,
        "coverage": coverage_mod.assess(document).as_dict(),
    }
