"""Per-session isolation for modes.trial_balance.pipeline.agent.get_agent()'s conversation memory.

get_agent() (backend/agent.py) is a process-wide singleton built once with a
single yukta Memory/Chat object (`agent.set_memory(memory)` at build time).
Every /ask and /ask-general call -- regardless of session_id -- invoked that
same agent instance and therefore shared and appended to the exact same chat
history: one user's prior questions and answers could appear in the context
another user's question is answered against, bounded only by the memory's
own max_tokens truncation. request.session_id was accepted by both routes but
never actually used to scope anything.

This module fixes that without changing get_agent()'s singleton-agent shape
(the LLM client, tools, system prompt are legitimately process-wide): before
each call, swap the agent's ACTIVE memory for one scoped to that request's
session_id, backed by Valkey (ValkeyChatStorageBackend) so it survives process
restarts and is shared correctly across multiple worker processes -- then
persist the updated turns back to Valkey after the call.

If session_id is omitted (a client that never adopted it, or /ask-general's
genuinely stateless "no TB attached" case), each call gets a fresh, unshared,
never-persisted memory -- never the old shared singleton behaviour.
"""

import logging
import time
import uuid
from typing import Optional

from yukta import Memory
from yukta.config.memory_config import MemoryConfig

from modes.trial_balance.pipeline.valkey_client import ValkeyChatStorageBackend

logger = logging.getLogger(__name__)

_CHAT_MEMORY_MAX_TOKENS = 15000  # matches backend/agent.py's _build_memory default

_valkey_chat_backend = ValkeyChatStorageBackend()


def _session_memory(session_id: str, system_prompt: str, durable_history: Optional[list] = None) -> Memory:
    """A Memory scoped to `session_id`: rehydrated from Valkey if a prior turn
    exists for this session, otherwise a fresh empty chat under that same id.

    `durable_history` ([{"role": "user"|"assistant", "content": str}, ...],
    oldest first -- see pipeline/db.py's history_for_agent()) is the fallback
    when Valkey has NOTHING for this session: either genuinely new, or --
    the case this exists for -- Valkey's 2-hour TTL already expired on a
    conversation that Postgres still has a durable record of (up to the full
    90-day retention window). Only used when Valkey's own load comes back
    empty, so a live, still-cached session is never overridden by a
    possibly-stale Postgres snapshot."""
    config = MemoryConfig(max_tokens=_CHAT_MEMORY_MAX_TOKENS, storage_backend=_valkey_chat_backend)
    memory = Memory(system_prompt=system_prompt, session_id=session_id, config=config)

    try:
        prior = memory.chat_manager.load_chat(session_id)
    except FileNotFoundError:
        prior = None
    except Exception as e:
        # Corrupt/unreadable prior state must not break this turn -- fall back to
        # the fresh empty chat Memory() already created above.
        logger.warning("[agent_memory] failed to load prior chat for session %s: %s", session_id, e)
        prior = None

    if prior is not None:
        memory.chat = prior
        memory.chat_manager.chats[session_id] = prior
    elif durable_history:
        try:
            for turn in durable_history:
                if turn.get("role") == "user":
                    memory.chat.add_user_message(turn.get("content", ""))
                else:
                    # yukta's own Chat role name is "agent", not "assistant" --
                    # pipeline_chat_messages stores "assistant" to match the
                    # Postgres CHECK constraint shared with every other role-typed
                    # column convention in this codebase.
                    memory.chat.add_agent_message(turn.get("content", ""))
            memory.chat_manager.chats[session_id] = memory.chat
        except Exception as e:
            # A rehydration failure must degrade to a fresh empty chat for this
            # turn, never break the request -- same posture as the Valkey-load
            # failure path above.
            logger.warning("[agent_memory] failed to rehydrate durable history for session %s: %s", session_id, e)

    return memory


def invoke_scoped(agent, prompt: str, session_id: Optional[str] = None, durable_history: Optional[list] = None) -> str:
    """Run one agent turn with memory scoped to `session_id` instead of the
    agent's process-wide default. Call this from every route that calls
    get_agent().invoke(...) -- never call agent.invoke() directly with the
    agent's own default memory once this module is wired in, or the isolation
    fix is silently bypassed for that call site."""
    effective_session_id = session_id or f"anon-{uuid.uuid4().hex[:12]}"

    system_prompt = agent.get_system_prompt()
    memory = _session_memory(effective_session_id, system_prompt, durable_history=durable_history)
    agent.set_memory(memory)

    # A real per-turn latency number, not a guess: this is the single choke
    # point every /ask and /ask-general call passes through (both routes
    # call THIS function -- see this module's own docstring), and nothing
    # before this logged how long a turn actually took. `max_iter=120`
    # (backend/agent.py) is a much larger ceiling than FS's 8, and there was
    # no data anywhere to say whether a typical TB turn is nowhere near that
    # or routinely climbing toward it. Wall-clock only, not a token/tool-call
    # breakdown -- `agent.invoke()` returns a plain string, and getting
    # finer detail would mean reaching into yukta's own internals, which is
    # real further work, not assumed available here.
    _t0 = time.perf_counter()
    try:
        response = agent.invoke(prompt)
        logger.info(
            "tb agent turn latency: session=%s elapsed=%.2fs prompt_len=%d",
            effective_session_id, time.perf_counter() - _t0, len(prompt),
        )
    finally:
        try:
            memory.chat_manager.save_chat(effective_session_id)
        except Exception as e:
            # Best-effort persistence -- an unsaved turn degrades this session back
            # to stateless next time, it must never fail an already-answered request.
            logger.warning("[agent_memory] failed to persist chat for session %s: %s", effective_session_id, e)

    return response
