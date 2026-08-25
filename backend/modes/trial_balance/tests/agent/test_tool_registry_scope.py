"""Guards on the two tool registries, which are deliberately different sizes.

`build_tools()` is what the LLM agent may choose between; `get_tool_registry()` is
what `call_tool()` resolves against. Scoping the first was what brought the assembled
prompt back under budget -- the analytics chain is sequenced deterministically, so
advertising it to the agent spent ~2,400 schema tokens per request on capabilities the
agent never picks.

Two ways that scoping goes wrong, both silent:

  1. A tool routes.py calls deterministically stops resolving. The route returns a
     424 and a whole audit area vanishes from the report.
  2. A tool the AGENT needs is scoped out. Nothing errors -- the agent simply never
     calls it. That is how the upload path breaks: it has no deterministic chain, so
     the agent itself runs process_input_documents -> extract_grouping_mapping ->
     build_canonical_tb -> persist_canonical_tb_to_live, and dropping any of those
     domains leaves the route returning 200 with no canonical TB ever produced.

Both are asserted below, because both were live risks while this was being written.
"""

import re
from pathlib import Path

import pytest

from modes.trial_balance.pipeline.agent import agent_tool_domains, build_tools, get_tool_registry
from modes.trial_balance.pipeline.config import settings

_ROUTES = Path("modes/trial_balance/router.py")


def _tool_names(tools):
    return {getattr(t, "name", str(t)) for t in tools}


def _call_tool_names_in_routes() -> set:
    """Every literal tool name routes.py invokes deterministically."""
    src = _ROUTES.read_text(encoding="utf-8")
    return set(re.findall(r'call_tool\(\s*["\']([a-z_]+)["\']', src))


class TestDeterministicRegistryIsComplete:
    def test_every_call_tool_name_in_routes_resolves(self):
        """The registry call_tool() reads is unfiltered by design. If scoping ever
        leaked into it, these routes would start returning 424."""
        registry = get_tool_registry()
        called = _call_tool_names_in_routes()
        assert called, "no call_tool() names found -- the scraper needs updating"
        missing = sorted(called - set(registry))
        assert not missing, (
            f"routes.py calls {missing} deterministically but they are not in "
            "get_tool_registry(). call_tool() would raise ToolNotAvailableError."
        )

    def test_registry_is_larger_than_the_agent_view(self):
        """The whole point of the split. If these are equal, scoping is not applied
        and the prompt is back over budget."""
        assert len(get_tool_registry()) > len(build_tools())

    def test_registry_holds_every_analytics_tool(self):
        registry = set(get_tool_registry())
        for tool in ("build_counterpart_screen", "build_finding_records",
                     "build_materiality", "build_excel_report", "build_docx_report",
                     "build_run_log", "build_comparison_report"):
            assert tool in registry, f"{tool} missing from the deterministic registry"


class TestAgentScopeCoversWhatTheAgentMustDo:
    def test_upload_chain_is_visible_to_the_agent(self):
        """The upload path has no deterministic chain. Every one of these must be
        agent-selectable or uploads silently produce nothing."""
        names = _tool_names(build_tools())
        for tool in ("process_input_documents", "extract_grouping_mapping",
                     "build_canonical_tb", "persist_canonical_tb_to_live"):
            assert tool in names, (
                f"{tool} is not agent-selectable, but the upload path depends on the "
                "agent calling it. Add its domain to settings.AGENT_TOOL_DOMAINS."
            )

    def test_chat_tools_are_visible_to_the_agent(self):
        """/ask and /ask-general are agent-routed; without these they cannot answer."""
        names = _tool_names(build_tools())
        chat = {n for n in names if n.startswith("chat_")}
        assert len(chat) >= 9, f"only {len(chat)} chat tool(s) visible: {sorted(chat)}"

    def test_db_lookup_tools_are_visible(self):
        names = _tool_names(build_tools())
        assert {"list_db_documents", "load_tb_from_db"} <= names


class TestPersistenceIsReachable:
    def test_persist_is_agent_selectable_and_instructed(self):
        """persist_canonical_tb_to_live is called by NOTHING deterministically -- it
        runs only because the system prompt instructs the agent to. Both halves of
        that have to hold, and the Phase-5 prompt trim briefly removed the second."""
        from modes.trial_balance.pipeline.agent import _load_system_prompt

        assert "persist_canonical_tb_to_live" in _tool_names(build_tools())
        assert "persist_canonical_tb_to_live" in _load_system_prompt(), (
            "The prompt no longer instructs the agent to persist to LIVE staging, and "
            "nothing calls it deterministically -- uploads would never reach LIVE."
        )
        assert "persist_canonical_tb_to_live" not in _call_tool_names_in_routes(), (
            "persist is now called deterministically -- update this test and the "
            "prompt instruction, which is then redundant."
        )


class TestPromptBudget:
    def test_assembled_prompt_is_within_budget(self):
        """Phase 5's acceptance criterion. Trimming the prompt alone could not reach
        it: schemas at 62 tools were 2,439 against a 2,048 budget."""
        from modes.trial_balance.pipeline.agent import _assembled_prompt_text, estimate_token_count

        total = estimate_token_count(_assembled_prompt_text(build_tools()))
        assert total <= settings.SYSTEM_PROMPT_MAX_TOKENS, (
            f"assembled prompt is {total} tokens against a budget of "
            f"{settings.SYSTEM_PROMPT_MAX_TOKENS}"
        )

    @pytest.mark.parametrize("value,expected", [
        ("*", set()),                       # documented escape hatch to pre-Phase-5
        ("chat", {"chat"}),
        ("chat, db_bridge ", {"chat", "db_bridge"}),   # whitespace tolerated
        ("", set()),                        # empty behaves as no filtering
    ])
    def test_scope_is_configurable_without_a_code_change(self, monkeypatch, value, expected):
        """Settings is a frozen dataclass, so the module-level object is swapped
        rather than mutated -- the same way a deployment would override it via .env."""
        import dataclasses

        import modes.trial_balance.pipeline.agent as agent_mod

        monkeypatch.setattr(agent_mod, "settings",
                            dataclasses.replace(settings, AGENT_TOOL_DOMAINS=value))
        assert agent_tool_domains() == expected


class TestSafeWordingSurvivedTheTrim:
    @pytest.mark.parametrize("rule", [
        "risk indicator",
        "opinion",
        "fabricate",
        "freehand-compute",
    ])
    def test_safety_floor_intact(self, rule):
        """The prompt lost a third of its length in Phase 5. The safety floor was not
        the part to lose."""
        from modes.trial_balance.pipeline.agent import _load_system_prompt

        assert rule.lower() in _load_system_prompt().lower(), f"safety rule missing: {rule}"
