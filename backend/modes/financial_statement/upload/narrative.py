"""In-memory equivalents of the corpus narrative queries.

Each function here mirrors one SQL statement in ``tools_fs.py``,
predicate-for-predicate, so the two can be read side by side. That is deliberate:
these are not "roughly equivalent" reimplementations. The SQL encodes behaviour
that took measurement to arrive at, and dropping any of it silently changes what
an auditor is shown:

* **standalone before consolidated** is the primary sort key on every narrative
  query (``ORDER BY (breadcrumb ILIKE '%consolidated%') ASC, …``). Lose it and a
  consolidated note answers a standalone question.
* **``chunk_type IN ('text','list')``** is what keeps headings, tables of
  contents and summary rows out of a passage body.
* **``(page_ocr_start, chunk_id) > (anchor…)``** is document order. It is a
  tuple comparison, not two separate tests.
* **``breadcrumb ILIKE '%notes to%'`` plus ``content ~ '^[0-9]+\\.[0-9]+\\.?\\s'``**
  are what separate a level-2 accounting-policy sub-note from a level-1
  disclosure note. ``tools_fs`` is explicit that without both, "provisions" also
  matches "NOTE 27 PROVISIONS", a movement schedule rather than the policy text.
* **0.55** is the heading-anchor threshold, calibrated against a real document
  where the correct heading scored 0.68 and the next-best unrelated one 0.53.

Where a package holds more than one file, the source filename is appended to the
``section`` of each row. ``SourceRef`` renders ``section`` into every citation,
so this is what stops "page 12" being ambiguous once the auditor's report and the
statements are searched as one filing.
"""

from __future__ import annotations

import ast
import logging
import re

from . import diagnostics
from .sieve import PRECISION, SAFETY, Stage, first, sieve

logger = logging.getLogger(__name__)

#: Matches DisclosureSearchTools._HEADING_ANCHOR_THRESHOLD.
HEADING_ANCHOR_THRESHOLD = 0.55
#: Matches DisclosureSearchTools._RESULT_LIMIT.
RESULT_LIMIT = 8
FOLLOWING_LIMIT = 4

BODY_TYPES = ("text", "list")

#: AccountingPolicyTools._POLICY_SUBHEADING_PATTERN, verbatim.
POLICY_SUBHEADING_RE = re.compile(r"^[0-9]+\.[0-9]+\.?\s")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _crumb(row: dict) -> str:
    crumbs = row.get("section_breadcrumb") or []
    if isinstance(crumbs, str):
        return crumbs
    return " > ".join(str(c) for c in crumbs)


def _is_consolidated(row: dict) -> bool:
    """The standalone/consolidated sort key, as the SQL computes it."""
    return "consolidated" in _crumb(row).lower()


def _order_key(row: dict) -> tuple:
    """`(page_ocr_start, chunk_id)` — document order, as a tuple comparison."""
    return (row.get("page_ocr_start") or 0, str(row.get("chunk_id") or ""))


def _ilike(haystack: str | None, needle: str) -> bool:
    return needle.lower() in (haystack or "").lower()


#: "NOTE 27 PROVISIONS" -- a disclosure/movement schedule, not policy text.
#: This is what the N.M. numbering filter exists to exclude, so the RELAXED tier
#: has to keep excluding it or the relaxation reintroduces the very bug the
#: original filter prevented.
_SCHEDULE_HEADING_RE = re.compile(r"^\s*note\s*[-#]?\s*\d", re.I)


def _standalone_only(name: str = "standalone scope") -> Stage:
    """The one filter that never relaxes.

    Present in every tier of the corpus's own five-tier fallback, including the
    last resort. Relaxing it does not give a vaguer answer, it gives a wrong one:
    consolidated figures answering a standalone question.
    """
    return Stage(
        name=name,
        kind=SAFETY,
        tiers=[lambda r: not _is_consolidated(r)],
        why="standalone content only; consolidated figures answer a different question",
    )


def _body_chunks_stage() -> Stage:
    return Stage(
        name="passage body",
        kind=PRECISION,
        tiers=[
            lambda r: r.get("chunk_type") in BODY_TYPES,
            # If a document produced no text/list rows at all, anything that is
            # not a heading is still a better passage body than nothing.
            lambda r: r.get("chunk_type") != "heading",
        ],
        why="prose and lists make up a passage; headings are anchors, not body",
        tier_names=["text/list chunks", "any non-heading chunk"],
    )


