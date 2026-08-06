"""User-uploaded PDFs: page-wise chunking, embedding, storage and retrieval.

This is the self-contained "third corpus". Uploaded PDFs are chunked one chunk
per page (very dense pages are split), embedded with the same bge-m3 server as
the main corpora, and stored in a SEPARATE local Postgres database
(``finance_uploads`` on the docker pgvector instance) — kept apart from the
remote Ind AS / annual-report corpora. The pipeline blends retrieved page chunks
into its answer alongside the two existing corpora.
"""

from __future__ import annotations

import hashlib
import os
from urllib.parse import urlparse

import fitz  # PyMuPDF
import psycopg2
import psycopg2.extras

from yukta_rag.core.config import (
    UPLOAD_MAX_PAGE_CHARS,
    UPLOADS_ADMIN_DSN,
    UPLOADS_DSN,
)
from yukta_rag.core.embeddings import embed_texts

BATCH_SIZE = 32
_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "uploads_schema.sql")


# ---------------------------------------------------------------------------
# Connections / schema bootstrap
# ---------------------------------------------------------------------------


def get_uploads_connection():
    """Open a new psycopg2 connection to the local finance_uploads database."""
    return psycopg2.connect(UPLOADS_DSN)


def ensure_uploads_schema() -> None:
    """Create the finance_uploads database (if missing) and its tables.

    Idempotent and safe to call on every startup. Docker init scripts only run
    against a fresh volume, so this runtime bootstrap is the reliable path on an
    already-initialised instance. Raises if the local Postgres is unreachable.
    """
    try:
        conn = get_uploads_connection()
    except psycopg2.OperationalError as exc:
        # "database does not exist" -> create it via the admin DB; re-raise
        # anything else (server down, bad credentials) for the caller to handle.
        if "does not exist" not in str(exc):
            raise
        _create_uploads_database()
        conn = get_uploads_connection()

    try:
        with open(_SCHEMA_PATH, encoding="utf-8") as f:
            schema_sql = f.read()
        with conn.cursor() as cur:
            cur.execute(schema_sql)
        conn.commit()
    finally:
        conn.close()


def _create_uploads_database() -> None:
    """CREATE DATABASE <finance_uploads> on the instance, via the admin DB."""
    dbname = urlparse(UPLOADS_DSN).path.lstrip("/")
    if not dbname:
        raise ValueError("UPLOADS_DSN has no database name")
    admin = psycopg2.connect(UPLOADS_ADMIN_DSN)
    try:
        admin.autocommit = True  # CREATE DATABASE cannot run in a transaction
        with admin.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
            if cur.fetchone() is not None:
                return
            try:
                cur.execute(f'CREATE DATABASE "{dbname}"')
            except psycopg2.Error as exc:
                # A docker volume first initialised by a different glibc than the
                # running image leaves template1 with a stale collation version,
                # which makes CREATE DATABASE fail. Refresh the template's recorded
                # version — the metadata-only fix Postgres itself recommends — and
                # retry once. Only triggers on that specific error.
                if "collation version mismatch" not in str(exc):
                    raise
                cur.execute("ALTER DATABASE template1 REFRESH COLLATION VERSION")
                cur.execute(f'CREATE DATABASE "{dbname}"')
    finally:
        admin.close()


# ---------------------------------------------------------------------------
# Page-wise chunking
# ---------------------------------------------------------------------------


def _split_page(text: str, max_chars: int) -> list[str]:
    """Split an over-long page into sub-chunks on paragraph boundaries.

    Keeps whole paragraphs together where possible so a sub-chunk stays coherent;
    a single paragraph longer than ``max_chars`` is hard-sliced as a last resort.
    """
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    cur = ""
    for para in text.split("\n\n"):
        if cur and len(cur) + len(para) + 2 > max_chars:
            parts.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        parts.append(cur)
    out: list[str] = []
    for p in parts:
        if len(p) <= max_chars:
            out.append(p)
        else:
            out.extend(p[i : i + max_chars] for i in range(0, len(p), max_chars))
    return out


def extract_page_chunks(pdf_bytes: bytes) -> tuple[list[dict], int]:
    """Return ``(chunks, total_pages)`` from a PDF's embedded text layer.

    One chunk per non-empty page (page-wise); a page longer than
    ``UPLOAD_MAX_PAGE_CHARS`` becomes several sub-chunks tagged ``suffix`` a/b/…
    Empty (e.g. scanned, image-only) pages yield no chunk — OCR is out of scope.
    Each chunk: ``{"page_no": int, "suffix": str, "text": str}``.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        total_pages = doc.page_count
        chunks: list[dict] = []
        for i in range(total_pages):
            text = (doc[i].get_text("text") or "").strip()
            if not text:
                continue
            page_no = i + 1
            parts = _split_page(text, UPLOAD_MAX_PAGE_CHARS)
            if len(parts) == 1:
                chunks.append({"page_no": page_no, "suffix": "", "text": parts[0]})
            else:
                for j, part in enumerate(parts):
                    suffix = chr(ord("a") + j) if j < 26 else f"z{j}"
                    chunks.append({"page_no": page_no, "suffix": suffix, "text": part})
    finally:
        doc.close()
    return chunks, total_pages


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

_UPSERT_DOC = """
INSERT INTO uploaded_documents (doc_id, filename, total_pages, total_chunks)
VALUES (%(doc_id)s, %(filename)s, %(total_pages)s, %(total_chunks)s)
ON CONFLICT (doc_id) DO UPDATE SET
    filename = EXCLUDED.filename,
    total_pages = EXCLUDED.total_pages,
    total_chunks = EXCLUDED.total_chunks,
    uploaded_at = now()
