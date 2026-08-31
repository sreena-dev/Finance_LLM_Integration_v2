"""
Connection + tolerance config for fs_db. Everything is overridable by env var
(prefix FSDB_) or an optional `fs_db/.env` file, with the values you gave for the
current `finance_llm` DB baked in as defaults so the package runs out of the box.

When you migrate to a different DB, change it HERE (or via env) — nothing else in
the package hard-codes a host, port, or credential.
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


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


# --- current finance_llm DB (192.168.200.29:5478) --------------------------
PG = {
    "host": _env("FSDB_PG_HOST", "192.168.200.29"),
    "port": int(_env("FSDB_PG_PORT", "5478")),
    "dbname": _env("FSDB_PG_DB", "finance_llm"),
    "user": _env("FSDB_PG_USER", "bfl"),
    # No credential default: set FSDB_PG_PASSWORD in the environment or fs_db/.env
    # (both gitignored) so the password never lives in version control.
    "password": _env("FSDB_PG_PASSWORD", ""),
    "connect_timeout": int(_env("FSDB_PG_TIMEOUT", "6")),
}

# --- arithmetic tolerance --------------------------------------------------
# FS figures are typically in ₹ crore/lakh to 2 decimals; summing many 2-dp
# numbers drifts a few paise. Tie = |a-b| <= ABS + REL*|expected|.
ARITH_ABS_TOL = float(_env("FSDB_ARITH_ABS_TOL", "0.05"))
ARITH_REL_TOL = float(_env("FSDB_ARITH_REL_TOL", "0.001"))

# --- observability (optional Phoenix tracing) ------------------------------
# OFF by default. Self-contained: fs_db exports to its OWN Phoenix project so its
# spans never mix with the rag pipeline's 'cag-audit-rag'. Falls back to the repo
# PHOENIX_ENDPOINT if FSDB_PHOENIX_ENDPOINT isn't set. See tracing.py.
TRACE_ENABLED = _env("FSDB_TRACE_ENABLED", "0")
# The shared LAN Phoenix. Hardcoded rather than read from the repo .env for the same
# reason as the DB settings above: this package is self-contained and `_load_dotenv`
# only reads `fs_db/.env`, so a repo-root PHOENIX_ENDPOINT is invisible here.
PHOENIX_ENDPOINT = _env("FSDB_PHOENIX_ENDPOINT",
                        _env("PHOENIX_ENDPOINT", "http://10.10.116.160:6006/v1/traces"))
PHOENIX_PROJECT = _env("FSDB_PHOENIX_PROJECT", "fs-db-audit")
