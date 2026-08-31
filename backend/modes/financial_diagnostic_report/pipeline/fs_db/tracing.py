"""
Optional OpenTelemetry tracing for fs_db, exported to Arize Phoenix.

Self-contained (imports nothing from rag/): fs_db owns its trace config so the
folder stays removable. Spans land in a SEPARATE Phoenix project (default
'fs-db-audit'), so the deterministic FS flow never mixes with the rag pipeline's
'cag-audit-rag'.

Design guarantees (an audit tool cannot tolerate observability that breaks the
thing it observes):
  • Opt-in.       Off unless FSDB_TRACE_ENABLED is truthy. When off, span() is a
                  pure no-op — zero cost.
  • Fail-open.    Any setup/export problem (Phoenix down, OTel packages missing)
                  degrades to no-op; it never raises into the flow.
  • Flushed.      A short-lived CLI would exit before the background exporter
                  sends its batch, so we register an atexit shutdown to flush.

Enable:  set FSDB_TRACE_ENABLED=1 (env or fs_db/.env), or pass `--trace` to the CLI,
with a Phoenix server running (python -m phoenix.server.main serve, UI :6006).
Needs: opentelemetry-sdk + opentelemetry-exporter-otlp-proto-http (already present
for the rag tracer; listed optional in requirements.txt).
"""
from __future__ import annotations
import atexit
import json
import os
from contextlib import contextmanager

from .config import TRACE_ENABLED, PHOENIX_ENDPOINT, PHOENIX_PROJECT

# OpenInference semantic-convention keys (hardcoded → no extra package needed).
_KIND = "openinference.span.kind"
_IN_VAL, _IN_MIME = "input.value", "input.mime_type"
_OUT_VAL, _OUT_MIME = "output.value", "output.mime_type"

_ENABLED = False
_INIT = False
_TRACER = None
_PROVIDER = None

# How long the background exporter may sit on a finished span before shipping it. The SDK
# default is 5000ms — five seconds of an empty project after a query, which reads as broken
# tracing. 100ms is the practical floor worth using: the exporter thread ships a finished
# span within a tenth of a second, which no human perceives as a delay, and it stays on the
# BACKGROUND thread. A SimpleSpanProcessor would export on the calling thread with no timer
# at all, but then a slow or hung collector would stall the query itself — trading a delay
# nobody can see for a failure everybody can. The timer stays; it is just short.
_SCHEDULE_DELAY_MS = 100
_FLUSH_TIMEOUT_MS = 3000

# Synchronous export. Set FSDB_TRACE_SYNC=1 to swap the batch processor for
# SimpleSpanProcessor, which ships each span on the CALLING thread the instant it ends — no
# timer, nothing buffered. Right while watching a UI, wrong under load: the exporter is then
# in the request path, so a slow collector backs up into the work. Hence the short explicit
# exporter timeout in this mode; the 10s default would let one dead collector add ten
# seconds per span.
_SYNC_EXPORT_TIMEOUT_S = 2


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _safe_print(msg: str) -> None:
    try:
        print(msg)
    except Exception:
        try:
            print(msg.encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass


def setup_tracing() -> bool:
    """Idempotent. Stand up a TracerProvider exporting to Phoenix over OTLP/HTTP.
    Reads the enable flag from the live environment so a --trace flag set at
    runtime still takes effect. Returns True if tracing is live."""
    global _ENABLED, _INIT, _TRACER, _PROVIDER
    if _INIT:
        return _ENABLED
    _INIT = True

    if not _truthy(os.getenv("FSDB_TRACE_ENABLED", TRACE_ENABLED)):
        return False
    try:
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        # Our OWN provider (not the global one) so we never clash with rag's tracer
        # if both ever share a process; spans still nest via the OTel context API.
        _PROVIDER = TracerProvider(resource=Resource.create({
            "service.name": PHOENIX_PROJECT,
            "openinference.project.name": PHOENIX_PROJECT,
        }))
        sync = _truthy(os.getenv("FSDB_TRACE_SYNC", "0"))
        if sync:
            from opentelemetry.sdk.trace.export import SimpleSpanProcessor
            _PROVIDER.add_span_processor(SimpleSpanProcessor(
                OTLPSpanExporter(endpoint=PHOENIX_ENDPOINT, timeout=_SYNC_EXPORT_TIMEOUT_S)))
            delay = 0
        else:
            delay = int(os.getenv("FSDB_TRACE_SCHEDULE_DELAY_MS", str(_SCHEDULE_DELAY_MS)))
            _PROVIDER.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=PHOENIX_ENDPOINT),
                                   schedule_delay_millis=delay))
        _TRACER = _PROVIDER.get_tracer("fs_db")
        _ENABLED = True

        import logging
        logging.getLogger("opentelemetry.sdk.trace.export").setLevel(logging.ERROR)
        logging.getLogger("opentelemetry.exporter.otlp").setLevel(logging.ERROR)
        atexit.register(shutdown_tracing)      # flush the batch before the CLI exits
    except Exception as e:                      # any SDK problem → stay off, never raise
        _safe_print(f"[fs_db.tracing] disabled (setup error: {e})")
        _ENABLED = False
        return _ENABLED
    _safe_print(f"[fs_db.tracing] Phoenix ON -> {PHOENIX_ENDPOINT} "
                f"(project '{PHOENIX_PROJECT}', {'SYNC' if sync else f'batched {delay}ms'})")
    return _ENABLED


