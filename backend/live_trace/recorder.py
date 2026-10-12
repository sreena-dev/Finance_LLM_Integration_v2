"""Live run trace for Trial Balance: what the backend is doing, step by step, right now.

This package (`live_trace/`) is self-contained and edits no existing file: `instrument.py` attaches
it from outside at startup (see `asgi.py`).

WHY THIS EXISTS
---------------
An upload or audit runs for minutes. The server log is a flat stream interleaved with
every other request, and Phoenix records LLM spans for developers. Neither answers the
question the person waiting has: "what is it doing right now, and where is the LLM
involved?". This module records an ordered event stream per run -- steps, tool calls and
LLM calls -- that the /trace page reads.

WHAT IS RECORDED
----------------
Events, never payloads: step names, counters, durations, the *purpose* of an LLM call, how
many characters went out and came back. Prompts and responses carry client financial data
and are NOT stored unless TB_TRACE_CAPTURE_PAYLOADS=true (a short clipped excerpt, for
debugging only).

THE ONE RULE: TRACING MUST NEVER AFFECT A RUN
---------------------------------------------
Every public function swallows its own failures. If Valkey is down, a circuit breaker
turns emission into a cheap no-op for a short while instead of adding the connect timeout
to every step of the pipeline.

STORAGE (Valkey, same instance as the preview store)
----------------------------------------------------
    trace:run:{run_id}   JSON   run metadata (kind, label, user, status, timings)
    trace:ev:{run_id}    LIST   events, oldest first, capped
    trace:seq:{run_id}   INT    event sequence counter
    trace:user:{user_id} ZSET   run ids by start time, for the run list
All keys expire after TRACE_TTL_SECONDS.

HOW A RUN IS BOUND
------------------
`instrument.py` wraps the route handlers in `with recorder.run(run_id, kind=..., user_id=...)`. That puts
the run in a ContextVar, so anything deeper in the same request -- the pipeline_tool
wrapper, the LLM client wrapper, `with runtrace.step(...)` -- finds it without being
handed an id. Where a worker thread does not inherit the context (yukta's tool loop may
use one), pipeline_tool falls back to the session id embedded in `output_dir`.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
import threading
import time
import uuid
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

TRACE_TTL_SECONDS = 24 * 3600
MAX_EVENTS_PER_RUN = 2000
MAX_RUNS_PER_USER = 200
_BREAKER_SECONDS = 30
_CLIP = 400

_current_run: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar("tb_trace_run", default=None)
_step_stack: contextvars.ContextVar[tuple] = contextvars.ContextVar("tb_trace_steps", default=())

_dead_until = 0.0
_lock = threading.Lock()


def _capture_payloads() -> bool:
    return os.getenv("TB_TRACE_CAPTURE_PAYLOADS", "false").strip().lower() == "true"


def _enabled() -> bool:
    return os.getenv("TB_TRACE_ENABLED", "true").strip().lower() != "false"


def _client():
    """A Valkey client, or None when tracing is off / Valkey is disabled / the breaker is open."""
    global _dead_until
    if not _enabled() or time.time() < _dead_until:
        return None
    try:
        from modes.trial_balance.pipeline.valkey_client import get_valkey_client

        return get_valkey_client()
    except Exception:
        return None


def _guard(fn):
    """Run one Valkey operation; on any failure open the breaker and return None."""
    global _dead_until
    client = _client()
    if client is None:
        return None
    try:
        return fn(client)
    except Exception as exc:
        with _lock:
            _dead_until = time.time() + _BREAKER_SECONDS
        logger.warning("[runtrace] Valkey unavailable, tracing paused for %ss: %s", _BREAKER_SECONDS, exc)
        return None


def _clip(value: Any, limit: int = _CLIP) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    return value


# ------------------------------------------------------------------ run lifecycle

def current_run_id() -> Optional[str]:
    ctx = _current_run.get()
    return ctx["run_id"] if ctx else None


def _write_meta(run_id: str, updates: dict) -> None:
    def op(c):
        raw = c.get(f"trace:run:{run_id}")
        meta = json.loads(raw) if raw else {}
        meta.update(updates)
        c.set(f"trace:run:{run_id}", json.dumps(meta), ex=TRACE_TTL_SECONDS)

    _guard(op)


def _register_run(run_id: str, user_id: Optional[str], meta: dict) -> None:
    def op(c):
        raw = c.get(f"trace:run:{run_id}")
        existing = json.loads(raw) if raw else {}
        merged = {**existing, **{k: v for k, v in meta.items() if v is not None}}
        merged.setdefault("started_at", time.time())
        merged["run_id"] = run_id
        merged["user_id"] = user_id or merged.get("user_id")
        c.set(f"trace:run:{run_id}", json.dumps(merged), ex=TRACE_TTL_SECONDS)
        if merged["user_id"]:
            key = f"trace:user:{merged['user_id']}"
            c.zadd(key, {run_id: merged["started_at"]})
            c.zremrangebyrank(key, 0, -(MAX_RUNS_PER_USER + 1))
            c.expire(key, TRACE_TTL_SECONDS)

    _guard(op)


@contextlib.contextmanager
def run(run_id: Optional[str], *, kind: str, user_id: Optional[str] = None,
        label: Optional[str] = None, end_status: str = "ok", **meta) -> Iterator[Optional[str]]:
    """Bind this request to `run_id` and record its start and end.

    `end_status` is what a clean exit records. /upload passes "waiting" because the run
    continues at /upload-mapped under the same id. An exception records "failed".
    """
    if not run_id:
        yield None
        return
    prior_run = _current_run.set({"run_id": run_id, "user_id": user_id})
    prior_stack = _step_stack.set(())
    _register_run(run_id, user_id, {"kind": kind, "label": label, "status": "running", **meta})
    event("run_start", step=kind, message=label or kind)
    began = time.time()
    try:
        yield run_id
    except BaseException as exc:
        # HTTPException and friends are expected control flow in a route; record them as
        # failures with their detail rather than only the class name.
        detail = getattr(exc, "detail", None) or str(exc) or type(exc).__name__
        event("run_end", status="failed", message=_clip(str(detail)), duration_ms=_ms(began))
        _write_meta(run_id, {"status": "failed", "ended_at": time.time(), "error": _clip(str(detail))})
        raise
    else:
        final = _final_status.get(run_id, end_status)
        _final_status.pop(run_id, None)
        event("run_end", status=final, duration_ms=_ms(began))
        _write_meta(run_id, {"status": final, "ended_at": time.time()})
    finally:
        _step_stack.reset(prior_stack)
        _current_run.reset(prior_run)


_final_status: dict[str, str] = {}


def set_final_status(status: str) -> None:
    """Override the status a clean exit of the current run records (e.g. "partial")."""
    rid = current_run_id()
    if rid:
        _final_status[rid] = status


@contextlib.contextmanager
def bind(run_id: Optional[str], user_id: Optional[str] = None) -> Iterator[None]:
    """Attach to an already-registered run without recording a start/end -- for code that
    runs outside the request that owns the run (a worker thread, a deferred call)."""
    if not run_id:
        yield
        return
    prior_run = _current_run.set({"run_id": run_id, "user_id": user_id})
    try:
        yield
    finally:
        _current_run.reset(prior_run)


def _ms(started: float) -> int:
    return int((time.time() - started) * 1000)


# ------------------------------------------------------------------ events

def event(type_: str, *, step: Optional[str] = None, run_id: Optional[str] = None, **fields) -> None:
    """Append one event to the current (or given) run. Never raises."""
    try:
        rid = run_id or current_run_id()
        if not rid:
            return
        stack = _step_stack.get()
        payload = {
            "type": type_,
            "ts": time.time(),
            "step": step if step is not None else (stack[-1] if stack else None),
            "depth": len(stack),
            **{k: v for k, v in fields.items() if v is not None},
        }

        def op(c):
            payload["seq"] = int(c.incr(f"trace:seq:{rid}"))
            c.expire(f"trace:seq:{rid}", TRACE_TTL_SECONDS)
            key = f"trace:ev:{rid}"
            c.rpush(key, json.dumps(payload, default=str))
            c.ltrim(key, -MAX_EVENTS_PER_RUN, -1)
            c.expire(key, TRACE_TTL_SECONDS)

        _guard(op)
    except Exception:
        logger.debug("[runtrace] event dropped", exc_info=True)


def note(message: str, **fields) -> None:
    """A plain observation inside the current step ("tier CASE_2: 41 of 160 rows incomplete")."""
    event("note", message=_clip(message, 600), **fields)


class _Step:
    def __init__(self) -> None:
        self.fields: dict = {}

    def set(self, **fields) -> None:
        """Counters to attach to this step's end event (rows, mapped, tier ...)."""
        self.fields.update(fields)


