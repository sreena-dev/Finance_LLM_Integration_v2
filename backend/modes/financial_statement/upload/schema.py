"""The four tables that hold an uploaded document's extraction.

    python -m modes.financial_statement.upload.schema          create, then report
    python -m modes.financial_statement.upload.schema --check  report only

WHAT IS STORED, AND WHAT IS NOT
--------------------------------
The **extraction** is stored. The **uploaded PDF is not**, here or anywhere:
`ingestion/app/jobs.py` drops the bytes the moment the conversion job ends and
there is no path that writes them to disk, Redis or Postgres. What lands here
is what the tools actually read -- markdown tables, narrative chunks, page
images, and the quality report -- plus the sha256 of the source file, which is
a digest and not the file.

That distinction is the whole design. The FS tools never open a PDF; they read
`table_md` strings and text chunks through the ~18 functions `bridge.py`
rebinds. Persisting the file would buy nothing for tool access, while putting
original C&AG filings into every database backup.

WHY THIS EXISTS AT ALL
-----------------------
Redis alone could not do it. An extraction lived on a 2-hour TTL, and when it
lapsed there was no recovery: the source PDF was already gone, so the user had
to re-upload and pay for a full re-conversion -- minutes of OCR. Raising the
Redis TTL to 30 days is not an option either: Redis runs `maxmemory 3gb` with
`maxmemory-policy noeviction`, one document is several MB of mostly base64 page
images, so a few dozen users' retained documents fill the instance and Redis
then *rejects new uploads* rather than evicting old ones.

So Postgres is the system of record and Redis stays the hot cache.

WHY FOUR TABLES AND NOT ONE
----------------------------
The child table for tables mirrors `public.table_chunks` column-for-column.
That is deliberate and load-bearing: `ingestion/app/emit.py` already emits
exactly those column names so the existing corpus check library can read an
upload unchanged, and keeping the names means a future cross-document query is
the corpus SQL with one table name changed.

Page images get their own table because they are the megabytes. Isolating them
keeps `SELECT table_md ...` off them entirely.

WHY THE DDL LIVES IN A PYTHON STRING
-------------------------------------
This repo has no migration tooling -- no alembic, no .sql files. The precedents
are `app/auth/schema.py` and `fdr/facts_store.py`, both holding idempotent DDL
as a module-level string executed on first use. This follows that shape so
there is one convention rather than three.

WHY IT RUNS LAZILY AND NOT AT IMPORT
-------------------------------------
A database blip during boot must not take down the gateway. Running on first
use means the gateway always boots and only uploads report a problem.

WHY doc_id IS NOT THE PRIMARY KEY
----------------------------------
`doc_id` is content-addressed -- `up_<sha256(pdf)[:16]>` (`emit.py`) -- so the
same filing uploaded by two different users yields the *same* doc_id. As a
primary key it would make one user's upload collide with another's. The unique
constraint is `(user_id, conversation_id, doc_id)`, which is exactly the Redis
key `fs:doc:{user_id}:{conversation_id}:{doc_id}` this mirrors, and `user_id`
is in the WHERE clause of every read and delete rather than checked afterwards
in Python -- the same discipline `conversations.py` applies to chat history.
"""

from __future__ import annotations

import logging
import threading