def decorate(rows: list[dict], multi_file: bool) -> list[dict]:
    """Name the source file in the citation when a package spans several.

    Appended to ``section`` because that is the field ``SourceRef`` renders, and
    a reader given "page 12" across a merged auditor's report and statements
    otherwise cannot tell which document to open.
    """
    if not multi_file:
        return rows
    out = []
    for row in rows:
        source = row.get("source_file")
        if not source:
            out.append(row)
            continue
        copy = dict(row)
        section = copy.get("section")
        copy["section"] = f"{section} — {source}" if section else source
        out.append(copy)
    return out


def parse_vector(vector_str: str) -> list[float] | None:
    """Recover the list `str(query_vector)` was built from.

    ``search_company_disclosures`` embeds the topic, stringifies the vector for
    pgvector, then passes that string down. For an uploaded document there is no
    pgvector to hand it to, so it is parsed back rather than re-embedded — which
    would double the endpoint calls and could return a different vector.
    """
    try:
        value = ast.literal_eval(vector_str)
    except (ValueError, SyntaxError):
        return None
    if isinstance(value, (list, tuple)) and value and isinstance(value[0], (int, float)):
        return [float(v) for v in value]
    return None


# ---------------------------------------------------------------------------
# DocumentResolver
# ---------------------------------------------------------------------------

def fetch_following_chunks(upload, anchor: dict, limit: int = FOLLOWING_LIMIT) -> list[dict]:
    """`chunk_type IN ('text','list') AND (page,id) > anchor ORDER BY (page,id)`."""
    after = _order_key(anchor)
    result = sieve(upload.narrative(), [
        _body_chunks_stage(),
        Stage(
            name="after the anchor",
            kind=SAFETY,
            tiers=[lambda r: _order_key(r) > after],
            why="a passage body follows its heading; earlier text belongs to another note",
        ),
    ])
    diagnostics.record("passage body", result)
    return sorted(result.rows, key=_order_key)[:limit]


def find_compliance_passage(upload) -> dict | None:
    """The three sequential ILIKE passes of `_find_compliance_passage`.

    Order matters and is preserved: an explicit "Statement of Compliance"
    heading, then "Basis of Preparation", then any chunk asserting preparation
    in accordance with a named framework. The first is the entity's own
    framework declaration; the third is a last resort that may be a
    Director's-Responsibility boilerplate, which the playbook warns is not a
    compliance verification.
    """
    def framework_prose(row: dict) -> bool:
        content = (row.get("content") or "").lower()
        return (
            ("accordance with" in content and "accounting standard" in content)
            or "indian gaap" in content
            or "ifrs" in content
        )

    anchor, result = first(
        upload.narrative(),
        [
            _standalone_only(),
            Stage(
                name="compliance heading",
                kind=PRECISION,
                tiers=[
                    lambda r: r.get("chunk_type") == "heading"
                    and _ilike(r.get("content"), "statement of compliance"),
                    lambda r: r.get("chunk_type") == "heading"
                    and _ilike(r.get("content"), "basis of preparation"),
                    # Last resort, matching the corpus's third pass: any chunk
                    # asserting preparation under a named framework. This one may
                    # be a Director's Responsibility boilerplate, which playbook 2
                    # warns is not a compliance verification -- so it is the
                    # weakest tier, never the first.
                    framework_prose,
                ],
                why="the entity's own statement of the framework it reports under",
                tier_names=[
                    "a Statement of Compliance heading",
                    "a Basis of Preparation heading",
                    "any passage naming the reporting framework",
                ],
            ),
        ],
        key=_order_key,
    )
    diagnostics.record("compliance passage", result)
    return anchor


# ---------------------------------------------------------------------------
# DisclosureSearchTools
# ---------------------------------------------------------------------------

def find_heading_anchor(upload, query_vector: list[float] | None, query_text: str = "") -> dict | None:
    """Best-matching heading, standalone preferred, gated at 0.55."""
    headings = upload.narrative(("heading",))
    if not headings:
        return None

    index = upload.ensure_index()
    ranked = _rank(index, headings, query_vector, query_text)
    if not ranked:
        return None

    # Standalone first, then similarity — the SQL's two-key ORDER BY.
    ranked.sort(key=lambda p: (_is_consolidated(p[0]), -p[1]))
    best, score = ranked[0]
    if score < HEADING_ANCHOR_THRESHOLD:
        return None
    row = dict(best)
    row["similarity"] = round(score, 4)
    return row


