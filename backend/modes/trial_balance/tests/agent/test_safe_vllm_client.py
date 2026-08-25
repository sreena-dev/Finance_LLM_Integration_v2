"""Tests for backend/agent/agent.py's SafeVLLMClient auth-header fix.

yukta's VLLMClient.get_model_info() calls self._session.get(...) directly,
bypassing _make_request()'s per-call Authorization header entirely. Against
an authenticated endpoint (confirmed live: gemma-4-26b-a4b-it via sglang)
this 401s, and get_context_window() silently falls back to its hardcoded
8192-token default -- understating this model's real 65536-token window by
8x, which makes the agent trim conversation history far earlier than
necessary on exactly the model picked for its larger context window."""

from modes.trial_balance.pipeline.agent import SafeVLLMClient


def test_api_key_set_as_session_default_header():
    client = SafeVLLMClient(model_name="test-model", base_url="http://localhost:9999", api_key="sk-test-123")
    assert client._session.headers.get("Authorization") == "Bearer sk-test-123"


def test_no_api_key_leaves_no_authorization_header():
    client = SafeVLLMClient(model_name="test-model", base_url="http://localhost:9999")
    assert "Authorization" not in client._session.headers


def test_empty_string_api_key_leaves_no_authorization_header():
    """Matches config.py's LLM_API_KEY default of "not-needed" being a real value
    (falsy check is on the string being empty, not on a specific placeholder) --
    an empty string is the only case that should be treated as "no key configured"."""
    client = SafeVLLMClient(model_name="test-model", base_url="http://localhost:9999", api_key="")
    assert "Authorization" not in client._session.headers
