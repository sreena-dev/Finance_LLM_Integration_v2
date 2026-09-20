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

Some withheld cells are RECOVERED rather than left blank: a second, independent
vision read of the same image may supply a figure the primary extraction could
not. Two outcomes, both reported, and they must never be conflated: a recovered
figure that the column's own arithmetic then CONFIRMS is promoted to a plain
number and is no longer withheld at all -- it does not appear in the list
below. A recovered figure the arithmetic did NOT confirm stays withheld, still
appears as a `[recovered ...]` marker carrying a confidence band, and is exactly
as un-quotable-as-a-figure as a plain `[unreadable: ...]` cell -- the only
difference is that a human can now see what the page appears to say.
"""

from __future__ import annotations

from typing import Any

from . import coverage as coverage_mod
from . import periods as periods_mod
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

    # What the DATA covers, which is not the same question as what the filing
    # is FOR -- a filing for 2023-24 prints 2022-23 beside it in every table.
    # Reporting only the identified year is what led the model to refuse a
    # year-on-year question whose second figure was in the next column.
    lines.append("")
    lines.append(periods_mod.report(document))

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
    recovered_all = quality.get("recovered_cells") or []
    promoted = [r for r in recovered_all if r.get("promoted")]
    display_only = [r for r in recovered_all if not r.get("promoted")]

    user_edits = [e for e in (quality.get("user_edits") or []) if e.get("active", True)]

    lines.append("")
    if not unreadable:
        if promoted:
            lines.append(
                f"No figures remain withheld. {len(promoted)} figure(s) the primary "
                "extraction could not read were recovered by a second, independent "
                "vision read of the same image AND confirmed by the arithmetic of the "
                "column they sit in — they appear in the tables as ordinary numbers "
                "and may be quoted exactly like a cleanly-read figure."
            )
        elif user_edits:
            lines.append(
                "No figures remain withheld: the last one(s) the extraction could not "
                "read were entered by the user after reading the scan (see below) — "
                "every other extracted amount was read cleanly or confirmed by the "
                "arithmetic of the column it sits in."
            )
        else:
            lines.append(
                "No figures were withheld: every extracted amount was either read "
                "cleanly or confirmed by the arithmetic of the column it sits in."
            )
    else:
        lines.append(
            f"{len(unreadable)} figure(s) COULD NOT BE READ RELIABLY by the primary "
            "extraction and have been withheld. Do not treat a withheld figure as a "
            "nil balance or as a disclosure failure by the entity — it is an "
            "extraction limitation, not a financial one."
        )
        if promoted:
            lines.append(
                f"{len(promoted)} OTHER figure(s) were also recovered by a second read, "
                "but THOSE were confirmed by the column's own arithmetic and appear in "
                "the tables as ordinary, quotable numbers — they are not withheld and "
                "are not in the list below."
            )
        if display_only:
            lines.append(
                f"{len(display_only)} of the figures still withheld below were recovered "
                "by a second, independent vision read of the same image, but the "
                "column's own arithmetic did NOT confirm them. They appear as "
                "`[recovered ...]` markers carrying the second reader's figure and a "
                "confidence band (high/medium/low — never high here, since an "
                "arithmetic-confirmed figure is promoted and is not one of these). "
                "Quote a `[recovered ...]` figure only together with its caveat and "
                "confidence band, and NEVER in a computation, NEVER as an established "
                "amount, and NEVER as the basis of a finding against the entity — "
                "nothing has corroborated it."
            )
        lines.append(
            "Figures still withheld (appear as `[unreadable: ...]` or "
            "`[recovered ...]` markers — do not quote, infer, interpolate or reason "
            "about any of these values beyond what a `[recovered ...]` marker itself "
            "shows):"
        )
        for cell in unreadable[:_MAX_LISTED_CELLS]:
            reasons = ", ".join(cell.get("reasons") or []) or "unclear"
            recovered_text = cell.get("recovered_text")
            confidence = cell.get("confidence")
            if recovered_text is not None:
                detail = f"recovered as \"{recovered_text}\", confidence {confidence}: {reasons}"
            else:
                detail = f"printed as \"{cell.get('raw')}\"; {reasons}"
            lines.append(
                f"  - page {cell.get('page_no')}, table {cell.get('table_id')}, "
                f"row \"{cell.get('row_label')}\", column \"{cell.get('column')}\" ({detail})"
            )
        if len(unreadable) > _MAX_LISTED_CELLS:
            lines.append(f"  - ... and {len(unreadable) - _MAX_LISTED_CELLS} more.")

    # ---- user-entered figures ---------------------------------------------
    #
    # A cell that was `[unreadable ...]` or `[recovered ...]` and is now a
    # plain, computable number ONLY because a person typed it after reading
    # the scan. `edits.apply` already removed it from `unreadable`/
    # `recovered_all` above, so it never appears twice — it is usable, but
    # every reader of this report must be told it rests on a human, not on
    # the extraction.
    if user_edits:
        lines.append("")
        lines.append(
            f"{len(user_edits)} figure(s) in this document were ENTERED BY THE USER "
            "after reading the scan directly — the extraction itself could not read "
            "them. They may be used in computation and quoted, but say so wherever "
            "you rely on one of them, and never present it as read from the filing:"
        )
        for edit in user_edits:
            footing = edit.get("footing") or {}
            verdict = footing.get("verdict")
            if verdict == "ties":
                foot_note = "this column's total ties with the figure included"
            elif verdict == "does_not_tie":
                foot_note = "not confirmed by this column's arithmetic (simple check)"
            else:
                foot_note = "not checked by arithmetic"
            lines.append(
                f"  - page {edit.get('page_no')}, table {edit.get('table')}, "
                f"row \"{edit.get('row_label')}\", column \"{edit.get('column')}\" = "
                f"{edit.get('value')} (previously {edit.get('original_state') or 'unreadable'}; "
                f"{foot_note})"
            )

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
    if user_edits:
        lines.append(
            "Footing results above were computed before user entries and are not "
            "re-run against them; see the advisory, column-only check reported "
            "alongside each entered figure above instead."
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
        years = periods_mod.years_available(document)
        # The filing's own year is what it is FOR; the period columns are what
        # it can be ASKED about. Reporting only the former is what made a
        # single filing look like a single year of data.
        if len(years) > 1:
            periods_line = (
                "    periods IN THE DATA: "
                + ", ".join(str(y) for y in years)
                + " (this file carries its own comparative — a year-on-year "
                  "movement needs no second upload)\n"
            )
        elif years:
            periods_line = f"    periods IN THE DATA: {years[0]} only\n"
        else:
            periods_line = (
                "    periods IN THE DATA: none could be read from the table "
                "headings — do not assume the coverage either way\n"
            )
        lines.append(
            f"- {summary['filename']} (doc_id={summary['doc_id']})\n"
            f"    entity: {summary['company'] or 'not read from the document'}\n"
            f"    financial year: {year} (inferred, confidence {confidence})\n"
            + periods_line
            + f"    framework: {summary['framework'] or 'not determined'}"
            + (f" Division {summary['framework_division']}" if summary.get("framework_division") else "")
            + f"\n    {summary['pages']} page(s), {summary['tables']} table(s), "
              f"scan quality {summary['grade']} (weakest page {summary['low_grade']})\n"
            f"    {summary['unreadable_cells']} figure(s) withheld as unreadable "
            f"({summary.get('recovered_cells', 0)} of those shown via a second-read "
            f"recovery, still not confirmed by arithmetic), "
            f"{summary['failed_footings']} total(s) did not foot"
            + (
                f", {summary['user_entered_cells']} figure(s) entered by the user"
                if summary.get("user_entered_cells")
                else ""
            )
        )
    lines.append("")
    sentence = periods_mod.periods_sentence(documents)
    if sentence:
        lines.append(sentence)
        lines.append("")
    lines.append(
        "These are the ONLY documents in scope for this conversation. Any figure "
        "you quote about them must come from them."
    )
    lines.append(
        "A comparison ACROSS documents may only use documents listed above. A "
        "comparison BETWEEN PERIODS does not require two documents: where a "
        "document lists more than one period above, both figures are in its own "
        "tables, side by side on the same row, and must be read from there. "
        "Never tell the user an earlier year was not provided without checking "
        "the period list above first."
    )
    return "\n".join(lines)


def as_payload(document: UploadedDocument) -> dict[str, Any]:
    """The JSON the UI renders as its quality panel."""
    return {
        **document.summary(),
        "identification": document.identification,
        "quality": document.quality,
        "coverage": coverage_mod.assess(document).as_dict(),
        # The periods the DATA carries, which is not the document's own
        # financial year -- shown so a reader can see for themselves that a
        # single filing answers a two-year question, rather than having to
        # take the model's word for what is in scope.
        "periods": [
            {
                "label": entry.period.label,
                "year": entry.period.year,
                "kind": entry.period.kind,
                "is_comparative": entry.period.is_comparative,
                "tables": entry.tables,
            }
            for entry in periods_mod.coverage(document)
        ],
    }
