"""
Single source of truth for every `finance_llm` table/column name the flow touches.

WHY THIS FILE EXISTS: the DB schema differs between the old KB and this one, and
will change again on migration. Every SQL string in the package references these
constants instead of literals, so adapting to a new schema is a one-file edit.

Schema mapped live on 2026-07-22 against finance_llm @ 192.168.200.29:5478 (PG 16).
See SCHEMA.md for the human-readable table→flow mapping.
"""
from __future__ import annotations

# ---- documents: annual-report registry (req 1 entity/period, req 2 quality) ----
DOCUMENTS = "documents"
DOC = dict(
    id="doc_id", company="company", fy_start="fy_start", fy_end="fy_end",
    name="doc_name", pages_ocr="total_pages_ocr", pages_pdf="total_pages_pdf",
    total_chunks="total_chunks", total_tables="total_tables",
    toc="toc_sections", pdf_key="pdf_minio_key", pdf_bucket="pdf_minio_bucket",
)

# ---- table_chunks: extracted tables incl. the primary statements & note schedules ----
TABLE_CHUNKS = "table_chunks"
TBL = dict(
    id="table_id", doc="doc_id", title="table_title", desc="table_description",
    section="section", toc_section="toc_section", ttype="table_type",
    is_financial="is_financial", stmt_type="financial_stmt_type",
    col_headers="column_headers", row_count="row_count", unit="unit",
    currency="currency", note_refs="note_refs", md="table_md",
    page_pdf_start="page_pdf_start", page_pdf_end="page_pdf_end",
    page_ocr_start="page_ocr_start", page_ocr_end="page_ocr_end",
)

# financial_stmt_type controlled values observed in this DB
STMT_BS = "balance_sheet"
STMT_PL = "profit_loss"
STMT_CF = "cash_flow"
STMT_SOCE = "statement_of_equity"
PRIMARY_STMTS = [STMT_BS, STMT_PL, STMT_CF, STMT_SOCE]

# ---- text_chunks: narrative (auditor report / CARO / accounting policies / notes prose) ----
TEXT_CHUNKS = "text_chunks"
TXT = dict(
    id="chunk_id", doc="doc_id", section="section", toc_section="toc_section",
    title="title", ctype="chunk_type", content="content",
    note_refs="note_refs", table_refs="table_refs",
    page_pdf_start="page_pdf_start", page_ocr_start="page_ocr_start",
)

# ---- ind_as_*: Ind AS standards corpus (req 4 / Part-A compliance grounding) ----
IND_AS_CHUNKS = "ind_as_chunks"
IND_AS_DOCS = "ind_as_documents"
INDAS = dict(
    chunk_id="chunk_id", doc="doc_id", seq="seq", section_title="section_title",
    paragraph_no="paragraph_no", page="page_no", text="text",
    std_no="standard_number", std_cat="standard_category", title="document_title",
)
