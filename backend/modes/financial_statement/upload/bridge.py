"""Making the existing FS tool library read uploaded documents.

This is the load-bearing piece of the upload feature, and it is deliberately
small. ``tools_fs.py`` is 8,378 lines carrying 33 ratios, cross-statement
tie-outs, Schedule III compliance, CARO cross-checks, going concern, materiality
and multi-year trends -- essentially the whole check library that sections 6 to
13 of the client specification asks for. None of it needs rewriting for uploads.
All of it reaches company data through a handful of functions, and this module
puts an uploaded document in front of those.

**Why rebinding rather than editing.** ``pipeline/`` is vendored verbatim from
the Financial_Statement branch and the backend README says not to edit it, so
that it stays re-pullable. This module therefore uses the idiom this same mode
already established in ``entity_resolution.py``: verify the target functions
against an expected signature map, then replace them with wrappers. A re-pull
that changes a signature raises at install time and the mode reports itself
unavailable -- which is the point. Declining silently would leave every log line
claiming the feature had shipped while uploads quietly went nowhere.

**Chaining, not clobbering.** ``entity_resolution.install()`` has already
replaced ``DocumentResolver._resolve_document`` by the time this runs. Each
wrapper here captures whatever function is currently bound and delegates to it,
so the entity-resolution ladder keeps working for corpus documents and the two
overrides compose instead of racing.

**Uploads take precedence, and only within their own conversation.** When the
active request has uploaded documents in scope and one of them matches, it is
returned; otherwise every call falls straight through to Postgres unchanged.
A corpus question in a conversation with no uploads takes exactly the code path
it took before this feature existed.
"""

from __future__ import annotations

import inspect
import logging

from . import diagnostics, edits, narrative
from .store import UploadedDocument, current_scope

logger = logging.getLogger(__name__)


class BridgeContractError(RuntimeError):
    """The vendored pipeline is not the shape these wrappers were written for."""


#: Leading parameter names each target must still take. Checked at install.
_EXPECTED = {
    "ComplianceTools._find_statement_tables": ("doc_id", "statement_type", "conn_reports"),
    "ComplianceTools._fetch_untitled_continuations": ("doc_id", "after_table_id", "anchor_page", "conn_reports"),
    "UnitResolver.resolve": ("doc_id", "conn_reports"),
    "DocumentResolver._resolve_document": ("company", "financial_year", "conn"),
    "DocumentResolver.latest_fy_end": ("company", "conn"),
    "TrendAnalysisTools._resolve_reports": ("company", "financial_year", "conn_reports"),
    "ToolRegistry.build_tools": ("self", "conn", "initial_chunks", "conn_reports", "allowed"),
    # Narrative side. Same discipline as above: a re-pull that renames any of
    # these parameters must fail the install loudly rather than leave notes and
    # policies quietly unreachable on every upload.
    "DocumentResolver._fetch_following_chunks": ("doc_id", "anchor", "conn"),
    "DocumentResolver._find_compliance_passage": ("doc_id", "conn"),
    "DocumentResolver._format_anchored_passage": ("doc_header", "anchor", "following", "cite_instruction"),
    "DisclosureSearchTools._find_heading_anchor": ("doc_id", "vector_str", "conn_reports"),
    "DisclosureSearchTools.search_company_disclosures": ("company", "financial_year", "topic", "conn_reports"),
    "AccountingPolicyTools._find_heading_by_keywords": ("doc_id", "phrases", "conn_reports"),
    "AccountingPolicyTools._find_heading_by_embedding": ("doc_id", "query_text", "conn_reports"),
    "ReportReferenceTools.lookup_report_reference": ("company", "financial_year", "reference", "conn_reports"),
    "ExecutiveSummaryTools._narrative": ("doc_id", "conn_reports"),
    "AuditorReportTools._find_clause_evidence": ("doc_id", "patterns", "conn_reports"),
    "AuditorReportTools._window_around_match": ("body", "patterns"),
    "GoingConcernTools._auditor_flagged": ("doc_id", "patterns", "conn_reports"),
    "AuditRiskTools._match_note_tables": ("doc_id", "patterns", "conn_reports"),
    "AccountingPolicyTools.get_accounting_policy_note": ("company", "financial_year", "topic", "conn_reports"),
}