def semantic_body_search(upload, query_vector: list[float] | None, query_text: str,
                         limit: int = RESULT_LIMIT) -> list[dict]:
    """Phase-2 fallback: rank `text`/`list` chunks by similarity."""
    bodies = upload.narrative(BODY_TYPES)
    if not bodies:
        return []
    ranked = _rank(upload.ensure_index(), bodies, query_vector, query_text)
    ranked.sort(key=lambda p: -p[1])
    out = []
    for row, score in ranked[:limit]:
        copy = dict(row)
        copy["similarity"] = round(score, 4)
        out.append(copy)
    return out


def _rank(index, rows: list[dict], query_vector: list[float] | None, query_text: str):
    """Score rows by cosine where possible, by token overlap where not."""
    from . import embeddings

    index.ensure(rows)
    if not index.degraded and query_vector and index.vectors:
        return [
            (r, embeddings.cosine(query_vector, index.vectors.get(r.get("chunk_id"), [])))
            for r in rows
        ]
    if not index.degraded and query_text and index.vectors:
        try:
            vector = embeddings.embed_one(query_text)
        except embeddings.EmbeddingUnavailable:
            index.degraded = True
        else:
            return [
                (r, embeddings.cosine(vector, index.vectors.get(r.get("chunk_id"), [])))
                for r in rows
            ]
    needle = query_text or ""
    return [(r, embeddings.keyword_score(needle, r.get("content") or "")) for r in rows]


# ---------------------------------------------------------------------------
# AccountingPolicyTools
# ---------------------------------------------------------------------------

def _policy_headings(upload) -> list[dict]:
    """Heading rows that are accounting-policy sub-notes.

    Both filters are load-bearing. `tools_fs` states the failure they prevent:
    without them "provisions" also matches "NOTE 27 PROVISIONS" -- a movement
    schedule, not the policy text -- and "revenue from operations" matches an
    MD&A heading.

    The ``notes to`` breadcrumb filter is applied **only if this document has
    such a breadcrumb at all**. On a scan that marker is frequently absent:
    measured on the OD-SPSU filing, docling recovers ``## 2.11 TAXATION:`` but
    emits no "Notes to the Standalone Financial Statements" heading on the page,
    because the marker sits on an earlier sheet or inside body text. Applying
    the filter unconditionally would then match nothing and
    ``get_accounting_policy_note`` would report no policy note for a document
    that plainly has one -- which rule 16 exists to stop the model reporting as
    a disclosure failure.

    Dropping the filter costs precision, not correctness: the ``N.M.`` numbering
    regex still separates a policy sub-note from a ``NOTE 27`` schedule, which
    is the distinction that actually matters.
    """
    result = sieve(upload.narrative(("heading",)), [
        _standalone_only(),
        Stage(
            name="notes section",
            kind=PRECISION,
            tiers=[lambda r: _ilike(_crumb(r), "notes to")],
            why="policy notes live under the Notes to the Financial Statements",
            tier_names=["breadcrumb naming the notes section"],
        ),
        Stage(
            name="policy numbering",
            kind=PRECISION,
            tiers=[
                lambda r: bool(POLICY_SUBHEADING_RE.match((r.get("content") or "").strip())),
                # Weaker, but still excludes "NOTE 27 PROVISIONS" -- the movement
                # schedule this filter exists to keep out. Relaxing all the way to
                # "any heading" would hand back a table of numbers where the
                # policy text was asked for.
                lambda r: not _SCHEDULE_HEADING_RE.match((r.get("content") or "").strip()),
            ],
            why="an accounting-policy sub-note is numbered N.M, unlike a NOTE n schedule",
            tier_names=["N.M numbered heading", "any heading that is not a NOTE n schedule"],
            # The weakest tier is still binding. Skipping this stage when both
            # tiers match nothing would hand back "NOTE 27 PROVISIONS" -- a
            # movement schedule -- as an accounting policy, which is precisely
            # what the filter exists to prevent.
            floor=True,
        ),
    ])
    diagnostics.record("policy note lookup", result)
    return sorted(result.rows, key=lambda r: (_is_consolidated(r), _order_key(r)))


def find_heading_by_keywords(upload, phrases: list[str]) -> dict | None:
    for row in _policy_headings(upload):
        content = row.get("content") or ""
        if any(_ilike(content, p) for p in phrases):
            return row
    return None


def find_heading_by_embedding(upload, query_text: str) -> dict | None:
    rows = _policy_headings(upload)
    if not rows:
        return None
    ranked = _rank(upload.ensure_index(), rows, None, query_text)
    ranked.sort(key=lambda p: (_is_consolidated(p[0]), -p[1]))
    best, score = ranked[0]
    if score < HEADING_ANCHOR_THRESHOLD:
        return None
    row = dict(best)
    row["similarity"] = round(score, 4)
    return row


