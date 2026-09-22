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


def test_two_users_with_the_same_raw_session_id_never_share_memory():
    """router.py's _user_scoped_memory_key() is the fix for a narrower, auth-era
    version of this same bug: request.session_id is client-supplied and never
    verified against the caller's identity, so two different logged-in users
    could pick (or guess/reuse) the identical raw session_id string. Without the
    user_id prefix this would collide on the exact key _session_memory uses --
    this pins that a raw collision no longer bleeds across users."""
    from modes.trial_balance.router import _user_scoped_memory_key

    raw_session_id = "shared-guessable-id"
    key_user_a = _user_scoped_memory_key("user-A", raw_session_id)
    key_user_b = _user_scoped_memory_key("user-B", raw_session_id)
    assert key_user_a != key_user_b

    memory_a = _session_memory(key_user_a, "system prompt text")
    memory_a.chat.add_user_message("User A's question")
    memory_a.chat.add_agent_message("User A's answer")
    memory_a.chat_manager.save_chat(key_user_a)

    memory_b = _session_memory(key_user_b, "system prompt text")
    assert len(memory_b.chat.messages) == 0  # user B sees no trace of user A's turn


def test_durable_history_rehydrates_when_valkey_has_nothing():
    """The actual scenario this exists for: Valkey's 2-hour TTL already expired
    on a conversation that pipeline_chat_messages still has a durable record
    of (up to the full 90-day retention window) -- durable_history seeds the
    agent's real working memory, not just a display list."""
    durable_history = [
        {"role": "user", "content": "What is the current ratio?"},
        {"role": "assistant", "content": "It is 1.8."},
    ]
    memory = _session_memory("sess-expired", "system prompt text", durable_history=durable_history)
    assert len(memory.chat.messages) == 2
    # pipeline_chat_messages stores "assistant" (matches its Postgres CHECK
    # constraint); yukta's own Chat role name is "agent" -- confirm the mapping.
    assert [m.role for m in memory.chat.messages] == ["user", "agent"]
    assert memory.chat.messages[0].content == "What is the current ratio?"
    assert memory.chat.messages[1].content == "It is 1.8."


def test_durable_history_is_ignored_when_valkey_already_has_a_live_session():
    """A still-cached Valkey session must never be overridden by a possibly-
    stale Postgres snapshot -- Valkey wins whenever it has something."""
    memory = _session_memory("sess-live", "system prompt text")
    memory.chat.add_user_message("Live question")
    memory.chat.add_agent_message("Live answer")
    memory.chat_manager.save_chat("sess-live")

    stale_durable_history = [{"role": "user", "content": "Stale question"}, {"role": "assistant", "content": "Stale answer"}]
    rehydrated = _session_memory("sess-live", "system prompt text", durable_history=stale_durable_history)
    assert len(rehydrated.chat.messages) == 2
    assert rehydrated.chat.messages[0].content == "Live question"


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
