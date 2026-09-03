"""TB-v2 agent setup — the single master agent module (mirrors TB-v1's agent.py).

Tools are no longer discovered by walking the filesystem: backend/tools.py's
@pipeline_tool(name, domain) decorator populates TOOL_REGISTRY (name -> ToolEntry)
as each tool function is defined, at import time. build_tools() and
get_tool_registry() both read that registry directly.

We also do not port TB-v1's empirical patch wrappers (e.g.
`_wrap_extract_grouping_mapping`) -- those existed to paper over ambiguous
tool schemas from repeated LLM mistakes. TB-v2's tool contract (canonical
schema, artifact-only passing, defensive column access) is meant to make that
category of workaround unnecessary.
"""

import inspect
import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, List

from yukta import AgentConfig, Memory, SystemPrompt, create_agent, create_memory
from yukta.core.Clients import VLLMClient
from yukta.tools import Tool, ToolParameter, ToolProcessor

from modes.trial_balance.pipeline import tracing
from modes.trial_balance.pipeline.config import settings
from modes.trial_balance.pipeline.tools import TOOL_REGISTRY

logger = logging.getLogger(__name__)

# Must run before get_agent() constructs the first LLM client (module-level, at
# import time, is early enough -- the agent itself is built lazily on first
# request). See pipeline/tracing.py for why this can't just be yukta's own
# instrumentation.init_tracing().
tracing.init_tracing()

_PROMPT_PATH = Path(__file__).resolve().parent / "Prompt.md"
_RUNTIME_PROMPT_RE = re.compile(
    r"<!--\s*BEGIN RUNTIME PROMPT\s*-->(.*?)<!--\s*END RUNTIME PROMPT\s*-->",
    re.DOTALL,
)

_PY_TYPE_TO_TOOL_TYPE = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    dict: "dict",
    list: "array",
}


def _load_system_prompt() -> str:
    """Load only the text between the RUNTIME PROMPT markers in Prompt.md -- the
    rest of that file (preamble, the generated Tool Reference table) is repo
    documentation, not sent to the LLM. Tool schemas are sent separately and
    dynamically by build_tools(); duplicating them here would double-count
    against settings.SYSTEM_PROMPT_MAX_TOKENS for no benefit."""
    text = _PROMPT_PATH.read_text()
    match = _RUNTIME_PROMPT_RE.search(text)
    if not match:
        raise ValueError(f"{_PROMPT_PATH} is missing its <!-- BEGIN/END RUNTIME PROMPT --> markers.")
    return match.group(1).strip()


# Some sglang deployments serving gemma models emit a tool call as literal text using the
# model's native function-call special tokens instead of translating them into the
# structured OpenAI-style `tool_calls` field -- this happens when sglang is launched
# without a --tool-call-parser flag matching the model's chat template. Observed shape:
#   <|tool_call>:tool_name{key:<|"|>value<|"|>,other_key:<|"|>value<|"|>}<tool_call|>
# The real fix is server-side (relaunch sglang with the correct --tool-call-parser); this
# is a client-side safety net so the pipeline still works if that hasn't happened yet.
_RAW_TOOL_CALL_RE = re.compile(
    r"<\|tool_call>:(?P<name>[A-Za-z_][A-Za-z0-9_]*)\{(?P<args>.*?)\}<tool_call\|>",
    re.DOTALL,
)
_RAW_TOOL_CALL_ARG_RE = re.compile(
    r'(?P<key>[A-Za-z_][A-Za-z0-9_]*):(?:<\|"\|>(?P<qval>.*?)<\|"\|>|(?P<rawval>[^,}]+))',
    re.DOTALL,
)


def _coerce_raw_arg_value(raw: str):
    """Best-effort scalar coercion for an unquoted arg value (int/float/bool/null),
    since the raw tool-call text has no type information beyond quoted-vs-not."""
    s = raw.strip()
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    if s.lower() in ("null", "none"):
        return None
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d+\.\d+", s):
        return float(s)
    return s


def _parse_raw_tool_call_text(content: str):
    """Detect and convert the raw tool-call text pattern documented above into the
    tool_calls shape yukta.core.Clients.base_client.LLMResponse expects. Returns
    (tool_calls, remaining_content) -- remaining_content has the matched spans
    stripped out so any genuine commentary text around the tool call survives."""
    tool_calls = []
    remaining = content
    for i, match in enumerate(_RAW_TOOL_CALL_RE.finditer(content)):
        args = {}
        for arg_match in _RAW_TOOL_CALL_ARG_RE.finditer(match.group("args")):
            qval = arg_match.group("qval")
            value = qval if qval is not None else _coerce_raw_arg_value(arg_match.group("rawval") or "")
            args[arg_match.group("key")] = value
        tool_calls.append({
            "id": f"fallback-{i}",
            "type": "function",
            "function": {"name": match.group("name"), "arguments": json.dumps(args)},
        })
        remaining = remaining.replace(match.group(0), "", 1)
    return tool_calls, remaining.strip()


