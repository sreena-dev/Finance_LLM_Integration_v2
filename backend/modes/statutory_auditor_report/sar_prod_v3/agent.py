"""
agent.py — SAR Production v3 Entry Point
=========================================
Yukta-idiomatic implementation — mirrors Fin_Audit_QA/agent.py exactly.

Reads PROMPT.md and parses named sections (## SECTION_NAME).
Creates agent factory functions for each extraction/writing role.

Entry points:
  - build_extractor_main()  → SAR main report JSON extractor  (max_tokens=3000, temp=0.0)
  - build_extractor_caro()  → CARO 2020 JSON extractor        (max_tokens=6000, temp=0.0)
  - build_extractor_ifc()   → IFC JSON extractor              (max_tokens=2000, temp=0.0)
  - build_report_writer()   → SAR review memorandum writer    (max_tokens=16000, temp=0.1)

All extractor agents use max_iter=1 (one-shot) — no ReAct loop for report analysis.
The pipeline (pipeline/sar_report_pipeline.py) calls these factories once at startup.

YUKTA PATTERN (same as Fin_Audit_QA/agent.py):
  - SafeVLLMClient(VLLMClient) — bounds max_tokens to prevent vLLM 400 errors
  - _build_llm(max_tokens, temperature) — creates SafeVLLMClient from env vars
  - _build_config(max_iter, max_tokens)  — creates AgentConfig with tracing
  - _build_memory(system_prompt)         — creates Memory via create_memory()
  - Each factory: create_agent() → agent.set_memory(memory) → return agent

LLM CONFIG (reads from environment):
  GENERATION_BASE_URL  — vLLM / OpenAI-compatible endpoint (e.g. http://10.10.116.215:30004)
  GENERATION_MODEL     — model name (e.g. gemma-4-26b-a4b-it)
  GENERATION_TIMEOUT   — request timeout seconds (default 300)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("sar_prod_v3.agent")

# ─── Paths ───────────────────────────────────────────────────────────────────
_HERE = Path(__file__).parent
PROMPT_PATH = _HERE / "PROMPT.md"


# ===========================================================================
# PROMPT.md parser (same as Fin_Audit_QA pattern)
# ===========================================================================

def parse_prompts(prompt_file: Path = PROMPT_PATH) -> dict[str, str]:
    """
    Reads PROMPT.md and splits it into named sections.
    Sections are delimited by lines starting with "## ".
    Lines starting with "#" (not "##") are file-level comments and ignored.

    Returns:
        {"EXTRACTOR_MAIN": "<prompt text>", "EXTRACTOR_CARO": ..., ...}
    """
    text = prompt_file.read_text(encoding="utf-8")
    sections: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []

    for line in text.splitlines():
        if line.startswith("## "):
            if current_name:
                sections[current_name] = "\n".join(current_lines).strip()
            current_name = line[3:].strip()
            current_lines = []
        elif line.startswith("# ") or line.startswith("#\n") or line == "#":
            continue  # Skip file-level comments
        else:
            if current_name is not None:
                current_lines.append(line)

    if current_name:
        sections[current_name] = "\n".join(current_lines).strip()

    return sections


def _load_prompts() -> dict[str, str]:
    """Load prompts from PROMPT.md. Called lazily by each factory."""
    return parse_prompts()


# ===========================================================================
# SafeVLLMClient — bounds max_tokens to prevent vLLM 400 errors
# (identical pattern to Fin_Audit_QA/agent.py)
# ===========================================================================

try:
    from yukta.core.Clients import VLLMClient as _VLLMClient

    class SafeVLLMClient(_VLLMClient):
        """
        VLLMClient wrapper that strictly bounds max_tokens to prevent vLLM context
        limit errors when yukta's internal tokenizer underestimates tool schema tokens.

        Pattern copied from Fin_Audit_QA/agent.py.
        """
        _HARD_LIMIT = 20000   # absolute ceiling — raise if your model supports more

        def generate(self, messages, tools=None, **kwargs):
            # Bound max_tokens to avoid vLLM 400 / context-exceeded errors
            kwargs["max_tokens"] = min(kwargs.get("max_tokens", self._HARD_LIMIT), self._HARD_LIMIT)
            return super().generate(messages, tools=tools, **kwargs)

    _YUKTA_AVAILABLE = True

except ImportError:
    _YUKTA_AVAILABLE = False
    SafeVLLMClient = None  # type: ignore


# ===========================================================================
# LLM / Config / Memory builders
# (same structure as _build_llm / _build_config / _build_memory in Fin_Audit_QA)
# ===========================================================================

def _build_llm(max_tokens: int = 4096, temperature: float = 0.0):
    """
    Constructs and returns a SafeVLLMClient using env var configuration.

    Reads:
      GENERATION_BASE_URL  — base URL of vLLM endpoint
      GENERATION_MODEL     — model name
    """
    if not _YUKTA_AVAILABLE:
        return None   # pipeline will fall back to _make_direct_llm_caller

    base_url = os.environ.get("GENERATION_BASE_URL", "http://localhost:8000").rstrip("/")
    model    = os.environ.get("GENERATION_MODEL", "gemma-4-26b-a4b-it")

    return SafeVLLMClient(
        model_name=model,
        base_url=base_url,
        max_tokens=max_tokens,
        temperature=temperature,
    )


def _build_config(max_iter: int = 1, max_tokens: int = 4096, name: str = "sar_agent"):
    """
    Production-grade AgentConfig with logging and OpenTelemetry tracing.
    max_iter=1 for one-shot extractors/writer (no ReAct loop needed).
    """
    if not _YUKTA_AVAILABLE:
        return None

    from yukta import AgentConfig
    from yukta.instrumentation.setup import init_tracing_from_config

    config = AgentConfig(
        log_level=logging.INFO,
        enable_logging=True,
        auto_save_chat=True,
        max_iter=max_iter,
    )

    # `ENABLE_TRACING` was previously ignored here — this always set
    # open_telemetry=True and called init_tracing_from_config regardless,
    # so with no Phoenix collector running (the default, and the case with
    # ENABLE_TRACING=false in .env) every agent completion tried to export a
    # span batch to an unreachable localhost:6006, retried, and failed —
    # pure latency and log noise on every single call, 4x per report run.
    # The try/except below only ever caught failures from the *synchronous*
    # init call; the actual export failures happen later, asynchronously,
    # during the run, which is what was showing up in the logs. Matches the
    # Financial_Statement branch's `if not config.ENABLE_TRACING: skip`
    # convention (tracing_setup.py) rather than inventing a second one.
    tracing_enabled = os.environ.get("ENABLE_TRACING", "false").strip().lower() in {"1", "true", "yes"}
    config.open_telemetry = tracing_enabled

    if tracing_enabled:
        config.phoenix_endpoint = os.environ.get("PHOENIX_ENDPOINT", "http://localhost:6006/v1/traces")
        config.system_name = f"sar-prod-v3-{name}"
        try:
            init_tracing_from_config(config)
        except Exception as exc:
            logger.debug("Tracing init skipped (non-fatal): %s", exc)

    return config


def _build_memory(system_prompt_text: str, max_tokens: int = 10000):
    """
    Token-aware memory so that artifact paths generated by early tools
    remain accessible when the agent invokes later tools.

    Pattern copied from Fin_Audit_QA/agent.py's _build_memory().
    """
    if not _YUKTA_AVAILABLE:
        return None

    from yukta import create_memory
    return create_memory(
        system_prompt=system_prompt_text,
        max_tokens=max_tokens,
    )


# ===========================================================================
# Agent factory functions
# ===========================================================================

def build_extractor_main(llm=None):
    """
    Builds the SAR Main Report Extractor agent.
    Extracts: opinion, basis, EoM, KAM, going concern, Rule 11,
              Section 143(3), formal checks.
    Returns:
        Callable: agent(text: str) → dict (parsed JSON)
    """
    prompts = _load_prompts()
    system_prompt_text = prompts["EXTRACTOR_MAIN"]

    if _YUKTA_AVAILABLE:
        from yukta import SystemPrompt, create_agent
        client  = llm or _build_llm(max_tokens=3000, temperature=0.0)
        config  = _build_config(max_iter=1, max_tokens=3000, name="extractor-main")
        memory  = _build_memory(system_prompt_text, max_tokens=6000)
        agent   = create_agent(
            name="SAR_EXTRACTOR_MAIN",
            system_prompt=SystemPrompt("SAR_EXTRACTOR_MAIN", system_prompt_text),
            llm_client=client,
            config=config,
        )
        if memory:
            agent.set_memory(memory)
        return agent

    return _make_direct_llm_caller(system_prompt_text, max_tokens=3000, temperature=0.0, parse_json=True)


def build_extractor_caro(llm=None):
    """
    Builds the CARO 2020 Extractor agent.
    Extracts all 21 CARO clauses with adverse/partial flags.
    Returns:
        Callable: agent(text: str) → dict (parsed JSON)
    """
    prompts = _load_prompts()
    system_prompt_text = prompts["EXTRACTOR_CARO"]

    if _YUKTA_AVAILABLE:
        from yukta import SystemPrompt, create_agent
        client  = llm or _build_llm(max_tokens=6000, temperature=0.0)
        config  = _build_config(max_iter=1, max_tokens=6000, name="extractor-caro")
        memory  = _build_memory(system_prompt_text, max_tokens=6000)
        agent   = create_agent(
            name="SAR_EXTRACTOR_CARO",
            system_prompt=SystemPrompt("SAR_EXTRACTOR_CARO", system_prompt_text),
            llm_client=client,
            config=config,
        )
        if memory:
            agent.set_memory(memory)
        return agent

    return _make_direct_llm_caller(system_prompt_text, max_tokens=6000, temperature=0.0, parse_json=True)


def build_extractor_ifc(llm=None):
    """
    Builds the IFC Report Extractor agent.
    Extracts: IFC opinion type, material weaknesses, significant deficiencies.
    Returns:
        Callable: agent(text: str) → dict (parsed JSON)
    """
    prompts = _load_prompts()
    system_prompt_text = prompts["EXTRACTOR_IFC"]

    if _YUKTA_AVAILABLE:
        from yukta import SystemPrompt, create_agent
        client  = llm or _build_llm(max_tokens=2000, temperature=0.0)
        config  = _build_config(max_iter=1, max_tokens=2000, name="extractor-ifc")
        memory  = _build_memory(system_prompt_text, max_tokens=4000)
        agent   = create_agent(
            name="SAR_EXTRACTOR_IFC",
            system_prompt=SystemPrompt("SAR_EXTRACTOR_IFC", system_prompt_text),
            llm_client=client,
            config=config,
        )
        if memory:
            agent.set_memory(memory)
        return agent

    return _make_direct_llm_caller(system_prompt_text, max_tokens=2000, temperature=0.0, parse_json=True)


def build_report_writer(llm=None):
    """
    Builds the SAR Report Writer agent.
    Writes the complete 17-section SAR review memorandum from assembled data.
    This agent has NO tools — all input data is provided in the user message.
    Returns:
        Callable: agent(user_message: str) → str (markdown report)
    """
    prompts = _load_prompts()
    system_prompt_text = prompts["REPORT_WRITER"]

    if _YUKTA_AVAILABLE:
        from yukta import SystemPrompt, create_agent
        client  = llm or _build_llm(max_tokens=16000, temperature=0.1)
        config  = _build_config(max_iter=1, max_tokens=16000, name="report-writer")
        # Larger memory for writer — needs full context of all preceding data
        memory  = _build_memory(system_prompt_text, max_tokens=10000)
        agent   = create_agent(
            name="SAR_REPORT_WRITER",
            system_prompt=SystemPrompt("SAR_REPORT_WRITER", system_prompt_text),
            llm_client=client,
            config=config,
        )
        if memory:
            agent.set_memory(memory)
        return agent

    return _make_direct_llm_caller(system_prompt_text, max_tokens=8192, temperature=0.1, parse_json=False)


# ===========================================================================
# Writer user message assembler (called by pipeline, not by the LLM)
# ===========================================================================

def build_writer_user_message(
    merged_json: dict,
    coherence_observations: list[dict],
    sa_reference: str,
    caro_reference: str,
    financial_tables: dict,
    directors_report: str,
    preflight_summary: str,
    company: str,
    fy_label: str,
    scope: str = "standalone",
) -> str:
    """
    Assembles the user message for the SAR_REPORT_WRITER agent.
    Called by the pipeline after all data has been gathered and coherence checked.
    """
    import json as _json

    obs_text = "\n".join(
        f"- [{o['check_id']}] {o['tag']} ({o.get('risk_rating', '?')}): "
        f"{o['component']} — {o['observation']}"
        for o in coherence_observations
    ) or "No coherence observations raised."

    fs_text = ""
    if financial_tables:
        parts = []
        for key in ["balance_sheet", "profit_loss", "cash_flow"]:
            tbl = financial_tables.get(key)
            if tbl and isinstance(tbl, dict):
                parts.append(
                    f"**{key.replace('_', ' ').title()}** (Page {tbl.get('page_no', '?')}, "
                    f"Unit: {tbl.get('unit', '?')}):\n{tbl.get('table_md', '')}"
                )
        fs_text = "\n\n".join(parts)

    return (
        f"**SCOPE:** {scope.upper()} | **COMPANY:** {company} | **FY:** {fy_label}\n\n"
        f"**STRUCTURED DATA PACKAGE (from Extractor Agents):**\n"
        f"```json\n{_json.dumps(merged_json, indent=2)}\n```\n\n"
        f"**PRE-FLIGHT SUMMARY:**\n{preflight_summary}\n\n"
        f"**COHERENCE OBSERVATIONS (already tagged — reference in your report):**\n{obs_text}\n\n"
        f"**SA 700 / SA 705 / SA 706 REFERENCE EXCERPTS:**\n{sa_reference}\n\n"
        f"**CARO 2020 REFERENCE EXCERPTS:**\n{caro_reference}\n\n"
        f"**KEY FINANCIAL STATEMENTS:**\n{fs_text or 'Not available in supplied package.'}\n\n"
        f"**DIRECTORS' REPORT (for SA 720 consistency check):**\n"
        f"{(directors_report or '')[:8000] or 'Not available in supplied package.'}\n\n"
        "INSTRUCTION: Generate the complete two-part report now. "
        "Write PART 2 in full — every section 2.1 to 2.16 with minimum 3 paragraphs each. "
        "Copy the exact boilerplate for Sections 1.5 and 2.16."
    )


# ===========================================================================
# Fallback: direct HTTP LLM call (when yukta is NOT installed)
# ===========================================================================

def _make_direct_llm_caller(
    system_prompt: str, max_tokens: int, temperature: float, parse_json: bool
):
    """
    Returns a callable that makes a direct HTTP call to the vLLM endpoint.
    Used as a fallback when the yukta framework is not installed.

    Reads GENERATION_BASE_URL and GENERATION_MODEL from environment.
    """
    import json as _json
    import requests

    def _get_settings():
        base = os.environ.get("GENERATION_BASE_URL", "").rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        model   = os.environ.get("GENERATION_MODEL", "")
        timeout = int(os.environ.get("GENERATION_TIMEOUT", "300"))
        return base, model, timeout

    def _call(user_message: str):
        base, model, timeout = _get_settings()
        resp = requests.post(
            f"{base}/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_message},
                ],
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        if parse_json:
            # Strip markdown code fences if present
            if content.startswith("```"):
                parts = content.split("```")
                content = parts[1] if len(parts) > 1 else content
                if content.startswith("json"):
                    content = content[4:]
            return _json.loads(content.strip())
        return content

    return _call
