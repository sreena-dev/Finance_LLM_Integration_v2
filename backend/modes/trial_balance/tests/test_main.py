"""Tests for pipeline/tracing.py's Phoenix tracing wiring, and for router.py's
published endpoint contract.

Phoenix routes incoming OTLP spans to a project by the openinference.project.name
resource attribute specifically -- yukta.instrumentation.setup.init_tracing()'s OTLP
fallback path only sets service.name, so every span silently landed in Phoenix's
"default" project regardless of PHOENIX_PROJECT_NAME. Confirmed against a real,
running Phoenix instance during development (a span queried back from Phoenix's
own API landed in the "tb-v2" project); this pins the actual bug -- the resource
attribute key -- with a fast, isolated unit test that doesn't touch OTel's global
TracerProvider state.

Adapted from standalone TB-v2's tests/test_main.py: that file's TestModeContract
tested TB-v2's own standalone backend/main.py (a MODE dict + its own FastAPI app),
neither of which exists in this gateway -- this mode is mounted into the shared
gateway app via router.py + app/registry.py instead. The endpoint-inventory and
schema-hygiene assertions are worth keeping, so they're adapted here to build a
throwaway app around modes.trial_balance.router.router (same pattern
tests/api/test_routes_endpoints.py already uses), rather than dropped."""

from dataclasses import replace

import pytest

pytest.importorskip(
    "yukta",
    reason="yukta is installed from a local path and published to no index, so it is "
           "absent on a clean checkout -- see requirements.txt. These tests import "
           "modes.trial_balance.pipeline.agent, which needs it.",
)

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


class TestPhoenixTracingGate:
    """init_tracing() runs at import time, before get_agent() builds the first LLM
    client. Its two guard clauses decide whether tracing is wired at all, and both
    were previously untested -- a regression in either silently loses observability for
    the whole service rather than failing visibly."""

    def test_disabled_setting_short_circuits_before_touching_opentelemetry(self, monkeypatch):
        from modes.trial_balance.pipeline import tracing

        # settings is a frozen dataclass, so the whole object is swapped rather than
        # a field mutated.
        monkeypatch.setattr(tracing, "settings", replace(tracing.settings, ENABLE_TRACING=False))
        assert tracing.init_tracing() is False

    def test_an_already_registered_provider_is_not_replaced(self, monkeypatch):
        """Re-registering would detach spans already being collected by whatever
        registered first (a test harness, an APM agent, a second import)."""
        from modes.trial_balance.pipeline import tracing

        monkeypatch.setattr(tracing, "settings", replace(tracing.settings, ENABLE_TRACING=True))

        class AlreadyRegisteredProvider:
            pass

        monkeypatch.setattr(
            "opentelemetry.trace.get_tracer_provider", lambda: AlreadyRegisteredProvider()
        )
        assert tracing.init_tracing() is False

    def test_a_proxy_provider_is_treated_as_unregistered(self, monkeypatch):
        """OTel's default is a ProxyTracerProvider; that -- and NoOpTracerProvider --
        must read as "nothing registered yet", otherwise tracing never initialises."""
        from modes.trial_balance.pipeline import tracing

        monkeypatch.setattr(tracing, "settings", replace(tracing.settings, ENABLE_TRACING=True))

        class ProxyTracerProvider:
            pass

        monkeypatch.setattr("opentelemetry.trace.get_tracer_provider", lambda: ProxyTracerProvider())

        registered = {}
        monkeypatch.setattr("opentelemetry.trace.set_tracer_provider", lambda p: registered.update(p=p))

        assert tracing.init_tracing() is True
        assert "p" in registered

    def test_the_project_name_reaches_the_registered_provider(self, monkeypatch):
        """The whole point of the custom wiring: spans must carry
        openinference.project.name or Phoenix files them under "default"."""
        from openinference.semconv.resource import ResourceAttributes

        from modes.trial_balance.pipeline import tracing

        monkeypatch.setattr(
            tracing, "settings",
            replace(tracing.settings, ENABLE_TRACING=True, PHOENIX_PROJECT_NAME="tb-v2-under-test"),
        )

        class ProxyTracerProvider:
            pass

        monkeypatch.setattr("opentelemetry.trace.get_tracer_provider", lambda: ProxyTracerProvider())
        captured = {}
        monkeypatch.setattr("opentelemetry.trace.set_tracer_provider", lambda p: captured.update(p=p))

        tracing.init_tracing()
        attrs = captured["p"].resource.attributes
        assert attrs[ResourceAttributes.PROJECT_NAME] == "tb-v2-under-test"


class TestRouterContract:
    """router.py's prefix is the URL namespace app/registry.py's base_path for this
    mode must match, and its published endpoint set is this mode's public contract --
    a route silently added or dropped, or a decorator detached from its handler,
    should require updating this list."""

    @staticmethod
    def _app():
        from fastapi import FastAPI

        from modes.trial_balance.router import router

        app = FastAPI()
        app.include_router(router)
        return app

    def test_router_prefix_matches_the_gateway_registry_base_path(self):
        from modes.trial_balance.router import router

        assert router.prefix == "/api/trial-balance"

    def test_the_published_endpoint_inventory_is_exactly_what_is_expected(self):
        app = self._app()
        base = "/api/trial-balance"
        assert set(app.openapi()["paths"]) == {
            f"{base}/health",
            f"{base}/upload",
            f"{base}/preview",
            f"{base}/upload-mapped",
            f"{base}/companies/priority",
            f"{base}/documents",
            f"{base}/documents/{{doc_id}}",
            f"{base}/ask",
            f"{base}/ask-general",
            f"{base}/audit",
            f"{base}/audit/upload-grouping",
            f"{base}/audit/workbook",
            f"{base}/validate",
        }

    def test_the_removed_dead_parameter_is_no_longer_advertised(self):
        """`upload_doc_ids` was accepted by three request models and read by none --
        advertised in the schema as if it did something. Pinned so it cannot return
        without a handler behind it."""
        app = self._app()
        schemas = app.openapi()["components"]["schemas"]
        for model in ("AskGeneralRequest", "AuditRequest", "AuditWorkbookRequest"):
            assert "upload_doc_ids" not in schemas[model].get("properties", {})

    def test_upload_mapped_advertises_the_grouping_token(self):
        """The counterpart: /upload-mapped genuinely requires a grouping token now, so
        callers must be able to discover it from the schema."""
        app = self._app()
        props = app.openapi()["components"]["schemas"]["UploadMappedRequest"]["properties"]
        assert "grouping_token" in props
