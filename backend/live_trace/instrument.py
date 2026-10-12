"""Attach the live run trace to the gateway from the outside.

NOTHING IN THE EXISTING CODE IS EDITED. At startup `install(app)` wraps behaviour that already
exists, in memory, and the wrappers do nothing unless a request has been bound to a run:

  * the registered pipeline tools (one traced step per tool call)
  * every LLM call, at the yukta client class
  * the ingest stages (parse, quality gate, resolver, LLM classification, validation gate, writes)
  * the Trial Balance route handlers (upload, upload-mapped, audit, ask, validate), which open and
    close a run
  * a small middleware that notes who is calling, so an upload that has no user parameter of its
    own is still filed under the right person

Removing `live_trace/` and starting the gateway the usual way (`app.main:app`) restores the
original behaviour exactly.

Each patch is independent: if one target has moved or been renamed, that one is skipped with a
log line and the rest still install.
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
import importlib
import inspect
import logging
import re
import uuid
from typing import Any, Callable, Optional

from live_trace import recorder as rec

logger = logging.getLogger("live_trace")

_TB = "modes.trial_balance"
_TOOLS = f"{_TB}.pipeline.tools"
_BASE = "/api/trial-balance"
_installed = False
_SESSION_RE = re.compile(r"sessions[/\\]([0-9a-fA-F-]{36})")


def _safe(fn: Callable, default=None):
    """Run a describe/result lambda; a bug in tracing must never reach the pipeline."""
    try:
        return fn()
    except Exception:
        return default


# ---------------------------------------------------------------------------- tools

def _run_for_kwargs(kwargs: dict) -> Optional[str]:
    """The run for a tool called from a thread that did not inherit the request context: the
    session id embedded in output_dir maps back to the run through an alias."""
    out = kwargs.get("output_dir")
    match = _SESSION_RE.search(str(out)) if out else None
    return rec.resolve_alias(f"sess:{match.group(1)}") if match else None


def _wrap_tool(name: str, func: Callable) -> Callable:
    if getattr(func, "_live_trace", False):
        return func

    @functools.wraps(func)
    def traced(*args, **kwargs):
        binder = contextlib.nullcontext() if rec.current_run_id() else rec.bind(_safe(lambda: _run_for_kwargs(kwargs)))
        detail = {"kind": "tool"}
        if kwargs.get("tb_doc_id"):
            detail["doc_id"] = str(kwargs["tb_doc_id"])
        with binder, rec.step(name, **detail) as step:
            response = func(*args, **kwargs)
            if isinstance(response, dict):
                failed = (response.get("execution_status") == "FAILED"
                          or response.get("pipeline_status") in ("FAILED", "HALTED"))
                step.set(status="failed" if failed else "ok", pipeline_status=response.get("pipeline_status"),
                         message=str(response.get("message"))[:300] if failed else None)
            return response

    traced._live_trace = True
    return traced


def _instrument_tools() -> None:
    tools = importlib.import_module(_TOOLS)
    for name, entry in tools.TOOL_REGISTRY.items():
        entry.func = _wrap_tool(name, entry.func)
    # call_tool() caches name -> func on first use; drop it so it is rebuilt from the wrapped entries.
    agent = importlib.import_module(f"{_TB}.pipeline.agent")
    agent._tool_registry = None
    logger.info("live_trace: %d tools traced", len(tools.TOOL_REGISTRY))


# ---------------------------------------------------------------------------- LLM

_in_llm: contextvars.ContextVar[bool] = contextvars.ContextVar("live_trace_in_llm", default=False)


def _patch_generate(cls: type) -> None:
    original = cls.__dict__.get("generate")
    if original is None or getattr(original, "_live_trace", False):
        return

    @functools.wraps(original)
    def traced(self, messages=None, *args, **kwargs):
        # A subclass's generate() calling super().generate() is one LLM call, not two.
        if _in_llm.get() or not rec.current_run_id():
            return original(self, messages, *args, **kwargs)
        marker = _in_llm.set(True)
        call = _safe(lambda: rec.llm_started(model=getattr(self, "model_name", None), messages=messages))
        try:
            response = original(self, messages, *args, **kwargs)
        except BaseException as exc:
            _safe(lambda: rec.llm_finished(call, error=exc))
            raise
        else:
            _safe(lambda: rec.llm_finished(call, response=response))
            return response
        finally:
            _in_llm.reset(marker)

    traced._live_trace = True
    cls.generate = traced


def _instrument_llm() -> None:
    agent = importlib.import_module(f"{_TB}.pipeline.agent")
    seen = []
    for cls in (agent.SafeVLLMClient, *agent.SafeVLLMClient.__mro__[1:]):
        if cls is object or cls in seen or "generate" not in cls.__dict__:
            continue
        seen.append(cls)
        _patch_generate(cls)
    logger.info("live_trace: LLM calls traced on %s", [c.__name__ for c in seen])


# ---------------------------------------------------------------------------- stages

def _patch_step(module: str, attr: str, label: str, *, detail: Callable = None, result: Callable = None,
                skip_empty: bool = False) -> None:
    """Wrap `module.attr` so each call shows up as one step while a run is bound."""
    try:
        mod = importlib.import_module(module)
        original = getattr(mod, attr)
    except Exception as exc:
        logger.info("live_trace: skipped %s.%s (%s)", module, attr, exc)
        return
    if getattr(original, "_live_trace", False):
        return

    @functools.wraps(original)
    def traced(*args, **kwargs):
        if not rec.current_run_id() or (skip_empty and not _safe(lambda: args[0], True)):
            return original(*args, **kwargs)
        with rec.step(label, **(_safe(lambda: detail(args, kwargs), {}) if detail else {})) as step:
            out = original(*args, **kwargs)
            if result:
                step.set(**(_safe(lambda: result(out), {}) or {}))
            return out

    traced._live_trace = True
    setattr(mod, attr, traced)


def _patch_note(module: str, attr: str, describe: Callable) -> None:
    """Wrap `module.attr` so its result is reported as a one-line note."""
    try:
        mod = importlib.import_module(module)
        original = getattr(mod, attr)
    except Exception as exc:
        logger.info("live_trace: skipped %s.%s (%s)", module, attr, exc)
        return
    if getattr(original, "_live_trace", False):
        return

    @functools.wraps(original)
    def traced(*args, **kwargs):
        out = original(*args, **kwargs)
        if rec.current_run_id():
            text = _safe(lambda: describe(out))
            if text:
                _safe(lambda: rec.note(text))
        return out

    traced._live_trace = True
    setattr(mod, attr, traced)


def _instrument_stages() -> None:
    db_bridge, classify_doc, normalize = (f"{_TOOLS}.db_bridge", f"{_TOOLS}.classify_document", f"{_TOOLS}.normalize")

    _patch_step(db_bridge, "parse_tb_input", "Detect input format and parse workbook",
                detail=lambda a, k: {"files": len(a[0])},
                result=lambda out: {"documents": len(out), "rows": sum(len(p.tb_rows) for p in out)})
    _patch_step(db_bridge, "assess_ingestion_intake", "Intake checks (sheets, headers, formula errors)",
                result=lambda out: {"warnings": len(out)})
    _patch_step(db_bridge, "upsert_live_document_and_lines", "Write to LIVE staging (database)",
                result=lambda out: {"rows": out[1]})
    _patch_step(db_bridge, "write_parquet_atomic", "Write canonical_tb.parquet")

    _patch_step(classify_doc, "run_input_quality_gate", "Input quality gate",
                detail=lambda a, k: {"rows": len(a[0])},
                result=lambda out: {"status": out.status, "warnings": len(out.warnings)})
    _patch_note(classify_doc, "classify_quality_tier",
                lambda out: f"Quality tier {out.tier}: {out.explanation}")
    _patch_note(classify_doc, "check_confirmation_requirement",
                lambda out: None if out is None else f"Confirmation gate: data-quality confirmation needed ({out.reason})")
    _patch_step(classify_doc, "build_verbatim_rows", "Copy grouping verbatim (no LLM needed)",
                detail=lambda a, k: {"rows": len(a[0])})
    _patch_step(classify_doc, "resolve_grouping_hints", "Taxonomy resolver (exact, alias, candidates, rules)",
                detail=lambda a, k: {"rows": len(a[0])},
                result=lambda out: {"resolved": sum(1 for r in out[1].values() if getattr(r, "status", None) == "RESOLVED")})

    _patch_step(normalize, "classify_staged", "LLM: staged narrowing (main head, sub head 1, sub head 2)",
                detail=lambda a, k: {"rows": len(a[0])}, result=lambda out: {"resolved": len(out)}, skip_empty=True)
    _patch_step(normalize, "classify_all", "LLM: single-shot classification",
                detail=lambda a, k: {"rows": len(a[0])}, result=lambda out: {"resolved": len(out)}, skip_empty=True)
    _patch_step(normalize, "run_validation_gate", "Validation gate (check against taxonomy, one repair attempt)",
                detail=lambda a, k: {"rows": len(a[0])},
                result=lambda out: {"mapped": sum(1 for r in out if getattr(r, "mapped_status", None) == "MAPPED")})

    router = f"{_TB}.router"
    _patch_step(router, "_run_layer1_precheck", "Load document and run Layer 1 validation")
    _patch_step(router, "_run_core_analytics_chain", "Deterministic analytics chain")
    _patch_step(router, "_run_comparison_analytics_chain", "Comparison analytics chain")
    _patch_step(router, "_finalize_audit_result", "Assemble the audit result and reports")
    _patch_step(router, "invoke_scoped", "Agent: LLM completes the remaining analysis")

    # Each new pipeline session is linked to the run that created it, so a tool that runs in a thread
    # without the request context can still find its run (see _run_for_kwargs).
    try:
        r = importlib.import_module(router)
        original = r.create_session
        if not getattr(original, "_live_trace", False):
            @functools.wraps(original)
            def create_session(*args, **kwargs):
                session_id = original(*args, **kwargs)
                run_id = rec.current_run_id()
                if run_id and session_id:
                    _safe(lambda: rec.set_alias(f"sess:{session_id}", run_id))
                return session_id

            create_session._live_trace = True
            r.create_session = create_session
    except Exception as exc:
        logger.info("live_trace: skipped create_session link (%s)", exc)


# ---------------------------------------------------------------------------- routes

def _outcome(result: Any) -> None:
    """Record how a handler ended when it returns a result instead of raising."""
    body = result
    if body.__class__.__name__ == "JSONResponse":
        rec.set_final_status("failed")
    elif isinstance(body, dict):
        if body.get("pipeline_status") == "CONFIRMATION_REQUIRED":
            rec.set_final_status("waiting")
        elif (body.get("execution_status") == "FAILED" or body.get("pipeline_status") == "FAILED"
              or body.get("available") is False):
            rec.set_final_status("failed")


def _wrap_route(route, spec: dict) -> None:
    original = route.dependant.call
    if getattr(original, "_live_trace", False):
        return

    def begin(kwargs: dict):
        user = kwargs.get("user")
        user_id = getattr(user, "user_id", None) or rec.request_user()
        run_id = spec["run_id"](kwargs) if "run_id" in spec else None
        return (run_id or uuid.uuid4().hex), user_id, _safe(lambda: spec["label"](kwargs))

    def finish(kwargs: dict, result: Any) -> None:
        _outcome(result)
        if "after" in spec:
            _safe(lambda: spec["after"](kwargs, result))

    if inspect.iscoroutinefunction(original):
        @functools.wraps(original)
        async def traced(*args, **kwargs):
            run_id, user_id, label = begin(kwargs)
            with rec.run(run_id, kind=spec["kind"], user_id=user_id, label=label, end_status=spec.get("end", "ok")):
                try:
                    result = await original(*args, **kwargs)
                except Exception as exc:
                    _safe(lambda: spec["on_error"](kwargs, exc)) if "on_error" in spec else None
                    raise
                finish(kwargs, result)
                return result
    else:
        @functools.wraps(original)
        def traced(*args, **kwargs):
            run_id, user_id, label = begin(kwargs)
            with rec.run(run_id, kind=spec["kind"], user_id=user_id, label=label, end_status=spec.get("end", "ok")):
                result = original(*args, **kwargs)
                finish(kwargs, result)
                return result

    traced._live_trace = True
    route.dependant.call = traced


def _alias_upload(kwargs: dict, result: Any) -> None:
    token = result.get("token") if isinstance(result, dict) else None
    if token and rec.current_run_id():
        rec.set_alias(token, rec.current_run_id())


def _alias_upload_error(kwargs: dict, exc: Exception) -> None:
    detail = getattr(exc, "detail", None)
    token = detail.get("preview_token") if isinstance(detail, dict) else None
    if token and rec.current_run_id():
        rec.set_alias(token, rec.current_run_id())


def _route_specs() -> dict:
    return {
        ("POST", f"{_BASE}/upload"): {
            "kind": "upload", "end": "waiting",
            "label": lambda k: k["file"].filename, "after": _alias_upload, "on_error": _alias_upload_error},
        ("POST", f"{_BASE}/upload-mapped"): {
            "kind": "upload", "label": lambda k: None,
            "run_id": lambda k: rec.resolve_alias(k["request"].token)},
        ("POST", f"{_BASE}/audit"): {
            "kind": "audit",
            "label": lambda k: "{} - {}".format("Two TB comparison" if k["request"].doc_id_prior else "Single TB audit",
                                                 k["request"].doc_label or k["request"].doc_id)},
        ("POST", f"{_BASE}/ask"): {"kind": "ask", "label": lambda k: (k["request"].question or "")[:80]},
        ("POST", f"{_BASE}/ask-general"): {"kind": "ask", "label": lambda k: (k["request"].question or "")[:80]},
        ("POST", f"{_BASE}/validate"): {"kind": "validate", "label": lambda k: f"Validate - {k['request'].doc_id}"},
    }


def _instrument_routes(app) -> None:
    specs = _route_specs()
    done = 0
    for route in app.routes:
        for method in getattr(route, "methods", None) or ():
            spec = specs.get((method, getattr(route, "path", "")))
            if spec is not None and hasattr(route, "dependant"):
                _wrap_route(route, spec)
                done += 1
    logger.info("live_trace: %d route handlers open and close runs", done)


# ---------------------------------------------------------------------------- request user

class _RequestUser:
    """Pure ASGI middleware: remember who is calling, for handlers with no `user` parameter of their own."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            await self.app(scope, receive, send)
            return
        user_id = None
        for key, value in scope.get("headers", ()):
            if key == b"authorization" and value[:7].lower() == b"bearer ":
                try:
                    from app.auth.security import read_token

                    user_id = read_token(value[7:].decode()).user_id
                except Exception:
                    user_id = None
                break
        marker = rec.set_request_user(user_id)
        try:
            await self.app(scope, receive, send)
        finally:
            rec.reset_request_user(marker)


# ---------------------------------------------------------------------------- install

def install(app) -> None:
    """Attach the trace to `app`. Idempotent; every part is optional."""
    global _installed
    if _installed:
        return
    _installed = True

    for name, step in (("tools", _instrument_tools), ("llm", _instrument_llm), ("stages", _instrument_stages)):
        try:
            step()
        except Exception:
            logger.exception("live_trace: could not instrument %s; continuing without it", name)

    try:
        from fastapi import Depends

        from app.auth.deps import require_user
        from live_trace.api import router

        app.include_router(router, dependencies=[Depends(require_user)])
        _instrument_routes(app)
        app.add_middleware(_RequestUser)
    except Exception:
        logger.exception("live_trace: could not mount the trace API; continuing without it")
    logger.info("live_trace: installed")
