"""Cross-worker token store for the column-mapper handoffs.

The column-mapper flows are two-request conversations: the first request stores
something server-side and returns a short-lived token, a later request redeems
it.

    POST /upload                  (422) -> GET  /preview?token=
                                        -> POST /upload-mapped
    POST /audit/upload-grouping   (422) -> GET  /preview?token=
                                        -> POST /audit/upload-grouping-mapped
    POST /audit/upload-grouping   (200) -> POST /audit  (grouping_token)

(All relative to this mode's ``/api/trial-balance`` prefix. The source repo's
flat equivalents were ``/api/upload``, ``/api/excel-preview``,
``/api/upload-mapped`` and ``/api/audit-tb/*``.)

These used to be module-level dicts in ``server.py`` — and, in this gateway's
first Trial Balance integration, ``adapter._preview_store`` — which is wrong
either way once the backend runs more than one uvicorn worker *process*: each
has its own memory, a token minted in worker A does not exist in worker B, and
the kernel hands each new connection to whichever worker accepts first, so every
redemption becomes a coin flip between working and "preview token not found or
expired". The source repo ran ``--workers ${WEB_CONCURRENCY:-2}`` and hit this
for real.

This gateway currently defaults to ``--workers 1`` on purpose (see the CMD note
in ``backend/Dockerfile``: ``ask``'s conversation memory is an in-process
SessionStore), so the fault is latent here rather than live — it appears the
moment ``ARTHA_BACKEND_WORKERS`` is raised. Storing tokens on disk removes that
trap ahead of time and costs nothing at one worker.

All workers share the container filesystem, so that is the shared medium: no
schema, no DSN, no migration. Scope note: this is shared across workers *within
one container*. Because the documented way to scale this service is more
replicas rather than more workers, a multi-replica deployment needs the
``PREVIEW_CACHE_DIR`` path on a shared volume (or a Postgres-backed store
instead) for the mapper hand-offs to survive a cross-replica hop.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import tempfile
import time
from pathlib import Path

logger = logging.getLogger("modes.trial_balance.token_store")

TTL_SECONDS = 1800  # 30 minutes

# gettempdir() so a local (non-container) dev run lands somewhere writable too.
_DIR = Path(os.getenv("PREVIEW_CACHE_DIR")
            or Path(tempfile.gettempdir()) / "finsight-tokens")

# Tokens arrive straight from a query string or request body and are used to build
# a filesystem path, so anything that isn't a token_urlsafe-shaped string is
# rejected before it can touch the path.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

try:
    _DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(_DIR, 0o700)
except OSError as exc:  # pragma: no cover - surfaces as a 404 on first redeem
    logger.warning("could not prepare token cache dir %s: %s", _DIR, exc)


def _new_token() -> str:
    return secrets.token_urlsafe(16)


def _write_atomic(path: Path, data: bytes) -> None:
    """Write via a temp file + rename so another worker can never read a
    half-written payload."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _expiry() -> float:
    # Wall clock, not time.monotonic(): the deadline is written to disk and read
    # by a different process, potentially after a restart.
    return time.time() + TTL_SECONDS


def _sweep() -> None:
    """Delete expired entries. Called on each put, mirroring the eviction policy
    the in-memory dicts had. Tolerates races with the other workers' sweeps."""
    now = time.time()
    # "*.json" covers both ".json" (preview) and ".grp.json" (grouping) metadata.
    for meta_path in _DIR.glob("*.json"):
        try:
            meta = json.loads(meta_path.read_text())
            if float(meta.get("expires", 0)) >= now:
                continue
            meta_path.unlink(missing_ok=True)
            _DIR.joinpath(meta_path.stem + ".bin").unlink(missing_ok=True)
        except (OSError, ValueError, json.JSONDecodeError):
            continue


def _read_meta(token: str, suffix: str) -> dict | None:
    """Return the metadata for ``token`` if it exists and hasn't expired."""
    if not _TOKEN_RE.match(token or ""):
        return None
    try:
        meta = json.loads(_DIR.joinpath(token + suffix).read_text())
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if float(meta.get("expires", 0)) < time.time():
        return None
    return meta


# ---------------------------------------------------------------------------
# Raw uploaded bytes awaiting a manual column mapping.

def put_preview(filename: str, data: bytes) -> str:
    """Store raw file bytes for the column-mapper and return a token."""
    _sweep()
    token = _new_token()
    _write_atomic(_DIR / f"{token}.bin", data)
    _write_atomic(_DIR / f"{token}.json",
                  json.dumps({"filename": filename, "expires": _expiry()}).encode())
    return token


def get_preview(token: str) -> dict | None:
    """Return ``{"filename": str, "data": bytes}``, or None if unknown/expired."""
    meta = _read_meta(token, ".json")
    if meta is None:
        return None
    try:
        data = _DIR.joinpath(token + ".bin").read_bytes()
    except OSError:
        return None
    return {"filename": meta.get("filename") or "upload.xlsx", "data": data}


# ---------------------------------------------------------------------------
# Parsed chart-of-accounts / FSLI grouping overrides (client-format rule C7).
# Parsed on upload, so only the resulting override dict is kept, not raw bytes.

def put_grouping(override: dict[str, str]) -> str:
    """Store a parsed grouping override and return a token."""
    _sweep()
    token = _new_token()
    _write_atomic(_DIR / f"{token}.grp.json",
                  json.dumps({"override": override, "expires": _expiry()}).encode())
    return token


def get_grouping(token: str) -> dict[str, str] | None:
    """Return the stored override dict, or None if unknown/expired."""
    meta = _read_meta(token, ".grp.json")
    if meta is None:
        return None
    override = meta.get("override")
    return override if isinstance(override, dict) else None
