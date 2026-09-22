"""Tests for backend/agent.py's tool-schema derivation and prompt-budget accounting.

Why this file exists: `agent.py` sat at 64% coverage. The uncovered half was the wiring
that decides what the LLM is actually shown -- how each tool's parameters are derived
from its Python signature, how its description is truncated, and how the assembled
prompt is measured against the token budget.

None of that fails loudly when it goes wrong. A parameter marked optional when it is
really required produces an agent call missing an argument (exactly the class of defect
that left `/upload-mapped` broken); a multi-paragraph docstring leaking into a tool
description quietly inflates every single request; a mis-measured budget silently
disables the warning that would have caught either.

These tests need no LLM endpoint -- they exercise the schema-building path only.
"""

import inspect

import pytest

pytest.importorskip(
    "yukta",
    reason="yukta is installed from a local path and published to no index, so it is "
           "absent on a clean checkout -- see requirements.txt. These tests import "
           "backend.agent, which needs it.",
)

from modes.trial_balance.pipeline.agent import (
    _assembled_prompt_text,
    _params_from_signature,
    _tool_param_type,
    agent_tool_domains,
    build_tools,
    check_prompt_budget,
    estimate_token_count,
)
from modes.trial_balance.pipeline.config import settings

# ── Parameter type mapping ──────────────────────────────────────────────────


class TestToolParamType:
    def test_unannotated_parameters_default_to_string(self):
        assert _tool_param_type(inspect.Parameter.empty) == "string"

    def test_known_python_types_are_mapped(self):
        assert _tool_param_type(str) == "string"
        assert _tool_param_type(int) == "integer"
        assert _tool_param_type(bool) == "boolean"

    def test_an_unmapped_annotation_degrades_to_string_rather_than_raising(self):
        """An exotic annotation on a tool must not break schema generation for every
        other tool -- the agent losing one parameter's type is recoverable, the whole
        registry failing to build is not."""
        class SomethingExotic:
            pass

        assert _tool_param_type(SomethingExotic) == "string"


# ── Signature -> ToolParameter derivation ───────────────────────────────────


class TestParamsFromSignature:
    def test_a_parameter_without_a_default_is_required(self):
        def f(needed: str):
            pass

        params = _params_from_signature(f)
        assert len(params) == 1
        assert params[0].name == "needed"
        assert params[0].required is True

    def test_a_parameter_with_a_default_is_optional_and_carries_it(self):
        def f(optional: str = "fallback"):
            pass

        param = _params_from_signature(f)[0]
        assert param.required is False
        assert param.default == "fallback"

    def test_required_and_optional_are_distinguished_within_one_signature(self):
        """This is the property that matters: an agent told a required argument is
        optional will omit it, which is precisely how process_input_documents ended up
        being called without grouping_excel_path."""
        def f(a: str, b: str = None, c: int = 3):
            pass

        by_name = {p.name: p for p in _params_from_signature(f)}
        assert by_name["a"].required is True
        assert by_name["b"].required is False
        assert by_name["c"].required is False

    def test_var_args_and_kwargs_are_excluded(self):
        """*args/**kwargs cannot be expressed in a tool schema; advertising them would
        invite the agent to pass arguments the function silently discards."""
        def f(real: str, *args, **kwargs):
            pass

        assert [p.name for p in _params_from_signature(f)] == ["real"]

    def test_a_no_argument_tool_yields_no_parameters(self):
        def f():
            pass

        assert _params_from_signature(f) == []

    def test_every_parameter_gets_a_non_empty_description(self):
        def f(a: str, b: int = 1):
            pass

        assert all(p.description for p in _params_from_signature(f))


# ── Domain scoping ──────────────────────────────────────────────────────────


