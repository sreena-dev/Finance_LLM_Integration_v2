"""Tests for backend/agent.py's connection layer, prompt loading and agent construction.

Companion to test_agent_wiring.py (which covers tool-schema derivation). This file
covers the parts that decide whether a request reaches the LLM at all -- the reachability
probe now gating /audit, /ask and /ask-general, the runtime-prompt extraction, the
tool-dispatch entry point, and the lazily-built agent itself.
"""

import socket as socket_mod
from dataclasses import replace

import pytest

pytest.importorskip(
    "yukta",
    reason="yukta is installed from a local path and published to no index, so it is "
           "absent on a clean checkout -- see requirements.txt. These tests import "
           "backend.agent, which needs it.",
)

import modes.trial_balance.pipeline.agent as agent_mod
from modes.trial_balance.pipeline.config import settings


class TestLlmReachable:
    """llm_reachable() is a raw TCP probe, deliberately not an HTTP request through the
    yukta client -- that client retries 3x internally and the reasoning loops retry the
    whole call another 3x on top, so a down endpoint costs up to 9 real attempts per
    step. This probe is what stops a doomed run before it starts. A false positive
    re-introduces the multi-minute hang; a false negative reports the assistant as down
    while it is up."""

    def test_a_listening_socket_is_reported_reachable(self, monkeypatch):
        with socket_mod.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            port = server.getsockname()[1]

            monkeypatch.setattr(
                agent_mod, "settings", replace(settings, LLM_BASE_URL=f"http://127.0.0.1:{port}/v1")
            )
            assert agent_mod.llm_reachable() is True

    def test_a_closed_port_is_reported_unreachable(self, monkeypatch):
        # Bind then release, so the port is known-free rather than merely assumed.
        with socket_mod.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        monkeypatch.setattr(
            agent_mod, "settings", replace(settings, LLM_BASE_URL=f"http://127.0.0.1:{port}/v1")
        )
        assert agent_mod.llm_reachable(timeout=1.0) is False

    def test_a_url_without_a_host_is_unreachable_not_an_exception(self, monkeypatch):
        monkeypatch.setattr(agent_mod, "settings", replace(settings, LLM_BASE_URL="not-a-url"))
        assert agent_mod.llm_reachable() is False

    def test_socket_errors_are_swallowed_and_reported_as_unreachable(self, monkeypatch):
        """The probe must never raise: it runs inside request handlers whose whole
        purpose is to degrade gracefully when the endpoint is down."""

        def boom(*a, **k):
            raise OSError("network is unreachable")

        monkeypatch.setattr("socket.create_connection", boom)
        assert agent_mod.llm_reachable() is False

    def test_default_scheme_ports_are_inferred_when_the_url_omits_one(self, monkeypatch):
        """urlparse yields no port for https://host/v1; passing None through would raise
        inside create_connection instead of probing 443."""
        captured = {}

        def fake_connect(addr, timeout=None):
            captured["addr"] = addr
            raise OSError("not actually connecting")

        monkeypatch.setattr("socket.create_connection", fake_connect)

        monkeypatch.setattr(agent_mod, "settings", replace(settings, LLM_BASE_URL="https://example.invalid/v1"))
        agent_mod.llm_reachable()
        assert captured["addr"] == ("example.invalid", 443)

        monkeypatch.setattr(agent_mod, "settings", replace(settings, LLM_BASE_URL="http://example.invalid/v1"))
        agent_mod.llm_reachable()
        assert captured["addr"] == ("example.invalid", 80)


class TestSystemPromptLoading:
    def test_only_the_runtime_marked_section_is_sent_to_the_llm(self):
        """Prompt.md also holds repo documentation and a generated Tool Reference table.
        Sending the whole file would double-count tool schemas -- build_tools() already
        sends them dynamically -- against the token budget on every single request."""
        prompt = agent_mod._load_system_prompt()
        assert prompt
        assert "BEGIN RUNTIME PROMPT" not in prompt
        assert "END RUNTIME PROMPT" not in prompt

    def test_a_prompt_file_missing_its_markers_fails_loudly(self, tmp_path, monkeypatch):
        """Silently falling back to the whole file, or to an empty prompt, would degrade
        every response with no visible error."""
        broken = tmp_path / "Prompt.md"
        broken.write_text("no markers anywhere in this file", encoding="utf-8")
        monkeypatch.setattr(agent_mod, "_PROMPT_PATH", broken)

        with pytest.raises(ValueError, match="RUNTIME PROMPT"):
            agent_mod._load_system_prompt()


class TestCallTool:
    def test_an_unregistered_tool_raises_tool_not_available(self):
        with pytest.raises(agent_mod.ToolNotAvailableError, match="not registered"):
            agent_mod.call_tool("no_such_tool_exists_anywhere")

    def test_the_registry_is_unfiltered_and_cached(self):
        """call_tool() must reach every registered tool regardless of the agent's domain
        scope, and must not rebuild the map on every call."""
        from modes.trial_balance.pipeline.tools import TOOL_REGISTRY

        first = agent_mod.get_tool_registry()
        assert set(first) == set(TOOL_REGISTRY)
        assert agent_mod.get_tool_registry() is first

    def test_a_registered_tool_is_invoked_with_its_keyword_arguments(self, tmp_path):
        result = agent_mod.call_tool(
            "build_entity_profile", canonical_tb_file=str(tmp_path / "absent.parquet")
        )
        # Reached the real tool (which then failed on its missing input) rather than
        # raising ToolNotAvailableError.
        assert result["execution_status"] == "FAILED"


