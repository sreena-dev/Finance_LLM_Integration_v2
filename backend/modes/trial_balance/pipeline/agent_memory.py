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
import uuid
from typing import Optional

from yukta import Memory
from yukta.config.memory_config import MemoryConfig

from modes.trial_balance.pipeline.valkey_client import ValkeyChatStorageBackend

logger = logging.getLogger(__name__)

_CHAT_MEMORY_MAX_TOKENS = 15000  # matches backend/agent.py's _build_memory default

_valkey_chat_backend = ValkeyChatStorageBackend()


def _session_memory(session_id: str, system_prompt: str) -> Memory:
    """A Memory scoped to `session_id`: rehydrated from Valkey if a prior turn
    exists for this session, otherwise a fresh empty chat under that same id."""
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

    return memory


def invoke_scoped(agent, prompt: str, session_id: Optional[str] = None) -> str:
    """Run one agent turn with memory scoped to `session_id` instead of the
    agent's process-wide default. Call this from every route that calls
    get_agent().invoke(...) -- never call agent.invoke() directly with the
    agent's own default memory once this module is wired in, or the isolation
    fix is silently bypassed for that call site."""
    effective_session_id = session_id or f"anon-{uuid.uuid4().hex[:12]}"

    system_prompt = agent.get_system_prompt()
    memory = _session_memory(effective_session_id, system_prompt)
    agent.set_memory(memory)

    try:
        response = agent.invoke(prompt)
    finally:
        try:
            memory.chat_manager.save_chat(effective_session_id)
        except Exception as e:
            # Best-effort persistence -- an unsaved turn degrades this session back
            # to stateless next time, it must never fail an already-answered request.
            logger.warning("[agent_memory] failed to persist chat for session %s: %s", effective_session_id, e)

    return response