class TestAgentToolDomains:
    def test_a_comma_separated_list_becomes_a_set(self, monkeypatch):
        from dataclasses import replace

        import modes.trial_balance.pipeline.agent as agent_mod

        monkeypatch.setattr(
            agent_mod, "settings", replace(settings, AGENT_TOOL_DOMAINS="chat,input,grouping")
        )
        assert agent_tool_domains() == {"chat", "input", "grouping"}

    def test_surrounding_whitespace_and_empty_entries_are_ignored(self, monkeypatch):
        from dataclasses import replace

        import modes.trial_balance.pipeline.agent as agent_mod

        monkeypatch.setattr(
            agent_mod, "settings", replace(settings, AGENT_TOOL_DOMAINS=" chat , , input ,")
        )
        assert agent_tool_domains() == {"chat", "input"}

    def test_a_star_means_no_filtering_at_all(self, monkeypatch):
        """"*" returns an empty set, which build_tools() reads as "do not filter" --
        the opposite of "allow nothing". Pinned because that inversion is easy to
        break and would silently unregister every tool."""
        from dataclasses import replace

        import modes.trial_balance.pipeline.agent as agent_mod

        monkeypatch.setattr(agent_mod, "settings", replace(settings, AGENT_TOOL_DOMAINS="*"))
        assert agent_tool_domains() == set()

    def test_a_star_scope_advertises_every_registered_tool(self, monkeypatch):
        from dataclasses import replace

        import modes.trial_balance.pipeline.agent as agent_mod
        from modes.trial_balance.pipeline.tools import TOOL_REGISTRY

        monkeypatch.setattr(agent_mod, "settings", replace(settings, AGENT_TOOL_DOMAINS="*"))
        assert len(build_tools()) == len(TOOL_REGISTRY)


class TestBuildTools:
    def test_only_tools_in_scope_are_advertised(self, monkeypatch):
        from dataclasses import replace

        import modes.trial_balance.pipeline.agent as agent_mod
        from modes.trial_balance.pipeline.tools import TOOL_REGISTRY

        monkeypatch.setattr(agent_mod, "settings", replace(settings, AGENT_TOOL_DOMAINS="input"))
        tools = build_tools()

        expected = {n for n, e in TOOL_REGISTRY.items() if e.domain == "input"}
        assert {t.name for t in tools} == expected
        assert expected  # the fixture domain must not be empty, or this proves nothing

    def test_out_of_scope_tools_remain_directly_callable(self, monkeypatch):
        """Scoping controls what the *agent* may pick, not what exists. routes.py calls
        the analytics chain deterministically via call_tool(), which resolves against
        the unfiltered registry -- narrowing the agent's menu must never disable that."""
        from dataclasses import replace

        import modes.trial_balance.pipeline.agent as agent_mod
        from modes.trial_balance.pipeline.agent import get_tool_registry

        monkeypatch.setattr(agent_mod, "settings", replace(settings, AGENT_TOOL_DOMAINS="input"))
        advertised = {t.name for t in build_tools()}

        assert "build_variance_analysis" not in advertised
        assert "build_variance_analysis" in get_tool_registry()

    def test_tool_descriptions_are_truncated_to_a_single_line(self, monkeypatch):
        """yukta serialises the full description into the schema on every request, so a
        multi-paragraph docstring is a permanent per-call token cost. Several tools in
        this codebase have long docstrings, which is exactly why this truncation
        exists."""
        from dataclasses import replace

        import modes.trial_balance.pipeline.agent as agent_mod

        monkeypatch.setattr(agent_mod, "settings", replace(settings, AGENT_TOOL_DOMAINS="*"))
        for tool in build_tools():
            assert "\n" not in tool.description
            assert tool.description.strip() == tool.description

    def test_every_advertised_tool_has_a_description(self, monkeypatch):
        from dataclasses import replace

        import modes.trial_balance.pipeline.agent as agent_mod

        monkeypatch.setattr(agent_mod, "settings", replace(settings, AGENT_TOOL_DOMAINS="*"))
        assert all(t.description for t in build_tools())

    def test_advertised_parameters_match_the_underlying_function_signature(self, monkeypatch):
        """The schema the LLM sees must describe the function that will actually run.
        A drift here is invisible until the agent calls a tool with wrong arguments."""
        from dataclasses import replace

        import modes.trial_balance.pipeline.agent as agent_mod
        from modes.trial_balance.pipeline.tools import TOOL_REGISTRY

        monkeypatch.setattr(agent_mod, "settings", replace(settings, AGENT_TOOL_DOMAINS="*"))
        for tool in build_tools():
            func = TOOL_REGISTRY[tool.name].func
            real = {
                n for n, p in inspect.signature(func).parameters.items()
                if n not in ("self", "args", "kwargs")
                and p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
            }
            assert {p.name for p in tool.parameters} == real, f"schema drift on {tool.name}"

    def test_required_parameters_are_advertised_as_required(self, monkeypatch):
        """The concrete regression this guards: a tool's required parameter must reach
        the agent marked required, not optional -- pinned here against
        ingest_tb_to_live's tb_grouping_template_path (required) vs grouping_file_path/
        output_dir (both optional)."""
        from dataclasses import replace

        import modes.trial_balance.pipeline.agent as agent_mod

        monkeypatch.setattr(agent_mod, "settings", replace(settings, AGENT_TOOL_DOMAINS="*"))
        tool = next(t for t in build_tools() if t.name == "ingest_tb_to_live")
        by_name = {p.name: p for p in tool.parameters}

        assert by_name["tb_grouping_template_path"].required is True
        assert by_name["grouping_file_path"].required is False
        assert by_name["output_dir"].required is False


