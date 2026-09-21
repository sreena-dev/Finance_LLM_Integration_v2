"""Windows MAX_PATH (260 char) workaround.

Once a path pushes past 260 characters, `Path.exists()`/`Path.is_file()`
can silently return False (no exception) and `openpyxl.load_workbook()`
raises a plain FileNotFoundError -- even though the file genuinely exists.
Enabling Windows' LongPathsEnabled policy is a useful complement but not a
substitute, since it can't be assumed present on every machine this runs
on -- the extended-length prefix bypasses MAX_PATH unconditionally,
regardless of that policy.

Ported verbatim from TB_normalization_v1's input/long_path.py.
"""

from __future__ import annotations

import os
from pathlib import Path

_EXTENDED_PREFIX = "\\\\?\\"
_UNC_PREFIX = "\\\\?\\UNC\\"
# Computed once, checked via this module-local flag (never by re-reading
# os.name at call time) so tests can monkeypatch just this flag instead of
# os.name itself.
_IS_WINDOWS = os.name == "nt"


def to_long_path(path: Path) -> Path:
    """Returns a Path safe to pass to Win32 file APIs regardless of length.
    No-op on non-Windows platforms. Idempotent -- safe to call on an
    already-prefixed path."""
    if not _IS_WINDOWS:
        return path

    raw = str(path)
    if raw.startswith(_EXTENDED_PREFIX):
        return path

    absolute = str(path.resolve())
    if absolute.startswith("\\\\"):
        return Path(_UNC_PREFIX + absolute.lstrip("\\"))
    return Path(_EXTENDED_PREFIX + absolute)
