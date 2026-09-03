"""The two new tables, and the only DDL this project runs.

    python -m app.auth.schema          create them, then report what exists
    python -m app.auth.schema --check  report only, create nothing

WHAT THIS CREATES, AND WHAT IT WILL NEVER TOUCH
-----------------------------------------------
Two tables, both `CREATE TABLE IF NOT EXISTS`, both prefixed `artha_` so they
cannot collide with anything already in `finance_llm`. There is no ALTER, no
DROP, no DELETE and no UPDATE against any pre-existing table anywhere in this
project. `documents`, `text_chunks` and `table_chunks` are read-only to us and
stay that way.

WHY THE DDL LIVES IN A PYTHON STRING
-------------------------------------
This repo has no migration tooling — no alembic, no .sql files, no pyproject.
The one existing precedent for creating a table is `fdr/facts_store.py`, which
holds idempotent DDL as a module-level string and executes it on first use. This
follows that shape so there is one convention rather than two.

WHY IT RUNS LAZILY AND NOT AT IMPORT
-------------------------------------
A database blip during boot must not take down the gateway. If this ran at
import, an unreachable database would crash the whole process — including the
four modes that do not need these tables at all — and `/api/health`, which the
container healthcheck polls, would flap and restart-loop the container. Running
on first auth request means the gateway always boots, the healthcheck stays
green, and only authentication reports a problem.

The trade-off, stated plainly: because auth gates the whole app, a failure here
still means nobody can sign in. It is reported as a clear 503 naming the cause
rather than as a container that will not start.

WHY UUIDs ARE GENERATED IN PYTHON
----------------------------------
`bigserial` would need `CREATE SEQUENCE` and `gen_random_uuid()` needs the
pgcrypto extension on older servers — `CREATE EXTENSION` is DDL beyond what this
change is authorised to run. `uuid4()` in Python needs neither.
"""

from __future__ import annotations

import logging
import threading

from . import db as auth_db

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_DDL = """
-- Platform users. The only place a credential is stored, and it stores a
-- one-way hash rather than a password.
CREATE TABLE IF NOT EXISTS public.artha_users (
    user_id       uuid        PRIMARY KEY,
    username      text        NOT NULL,
    email         text        NOT NULL,
    password_hash text        NOT NULL,
    display_name  text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz
);

-- Case-insensitive uniqueness: 'Harish' and 'harish' must not be two accounts,
-- and a UNIQUE constraint on the raw column would allow exactly that.
CREATE UNIQUE INDEX IF NOT EXISTS artha_users_username_lower_uq
    ON public.artha_users (lower(username));
CREATE UNIQUE INDEX IF NOT EXISTS artha_users_email_lower_uq
    ON public.artha_users (lower(email));

-- Financial Statements chat history, one row per turn.
--
-- A conversation is not a row here -- it is a GROUP BY over conversation_id.
-- That avoids a parent table whose updated_at can drift out of step with its
-- children, and it keeps this change to the two tables it was scoped to.
--
-- Append-only. The alternative (one row per conversation holding a jsonb array
-- rewritten on every turn) has a lost-update window between concurrent turns
-- and grows a single row without bound.
CREATE TABLE IF NOT EXISTS public.artha_fs_messages (
    message_id      uuid        PRIMARY KEY,
    conversation_id uuid        NOT NULL,
    user_id         uuid        NOT NULL
                    REFERENCES public.artha_users(user_id) ON DELETE CASCADE,
    seq             integer     NOT NULL,
    role            text        NOT NULL CHECK (role IN ('user', 'assistant')),
    content         text        NOT NULL,
    -- The full QueryResponse for assistant turns, so a reopened conversation
    -- renders through the existing AnswerCard with its evidence and citations
    -- intact rather than as bare text.
    payload         jsonb,
    -- What the rewriter actually sent to the pipeline, when it differed from
    -- what the user typed. Kept because a surprising answer is otherwise
    -- impossible to explain after the fact.
    rewritten_query text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS artha_fs_messages_conv_seq_uq
    ON public.artha_fs_messages (conversation_id, seq);
CREATE INDEX IF NOT EXISTS artha_fs_messages_user_recent_idx
    ON public.artha_fs_messages (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS artha_fs_messages_conv_idx
    ON public.artha_fs_messages (conversation_id, seq);
"""

_TABLES = ("artha_users", "artha_fs_messages")

_ready = False
_lock = threading.Lock()


def _can_create(cur) -> bool:
    cur.execute(
        "SELECT has_database_privilege(current_user, current_database(), 'CREATE')"
    )
    row = cur.fetchone()
    return bool(row[0] if not isinstance(row, dict) else list(row.values())[0])


def existing_tables() -> list[str]:
    """Which of our tables are already present."""
    with auth_db.db_cursor(dict_rows=False) as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = ANY(%s)",
            (list(_TABLES),),
        )
        return sorted(r[0] for r in cur.fetchall())


def ensure_schema(force: bool = False) -> None:
    """Create the two tables if they are missing. Safe to call repeatedly.

    Guarded so the DDL is attempted once per process; a `CREATE TABLE IF NOT
    EXISTS` on every request would be harmless but pointless traffic.
    """
    global _ready
    if _ready and not force:
        return

    with _lock:
        if _ready and not force:
            return

        present = set(existing_tables())
        if present == set(_TABLES):
            _ready = True
            return

        with auth_db.db_cursor(dict_rows=False) as cur:
            if not _can_create(cur):
                missing = ", ".join(sorted(set(_TABLES) - present))
                raise auth_db.AuthDBError(
                    f"The database user lacks CREATE on this database, so the "
                    f"table(s) {missing} cannot be created automatically. Either "
                    f"grant CREATE, or have a DBA run the DDL in "
                    f"backend/app/auth/schema.py once — it creates only "
                    f"artha_users and artha_fs_messages and alters nothing."
                )
            cur.execute(_DDL)

        logger.info("Auth schema ready (v%s): %s",
                    SCHEMA_VERSION, ", ".join(_TABLES))
        _ready = True


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Create the two artha_* tables.")
    ap.add_argument("--check", action="store_true",
                    help="report what exists; create nothing")
    args = ap.parse_args(argv)

    logging.basicConfig(level="INFO", format="%(levelname)s: %(message)s")

    target = auth_db.dsn()
    if not target:
        print("No platform database configured. Set FINANCE_DSN or ARTHA_DB_DSN.")
        return 1
    # Never print the DSN itself — it carries the password.
    print(f"Platform database: {target.rsplit('@', 1)[-1]}")

    if not args.check:
        ensure_schema(force=True)

    present = existing_tables()
    for name in _TABLES:
        print(f"  {'present' if name in present else 'MISSING':>8}  {name}")
    return 0 if set(present) == set(_TABLES) else 1


if __name__ == "__main__":
    import sys
    from pathlib import Path

    if __package__ in (None, ""):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

    from dotenv import load_dotenv

    _BACKEND = Path(__file__).resolve().parent.parent.parent
    for _candidate in (_BACKEND / ".env", _BACKEND.parent / ".env"):
        if _candidate.is_file():
            load_dotenv(_candidate, override=False)
            break

    raise SystemExit(main())
