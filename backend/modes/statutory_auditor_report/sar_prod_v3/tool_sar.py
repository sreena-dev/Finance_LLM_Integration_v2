"""
tool_sar.py — SAR Production v3  (All Tools in One File)
=========================================================
Wired to the real production DB schema:

  Documents   → documents table
  Text chunks → text_chunks table
  Tables      → table_chunks table

Four tool classes:
  FetchTools      — DB retrieval using production schema columns
  CheckTools      — Deterministic pre-flight checks (pure Python, no DB)
  ComputeTools    — Financial ratio / metric computations (pure Python, no DB)
  ReferenceTools  — Vector similarity search on reference standards DB

DB CONNECTION:
  Reads FINANCE_DSN from environment (set in .env or shell).
  Example: FINANCE_DSN=postgresql://user:pass@host:5432/finance_db

SCHEMA OVERVIEW (from production DB):
  documents(doc_id PK, doc_uuid, entity_id, entity_name, fy_start, fy_end,
            doc_name, source_sha256, total_pages_pdf, toc_sections, cin)

  text_chunks(chunk_id PK, parent_chunk, doc_id FK, page_no, statement_scope,
              heading, sub_heading, section_title, section_content,
              regulation_reference, note_reference, figure_reference,
              embedding[1024], title_embedding[1024], embedding_info,
              tsvector, has_table, table_id FK)

  table_chunks(doc_id FK, table_id PK, page_no, table_title, table_description,
               is_financial, financial_statement_type, column_headers, row_count,
               unit, currency, note_reference, description_embedding[1024],
               title_embedding[1024], tsvector, table_html)
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

logger = logging.getLogger("sar_prod_v3.tool_sar")


# ===========================================================================
# Shared date parsing (used by CheckTools.check_report_date_sequence and
# ComputeTools.compute_report_date_gap — extracted here, Gap-closure Phase 1,
# so both checks parse the same set of formats identically instead of the
# closing-sentence-style drift you get from two copies of the same regex-ish
# logic maintained separately. See GAP_CLOSURE_LOG.md.)
# ===========================================================================

def _parse_flexible_date(ds: str) -> Optional[date]:
    """Best-effort parse of a date string in any of the formats this pipeline
    has actually seen come out of extracted SAR text. Returns None (never
    raises) when nothing matches — callers treat that as "could not verify",
    not as a parse error worth crashing over."""
    for fmt in ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y",
                "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y"]:
        try:
            return datetime.strptime(ds.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


# ===========================================================================
# DB CONNECTION HELPER
# ===========================================================================

def _get_db_conn():
    """
    Returns a psycopg2 connection using FINANCE_DSN from environment.

    Set the env var in your .env file:
        FINANCE_DSN=postgresql://user:password@host:5432/finance_db

    Or export it in your shell before running the pipeline.
    """
    import psycopg2
    dsn = os.environ.get("FINANCE_DSN", "")
    if not dsn:
        raise EnvironmentError(
            "FINANCE_DSN environment variable is not set. "
            "Add it to your .env file: FINANCE_DSN=postgresql://user:pass@host:5432/db"
        )
    return psycopg2.connect(dsn)


# ===========================================================================
# ToolResult — standard return type for all FetchTools methods
# ===========================================================================

@dataclass
class ToolResult:
    """Standardised return type for all SAR fetch tools."""
    data: Any
    quality_flag: str = "reliable"   # reliable | partial | low_confidence | not_found
    fallback_used: str = "direct"    # direct | tsvector | keyword | absent
    source_meta: dict = field(default_factory=dict)

    def is_usable(self) -> bool:
        return self.quality_flag != "not_found"

    def to_prompt_block(self) -> str:
        """Returns a string safe to inject into an LLM prompt with quality context."""
        if not self.is_usable():
            return f"[NOT FOUND — {self.source_meta.get('section', 'unknown section')}]"
        note = ""
        if self.quality_flag == "low_confidence":
            note = "\n⚠️ NOTE: This section was extracted with low confidence. Treat as provisional."
        elif self.quality_flag == "partial":
            note = "\n⚠️ NOTE: This section may be partially extracted."
        return f"{self.data}{note}"


# ===========================================================================
# CheckResult — standard return type for all CheckTools methods
# ===========================================================================

@dataclass
class CheckResult:
    check_id: str
    tag: str           # FINDING | RISK_FLAG | AUDIT_POINTER
    component: str
    passed: bool
    observation: str
    evidence: str
    risk_rating: str = "Information request only"


# ===========================================================================
# CLASS: FetchTools — DB retrieval from production schema
# ===========================================================================

class FetchTools:
    """
    Fetches text and table data from the production DB.

    PRODUCTION DB SCHEMA:
      documents(doc_id, entity_name, fy_start, fy_end, doc_name, toc_sections, cin, ...)
      text_chunks(chunk_id, doc_id FK, heading, section_title, section_content,
                  regulation_reference, statement_scope, page_no, has_table,
                  table_id FK, tsvector)
      table_chunks(table_id PK, doc_id FK, table_title, table_description,
                   is_financial, financial_statement_type, column_headers,
                   unit, currency, table_html, tsvector)

    SECTION ROUTING STRATEGY (priority cascade):
      1. regulation_reference ILIKE match  (CARO / IFC / SA-specific)
      2. heading / section_title keyword match
      3. tsvector full-text search fallback
    """

    # ---------------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------------

    @staticmethod
    def _quality_flag(chunk_count: int, expected_min: int = 1) -> str:
        """Map chunk count to quality flag."""
        if chunk_count >= expected_min:
            return "reliable"
        elif chunk_count > 0:
            return "partial"
        return "not_found"

    @staticmethod
    def _concat_rows(rows: list[tuple], content_col: int = 0) -> str:
        """
        Concatenate content values from rows.
        Each row is a tuple; content_col is the index of the content field.
        Adds [Section: heading] labels where available.
        """
        if not rows:
            return ""
        parts = []
        for row in rows:
            content = row[content_col] or ""
            heading = row[1] if len(row) > 1 else ""
            section_title = row[2] if len(row) > 2 else ""
            label = section_title or heading or "Section"
            if content.strip():
                parts.append(f"[Section: {label}]\n{content.strip()}")
        return "\n\n".join(parts)

    # ---------------------------------------------------------------------------
    # Document resolution
    # ---------------------------------------------------------------------------

    @classmethod
    def resolve_doc_id(cls, company: str, fy_start: int, fy_end: int) -> str | None:
        """
        Looks up the doc_id for a given company + financial year.
        Matching is case-insensitive and normalises underscores to spaces.

        Returns:
            doc_id string, or None if no match found.
        """
        conn = _get_db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT doc_id FROM documents
                    WHERE LOWER(REPLACE(company, '_', ' ')) = LOWER(REPLACE(%s, '_', ' '))
                      AND fy_start = %s AND fy_end = %s
                    ORDER BY doc_id
                    LIMIT 1
                    """,
                    [company, fy_start, fy_end],
                )
                row = cur.fetchone()
                if row:
                    return row[0]

                # Fuzzy fallback: ILIKE partial match
                cur.execute(
                    """
                    SELECT doc_id FROM documents
                    WHERE company ILIKE %s
                      AND fy_start = %s AND fy_end = %s
                    ORDER BY doc_id
                    LIMIT 1
                    """,
                    [f"%{company.strip()}%", fy_start, fy_end],
                )
                row = cur.fetchone()
                return row[0] if row else None
        finally:
            conn.close()

    @classmethod
    def get_document_meta(cls, doc_id: str) -> dict:
        """
        Returns document metadata from the documents table.
        Used by the pipeline for display / report header.
        """
        conn = _get_db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT doc_id, doc_uuid, company, fy_start, fy_end,
                           doc_name, total_pages_pdf, toc_sections
                    FROM documents
                    WHERE doc_id = %s
                    """,
                    [doc_id],
                )
                row = cur.fetchone()
                if not row:
                    return {}
                return {
                    # Interface preserved as "entity_name" for callers (the
                    # pipeline and frontend read this key); only the source
                    # column is "company" in this schema. `cin` has no
                    # column here — this schema doesn't track it — so it's
                    # always None rather than a KeyError for callers that
                    # expect the key to exist.
                    "doc_id": row[0], "doc_uuid": row[1], "entity_name": row[2],
                    "fy_start": row[3], "fy_end": row[4], "doc_name": row[5],
                    "total_pages": row[6], "toc_sections": row[7], "cin": None,
                }
        finally:
            conn.close()

    # ---------------------------------------------------------------------------
    # Core text fetch: the universal _fetch_text_chunks engine
    # ---------------------------------------------------------------------------

    @classmethod
    def _fetch_text_chunks(
        cls,
        doc_id: str,
        *,
        heading_patterns: list[str] | None = None,
        regulation_patterns: list[str] | None = None,
        tsquery: str | None = None,
        exclude_regulation_patterns: list[str] | None = None,
        statement_scope: str | None = None,
        toc_patterns: list[str] | None = None,
        order_by: str = "page_pdf_start ASC",
        limit: int = 100,
    ) -> list[tuple]:
        """
        Universal text chunk fetcher with a priority cascade.

        SCHEMA NOTE — adapted from the columns this was originally written
        against to the ones this deployment's `text_chunks` actually has.
        There is no `heading`/`sub_heading`/`section_title` hierarchy, no
        `regulation_reference` classification column, no stored `tsvector`,
        and no `statement_scope` (standalone/consolidated is not tracked at
        chunk level, so `statement_scope` is accepted for signature
        compatibility but is a no-op — every chunk matches regardless of
        scope). Concretely:
          - heading_patterns / regulation_patterns both route through
            `section`, `title` and `section_breadcrumb` — this schema has one
            structural-heading dimension, not two, so both parameter lists
            search the same fields. Passing both is still meaningful: it
            just means "match either list of phrases".
          - toc_patterns (new) scopes to `toc_section` — e.g. restricting to
            the "Independent Auditors' Report" chapter — which cuts down
            false positives from keyword matches elsewhere in a long annual
            report. Callers that don't need it can omit it.
          - tsquery runs against `to_tsvector('english', content)` computed
            at query time (no stored tsvector column / GIN index here), so
            it is a correctness fallback, not a fast path — used only when
            the primary heading-based match returns nothing.

        Returns list of rows:
          (content, section, title, page_pdf_start, chunk_id, toc_section)
        — same 6-column shape the original callers (_concat_rows and friends)
        already expect positionally.
        """
        conn = _get_db_conn()
        try:
            with conn.cursor() as cur:
                # Build WHERE clauses
                conditions = ["doc_id = %s"]
                params: list[Any] = [doc_id]

                inclusion_clause_parts = []

                if regulation_patterns:
                    reg_clauses = " OR ".join(
                        ["(section ILIKE %s OR title ILIKE %s OR section_breadcrumb::text ILIKE %s)"]
                        * len(regulation_patterns)
                    )
                    inclusion_clause_parts.append(f"({reg_clauses})")
                    for p in regulation_patterns:
                        params.extend([p, p, p])

                if heading_patterns:
                    head_clauses = " OR ".join(
                        ["(section ILIKE %s OR title ILIKE %s OR section_breadcrumb::text ILIKE %s)"]
                        * len(heading_patterns)
                    )
                    inclusion_clause_parts.append(f"({head_clauses})")
                    for p in heading_patterns:
                        params.extend([p, p, p])

                if tsquery:
                    inclusion_clause_parts.append(
                        "to_tsvector('english', content) @@ to_tsquery('english', %s)"
                    )
                    params.append(tsquery)

                if inclusion_clause_parts:
                    conditions.append(f"({' OR '.join(inclusion_clause_parts)})")

                if toc_patterns:
                    toc_clauses = " OR ".join(["toc_section ILIKE %s"] * len(toc_patterns))
                    conditions.append(f"({toc_clauses})")
                    params.extend(toc_patterns)

                # Exclusion clauses
                if exclude_regulation_patterns:
                    for ep in exclude_regulation_patterns:
                        conditions.append(
                            "(section IS NULL OR section NOT ILIKE %s) "
                            "AND (title IS NULL OR title NOT ILIKE %s)"
                        )
                        params.extend([ep, ep])

                # statement_scope: intentionally not filtered on — see docstring.

                # Priority ordering: rows that matched a curated heading/
                # regulation route (e.g. "%emphasis of matter%") must outrank
                # rows that only matched the broad, recall-oriented tsquery
                # OR-expression. Without this, ORDER BY page_pdf_start ASC +
                # LIMIT silently drops the real heading matches whenever
                # generic tsquery hits from earlier pages (the word "matter"
                # alone appears dozens of times before the Independent
                # Auditors' Report even starts) fill the LIMIT first.
                priority_clauses = []
                priority_params: list[Any] = []
                if regulation_patterns:
                    priority_clauses.append(
                        " OR ".join(
                            ["(section ILIKE %s OR title ILIKE %s OR section_breadcrumb::text ILIKE %s)"]
                            * len(regulation_patterns)
                        )
                    )
                    for p in regulation_patterns:
                        priority_params.extend([p, p, p])
                if heading_patterns:
                    priority_clauses.append(
                        " OR ".join(
                            ["(section ILIKE %s OR title ILIKE %s OR section_breadcrumb::text ILIKE %s)"]
                            * len(heading_patterns)
                        )
                    )
                    for p in heading_patterns:
                        priority_params.extend([p, p, p])

                final_order_by = order_by
                if priority_clauses and order_by == "page_pdf_start ASC":
                    priority_expr = " OR ".join(f"({c})" for c in priority_clauses)
                    final_order_by = f"(CASE WHEN ({priority_expr}) THEN 0 ELSE 1 END), page_pdf_start ASC"
                    params = params + priority_params

                where = " AND ".join(conditions)
                sql = f"""
                    SELECT content, section, title, page_pdf_start, chunk_id, toc_section
                    FROM text_chunks
                    WHERE {where}
                    ORDER BY {final_order_by}
                    LIMIT {limit}
                """
                cur.execute(sql, params)
                return cur.fetchall()
        finally:
            conn.close()

    # ---------------------------------------------------------------------------
    # Main Auditor's Report
    # ---------------------------------------------------------------------------

    @classmethod
    def fetch_main_sar_text(cls, doc_id: str, scope: str = "standalone") -> ToolResult:
        """
        Fetches the full main auditor's report text.

        Sections included:
          Opinion, Basis for Opinion, Key Audit Matters, Emphasis of Matter,
          Going Concern, Responsibilities, Other Legal Requirements, Rule 11,
          Section 143(3), Formal Checks (UDIN, signatures).

        CARO and IFC are EXCLUDED — they are fetched separately.

        Uses the new schema:
          - Primary: heading ILIKE '%auditor%' or '%independent%' or '%statutory%'
          - Exclusion: regulation_reference NOT ILIKE '%CARO%' and NOT ILIKE '%143(3A)%'
          - Fallback: tsvector search for 'auditor & opinion'
        """
        try:
            rows = cls._fetch_text_chunks(
                doc_id,
                heading_patterns=[
                    "%independent auditor%",
                    "%statutory auditor%",
                    "%auditor%report%",
                    "%basis for%opinion%",
                    "%key audit matter%",
                    "%emphasis of matter%",
                    "%going concern%",
                    "%other legal%regulatory%",
                    "%rule 11%",
                    "%section 143%",
                    "%formal%check%",
                    "%udin%",
                ],
                exclude_regulation_patterns=["%CARO%", "%143(3A)%"],
                statement_scope=scope,
                toc_patterns=["%auditor%"],
                # A report with both Consolidated and Standalone opinions
                # repeats every one of the heading_patterns above once per
                # opinion, so match counts routinely exceed 100 (e.g. 180 for
                # a real filing). Combined with ORDER BY page_pdf_start ASC —
                # where NULLS sort last, and a large share of this schema's
                # chunks have no page number at all — a tight LIMIT here
                # silently drops the second (usually Standalone) opinion's
                # text, including its mandatory EoM/KAM closing sentences.
                limit=250,
            )

            if not rows:
                # tsvector fallback — deliberately NOT toc-scoped: it exists
                # to catch the case where the primary match failed because
                # the document's toc_section labelling doesn't match what we
                # expect, so narrowing it further would defeat the fallback.
                rows = cls._fetch_text_chunks(
                    doc_id,
                    tsquery="auditor & opinion",
                    exclude_regulation_patterns=["%CARO%", "%143(3A)%"],
                    statement_scope=scope,
                    limit=40,
                )

            if not rows:
                return ToolResult(
                    data="", quality_flag="not_found",
                    source_meta={"doc_id": doc_id, "section": "MAIN_REPORT", "scope": scope},
                )

            text = cls._concat_rows(rows)
            flag = "reliable" if len(rows) >= 3 else "partial"
            return ToolResult(
                data=text, quality_flag=flag,
                source_meta={
                    "doc_id": doc_id, "scope": scope,
                    "chunk_count": len(rows),
                    "pages": sorted({r[3] for r in rows if r[3]}),
                },
            )
        except Exception as exc:
            logger.error("fetch_main_sar_text(%s) failed: %s", doc_id, exc)
            return ToolResult(
                data="", quality_flag="not_found",
                source_meta={"doc_id": doc_id, "error": str(exc)},
            )

    # ---------------------------------------------------------------------------
    # CARO 2020 Annexure
    # ---------------------------------------------------------------------------

    @classmethod
    def fetch_caro_text(cls, doc_id: str) -> ToolResult:
        """
        Fetches CARO 2020 annexure text.
        Primary routing: regulation_reference ILIKE '%CARO%', OR the
        "Annexure A to the Independent Auditors' Report" naming convention
        (see below). Fallback: narrower heading keywords + tsvector.

        BUG FIX (found against real ingested data, not hypothetical):
        most real Indian SARs never put the literal word "CARO" in the
        annexure's own heading at all — they head it "Annexure A/'A'/- A to
        the Independent Auditors' Report" and only use "CARO" / "the Order"
        in body prose. Confirmed on RVNL_2024_2025: the real CARO annexure
        is headed "Annexure - A To The Independent Auditors' Report", which
        neither of the old regulation_patterns matched — so this always fell
        through to the fallback tier, whose bare "%annexure%" pattern then
        matched EVERY unrelated annexure in the annual report (director
        Code-of-Conduct declarations, CSR-committee annexures, etc.) and
        concatenated all of that non-CARO text as if it were the CARO
        annexure. The extractor LLM, given that garbage, correctly reported
        `caro_applicable: false` — a false "not applicable" rather than a
        genuine one. `%annexure%` alone is removed below for exactly this
        reason; the "Annexure - A" family of patterns is specific enough
        (verified against real data) not to sweep in unrelated annexures.

        SECOND BUG FOUND FIXING THE FIRST ONE: the old `toc_patterns=
        ["%auditor%"]` AND-filter on the primary tier is itself unreliable
        on real data and was dropped. Confirmed on the same document: every
        chunk under "Annexure - A To The Independent Auditors' Report" has
        `toc_section = 'CNK & Associates LLP Chartered Accountants'` (the
        audit firm's own name — the nearest top-level TOC entry ingestion
        attached to this whole page range) — no "auditor" substring in it
        at all, so the AND-filter silently zeroed out every otherwise-
        correct match. The new regulation_patterns above are already
        specific enough that this extra restriction was doing more harm
        (real false negatives) than good.
        """
        try:
            rows = cls._fetch_text_chunks(
                doc_id,
                regulation_patterns=[
                    "%CARO%", "%Companies Auditor%Report%Order%",
                    "%annexure - a%", "%annexure-a%", "%annexure a %", "%annexure 'a'%",
                ],
                limit=60,
            )

            if not rows:
                rows = cls._fetch_text_chunks(
                    doc_id,
                    heading_patterns=["%caro%", "%auditor%report%order%"],
                    tsquery="caro & clause",
                    limit=40,
                )

            if not rows:
                return ToolResult(
                    data="", quality_flag="not_found",
                    source_meta={"doc_id": doc_id, "section": "CARO_2020"},
                )

            text = cls._concat_rows(rows)
            return ToolResult(
                data=text,
                quality_flag="reliable" if len(rows) >= 5 else "partial",
                source_meta={"doc_id": doc_id, "chunk_count": len(rows)},
            )
        except Exception as exc:
            logger.error("fetch_caro_text(%s) failed: %s", doc_id, exc)
            return ToolResult(
                data="", quality_flag="not_found",
                source_meta={"doc_id": doc_id, "error": str(exc)},
            )

    # ---------------------------------------------------------------------------
    # IFC Report
    # ---------------------------------------------------------------------------

    @classmethod
    def fetch_ifc_text(cls, doc_id: str) -> ToolResult:
        """
        Fetches Internal Financial Controls (IFC) report text.
        Primary routing: regulation_reference ILIKE '%143(3A)%' or '%IFC%'
        Fallback: heading ILIKE '%internal financial%'
        """
        try:
            rows = cls._fetch_text_chunks(
                doc_id,
                # "Annexure B/'B'/- B to the Independent Auditors' Report" added
                # alongside the literal-phrase patterns for the same reason as
                # fetch_caro_text's "Annexure A" fix above — a filing that only
                # labels this "Annexure B" (no literal "internal financial
                # control" in the heading itself) would otherwise be missed.
                regulation_patterns=[
                    "%143(3A)%", "%internal financial control%", "%IFC%",
                    "%annexure - b%", "%annexure-b%", "%annexure b %", "%annexure 'b'%",
                ],
                heading_patterns=["%internal financial control%"],
                # toc_patterns=["%auditor%"] deliberately dropped — see the
                # matching note in fetch_caro_text above: real `toc_section`
                # values under an annexure are frequently the audit firm's
                # own name, not anything containing "auditor".
                limit=30,
            )

            if not rows:
                rows = cls._fetch_text_chunks(
                    doc_id,
                    tsquery="internal & financial & control",
                    limit=20,
                )

            if not rows:
                return ToolResult(
                    data="", quality_flag="not_found",
                    source_meta={"doc_id": doc_id, "section": "IFC_REPORT"},
                )

            text = cls._concat_rows(rows)
            return ToolResult(
                data=text,
                quality_flag="reliable" if len(rows) >= 2 else "partial",
                source_meta={"doc_id": doc_id, "chunk_count": len(rows)},
            )
        except Exception as exc:
            logger.error("fetch_ifc_text(%s) failed: %s", doc_id, exc)
            return ToolResult(
                data="", quality_flag="not_found",
                source_meta={"doc_id": doc_id, "error": str(exc)},
            )

    # ---------------------------------------------------------------------------
    # Financial Tables (Balance Sheet, P&L, Cash Flow)
    # ---------------------------------------------------------------------------

    @classmethod
    def fetch_financial_tables(cls, doc_id: str) -> ToolResult:
        """
        Fetches Balance Sheet, P&L, and Cash Flow tables.

        SCHEMA NOTE: this deployment's `table_chunks` names the classification
        column `financial_stmt_type` (not `financial_statement_type`), and
        stores the table body pre-converted to markdown in `table_md` rather
        than raw HTML in `table_html` — so unlike the original, there is no
        HTML-to-markdown conversion step here; `table_md` is used as-is.

        Returns:
          dict with keys 'balance_sheet', 'profit_loss', 'cash_flow', each containing:
            {table_title, table_md, unit, currency, page_no, description}
        """
        target_types = ["balance_sheet", "profit_loss", "cash_flow"]
        conn = _get_db_conn()
        try:
            with conn.cursor() as cur:
                ph = ",".join(["%s"] * len(target_types))
                cur.execute(
                    f"""
                    SELECT table_id, financial_stmt_type, table_title,
                           table_description, table_md, unit, currency, page_pdf_start, column_headers
                    FROM table_chunks
                    WHERE doc_id = %s
                      AND is_financial = TRUE
                      AND financial_stmt_type IN ({ph})
                    ORDER BY page_pdf_start ASC
                    """,
                    [doc_id] + target_types,
                )
                rows = cur.fetchall()
        finally:
            conn.close()

        if not rows:
            return ToolResult(
                data={}, quality_flag="not_found",
                source_meta={"doc_id": doc_id, "note": "No financial tables found"},
            )

        result: dict[str, Any] = {}
        for row in rows:
            table_id, fs_type, title, description, table_md_raw, unit, currency, page_no, col_headers = row
            table_md = f"**{title}**\n{table_md_raw}" if title else (table_md_raw or "")
            # Use first occurrence of each type (ordered by page_no)
            if fs_type not in result:
                result[fs_type] = {
                    "table_title": title or fs_type,
                    "table_md": table_md,
                    "unit": unit or "Crores",
                    "currency": currency or "INR",
                    "page_no": page_no,
                    "description": description or "",
                    "table_id": table_id,
                }

        missing = [t for t in target_types if t not in result]
        flag = "reliable" if not missing else ("partial" if result else "not_found")
        return ToolResult(
            data=result, quality_flag=flag,
            source_meta={
                "doc_id": doc_id,
                "tables_found": list(result.keys()),
                "tables_missing": missing,
            },
        )

    # ---------------------------------------------------------------------------
    # CARO Appendix Tables (Title Deeds, Disputed Dues)
    # ---------------------------------------------------------------------------

    @classmethod
    def fetch_appendix_tables(cls, doc_id: str) -> ToolResult:
        """
        Fetches CARO appendix tables:
          Appendix-1: Title Deeds of Immovable Properties
          Appendix-2: Disputed Statutory Dues

        Routes via:
          table_title ILIKE '%appendix%' OR '%title deed%' OR '%disputed%',
          with a `to_tsvector` fallback over the table's own title/description
          (this schema has no stored `tsvector` column to query directly).

        SCHEMA NOTE: `table_html`/`page_no` don't exist here — this deployment
        stores the pre-converted markdown in `table_md` and the page number as
        `page_pdf_start`; used directly, no HTML-to-markdown step needed.

        BUG FIX (pre-existing, not schema-related): the literal '%appendix%'
        etc. were embedded directly in the SQL text rather than passed as
        bind parameters. psycopg2 scans the query string for %s-style
        placeholders, and an un-doubled literal `%` there raises IndexError
        before the query ever reaches postgres — this method has never
        successfully executed. Fixed by binding them as parameters like
        every other ILIKE pattern in this file.
        """
        conn = _get_db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT table_id, table_title, table_description, table_md, page_pdf_start
                    FROM table_chunks
                    WHERE doc_id = %s
                      AND (
                            table_title ILIKE %s
                         OR table_title ILIKE %s
                         OR table_title ILIKE %s
                         OR to_tsvector('english', COALESCE(table_title, '') || ' ' || COALESCE(table_description, ''))
                            @@ to_tsquery('english', %s)
                      )
                    ORDER BY page_pdf_start ASC
                    LIMIT 10
                    """,
                    [doc_id, "%appendix%", "%title deed%", "%disputed%", "appendix & title | disputed & dues"],
                )
                rows = cur.fetchall()
        finally:
            conn.close()

        result: dict[str, Any] = {"appendix_1": None, "appendix_2": None}
        for row in rows:
            table_id, title, description, table_md, page_no = row
            text = table_md or description or "[Table available but no content extracted]"
            title_lower = (title or "").lower()
            if "title deed" in title_lower or "appendix 1" in title_lower or "appendix-1" in title_lower:
                result["appendix_1"] = f"**{title}** (Page {page_no})\n\n{text}"
            elif "disputed" in title_lower or "appendix 2" in title_lower or "appendix-2" in title_lower:
                result["appendix_2"] = f"**{title}** (Page {page_no})\n\n{text}"
            else:
                # Assign to first available slot
                if not result["appendix_1"]:
                    result["appendix_1"] = f"**{title}** (Page {page_no})\n\n{text}"
                elif not result["appendix_2"]:
                    result["appendix_2"] = f"**{title}** (Page {page_no})\n\n{text}"

        found = [k for k, v in result.items() if v is not None]
        flag = "reliable" if len(found) == 2 else ("partial" if found else "not_found")
        return ToolResult(
            data=result, quality_flag=flag,
            source_meta={"doc_id": doc_id, "appendices_found": found},
        )

    # ---------------------------------------------------------------------------
    # Directors' Report
    # ---------------------------------------------------------------------------

    @classmethod
    def fetch_directors_report(cls, doc_id: str) -> ToolResult:
        """Fetches Directors' Report text for SA 720 consistency checks."""
        try:
            rows = cls._fetch_text_chunks(
                doc_id,
                heading_patterns=["%director%", "%board%report%", "%management discussion%", "%mda%"],
                toc_patterns=["%board%report%", "%director%"],
                limit=40,
            )

            if not rows:
                rows = cls._fetch_text_chunks(
                    doc_id,
                    tsquery="directors & report",
                    limit=20,
                )

            if not rows:
                return ToolResult(
                    data="", quality_flag="not_found",
                    source_meta={"doc_id": doc_id, "section": "DIRECTORS_REPORT"},
                )

            text = cls._concat_rows(rows)
            return ToolResult(
                data=text,
                quality_flag="reliable" if len(rows) >= 3 else "partial",
                source_meta={"doc_id": doc_id, "chunk_count": len(rows)},
            )
        except Exception as exc:
            logger.error("fetch_directors_report(%s) failed: %s", doc_id, exc)
            return ToolResult(
                data="", quality_flag="not_found",
                source_meta={"doc_id": doc_id, "error": str(exc)},
            )

    # ---------------------------------------------------------------------------
    # Prior Year SAR Result (for trend comparison)
    # ---------------------------------------------------------------------------

    @classmethod
    def fetch_prior_year_result(cls, company: str, fy_end: int) -> ToolResult:
        """
        Fetches the SAR parsed JSON from the prior financial year.
        Used for year-on-year opinion trend comparison.

        Looks in the sar_results table (if it exists) for prior-year result.
        Returns ToolResult with quality_flag='not_found' if no prior year data exists.
        """
        prior_fy = fy_end - 1
        conn = _get_db_conn()
        try:
            with conn.cursor() as cur:
                # Check if sar_results table exists
                cur.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_name = 'sar_results'
                    )
                    """
                )
                if not cur.fetchone()[0]:
                    return ToolResult(
                        data={}, quality_flag="not_found",
                        source_meta={
                            "company": company, "fy_end_searched": prior_fy,
                            "note": "sar_results table does not exist yet.",
                        },
                    )

                # NOTE: dormant on this deployment (sar_results doesn't
                # exist, so the EXISTS check above returns early first).
                # `entity_name` dropped from the OR — this deployment's
                # documents table doesn't have that column, and sar_results
                # would presumably follow the same naming if/when created.
                cur.execute(
                    """
                    SELECT result_json FROM sar_results
                    WHERE company ILIKE %s AND fy_end = %s
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    [company, prior_fy],
                )
                row = cur.fetchone()
        finally:
            conn.close()

        if not row:
            return ToolResult(
                data={}, quality_flag="not_found",
                source_meta={
                    "company": company, "fy_end_searched": prior_fy,
                    "note": f"No SAR result found for FY ending {prior_fy}",
                },
            )

        result_json = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        return ToolResult(
            data=result_json, quality_flag="reliable",
            source_meta={"company": company, "fy_end": prior_fy},
        )

    @classmethod
    def save_sar_result(cls, company: str, fy_end: int, result_json: dict) -> None:
        """
        Persists a compact trend record (see prior_year_continuity.
        build_prior_year_summary — NOT the full merged JSON or memorandum)
        so `fetch_prior_year_result` finds something on the *next* run for
        this company. Gap-closure Phase 3, Gap #6.

        `fetch_prior_year_result` above has been dormant since it was
        written — its own EXISTS check confirms `sar_results` has never
        existed in this deployment, and until now nothing ever wrote to it
        either. Rather than requiring a manual migration this codebase has
        no way to apply here, this method creates the table itself
        (`CREATE TABLE IF NOT EXISTS`) on first use: the very first report
        generated after this change creates the table and writes one row;
        from the *second* report for the same company onward,
        `fetch_prior_year_result` starts finding it.

        Best-effort by design: the caller (SARReportPipeline.run) wraps
        this in a try/except and logs a warning on failure rather than
        letting a trend-record write failure fail report generation —
        losing this year's trend record is a next-year inconvenience, not
        a reason to withhold this year's report.
        """
        conn = _get_db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sar_results (
                        id SERIAL PRIMARY KEY,
                        company TEXT NOT NULL,
                        fy_end INTEGER NOT NULL,
                        result_json JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
                cur.execute(
                    "INSERT INTO sar_results (company, fy_end, result_json) VALUES (%s, %s, %s)",
                    [company, fy_end, json.dumps(result_json)],
                )
            conn.commit()
        finally:
            conn.close()


# ===========================================================================
# HTML TABLE HELPER
# ===========================================================================

def _html_table_to_text(html: str | None, *, title: str = "", description: str = "") -> str:
    """
    Converts an HTML table to a readable text/markdown representation.

    Priority:
      1. pandas.read_html() → markdown table (clean for LLM)
      2. Regex-based text extraction fallback (no pandas required)
      3. Raw HTML truncated (last resort)

    Args:
        html: HTML string from table_chunks.table_html
        title: Table title (prepended to output)
        description: AI-generated description (used if HTML parse fails)
    """
    if not html or not html.strip():
        return description or "[Table data not available]"

    header = f"**{title}**\n" if title else ""

    # Attempt 1: pandas read_html
    try:
        import pandas as pd
        from io import StringIO
        dfs = pd.read_html(StringIO(html), flavor="bs4")
        if dfs:
            df = dfs[0]
            # Replace NaN with empty string for clean output
            df = df.fillna("")
            md = df.to_markdown(index=False)
            return f"{header}{md}"
    except Exception:
        pass

    # Attempt 2: regex text extraction
    try:
        # Strip all HTML tags and extract text
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s{2,}", " ", text).strip()
        if text and description:
            return f"{header}{description}\n\n{text[:3000]}"
        elif text:
            return f"{header}{text[:3000]}"
    except Exception:
        pass

    # Attempt 3: description only
    if description:
        return f"{header}{description}"

    return "[Table available but could not be parsed]"


# ===========================================================================
# CLASS: CheckTools — Deterministic checks (no DB, no LLM)
# ===========================================================================

class CheckTools:
    """
    Runs deterministic checks on raw SAR text BEFORE any LLM is invoked.
    All methods are pure Python: fast, testable, 100% deterministic.
    Results are passed to agents as pre-seeded observations.

    RULE: Never present a RISK_FLAG or AUDIT_POINTER as a FINDING.
    """

    # Mandatory sections that must be present in any valid SAR
    _MANDATORY_SECTIONS = {
        "PRE-01": {
            "pattern": r"(?i)in\s+our\s+opinion|we\s+report\s+that|true\s+and\s+fair\s+view",
            "component": "Opinion Paragraph",
            "tag": "AUDIT_POINTER",
            "missing_obs": "The opinion paragraph ('In our opinion...true and fair view') was not identified in the extracted text.",
        },
        "PRE-02": {
            "pattern": r"(?i)standards?\s+on\s+auditing|basis\s+for\s+(?:our\s+)?(?:audit\s+)?opinion",
            "component": "Basis for Opinion (SA 700.28)",
            "tag": "AUDIT_POINTER",
            "missing_obs": "The 'Basis for Opinion' paragraph was not identified. This is mandatory per SA 700.28.",
        },
        "PRE-03": {
            "pattern": r"(?i)emphasis\s+of\s+matter|we\s+draw\s+attention",
            "component": "Emphasis of Matter",
            "tag": "AUDIT_POINTER",
            "missing_obs": "No Emphasis of Matter paragraph was found. (Note: EoM is not always required — flag as AUDIT_POINTER if financial statements show significant items.)",
        },
        "PRE-04": {
            "pattern": r"(?i)report\s+on\s+other\s+legal\s+and\s+regulatory|as\s+required\s+by\s+section\s+143",
            "component": "Report on Other Legal and Regulatory Requirements (Section 143)",
            "tag": "AUDIT_POINTER",
            "missing_obs": "The 'Report on Other Legal and Regulatory Requirements' section was not identified. This section is mandatory for all companies audited under the Companies Act 2013.",
        },
        "PRE-05": {
            "pattern": r"(?i)rule\s+11|rules?\s+made\s+thereunder|as\s+required\s+by\s+rules",
            "component": "Rule 11 Sub-clauses",
            "tag": "AUDIT_POINTER",
            "missing_obs": "Rule 11 sub-clauses were not identified. Companies (Audit and Auditors) Rules 2014 Rule 11 requires reporting on pending litigations, foreseeable losses, audit trail, and IEPF transfers.",
        },
        "PRE-06": {
            "pattern": r"(?i)CARO|companies\s+\(auditor[''s]\s+report\)\s+order",
            "component": "CARO 2020 Annexure",
            "tag": "AUDIT_POINTER",
            "missing_obs": "CARO 2020 Annexure was not identified. CARO 2020 is mandatory for companies meeting the prescribed thresholds.",
        },
        "PRE-07": {
            "pattern": r"(?i)internal\s+financial\s+controls?|IFC",
            "component": "IFC Report (Section 143(3)(i))",
            "tag": "AUDIT_POINTER",
            "missing_obs": "The Internal Financial Controls (IFC) report was not identified. Reporting on IFC is mandatory per Section 143(3)(i) of the Companies Act 2013.",
        },
        "PRE-08": {
            "pattern": r"(?i)UDIN|unique\s+document\s+identification",
            "component": "UDIN",
            "tag": "AUDIT_POINTER",
            "missing_obs": "UDIN (Unique Document Identification Number) was not found in the report. UDIN is mandatory per ICAI guidelines.",
        },
        "PRE-09": {
            "pattern": r"(?i)comptroller\s+and\s+auditor\s+general|C&AG|section\s+143\s*\(\s*5\s*\)",
            "component": "C&AG Directions (Section 143(5))",
            "tag": "AUDIT_POINTER",
            "missing_obs": "C&AG / Comptroller and Auditor General directions under Section 143(5) were not found. (Note: This is only applicable to government companies.)",
        },
    }

    @classmethod
    def run_preflight_checks(cls, main_text: str, caro_text: str = "", ifc_text: str = "") -> dict:
        """
        Runs all deterministic mandatory-component checks on raw SAR text.
        Called BEFORE any LLM agent runs.

        Returns:
            {
              "observations": list[dict],
              "preflight_summary": str,
              "components_found": list[str],
              "components_missing": list[str]
            }
        """
        combined = "\n".join(filter(None, [main_text, caro_text, ifc_text]))
        observations, found, missing = [], [], []

        for check_id, check in cls._MANDATORY_SECTIONS.items():
            if re.search(check["pattern"], combined):
                found.append(check["component"])
            else:
                missing.append(check["component"])
                observations.append({
                    "check_id": check_id,
                    "tag": check["tag"],
                    "component": check["component"],
                    "observation": check["missing_obs"],
                    "risk_rating": "Information request only",
                })

        n = len(missing)
        if n == 0:
            summary = "Pre-flight checks: All mandatory components identified. No pre-flight AUDIT_POINTERs raised."
        else:
            summary = (
                f"Pre-flight checks: {n} mandatory component(s) NOT identified — "
                f"{'; '.join(missing)}. "
                f"These are already raised as AUDIT_POINTERs below. Do NOT re-raise them in your analysis."
            )
        return {
            "observations": observations,
            "preflight_summary": summary,
            "components_found": found,
            "components_missing": missing,
        }

    @staticmethod
    def validate_udin_format(udin_string: str) -> CheckResult:
        """
        Validates that a UDIN follows the ICAI-prescribed 18-character format:
        2-digit year + 6-digit membership no. + 10-char alphanumeric.
        """
        if not udin_string or not udin_string.strip():
            return CheckResult(
                check_id="CHK-UDIN-01", tag="AUDIT_POINTER",
                component="Formal Checks — UDIN", passed=False,
                observation="UDIN was not provided or could not be extracted from the report.",
                evidence="", risk_rating="Information request only",
            )
        udin = udin_string.strip()
        is_valid = bool(re.match(r"^[0-9]{2}[0-9]{6}[A-Z0-9]{10}$", udin, re.IGNORECASE))
        if is_valid:
            return CheckResult(
                check_id="CHK-UDIN-01", tag="FINDING",
                component="Formal Checks — UDIN", passed=True,
                observation="UDIN is present and conforms to the ICAI 18-character format.",
                evidence=udin,
            )
        return CheckResult(
            check_id="CHK-UDIN-01", tag="AUDIT_POINTER",
            component="Formal Checks — UDIN", passed=False,
            observation=(f"UDIN '{udin}' does not conform to the expected 18-character ICAI format. "
                         f"Verify against the ICAI UDIN portal."),
            evidence=udin, risk_rating="Information request only",
        )

    @staticmethod
    def check_eom_closing_sentence(eom_text: str) -> CheckResult:
        """
        Checks for the mandatory SA 706.8 closing sentence in the EoM section:
        'Our opinion on the Financial Statements is not modified in respect of the above matters.'
        """
        if not eom_text or not eom_text.strip():
            return CheckResult(
                check_id="CHK-EOM-01", tag="AUDIT_POINTER",
                component="Emphasis of Matter — Closing Sentence", passed=False,
                observation="Emphasis of Matter section was not found or is empty.",
                evidence="", risk_rating="Information request only",
            )
        patterns = [
            # "statements?" and "(the )?above" — real filings routinely say
            # "Financial Statement" (singular) and "in respect of above
            # matters" (no "the"); IRCTC FY24-25 does both, and the earlier
            # strict versions of these two clauses reported it as a SA 706
            # departure even though the sentence was present verbatim.
            r"our\s+opinion\s+(?:on\s+the\s+(?:standalone|consolidated)\s+)?financial\s+statements?\s+is\s+not\s+modified",
            r"not\s+modified\s+in\s+respect\s+of\s+(?:the\s+)?above\s+matters?",
            r"opinion\s+is\s+not\s+modified\s+in\s+respect",
        ]
        for pattern in patterns:
            match = re.search(pattern, eom_text, re.IGNORECASE)
            if match:
                start = max(0, match.start() - 30)
                end = min(len(eom_text), match.end() + 50)
                return CheckResult(
                    check_id="CHK-EOM-01", tag="FINDING",
                    component="Emphasis of Matter — Closing Sentence (SA 706.8)", passed=True,
                    observation="Mandatory SA 706.8 closing sentence is present.",
                    evidence=eom_text[start:end].strip(),
                )
        return CheckResult(
            check_id="CHK-EOM-01", tag="FINDING",
            component="Emphasis of Matter — Closing Sentence (SA 706.8)", passed=False,
            observation=(
                "The mandatory closing sentence required by SA 706.8 — 'Our opinion on the Financial "
                "Statements is not modified in respect of the above matters' — was NOT found. "
                "Its absence is a departure from SA 706."
            ),
            # Absence is still directly verifiable from the package (a reviewer
            # can read this same text and confirm the sentence isn't in it) —
            # so the evidence carries the searched text itself, not a quote of
            # the (absent) sentence. This is what keeps this a traceable
            # FINDING under the lineage rule in observation.py rather than
            # being downgraded to an AUDIT_POINTER for lack of a source trace.
            evidence=(eom_text[:500] + " …") if len(eom_text) > 500 else eom_text,
            risk_rating="High",
        )

    @staticmethod
    def check_kam_auditor_response(kam_items: list[dict]) -> CheckResult:
        """
        Checks the SA 701 structural requirement that every Key Audit Matter
        is paired with a description of how the audit addressed it.

        Unlike check_eom_closing_sentence, this runs against the extractor's
        structured `key_audit_matters.items[]` (each with a `how_addressed`
        field — see PROMPT.md) rather than raw text: SA 701's requirement is
        per-matter, not one closing sentence for the whole section, so there
        is no single fixed sentence a regex could anchor to the way SA 706.8
        gives EoM. KAM presence/applicability itself is a separate question,
        already handled by applicability.resolve_kam_applicability
        (APPL-KAM-01) — this check only runs once KAM is known to be present.
        """
        if not kam_items:
            return CheckResult(
                check_id="CHK-KAM-01", tag="AUDIT_POINTER",
                component="Key Audit Matters — Auditor's Response (SA 701)", passed=False,
                observation="Key Audit Matters section was marked present but no matters were extracted.",
                evidence="", risk_rating="Information request only",
            )

        missing = [
            (item.get("title") or f"item {i + 1}").strip() or f"item {i + 1}"
            for i, item in enumerate(kam_items)
            if not (item.get("how_addressed") or "").strip()
        ]
        if not missing:
            titles = "; ".join((item.get("title") or "").strip() for item in kam_items if item.get("title"))
            return CheckResult(
                check_id="CHK-KAM-01", tag="FINDING",
                component="Key Audit Matters — Auditor's Response (SA 701)", passed=True,
                observation="Every key audit matter is paired with a description of how the audit addressed it, per SA 701.",
                evidence=titles or f"{len(kam_items)} key audit matter(s) verified.",
            )
        return CheckResult(
            check_id="CHK-KAM-01", tag="FINDING",
            component="Key Audit Matters — Auditor's Response (SA 701)", passed=False,
            observation=(
                f"{len(missing)} of {len(kam_items)} key audit matter(s) have no 'how our audit "
                f"addressed the matter' description extracted: {', '.join(missing)}. SA 701 requires "
                f"each key audit matter to be paired with a description of the auditor's response."
            ),
            evidence=", ".join(missing),
            risk_rating="High",
        )

    @staticmethod
    def check_report_date_sequence(fs_approval_date: str, report_date: str) -> CheckResult:
        """
        Deterministic date-logic check (Gap-closure Phase 1, Gap #5 / source
        spec §30 "Date Logic" and §35.1's own example of a FINDING: "report
        date before approval date").

        Companies Act 2013 s.134(1): the financial statements must be
        approved by the Board before the auditor's report on them is dated.
        A report dated before that approval is a direct, package-internal
        contradiction — no external evidence is needed to see it, which is
        exactly the wiki's test for FINDING (§35.1).

        Deliberately does NOT apply any fixed numeric day-range benchmark —
        the source spec is explicit that SA 700 sets none (§30, and the
        writer prompt's own "Do NOT apply any fixed numeric day-range
        benchmark" instruction in PROMPT.md §2.9). That is a different,
        already-existing check: ComputeTools.compute_report_date_gap, which
        measures the FY-end-to-report-date gap for informational/timeliness
        purposes only and is not a substitute for this ordering check (nor
        vice versa) — see GAP_CLOSURE_LOG.md for why both exist.
        """
        approval = _parse_flexible_date(fs_approval_date)
        report = _parse_flexible_date(report_date)

        if not approval or not report:
            return CheckResult(
                check_id="CHK-DATE-01", tag="AUDIT_POINTER",
                component="Formal Checks — Report Date Sequence (s.134(1))", passed=False,
                observation=(
                    f"Could not verify the sequence of the report date and the financial "
                    f"statement approval date — one or both could not be parsed from the "
                    f"extracted text (fs_approval_date='{fs_approval_date}', report_date='{report_date}')."
                ),
                evidence="", risk_rating="Information request only",
            )

        if report < approval:
            return CheckResult(
                check_id="CHK-DATE-01", tag="FINDING",
                component="Formal Checks — Report Date Sequence (s.134(1))", passed=False,
                observation=(
                    f"The auditor's report is dated {report.isoformat()}, which is before the "
                    f"financial statements' approval date of {approval.isoformat()}. Under "
                    f"s.134(1) of the Companies Act 2013, the report cannot be dated earlier "
                    f"than the date the financial statements were approved."
                ),
                evidence=f"FS approval date: {approval.isoformat()} | Report date: {report.isoformat()}",
                risk_rating="High",
            )

        return CheckResult(
            check_id="CHK-DATE-01", tag="FINDING",
            component="Formal Checks — Report Date Sequence (s.134(1))", passed=True,
            observation=(
                f"The report date ({report.isoformat()}) is on or after the financial "
                f"statements' approval date ({approval.isoformat()}); no s.134(1) sequencing "
                f"issue identified."
            ),
            evidence=f"FS approval date: {approval.isoformat()} | Report date: {report.isoformat()}",
        )

    @staticmethod
    def check_opinion_coherence_signals(
        opinion_type: str,
        caro_adverse_count: int,
        ifc_has_material_weakness: bool,
        going_concern_distress_signals: list[str] | None = None,
    ) -> list[CheckResult]:
        """
        Deterministic coherence flag checks. Fire BEFORE LLM analysis.
        Returns list of triggered CheckResult objects.
        """
        results = []
        opinion_lower = (opinion_type or "").lower().strip()
        gc_signals = going_concern_distress_signals or []

        if opinion_lower == "unmodified" and caro_adverse_count > 0:
            results.append(CheckResult(
                check_id="CHK-COH-01", tag="FINDING",
                component="Opinion Coherence — Main Opinion vs CARO", passed=False,
                observation=(
                    f"The main audit opinion is Unmodified, but CARO 2020 contains "
                    f"{caro_adverse_count} adverse or unfavourable clause(s). "
                    f"An unmodified opinion typically implies no material misstatement; "
                    f"the presence of adverse CARO remarks requires explanation."
                ),
                evidence=f"Opinion type: {opinion_type} | CARO adverse clauses: {caro_adverse_count}",
                risk_rating="High",
            ))

        if opinion_lower == "unmodified" and ifc_has_material_weakness:
            results.append(CheckResult(
                check_id="CHK-COH-02", tag="FINDING",
                component="Opinion Coherence — Main Opinion vs IFC", passed=False,
                observation=(
                    "The main audit opinion is Unmodified, but the IFC report discloses a material "
                    "weakness. A material weakness may indicate a significant risk of material "
                    "misstatement. The auditor should have considered whether this affects the main opinion."
                ),
                evidence=f"Opinion type: {opinion_type} | IFC material weakness: present",
                risk_rating="High",
            ))

        if opinion_lower == "unmodified" and gc_signals:
            signals_text = "; ".join(gc_signals[:3])
            results.append(CheckResult(
                check_id="CHK-COH-03", tag="RISK_FLAG",
                component="Going Concern — Opinion vs Financial Distress", passed=False,
                observation=(
                    f"The main audit opinion is Unmodified with no going concern disclosure, but the "
                    f"financial statements show distress indicators: {signals_text}. "
                    f"Per SA 570, the auditor should have evaluated going concern."
                ),
                evidence=f"Distress signals: {signals_text}",
                risk_rating="High",
            ))

        return results


# ===========================================================================
# CLASS: ComputeTools — Pure Python financial computations
# ===========================================================================

class ComputeTools:
    """
    Computes derived values from already-fetched data.
    No DB calls, no LLM calls. Fast, testable, deterministic.
    Inputs: markdown table strings produced by FetchTools (after HTML→MD conversion).
    """

    @staticmethod
    def compute_net_worth(balance_sheet_md: str) -> dict:
        """
        Computes net worth from a Balance Sheet markdown/text table.
        Net Worth = Total Equity  OR  Share Capital + Reserves & Surplus.
        """
        if not balance_sheet_md or not balance_sheet_md.strip():
            return {
                "net_worth": None, "net_worth_sign": "unknown",
                "unit": "unknown", "method": "unknown",
                "note": "Balance sheet text was empty or not provided.",
            }

        unit = "Crores"
        for pat, label in [
            (r"(?i)(rs\.?\s*in\s*crore)", "Crores"),
            (r"(?i)(rs\.?\s*in\s*lakh)", "Lakhs"),
            (r"(?i)(in\s*million)", "Millions"),
        ]:
            if re.search(pat, balance_sheet_md):
                unit = label
                break

        total_eq = re.search(r"(?i)total\s+equity[^\n|]*\|?\s*([\d,]+\.?\d*)", balance_sheet_md)
        if total_eq:
            try:
                val = float(total_eq.group(1).replace(",", ""))
                sign = "positive" if val > 0 else ("negative" if val < 0 else "zero")
                return {
                    "net_worth": val, "net_worth_sign": sign, "unit": unit,
                    "method": "equity_sum", "note": f"Extracted from 'Total Equity' row: {val} {unit}",
                }
            except ValueError:
                pass

        sc_m = re.search(r"(?i)share\s+capital[^\n|]*\|?\s*([\d,]+\.?\d*)", balance_sheet_md)
        rs_m = re.search(
            r"(?i)(?:reserves?\s+(?:and|&)\s+surplus|other\s+equity)[^\n|]*\|?\s*([\d,]+\.?\d*)",
            balance_sheet_md,
        )
        if sc_m and rs_m:
            try:
                val = float(sc_m.group(1).replace(",", "")) + float(rs_m.group(1).replace(",", ""))
                sign = "positive" if val > 0 else ("negative" if val < 0 else "zero")
                return {
                    "net_worth": val, "net_worth_sign": sign, "unit": unit,
                    "method": "equity_sum", "note": f"Share Capital + Reserves = {val} {unit}",
                }
            except ValueError:
                pass

        return {
            "net_worth": None, "net_worth_sign": "unknown", "unit": unit,
            "method": "unknown",
            "note": "Could not extract net worth. Manual verification required.",
        }

    @staticmethod
    def compute_report_date_gap(fy_end_date: str, report_date: str) -> dict:
        """
        Calculates the gap in days between FY end and report signing date.
        Normal range: 60–90 days. PSUs with C&AG audit: 90–180 days.
        """
        fy_end = _parse_flexible_date(fy_end_date)
        rep = _parse_flexible_date(report_date)
        if not fy_end or not rep:
            return {
                "gap_days": None, "fy_end_date": fy_end_date, "report_date": report_date,
                "assessment": "unknown", "flag": True,
                "note": f"Could not parse dates: '{fy_end_date}', '{report_date}'",
            }

        gap = (rep - fy_end).days
        if gap < 0:
            return {
                "gap_days": gap, "fy_end_date": str(fy_end), "report_date": str(rep),
                "assessment": "premature", "flag": True,
                "note": f"Report date ({rep}) is BEFORE FY end ({fy_end}). FINDING.",
            }
        elif gap <= 90:
            return {
                "gap_days": gap, "fy_end_date": str(fy_end), "report_date": str(rep),
                "assessment": "normal", "flag": False,
                "note": f"Gap of {gap} days is within the normal 60–90 day range.",
            }
        elif gap <= 180:
            return {
                "gap_days": gap, "fy_end_date": str(fy_end), "report_date": str(rep),
                "assessment": "delayed", "flag": True,
                "note": f"Gap of {gap} days exceeds 90 days. Acceptable for PSU with C&AG audit.",
            }
        return {
            "gap_days": gap, "fy_end_date": str(fy_end), "report_date": str(rep),
            "assessment": "very_delayed", "flag": True,
            "note": f"Gap of {gap} days is unusually long (>180 days). Raise AUDIT_POINTER.",
        }

    @classmethod
    def compute_financial_ratios(
        cls, balance_sheet_md: str, pl_md: str = "", cash_flow_md: str = ""
    ) -> dict:
        """
        Computes key financial ratios for going concern distress detection.
        Returns: {current_ratio, debt_equity_ratio, operating_cash_flow_sign,
                  distress_signals, unit}
        """
        result = {
            "current_ratio": None, "debt_equity_ratio": None,
            "operating_cash_flow_sign": "unknown", "distress_signals": [], "unit": "unknown",
        }

        nw = cls.compute_net_worth(balance_sheet_md)
        result["unit"] = nw.get("unit", "unknown")
        if nw["net_worth_sign"] == "negative":
            result["distress_signals"].append(
                f"Negative net worth detected: {nw['net_worth']} {nw['unit']} ({nw['note']})"
            )

        ca_m = re.search(r"(?i)total\s+current\s+assets[^\n|]*\|?\s*([\d,]+\.?\d*)", balance_sheet_md)
        cl_m = re.search(r"(?i)total\s+current\s+liabilit[^\n|]*\|?\s*([\d,]+\.?\d*)", balance_sheet_md)
        if ca_m and cl_m:
            try:
                ca, cl = float(ca_m.group(1).replace(",", "")), float(cl_m.group(1).replace(",", ""))
                if cl > 0:
                    cr = round(ca / cl, 2)
                    result["current_ratio"] = cr
                    if cr < 1.0:
                        result["distress_signals"].append(
                            f"Current ratio below 1.0 ({cr}): current liabilities exceed current assets."
                        )
            except (ValueError, ZeroDivisionError):
                pass

        if cash_flow_md:
            ocf_m = re.search(
                r"(?i)(?:net\s+cash\s+(?:generated|used)\s+(?:from|in)\s+operating|"
                r"cash\s+flow\s+from\s+operating)[^\n|]*\|?\s*(\(?[\d,]+\.?\d*\)?)",
                cash_flow_md,
            )
            if ocf_m:
                ocf_str = ocf_m.group(1).replace(",", "")
                is_neg = ocf_str.startswith("(") or "used" in ocf_m.group(0).lower()
                try:
                    ocf_val = float(ocf_str.strip("()"))
                    if is_neg or ocf_val < 0:
                        result["operating_cash_flow_sign"] = "negative"
                        result["distress_signals"].append(f"Negative operating cash flow: {ocf_str}")
                    else:
                        result["operating_cash_flow_sign"] = "positive"
                except ValueError:
                    pass

        return result


# ===========================================================================
# CLASS: ReferenceTools — Vector similarity search on reference standards DB
# ===========================================================================

class ReferenceTools:
    """
    Retrieves chunks from the REFERENCE database via vector similarity search.
    Reference DB contains: SA 700 series, CARO 2020 framework, CAG directions,
    EAC opinions, Schedule III, Ind AS appendices.

    CONFIG (from environment):
      REFERENCE_DSN  — PostgreSQL DSN for reference DB
      EMBEDDING_BASE_URL — Base URL of embedding API endpoint
      EMBEDDING_MODEL    — Model name for embeddings (default: text-embedding-3-small)

    If REFERENCE_DSN is not set, all methods return empty strings gracefully.
    """

    # BUG FIX — this used to be a list of "standard_code" values
    # ("SA 700", "CARO_2020", ...) filtered against a single unified
    # `reference_chunks` table via `WHERE standard_code IN (...)`. That
    # table has never existed in this deployment's REFERENCE_DSN — confirmed
    # against a real live run (RVNL_2024_2025): every single call logged
    # "relation \"reference_chunks\" does not exist" and silently returned
    # empty context, meaning the writer has never actually received a real
    # SA 700/705 or CARO framework citation from this class. The real
    # database instead has one physical table per corpus (`sa_700_chunks`,
    # `cag_caro_chunks`, `cag_directions_chunks`, ...), each with its own
    # column names — no shared `standard_code`/`content`/`paragraph_no`
    # schema to filter across. `_vector_search` below now takes real table
    # names and looks up each one's column mapping in `_TABLE_SCHEMAS`,
    # rather than one WHERE-IN clause over a table that isn't there.
    #
    # There is no per-row "which SA number" column inside `sa_700_chunks`
    # (it holds the whole SA 700/705/706/701/570/720 series together) — the
    # query text itself is what narrows relevance via embedding similarity,
    # so one table name covers the whole "SA standards" corpus.
    _TABLE_SCHEMAS: dict[str, dict[str, str | None]] = {
        "sa_700_chunks": {"content": "chunk", "section": "section_title", "para": "para_no", "doc": "doc_name", "page": "page_no"},
        "cag_caro_chunks": {"content": "txt", "section": None, "para": None, "doc": "doc_name", "page": None},
        "cag_directions_chunks": {"content": "txt", "section": None, "para": None, "doc": "doc_name", "page": "page_no"},
    }
    _SA_TABLES = ["sa_700_chunks"]
    _CARO_FRAMEWORK_TABLES = ["cag_caro_chunks", "cag_directions_chunks"]

    _SAR_REPORT_SA_QUERIES = [
        "auditor opinion qualified adverse disclaimer modified SA 705 basis reporting unmodified",
        "emphasis of matter key audit matter going concern SA 706 SA 701 SA 570 material uncertainty",
    ]
    _SAR_REPORT_CARO_QUERIES = [
        "CARO 2020 clause adverse unfavourable answer loans default statutory dues fraud",
        "CARO internal financial controls material weakness reporting IFC",
    ]

    @staticmethod
    def _get_ref_db_conn():
        """Returns psycopg2 connection for reference DB. Returns None if not configured."""
        import psycopg2
        dsn = os.environ.get("REFERENCE_DSN", "")
        if not dsn:
            return None
        try:
            return psycopg2.connect(dsn)
        except Exception as exc:
            logger.warning("Could not connect to reference DB: %s", exc)
            return None

    @staticmethod
    def _embed(text: str) -> list[float] | None:
        """
        Calls the configured embedding endpoint.
        Returns None if EMBEDDING_BASE_URL is not set (graceful degradation).
        """
        base_url = os.environ.get("EMBEDDING_BASE_URL", "").rstrip("/")
        model = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
        if not base_url:
            return None
        try:
            import requests
            resp = requests.post(
                f"{base_url}/v1/embeddings",
                json={"input": text, "model": model},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]
        except Exception as exc:
            logger.warning("Embedding call failed: %s", exc)
            return None

    @classmethod
    def _vector_search(cls, query: str, tables: list[str], top_k: int = 5) -> list[dict]:
        """Vector similarity search on the reference DB, across one or more
        real physical tables (see `_TABLE_SCHEMAS` — each table is queried
        separately since they don't share column names, then merged and
        re-sorted by score). Returns empty list if unavailable, and silently
        skips any table name not in `_TABLE_SCHEMAS` rather than raising —
        a typo here should degrade like "no results", not crash the caller.
        """
        conn = cls._get_ref_db_conn()
        if not conn:
            return []

        query_vec = cls._embed(query)
        if not query_vec:
            conn.close()
            return []

        all_rows: list[dict] = []
        try:
            vec_literal = "[" + ",".join(str(v) for v in query_vec) + "]"
            with conn.cursor() as cur:
                for table in tables:
                    schema = cls._TABLE_SCHEMAS.get(table)
                    if not schema:
                        logger.warning("ReferenceTools: unknown reference table %r — skipped.", table)
                        continue
                    section_expr = schema["section"] or "NULL"
                    para_expr = schema["para"] or "NULL"
                    page_expr = schema["page"] or "NULL"
                    sql = f"""
                        SELECT {schema['doc']}, {section_expr}, {para_expr}, {schema['content']},
                               {page_expr}, 1 - (embedding <=> %s::vector) AS score
                        FROM {table}
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s
                    """
                    try:
                        cur.execute(sql, [vec_literal, vec_literal, top_k])
                        for r in cur.fetchall():
                            all_rows.append({
                                "standard_code": table, "source_doc": r[0], "section_title": r[1],
                                "paragraph_no": r[2], "content": r[3], "page_no": r[4], "score": r[5],
                            })
                    except Exception as exc:
                        logger.warning("Reference vector search failed for table %s: %s", table, exc)
                        conn.rollback()  # a failed statement poisons the transaction for the next table otherwise
            all_rows.sort(key=lambda r: r.get("score", 0), reverse=True)
            return all_rows[:top_k]
        finally:
            conn.close()

    @staticmethod
    def _format_blocks(rows: list[dict], cap: int = 1200) -> str:
        if not rows:
            return "Reference context not available (REFERENCE_DSN not configured, or no matching chunks found)."
        blocks = []
        for r in rows:
            para = r.get("paragraph_no") or ""
            sec = r.get("section_title") or ""
            std = r.get("standard_code") or "Reference"
            cite = f"[{std}{', ' + sec if sec else ''}{', Para ' + str(para) if para else ''}]"
            body = (r.get("content") or "").strip()
            if len(body) > cap:
                body = body[:cap].rstrip() + " …"
            blocks.append(f"{cite}\n{body}")
        return "\n\n---\n\n".join(blocks)

    @classmethod
    def get_sar_report_sa_context(cls, top_k_per_query: int = 4) -> str:
        """
        Fetches pre-defined SA standard reference chunks for SAR report generation.
        Runs 2 targeted queries, deduplicates, returns combined result.
        Returns empty fallback string if reference DB is not configured.
        """
        all_rows, seen = [], set()
        for query in cls._SAR_REPORT_SA_QUERIES:
            for row in cls._vector_search(query, cls._SA_TABLES, top_k_per_query):
                uid = (row.get("standard_code"), row.get("paragraph_no"))
                if uid not in seen:
                    seen.add(uid)
                    all_rows.append(row)
        all_rows.sort(key=lambda r: r.get("score", 0), reverse=True)
        return cls._format_blocks(all_rows[:top_k_per_query * 2])

    @classmethod
    def get_sar_report_caro_context(cls, top_k_per_query: int = 4) -> str:
        """
        Fetches pre-defined CARO framework reference chunks for SAR report generation.
        Returns empty fallback string if reference DB is not configured.
        """
        all_rows, seen = [], set()
        for query in cls._SAR_REPORT_CARO_QUERIES:
            for row in cls._vector_search(query, cls._CARO_FRAMEWORK_TABLES, top_k_per_query):
                uid = (row.get("standard_code"), row.get("paragraph_no"))
                if uid not in seen:
                    seen.add(uid)
                    all_rows.append(row)
        all_rows.sort(key=lambda r: r.get("score", 0), reverse=True)
        return cls._format_blocks(all_rows[:top_k_per_query * 2])

    @classmethod
    def fetch_sa_standard_chunks(
        cls, query: str, tables: list[str] | None = None, top_k: int = 5
    ) -> str:
        """Public API: retrieve relevant SA standard chunks for a specific query."""
        rows = cls._vector_search(query, tables or cls._SA_TABLES, top_k)
        return cls._format_blocks(rows)

    @classmethod
    def fetch_caro_framework_chunks(cls, query: str, top_k: int = 5) -> str:
        """Public API: retrieve relevant CARO 2020 / CAG directions chunks."""
        rows = cls._vector_search(query, cls._CARO_FRAMEWORK_TABLES, top_k)
        return cls._format_blocks(rows)

    # ---------------------------------------------------------------------------
    # C&AG §143(5) directions — date-window applicability (Gap-closure Phase 5,
    # Gap #2). See cag_directions_engine.py's module docstring for why the
    # match key is the auditor's REPORT DATE, not the entity or FY: this is a
    # standing government-wide document, not an entity-specific one, and its
    # own text (chunk 7 in the live data) states its own applicability window.
    # ---------------------------------------------------------------------------

    @classmethod
    def fetch_cag_directions_for_date(cls, report_date_str: str) -> list[dict]:
        """Returns every direction chunk in `cag_directions_chunks` whose
        [effective_from, effective_to] window covers `report_date_str`.
        Empty list (never an error) when REFERENCE_DSN is unset, the
        connection fails, the date can't be parsed, or nothing covers it —
        all three are "nothing to test against", not failures.
        """
        report_date = _parse_flexible_date(report_date_str)
        if not report_date:
            return []

        conn = cls._get_ref_db_conn()
        if not conn:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT chunk_id, doc_name, page_no, txt, effective_from, effective_to
                    FROM cag_directions_chunks
                    WHERE effective_from IS NOT NULL
                      AND effective_from::date <= %s
                      AND (effective_to IS NULL OR effective_to::date >= %s)
                    ORDER BY chunk_id
                    """,
                    [report_date, report_date],
                )
                rows = cur.fetchall()
            return [
                {
                    "chunk_id": r[0], "doc_name": r[1], "page_no": r[2], "txt": r[3],
                    "effective_from": r[4], "effective_to": r[5],
                }
                for r in rows
            ]
        except Exception as exc:
            logger.warning("fetch_cag_directions_for_date failed: %s", exc)
            return []
        finally:
            conn.close()