@contextlib.contextmanager
def step(name: str, **detail) -> Iterator[_Step]:
    """A named stage of work. Emits step_start, then step_end with duration and outcome."""
    handle = _Step()
    stack = _step_stack.get()
    event("step_start", step=name, **detail)
    token = _step_stack.set(stack + (name,))
    began = time.time()
    try:
        yield handle
    except BaseException as exc:
        _step_stack.reset(token)
        event("step_end", step=name, status="failed", duration_ms=_ms(began),
              message=_clip(str(getattr(exc, "detail", None) or exc) or type(exc).__name__), **handle.fields)
        raise
    else:
        _step_stack.reset(token)
        event("step_end", step=name, status=handle.fields.pop("status", "ok"), duration_ms=_ms(began), **handle.fields)


def current_step() -> Optional[str]:
    stack = _step_stack.get()
    return stack[-1] if stack else None


# ------------------------------------------------------------------ LLM calls

def llm_started(*, model: Optional[str], messages: Any, purpose: Optional[str] = None,
                role: Optional[str] = None) -> Optional[dict]:
    """Mark the start of one LLM call; pass the result to llm_finished. None when untraced."""
    if not current_run_id():
        return None
    prompt_chars = _message_chars(messages)
    call = {
        "call_id": uuid.uuid4().hex[:8],
        "t0": time.time(),
        "model": model,
        "purpose": purpose or current_step() or "unspecified",
        "prompt_chars": prompt_chars,
        "run_id": current_run_id(),
        "role": role,
    }
    extra = {}
    if _capture_payloads():
        extra["prompt_excerpt"] = _clip(_last_user_text(messages))
    event("llm_start", step=call["purpose"], call_id=call["call_id"], model=model, role=role,
          prompt_chars=prompt_chars, **extra)
    return call