_installed = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uploaded(doc_id: str) -> UploadedDocument | None:
    """The uploaded document with this id, if the current request may see it."""
    scope = current_scope()
    return scope.by_id(doc_id) if scope else None


def _as_document_row(document: UploadedDocument) -> dict:
    """An uploaded document in the shape a ``public.documents`` row has.

    Callers read ``doc_id`` (18 sites), ``fy_end``, and the resolver's own
    formatter reads ``company``/``fy_start``/``doc_name``. ``doc_name`` is the
    filename because prompt rule 18 renders it into every citation, and the
    filename is what the user will recognise -- they uploaded it a minute ago.
    """
    return {
        "doc_id": document.doc_id,
        "company": document.company or document.filename,
        "fy_start": document.document.get("fy_start"),
        "fy_end": document.document.get("fy_end"),
        "doc_name": document.filename,
    }


def _document_header(upload) -> str:
    """The `Document: …` line every reports-DB tool prints above its output.

    Rebuilt here rather than reused because the branch builds it inline from a
    resolved `match` dict, and the upload path has the document itself.
    """
    row = _as_document_row(upload)
    fy = ""
    if row.get("fy_start") and row.get("fy_end"):
        fy = f", FY{row['fy_start']}-{str(row['fy_end'])[-2:]}"
    header = (
        f"Document: {row['doc_name']} (doc_id={row['doc_id']}, "
        f"company={row['company']}{fy})"
    )
    # `upload.ingestion_status == "partial"` means this is a snapshot the
    # ingestion service published WHILE STILL CONVERTING (see `router.py`'s
    # `/upload/{job_id}/events` partial branch and `UploadedDocument.
    # ingestion_status`'s own docstring) -- tables verified so far, but the
    # document is not finished. Every reports-DB tool prints this header, so
    # this is the one place that has to say so: a table this tool doesn't
    # find on a partial document may simply not have been converted YET, not
    # "the filing doesn't disclose it" -- the same honesty this pipeline
    # already insists on for an individual withheld figure (never silent),
    # carried into the chat layer for the document as a whole.
    if getattr(upload, "ingestion_status", "complete") == "partial":
        header += (
            f"\nNOTE: this document is STILL BEING INGESTED ({len(upload.tables)} "
            "table(s) extracted so far). A table or figure not found here may "
            "not have been converted yet -- it is not evidence the filing omits "
            "it. Say so plainly to the user rather than treating this as complete."
        )
    return header


def _table_text_with_edit_note(document: UploadedDocument, table: dict) -> str:
    """A table's `table_md`, plus a DATA QUALITY NOTE line naming any figure
    a user typed in after reading the scan (see `edits.py`).

    Both call sites that hand a table's markdown to the model route through
    here, so disclosure cannot be added at one site and forgotten at the
    other. The note is a plain sentence, not a `|` line, so it is inert to
    `parse_table_md` and safe to sit directly under the table -- and rule 17
    already requires the model to reproduce a DATA QUALITY NOTE it is shown.
    """
    table_md = table.get("table_md") or ""
    note = edits.user_edits_note(document.quality, document.doc_id, table.get("table_id") or "")
    return f"{table_md}\n{note}" if note else table_md


def _source_ref(source_ref_cls, table: dict) -> str:
    try:
        return source_ref_cls.table(
            table.get("table_id"), table.get("page_ocr_start"), table.get("table_title")
        )
    except Exception:
        return f"[table_id: {table.get('table_id')} | page {table.get('page_ocr_start')}]"


# ---------------------------------------------------------------------------
# Wrappers
# ---------------------------------------------------------------------------

def _wrap_find_statement_tables(original, source_ref_cls, statement_labels):
    def _find_statement_tables(doc_id, statement_type, conn_reports):
        document = _uploaded(doc_id)
        if document is None:
            return original(doc_id, statement_type, conn_reports)

        tables = document.financial_tables(statement_type)
        if not tables:
            # Phrased to match the branch's own "No standalone ..." wording,
            # because FinancialFactBase.load keys on that substring to decide a
            # statement was not found. A different sentence here would make it
            # treat the miss as a parsed-but-empty table instead.
            label = statement_labels.get(statement_type, statement_type)
            return (
                f"No standalone {label} table was found for this document."
            )

        parts = []
        for table in tables:
            parts.append(_source_ref(source_ref_cls, table) + "\n" + _table_text_with_edit_note(document, table))
        return "\n\n".join(parts)

    return _find_statement_tables