# ---------------------------------------------------------------------------
# ReportReferenceTools
# ---------------------------------------------------------------------------

def lookup_tables(upload, phrase: str, note_prefix: str | None, limit: int = 10) -> list[dict]:
    """`table_title ILIKE %ref% OR table_description ILIKE %ref% [OR title LIKE 'N.%']`."""
    out = []
    for table in upload.financial_tables():
        if _ilike(table.get("toc_section"), "consolidated"):
            continue
        title = table.get("table_title") or ""
        description = table.get("table_description") or ""
        if _ilike(title, phrase) or _ilike(description, phrase) or (
            note_prefix and title.strip().lower().startswith(f"{note_prefix}.".lower())
        ):
            out.append(table)
        if len(out) >= limit:
            break
    return out


def lookup_text(upload, phrase: str, note_prefix: str | None, limit: int = 10) -> list[dict]:
    """`content ILIKE %ref% [OR (chunk_type='heading' AND content LIKE 'N.%')]`."""
    out = []
    for row in upload.narrative():
        if _is_consolidated(row):
            continue
        content = row.get("content") or ""
        if _ilike(content, phrase) or (
            note_prefix
            and row.get("chunk_type") == "heading"
            and content.strip().lower().startswith(f"{note_prefix}.".lower())
        ):
            out.append(row)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# AuditorReportTools / GoingConcernTools
# ---------------------------------------------------------------------------

def find_clause_evidence(upload, patterns: list[str], auditor_voice: list[str] | None,
                         window) -> tuple[str, str, bool] | None:
    """Two passes over two sources, matching `_find_clause_evidence`.

    The auditor-voice test is applied to the **body text**, not to metadata, and
    that is not an accident: `tools_fs` records that section metadata for
    auditor content in this corpus "is just the company name", so scoping by
    section never fires, and without some scoping the statutory-dues clause
    lands in the sustainability section.
    """
    for restrict in (True, False):
        for source, id_field, body_field in (
            ("texts", "chunk_id", "content"),
            ("tables", "table_id", "table_md"),
        ):
            rows = (
                upload.narrative() if source == "texts" else upload.financial_tables()
            )
            for row in rows:
                scope = _crumb(row) if source == "texts" else (row.get("toc_section") or "")
                if "consolidated" in scope.lower():
                    continue
                body = row.get(body_field) or ""
                if not any(_ilike(body, p) for p in patterns):
                    continue
                if restrict and auditor_voice and not any(_ilike(body, v) for v in auditor_voice):
                    continue
                label = f"{'text_chunks' if source == 'texts' else 'table_chunks'}:{row.get(id_field)}"
                return label, window(body, patterns), restrict
    return None


def find_flagged(upload, patterns: list[str], window) -> tuple[str, str] | None:
    """One pass over two sources, matching `GoingConcernTools._auditor_flagged`."""
    found = find_clause_evidence(upload, patterns, None, window)
    if found is None:
        return None
    label, extract, _ = found
    return label, extract


# ---------------------------------------------------------------------------
# AuditRiskTools
# ---------------------------------------------------------------------------

def match_note_tables(upload, patterns: list[str]) -> list[dict]:
    """`table_title ILIKE any OR table_description ILIKE any`, standalone only.

    The single helper behind `get_schedule_note`, `get_audit_report_highlights`
    and `review_account_area` — three tools that return nothing for an uploaded
    document without it.
    """
    tables = [t for t in upload.financial_tables()
              if not _ilike(t.get("toc_section"), "consolidated")]

    out = [
        t for t in tables
        if any(_ilike(t.get("table_title") or "", p)
               or _ilike(t.get("table_description") or "", p)
               for p in patterns)
    ]

    if not out and tables:
        # Deliberately NOT relaxed. Dropping the pattern match returns every
        # table in the document for any query, which is not a weaker answer but
        # a wrong one. When no table carries a title or a description at all,
        # that is a coverage failure worth naming rather than working around.
        titled = sum(1 for t in tables
                     if (t.get("table_title") or t.get("table_description")))
        if titled == 0:
            diagnostics.record(
                "schedule notes", None,
                "None of this document's tables carry a caption or a derived "
                "description, so schedule notes cannot be matched by name on it. "
                "That is an extraction limitation of the scan, not an absence of "
                "the note -- ask for the note by its number instead.",
            )
    return sorted(out, key=lambda t: str(t.get("table_id") or ""))
