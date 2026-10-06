"""
Where auditor-set threshold overrides live, and nothing else.

THE MODEL
---------
Every threshold in the FDR has a DEFAULT, written in code (`xbrl_signal_thresholds.py`,
`xbrl_trend_thresholds.py`, or declared in `xbrl_threshold_registry.py` for the formerly
inline numbers). An OVERRIDE is a value an auditor set from the UI. The effective value
is `override if one exists else default`. The defaults are never edited or removed, so
"reset" is always possible and a missing/corrupt store file degrades to the defaults
rather than to an error.

This module is deliberately dependency-free (stdlib only) so that the threshold modules
can import it to read overrides without importing each other.

STORAGE
-------
One JSON file, written atomically (temp file + `os.replace`) so a crash can never leave
half a file. Path: `FDR_THRESHOLD_STORE`, else `<backend>/data/fdr_thresholds.json`.
In a container, point it at a mounted volume or overrides vanish on redeploy.

Reads are cached against the file's mtime, so every request sees a save made by any worker
process without re-parsing the file each time. Concurrent saves within one process are
serialised by a lock; across processes the last writer wins (the file is replaced whole,
never merged), which is acceptable for a rarely-edited audit setting.

AUDIT TRAIL
-----------
Each save records who changed what (old -> new) in `history`, capped, so a report's
numbers can be explained after the fact (spec Sec 16 / 18.3: same inputs, same output).
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

_HISTORY_CAP = 200
_lock = threading.RLock()
_cache: dict[str, Any] = {"mtime": None, "path": None, "data": None}


def _path() -> Path:
    env = (os.environ.get("FDR_THRESHOLD_STORE") or "").strip()
    if env:
        return Path(env)
    # .../backend/modes/financial_diagnostic_report/pipeline/xbrl_fetch/this_file -> backend/
    return Path(__file__).resolve().parents[4] / "data" / "fdr_thresholds.json"


def _empty() -> dict[str, Any]:
    return {"version": 0, "updated_at": None, "updated_by": None, "overrides": {}, "history": []}


def _load() -> dict[str, Any]:
    path = _path()
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return _empty()
    with _lock:
        if _cache["mtime"] == mtime and _cache["path"] == str(path) and _cache["data"] is not None:
            return _cache["data"]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            ov = data.get("overrides")
            if not isinstance(ov, dict):
                raise ValueError("overrides must be an object")
            data["overrides"] = {k: float(v) for k, v in ov.items()}
            data.setdefault("version", 0)
            data.setdefault("history", [])
        except (OSError, ValueError, TypeError):
            # Corrupt or unreadable store: fall back to defaults, never fail a report.
            return _empty()
        _cache.update(mtime=mtime, path=str(path), data=data)
        return data


def overrides() -> dict[str, float]:
    """Stored overrides, keyed by registry key, in the units the code uses."""
    return dict(_load()["overrides"])


def override(key: str) -> float | None:
    return _load()["overrides"].get(key)


def meta() -> dict[str, Any]:
    d = _load()
    return {"version": d.get("version", 0), "updated_at": d.get("updated_at"),
            "updated_by": d.get("updated_by"), "history": list(d.get("history", []))}


def save(new_overrides: dict[str, float], actor: str, *, changes: list[dict[str, Any]]) -> None:
    """Replace the override set. `changes` is the human-readable diff for the audit trail."""
    with _lock:
        current = _load()
        history = list(current.get("history", []))
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        if changes:
            history.append({"at": stamp, "by": actor, "changes": changes})
        doc = {
            "version": int(current.get("version", 0)) + 1,
            "updated_at": stamp,
            "updated_by": actor,
            "overrides": {k: float(v) for k, v in new_overrides.items()},
            "history": history[-_HISTORY_CAP:],
        }
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".fdr_thr_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=2)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        _cache.update(mtime=None, data=None)   # force re-read on next access