def _wrap_fetch_untitled_continuations(original):
    def _fetch_untitled_continuations(doc_id, after_table_id, anchor_page, conn_reports, limit=1):
        document = _uploaded(doc_id)
        if document is None:
            return original(doc_id, after_table_id, anchor_page, conn_reports, limit)
        if anchor_page is None:
            return []

        # Same rule the SQL applies: the next untitled table within two pages of
        # the anchor. A statement split across a page break arrives as a titled
        # block plus an untitled continuation, and without this the second half
        # of a balance sheet is simply missing.
        out = []
        for table in document.financial_tables():
            if str(table.get("table_id") or "") <= str(after_table_id or ""):
                continue
            if (table.get("table_title") or "").strip():
                continue
            page = table.get("page_ocr_start")
            if page is None or not (anchor_page <= page <= anchor_page + 2):
                continue
            out.append(table)
            if len(out) >= limit:
                break
        return out

    return _fetch_untitled_continuations


def _wrap_unit_resolver(original):
    def resolve(doc_id, conn_reports):
        document = _uploaded(doc_id)
        if document is None:
            return original(doc_id, conn_reports)

        # Majority vote over the document's own financial tables, matching what
        # the Postgres version does. Deliberately does NOT fall back to a
        # default: UnitResolver's own docstring is explicit that a guessed scale
        # is worse than an absent one, because the same digits mean different
        # things three orders of magnitude apart.
        scales: dict[str, int] = {}
        currencies: dict[str, int] = {}
        for table in document.tables:
            if not table.get("is_financial"):
                continue
            unit = (table.get("unit") or "").strip().lower()
            if unit and unit != "%":
                scales[unit] = scales.get(unit, 0) + 1
            currency = (table.get("currency") or "").strip().upper()
            if currency:
                currencies[currency] = currencies.get(currency, 0) + 1

        result = {"scale": None, "currency": None, "label": None,
                  "declared": False, "mixed": False}
        if scales:
            result["scale"] = max(scales, key=scales.get)
            result["mixed"] = len(scales) > 1
        if currencies:
            result["currency"] = max(currencies, key=currencies.get)

        if result["scale"] or result["currency"]:
            symbol = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}.get(
                result["currency"] or "", ""
            )
            parts = [p for p in (symbol or result["currency"] or "", result["scale"]) if p]
            result["label"] = " ".join(parts)
            result["declared"] = bool(result["label"])
        return result

    return resolve


def _wrap_resolve_document(original):
    def _resolve_document(company, financial_year, conn):
        scope = current_scope()
        if scope and scope.documents:
            matches = scope.match(company, financial_year)
            if len(matches) == 1:
                return _as_document_row(matches[0])
            if len(matches) > 1:
                # Same contract as the branch: a list means "ask the user".
                return [_as_document_row(d) for d in matches]
        # No uploads in scope, or none matched. A question about an entity the
        # user never uploaded belongs to the corpus, so fall through.
        return original(company, financial_year, conn)

    return _resolve_document


def _wrap_latest_fy_end(original):
    def latest_fy_end(company, conn):
        scope = current_scope()
        if scope and scope.documents:
            matches = scope.match(company)
            ends = [d.fy_end for d in matches if d.fy_end is not None]
            if ends:
                return max(ends)
        return original(company, conn)

    return latest_fy_end


