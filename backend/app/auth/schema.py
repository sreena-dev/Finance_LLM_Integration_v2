"""The two new tables, and the only DDL this project runs.

    python -m app.auth.schema          create them, then report what exists
    python -m app.auth.schema --check  report only, create nothing

WHAT THIS CREATES, AND WHAT IT WILL NEVER TOUCH
-----------------------------------------------
Two core tables plus two admin tables, all `CREATE TABLE IF NOT EXISTS`, all
prefixed `artha_` so they cannot collide with anything already in
`finance_llm`. The one ALTER in the project adds `artha_users.is_super_admin`
-- our own table, never a pre-existing one. There is no DROP, no DELETE and no
UPDATE against any table we did not create. `documents`, `text_chunks` and
`table_chunks` are read-only to us and stay that way.

CORE VS OPTIONAL
----------------
Authentication gates the whole app, so a failure to create an ADMIN object
must never stop anyone signing in. The two core tables are required (failure
raises, as before). The admin column, `artha_query_events` and
`artha_admin_audit` are optional: if the database user cannot create them the
failure is logged, a flag is left unset, and admin features report themselves
unavailable while everything else keeps working.

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

SCHEMA_VERSION = 2

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

# Optional, each run on its own so one failure cannot block the others.
_DDL_ADMIN_COLUMN = """
-- Granted only by `python -m app.auth.admin grant <username>`; no API route
-- and no sign-up path ever sets it.
ALTER TABLE public.artha_users
    ADD COLUMN IF NOT EXISTS is_super_admin boolean NOT NULL DEFAULT false;
"""

_DDL_EVENTS = """
-- One row per query, success or failure. Exists because tokens, tool-call
-- counts and failed queries otherwise only ever reach stdout.
CREATE TABLE IF NOT EXISTS public.artha_query_events (
    event_id             uuid        PRIMARY KEY,
    user_id              uuid        REFERENCES public.artha_users(user_id)
                                     ON DELETE SET NULL,
    conversation_id      uuid,
    mode                 text        NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    query_text           text,
    status               text        NOT NULL
                         CHECK (status IN ('ok', 'error', 'unavailable')),
    elapsed_seconds      double precision,
    prompt_tokens        integer,
    completion_tokens    integer,
    tool_calls           integer,
    tools_used           jsonb,
    confidence           text,
    unsourced            boolean,
    has_upload           boolean,
    num_chunks_retrieved integer,
    rewritten            boolean,
    error_stage          text,
    error_message        text
);
CREATE INDEX IF NOT EXISTS artha_query_events_created_idx
    ON public.artha_query_events (created_at DESC);
CREATE INDEX IF NOT EXISTS artha_query_events_user_idx
    ON public.artha_query_events (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS artha_query_events_status_idx
    ON public.artha_query_events (status, created_at DESC);
"""

_DDL_AUDIT = """
-- Viewing another user's chats is sensitive; every admin read leaves a row.
CREATE TABLE IF NOT EXISTS public.artha_admin_audit (
    audit_id       uuid        PRIMARY KEY,
    admin_user_id  uuid        NOT NULL,
    action         text        NOT NULL,
    target_user_id uuid,
    detail         text,
    at             timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS artha_admin_audit_at_idx
    ON public.artha_admin_audit (at DESC);
"""

_TABLES = ("artha_users", "artha_fs_messages")
_OPTIONAL_TABLES = ("artha_query_events", "artha_admin_audit")

_ready = False
_lock = threading.Lock()

# Set by ensure_schema(); read through the accessor functions below.
_admin_column_ok = False
_events_ok = False
_audit_ok = False


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
            (list(_TABLES) + list(_OPTIONAL_TABLES),),
        )
        return sorted(r[0] for r in cur.fetchall())


def _has_admin_column() -> bool:
    with auth_db.db_cursor(dict_rows=False) as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'artha_users' "
            "AND column_name = 'is_super_admin'"
        )
        return cur.fetchone() is not None


def _try_optional(label: str, ddl: str) -> bool:
    """Run one optional DDL block. Never raises: a failure here must not take
    authentication down with it (see the module docstring)."""
    try:
        with auth_db.db_cursor(dict_rows=False) as cur:
            cur.execute(ddl)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not create %s (%s: %s). Admin features that need it are "
            "unavailable; sign-in and every other feature are unaffected.",
            label, type(exc).__name__, exc,
        )
        return False


def ensure_schema(force: bool = False) -> None:
    """Create the core tables if missing, then the optional admin objects.

    Safe to call repeatedly. Guarded so the DDL is attempted once per process;
    a `CREATE TABLE IF NOT EXISTS` on every request would be harmless but
    pointless traffic.

    The skip check covers table names AND the `is_super_admin` column. Names
    alone (the original check) would skip the ALTER forever on a database where
    both original tables already exist -- which is every deployed database.
    """
    global _ready, _admin_column_ok, _events_ok, _audit_ok
    if _ready and not force:
        return

    with _lock:
        if _ready and not force:
            return

        present = set(existing_tables())

        if not set(_TABLES) <= present:
            with auth_db.db_cursor(dict_rows=False) as cur:
                if not _can_create(cur):
                    missing = ", ".join(sorted(set(_TABLES) - present))
                    raise auth_db.AuthDBError(
                        f"The database user lacks CREATE on this database, so "
                        f"the table(s) {missing} cannot be created "
                        f"automatically. Either grant CREATE, or have a DBA "
                        f"run the DDL in backend/app/auth/schema.py once."
                    )
                cur.execute(_DDL)
            present = set(existing_tables())

        # Optional admin objects. Each is independent of the others.
        try:
            _admin_column_ok = _has_admin_column()
        except Exception:  # noqa: BLE001
            _admin_column_ok = False
        if not _admin_column_ok:
            _try_optional("artha_users.is_super_admin", _DDL_ADMIN_COLUMN)
            try:
                _admin_column_ok = _has_admin_column()
            except Exception:  # noqa: BLE001
                _admin_column_ok = False

        _events_ok = "artha_query_events" in present or _try_optional(
            "artha_query_events", _DDL_EVENTS)
        _audit_ok = "artha_admin_audit" in present or _try_optional(
            "artha_admin_audit", _DDL_AUDIT)

        logger.info(
            "Auth schema ready (v%s): admin_column=%s events=%s audit=%s",
            SCHEMA_VERSION, _admin_column_ok, _events_ok, _audit_ok,
        )
        _ready = True


def _flag(name: str) -> bool:
    """Make sure the schema has been attempted, then read a flag. Never
    raises: an unreachable database reads as 'not available', which fails
    closed."""
    try:
        ensure_schema()
    except Exception:  # noqa: BLE001
        return False
    return bool(globals()[name])


def admin_column_ready() -> bool:
    return _flag("_admin_column_ok")


def events_ready() -> bool:
    return _flag("_events_ok")


def audit_ready() -> bool:
    return _flag("_audit_ok")


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
    for name in _TABLES + _OPTIONAL_TABLES:
        print(f"  {'present' if name in present else 'MISSING':>8}  {name}")
    col = _has_admin_column()
    print(f"  {'present' if col else 'MISSING':>8}  artha_users.is_super_admin")
    return 0 if set(_TABLES) <= set(present) else 1


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