class TestAgentConstruction:
    def test_config_pins_the_agent_identity_and_iteration_ceiling(self):
        """max_iter bounds a runaway tool-calling loop; system_name is what traces are
        grouped under in Phoenix."""
        config = agent_mod._build_config()
        assert config.system_name == "tb-v2-agent"
        assert config.max_iter == 120

    def test_memory_adopts_the_models_real_context_window(self):
        """yukta's default is a hardcoded 8192; the deployed model reports 65536.
        Without this correction the agent trims history 8x earlier than it needs to."""

        class FakeLLM:
            def get_context_window(self):
                return 65536

        memory = agent_mod._build_memory(FakeLLM())
        assert memory.chat.context_window == 65536
        assert memory.chat.max_input_tokens == 65536 - memory.chat.context_buffer

    def test_a_failing_context_window_probe_degrades_to_the_default(self):
        """An unreachable or unauthenticated endpoint must not prevent the agent being
        built -- it just keeps yukta's conservative default."""

        class ExplodingLLM:
            def get_context_window(self):
                raise RuntimeError("401 Unauthorized")

        assert agent_mod._build_memory(ExplodingLLM()) is not None

    def test_a_client_without_the_probe_is_accepted_as_is(self):
        class MinimalLLM:
            pass

        assert agent_mod._build_memory(MinimalLLM()) is not None

    def test_build_llm_is_wired_to_the_configured_endpoint(self, monkeypatch):
        monkeypatch.setattr(
            agent_mod, "settings",
            replace(settings, LLM_BASE_URL="http://llm.invalid:1234/v1", LLM_API_KEY="sk-unit-test"),
        )
        llm = agent_mod._build_llm()
        assert llm._session.headers.get("Authorization") == "Bearer sk-unit-test"

    def test_get_agent_caches_a_single_instance(self, monkeypatch):
        monkeypatch.setattr(agent_mod, "_agent", None)
        built = []

        def fake_build():
            built.append(1)
            return "AGENT"

        monkeypatch.setattr(agent_mod, "build_tb_agent", fake_build)

        assert agent_mod.get_agent() == "AGENT"
        assert agent_mod.get_agent() == "AGENT"
        assert len(built) == 1  # built once, reused thereafter


class _FakeResponse:
    def __init__(self, content, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []

    def has_tool_calls(self):
        return bool(self.tool_calls)


class TestSafeVllmClientGenerate:
    """generate() caps max_tokens and recovers tool calls that a mis-configured sglang
    emitted as literal text instead of structured tool_calls. Both apply on every LLM
    round-trip."""

    def _client(self):
        return agent_mod.SafeVLLMClient(model_name="m", base_url="http://127.0.0.1:9", api_key=None)

    def _patch_super_generate(self, monkeypatch, response):
        base = agent_mod.SafeVLLMClient.__bases__[0]

        def fake_generate(self, messages, tools=None, **kwargs):
            fake_generate.seen = kwargs
            return response

        monkeypatch.setattr(base, "generate", fake_generate, raising=False)
        return fake_generate

    def test_max_tokens_is_capped_even_when_a_caller_asks_for_more(self, monkeypatch):
        fake = self._patch_super_generate(monkeypatch, _FakeResponse("ok"))
        self._client().generate([], max_tokens=100000)
        assert fake.seen["max_tokens"] == 4096

    def test_max_tokens_is_defaulted_when_a_caller_supplies_none(self, monkeypatch):
        fake = self._patch_super_generate(monkeypatch, _FakeResponse("ok"))
        self._client().generate([])
        assert fake.seen["max_tokens"] == 4096

    def test_raw_tool_call_text_is_parsed_into_structured_calls(self, monkeypatch):
        # The shape an sglang deployment launched without a matching --tool-call-parser
        # emits: the model's native function-call tokens as literal text.
        raw = (
            "Sure, checking that now."
            '<|tool_call>:list_db_documents{entity_id:<|"|>APCPL<|"|>}<tool_call|>'
        )
        self._patch_super_generate(monkeypatch, _FakeResponse(raw))
        response = self._client().generate([])

        assert response.tool_calls, "raw tool-call text was not recovered"
        assert "<|tool_call>" not in response.content
        assert "Sure, checking that now." in response.content

    def test_a_clean_structured_response_is_left_untouched(self, monkeypatch):
        original = _FakeResponse("plain answer", tool_calls=["already-structured"])
        self._patch_super_generate(monkeypatch, original)
        response = self._client().generate([])

        assert response.tool_calls == ["already-structured"]
        assert response.content == "plain answer"

    def test_plain_prose_without_tool_call_markers_is_left_untouched(self, monkeypatch):
        self._patch_super_generate(monkeypatch, _FakeResponse("just an answer, no tools"))
        response = self._client().generate([])

        assert response.tool_calls == []
        assert response.content == "just an answer, no tools"
