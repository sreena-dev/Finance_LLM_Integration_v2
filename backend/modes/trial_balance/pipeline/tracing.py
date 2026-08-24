"""Phoenix tracing bootstrap for the Trial Balance mode.

Formerly TB-v2's standalone backend/main.py (deleted on migration -- the gateway's
own app/main.py owns FastAPI app setup now, this module keeps only the tracing
concern). Uses the gateway's SHARED tracing config (ENABLE_TRACING, PHOENIX_ENDPOINT,
PHOENIX_PROJECT_NAME -- one Phoenix project for the whole platform, matching how
Financial Statement's tracing_setup.py already reads the same three names), not a
TB-only project name.

init_tracing() must run before pipeline.agent.get_agent() constructs the first LLM
client -- called at import time from pipeline/agent.py, early enough since the agent
itself is built lazily on first request.
"""

import logging

from modes.trial_balance.pipeline.config import settings

logger = logging.getLogger(__name__)


def _phoenix_resource_attributes(project_name: str) -> dict:
    """The resource attributes Phoenix needs to route OTLP spans to `project_name`
    instead of silently landing them in its "default" project. Split out from
    init_tracing so this routing-key logic (the actual bug this function exists to
    fix) is unit-testable without touching OTel's global TracerProvider state."""
    from openinference.semconv.resource import ResourceAttributes
    from opentelemetry.sdk.resources import SERVICE_NAME

    return {SERVICE_NAME: project_name, ResourceAttributes.PROJECT_NAME: project_name}


def init_tracing() -> bool:
    """Register a TracerProvider so yukta's @trace_yukta spans (e.g. VLLMClient.generate
    on every LLM call the agent makes) actually export to Phoenix, instead of silently
    no-op'ing against OTel's default ProxyTracerProvider.

    Phoenix routes incoming OTLP spans to a project by the `openinference.project.name`
    resource attribute specifically (see phoenix.otel.otel.PROJECT_NAME /
    ResourceAttributes.PROJECT_NAME) -- without it every span silently lands in
    Phoenix's "default" project instead of PHOENIX_PROJECT_NAME.
    """
    if not settings.ENABLE_TRACING:
        return False

    from opentelemetry import trace

    if type(trace.get_tracer_provider()).__name__ not in ("ProxyTracerProvider", "NoOpTracerProvider"):
        logger.debug("init_tracing: a TracerProvider is already registered -- skipping.")
        return False

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create(_phoenix_resource_attributes(settings.PHOENIX_PROJECT_NAME))
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.PHOENIX_ENDPOINT)))
    trace.set_tracer_provider(provider)
    logger.info(
        "Phoenix tracing registered (project=%s, endpoint=%s)", settings.PHOENIX_PROJECT_NAME, settings.PHOENIX_ENDPOINT
    )
    return True