def llm_finished(call: Optional[dict], *, response: Any = None, error: Optional[BaseException] = None) -> None:
    if not call:
        return
    text = _response_text(response)
    extra = {}
    if _capture_payloads() and text:
        extra["response_excerpt"] = _clip(text)
    event(
        "llm_end",
        run_id=call["run_id"],
        step=call["purpose"],
        call_id=call["call_id"],
        model=call["model"],
        role=call["role"],
        status="failed" if error else "ok",
        duration_ms=_ms(call["t0"]),
        prompt_chars=call["prompt_chars"],
        response_chars=len(text) if text else 0,
        tool_calls=_tool_call_names(response),
        message=_clip(f"{type(error).__name__}: {error}") if error else None,
        **extra,
    )


def _message_chars(messages: Any) -> int:
    try:
        if isinstance(messages, str):
            return len(messages)
        return sum(len(str(m.get("content", "") if isinstance(m, dict) else m)) for m in messages)
    except Exception:
        return 0


def _last_user_text(messages: Any) -> str:
    try:
        for m in reversed(list(messages)):
            if isinstance(m, dict) and m.get("role") == "user":
                return str(m.get("content", ""))
    except Exception:
        pass
    return ""


def _response_text(response: Any) -> str:
    for attr in ("content", "text"):
        value = getattr(response, attr, None)
        if isinstance(value, str):
            return value
    if isinstance(response, dict):
        return str(response.get("content") or "")
    return response if isinstance(response, str) else ""


def _tool_call_names(response: Any) -> Optional[list]:
    calls = getattr(response, "tool_calls", None)
    if not calls:
        return None
    names = []
    for call in calls:
        fn = call.get("function", {}) if isinstance(call, dict) else getattr(call, "function", None)
        name = fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", None)
        if name:
            names.append(name)
    return names or None


def wrap_llm_client(client, role: Optional[str] = None):
    """Instrument `client.generate` in place so every LLM call is traced. Idempotent.

    `role` names who is asking (planner, composer, extract ...) when the caller knows."""
    if client is None or getattr(client, "_runtrace_wrapped", False):
        return client
    original = client.generate

    def traced_generate(messages, *args, **kwargs):
        call = llm_started(model=getattr(client, "model_name", None) or getattr(client, "model", None),
                           messages=messages, role=role)
        try:
            response = original(messages, *args, **kwargs)
        except BaseException as exc:
            llm_finished(call, error=exc)
            raise
        llm_finished(call, response=response)
        return response

    client.generate = traced_generate
    client._runtrace_wrapped = True
    return client


# ------------------------------------------------------------------ reading (for the /trace API)

def list_runs(user_id: Optional[str], *, limit: int = 50, all_users: bool = False) -> list:
    def op(c):
        if all_users:
            ids: list = []
            for key in c.scan_iter("trace:user:*", count=100):
                ids.extend(c.zrevrange(key, 0, limit - 1))
        else:
            ids = c.zrevrange(f"trace:user:{user_id}", 0, limit - 1)
        runs = []
        for rid in ids:
            raw = c.get(f"trace:run:{rid}")
            if raw:
                runs.append(json.loads(raw))
        runs.sort(key=lambda r: r.get("started_at", 0), reverse=True)
        return runs[:limit]

    return _guard(op) or []


def get_run(run_id: str) -> Optional[dict]:
    def op(c):
        raw = c.get(f"trace:run:{run_id}")
        return json.loads(raw) if raw else None

    return _guard(op)


def get_events(run_id: str, after: int = 0) -> list:
    def op(c):
        out = []
        for raw in c.lrange(f"trace:ev:{run_id}", 0, -1):
            ev = json.loads(raw)
            if ev.get("seq", 0) > after:
                out.append(ev)
        return out

    return _guard(op) or []


def available() -> bool:
    """True when tracing is on and Valkey answers right now."""
    return bool(_guard(lambda c: c.ping()))


# ------------------------------------------------------------------ aliases and request user

def set_alias(alias: str, run_id: str) -> None:
    """Remember that `alias` (an upload token, a session id) belongs to `run_id`, so a later request
    carrying only that token continues the same run."""
    if alias and run_id:
        _guard(lambda c: c.set(f"trace:alias:{alias}", run_id, ex=TRACE_TTL_SECONDS))


def resolve_alias(alias: Optional[str]) -> Optional[str]:
    if not alias:
        return None
    return _guard(lambda c: c.get(f"trace:alias:{alias}"))


_request_user: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("tb_trace_user", default=None)


def set_request_user(user_id: Optional[str]):
    return _request_user.set(user_id)


def reset_request_user(token) -> None:
    _request_user.reset(token)


def request_user() -> Optional[str]:
    return _request_user.get()
