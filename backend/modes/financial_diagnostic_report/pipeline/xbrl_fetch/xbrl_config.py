"""
Connection config for `as_db`. Everything overridable by env var (prefix XBRLAS_) or an
optional `xbrl_fetch/.env` file — mirrors `fs_db/config.py`'s pattern so the two packages
are configured the same way, but points at a different, already-hosted database.

No credential default: set XBRLAS_PG_PASSWORD in the environment or xbrl_fetch/.env
(both gitignored) so the password never lives in version control.
"""
from __future__ import annotations
import os
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimal KEY=VALUE loader (no dependency). Does not override real env vars."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


_load_dotenv(Path(__file__).with_name(".env"))


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _port(val: str) -> int | str:
    val = val.strip()
    return int(val) if val.isdigit() else val


# --- as_db (hosted, already exists) -----------------------------------------
# No infra defaults in source: host/port/db/user/password are all read from the
# environment or xbrl_fetch/.env (gitignored) so this file carries no real
# connection details, not even a non-secret one.
PG = {
    "host": _env("XBRLAS_PG_HOST"),
    "port": _port(_env("XBRLAS_PG_PORT", "5432")),
    "dbname": _env("XBRLAS_PG_DB"),
    "user": _env("XBRLAS_PG_USER"),
    "password": _env("XBRLAS_PG_PASSWORD"),
    "connect_timeout": int(_env("XBRLAS_PG_TIMEOUT", "10")),
}