"""

_INSERT_CHUNKS = """
INSERT INTO uploaded_page_chunks (chunk_id, doc_id, page_no, text, embedding)
VALUES %s
ON CONFLICT (chunk_id) DO NOTHING
"""
_CHUNK_TEMPLATE = "(%(chunk_id)s, %(doc_id)s, %(page_no)s, %(text)s, %(embedding)s::vector)"


def _vec(emb: list[float]) -> str:
    return "[" + ",".join(str(x) for x in emb) + "]"


def ingest_uploaded_pdf(filename: str, data: bytes) -> dict:
    """Chunk a PDF page-by-page, embed each chunk and store it locally.

    ``doc_id`` is derived from the file content (sha1) so re-uploading the same
    file is idempotent — its chunks are replaced rather than duplicated. Returns
    ``{doc_id, filename, pages, chunks, pages_with_text}``.
    """
    doc_id = "UP-" + hashlib.sha1(data).hexdigest()[:16]
    page_chunks, total_pages = extract_page_chunks(data)
    pages_with_text = len({c["page_no"] for c in page_chunks})

    conn = get_uploads_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(_UPSERT_DOC, {
                "doc_id": doc_id,
                "filename": filename[:255],
                "total_pages": total_pages,
                "total_chunks": len(page_chunks),
            })
            # idempotent re-ingest: drop any prior chunks for this file
            cur.execute("DELETE FROM uploaded_page_chunks WHERE doc_id = %s", (doc_id,))

            for i in range(0, len(page_chunks), BATCH_SIZE):
                batch = page_chunks[i : i + BATCH_SIZE]
                embeddings = embed_texts([c["text"] for c in batch])
                rows = []
                for c, emb in zip(batch, embeddings):
                    suffix = f"_{c['suffix']}" if c["suffix"] else ""
                    rows.append({
                        "chunk_id": f"{doc_id}_p{c['page_no']:04d}{suffix}",
                        "doc_id": doc_id,
                        "page_no": c["page_no"],
                        "text": c["text"],
                        "embedding": _vec(emb),
                    })
                psycopg2.extras.execute_values(
                    cur, _INSERT_CHUNKS, rows, template=_CHUNK_TEMPLATE
                )
        conn.commit()
    finally:
        conn.close()

    return {
        "doc_id": doc_id,
        "filename": filename,
        "pages": total_pages,
        "chunks": len(page_chunks),
        "pages_with_text": pages_with_text,
    }


# ---------------------------------------------------------------------------
# Retrieval / management
# ---------------------------------------------------------------------------

_RETRIEVE_SQL = """
    SELECT d.filename, c.doc_id, c.page_no, c.text,
           1 - (c.embedding <=> %s::vector) AS score
    FROM uploaded_page_chunks c
    JOIN uploaded_documents d ON d.doc_id = c.doc_id
    WHERE c.embedding IS NOT NULL AND c.doc_id = ANY(%s)
    ORDER BY c.embedding <=> %s::vector
    LIMIT %s
"""


def retrieve_uploaded(query: str, doc_ids: list[str], top_k: int = 5) -> list[dict]:
    """Dense pgvector search over uploaded page chunks, scoped to ``doc_ids``."""
    if not doc_ids:
        return []
    from yukta_rag.core.embeddings import to_vector_literal

    vec = to_vector_literal(query)
    conn = get_uploads_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(_RETRIEVE_SQL, (vec, list(doc_ids), vec, top_k))
            cols = [c.name for c in cur.description]
            rows = cur.fetchall()
    finally:
        conn.close()
    return [dict(zip(cols, r)) for r in rows]


def list_uploaded_docs() -> list[dict]:
    """All uploaded documents, newest first."""
    conn = get_uploads_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT doc_id, filename, total_pages, total_chunks, uploaded_at "
                "FROM uploaded_documents ORDER BY uploaded_at DESC"
            )
            cols = [c.name for c in cur.description]
            rows = cur.fetchall()
    finally:
        conn.close()
    return [dict(zip(cols, r)) for r in rows]


def delete_uploaded_doc(doc_id: str) -> bool:
    """Delete an uploaded document and its chunks (FK cascade). True if removed."""
    conn = get_uploads_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM uploaded_documents WHERE doc_id = %s", (doc_id,))
            deleted = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return deleted > 0