class SafeVLLMClient(VLLMClient):
    """VLLMClient wrapper that strictly bounds max_tokens to prevent vLLM context limit
    errors, falls back to parsing raw tool-call special-token text (see
    _parse_raw_tool_call_text) when the server doesn't translate it into a structured
    tool_calls response itself, and fixes get_model_info()'s missing auth header."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # VLLMClient.get_model_info() calls self._session.get(...) directly, bypassing
        # _make_request()'s per-call Authorization header entirely -- against an
        # authenticated endpoint this 401s and get_context_window() silently falls back to
        # its hardcoded 8192-token default, understating a real 65536-token model by 8x and
        # causing the agent to trim conversation history far earlier than it needs to.
        # Setting it as a session-level default header fixes get_model_info() without
        # touching _make_request()'s own (already-correct) per-call header, since `requests`
        # merges call-level headers on top of session-level ones.
        # PATCHED (integration): `_session` is not present on every build of
        # yukta's VLLMClient. The one in use here issues a module-level
        # `requests.get(...)` from get_model_info() and has no session object at
        # all, so this line raised "'SafeVLLMClient' object has no attribute
        # '_session'" from the constructor -- taking down the whole Trial Balance
        # agent before it ran anything.
        #
        # Guarded the same way the Financial Statement mode already guards the
        # identical access. Where there is no session there is also no
        # session-level header to fix, and the only consequence is the one this
        # workaround was written to avoid: get_model_info() may 401 and the
        # context window falls back to its default. A smaller context window is a
        # degradation; a constructor that raises is an outage.
        api_key = self.config.get("api_key")
        session = getattr(self, "_session", None)
        if api_key and session is not None:
            session.headers["Authorization"] = f"Bearer {api_key}"

    def generate(self, messages, tools=None, **kwargs):
        kwargs["max_tokens"] = min(kwargs.get("max_tokens", 4096), 4096)
        response = super().generate(messages, tools=tools, **kwargs)
        if not response.has_tool_calls() and response.content and "<|tool_call>" in response.content:
            parsed_calls, remaining_text = _parse_raw_tool_call_text(response.content)
            if parsed_calls:
                logger.warning(
                    "Server returned raw tool-call text instead of structured tool_calls "
                    "(sglang missing --tool-call-parser?) -- parsed %d call(s) client-side.",
                    len(parsed_calls),
                )
                response.tool_calls = parsed_calls
                response.content = remaining_text
        return response


def _build_llm() -> VLLMClient:
    return SafeVLLMClient(
        model_name=settings.LLM_MODEL,
        base_url=settings.LLM_BASE_URL,
        max_tokens=4096,
        temperature=0.2,
        api_key=settings.LLM_API_KEY,
    )


def _tool_param_type(annotation: Any) -> str:
    if annotation is inspect.Parameter.empty:
        return "string"
    return _PY_TYPE_TO_TOOL_TYPE.get(annotation, "string")


def _params_from_signature(func: Callable) -> List[ToolParameter]:
    """Derive ToolParameter list from a tool function's signature. A parameter
    is required iff it has no default value."""
    params = []
    for name, p in inspect.signature(func).parameters.items():
        if name in ("self", "args", "kwargs") or p.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        required = p.default is inspect.Parameter.empty
        default = None if required else p.default
        params.append(
            ToolParameter(
                name,
                _tool_param_type(p.annotation),
                f"Parameter '{name}'.",
                required=required,
                default=default,
            )
        )
    return params


def agent_tool_domains() -> set:
    """Domains the agent may choose between, from settings.AGENT_TOOL_DOMAINS.
    "*" means every domain."""
    raw = (settings.AGENT_TOOL_DOMAINS or "").strip()
    if raw == "*":
        return set()          # empty set == no filtering, see build_tools()
    return {d.strip() for d in raw.split(",") if d.strip()}


def build_tools() -> list:
    """Build yukta Tool instances from the @pipeline_tool-registered TOOL_REGISTRY
    entries the AGENT may select from.

    Scoped by settings.AGENT_TOOL_DOMAINS. This is deliberately NOT every tool: the
    analytics chain is sequenced deterministically by routes.py, so advertising it to
    the agent spends schema tokens on every request for capabilities the agent never
    picks. Tools outside the scope remain fully callable -- call_tool() resolves
    against get_tool_registry(), which is unfiltered.
    """
    allowed = agent_tool_domains()
    tools = []
    for tool_name, entry in TOOL_REGISTRY.items():
        if allowed and entry.domain not in allowed:
            continue

        func = entry.func
        doc = inspect.getdoc(func) or f"Runs the {tool_name} pipeline step."
        # Only the first docstring line/sentence is used as the tool description --
        # yukta's tool-schema serialization sends the full string to the LLM on every
        # call, so a multi-paragraph docstring would silently blow the token budget.
        description = doc.strip().split("\n")[0].strip()

        tools.append(
            Tool(
                name=tool_name,
                description=description,
                parameters=_params_from_signature(func),
                function=func,
            )
        )
    return tools


def _build_tool_processor() -> ToolProcessor:
    processor = ToolProcessor()
    for tool in build_tools():
        processor.add_tool(tool)
    return processor


def estimate_token_count(text: str) -> int:
    """Cheap approximation: whitespace word count * 1.3. Good enough for a
    budget warning, not meant to match a real tokenizer exactly."""
    return int(len(text.split()) * 1.3)


def _assembled_prompt_text(tools: list) -> str:
    """Concatenate the system prompt with every tool's name/description/param
    schema, the same content that ends up in the LLM's context on every call,
    for token-budget estimation."""
    parts = [_load_system_prompt()]
    for tool in tools:
        parts.append(tool.name)
        parts.append(tool.description)
        for p in tool.parameters:
            parts.append(f"{p.name} {p.type} {p.description}")
    return "\n".join(parts)


def check_prompt_budget(tools: list) -> int:
    """Log a warning if the assembled system prompt + tool schemas exceed
    settings.SYSTEM_PROMPT_MAX_TOKENS. Returns the estimated token count."""
    estimated = estimate_token_count(_assembled_prompt_text(tools))
    if estimated > settings.SYSTEM_PROMPT_MAX_TOKENS:
        logger.warning(
            f"Assembled system prompt + tool schemas are ~{estimated} tokens, "
            f"exceeding SYSTEM_PROMPT_MAX_TOKENS={settings.SYSTEM_PROMPT_MAX_TOKENS}."
        )
    else:
        logger.info(f"Assembled system prompt + tool schemas are ~{estimated} tokens.")
    return estimated


def _build_config() -> AgentConfig:
    config = AgentConfig(
        log_level=logging.INFO,
        enable_logging=True,
        auto_save_chat=True,
        max_iter=120,
    )
    config.system_name = "tb-v2-agent"
    return config


def _build_memory(llm: VLLMClient) -> Memory:
    memory = create_memory(system_prompt=_load_system_prompt(), max_tokens=15000)
    if hasattr(llm, "get_context_window"):
        try:
            real_context_window = llm.get_context_window()
            memory.chat.context_window = real_context_window
            memory.chat.max_input_tokens = real_context_window - memory.chat.context_buffer
        except Exception as e:
            logger.warning(f"Failed to correct memory context window: {e}")
    return memory


def build_tb_agent():
    """Build and return a fully configured TB-v2 agent instance."""
    llm = _build_llm()
    tools = build_tools()
    check_prompt_budget(tools)

    processor = ToolProcessor()
    for tool in tools:
        processor.add_tool(tool)

    system_prompt = SystemPrompt("TB_v2_Agent", _load_system_prompt())
    config = _build_config()
    memory = _build_memory(llm)

    agent = create_agent(
        name="TB_v2_Agent",
        system_prompt=system_prompt,
        llm_client=llm,
        tools_processor=processor,
        config=config,
    )
    agent.set_memory(memory)
    return agent


_agent = None


def get_agent():
    """Lazily build and cache the module-level agent instance."""
    global _agent
    if _agent is None:
        _agent = build_tb_agent()
    return _agent


_tool_registry = None


def get_tool_registry() -> dict:
    """name -> raw (undecorated-wrapper) callable, for routes that want to call
    one deterministic tool directly instead of round-tripping through the LLM
    agent. Lazily built and cached; does not require yukta."""
    global _tool_registry
    if _tool_registry is None:
        # Unfiltered by design: every deterministic call_tool() in routes.py
        # resolves here, including tools the agent is not shown.
        _tool_registry = {name: entry.func for name, entry in TOOL_REGISTRY.items()}
    return _tool_registry


class ToolNotAvailableError(Exception):
    """Raised when a route asks for a tool that hasn't been registered (module
    missing or failed to import)."""


def call_tool(name: str, **kwargs) -> dict:
    """Invoke one pipeline tool by name directly, bypassing the LLM agent. Use
    for deterministic single-tool routes (upload, list, chat lookups)."""
    func = get_tool_registry().get(name)
    if func is None:
        raise ToolNotAvailableError(f"Tool '{name}' is not registered (module missing or failed to import).")
    return func(**kwargs)
