"""Reading and writing an uploaded document's extraction in Postgres.

Mapping only. Every policy decision -- what a document IS, how long it lives,
which conversation may see it -- belongs to `store.py`; this module turns an
`UploadedDocument` into four tables' worth of rows and back.

WHAT IS AND IS NOT HERE
------------------------
The uploaded PDF is not stored. See `schema.py` for why that stays true and
what is stored instead.

user_id IS IN EVERY WHERE CLAUSE
---------------------------------
Never checked afterwards in Python. A conversation id is unguessable, but "hard
to guess" is not an access control -- the filter is. Same discipline as
`conversations.py` and as the Redis key scheme this mirrors.

ONE DEFINITION OF A DOCUMENT
-----------------------------
The field list comes from `store._serializable`, which is already the single
definition of what survives a round trip. Anything it writes to Redis, this
writes to Postgres; anything it omits (the lazily-built vector `index`, which
is not JSON-serialisable and is cheap to rebuild) is omitted here too. There is
deliberately no second serialisation format to keep in step.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from app.auth.db import AuthDBError, db_cursor

from . import schema as schema_mod

logger = logging.getLogger(__name__)

#: Columns of the tables child, in the order the INSERT below binds them.
#: Mirrors public.table_chunks plus the upload-only fields; see schema.py.
_TABLE_COLUMNS = (
    "table_id", "doc_id", "table_title", "table_description", "table_md",
    "page_ocr_start", "page_ocr_end", "financial_stmt_type", "toc_section",
    "unit", "currency", "note_refs", "is_financial", "source_file",
    "bbox", "confidence", "vlm_agreement", "snippet_jpeg_b64",
    "findings", "footings",
)

_TEXT_COLUMNS = (
    "chunk_id", "doc_id", "content", "page_ocr_start", "page_pdf_start",
    "section", "title", "chunk_type", "section_breadcrumb", "note_refs",
    "bbox", "source_file",
)

_PAGE_COLUMNS = ("page_no", "image_jpeg_b64", "width_px", "height_px")

#: Fields stored as jsonb, so a list or dict round-trips as itself rather than
#: as its repr. Everything else is bound as a scalar.
_JSON_TABLE_FIELDS = {"note_refs", "bbox", "findings", "footings"}
_JSON_TEXT_FIELDS = {"section_breadcrumb", "note_refs", "bbox"}


def _json(value: Any, default: Any) -> str:
    return json.dumps(value if value is not None else default)


def _row_values(record: dict[str, Any], columns, json_fields,
                doc_id: str | None = None) -> list[Any]:
    out: list[Any] = []
    for column in columns:
        value = record.get(column)
        # doc_id is denormalised onto every child row, and is taken from the
        # PARENT rather than from the record. A stored table/text dict does not
        # always carry one -- the tools index children by table_id/chunk_id and
        # never needed it -- and trusting the record put a NULL in a NOT NULL
        # column. Taking it from the parent is also the only way it can be
        # wrong-proof: the child belongs to that document by construction.
        if column == "doc_id" and doc_id is not None:
            value = doc_id
        if column in json_fields:
            out.append(_json(value, [] if column != "bbox" else None))
        else:
            out.append(value)
    return out


def _placeholders(n: int) -> str:
    return "(" + ", ".join(["%s"] * n) + ")"


def save(document, retention_days: int) -> None:
    """Write one document and its children, replacing any previous version.

    Idempotent on `(user_id, conversation_id, doc_id)`: re-uploading the same
    file into the same conversation replaces the extraction rather than
    accumulating a second copy. That matters because `doc_id` is content
    addressed, so "the same file" is exact rather than a guess -- and because
    a re-ingestion after a pipeline improvement should supersede, not double.

    The whole document is one transaction. A half-written extraction -- tables
    but no texts -- would present as a document whose notes the scan failed to
    yield, which is precisely the misreading prompt rule 24 exists to prevent.
    """
    schema_mod.ensure_schema()

    tables = document.tables or []
    texts = document.texts or []
    pages = document.pages or []
    identification = document.identification or {}
    core = document.document or {}

    with db_cursor(dict_rows=False) as cur:
        # Replace rather than upsert-in-place: the children are keyed on the
        # parent's surrogate id, and a fresh id makes the cascade delete do the
        # cleanup instead of three separate DELETEs that could half-apply.
        cur.execute(
            "DELETE FROM public.financial_statement_live_ingestion "
            "WHERE user_id = %s AND conversation_id = %s AND doc_id = %s",
            (document.user_id, document.conversation_id, document.doc_id),
        )

        ingestion_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO public.financial_statement_live_ingestion ("
            "  ingestion_id, doc_id, user_id, conversation_id, filename, sha256,"
            "  company, financial_year, fy_start, fy_end, framework,"
            "  statement_flavour, document, identification, quality,"
            "  ingest_version, uploaded_at, expires_at"
            ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
            "          %s::jsonb, %s::jsonb, %s::jsonb, %s,"
            "          to_timestamp(%s), now() + make_interval(days => %s))",
            (
                ingestion_id,
                document.doc_id,
                document.user_id,
                document.conversation_id,
                document.filename,
                core.get("sha256"),
                document.company,
                document.financial_year,
                core.get("fy_start"),
                core.get("fy_end"),
                identification.get("framework"),
                identification.get("statement_flavour"),
                _json(core, {}),
                _json(identification, {}),
                _json(document.quality or {}, {}),
                core.get("ingest_version") or identification.get("ingest_version"),
                document.uploaded_at,
                int(retention_days),
            ),
        )

        if tables:
            columns = ", ".join(("ingestion_id",) + _TABLE_COLUMNS)
            row_sql = _placeholders(len(_TABLE_COLUMNS) + 1)
            values: list[Any] = []
            chunks: list[str] = []
            for record in tables:
                chunks.append(row_sql)
                values.append(ingestion_id)
                values.extend(_row_values(record, _TABLE_COLUMNS, _JSON_TABLE_FIELDS,
                                          document.doc_id))
            cur.execute(
                f"INSERT INTO public.financial_statement_live_ingestion_tables "
                f"({columns}) VALUES " + ", ".join(chunks),
                values,
            )

        if texts:
            columns = ", ".join(("ingestion_id",) + _TEXT_COLUMNS)
            row_sql = _placeholders(len(_TEXT_COLUMNS) + 1)
            values = []
            chunks = []
            for record in texts:
                chunks.append(row_sql)
                values.append(ingestion_id)
                values.extend(_row_values(record, _TEXT_COLUMNS, _JSON_TEXT_FIELDS,
                                          document.doc_id))
            cur.execute(
                f"INSERT INTO public.financial_statement_live_ingestion_texts "
                f"({columns}) VALUES " + ", ".join(chunks),
                values,
            )

        if pages:
            columns = ", ".join(("ingestion_id",) + _PAGE_COLUMNS)
            row_sql = _placeholders(len(_PAGE_COLUMNS) + 1)
            values = []
            chunks = []
            for record in pages:
                chunks.append(row_sql)
                values.append(ingestion_id)
                values.extend(_row_values(record, _PAGE_COLUMNS, set()))
            cur.execute(
                f"INSERT INTO public.financial_statement_live_ingestion_pages "
                f"({columns}) VALUES " + ", ".join(chunks),
                values,
            )


def _rebuild(parent: dict, tables: list[dict], texts: list[dict],
             pages: list[dict]):
    """Rows back into the UploadedDocument the tools already read.

    Imported here rather than at module scope so `store` can import this
    module without a cycle.
    """
    from .store import UploadedDocument

    def strip(rows: list[dict]) -> list[dict]:
        return [{k: v for k, v in row.items() if k != "ingestion_id"}
                for row in rows]

    uploaded_at = parent.get("uploaded_at")
    return UploadedDocument(
        doc_id=parent["doc_id"],
        user_id=parent["user_id"],
        conversation_id=parent["conversation_id"],
        filename=parent["filename"],
        document=parent.get("document") or {},
        identification=parent.get("identification") or {},
        quality=parent.get("quality") or {},
        tables=strip(tables),
        texts=strip(texts),
        pages=strip(pages),
        uploaded_at=uploaded_at.timestamp() if uploaded_at else 0.0,
    )


def _children(cur, ingestion_ids: list[str]) -> tuple[dict, dict, dict]:
    """Every child row for these parents, grouped by ingestion_id.

    Fetched in three statements rather than per document: a conversation with
    several filings would otherwise issue 3N round trips to rebuild a scope
    that is read on every single query.
    """
    by_table: dict[str, list[dict]] = {i: [] for i in ingestion_ids}
    by_text: dict[str, list[dict]] = {i: [] for i in ingestion_ids}
    by_page: dict[str, list[dict]] = {i: [] for i in ingestion_ids}
    if not ingestion_ids:
        return by_table, by_text, by_page

    cur.execute(
        "SELECT * FROM public.financial_statement_live_ingestion_tables "
        "WHERE ingestion_id = ANY(%s::uuid[]) ORDER BY page_ocr_start, table_id",
        (ingestion_ids,),
    )
    for row in cur.fetchall():
        by_table[str(row["ingestion_id"])].append(dict(row))

    cur.execute(
        "SELECT * FROM public.financial_statement_live_ingestion_texts "
        "WHERE ingestion_id = ANY(%s::uuid[]) ORDER BY page_ocr_start, chunk_id",
        (ingestion_ids,),
    )
    for row in cur.fetchall():
        by_text[str(row["ingestion_id"])].append(dict(row))

    cur.execute(
        "SELECT * FROM public.financial_statement_live_ingestion_pages "
        "WHERE ingestion_id = ANY(%s::uuid[]) ORDER BY page_no",
        (ingestion_ids,),
    )
    for row in cur.fetchall():
        by_page[str(row["ingestion_id"])].append(dict(row))

    return by_table, by_text, by_page


def load(user_id: str, conversation_id: str) -> list:
    """Every live document in one conversation, oldest first.

    Expired rows are excluded by the query rather than swept first, so a
    document is never served past its retention window even if the sweep has
    not run yet.
    """
    schema_mod.ensure_schema()
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM public.financial_statement_live_ingestion "
            "WHERE user_id = %s AND conversation_id = %s AND expires_at > now() "
            "ORDER BY uploaded_at",
            (user_id, conversation_id),
        )
        parents = [dict(r) for r in cur.fetchall()]
        if not parents:
            return []
        ids = [str(p["ingestion_id"]) for p in parents]
        tables, texts, pages = _children(cur, ids)

    return [
        _rebuild(p, tables[str(p["ingestion_id"])],
                 texts[str(p["ingestion_id"])], pages[str(p["ingestion_id"])])
        for p in parents
    ]


def load_one(user_id: str, conversation_id: str, doc_id: str):
    """One document, or None."""
    schema_mod.ensure_schema()
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM public.financial_statement_live_ingestion "
            "WHERE user_id = %s AND conversation_id = %s AND doc_id = %s "
            "  AND expires_at > now()",
            (user_id, conversation_id, doc_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        parent = dict(row)
        ingestion_id = str(parent["ingestion_id"])
        tables, texts, pages = _children(cur, [ingestion_id])

    return _rebuild(parent, tables[ingestion_id], texts[ingestion_id],
                    pages[ingestion_id])


def update_table_cell(user_id: str, conversation_id: str, doc_id: str,
                       table_id: str, mutate):
    """Rewrite one table's `table_md` and the parent's `quality`, in place.

    Deliberately not `save()`: that deletes and reinserts the whole document
    -- every table, every text chunk, every page image -- under a fresh
    `ingestion_id`, which is far too heavy for a one-cell edit and would also
    require the caller to have the full document in hand. This touches
    exactly two rows.

    `mutate(table_md, quality) -> (new_table_md, new_quality, result)` holds
    all the policy (`edits.apply`); this function holds only the transaction.
    `SELECT ... FOR UPDATE` on the parent row serialises concurrent edits to
    the same document -- the loser sees the writer's committed `table_md` and
    its own `expected_cell` check (inside `mutate`) turns that into a 409
    rather than a lost update. `expires_at` is never written here: an edit
    must not extend a document's retention as a side effect.

    Returns `None` if the document or the table does not exist (the caller
    turns that into 404), else `(new_table_md, new_quality, result)` -- the
    same three values `mutate` produced, handed back so a caller updating a
    Redis cache alongside Postgres can write the exact values just committed
    rather than calling `mutate` a second time (it is not idempotent: a
    second call would append to `history` twice, or fail its own
    `expected_cell` check against the value it just wrote). If `mutate`
    raises, nothing is written and the exception -- typically
    `edits.EditError` -- is re-raised to the caller unchanged; it is caught
    here, before `db_cursor`'s own exception handling would otherwise wrap it
    as an opaque `AuthDBError` and destroy the status/code the router needs to
    answer the request properly.
    """
    schema_mod.ensure_schema()

    def _run(cur) -> dict[str, Any]:
        # A nested function so a `return` here only ends THIS lookup, rather
        # than exiting `update_table_cell` itself and skipping the raise/
        # None-vs-tuple handling below `with db_cursor(...)` -- a `return`
        # inside a `with` block exits the enclosing function, not just the
        # block, so folding this logic directly into the outer function's
        # `with` body silently dropped the error-reraise and the tuple
        # result on every path that used an early `return`.
        cur.execute(
            "SELECT ingestion_id, quality FROM public.financial_statement_live_ingestion "
            "WHERE user_id = %s AND conversation_id = %s AND doc_id = %s "
            "  AND expires_at > now() FOR UPDATE",
            (user_id, conversation_id, doc_id),
        )
        row = cur.fetchone()
        if row is None:
            return {"missing": True}
        ingestion_id, quality = row[0], (row[1] or {})

        cur.execute(
            "SELECT table_md FROM public.financial_statement_live_ingestion_tables "
            "WHERE ingestion_id = %s AND table_id = %s FOR UPDATE",
            (ingestion_id, table_id),
        )
        trow = cur.fetchone()
        if trow is None:
            return {"missing": True}
        table_md = trow[0] or ""

        try:
            new_md, new_quality, result = mutate(table_md, quality)
        except Exception as exc:  # noqa: BLE001 -- re-raised unchanged, see below
            return {"error": exc}

        cur.execute(
            "UPDATE public.financial_statement_live_ingestion_tables "
            "SET table_md = %s WHERE ingestion_id = %s AND table_id = %s",
            (new_md, ingestion_id, table_id),
        )
        cur.execute(
            "UPDATE public.financial_statement_live_ingestion "
            "SET quality = %s::jsonb WHERE ingestion_id = %s",
            (_json(new_quality, {}), ingestion_id),
        )
        return {"new_md": new_md, "new_quality": new_quality, "result": result}

    with db_cursor(dict_rows=False) as cur:
        outcome = _run(cur)

    if outcome.get("missing"):
        return None
    if "error" in outcome:
        raise outcome["error"]
    return outcome["new_md"], outcome["new_quality"], outcome["result"]


def delete(user_id: str, conversation_id: str, doc_id: str) -> int:
    """Remove one document. Children go with it by cascade."""
    schema_mod.ensure_schema()
    with db_cursor(dict_rows=False) as cur:
        cur.execute(
            "DELETE FROM public.financial_statement_live_ingestion "
            "WHERE user_id = %s AND conversation_id = %s AND doc_id = %s",
            (user_id, conversation_id, doc_id),
        )
        return cur.rowcount or 0


def drop_conversation(user_id: str, conversation_id: str) -> int:
    """Remove every document in one conversation."""
    schema_mod.ensure_schema()
    with db_cursor(dict_rows=False) as cur:
        cur.execute(
            "DELETE FROM public.financial_statement_live_ingestion "
            "WHERE user_id = %s AND conversation_id = %s",
            (user_id, conversation_id),
        )
        return cur.rowcount or 0


def sweep_expired() -> int:
    """Delete everything past its retention window. Returns rows removed.

    Called opportunistically rather than on a schedule, matching the
    self-healing style `store.list` and `_enforce_cap` already use instead of
    introducing a scheduler this service does not have.
    """
    schema_mod.ensure_schema()
    with db_cursor(dict_rows=False) as cur:
        cur.execute(
            "DELETE FROM public.financial_statement_live_ingestion "
            "WHERE expires_at <= now()"
        )
        removed = cur.rowcount or 0
    if removed:
        logger.info("swept %d expired uploaded document(s)", removed)
    return removed


def ping() -> tuple[bool, str | None]:
    """(reachable, reason) -- never raises. For health reporting."""
    try:
        missing = set(schema_mod.TABLES) - set(schema_mod.existing_tables())
        if missing:
            return False, f"missing table(s): {', '.join(sorted(missing))}"
        return True, None
    except AuthDBError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
