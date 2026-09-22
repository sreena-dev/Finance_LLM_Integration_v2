"""Tests for backend/agent/agent.py's _parse_raw_tool_call_text fallback parser.

A real /audit run against a gemma-4-26b-a4b-it endpoint served via sglang showed
the server emitting a tool call as literal text using the model's native
function-call special tokens instead of a structured OpenAI-style tool_calls
field -- the agent loop only acts on response.has_tool_calls(), so it silently
treated the garbled text as a final answer and never called any tool. Root
cause is server-side (sglang needs a --tool-call-parser flag matching this
model's chat template); this fallback is a client-side safety net.
"""

import json

import pytest

pytest.importorskip(
    "yukta",
    reason="yukta is installed from a local path and published to no index, so it is "
           "absent on a clean checkout -- see requirements.txt. These tests import "
           "backend.agent, which needs it.",
)

from modes.trial_balance.pipeline.agent import _parse_raw_tool_call_text


def test_parses_the_real_captured_response():
    """Exact content string captured from a live sglang response."""
    content = (
        '<|tool_call>:load_tb_from_db{output_dir:<|"|>'
        "/home/madhan/fin_llm/TB-v2/sessions/dd57d9aa-3d5b-4e6f-8831-c1a2bd60bb73"
        '<|"|>,tb_doc_id:<|"|>OVRL_2025_2026<|"|>}<tool_call|>'
    )
    calls, remaining = _parse_raw_tool_call_text(content)

    assert len(calls) == 1
    assert calls[0]["type"] == "function"
    assert calls[0]["function"]["name"] == "load_tb_from_db"
    args = json.loads(calls[0]["function"]["arguments"])
    assert args == {
        "output_dir": "/home/madhan/fin_llm/TB-v2/sessions/dd57d9aa-3d5b-4e6f-8831-c1a2bd60bb73",
        "tb_doc_id": "OVRL_2025_2026",
    }
    assert remaining == ""


def test_no_match_on_clean_text_returns_empty():
    calls, remaining = _parse_raw_tool_call_text("This is a normal final answer with no tool call.")
    assert calls == []
    assert remaining == "This is a normal final answer with no tool call."


def test_parses_multiple_tool_calls_in_one_response():
    content = (
        '<|tool_call>:build_materiality{output_dir:<|"|>/tmp/x<|"|>}<tool_call|>'
        '<|tool_call>:build_variance_analysis{output_dir:<|"|>/tmp/x<|"|>}<tool_call|>'
    )
    calls, remaining = _parse_raw_tool_call_text(content)
    assert [c["function"]["name"] for c in calls] == ["build_materiality", "build_variance_analysis"]
    assert remaining == ""


def test_coerces_unquoted_scalar_arg_types():
    content = '<|tool_call>:build_audit_reasoning{max_observations:5,use_llm:true,note:null}<tool_call|>'
    calls, _ = _parse_raw_tool_call_text(content)
    args = json.loads(calls[0]["function"]["arguments"])
    assert args == {"max_observations": 5, "use_llm": True, "note": None}


def test_preserves_surrounding_commentary_text():
    content = (
        'Sure, I will load the trial balance now. '
        '<|tool_call>:load_tb_from_db{tb_doc_id:<|"|>OVRL_2025_2026<|"|>}<tool_call|>'
    )
    calls, remaining = _parse_raw_tool_call_text(content)
    assert len(calls) == 1
    assert remaining == "Sure, I will load the trial balance now."


def test_empty_args_block_parses_to_empty_dict():
    calls, _ = _parse_raw_tool_call_text("<|tool_call>:build_data_sufficiency_grade{}<tool_call|>")
    assert json.loads(calls[0]["function"]["arguments"]) == {}