def flush(timeout_millis: int | None = None) -> None:
    """Ship whatever has finished, now. Call it once a unit of work is COMPLETE.

    The batch processor is what keeps tracing off the calling path, but it also means a
    finished trace is invisible until its timer fires. Called after the report/flow is
    assembled there is nobody waiting on it, so it costs a LAN round trip nobody watches
    and makes the trace present the moment the work is done.

    Never raises, and the wait is bounded: a collector that has gone away must not hold the
    caller open, so a timeout is simply ignored.
    """
    if not _ENABLED or _PROVIDER is None:
        return
    try:
        _PROVIDER.force_flush(timeout_millis or _FLUSH_TIMEOUT_MS)
    except Exception:
        pass


def shutdown_tracing() -> None:
    """Force-flush + shut down the provider (called on process exit)."""
    global _PROVIDER
    p, _PROVIDER = _PROVIDER, None
    if p is not None:
        try:
            p.shutdown()
        except Exception:
            pass


class _NoopSpan:
    def set(self, *_a, **_k): ...
    def set_output(self, *_a, **_k): ...
    def event(self, *_a, **_k): ...


class _Span:
    def __init__(self, span):
        self._s = span

    def set(self, key, value):
        try:
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False, default=str)
            self._s.set_attribute(key, value)
        except Exception:
            pass

    def set_output(self, value, mime="text/plain"):
        try:
            if isinstance(value, (dict, list)):
                value, mime = json.dumps(value, ensure_ascii=False, default=str), "application/json"
            self._s.set_attribute(_OUT_VAL, "" if value is None else str(value))
            self._s.set_attribute(_OUT_MIME, mime)
        except Exception:
            pass

    def event(self, name, **attrs):
        try:
            self._s.add_event(name, {k: str(v) for k, v in attrs.items()})
        except Exception:
            pass


@contextmanager
def span(name: str, kind: str = "CHAIN", input=None):
    """Context manager yielding a span handle, made the CURRENT span for its
    duration (correct for the synchronous flow). No-op when tracing is off."""
    if not _INIT:
        setup_tracing()                 # lazy init on first span
    if not _ENABLED or _TRACER is None:
        yield _NoopSpan()
        return
    # The try guards span CREATION only — never the yield. Catching an exception thrown in
    # from the traced body and yielding a second time makes this generator yield twice,
    # which contextlib turns into "generator didn't stop after throw()", masking the real
    # error with a RuntimeError from the tracer. Body exceptions propagate untouched; OTel
    # marks the span ERROR on the way out, which is what makes a failed flow findable.
    try:
        cm = _TRACER.start_as_current_span(name)
    except Exception:
        yield _NoopSpan()               # tracing must never take down the flow
        return
    with cm as s:
        try:
            s.set_attribute(_KIND, kind)
            if input is not None:
                val = input if isinstance(input, str) else json.dumps(input, default=str)
                s.set_attribute(_IN_VAL, val)
                s.set_attribute(_IN_MIME,
                                "text/plain" if isinstance(input, str) else "application/json")
        except Exception:
            pass
        yield _Span(s)
