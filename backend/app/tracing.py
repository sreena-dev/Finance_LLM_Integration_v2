"""One tracing bootstrap for the whole gateway.

Every mode's pipeline emits spans the same way: yukta decorates ``Agent.run``
(CHAIN), the LLM clients' ``generate`` (LLM) and ``Agent.execute_tool`` (TOOL)
with ``@trace_yukta``, which calls ``opentelemetry.trace.get_tracer()``. That is
a no-op until a global TracerProvider is registered — so registering exactly one
here, before any mode is imported, is all four modes' tracing at once.

Why it is centralised rather than left to each mode
---------------------------------------------------
OpenTelemetry allows a single global TracerProvider: the first
``set_tracer_provider`` wins and later calls are warned about and ignored. Three
of the four modes previously tried to register their own, lazily, on first use —
so whichever mode a user happened to touch first decided the exporter protocol
and the Phoenix project for every other mode, and the rest silently disagreed:

  * Financial Statements registered from ``pipeline/api_server.py``, the
    standalone server from the source branch. This gateway never imports that
    module, so its setup never ran here at all — and its defaults were gRPC on
    port 4317, which is closed on the Phoenix host we export to.
  * SAR report went through yukta's ``init_tracing_from_config``, which names the
    project after the *agent* (``sar-prod-v3-<name>``), scattering one run across
    several projects.
  * SAR chat built a provider by hand with the right protocol but no Phoenix
    project attribute at all, so its spans landed in ``default``.
  * Trial Balance had no tracing.

Registering once, first, means every mode's spans carry the same project and
leave by the same exporter. The per-mode setups are all guarded ("skip if a
provider already exists"), so they now back off instead of competing.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_registered = False


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _has_real_provider() -> bool:
    """True if something already registered a non-default TracerProvider."""
    try:
        from opentelemetry import trace

        name = type(trace.get_tracer_provider()).__name__
        return name not in ("ProxyTracerProvider", "DefaultTracerProvider", "NoOpTracerProvider")
    except Exception:  # noqa: BLE001 - tracing must never break startup
        return False


def setup_tracing() -> bool:
    """Register the global TracerProvider. Returns True if this call did it.

    Never raises: a tracing backend being unreachable is not a reason for the
    gateway to fail to start, and an unreachable collector already degrades to
    dropped span batches in the background rather than failed requests.
    """
    global _registered

    if not _truthy(os.environ.get("ENABLE_TRACING")):
        logger.info("Tracing disabled (ENABLE_TRACING is not true).")
        return False
    if _registered or _has_real_provider():
        _registered = True
        return False

    endpoint = os.environ.get("PHOENIX_ENDPOINT", "http://localhost:6006/v1/traces")
    project = os.environ.get("PHOENIX_PROJECT_NAME", "artha-integrated")
    # http/protobuf, not gRPC. The Phoenix instance we export to serves OTLP over
    # its HTTP port and has 4317 closed, and gRPC against an HTTP `/v1/traces`
    # URL fails as a connection error with no useful message.
    protocol = os.environ.get("PHOENIX_PROTOCOL", "http/protobuf")

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        # Built with the plain OTel SDK rather than phoenix.otel.register().
        # That helper does exactly this, but reaching it imports
        # phoenix.trace.dsl.filter, which defines a frozen dataclass with a
        # mappingproxy default — rejected outright by Python 3.11's dataclasses
        # ("mutable default ... for field boolean_names"). The import blows up
        # before any tracing is configured, so the convenience wrapper is the
        # one thing that cannot be used here. This path has no such dependency.
        #
        # `openinference.project.name` is the resource attribute Phoenix routes
        # spans by; without it everything lands in the "default" project no
        # matter what the collector URL says.
        resource = Resource.create(
            {
                "openinference.project.name": project,
                "service.name": os.environ.get("ARTHA_SERVICE_NAME", "artha-gateway"),
            }
        )
        provider = TracerProvider(resource=resource)

        # BatchSpanProcessor, never the Simple one: that exports inside
        # span.end(), so every agent run, LLM call and tool call would block on a
        # round trip to Phoenix. On a pipeline making ~20 sequential tool calls
        # that turns a slow collector into a slow product.
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)

        _registered = True
        logger.info(
            "Tracing registered: project=%s endpoint=%s protocol=%s (batch)",
            project,
            endpoint,
            protocol,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tracing setup failed (%s: %s) — continuing untraced.", type(exc).__name__, exc)
        return False