def _wrap_resolve_reports(original):
    """Let a multi-year trend span every uploaded filing, not two of them.

    ``get_multi_year_trend`` is the ONLY tool that computes a cross-year
    difference or CAGR, and prompt rule 15 forbids the model doing that
    arithmetic itself. So when this function cannot assemble a series, a
    comparative question about uploaded documents is not merely awkward -- it
    is unanswerable, and the model's only honest move is to decline.

    Two things stopped it assembling one, both fixed here rather than in the
    vendored file:

    * With no year given, the original builds ``[f"{y}-{y+1}" for y in
      range(latest - 2, latest)]`` -- always exactly TWO labels, so a user who
      uploaded three filings got a two-year trend and no indication the third
      had been dropped.
    * Each label was then resolved separately, and any label matching more
      than one document abandoned the whole request with an "ask the user"
      message (``format_ambiguous``) rather than a series.

    An upload set is not the corpus: it is a handful of files the user chose
    deliberately, and every one of them for the named entity belongs in the
    trend. So this returns ALL of them, ascending by ``fy_end``, and leaves the
    tool's own line-item extraction and delta arithmetic untouched.

    The year argument is deliberately not used to narrow: a trend needs more
    than one period by definition, and narrowing to the single named year is
    what produced a one-point "trend". Entity scoping still applies -- a
    company with no uploads falls through to Postgres unchanged.
    """
    def _resolve_reports(company, financial_year, conn_reports):
        scope = current_scope()
        if not (scope and scope.documents):
            return original(company, financial_year, conn_reports)

        matches = [m for m in scope.match(company) if m.fy_end is not None]
        if not matches:
            # Uploads exist but none are this entity's. A question about a
            # company the user never uploaded belongs to the corpus.
            return original(company, financial_year, conn_reports)

        matches.sort(key=lambda m: m.fy_end)
        return [_as_document_row(m) for m in matches], None

    return _resolve_reports


def _wrap_build_tools(original):
    """Append the upload-only tools to whatever the registry already builds.

    Appending rather than editing ``ToolRegistry.build_tools`` keeps the
    vendored file untouched, and appending *after* the originals matters: the
    branch's own tools keep their positions, so a change in tool ordering cannot
    perturb the model's selection behaviour on corpus questions.

    The four extra tools appear only when the conversation actually has uploads
    -- ``upload_tools.build_tools()`` returns [] otherwise. Registering them
    unconditionally would teach the model that an upload surface exists in every
    conversation, and it would then call one, find nothing, and report the
    nothing as a finding.
    """
    def build_tools(self, conn, initial_chunks, conn_reports=None, allowed=None):
        tools = original(self, conn, initial_chunks, conn_reports, allowed)
        try:
            from . import tools as upload_tools
            extra = upload_tools.build_tools()
        except Exception:  # noqa: BLE001 - never take the agent down for this
            logger.exception("failed to build upload tools; continuing without them")
            return tools
        if extra:
            names = {getattr(t, "name", None) for t in tools}
            tools = tools + [t for t in extra if getattr(t, "name", None) not in names]
        return tools

    return build_tools




# ---------------------------------------------------------------------------
# Narrative wrappers
#
# Everything below serves NOTES, ACCOUNTING POLICIES and AUDITOR'S-REPORT PROSE
# for an uploaded document. The numeric wrappers above were not enough on their
# own: three of the ten skill playbooks are narrative-first, and two of them --
# Auditor's Report & CARO, and Going Concern -- rank prose ABOVE the computed
# figures by explicit instruction ("a Material Uncertainty Related to Going
# Concern section ... is a genuine and far stronger signal than any computed
# indicator"). Rule 13 makes calling the disclosure tools mandatory for any
# named-company policy question.
#
# HELPERS are wrapped rather than the public tools wherever possible, because
# the helpers are where the SQL is and the public tools are where the formatting
# and the caveats are. Bridging `_find_heading_anchor` + `_fetch_following_chunks`
# makes `search_company_disclosures` phase 1 and `get_reporting_framework` work
# with their own formatting intact; bridging `_match_note_tables` fixes three
# tools at once; bridging `_find_clause_evidence` fixes CARO and Rule 11(g)
# together.
# ---------------------------------------------------------------------------

def _multi_file(upload) -> bool:
    return len(getattr(upload, "members", []) or []) > 1


def _wrap_fetch_following_chunks(original):
    def _fetch_following_chunks(doc_id, anchor, conn, limit=4):
        upload = _uploaded(doc_id)
        if upload is None:
            return original(doc_id, anchor, conn, limit)
        rows = narrative.fetch_following_chunks(upload, anchor, limit)
        return narrative.decorate(rows, _multi_file(upload))

    return _fetch_following_chunks


