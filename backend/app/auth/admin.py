"""Grant, revoke and list super administrators. Command line ONLY.

    python -m app.auth.admin list
    python -m app.auth.admin grant <username>
    python -m app.auth.admin revoke <username> [--force]

WHY THERE IS NO API FOR THIS
-----------------------------
The dashboard this unlocks can read every user's conversations. If admin could
be granted over HTTP -- even by another admin -- then one stolen admin session
could mint more of them, and sign-up (which is open by design) would be one bug
away from self-service admin. So the only way to set the flag is a shell on the
machine that can already reach the database, which is the same trust boundary
as the database itself.

The flag is read from the database on every request (see
`deps.require_super_admin`), so `revoke` takes effect on the very next call.
"""

from __future__ import annotations

import logging
import sys

from . import db as auth_db
from . import schema


def _need_column() -> bool:
    schema.ensure_schema(force=True)
    if not schema.admin_column_ready():
        print(
            "The admin column could not be created on this database, so admins "
            "cannot be managed. Check the log above: the database user probably "
            "cannot ALTER artha_users. Grant it, or have a DBA run\n"
            "  ALTER TABLE public.artha_users ADD COLUMN IF NOT EXISTS "
            "is_super_admin boolean NOT NULL DEFAULT false;"
        )
        return False
    return True


def list_admins() -> int:
    if not _need_column():
        return 1
    with auth_db.db_cursor() as cur:
        cur.execute(
            "SELECT username, email, last_login_at FROM public.artha_users "
            "WHERE is_super_admin ORDER BY lower(username)"
        )
        rows = cur.fetchall()
    if not rows:
        print("No super administrators. Grant one with: "
              "python -m app.auth.admin grant <username>")
        return 0
    for r in rows:
        last = r["last_login_at"].strftime("%Y-%m-%d %H:%M") if r["last_login_at"] else "never"
        print(f"  {r['username']:<24} {r['email']:<32} last sign-in: {last}")
    return 0


def _count_admins(cur) -> int:
    cur.execute("SELECT count(*) AS n FROM public.artha_users WHERE is_super_admin")
    return int(cur.fetchone()["n"])


def set_admin(username: str, value: bool, force: bool = False) -> int:
    if not _need_column():
        return 1
    with auth_db.db_cursor() as cur:
        cur.execute(
            "SELECT user_id, username, is_super_admin FROM public.artha_users "
            "WHERE lower(username) = lower(%s)",
            (username,),
        )
        row = cur.fetchone()
        if row is None:
            print(f"No such user: {username!r}")
            return 1
        if bool(row["is_super_admin"]) == value:
            print(f"{row['username']} is already "
                  f"{'a super administrator' if value else 'not a super administrator'}.")
            return 0
        if not value and not force and _count_admins(cur) <= 1:
            print(
                f"{row['username']} is the only super administrator. Revoking "
                "would leave nobody able to open the dashboard. Grant another "
                "first, or pass --force."
            )
            return 1
        cur.execute(
            "UPDATE public.artha_users SET is_super_admin = %s WHERE user_id = %s",
            (value, str(row["user_id"])),
        )
    print(f"{row['username']}: super administrator = {value}")
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Manage super administrators.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="show current super administrators")
    g = sub.add_parser("grant", help="make a user a super administrator")
    g.add_argument("username")
    r = sub.add_parser("revoke", help="remove super administrator rights")
    r.add_argument("username")
    r.add_argument("--force", action="store_true",
                   help="allow revoking the last remaining super administrator")
    args = ap.parse_args(argv)

    logging.basicConfig(level="WARNING", format="%(levelname)s: %(message)s")

    target = auth_db.dsn()
    if not target:
        print("No platform database configured. Set FINANCE_DSN or ARTHA_DB_DSN.")
        return 1
    # Never print the DSN itself -- it carries the password.
    print(f"Platform database: {target.rsplit('@', 1)[-1]}")

    if args.cmd == "list":
        return list_admins()
    if args.cmd == "grant":
        return set_admin(args.username, True)
    return set_admin(args.username, False, force=args.force)


if __name__ == "__main__":
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
