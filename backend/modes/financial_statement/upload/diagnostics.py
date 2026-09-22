"""Why a lookup came back empty, carried to wherever it can be said out loud.

The helpers the FS tools call return ``dict | None`` or ``list``. A ``None`` has
nowhere to put a reason, so when a filter eliminates every candidate the fact
dies at the return statement and the tool prints a bare "not found" — which is
the failure this whole change exists to stop.

The reason is collected here instead, request-scoped, and drained by the
string-returning tools that actually talk to the model. Same ``ContextVar``
mechanism as ``store.Scope`` and for the same reason: the tool closures the agent
framework calls have no parameter to thread it through, and a module global would
leak one user's diagnostics into another's request under concurrency.

Prompt rules 16, 17 and 24 already oblige the model to reproduce a coverage
caveat and to treat a miss as an extraction limitation rather than a disclosure
failure. This is what gives it something true to reproduce.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Note:
    """One thing worth telling the reader about how a lookup went."""

    source: str
    text: str
    #: The sieve trace, for the coverage report and for debugging.
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class Collector:
    notes: list[Note] = field(default_factory=list)

    def add(self, source: str, text: str | None, detail: dict | None = None) -> None:
        if not text:
            return
        # De-duplicate: a policy lookup runs the same sieve twice (keywords, then
        # embeddings) and the same caveat twice reads like two separate problems.
        for existing in self.notes:
            if existing.source == source and existing.text == text:
                return
        self.notes.append(Note(source=source, text=text, detail=detail or {}))

    def drain(self) -> list[Note]:
        notes, self.notes = self.notes, []
        return notes

    def caveats(self) -> str:
        """The collected notes as text to append to a tool's output."""
        return "\n".join(n.text for n in self.drain())


_COLLECTOR: contextvars.ContextVar[Collector | None] = contextvars.ContextVar(
    "fs_upload_diagnostics", default=None
)


def start() -> object:
    return _COLLECTOR.set(Collector())


def reset(token) -> None:
    try:
        _COLLECTOR.reset(token)
    except (ValueError, LookupError):
        # Reset from a different context than the set. Nothing to undo.
        pass


def current() -> Collector | None:
    return _COLLECTOR.get()


def record(source: str, result, text: str | None = None) -> None:
    """Record a sieve result's caveat, if it has one.

    Safe to call when no collector is active: outside a request there is nobody
    to tell, and a missing collector must never be the thing that breaks a tool.
    """
    collector = _COLLECTOR.get()
    if collector is None:
        return
    # `result is not None`, NOT `if result`. SieveResult.__bool__ reports whether
    # it found rows, so a truthiness test skips the caveat in exactly the case
    # that has one worth hearing -- the empty result. That inversion cost a
    # silent block once already.
    message = text if text is not None else (
        result.caveat() if result is not None else None
    )
    detail = result.as_dict() if hasattr(result, "as_dict") else {}
    collector.add(source, message, detail)


def caveats() -> str:
    collector = _COLLECTOR.get()
    return collector.caveats() if collector is not None else ""