from app.auth.db import AuthDBError, db_cursor

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_DDL = """
-- One row per uploaded document. NOT per package: packages are derived at read
-- time by group_into_packages(), and must stay that way so a correction to the
-- grouping rule never requires rewriting stored rows.
CREATE TABLE IF NOT EXISTS public.financial_statement_live_ingestion (
    ingestion_id      uuid        PRIMARY KEY,
    doc_id            text        NOT NULL,
    user_id           text        NOT NULL,
    conversation_id   text        NOT NULL,
    filename          text        NOT NULL,
    -- The digest of the PDF. THE BYTES ARE NOT STORED. This is what makes a
    -- re-upload of the same file resolve to the same doc_id rather than
    -- duplicating its tables.
    sha256            text,

    -- Denormalised out of `identification` because every lookup filters on
    -- them. financial_year is stored EXACTLY as identify.py read it off the
    -- statement ('2023-24'); reconciling other spellings is Scope._fy_key's
    -- job at comparison time, never a rewrite of this column.
    company           text,
    financial_year    text,
    fy_start          integer,
    fy_end            integer,
    framework         text,
    statement_flavour text,

    -- The three dicts the tools read wholesale. jsonb rather than columns:
    -- their shape is the ingestion service's contract and moves with the
    -- pipeline, and nothing queries inside them.
    document          jsonb       NOT NULL,
    identification    jsonb       NOT NULL,
    quality           jsonb       NOT NULL,

    -- Which pipeline produced this. A 30-day-old row may predate an accuracy
    -- fix, and without this there is no way to tell a stale extraction from a
    -- current one, or to target a re-ingestion when one lands.
    ingest_version    text,

    uploaded_at       timestamptz NOT NULL DEFAULT now(),
    expires_at        timestamptz NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS fsli_scope_doc_uq
    ON public.financial_statement_live_ingestion (user_id, conversation_id, doc_id);
CREATE INDEX IF NOT EXISTS fsli_conv_idx
    ON public.financial_statement_live_ingestion (user_id, conversation_id, uploaded_at);
CREATE INDEX IF NOT EXISTS fsli_expiry_idx
    ON public.financial_statement_live_ingestion (expires_at);


-- Mirrors public.table_chunks column-for-column, plus the upload-only fields
-- the corpus has no equivalent for. See ingestion emit.TableRecord.
CREATE TABLE IF NOT EXISTS public.financial_statement_live_ingestion_tables (
    ingestion_id        uuid    NOT NULL
                        REFERENCES public.financial_statement_live_ingestion(ingestion_id)
                        ON DELETE CASCADE,
    table_id            text    NOT NULL,
    doc_id              text    NOT NULL,
    table_title         text,
    table_description   text,
    table_md            text,
    page_ocr_start      integer,
    page_ocr_end        integer,
    -- balance_sheet | profit_loss | cash_flow | statement_of_equity. Anything
    -- else and _find_statement_tables matches nothing and the document reports
    -- as having no financial statements at all.
    financial_stmt_type text,
    toc_section         text,
    unit                text,
    currency            text,
    note_refs           jsonb   NOT NULL DEFAULT '[]'::jsonb,
    is_financial        boolean NOT NULL DEFAULT false,
    source_file         text,
    bbox                jsonb,
    confidence          double precision,
    vlm_agreement       double precision,
    -- TOASTed out of line by Postgres and never read unless selected, which
    -- the hot query shape does not do.
    snippet_jpeg_b64    text,
    findings            jsonb   NOT NULL DEFAULT '[]'::jsonb,
    footings            jsonb   NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (ingestion_id, table_id)
);

CREATE INDEX IF NOT EXISTS fsli_tables_stmt_idx
    ON public.financial_statement_live_ingestion_tables (ingestion_id, financial_stmt_type);


-- Mirrors public.text_chunks. section_breadcrumb is load-bearing: the policy
-- lookups filter on it matching '%notes to%', and an empty one silently
-- excludes a document from policy-note lookup entirely.
CREATE TABLE IF NOT EXISTS public.financial_statement_live_ingestion_texts (
    ingestion_id       uuid    NOT NULL
                       REFERENCES public.financial_statement_live_ingestion(ingestion_id)
                       ON DELETE CASCADE,
    chunk_id           text    NOT NULL,
    doc_id             text    NOT NULL,
    content            text,
    -- THIS is the page number every FS query selects, orders by and cites --
    -- not page_pdf_start, whose offset swings by hundreds within one document.
    page_ocr_start     integer,
    page_pdf_start     integer,
    section            text,
    title              text,
    chunk_type         text    NOT NULL DEFAULT 'text',
    section_breadcrumb jsonb   NOT NULL DEFAULT '[]'::jsonb,
    note_refs          jsonb   NOT NULL DEFAULT '[]'::jsonb,
    bbox               jsonb,
    source_file        text,
    PRIMARY KEY (ingestion_id, chunk_id)
);

-- Passage assembly orders on exactly this tuple
-- (DocumentResolver._fetch_following_chunks).
CREATE INDEX IF NOT EXISTS fsli_texts_order_idx
    ON public.financial_statement_live_ingestion_texts
       (ingestion_id, page_ocr_start, chunk_id);


-- The megabytes, deliberately isolated so no statement query ever touches
-- them. Only the document pane's page view reads this table.
CREATE TABLE IF NOT EXISTS public.financial_statement_live_ingestion_pages (
    ingestion_id   uuid    NOT NULL
                   REFERENCES public.financial_statement_live_ingestion(ingestion_id)
                   ON DELETE CASCADE,
    page_no        integer NOT NULL,
    image_jpeg_b64 text,
    width_px       integer NOT NULL DEFAULT 0,
    height_px      integer NOT NULL DEFAULT 0,
    PRIMARY KEY (ingestion_id, page_no)
);
"""

