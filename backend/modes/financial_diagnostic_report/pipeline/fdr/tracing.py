"""
Optional OpenTelemetry tracing for the FDR flow, exported to Arize Phoenix.

Self-contained, and structurally duplicated from `fs_db/tracing.py` rather than imported
from it — the two packages own their own trace config so each stays independently
removable. Spans land in their OWN Phoenix project (default 'fdr-planning'), so the
planning flow never mixes with 'fs-db-audit' or the rag pipeline's 'cag-audit-rag'.

The same three guarantees the fs_db tracer makes, for the same reason — an audit tool
cannot tolerate observability that breaks the thing it observes:

  • Opt-in.     Off unless FDR_TRACE_ENABLED is truthy. When off, span() is a pure no-op
                and NO OpenTelemetry package is imported — `fdr` stays stdlib-only, which
                is the property that lets it run anywhere.
  • Fail-open.  Any setup or export problem (Phoenix down, packages missing) degrades to
                no-op. It never raises into the flow.
  • Flushed.    A short-lived CLI would exit before the background exporter sends its
                batch, so an atexit shutdown forces the flush.

WHAT IS WORTH TRACING IN A DETERMINISTIC FLOW
---------------------------------------------
There is no model call here and no latency worth chasing. The value is different: a run
that reports nothing looks identical to a run that found nothing, and the trace is what
tells them apart. Each stage records what it could NOT do and why — abstained signals with
their missing inputs, clusters that went unchecked, dimensions that could not be assessed,
themes dropped by capacity. That turns "the FDR said nothing again" into a specific,
searchable answer.

Enable:  set FDR_TRACE_ENABLED=1, or pass `--trace` to the CLI, with Phoenix running
         (python -m phoenix.server.main serve; UI on :6006).
"""
from __future__ import annotations
import atexit
import json
import os
from contextlib import contextmanager

# Config lives here, not in a config.py, because it is the only configuration `fdr` has.
TRACE_ENABLED = os.getenv("FDR_TRACE_ENABLED", "0")
# Default is the SHARED LAN Phoenix. It is hardcoded here, not read from the repo .env,
# because `fdr` is stdlib-only and owns its own config — and the CLI (`--trace`) never
# passes through the `rag/fdr_service` bridge that copies .env into the environment.
# Override with FDR_PHOENIX_ENDPOINT, or PHOENIX_ENDPOINT for the whole stack.
PHOENIX_ENDPOINT = os.getenv(
    "FDR_PHOENIX_ENDPOINT",
    os.getenv("PHOENIX_ENDPOINT", "http://10.10.116.160:6006/v1/traces"),
)
PHOENIX_PROJECT = os.getenv("FDR_PHOENIX_PROJECT", "fdr-planning")

# OpenInference semantic-convention keys, hardcoded so no extra package is needed.
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

# Synchronous export. Set FDR_TRACE_SYNC=1 to swap the batch processor for
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


def enable() -> None:
    """Turn tracing on for this process (the CLI's --trace flag).

    Must be called before the first span. Resets the init latch so a flag parsed after
    import still takes effect — otherwise `--trace` would silently do nothing whenever
    anything had already traced.
    """
    global _INIT
    os.environ["FDR_TRACE_ENABLED"] = "1"
    _INIT = False


def setup_tracing() -> bool:
    """Idempotent. Stand up a TracerProvider exporting to Phoenix over OTLP/HTTP."""
    global _ENABLED, _INIT, _TRACER, _PROVIDER
    if _INIT:
        return _ENABLED
    _INIT = True

    if not _truthy(os.getenv("FDR_TRACE_ENABLED", TRACE_ENABLED)):
        return False
    try:
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        endpoint = os.getenv("FDR_PHOENIX_ENDPOINT",
                             os.getenv("PHOENIX_ENDPOINT", PHOENIX_ENDPOINT))
        project = os.getenv("FDR_PHOENIX_PROJECT", PHOENIX_PROJECT)

        # Our OWN provider, not the global one, so we never clash with the fs_db or rag
        # tracers if they ever share a process. Spans still nest via the OTel context API.
        _PROVIDER = TracerProvider(resource=Resource.create({
            "service.name": project,
            "openinference.project.name": project,
        }))
        sync = _truthy(os.getenv("FDR_TRACE_SYNC", "0"))
        if sync:
            from opentelemetry.sdk.trace.export import SimpleSpanProcessor
            _PROVIDER.add_span_processor(SimpleSpanProcessor(
                OTLPSpanExporter(endpoint=endpoint, timeout=_SYNC_EXPORT_TIMEOUT_S)))
            delay = 0
        else:
            delay = int(os.getenv("FDR_TRACE_SCHEDULE_DELAY_MS", str(_SCHEDULE_DELAY_MS)))
            _PROVIDER.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint), schedule_delay_millis=delay))
        _TRACER = _PROVIDER.get_tracer("fdr")
        _ENABLED = True

        import logging
        logging.getLogger("opentelemetry.sdk.trace.export").setLevel(logging.ERROR)
        logging.getLogger("opentelemetry.exporter.otlp").setLevel(logging.ERROR)
        atexit.register(shutdown_tracing)
    except Exception as e:                       # any SDK problem -> stay off, never raise
        _safe_print(f"[fdr.tracing] disabled (setup error: {e})")
        _ENABLED = False
        return _ENABLED
    _safe_print(f"[fdr.tracing] Phoenix ON -> {endpoint} (project '{project}', "
                f"{'SYNC' if sync else f'batched {delay}ms'})")
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
    global _PROVIDER
    p, _PROVIDER = _PROVIDER, None
    if p is not None:
        try:
            p.shutdown()
        except Exception:
            pass


def is_enabled() -> bool:
    return _ENABLED


class _NoopSpan:
    def set(self, *_a, **_k): ...
    def set_output(self, *_a, **_k): ...
    def event(self, *_a, **_k): ...


class _Span:
    def __init__(self, span):
        self._s = span

    def set(self, key, value):
        try:
            if isinstance(value, (dict, list, tuple)):
                value = json.dumps(list(value) if isinstance(value, tuple) else value,
                                   ensure_ascii=False, default=str)
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
    """Context manager yielding a span handle, current for its duration. No-op when off."""
    if not _INIT:
        setup_tracing()                  # lazy init on first span
    if not _ENABLED or _TRACER is None:
        yield _NoopSpan()
        return
    # The try guards span CREATION only. It must not wrap the yield: an exception from the
    # traced body is thrown back in at the yield, and catching it here to yield a second
    # time makes this generator yield twice, which contextlib reports as "generator didn't
    # stop after throw()" — masking the real error with a RuntimeError from the tracer.
    # That is the precise failure this module promises never to cause. Body exceptions now
    # propagate untouched, and OTel marks the span ERROR on the way out, which is the
    # behaviour that makes a failed run findable in Phoenix at all.
    try:
        cm = _TRACER.start_as_current_span(name)
    except Exception:
        yield _NoopSpan()                # tracing must never take down the flow
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
