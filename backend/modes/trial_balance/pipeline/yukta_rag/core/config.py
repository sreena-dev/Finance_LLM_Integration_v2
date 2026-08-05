"""Configuration for the yukta_rag system (env-overridable)."""

from __future__ import annotations

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # python-dotenv optional
    pass

# Postgres holding the bge-m3 embedded chunks (annual reports + Ind AS).
FINANCE_DSN = os.getenv(
    "FINANCE_LLM_DSN",
    "postgresql://bfl:nU6e4WHM8sksLrLPB4Xl@192.168.200.29:5478/finance_llm",
)

# Reference corpora DB (bge-m3 embedded): EAC opinions, SA 700, Schedule III,
# CARO/CAG and Ind AS appendices. A separate Postgres from FINANCE_DSN.
REFERENCE_DSN = os.getenv(
    "REFERENCE_DSN",
    "postgresql://financial_llm:financial_llm_dev_pass@192.168.200.29:11352/financial_llm",
)

# Separate LOCAL Postgres (the docker-compose pgvector instance, port 5433) that
# holds user-uploaded PDFs chunked page-by-page. Kept apart from the remote
# corpora above. UPLOADS_ADMIN_DSN points at an existing database on the same
# instance, used only to CREATE DATABASE finance_uploads the first time.
UPLOADS_DSN = os.getenv(
    "UPLOADS_DSN", "postgresql://rag:rag@localhost:5433/finance_uploads"
)
UPLOADS_ADMIN_DSN = os.getenv(
    "UPLOADS_ADMIN_DSN", "postgresql://rag:rag@localhost:5433/financial_llm"
)

# How many uploaded page-chunks to retrieve per query, and the per-page char
# ceiling above which a page is split into sub-chunks so bge-m3 (~8k tokens)
# doesn't silently truncate a very dense page.
UPLOAD_TOP_K = int(os.getenv("UPLOAD_TOP_K", os.getenv("DEFAULT_TOP_K", "5")))
UPLOAD_MAX_PAGE_CHARS = int(os.getenv("UPLOAD_MAX_PAGE_CHARS", "6000"))

# bge-m3 embedding server (1024-dim).
EMBEDDING_URL = os.getenv("EMBEDDING_URL", "http://192.168.200.22:11450/v1/embeddings")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "bge-m3")

# OpenAI-compatible generation endpoint (gemma).
#
# PATCHED for this integration: the Integrated gateway's Statutory Auditor's
# Report mode already occupies the generic GENERATION_BASE_URL/GENERATION_MODEL
# env var names (pointing at a different endpoint/model). Reading those names
# unchanged here would silently make Trial Balance run on SAR's LLM instead of
# its own. TB_GENERATION_BASE_URL/TB_GENERATION_MODEL are TB-specific and take
# priority; the branch's own hardcoded defaults remain the fallback so this
# still works standalone with no env vars set at all.
GENERATION_BASE_URL = os.getenv("TB_GENERATION_BASE_URL", "http://10.10.116.160:11632/v1")
GENERATION_MODEL = os.getenv("TB_GENERATION_MODEL", "gemma-4-12b-it")

# The generation model's context window and the tokens reserved for its output.
# Set GENERATION_CONTEXT_TOKENS to the real window when swapping in a new model.
GENERATION_CONTEXT_TOKENS = int(os.getenv("GENERATION_CONTEXT_TOKENS", "16384"))
GENERATION_MAX_OUTPUT_TOKENS = int(os.getenv("GENERATION_MAX_OUTPUT_TOKENS", "1536"))

# HTTP read timeout (seconds) for a single generation call. Slow local models
# (large prefill + many output tokens) can exceed the client's 300s default and
# fail with ReadTimeout — raise this so the answer completes.
GENERATION_TIMEOUT = int(os.getenv("GENERATION_TIMEOUT", "900"))

# Shorter timeout for the FDR statement-extraction calls (small structured-JSON
# extractions against one table, not the large freeform generations GENERATION_TIMEOUT
# is sized for). A single-year extraction runs without caching now, so a flaky call
# retried at the full 900s budget can chain past nginx's proxy_read_timeout before the
# pipeline even gives up on one statement/year — this bounds that worst case tightly.
FDR_EXTRACTOR_TIMEOUT = int(os.getenv("FDR_EXTRACTOR_TIMEOUT", "120"))

TOP_K = int(os.getenv("DEFAULT_TOP_K", "5"))
