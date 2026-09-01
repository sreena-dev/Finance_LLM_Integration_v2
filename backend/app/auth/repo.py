"""Every SQL statement that touches `artha_users`, in one place.

Reads and writes only that table. Nothing here goes near `documents`,
`text_chunks`, `table_chunks` or anything else that existed before this change.
"""

from __future__ import annotations

import uuid

from . import db as auth_db
from . import security

_COLUMNS = "user_id, username, email, display_name, created_at, last_login_at"


class UsernameTaken(ValueError):
    """That username or email already belongs to an account."""


def _row_to_user(row: dict) -> dict:
    return {
        "user_id": str(row["user_id"]),
        "username": row["username"],
        "email": row["email"],
        "display_name": row.get("display_name"),
    }


def create_user(username: str, email: str, password: str,
                display_name: str | None = None) -> dict:
    """Register an account. Raises UsernameTaken on a duplicate.

    The uniqueness check is the database's `lower(...)` unique indexes, not a
    prior SELECT: two simultaneous sign-ups with the same name would both pass a
    check-then-insert, and only a constraint actually prevents that.
    """
    import psycopg2

    user_id = uuid.uuid4()
    password_hash = security.hash_password(password)

    try:
        with auth_db.db_cursor() as cur:
            cur.execute(
                "INSERT INTO public.artha_users "
                "(user_id, username, email, password_hash, display_name) "
                "VALUES (%s, %s, %s, %s, %s) "
                f"RETURNING {_COLUMNS}",
                (str(user_id), username, email, password_hash, display_name),
            )
            return _row_to_user(cur.fetchone())
    except auth_db.AuthDBError as exc:
        cause = exc.__cause__
        if isinstance(cause, psycopg2.errors.UniqueViolation):
            raise UsernameTaken(
                "That username or email is already registered."
            ) from exc
        raise


def get_by_login(login: str) -> dict | None:
    """Find an account by username OR email, case-insensitively.

    Returns the row including `password_hash` — this is the only function that
    exposes it, and only `authenticate` below calls it.
    """
    with auth_db.db_cursor() as cur:
        cur.execute(
            f"SELECT {_COLUMNS}, password_hash FROM public.artha_users "
            "WHERE lower(username) = lower(%s) OR lower(email) = lower(%s) "
            "LIMIT 1",
            (login, login),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_by_id(user_id: str) -> dict | None:
    with auth_db.db_cursor() as cur:
        try:
            cur.execute(
                f"SELECT {_COLUMNS} FROM public.artha_users WHERE user_id = %s",
                (str(user_id),),
            )
        except Exception:
            # A malformed uuid in a token is a bad token, not a server error.
            return None
        row = cur.fetchone()
        return _row_to_user(dict(row)) if row else None


def authenticate(login: str, password: str) -> dict | None:
    """The account for these credentials, or None.

    Always runs a hash comparison, even when no such account exists, so the
    response time does not reveal which usernames are registered.
    """
    row = get_by_login(login)
    stored = row["password_hash"] if row else (
        "$2b$12$" + "." * 53  # well-formed shape, matches nothing
    )
    ok = security.verify_password(password, stored)
    if not row or not ok:
        return None

    with auth_db.db_cursor() as cur:
        cur.execute(
            "UPDATE public.artha_users SET last_login_at = now() "
            "WHERE user_id = %s",
            (str(row["user_id"]),),
        )
    return _row_to_user(row)
