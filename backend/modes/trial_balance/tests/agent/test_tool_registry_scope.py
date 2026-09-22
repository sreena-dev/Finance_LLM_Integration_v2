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
     calls it.

The upload path used to be case 2's canonical example (no deterministic chain; the
agent had to run process_input_documents -> extract_grouping_mapping ->
build_canonical_tb -> persist_canonical_tb_to_live itself, or nothing was ever
produced). It has since become case 1 instead: routes.py's /upload-mapped now calls
ingest_tb_to_live deterministically, the same single entry point live-template
submissions use -- see TestDeterministicRegistryIsComplete for that guard.
"""

import re
from pathlib import Path

import pytest

pytest.importorskip(
    "yukta",
    reason="yukta is installed from a local path and published to no index, so it is "
           "absent on a clean checkout -- see requirements.txt. These tests import "
           "backend.agent, which needs it.",
)

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
    def test_ingest_tb_to_live_is_deterministically_reachable_not_agent_dependent(self):
        """The upload path's ingestion step is no longer something the agent must
        remember to call -- routes.py's /upload-mapped calls ingest_tb_to_live
        deterministically (asserted by test_every_call_tool_name_in_routes_resolves),
        the same way live-template submissions do. Scoping it out of the agent's own
        menu (settings.AGENT_TOOL_DOMAINS) must not disable that route."""
        assert "ingest_tb_to_live" in _call_tool_names_in_routes()
        assert "ingest_tb_to_live" in get_tool_registry()

    def test_chat_tools_are_visible_to_the_agent(self):
        """/ask and /ask-general are agent-routed; without these they cannot answer."""
        names = _tool_names(build_tools())
        chat = {n for n in names if n.startswith("chat_")}
        assert len(chat) >= 9, f"only {len(chat)} chat tool(s) visible: {sorted(chat)}"

    def test_db_lookup_tools_are_visible(self):
        names = _tool_names(build_tools())
        assert {"list_db_documents", "load_tb_from_db"} <= names


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
