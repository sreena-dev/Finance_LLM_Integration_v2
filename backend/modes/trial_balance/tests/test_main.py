"""Tests for backend/main.py's Phoenix tracing wiring.

Phoenix routes incoming OTLP spans to a project by the openinference.project.name
resource attribute specifically -- yukta.instrumentation.setup.init_tracing()'s OTLP
fallback path only sets service.name, so every span silently landed in Phoenix's
"default" project regardless of PHOENIX_PROJECT_NAME. Confirmed against a real,
running Phoenix instance during development (a span queried back from Phoenix's
own API landed in the "tb-v2" project); this pins the actual bug -- the resource
attribute key -- with a fast, isolated unit test that doesn't touch OTel's global
TracerProvider state."""

from modes.trial_balance.pipeline.tracing import _phoenix_resource_attributes


def test_project_name_resource_attribute_is_the_phoenix_routing_key():
    from openinference.semconv.resource import ResourceAttributes

    attrs = _phoenix_resource_attributes("tb-v2")
    assert attrs[ResourceAttributes.PROJECT_NAME] == "tb-v2"
    assert ResourceAttributes.PROJECT_NAME == "openinference.project.name"


def test_service_name_also_set_for_non_phoenix_otel_tooling():
    from opentelemetry.sdk.resources import SERVICE_NAME

    attrs = _phoenix_resource_attributes("tb-v2")
    assert attrs[SERVICE_NAME] == "tb-v2"


def test_different_project_names_produce_different_attributes():
    assert _phoenix_resource_attributes("tb-v2") != _phoenix_resource_attributes("tb-v2-staging")
