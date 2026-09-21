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

    # `api_key` is passed through **kwargs into the base client's config, which
    # turns it into an `Authorization: Bearer ...` header (see
    # yukta/core/Clients/base_client.py). Only sent when set: the header key is
    # absent from config when the value is empty, so an endpoint that needs no
    # auth still sees an unauthenticated request rather than "Bearer ".
    kwargs = {}
    api_key = os.environ.get("GENERATION_API_KEY", "").strip()
    if api_key:
        kwargs["api_key"] = api_key

    client = SafeVLLMClient(
        model_name=model,
        base_url=base_url,
        max_tokens=max_tokens,
        temperature=temperature,
        **kwargs,
    )

    # The base client turns config["api_key"] into an Authorization header for
    # its POST path only. get_model_info() issues a *separate*
    # self._session.get("/v1/models") that bypasses that path, so on a gateway
    # which authenticates /v1/models too it 401s on every call — visible as
    # "Failed to fetch model info from vLLM: 401 Unauthorized" and a silent
    # fallback to a default context window. Setting the header as a session
    # default covers both requests; the per-request header still takes
    # precedence where the base client sets one explicitly.
    if api_key:
        session = getattr(client, "_session", None)
        if session is not None:
            session.headers["Authorization"] = f"Bearer {api_key}"

    return client


def _build_config(max_iter: int = 1, max_tokens: int = 4096, name: str = "sar_agent"):
    """
    Production-grade AgentConfig with logging and OpenTelemetry tracing.
    max_iter=1 for one-shot extractors/writer (no ReAct loop needed).
    """
    if not _YUKTA_AVAILABLE:
        return None

    from yukta import AgentConfig

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
            # Imported here rather than at the top of the function: when tracing
            # is off (the default) a yukta build without the instrumentation
            # module must not stop the agent being built at all.
            from yukta.instrumentation.setup import init_tracing_from_config

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

def _format_formal_checks_summary(formal_checks_summary: dict | None) -> str:
    """Renders the Gap-closure Phase 1 deterministic formal-checks summary
    (UDIN, SA 706.8 EoM closing sentence, report-date-vs-FS-approval-date
    sequencing) into three lines the writer can quote directly in section
    1.3 / 2.1, instead of judging any of the three itself from raw text.

    See formal_review.run_formal_checks() for what populates this dict, and
    GAP_CLOSURE_LOG.md for why these three checks moved out of the writer's
    judgment and into deterministic code.
    """
    if not formal_checks_summary:
        return "Not available for this run."

    udin = formal_checks_summary.get("udin") or {}
    if udin.get("checks"):
        udin_line = (
            "All present and ICAI-format-valid."
            if udin.get("all_valid")
            else "; ".join(c["observation"] for c in udin["checks"] if not c["passed"])
        )
    else:
        udin_line = "Not verified — no auditor/UDIN block was extracted."

    eom = formal_checks_summary.get("eom_closing_sentence")
    if eom is None:
        eom_line = "Not applicable — no Emphasis of Matter section was identified."
    else:
        eom_line = eom["observation"]

    date_seq = formal_checks_summary.get("report_date_sequence")
    if date_seq is None:
        date_line = "Not verified — report date and/or FS approval date were not both extracted."
    else:
        date_line = date_seq["observation"]

    return (
        f"- UDIN: {udin_line}\n"
        f"- SA 706.8 Emphasis-of-Matter closing sentence: {eom_line}\n"
        f"- Report date vs. FS approval date (s.134(1)): {date_line}"
    )


