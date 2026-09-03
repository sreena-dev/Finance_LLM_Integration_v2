"""The four tools that exist only when a document has been uploaded.

Everything else an uploaded document needs is already covered: ratios,
tie-outs, Schedule III compliance, CARO, going concern, trends and account-area
review all reach it through ``bridge.install()`` without knowing it is not in
Postgres. These four cover what the corpus tools have no equivalent for, because
a corpus document has no extraction quality to report and no user-set threshold
to honour.

They are registered only when the conversation actually has uploads. Registering
them unconditionally would teach the model that an upload surface exists in
every conversation, and the measured consequence of that in this codebase is
the model calling a tool to discover nothing is there and then reporting the
nothing as a finding -- which is exactly what prompt rule 16 exists to stop.
"""

from __future__ import annotations

import logging

from . import materiality as materiality_mod
from . import quality as quality_mod
from .store import current_scope

logger = logging.getLogger(__name__)


def _scope_or_message():
    scope = current_scope()
    if scope is None or not scope.documents:
        return None, (
            "No documents have been uploaded in this conversation, so there is "
            "nothing to report on. This says nothing about whether data exists "
            "in the corpus — ask about a company by name for that."
        )
    return scope, None


def list_uploaded_documents() -> str:
    scope, message = _scope_or_message()
    if scope is None:
        return message
    return quality_mod.document_list(scope.documents)


def get_extraction_quality_report(doc_id: str = "") -> str:
    """Quality for one uploaded document, or all of them."""
    scope, message = _scope_or_message()
    if scope is None:
        return message

    if doc_id:
        document = scope.by_id(doc_id)
        if document is None:
            known = ", ".join(d.doc_id for d in scope.documents)
            return (
                f"No uploaded document with doc_id '{doc_id}' in this "
                f"conversation. Uploaded documents are: {known}."
            )
        return quality_mod.quality_report(document)

    return "\n\n".join(quality_mod.quality_report(d) for d in scope.documents)


def set_materiality_threshold(amount: str = "", basis: str = "") -> str:
    """Record the audit team's own materiality, overriding the provisional band."""
    scope, message = _scope_or_message()
    if scope is None:
        return message

    parsed = materiality_mod.parse_amount(amount)
    if parsed is None:
        return (
            f"Could not read '{amount}' as a materiality amount. Give a figure "
            "such as '2.5 crore', '50 lakh' or '2500000', optionally with the "
            "basis it was derived from."
        )

    threshold = materiality_mod.Materiality(
        amount=parsed,
        basis=basis or "supplied by the audit team",
        unit_label=None,
        user_supplied=True,
        reason=basis or None,
    )
    materiality_mod.REGISTRY.set(scope.user_id, scope.conversation_id, threshold)
    return (
        f"Materiality set to {parsed:,.2f} for this conversation"
        + (f", on the basis: {basis}." if basis else ".")
        + " This overrides any provisional band. Every risk flag and rating from "
        "here on must state this threshold in its legend, and must still raise "
        "matters that are material by nature or by context regardless of it."
        + materiality_mod.legend(threshold)
    )


