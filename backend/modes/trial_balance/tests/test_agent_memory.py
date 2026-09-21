"""backend/agent_memory.py: per-session isolation for the agent's conversation
memory (fixes get_agent()'s previously-shared, cross-session memory object --
every /ask and /ask-general call used to append to the SAME yukta Chat
regardless of session_id). Exercises _session_memory directly against the
autouse fake_valkey fixture; a full invoke_scoped() test would need a real or
mocked yukta Agent/LLM client, which is exercised at the routes.py level
instead (tests/api/test_routes_endpoints.py already mocks get_agent())."""

from modes.trial_balance.pipeline.agent_memory import _session_memory


def test_fresh_session_has_no_prior_messages():
    memory = _session_memory("sess-fresh", "system prompt text")
    assert len(memory.chat.messages) == 0


def test_prior_turns_rehydrate_for_the_same_session_id():
    memory = _session_memory("sess-continue", "system prompt text")
    memory.chat.add_user_message("What is the current ratio?")
    memory.chat.add_agent_message("It is 1.8.")
    memory.chat_manager.save_chat("sess-continue")

    rehydrated = _session_memory("sess-continue", "system prompt text")
    assert len(rehydrated.chat.messages) == 2
    assert [m.role for m in rehydrated.chat.messages] == ["user", "agent"]


def test_different_sessions_never_bleed_into_each_other():
    """The actual bug this module fixes: get_agent()'s single shared Memory
    meant every session saw every other session's turns."""
    memory_a = _session_memory("sess-A", "system prompt text")
    memory_a.chat.add_user_message("Question from tenant A")
    memory_a.chat.add_agent_message("Answer for tenant A")
    memory_a.chat_manager.save_chat("sess-A")

    memory_b = _session_memory("sess-B", "system prompt text")
    assert len(memory_b.chat.messages) == 0

    rehydrated_a = _session_memory("sess-A", "system prompt text")
    assert len(rehydrated_a.chat.messages) == 2


def test_corrupt_or_missing_prior_state_falls_back_to_fresh_chat(monkeypatch):
    """A load failure (bad JSON, unexpected shape) must degrade to a fresh
    empty chat for this call, never raise and never block the request."""
    from modes.trial_balance.pipeline.agent_memory import _valkey_chat_backend

    memory = _session_memory("sess-broken", "system prompt text")
    memory.chat_manager.chats["sess-broken"] = memory.chat  # register so load_chat can find storage

    def _broken_load(session_id):
        raise ValueError("corrupt payload")

    monkeypatch.setattr(_valkey_chat_backend, "load", _broken_load)

    recovered = _session_memory("sess-broken", "system prompt text")
    assert len(recovered.chat.messages) == 0
