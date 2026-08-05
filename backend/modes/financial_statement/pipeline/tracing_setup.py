"""
tracing_setup.py — One-call Arize Phoenix tracing bootstrap for this app.

yukta.Agent.run() (CHAIN spans), the LLM clients' generate() (LLM spans), and
Agent.execute_tool() (TOOL spans — covers every registered tool generically,
including get_reporting_framework) are already decorated with @trace_yukta
(see yukta/yukta/instrumentation/decorators.py). Those decorators call
opentelemetry.trace.get_tracer(), which is a no-op until a TracerProvider is
registered — that registration is all this module does.

Call setup_tracing() once at process startup (api_server.py's lifespan,
app.py's module scope). Safe to call more than once — both the local Phoenix
app launch and yukta.instrumentation.init_tracing() are idempotent-guarded.
"""

import logging
import sys

from tools_fs import Config as config

logger = logging.getLogger("simple_rag.tracing")

_phoenix_app_launched = False
_tracer_registered = False


def _has_real_provider() -> bool:
    """True if a non-default (SDK) TracerProvider is already globally registered."""
    try:
        from opentelemetry import trace
        provider = trace.get_tracer_provider()
        name = type(provider).__name__
        return name not in ("ProxyTracerProvider", "DefaultTracerProvider", "NoOpTracerProvider")
    except Exception:
        return False


def setup_tracing() -> bool:
    """Launch the local Phoenix UI (if configured) and register the tracer.

    Registers with batch=True (async BatchSpanProcessor) explicitly — the
    plain phoenix.otel.register() / yukta.instrumentation.init_tracing()
    default to a synchronous SimpleSpanProcessor, which blocks every
    span.end() (i.e. every agent run / LLM call / tool call) on the span
    export call. If Phoenix is slow or unreachable that adds many seconds
    of latency per request, so this must not be left as the default.

    Returns True if a TracerProvider was registered by this call, False if
    tracing is disabled or already set up.
    """
    global _phoenix_app_launched, _tracer_registered

    if not config.ENABLE_TRACING:
        return False
    if _tracer_registered or _has_real_provider():
        _tracer_registered = True
        return False

    if config.PHOENIX_LAUNCH_LOCAL and not _phoenix_app_launched:
        # Phoenix prints an emoji banner ("🌍 To view the Phoenix app...") on
        # launch. On Windows, stdout/stderr default to the console's ANSI
        # codepage (e.g. cp1252) when not attached to a real UTF-8 terminal
        # (as happens when output is redirected to a file/log), which raises
        # UnicodeEncodeError on that print — the server itself starts fine,
        # but the exception would otherwise make this look like a failure.
        for stream in (sys.stdout, sys.stderr):
            try:
                if hasattr(stream, "reconfigure"):
                    stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
        try:
            import phoenix as px
            px.launch_app()
            _phoenix_app_launched = True
            logger.info("Phoenix UI launched locally — http://localhost:6006")
        except Exception as exc:
            logger.warning(
                f"Could not launch local Phoenix app ({exc}). If you run Phoenix "
                "separately (e.g. `phoenix serve`), tracing will still work; "
                "otherwise span exports will fail silently in the background."
            )

    try:
        import phoenix.otel as otel
        otel.register(
            project_name=config.PHOENIX_PROJECT_NAME,
            endpoint=config.PHOENIX_ENDPOINT,
            protocol=config.PHOENIX_PROTOCOL,
            batch=True,
            auto_instrument=False,
        )
        _tracer_registered = True
        logger.info(
            f"Phoenix tracing registered (project={config.PHOENIX_PROJECT_NAME}, batch mode)."
        )
        return True
    except ImportError:
        logger.debug("phoenix.otel unavailable — falling back to yukta.instrumentation.init_tracing.")
    except Exception as exc:
        logger.warning(f"phoenix.otel.register failed ({exc}) — falling back to yukta.instrumentation.init_tracing.")

    try:
        from yukta.instrumentation import init_tracing
    except Exception as exc:
        logger.warning(f"yukta.instrumentation unavailable — tracing disabled ({exc})")
        return False

    ok = init_tracing(
        endpoint=config.PHOENIX_ENDPOINT,
        project_name=config.PHOENIX_PROJECT_NAME,
        enabled=True,
    )
    _tracer_registered = _tracer_registered or ok
    return ok