def compare_uploaded_years(metric: str = "") -> str:
    """Line up the uploaded documents by financial year.

    Comparison is restricted to uploaded documents. Reaching into the corpus for
    a missing year would silently mix two sources in one trend, and a reader
    cannot tell from a percentage which of its two endpoints came from the file
    they just supplied.
    """
    scope, message = _scope_or_message()
    if scope is None:
        return message

    dated = [d for d in scope.documents if d.financial_year]
    undated = [d for d in scope.documents if not d.financial_year]

    if len(dated) < 2:
        lines = [
            "At least two uploaded documents with a readable financial year are "
            "needed for a comparison."
        ]
        if dated:
            lines.append(
                f"Only one has a year: {dated[0].filename} ({dated[0].financial_year})."
            )
        if undated:
            lines.append(
                "These uploads have no readable year, so they cannot be placed on "
                "a timeline: " + ", ".join(d.filename for d in undated)
                + ". Ask the user which year each covers."
            )
        return "\n".join(lines)

    dated.sort(key=lambda d: d.financial_year or "")
    lines = [
        f"{len(dated)} uploaded document(s) line up by financial year"
        + (f" for '{metric}'" if metric else "") + ":",
        "",
    ]
    for document in dated:
        summary = document.summary()
        lines.append(
            f"- {document.financial_year}: {document.filename} "
            f"(doc_id={document.doc_id}, entity {summary['company'] or 'unread'}, "
            f"scan quality {summary['grade']}, "
            f"{summary['unreadable_cells']} figure(s) withheld)"
        )

    entities = {(d.company or "").strip().lower() for d in dated if d.company}
    if len(entities) > 1:
        lines.append("")
        lines.append(
            "! These documents name DIFFERENT entities: "
            + ", ".join(sorted(d.company for d in dated if d.company))
            + ". Do not present a trend across them without confirming with the "
            "user that they are the same reporting entity."
        )

    years = [d.financial_year for d in dated]
    if len(set(years)) != len(years):
        lines.append("")
        lines.append(
            "! More than one upload resolves to the same financial year. Confirm "
            "which is the filing to use before comparing — one may be a restated "
            "or superseded set."
        )

    scales = {
        (d.identification.get("framework") or "") for d in dated
    }
    if len(scales) > 1:
        lines.append("")
        lines.append(
            "! These documents were prepared under different reporting frameworks "
            f"({', '.join(sorted(s or 'undetermined' for s in scales))}). "
            "Comparatives across a framework change need the restatement note "
            "before a movement means anything."
        )

    lines.append("")
    lines.append(
        "Now call the ordinary company tools once per document — they read "
        "uploaded documents exactly as they read filed reports — and quote each "
        "figure with the year and document it came from. Use ONLY the documents "
        "listed above; do not fill a missing year from the corpus."
    )
    if undated:
        lines.append(
            "Not included (no readable year): "
            + ", ".join(d.filename for d in undated) + "."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

#: (name, description, parameters) for each tool. Descriptions are written as
#: single-line triggers to match the style the FS prompt's TOOLS section uses.
SPECS = [
    (
        "list_uploaded_documents",
        "List the financial-statement files the user uploaded in THIS conversation, "
        "with the entity, financial year, framework and scan quality read from each. "
        "Call this FIRST whenever the user says 'the uploaded', 'this document', "
        "'the file I sent' or asks to compare years, so you know what is in scope.",
        [],
    ),
    (
        "get_extraction_quality_report",
        "Report how reliably an uploaded document was read: scan quality per page, "
        "which figures could NOT be read and were withheld, and which printed totals "
        "did not add up. Call this before reporting any finding from an uploaded "
        "document, and whenever the user asks whether the extraction is trustworthy.",
        [("doc_id", "string", "The uploaded document's doc_id. Omit for all of them.", False)],
    ),
    (
        "set_materiality_threshold",
        "Record the audit team's own materiality for this conversation, overriding "
        "the provisional band. Call this whenever the user states a threshold "
        "('use 2 crore as materiality', 'our materiality is 50 lakh').",
        [
            ("amount", "string", "The threshold as the user gave it, e.g. '2.5 crore', '50 lakh', '2500000'.", True),
            ("basis", "string", "How the team derived it, if they said, e.g. '0.5% of total assets'.", False),
        ],
    ),
    (
        "compare_uploaded_years",
        "Line up the uploaded documents by financial year before a multi-year "
        "comparison, and flag mismatched entities, duplicate years or a framework "
        "change. Call this before answering any question comparing periods across "
        "uploaded files.",
        [("metric", "string", "What is being compared, e.g. 'revenue', 'total assets'. Optional.", False)],
    ),
]

_HANDLERS = {
    "list_uploaded_documents": list_uploaded_documents,
    "get_extraction_quality_report": get_extraction_quality_report,
    "set_materiality_threshold": set_materiality_threshold,
    "compare_uploaded_years": compare_uploaded_years,
}


def build_tools() -> list:
    """Build the yukta Tool objects, or return [] if uploads are out of scope.

    Returns an empty list rather than raising when ``yukta`` is missing, so a
    gateway without it degrades to the same state it is already in for every
    other agent-backed feature.
    """
    scope = current_scope()
    if scope is None or not scope.documents:
        return []

    try:
        from yukta import Tool, ToolParameter, ToolType
    except ImportError:
        logger.warning("yukta is not installed; upload tools will not be registered")
        return []

    tools = []
    for name, description, parameters in SPECS:
        tools.append(Tool(
            name=name,
            description=description,
            parameters=[
                ToolParameter(name=p, type=t, description=d, required=r)
                for p, t, d, r in parameters
            ],
            # CUSTOM, matching every one of the 24 tools ToolRegistry already
            # registers. It is the only ToolType this pipeline uses.
            tool_type=ToolType.CUSTOM,
            function=_HANDLERS[name],
        ))
    return tools
