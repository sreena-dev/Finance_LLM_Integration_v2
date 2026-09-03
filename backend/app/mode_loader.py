"""Import each mode's router without letting one failure take the gateway down.

THE FAILURE THIS PREVENTS
-------------------------
`app/main.py` imported all five mode routers at module scope. A mode router is
not an inert file — `modes/trial_balance/router.py` verifies its knowledge packs
and creates an upload directory at import time, and importing it pulls in
`pipeline/tools.py`, which imports `python-docx` at module level.

So a single missing dependency in ONE mode aborted the import of `app.main`, and
uvicorn refused to start. Observed exactly that: `ModuleNotFoundError: No module
named 'docx'` took down the gateway, the Statutory Auditor's Report, SAR Q&A,
Financial Statements and the Diagnostic Report — none of which use docx — and
presented it as a stack trace with no indication of which mode was at fault.

That contradicts the guarantee the README makes and that every other layer of
this project keeps: "a mode that cannot load degrades on its own — the gateway
and the other modes keep serving." The adapters already honour it, wrapping
their pipeline imports in `ModeUnavailableError`. The router import was the one
place that did not.

WHAT THIS DOES INSTEAD
----------------------
Each router is imported on its own. A failure is recorded against that mode id
and the other four are unaffected. The gateway starts, `/api/health` answers, and
the broken mode reports itself unavailable with the real exception text — which
names the missing dependency, so the fix is the next thing you read rather than
something to work out from a traceback.

WHY THIS MODULE IS A LEAF
-------------------------
`app/registry.py` needs to report these failures and `app/main.py` needs to
record them. If either owned the dict the other would have to import it, and
`main` already imports `registry`. Both importing this — which imports nothing
of ours — keeps the graph acyclic.
"""

from __future__ import annotations

import importlib
import logging

logger = logging.getLogger(__name__)

# mode id -> why its router could not be imported. Populated by `load_routers`
# at startup and read by `registry.describe` on every /api/modes call.
IMPORT_FAILURES: dict[str, str] = {}


def failure_for(mode_id: str) -> str | None:
    """Why this mode's routes are absent, or None if it loaded."""
    return IMPORT_FAILURES.get(mode_id)


def _explain(mode_id: str, exc: BaseException) -> str:
    """The failure, phrased so the next action is obvious.

    A bare `ModuleNotFoundError: No module named 'docx'` does not say which
    package to install — the import name and the pip name differ often enough
    that guessing is a real cost. The common ones are named outright.
    """
    detail = f"{type(exc).__name__}: {exc}"

    if isinstance(exc, ModuleNotFoundError) and exc.name:
        pip_name = {
            "docx": "python-docx",
            "dotenv": "python-dotenv",
            "multipart": "python-multipart",
            "psycopg2": "psycopg2-binary",
            "psycopg": "psycopg[binary]",
            "jwt": "PyJWT",
            "fitz": "PyMuPDF",
            "yukta": "yukta (not on PyPI - build it with "
                     "scripts/build-yukta-wheel.sh or install from source)",
        }.get(exc.name, exc.name)
        # ASCII only: this string is logged, and a Windows console that cannot
        # encode a character raises UnicodeEncodeError from inside the logging
        # call. tools_fs.py:305-311 documents that exact failure aborting table
        # discovery in this project, so it is not a hypothetical.
        return (
            f"{detail}. This mode needs the '{exc.name}' module - install it "
            f"with `pip install {pip_name}`. The other modes are unaffected."
        )

    return f"{detail}. The other modes are unaffected."


def load_routers(specs: tuple[tuple[str, str], ...]) -> list[tuple[str, object]]:
    """Import each `(mode_id, module_path)`, returning those that succeeded.

    Never raises. A mode whose router will not import is recorded in
    `IMPORT_FAILURES` and simply has no routes; the caller registers a stub for
    it so its endpoints answer 503 rather than 404.
    """
    loaded: list[tuple[str, object]] = []

    for mode_id, module_path in specs:
        try:
            module = importlib.import_module(module_path)
            loaded.append((mode_id, module.router))
        except Exception as exc:  # noqa: BLE001 - one mode must not stop the rest
            reason = _explain(mode_id, exc)
            IMPORT_FAILURES[mode_id] = reason
            # exc_info because the message alone rarely says WHERE in the mode's
            # import chain it broke, and that is what a fix starts from.
            logger.error("Mode '%s' could not be loaded and will report itself "
                         "unavailable: %s", mode_id, reason, exc_info=True)

    if IMPORT_FAILURES:
        logger.warning(
            "%d of %d modes failed to load (%s). The gateway and the remaining "
            "%d are serving normally.",
            len(IMPORT_FAILURES), len(specs), ", ".join(sorted(IMPORT_FAILURES)),
            len(loaded),
        )

    return loaded