def _wrap_find_compliance_passage(original, document_resolver, source_ref):
    def _find_compliance_passage(doc_id, conn):
        upload = _uploaded(doc_id)
        if upload is None:
            return original(doc_id, conn)

        anchor = narrative.find_compliance_passage(upload)
        if anchor is None:
            # Worded exactly as the branch words it: this string is what the
            # Framework playbook relays to the user, and `_clean_passage`
            # downstream strips the scaffolding around it.
            return (
                "No Statement of Compliance / Basis of Preparation passage was "
                "found under the Notes to the Standalone Financial Statements "
                "for this document."
            )

        multi = _multi_file(upload)
        anchor = narrative.decorate([anchor], multi)[0]
        following = narrative.decorate(
            narrative.fetch_following_chunks(upload, anchor), multi
        )
        header = _document_header(upload)
        return document_resolver._format_anchored_passage(
            header, anchor, following, source_ref.instruction()
        )

    return _find_compliance_passage


def _wrap_find_heading_anchor(original):
    def _find_heading_anchor(doc_id, vector_str, conn_reports):
        upload = _uploaded(doc_id)
        if upload is None:
            return original(doc_id, vector_str, conn_reports)
        vector = narrative.parse_vector(vector_str)
        return narrative.find_heading_anchor(upload, vector)

    return _find_heading_anchor


def _wrap_find_heading_by_keywords(original):
    def _find_heading_by_keywords(doc_id, phrases, conn_reports):
        upload = _uploaded(doc_id)
        if upload is None:
            return original(doc_id, phrases, conn_reports)
        return narrative.find_heading_by_keywords(upload, phrases)

    return _find_heading_by_keywords


def _wrap_find_heading_by_embedding(original):
    def _find_heading_by_embedding(doc_id, query_text, conn_reports):
        upload = _uploaded(doc_id)
        if upload is None:
            return original(doc_id, query_text, conn_reports)
        return narrative.find_heading_by_embedding(upload, query_text)

    return _find_heading_by_embedding


def _wrap_search_company_disclosures(original, document_resolver, disclosure_tools, source_ref):
    """Phase 2 only — phase 1 already works through the wrapped helpers.

    The original resolves the document, embeds the topic, tries a heading anchor
    (now served from memory) and returns an anchored passage. It only reaches its
    own SQL when no heading clears 0.55, and that is the branch replaced here.
    """
    def search_company_disclosures(company, financial_year, topic, conn_reports):
        scope = current_scope()
        if not (scope and scope.documents):
            return original(company, financial_year, topic, conn_reports)

        matches = scope.match(company, financial_year)
        if not matches:
            return original(company, financial_year, topic, conn_reports)
        if len(matches) > 1:
            return document_resolver.format_ambiguous(
                company, financial_year, [_as_document_row(m) for m in matches],
                ask="the exact company and financial year",
            )

        upload = matches[0]
        header = _document_header(upload)
        multi = _multi_file(upload)

        index = upload.ensure_index()
        # One collector for the whole lookup, always reset. The sieves inside
        # record into it and the empty-result branch drains it; leaking the
        # ContextVar token would carry one request's caveats into the next.
        collector_token = diagnostics.start()
        try:
            try:
                from tools_fs import Embedder  # type: ignore
                vector = Embedder.embed_text(topic)
            except Exception:
                vector = None

            anchor = narrative.find_heading_anchor(upload, vector, topic)
            if anchor:
                anchor = narrative.decorate([anchor], multi)[0]
                following = narrative.decorate(
                    narrative.fetch_following_chunks(upload, anchor), multi
                )
                return document_resolver._format_anchored_passage(
                    header, anchor, following, source_ref.instruction()
                )

            rows = narrative.decorate(
                narrative.semantic_body_search(upload, vector, topic), multi
            )
            if not rows:
                # Why it found nothing, not just that it did. Without this the
                # model cannot tell "the scan yielded no prose" from "the filing
                # does not discuss it", and rule 24 turns on that distinction.
                why = diagnostics.caveats()
                return (
                    f"{header}\nNo narrative disclosure chunks matching '{topic}' were "
                    f"found in this uploaded document."
                    + (f"\n{why}" if why else "")
                )

            parts = [header]
            if index.degraded:
                # Said in the output, not just logged. A keyword-ranked result is a
                # weaker answer to the same question, and the reader has to know
                # that before treating a miss as an absence of disclosure.
                parts.append(
                    "NOTE: the embedding service was unavailable, so these passages were "
                    "ranked by keyword overlap rather than meaning. Treat the ranking as "
                    "approximate, and do not read a low-ranked or missing topic as evidence "
                    "that the entity did not disclose it."
                )
            parts.append(source_ref.instruction())
            parts.append("")
            for row in rows:
                parts.append(
                    source_ref.chunk(
                        row.get("chunk_id"), row.get("page_ocr_start"),
                        row.get("section") or row.get("title"),
                        f"similarity: {row.get('similarity'):.3f}"
                        if row.get("similarity") is not None else None,
                    ) + " " + (row.get("content") or "")
                )
            return "\n".join(parts)
        finally:
            diagnostics.reset(collector_token)

    return search_company_disclosures


