-- ============================================================
-- finance_uploads — user-uploaded PDFs, chunked page-by-page.
-- Lives in the LOCAL docker pgvector instance (separate from the
-- remote finance_llm corpora). Applied idempotently at app startup
-- by yukta_rag.uploads.ensure_uploads_schema().
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- One row per uploaded PDF.
CREATE TABLE IF NOT EXISTS uploaded_documents (
    doc_id       VARCHAR(80)  PRIMARY KEY,   -- "UP-<sha1(file)[:16]>" (stable per file)
    filename     VARCHAR(255) NOT NULL,
    total_pages  INT          NOT NULL,      -- pages in the PDF
    total_chunks INT          NOT NULL,      -- page-chunks stored (non-empty pages, post-split)
    uploaded_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- One row per page-chunk (page-wise chunking; very large pages are split).
CREATE TABLE IF NOT EXISTS uploaded_page_chunks (
    chunk_id   VARCHAR(120) PRIMARY KEY,     -- "<doc_id>_p0001" (or "..._p0001_b" for splits)
    doc_id     VARCHAR(80)  NOT NULL REFERENCES uploaded_documents(doc_id) ON DELETE CASCADE,
    page_no    INT          NOT NULL,        -- 1-based PDF page number
    text       TEXT         NOT NULL,
    embedding  VECTOR(1024),                 -- bge-m3 (1024-dim), NULL until embedded
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_uploaded_page_chunks_doc ON uploaded_page_chunks (doc_id);

-- NOTE: parsed trial-balance input has moved to the server ``finance_llm`` DB
-- (table ``tb_input_data``, created by
-- yukta_rag.trial_balance.trial_balance.ensure_tb_input_schema). It is no longer
-- stored here; only uploaded PDFs live in this local database.