TABLES = (
    "financial_statement_live_ingestion",
    "financial_statement_live_ingestion_tables",
    "financial_statement_live_ingestion_texts",
    "financial_statement_live_ingestion_pages",
)

_ready = False
_lock = threading.Lock()


def _can_create(cur) -> bool:
    cur.execute(
        "SELECT has_database_privilege(current_user, current_database(), 'CREATE')"
    )
    row = cur.fetchone()
    return bool(row[0] if not isinstance(row, dict) else list(row.values())[0])


def existing_tables() -> list[str]:
    """Which of our four tables are already present."""
    with db_cursor(dict_rows=False) as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = ANY(%s)",
            (list(TABLES),),
        )
        return sorted(r[0] for r in cur.fetchall())


def ensure_schema(force: bool = False) -> None:
    """Create the four tables if missing. Safe to call repeatedly.

    Guarded so the DDL is attempted once per process; a `CREATE TABLE IF NOT
    EXISTS` on every upload would be harmless but pointless traffic.
    """
    global _ready
    if _ready and not force:
        return

    with _lock:
        if _ready and not force:
            return

        present = set(existing_tables())
        if present == set(TABLES):
            _ready = True
            return

        with db_cursor(dict_rows=False) as cur:
            if not _can_create(cur):
                missing = ", ".join(sorted(set(TABLES) - present))
                raise AuthDBError(
                    f"The database user lacks CREATE on this database, so the "
                    f"table(s) {missing} cannot be created automatically. Either "
                    f"grant CREATE, or have a DBA run the DDL in "
                    f"modes/financial_statement/upload/schema.py once — it "
                    f"creates only the financial_statement_live_ingestion* "
                    f"tables and alters nothing that already exists."
                )
            cur.execute(_DDL)

        logger.info("FS upload schema ready (v%s): %s",
                    SCHEMA_VERSION, ", ".join(TABLES))
        _ready = True


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="Create the financial_statement_live_ingestion* tables."
    )
    ap.add_argument("--check", action="store_true",
                    help="report what exists; create nothing")
    args = ap.parse_args(argv)

    logging.basicConfig(level="INFO", format="%(levelname)s: %(message)s")

    from app.auth.db import dsn

    target = dsn()
    if not target:
        print("No platform database configured. Set FINANCE_DSN or ARTHA_DB_DSN.")
        return 1
    # Never print the DSN itself — it carries the password.
    print(f"Database: {target.rsplit('@', 1)[-1]}")

    if not args.check:
        ensure_schema(force=True)

    present = existing_tables()
    for name in TABLES:
        print(f"  {'present' if name in present else 'MISSING':>8}  {name}")
    return 0 if set(present) == set(TABLES) else 1


if __name__ == "__main__":
    import sys
    from pathlib import Path

    _BACKEND = Path(__file__).resolve().parents[3]
    if str(_BACKEND) not in sys.path:
        sys.path.insert(0, str(_BACKEND))

    from dotenv import load_dotenv

    for _candidate in (_BACKEND / ".env", _BACKEND.parent / ".env"):
        if _candidate.is_file():
            load_dotenv(_candidate, override=False)
            break

    raise SystemExit(main())