def _wrap_lookup_report_reference(original, report_tools, unit_resolver, source_ref):
    def lookup_report_reference(company, financial_year, reference, conn_reports):
        scope = current_scope()
        if not (scope and scope.documents):
            return original(company, financial_year, reference, conn_reports)
        matches = scope.match(company, financial_year)
        if len(matches) != 1:
            return original(company, financial_year, reference, conn_reports)

        upload = matches[0]
        multi = _multi_file(upload)
        prefix = report_tools._note_number_prefix(reference)

        tables = narrative.lookup_tables(upload, reference, prefix)
        texts = narrative.decorate(narrative.lookup_text(upload, reference, prefix), multi)

        header = _document_header(upload)
        try:
            units = unit_resolver.units_line(upload.doc_id, conn_reports)
        except Exception:
            units = ""

        if not tables and not texts:
            return (
                f"{header}\nNo data tables or narrative content matching "
                f"'{reference}' were found in this uploaded document."
            )

        parts = [header]
        if units:
            parts.append(units)
        parts.append(f"Reference lookup: '{reference}' (standalone only)")

        if tables:
            parts.append("")
            parts.append("--- Data tables ---")
            for table in tables:
                label = table.get("table_description") or table.get("table_title")
                if multi and table.get("source_file"):
                    label = f"{label} — {table['source_file']}" if label else table["source_file"]
                parts.append(
                    source_ref.table(table.get("table_id"), table.get("page_ocr_start"), label)
                )
                parts.append(_table_text_with_edit_note(upload, table))

        if texts:
            parts.append("")
            parts.append("--- Narrative / heading text ---")
            for row in texts:
                parts.append(
                    source_ref.chunk(
                        row.get("chunk_id"), row.get("page_ocr_start"),
                        row.get("section"), str(row.get("chunk_type")),
                    ) + " " + (row.get("content") or "")
                )
        return "\n".join(parts)

    return lookup_report_reference


def _wrap_narrative_summary(original):
    def _narrative(doc_id, conn_reports):
        upload = _uploaded(doc_id)
        if upload is None:
            return original(doc_id, conn_reports)
        # Deliberately empty. This feeds section 6 of the annual-report summary,
        # "the company's own business highlights", which the corpus serves from
        # pre-computed `section_summary` chunks. A standalone financial-statement
        # file has no MD&A to draw one from, and generating a "business
        # highlights" paragraph from the notes would be inventing management
        # narrative that the filing never contained. The tool omits the section
        # entirely when this returns "".
        return ""

    return _narrative


def _wrap_find_clause_evidence(original, auditor_tools):
    def _find_clause_evidence(doc_id, patterns, conn_reports):
        upload = _uploaded(doc_id)
        if upload is None:
            return original(doc_id, patterns, conn_reports)
        return narrative.find_clause_evidence(
            upload, patterns,
            list(getattr(auditor_tools, "_AUDITOR_VOICE_PATTERNS", []) or []),
            auditor_tools._window_around_match,
        )

    return _find_clause_evidence


def _wrap_auditor_flagged(original, auditor_tools):
    def _auditor_flagged(doc_id, patterns, conn_reports):
        upload = _uploaded(doc_id)
        if upload is None:
            return original(doc_id, patterns, conn_reports)
        return narrative.find_flagged(upload, patterns, auditor_tools._window_around_match)

    return _auditor_flagged