# ── Prompt budget ───────────────────────────────────────────────────────────


class TestEstimateTokenCount:
    def test_empty_text_is_zero(self):
        assert estimate_token_count("") == 0

    def test_count_scales_with_word_count(self):
        assert estimate_token_count("one two three four") == int(4 * 1.3)

    def test_longer_text_never_estimates_lower(self):
        short = estimate_token_count("a b c")
        long = estimate_token_count("a b c d e f g h")
        assert long > short


class TestAssembledPromptText:
    def test_includes_each_tool_name_description_and_parameters(self, monkeypatch):
        from dataclasses import replace

        import modes.trial_balance.pipeline.agent as agent_mod

        monkeypatch.setattr(agent_mod, "settings", replace(settings, AGENT_TOOL_DOMAINS="grouping"))
        tools = build_tools()
        text = _assembled_prompt_text(tools)

        for tool in tools:
            assert tool.name in text
            assert tool.description in text
            for p in tool.parameters:
                assert p.name in text

    def test_no_tools_still_yields_the_system_prompt(self):
        """The budget must account for the base system prompt even before any tool
        schema is added -- otherwise a large prompt with few tools reads as free."""
        assert len(_assembled_prompt_text([])) > 0


class TestCheckPromptBudget:
    def test_returns_the_estimate_and_logs_at_info_when_within_budget(self, monkeypatch, caplog):
        from dataclasses import replace

        import modes.trial_balance.pipeline.agent as agent_mod

        monkeypatch.setattr(
            agent_mod, "settings", replace(settings, AGENT_TOOL_DOMAINS="grouping",
                                           SYSTEM_PROMPT_MAX_TOKENS=10_000_000)
        )
        with caplog.at_level("INFO", logger="modes.trial_balance.pipeline.agent"):
            estimated = check_prompt_budget(build_tools())

        assert estimated > 0
        assert "tokens" in caplog.text
        assert "exceeding" not in caplog.text

    def test_warns_when_the_budget_is_exceeded(self, monkeypatch, caplog):
        """The warning is the only signal that the tool menu has outgrown the context
        window; silently exceeding it degrades every request."""
        from dataclasses import replace

        import modes.trial_balance.pipeline.agent as agent_mod

        monkeypatch.setattr(
            agent_mod, "settings", replace(settings, AGENT_TOOL_DOMAINS="*",
                                           SYSTEM_PROMPT_MAX_TOKENS=1)
        )
        with caplog.at_level("WARNING", logger="modes.trial_balance.pipeline.agent"):
            estimated = check_prompt_budget(build_tools())

        assert estimated > 1
        assert "exceeding" in caplog.text
        assert "SYSTEM_PROMPT_MAX_TOKENS=1" in caplog.text
