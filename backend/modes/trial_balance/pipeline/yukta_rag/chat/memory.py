"""Lightweight conversation memory for the chat (bounded to the last N turns).

A "turn" is one user question paired with the assistant's answer. Memory is kept
per session and capped at ``max_turns`` (default 5) so prompts stay bounded; the
oldest turn is dropped once the cap is exceeded.
"""

from __future__ import annotations

from collections import deque
from threading import Lock

MAX_TURNS = 5


class ConversationMemory:
    """An ordered, bounded list of (question, answer) turns for one chat."""

    def __init__(self, max_turns: int = MAX_TURNS):
        self.max_turns = max_turns
        self._turns: deque[dict] = deque(maxlen=max_turns)

    def add(self, question: str, answer: str) -> None:
        q = (question or "").strip()
        a = (answer or "").strip()
        if q and a:
            self._turns.append({"question": q, "answer": a})

    def turns(self) -> list[dict]:
        """Recent turns, oldest first."""
        return list(self._turns)

    def clear(self) -> None:
        self._turns.clear()

    def __len__(self) -> int:
        return len(self._turns)


class SessionStore:
    """Thread-safe map of session id -> ConversationMemory."""

    def __init__(self, max_turns: int = MAX_TURNS):
        self.max_turns = max_turns
        self._sessions: dict[str, ConversationMemory] = {}
        self._lock = Lock()

    def get(self, session_id: str) -> ConversationMemory:
        sid = session_id or "default"
        with self._lock:
            mem = self._sessions.get(sid)
            if mem is None:
                mem = ConversationMemory(self.max_turns)
                self._sessions[sid] = mem
            return mem

    def clear(self, session_id: str) -> None:
        sid = session_id or "default"
        with self._lock:
            self._sessions.pop(sid, None)


def format_history(turns: list[dict], answer_chars: int = 700) -> str:
    """Render recent turns as a compact transcript for prompting.

    Assistant answers are truncated so older context can't crowd out the
    retrieved excerpts, which remain authoritative.
    """
    if not turns:
        return ""
    lines = []
    for t in turns:
        q = (t.get("question") or "").strip()
        a = (t.get("answer") or "").strip()
        if len(a) > answer_chars:
            a = a[:answer_chars].rstrip() + " …"
        lines.append(f"User: {q}\nAssistant: {a}")
    return "\n\n".join(lines)