def _wrap_get_accounting_policy_note(original):
    """Wrapped only so a miss can say WHY it missed.

    Retrieval already works through the wrapped `_find_heading_by_*` helpers.
    But those return `dict | None`, and a `None` carries no reason -- so the
    tool's "no policy note was found" message reaches the model with nothing
    attached, and the model cannot tell a scan that yielded no numbered headings
    from a filing that bundles its policies without subheadings. The sieve
    recorded the difference; this is where it gets said.
    """
    def get_accounting_policy_note(company, financial_year, topic, conn_reports):
        scope = current_scope()
        if not (scope and scope.documents):
            return original(company, financial_year, topic, conn_reports)

        token = diagnostics.start()
        try:
            out = original(company, financial_year, topic, conn_reports)
            caveats = diagnostics.caveats()
        finally:
            diagnostics.reset(token)

        if caveats and isinstance(out, str):
            return f"{out}\n\n{caveats}"
        return out

    return get_accounting_policy_note


def _wrap_match_note_tables(original):
    def _match_note_tables(doc_id, patterns, conn_reports):
        upload = _uploaded(doc_id)
        if upload is None:
            return original(doc_id, patterns, conn_reports)
        return narrative.match_note_tables(upload, patterns)

    return _match_note_tables


# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------

def _verify(owner, attribute: str, expected: tuple[str, ...], label: str):
    fn = getattr(owner, attribute, None)
    if fn is None:
        raise BridgeContractError(
            f"{label} is missing. The vendored Financial_Statement pipeline has "
            f"changed shape; modes/financial_statement/upload/bridge.py must be "
            f"updated to match before it can be installed."
        )
    actual = tuple(inspect.signature(fn).parameters)
    if actual[: len(expected)] != expected:
        raise BridgeContractError(
            f"{label}{actual} no longer takes {expected}. "
            f"Update modes/financial_statement/upload/bridge.py."
        )
    return fn


def install(compliance_tools, unit_resolver, document_resolver, source_ref,
            tool_registry=None, narrative_targets=None) -> None:
    """Rebind the five functions every company-facing FS tool reads data through.

    Idempotent: installing twice would wrap the wrappers, and each layer would
    consult the same scope and then delegate to a copy of itself. Harmless in
    behaviour, but it makes a stack trace unreadable and doubles the work, so
    the second call is a no-op.
    """
    global _installed
    if _installed:
        return

    find = _verify(compliance_tools, "_find_statement_tables",
                   _EXPECTED["ComplianceTools._find_statement_tables"],
                   "ComplianceTools._find_statement_tables")
    continuations = _verify(compliance_tools, "_fetch_untitled_continuations",
                            _EXPECTED["ComplianceTools._fetch_untitled_continuations"],
                            "ComplianceTools._fetch_untitled_continuations")
    resolve = _verify(unit_resolver, "resolve",
                      _EXPECTED["UnitResolver.resolve"], "UnitResolver.resolve")
    resolve_document = _verify(document_resolver, "_resolve_document",
                               _EXPECTED["DocumentResolver._resolve_document"],
                               "DocumentResolver._resolve_document")
    latest = _verify(document_resolver, "latest_fy_end",
                     _EXPECTED["DocumentResolver.latest_fy_end"],
                     "DocumentResolver.latest_fy_end")

    build = None
    if tool_registry is not None:
        build = _verify(tool_registry, "build_tools",
                        _EXPECTED["ToolRegistry.build_tools"], "ToolRegistry.build_tools")

    labels = getattr(compliance_tools, "_STATEMENT_LABELS", {})

    compliance_tools._find_statement_tables = staticmethod(
        _wrap_find_statement_tables(find, source_ref, labels))
    compliance_tools._fetch_untitled_continuations = staticmethod(
        _wrap_fetch_untitled_continuations(continuations))
    unit_resolver.resolve = staticmethod(_wrap_unit_resolver(resolve))
    document_resolver._resolve_document = staticmethod(_wrap_resolve_document(resolve_document))
    document_resolver.latest_fy_end = staticmethod(_wrap_latest_fy_end(latest))
    if build is not None:
        tool_registry.build_tools = _wrap_build_tools(build)

    if narrative_targets:
        _install_narrative(narrative_targets, document_resolver, unit_resolver, source_ref)

    _installed = True
    logger.info(
        "FS upload bridge installed: statement tables, table continuations, "
        "units, document resolution, multi-year window, notes, accounting "
        "policies, auditor-report prose and note-level schedules now all serve "
        "uploaded documents when one is in scope, and fall through to the "
        "reports DB otherwise."
    )