def _format_applicability_summary(applicability: dict | None) -> str:
    """Renders the Gap-closure Phase 2 (Gap #4) applicability verdicts —
    CARO / IFC / KAM — into three lines telling the writer which areas are
    confirmed-applicable (reason freely about their clause/opinion data),
    and which are unresolved (do not raise a "missing clause"/"missing KAM"
    finding — that has already been raised as its own AUDIT_POINTER;
    source spec §22 / §19.1)."""
    if not applicability:
        return "Not available for this run."
    lines = []
    labels = {"caro": "CARO 2020", "ifc": "IFC (s.143(3)(i))", "kam": "Key Audit Matters"}
    for area, label in labels.items():
        result = applicability.get(area) or {}
        status = result.get("status", "uncertain")
        lines.append(f"- {label}: {status.upper()} — {result.get('basis', '')}")
    return "\n".join(lines)


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
    formal_checks_summary: dict | None = None,
    review_status: str = "complete",
    review_status_reasons: list[str] | None = None,
    applicability: dict | None = None,
) -> str:
    """
    Assembles the user message for the SAR_REPORT_WRITER agent.
    Called by the pipeline after all data has been gathered and coherence checked.

    `formal_checks_summary`, `review_status` and `review_status_reasons` are
    Gap-closure Phase 1 additions (Gap #5 — see GAP_CLOSURE_LOG.md): the
    three formal checks (UDIN, SA 706.8 closing sentence, report-date
    sequencing) and the package-level completeness verdict are now computed
    deterministically before this message is built, and handed to the
    writer as already-settled facts — mirroring how PRE-FLIGHT SUMMARY
    below already tells the writer "do NOT re-raise" pre-flight findings.

    `applicability` is a Gap-closure Phase 2 addition (Gap #4 — see
    applicability.py): tells the writer which of CARO / IFC / KAM are
    confirmed applicable versus unresolved, so it does not independently
    conclude "clause X is missing" or "KAM is missing" on an area whose
    applicability this package cannot confirm (source spec §22 / §19.1).
    """
    import json as _json

    obs_text = "\n".join(
        f"- [{o['check_id']}] {o['tag']} ({o.get('risk_rating', '?')}): "
        f"{o['component']} — {o['observation']}"
        for o in coherence_observations
    ) or "No coherence observations raised."

    review_status_line = ""
    if review_status != "complete":
        reasons_text = " ".join(review_status_reasons or [])
        review_status_line = (
            f"\n**REVIEW STATUS: {review_status.upper()}** — {reasons_text} "
            f"Label the memorandum's Memorandum Status accordingly "
            f"(e.g. 'PROVISIONAL — PENDING UDIN CONFIRMATION') and reduce confidence "
            f"language for report-dependent observations; do not withhold analysis.\n"
        )

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
        f"**SCOPE:** {scope.upper()} | **COMPANY:** {company} | **FY:** {fy_label}\n"
        f"{review_status_line}\n"
        f"**STRUCTURED DATA PACKAGE (from Extractor Agents):**\n"
        f"```json\n{_json.dumps(merged_json, indent=2)}\n```\n\n"
        f"**PRE-FLIGHT SUMMARY:**\n{preflight_summary}\n\n"
        f"**FORMAL CHECKS SUMMARY (deterministic — already verified, do NOT re-derive from raw text; "
        f"cite these verdicts directly in 1.3 and 2.1):**\n{_format_formal_checks_summary(formal_checks_summary)}\n\n"
        f"**APPLICABILITY SUMMARY (deterministic — do NOT raise a new 'missing clause'/'missing KAM' "
        f"finding on an area marked UNCERTAIN below; that has already been raised as its own AUDIT_POINTER):**\n"
        f"{_format_applicability_summary(applicability)}\n\n"
        f"**COHERENCE OBSERVATIONS (already tagged — reference in your report):**\n{obs_text}\n\n"
        f"**SA 700 / SA 705 / SA 706 REFERENCE EXCERPTS:**\n{sa_reference}\n\n"
        f"**CARO 2020 REFERENCE EXCERPTS:**\n{caro_reference}\n\n"
        f"**KEY FINANCIAL STATEMENTS:**\n{fs_text or 'Not available in supplied package.'}\n\n"
        f"**DIRECTORS' REPORT (for SA 720 consistency check):**\n"
        f"{(directors_report or '')[:8000] or 'Not available in supplied package.'}\n\n"
        "INSTRUCTION: Generate the complete SAR review memorandum now, strictly following the structure below. "
        "DO NOT replace any table with prose. DO NOT skip any section. DO NOT invent headings.\n\n"
        "# SAR REVIEW — [ENTITY] FY [YEAR]\n\n"
        "## PART 1 — EXECUTIVE SUMMARY\n\n"
        "### 1.1 Input Summary\n"
        "One paragraph: entity name, FY, scope (standalone/consolidated), auditor firm(s) and FRN, "
        "overall opinion type, and overall risk posture (High / Moderate / Low) with the specific driver named.\n\n"
        "### 1.2 Executive Risk Summary\n"
        "One bullet per material observation ONLY. Each bullet MUST use this exact format:\n"
        "`[TAG | Risk] — Short label: \"short verbatim quote (≤ one sentence)\"`\n"
        "Allowed TAGs: FINDING | RISK FLAG | AUDIT POINTER\n"
        "Allowed Risk levels: High | Medium | Low | Information request only\n"
        "End the list with this exact count line:\n"
        "`Total: FINDING: N | RISK FLAG: N | AUDIT POINTER: N`\n\n"
        "### 1.3 Opinion Summary Table\n"
        "MANDATORY — exactly 3 rows, no more, no less:\n"
        "| Aspect | Opinion Type | Key Qualifier |\n"
        "|---|---|---|\n"
        "| Main SAR | [Unmodified / Qualified / Adverse / Disclaimer] | [one-line basis or 'None'] |\n"
        "| IFC / ICFR | [Unmodified / Qualified / Adverse / Disclaimer] | [one-line basis or 'None'] |\n"
        "| EoM Closing Sentence | Present / Not Present | [quote or 'N/A'] |\n\n"
        "### 1.4 Key Observations Table\n"
        "MANDATORY TABLE — one row per observation from 1.2:\n"
        "| Obs# | Check ID | Component | Tag | Risk | Summary |\n"
        "|---|---|---|---|---|---|\n"
        "[One row per observation — Check IDs e.g. OPN-705-01, CARO-IX-02, RULE11G-01, EOM-706-01]\n\n"
        "### 1.5 Limitations and Caveats\n"
        "Copy this exact text verbatim:\n"
        "\"This review is based on a structured extraction of the auditors' report package, not a "
        "direct reading of the source document, and is not a re-audit. Any field or clause marked "
        "absent or not identified reflects a gap in extraction, not confirmation that it is absent "
        "from the signed report. All quotations, dates, and figures should be treated as unverified "
        "until checked against the source. This review does not conclude that the report was "
        "negligent, that procedures were not performed, or that any balance is misstated, and "
        "remains subject to verification by the audit team.\"\n\n"
        "---\n\n"
        "## PART 2 — DETAILED MEMORANDUM\n\n"
        "MANDATORY DEPTH RULE: Each section below MUST be written in full. "
        "DO NOT compress to one line. Every analysis point listed must be addressed. "
        "Every claim cites its source in parentheses (e.g. (Basis for Opinion), (CARO Clause vii)).\n\n"
        "### 2.1 Input Package Completeness\n"
        "For each component below, state Present or Absent on a separate line. "
        "If Absent → raise an AUDIT POINTER and state what it prevents from being verified.\n"
        "Components: Main SAR text | CARO 2020 Annexure | IFC Annexure | FS tables (BS, P&L, Cash Flow) | "
        "Directors' / Board Report | C&AG Section 143(5) directions | Prior-year SAR | Prior C&AG comments.\n"
        "Also confirm: standalone or consolidated scope; single auditor or joint auditors (list FRN of each).\n\n"
        "### 2.2 Opinion and Basis for Opinion\n"
        "- Opinion classification: Unmodified / Qualified / Adverse / Disclaimer. "
        "Apply SA 705 pervasiveness test. State verbatim quote of opinion paragraph. "
        "Test for buried qualifications: 'subject to' language, hedge words ('we believe'), long caveat lists.\n"
        "- Basis for Opinion: is independence and ethical-compliance declaration present? "
        "Cite the exact wording. Are SAs under s.143(10) referenced?\n"
        "- Cross-reference: does the opinion hold given CARO adverse clauses and IFC findings? "
        "State whether the unmodified opinion is coherent with each.\n\n"
        "### 2.3 Material Uncertainty Related to Going Concern (SA 570)\n"
        "- Is a MURGC paragraph present? Quote the going concern statement verbatim.\n"
        "- Check FS stress indicators: negative net worth, current ratio < 1.0, "
        "negative operating cash flow, CARO clause (ix) defaults, CARO clause (xix) adverse, "
        "material losses over multiple years. State whether any indicator is present.\n"
        "- Apply SA 570 decision logic: (1) auditor acknowledged uncertainty but no MURGC → FINDING; "
        "(2) uncertainty only inferred here → RISK_FLAG; (3) CARO (xix) adverse + no MURGC treatment → High RISK_FLAG; "
        "(4) going concern appropriate + MURGC present → confirm cross-reference to FS note.\n\n"
        "### 2.4 Key Audit Matters (SA 701)\n"
        "For EACH KAM identified:\n"
        "- State KAM title and the related note/FS line item.\n"
        "- Why significant: summarise the auditor's stated reason.\n"
        "- How addressed: what procedures did the auditor describe?\n"
        "- Is it entity-specific or generic boilerplate? Boilerplate → RISK_FLAG.\n"
        "- Verify KAM note amount/disclosure agrees with FS — mismatch → FINDING.\n"
        "- Is any high-risk FS area missing from KAM coverage? Silence → RISK_FLAG.\n\n"
        "### 2.5 Emphasis of Matter / Other Matter (SA 706)\n"
        "**2.5.1 Emphasis of Matter:**\n"
        "List every EoM item: item number, note reference, one-line description, and verbatim quote. "
        "Verify the SA 706 Para 8 mandatory closing sentence is present — if absent → FINDING, "
        "quote what is present instead. Check each EoM points to an identifiable note/disclosure — "
        "no corresponding disclosure → RISK_FLAG + AUDIT_POINTER. "
        "Assess whether EoM is being used inappropriately instead of a modification.\n"
        "**2.5.2 Other Matter:**\n"
        "State what the Other Matter communicates. Confirm it covers matters NOT in the FS (distinct from EoM). "
        "Note any non-compliance or reliance on other auditors mentioned.\n"
        "**2.5.3 Consistency Matrix — EoM / KAM / OM vs Financial Statements:**\n"
        "MANDATORY TABLE — one row per EoM item, per KAM, and per key CARO finding. Include clean rows.\n"
        "| Signal | FS / Report area cross-checked | Consistent / Contradiction | Tag | Risk |\n"
        "|---|---|---|---|---|\n"
        "[Fill all rows with actual values]\n\n"
        "### 2.6 Other Legal and Regulatory Requirements\n"
        "**2.6.1 Section 143(3):** List each clause (a) through (i) in a structured list. "
        "For each: topic, what the auditor stated, and whether compliant. "
        "Missing clause → RISK_FLAG (requires verification against signed report).\n"
        "**2.6.2 Rule 11:** Cover every Rule 11 sub-item found. For Rule 11(g) audit trail: "
        "if adverse or partial, state verbatim finding, tag FINDING High, and cross-tie to IFC IT controls and C&AG IT direction. "
        "Also cover: pending litigations, foreseeable losses, IEPF, dividend compliance, ultimate beneficiary disclosures.\n"
        "**2.6.3 Section 197(16):** Cover if found. If not applicable, state why.\n\n"
        "### 2.7 CARO 2020 Adverse Clause Review\n"
        "DO NOT list all 21 clauses. Only adverse, partial, or high-risk clauses.\n"
        "For EACH adverse/partial clause:\n"
        "- State clause number, topic, answer type (adverse/partial), and full verbatim finding.\n"
        "- Risk implication: what does this mean for the financial statements?\n"
        "- Expected echo: should this appear in the main opinion / EoM / KAM / IFC / Rule 11 / C&AG? "
        "If echo is absent → RISK_FLAG. Tag the observation.\n"
        "MANDATORY TABLE:\n"
        "| Clause | Topic | Answer | Verbatim Quote | Note Ref | Tag | Risk |\n"
        "|---|---|---|---|---|---|---|\n"
        "[One row per adverse or partial clause]\n"
        "**Compliant Clauses:** [clause numbers only, comma-separated]\n"
        "**Not In Package:** [clauses absent from supplied text]\n\n"
        "### 2.8 Internal Financial Controls (IFC / ICFR)\n"
        "- IFC opinion type (Unmodified/Qualified/Adverse/Disclaimer) and criteria used "
        "(state which guidance note or framework the auditor referenced).\n"
        "- For any control weakness identified: state the weakness, map it to the FS risk it creates, "
        "and identify which FS line items or balances are at elevated risk.\n"
        "- Coherence check: if IFC is adverse/qualified but main opinion is unmodified → FINDING. "
        "Cross-reference with Rule 11(g), CARO clause (xi), and C&AG IT direction if applicable.\n\n"
        "### 2.9 C&AG Section 143(5) Directions\n"
        "If this is a PSU: for each direction issued, state (a) the direction text, "
        "(b) whether the auditor's response is present and responsive, "
        "(c) whether the financial impact is quantified or reconciled. "
        "Unresponsive or unquantified direction → RISK_FLAG.\n"
        "If not a PSU or C&AG directions are absent from the supplied package: "
        "state this explicitly with the source of that determination.\n\n"
        "### 2.10 Subsequent Events and Report-Date Logic\n"
        "- State the report date and the FS approval date (s.134(1)). "
        "If report date precedes FS approval date → FINDING (direct violation).\n"
        "- Assess reasonableness of the report-date gap against the entity's own Board/AGM timeline. "
        "DO NOT apply any fixed numeric day-range benchmark — SA 700 sets none.\n"
        "- Are any subsequent events (post year-end to report date) disclosed in the report or FS notes? "
        "Material undisclosed subsequent events → RISK_FLAG + AUDIT_POINTER.\n\n"
        "### 2.11 SA 720 — Other Information Consistency\n"
        "- Compare going-concern treatment: what does the auditor state vs what does the Board/Directors' "
        "Report say about the company's ability to continue as a going concern?\n"
        "- Compare key financial metrics: are ratios, CSR spend, dividend statements in the Directors' Report "
        "consistent with the FS and with what the auditor's report implies?\n"
        "- Any direct contradiction between Other Information and the FS or the auditor's "
        "report → FINDING. Note and inconsistency that requires explanation → RISK_FLAG.\n\n"
        "### 2.12 Financial Statement Observations\n"
        "Scan FS tables for high-risk items that the SAR is silent on. For each item:\n"
        "- State the FS line item, value, and note reference.\n"
        "- State whether the SAR mentions it (in Opinion, EoM, KAM, CARO, or Rule 11).\n"
        "- If material and silent in the SAR → RISK_FLAG + AUDIT_POINTER.\n"
        "High-risk items to check: defaults, related-party exposures, statutory-dues arrears, "
        "litigation provisions, going-concern indicators, capital commitments.\n\n"
        "### 2.13 Consistency Matrix — Opinion vs CARO vs IFC\n"
        "MANDATORY TABLE — fill Assessment, Tag, and Risk for EVERY row. "
        "DO NOT leave rows blank. Include clean 'Consistent' rows too.\n"
        "| Report Signal | Cross-check | Assessment | Tag | Risk |\n"
        "|---|---|---|---|---|\n"
        "| Unmodified opinion | CARO adverse clauses present? | | | |\n"
        "| Unmodified opinion | IFC material weakness present? | | | |\n"
        "| Unmodified opinion | Negative net worth in BS? | | | |\n"
        "| CARO clause (xix) adverse | Going concern paragraph in main report? | | | |\n"
        "| EoM present | SA 706 Para 8 closing sentence present? | | | |\n"
        "| Rule 11(g) adverse | IFC opinion + C&AG IT direction consistent? | | | |\n"
        "| KAM topics | Corresponding FS notes adequate? | | | |\n"
        "| Directors' Report going concern | Auditor's going concern treatment | | | |\n"
        "| CARO note_ref populated | Note exists in FS and is consistent? | | | |\n\n"
        "### 2.14 Draft Section 143(6) Matters\n"
        "A matter qualifies ONLY when ALL three criteria are met: "
        "(1) material by value, nature, or context; "
        "(2) clearly traced to specific report text AND FS reference; "
        "(3) tagged FINDING — not RISK_FLAG or AUDIT_POINTER alone.\n"
        "For each qualifying matter: state the matter, the specific evidence, and draft language "
        "in safe hypothesis-labelled wording (e.g. 'The supplied package does not contain...').\n"
        "If nothing meets the threshold: state explicitly with the reasoning.\n\n"
        "### 2.15 Limitations and Caveats\n"
        "Copy this EXACT text verbatim — word for word:\n"
        "\"This review is based on a structured extraction of the auditors' report package, not a "
        "direct reading of the source document, and is not a re-audit. Any field or clause marked "
        "absent or not identified reflects a gap in extraction, not confirmation that it is absent "
        "from the signed report. All quotations, dates, and figures should be treated as unverified "
        "until checked against the source. This review does not conclude that the report was "
        "negligent, that procedures were not performed, or that any balance is misstated, and "
        "remains subject to verification by the audit team.\"\n\n"
        "ABSOLUTE RULES:\n"
        "1. Sections 1.3, 1.4, 2.5.3, 2.7, 2.13 MUST be markdown tables — NEVER prose.\n"
        "2. Section 1.2 bullets MUST use `[TAG | Risk] — Label: \"quote\"` format with total count line.\n"
        "3. DO NOT skip any section. Write all sections 2.1 through 2.15 in full.\n"
        "4. DO NOT add any section not listed above. DO NOT append any table after 2.15.\n"
        "5. NEVER say 'the JSON shows', 'the data package', 'marked as false', or 'the AI'.\n"
        "6. Every factual claim MUST cite its source: (Basis for Opinion), (CARO Clause vii), etc.\n"
        "7. 2.13 Consistency Matrix: fill Assessment, Tag and Risk for ALL 9 rows — never leave blank."

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

    def _auth_headers():
        """Bearer header for the generation endpoint, or {} when no key is set.

        This path bypasses the yukta client (it is the direct-HTTP fallback used
        when yukta is unavailable), so it has to add the header itself — the
        client's own api_key plumbing does not apply here.
        """
        key = os.environ.get("GENERATION_API_KEY", "").strip()
        return {"Authorization": f"Bearer {key}"} if key else {}

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
            headers=_auth_headers(),
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