def _install_narrative(t, document_resolver, unit_resolver, source_ref) -> None:
    """Rebind the nine narrative readers.

    `t` is a mapping of class name -> class, supplied by the adapter so this
    module needs no import of the vendored pipeline.
    """
    disclosure = t["DisclosureSearchTools"]
    policy = t["AccountingPolicyTools"]
    report_ref = t["ReportReferenceTools"]
    summary = t["ExecutiveSummaryTools"]
    auditor = t["AuditorReportTools"]
    going_concern = t["GoingConcernTools"]
    audit_risk = t["AuditRiskTools"]

    trend = t.get("TrendAnalysisTools")

    for owner, attribute, label in (
        (document_resolver, "_fetch_following_chunks", "DocumentResolver._fetch_following_chunks"),
        (document_resolver, "_find_compliance_passage", "DocumentResolver._find_compliance_passage"),
        (document_resolver, "_format_anchored_passage", "DocumentResolver._format_anchored_passage"),
        (disclosure, "_find_heading_anchor", "DisclosureSearchTools._find_heading_anchor"),
        (disclosure, "search_company_disclosures", "DisclosureSearchTools.search_company_disclosures"),
        (policy, "_find_heading_by_keywords", "AccountingPolicyTools._find_heading_by_keywords"),
        (policy, "_find_heading_by_embedding", "AccountingPolicyTools._find_heading_by_embedding"),
        (report_ref, "lookup_report_reference", "ReportReferenceTools.lookup_report_reference"),
        (summary, "_narrative", "ExecutiveSummaryTools._narrative"),
        (auditor, "_find_clause_evidence", "AuditorReportTools._find_clause_evidence"),
        (auditor, "_window_around_match", "AuditorReportTools._window_around_match"),
        (going_concern, "_auditor_flagged", "GoingConcernTools._auditor_flagged"),
        (audit_risk, "_match_note_tables", "AuditRiskTools._match_note_tables"),
        (policy, "get_accounting_policy_note", "AccountingPolicyTools.get_accounting_policy_note"),
    ):
        _verify(owner, attribute, _EXPECTED[label], label)

    # _format_anchored_passage and _window_around_match are VERIFIED but not
    # wrapped -- the upload path calls them to build its output, so their shape
    # matters even though their behaviour is unchanged.
    document_resolver._fetch_following_chunks = staticmethod(
        _wrap_fetch_following_chunks(document_resolver._fetch_following_chunks))
    document_resolver._find_compliance_passage = staticmethod(
        _wrap_find_compliance_passage(
            document_resolver._find_compliance_passage, document_resolver, source_ref))
    disclosure._find_heading_anchor = staticmethod(
        _wrap_find_heading_anchor(disclosure._find_heading_anchor))
    disclosure.search_company_disclosures = staticmethod(
        _wrap_search_company_disclosures(
            disclosure.search_company_disclosures, document_resolver, disclosure, source_ref))
    policy._find_heading_by_keywords = staticmethod(
        _wrap_find_heading_by_keywords(policy._find_heading_by_keywords))
    policy._find_heading_by_embedding = staticmethod(
        _wrap_find_heading_by_embedding(policy._find_heading_by_embedding))
    report_ref.lookup_report_reference = staticmethod(
        _wrap_lookup_report_reference(
            report_ref.lookup_report_reference, report_ref, unit_resolver, source_ref))
    summary._narrative = staticmethod(_wrap_narrative_summary(summary._narrative))
    auditor._find_clause_evidence = staticmethod(
        _wrap_find_clause_evidence(auditor._find_clause_evidence, auditor))
    going_concern._auditor_flagged = staticmethod(
        _wrap_auditor_flagged(going_concern._auditor_flagged, auditor))
    audit_risk._match_note_tables = staticmethod(
        _wrap_match_note_tables(audit_risk._match_note_tables))
    policy.get_accounting_policy_note = staticmethod(
        _wrap_get_accounting_policy_note(policy.get_accounting_policy_note))

    # Optional so an adapter that has not been updated to pass it still
    # installs -- but verified the moment it IS passed, on the same contract
    # as everything above.
    if trend is not None:
        _verify(trend, "_resolve_reports",
                _EXPECTED["TrendAnalysisTools._resolve_reports"],
                "TrendAnalysisTools._resolve_reports")
        trend._resolve_reports = staticmethod(
            _wrap_resolve_reports(trend._resolve_reports))
