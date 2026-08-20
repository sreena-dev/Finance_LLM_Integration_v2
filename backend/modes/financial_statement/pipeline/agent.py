"""
agent.py — Orchestrator: the single entry point for the Financial Audit RAG
pipeline.

This file is also meant as a REFERENCE TEMPLATE for anyone standing up
their own agent against a yukta-compatible model server. The core wiring
pattern below is:

    1. create_agent(...)     — yukta's factory function. Takes:
         name             : str, agent identifier (for logs/tracing).
         system_prompt    : yukta.SystemPrompt(name, text) — wraps the raw
                             prompt string.
         tools_processor  : yukta.ToolProcessor() — a registry of
                             yukta.Tool objects the agent may call during
                             its run.
         config           : yukta.AgentConfig(...) — temperature, max_iter
                             (tool-calling round cap), and persistence flags.
         llm_client       : a yukta.core.Clients.* client (here VLLMClient)
                             pointed at your OpenAI-compatible /v1/chat/
                             completions endpoint.
       This returns a yukta.Agent instance — NOT yet run.

    2. agent.run(user_message=..., llm_kwargs={...})
         Executes the agent: sends the system prompt + user message to the
         LLM, and loops up to config.max_iter rounds letting the model call
         tools and read results before producing a final response. Returns
         a dict: {"success": bool, "response": str, "error": str,
         "tool_calls": [...]}

A fresh Agent is built PER REQUEST (never reused across queries) because
tool closures capture that request's DB connection and a mutable
retrieved-chunks list — see ToolRegistry.build_tools in tools_fs.py.
auto_save_chat* is disabled in AgentConfig so these short-lived agents
never hit disk or a chat-history network round trip.

ONE agent, ONE continuous agentic loop ("fs-agent"): query classification,
rules-DB retrieval, every specialized reports-DB tool, and citation
validation all happen inside a single agent.run() call. Retrieval itself
is a tool (`search_knowledge_base`, registered in tools_fs.py's
ToolRegistry) that the agent calls when it decides it needs rules-DB
evidence — there is no separate rewrite/classification LLM call and no
Python-level "Stage A / Stage B / Stage C" split anymore. See prompt.md's
single `## FS_AGENT_PROMPT` section for the full behavior spec.
"""

import json
import re
import time

import tools_fs as tool_names


def _raw_tool_result(self, tool_result) -> str:
    """
    Replacement for yukta's Agent._format_tool_result, bound per instance in
    _build_fs_agent.

    yukta's version is json.dumps(result, indent=2, default=str). Every tool in
    tools_fs.py returns a plain formatted string, so that turns each newline into
    a literal \\n and escapes every quote — pure encoding overhead in a payload
    that is re-sent on every subsequent iteration. Pass strings through untouched
    and defer to the original behaviour for anything else.
    """
    payload = tool_result
    if isinstance(payload, dict):
        # yukta wraps executor output as {"result": ..., "error": ...}.
        for key in ("result", "error"):
            if isinstance(payload.get(key), str):
                return payload[key]
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, indent=2, default=str)


class Orchestrator:
    """
    Single orchestrator class for the whole RAG pipeline. See module
    docstring for the yukta wiring pattern and the single-agent design.
    """

    _PROMPT_PATH_NAME = "prompt.md"

    # Keys that must appear in the final answer JSON.
    _ANSWER_KEYS = {"reasoning_trace", "evidences", "final_answer", "aggregated_summary"}

    # MUST stay in sync with prompt.md's Step-0 intent list — an intent the
    # model reports that is missing here is silently downgraded to GENERAL.
    # test_prompt_sync.py asserts the two match.
    _VALID_INTENTS = {
        "CHANGE_DETECTION", "POLICY_EXPLANATION", "AMENDMENT_STATUS", "COMPARISON",
        "SPECIFIC_LOOKUP", "EAC_OPINION", "COMPLIANCE_FRAMEWORK",
        "STATEMENT_COMPLIANCE_CHECK", "RATIO_ANALYSIS", "RATIO_FORMULA_LOOKUP",
        "AUDIT_RISK_ANALYSIS", "TREND_ANALYSIS", "ACCOUNTING_POLICY_LOOKUP",
        "MATERIALITY_ASSESSMENT", "TIE_OUT_CHECK", "ANNUAL_REPORT_SUMMARY", "AUDITOR_REPORT_REVIEW", "GOING_CONCERN_ASSESSMENT", "ACCOUNT_AREA_REVIEW", "GENERAL",
    }

    def __init__(self, conn=None, conn_reports=None):
        """
        Loads prompt.md's single `## FS_AGENT_PROMPT` section. Builds the
        ToolRegistry used to construct the agent's tools per request.
        `conn`/`conn_reports` are accepted for API symmetry with answer()
        but are NOT required here — answer() takes its own conn/conn_reports
        per call (a psycopg2 connection should not be held open across
        unrelated requests in a long-lived singleton).
        """
        import os

        prompt_path = os.path.join(os.path.dirname(__file__), self._PROMPT_PATH_NAME)
        with open(prompt_path, "r", encoding="utf-8") as f:
            raw = f.read()

        self._system_prompt = self._load_prompt_md(raw)
        self._core_prompt, self._playbooks, self._prompt_tail = self._split_prompt(
            self._system_prompt
        )
        self._tool_registry = tool_names.ToolRegistry()

    @staticmethod
    def _load_prompt_md(raw: str) -> str:
        """Extract the '## FS_AGENT_PROMPT' section body (header stripped)."""
        parts = re.split(r"\n(?=## )", raw.strip())
        for part in parts:
            m = re.match(r"## (\S+)\s*\n(.*)", part.strip(), re.DOTALL)
            if m and m.group(1).strip() == "FS_AGENT_PROMPT":
                return m.group(2).strip()
        raise RuntimeError(
            "prompt.md is missing its '## FS_AGENT_PROMPT' section — cannot build Orchestrator."
        )

    # Query patterns that select a skill playbook. Only one playbook is ever
    # relevant to a question, but all ten used to be sent on every request and
    # on every tool-calling iteration — 7,847 of the prompt's 10,760 tokens.
    #
    # Deliberately biased toward RECALL: a false positive costs ~800 tokens once,
    # a false negative loses that playbook's procedure for the whole answer.
    # Word boundaries matter — see the conflict notes below.
    _PLAYBOOK_TRIGGERS = {
        "Financial Ratio Analysis": (
            r"\bratios?\b|\bformula|\bdscr\b|\brocei?\b|\broce\b|\broi\b|\broa\b|\broe\b"
            r"|\bliquidity\b|\bleverage\b|\bgearing\b|\bturnover\b|\bmargin\b|\bsolvency\b"
            r"|\bcurrent ratio\b|\bquick ratio\b|\bacid.test\b|\bcoverage\b|\bearnings per\b"
        ),
        # 'provisions', 'borrowings', 'inventory' are among the 7 audit schedules,
        # so Audit Risk must win over Account Area Review for those words.
        "Audit Risk Analysis": (
            r"\bschedule note\b|\bppe\b|\bproperty,? plant\b|\binventor(y|ies)\b"
            r"|\binvestments\b|\bprovisions\b|\breceivables\b|\bborrowings\b|\bintangible"
            r"|\bmovement\b|\baudit risk\b|\brisk items?\b|\brollforward\b|\bhidden risk\b"
        ),
        # \badopt / \btransition — "has X adopted Ind AS 115" is a framework
        # question that previously matched no playbook at all.
        "Framework & Compliance Check": (
            r"\bframework\b|\bbasis of preparation\b|\bcompl(y|ies|iant|iance)\b"
            r"|\bschedule iii\b|\bline items?\b|\bdivision i{1,2}\b|\bigaap\b|\bind as 7\b"
            r"|\badopt(ed|ion|s)?\b|\btransition(ed|ing)? to\b"
        ),
        "Multi-Year Trend Analysis": (
            r"\btrend\b|\byoy\b|\byear.on.year\b|\byear.over.year\b|\bcagr\b"
            r"|\bmulti.year\b|\bover the years\b|\b\d.year\b"
        ),
        # \bmaterialit — never bare 'material', which collides with
        # "cost of materials consumed".
        "Materiality Assessment": (
            r"\bmaterialit|\bperformance materiality\b|\bbenchmark\b|\bthreshold\b"
        ),
        # Never bare 'balance' — it appears in almost every finance question.
        "Cross-Statement Tie-Out Checks": (
            r"\btie.?outs?\b|\breconcile\b|\breconciliation\b|\binternal consistency\b"
            r"|\bcross.check\b|\bbalance sheet balance\b|\badd up\b|\binternal(ly)? consisten(cy|t)\b|\bfigures? .{0,20}consisten"
        ),
        "Annual Report Summary": r"\bsummar(y|ise|ize|ising|izing)\b|\boverview\b|\bhighlights\b",
        "Auditor's Report & CARO": (
            r"\bcaro\b|\bclause\b|\bannexure\b|\brule 11\b|\baudit trail\b"
            r"|\baccounting software\b|\bkey audit matters?\b|\bemphasis of matter\b|\bkam\b|\bauditor.{0,3}s report\b|\bcag comment"
        ),
        "Going Concern & Subsequent Events": (
            r"\bgoing concern\b|\bdistress\b|\bcontinue as\b|\bsubsequent event"
            r"|\bevents after\b|\bmaterial uncertainty\b"
        ),
        "Account Area Review": (
            r"\brelated part(y|ies)\b|\brpt\b|\bdeferred tax\b|\btaxation\b"
            r"|\bemployee benefits?\b|\bgratuity\b|\bcsr\b|\bleases?\b|\bsegment\b"
            r"|\bfair value\b|\bfinancial risk\b|\bmanagerial remuneration\b"
            r"|\bcapital management\b|\bcontingent liabilit|\bexceptional items?\b|\bgovernment grants?\b|\bsuspense\b"
        ),
    }

    # At most this many playbooks per request — two covers a genuine compound
    # question (formula AND value) without re-inflating the prompt.
    _MAX_PLAYBOOKS = 2

    # ------------------------------------------------------------------
    # Tool gating — same router, applied to the tool schemas
    # ------------------------------------------------------------------
    # All 24 tool schemas were re-sent on every call (~4,879 tok, 57% of a call).
    # A query only ever needs the tools its playbook uses, so the playbook
    # selection drives tool registration too — keeping procedure and capability
    # in lockstep by construction.
    #
    # Registered alongside any matched playbook. `search_knowledge_base` because
    # a company question can still need a standard; `lookup_report_reference`
    # because several playbooks name it as the fallback for anything they don't
    # cover; `validate_answer` because Rule 6 asks for a citation re-check.
    _SAFETY_TOOLS = {
        "search_knowledge_base", "lookup_report_reference", "validate_answer",
    }

    # No playbook matched => a standards question (EAC_OPINION, COMPARISON,
    # AMENDMENT_STATUS, SPECIFIC_LOOKUP, GENERAL). Those need the rules-DB tools
    # and none of the reports-DB ones. This set is also what makes the four
    # rules-only tools reachable at all, since they belong to no playbook.
    _RULES_DB_TOOLS = {
        "search_knowledge_base", "get_eac_opinion_by_topic", "compare_standards",
        "check_amendment_status", "cross_reference_lookup", "validate_answer",
    }

    # Keys MUST match _PLAYBOOK_TRIGGERS exactly — test_prompt_sync.py asserts it,
    # along with every tool being reachable from one of these three sets.
    _PLAYBOOK_TOOLS = {
        "Financial Ratio Analysis": {"compute_ratio_analysis", "get_ratio_formula"},
        "Audit Risk Analysis": {
            "get_audit_report_highlights", "get_schedule_note", "get_audit_requirements",
            "search_company_disclosures", "get_accounting_policy_note",
        },
        "Framework & Compliance Check": {
            "get_reporting_framework", "check_statement_compliance",
        },
        # get_schedule_note: the playbook tells the agent to look for a disclosed
        # reason before commenting on a Significant row.
        "Multi-Year Trend Analysis": {"get_multi_year_trend", "get_schedule_note"},
        "Materiality Assessment": {"compute_materiality"},
        # get_schedule_note: the playbook mandates it as the one permitted
        # follow-up when a note-to-face tie comes back NOT AVAILABLE. Without it
        # in this set the instruction was unfollowable — the model was told to
        # fetch the schedule and then offered no tool that could, which is how
        # tie-out answers ended at "could not verify" with the note one call away.
        "Cross-Statement Tie-Out Checks": {"run_tie_out_checks", "get_schedule_note"},
        "Annual Report Summary": {"summarize_annual_report"},
        "Auditor's Report & CARO": {
            "check_caro_clauses", "check_rule_11g", "get_audit_report_highlights",
        },
        # get_schedule_note: same reason as Trend — check the note before inferring.
        "Going Concern & Subsequent Events": {"assess_going_concern", "get_schedule_note"},
        "Account Area Review": {
            "review_account_area", "search_company_disclosures", "get_accounting_policy_note",
        },
    }

    # ------------------------------------------------------------------
    # Standards-question guard
    # ------------------------------------------------------------------
    # Every playbook above is a REPORTS-DB procedure: it needs a company and a
    # year. But the triggers fire on topic words alone, so a pure standards
    # question ("Explain Ind AS 116 lease accounting") matched Account Area
    # Review on `\bleases?\b`. Measured: 8 of 10 standards questions selected a
    # reports-DB playbook.
    #
    # That is not merely wasted tokens. prompt.md's TOOL-DRIVEN INTENTS rule
    # tells the model NOT to call `search_knowledge_base` for ACCOUNT_AREA_REVIEW
    # and AUDIT_RISK_ANALYSIS — so the misroute steered the model away from the
    # rules DB, the only place a standards answer exists, and pushed it toward
    # review_account_area(company=...) with no company to pass.
    #
    # A question that cites a standard or the Act and names neither a company nor
    # a year is a standards question: select no playbook, which is already the
    # intended path (see _select_playbooks — no match means the core RULES and
    # the rules-DB tools).
    _STANDARDS_REF = re.compile(
        r"\bind[\s-]?as[\s-]*\d+|\bifrs[\s-]*\d+|\bias[\s-]*\d+\b"
        r"|\bschedule\s+iii\b|\bcompanies act\b|\bind[\s-]?as\b(?!\w)"
        r"|\bus\s?gaap\b|\bigaap\b"
        # Rules-DB questions that name no standard but are unmistakably about the
        # literature rather than a filing: "is CARO 2020 still in force", "is
        # there an ICAI EAC opinion on deferred tax". Both were pulled into a
        # reports playbook by a topic word (\bcaro\b, \bdeferred tax\b).
        r"|\beac\b|\bicai\b|\bexpert advisory\b"
        r"|\bstill in force\b|\bsuperseded\b|\brepealed\b|\bbeen amended\b",
        re.IGNORECASE,
    )
    # A company question carries a financial year (FY2024, 2023-24, FY2023-24) or
    # names one of the companies in the reports DB.
    #
    # A BARE four-digit number is deliberately NOT a year signal: "CARO 2020" and
    # "Ind AS 115" name the pronouncement, not a reporting period, and counting
    # them defeated the guard on exactly the questions it exists for. Company
    # tokens carry the load for company questions worded without an FY.
    _YEAR_REF = re.compile(
        r"\bfy[\s-]*\d{2,4}|\b(?:19|20)\d{2}[-–/]\d{2,4}\b", re.IGNORECASE
    )

    _company_tokens_cache: set[str] | None = None

    @classmethod
    def _company_tokens(cls) -> set[str]:
        """
        Lowercased company tokens from the reports DB, cached per process.

        Returns an empty set if the DB is unreachable — the guard then falls back
        to the year test alone, which is the conservative direction: it keeps a
        playbook that might be unnecessary rather than dropping one that is needed.
        """
        if cls._company_tokens_cache is not None:
            return cls._company_tokens_cache
        tokens: set[str] = set()
        try:
            conn = tool_names.Database.get_reports_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT DISTINCT company FROM public.documents")
                    for (name,) in cur.fetchall():
                        for part in re.split(r"[_\s\-]+", str(name or "")):
                            # Drop filler that would match almost any sentence.
                            if len(part) >= 3 and part.lower() not in {
                                "the", "and", "of", "state", "india", "limited", "board",
                            }:
                                tokens.add(part.lower())
            finally:
                conn.close()
        except Exception:
            tokens = set()
        cls._company_tokens_cache = tokens
        return tokens

    @classmethod
    def _is_standards_question(cls, text: str) -> bool:
        """True when the query cites a standard/the Act but names no company or year."""
        if not cls._STANDARDS_REF.search(text):
            return False
        if cls._YEAR_REF.search(text):
            return False
        words = set(re.findall(r"[a-z]+", text.lower()))
        return not (words & cls._company_tokens())

    def _matched_playbooks(self, query: str) -> list[str]:
        """Playbook names this query selects, highest-scoring first (max _MAX_PLAYBOOKS)."""
        if not self._playbooks:
            return []
        text = (query or "").lower()
        if self._is_standards_question(text):
            return []
        scored = []
        for name, pattern in self._PLAYBOOK_TRIGGERS.items():
            if name not in self._playbooks:
                continue
            hits = len(re.findall(pattern, text, re.IGNORECASE))
            if hits:
                scored.append((hits, name))
        scored.sort(key=lambda x: -x[0])
        return [name for _, name in scored[: self._MAX_PLAYBOOKS]]

    def _select_tools(self, query: str) -> set[str] | None:
        """
        Tool names to register for this query, or None for "all of them".

        None is returned when routing is disabled, so the kill switch reverts
        prompt and tools together.
        """
        if not getattr(tool_names.Config, "PLAYBOOK_ROUTING", True):
            return None
        # Off by default — see Config.TOOL_GATING for the measured regression.
        if not getattr(tool_names.Config, "TOOL_GATING", False):
            return None
        matched = self._matched_playbooks(query)
        if not matched:
            return set(self._RULES_DB_TOOLS)
        allowed = set(self._SAFETY_TOOLS)
        for name in matched:
            allowed |= self._PLAYBOOK_TOOLS.get(name, set())
        return allowed

    @staticmethod
    def _split_prompt(system_prompt: str) -> tuple[str, dict[str, str], str]:
        """
        Split the prompt into (core, {playbook_name: text}, tail).

        `tail` is the JSON SCHEMA plus the closing BEFORE-YOU-REPLY check and is
        ALWAYS appended last — its position at the very end of the prompt is what
        makes the model honour the JSON output contract, so it must never end up
        in the middle of the assembled prompt.
        """
        marker = "# SKILL PLAYBOOKS"
        idx = system_prompt.find(marker)
        if idx == -1:
            # No playbook section: everything is core, routing becomes a no-op.
            return system_prompt, {}, ""

        core = system_prompt[:idx].rstrip()
        rest = system_prompt[idx:]

        playbooks: dict[str, str] = {}
        tail_parts: list[str] = []
        header, *sections = re.split(r"\n(?=### )", rest)
        for section in sections:
            m = re.match(r"### (.+)", section)
            name = m.group(1).strip() if m else ""
            if name.upper().startswith("JSON SCHEMA"):
                tail_parts.append(section)
            else:
                playbooks[name] = section
        return core, playbooks, ("\n\n" + "\n\n".join(tail_parts) if tail_parts else "")

    def _select_playbooks(self, query: str) -> str:
        """
        The playbook text to include for this query.

        No match returns "" deliberately: GENERAL and rules-DB questions are fully
        served by the core RULES, and injecting an irrelevant procedure is worse
        than injecting none. Every hard tool-call budget (Call ONCE, ONE area per
        call, max 5 clauses, ...) also lives in the tool JSON descriptions, which
        are sent regardless — so a routing miss costs procedural detail, never a
        core guarantee.

        Set PLAYBOOK_ROUTING=false to load every playbook (previous behaviour).
        """
        if not self._playbooks:
            return ""
        if not getattr(tool_names.Config, "PLAYBOOK_ROUTING", True):
            return "\n\n" + "\n".join(self._playbooks.values())

        picked = self._matched_playbooks(query)
        if not picked:
            return ""
        return "\n\n" + "\n".join(self._playbooks[n] for n in picked)

    @staticmethod
    def _filter_tools_section(core: str, allowed: set[str] | None) -> str:
        """
        Drop TOOLS bullets for tools this query will not have registered.

        Without this the prompt advertises all 24 tools while only the gated
        subset is callable — measured: the model read the full list, called
        `get_schedule_note`, got "Tool not found", and retried, turning an
        18,770-token query into 45,909. The advertised list and the registered
        set must agree.

        A bullet may name two tools that are always registered together (e.g.
        get_schedule_note + get_audit_requirements), so a bullet is kept when any
        of its tools is allowed.
        """
        if allowed is None:
            return core
        start = core.find("### TOOLS")
        end = core.find("### RULES", start + 1)
        if start == -1 or end == -1:
            return core

        kept = []
        for line in core[start:end].splitlines():
            named = re.findall(r"`([a-z0-9_]+)\(", line)
            if named and not (set(named) & allowed):
                continue
            kept.append(line)
        return core[:start] + "\n".join(kept) + "\n" + core[end:]

    def build_system_prompt(self, query: str) -> str:
        """Core rules + the playbook(s) matching this query + the JSON-contract tail."""
        header = "\n\n# SKILL PLAYBOOKS\n\nFollow the playbook below; it is the procedure for this query type.\n"
        selected = self._select_playbooks(query)
        core = self._filter_tools_section(self._core_prompt, self._select_tools(query))
        return core + (header + selected if selected else "") + self._prompt_tail

    # ------------------------------------------------------------------
    # yukta wiring helpers (see module docstring for the pattern)
    # ------------------------------------------------------------------

    @staticmethod
    def _make_llm_client(max_tokens: int):
        from yukta.core.Clients.vllm_client import VLLMClient

        # api_key reaches the base client's config and becomes an
        # `Authorization: Bearer ...` header. Omitted entirely when unset so an
        # endpoint without auth is not sent an empty bearer token.
        import os as _os

        kwargs = {}
        _key = _os.environ.get("GENERATION_API_KEY", "").strip()
        if _key:
            kwargs["api_key"] = _key

        return VLLMClient(
            model_name=tool_names.Config.LLM_MODEL_NAME,
            base_url=tool_names.Config.LLM_BASE_URL,
            max_tokens=max_tokens,
            timeout=120,
            **kwargs,
        )

    def _build_fs_agent(self, conn, retrieved_chunks: list[dict], conn_reports=None, callbacks=None,
                        query: str = ""):
        """
        Fresh, stateless yukta Agent scoped to this request.

        Both the system prompt AND the registered tools are selected per query by
        the same playbook router, so the procedure the agent is given and the
        tools it can reach never disagree. See build_system_prompt / _select_tools.
        """
        import types

        from yukta import create_agent, SystemPrompt, ToolProcessor, AgentConfig

        tools_processor = ToolProcessor()
        for tool in self._tool_registry.build_tools(
            conn, retrieved_chunks, conn_reports, allowed=self._select_tools(query)
        ):
            tools_processor.add_tool(tool)

        config = AgentConfig(
            temperature=0.1,
            max_iter=tool_names.Config.MAX_TOOL_ITERATIONS,
            auto_save_chat=False,
            auto_save_chat_history=False,
            verbose=False,
        )

        fs_agent = create_agent(
            name="fs-agent",
            system_prompt=SystemPrompt("fs-agent", self.build_system_prompt(query)),
            tools_processor=tools_processor,
            config=config,
            # See yukta_agents.py's original note: 2000 tokens truncated a
            # full 33-ratio answer mid-JSON; 6000 was sized for that case.
            llm_client=self._make_llm_client(max_tokens=6000),
            callbacks=callbacks,
        )

        # yukta feeds tool output back as json.dumps(result, indent=2), which
        # escapes every newline in our plain-text tool results to a literal \n —
        # roughly 8-15% of tool-result tokens spent on encoding. It is invoked as
        # self._format_tool_result(...), so an instance attribute takes priority
        # over the class method. Guarded so a yukta upgrade that drops or renames
        # the method degrades to the original behaviour instead of crashing.
        if hasattr(fs_agent, "_format_tool_result"):
            fs_agent._format_tool_result = types.MethodType(_raw_tool_result, fs_agent)

        return fs_agent

    # ------------------------------------------------------------------
    # Answer parsing (moved from llm.py, unchanged logic)
    # ------------------------------------------------------------------

    # Tool names that may appear as inline text artifacts in the model's content.
    _TOOL_NAMES = (
        "search_knowledge_base", "get_eac_opinion_by_topic", "compare_standards",
        "check_amendment_status", "validate_answer", "cross_reference_lookup",
    )

    @staticmethod
    def _strip_tool_artifacts(text: str) -> str:
        """Remove inline tool-call artifacts some models write into their content field."""
        names_pattern = "|".join(re.escape(n) for n in Orchestrator._TOOL_NAMES)
        text = re.sub(
            rf'\b(?:{names_pattern})\s*\{{[^{{}}]*(?:\{{[^{{}}]*\}}[^{{}}]*)?\}}',
            "",
            text,
            flags=re.DOTALL,
        )
        text = re.sub(
            r'\{[^{}]*(?:_results?|_result|tool_result)[^{}]*\}',
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        return text

    def _extract_json_from_text(self, text: str) -> dict:
        """Find the final-answer JSON object in the text (moved from llm.py)."""
        def _try_parse(s: str) -> dict:
            start = s.find("{")
            end = s.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(s[start : end + 1])
                except json.JSONDecodeError:
                    pass
            return {}

        candidate = _try_parse(text)
        if candidate and self._ANSWER_KEYS & candidate.keys():
            return candidate

        cleaned = self._strip_tool_artifacts(text)
        candidate = _try_parse(cleaned)
        if candidate and self._ANSWER_KEYS & candidate.keys():
            return candidate

        for i, ch in enumerate(cleaned):
            if ch != "{":
                continue
            for j in range(len(cleaned) - 1, i, -1):
                if cleaned[j] != "}":
                    continue
                try:
                    obj = json.loads(cleaned[i : j + 1])
                    if isinstance(obj, dict) and (self._ANSWER_KEYS & obj.keys()):
                        return obj
                except json.JSONDecodeError:
                    continue

        return candidate or {}

    # Each of these tools serves exactly one intent, so the tool the agent
    # actually called identifies the intent with no guesswork. prompt.md already
    # says "pick the label that matches the TOOL you are about to call" — this is
    # that rule enforced in code.
    #
    # Only tools with an unambiguous intent appear. `get_schedule_note` and
    # `search_company_disclosures` are deliberately absent: several playbooks call
    # them as a follow-up, so they do not identify the question being asked.
    _TOOL_INTENTS = {
        "assess_going_concern": "GOING_CONCERN_ASSESSMENT",
        "run_tie_out_checks": "TIE_OUT_CHECK",
        "compute_ratio_analysis": "RATIO_ANALYSIS",
        "get_ratio_formula": "RATIO_FORMULA_LOOKUP",
        "compute_materiality": "MATERIALITY_ASSESSMENT",
        "get_multi_year_trend": "TREND_ANALYSIS",
        "summarize_annual_report": "ANNUAL_REPORT_SUMMARY",
        "review_account_area": "ACCOUNT_AREA_REVIEW",
        "get_accounting_policy_note": "ACCOUNTING_POLICY_LOOKUP",
        "get_reporting_framework": "COMPLIANCE_FRAMEWORK",
        "check_statement_compliance": "STATEMENT_COMPLIANCE_CHECK",
        "get_eac_opinion_by_topic": "EAC_OPINION",
        "compare_standards": "COMPARISON",
        "check_amendment_status": "AMENDMENT_STATUS",
        "check_caro_clauses": "AUDITOR_REPORT_REVIEW",
        "check_rule_11g": "AUDITOR_REPORT_REVIEW",
        "get_audit_report_highlights": "AUDITOR_REPORT_REVIEW",
    }

    @classmethod
    def _intent_from_tools(cls, tool_calls: list[dict]) -> str | None:
        """The intent implied by the first intent-bearing tool the agent called."""
        for call in tool_calls or []:
            name = call.get("tool") if isinstance(call, dict) else None
            if name in cls._TOOL_INTENTS:
                return cls._TOOL_INTENTS[name]
        return None

    # ------------------------------------------------------------------
    # Citation backfill
    # ------------------------------------------------------------------
    # The `evidences` array is the ONLY thing the UI renders as citations, and
    # the model drops it on exactly the answers that need it most. Measured on
    # the 72-question baseline: every one of the eight rules-DB intents shipped
    # it empty while the reports-DB intents populated it, so standards answers
    # displayed with no sources at all — even when the prose cited [Chunk 2] and
    # a paragraph number in every bullet. prompt.md RULE 19 now demands the
    # array; this rebuilds it in Python when the model ignores the rule, on the
    # same principle as every figure in this system: don't ask the model to be
    # reliable about something that can be computed.
    #
    # Nothing here is invented. The claim text is the model's OWN sentence, and
    # the source is the citable descriptor of the chunk that sentence cited —
    # the same one ToolRegistry._format_chunks printed as [Chunk N].
    _CHUNK_REF = re.compile(r"\[Chunk\s*(\d+)\]", re.IGNORECASE)
    _MAX_BACKFILLED = 10

    @staticmethod
    def _chunk_citation(chunk: dict) -> str:
        """The citable descriptor for one retrieved chunk (mirrors _format_chunks)."""
        parts: list[str] = []
        if chunk.get("standard_number") not in (None, ""):
            parts.append(f"Ind AS {chunk['standard_number']}")
        elif chunk.get("doc_name"):
            parts.append(str(chunk["doc_name"]))
        else:
            parts.append(
                str(chunk.get("table_label") or chunk.get("table_name") or "rules database")
            )
        for key, prefix in (("section_title", ""), ("paragraph_no", "para ")):
            value = chunk.get(key)
            if value not in (None, ""):
                parts.append(f"{prefix}{value}")
        return ", ".join(parts)

    @staticmethod
    def _clean_claim(text: str) -> str:
        """
        One answer line as a citation claim: no list bullet, no bold markers, and
        no gap left where the '[Chunk N]' marker was cut out of the sentence.
        The evidence line is itself rendered inside `**…**`, so stray markers
        here surface as literal asterisks in the UI.
        """
        claim = text.strip().lstrip("-*•> ").replace("**", "").replace("__", "")
        claim = re.sub(r"\s+", " ", claim)
        claim = re.sub(r"\s+([.,;:)])", r"\1", claim)
        claim = re.sub(r"\(\s*\)", "", claim)
        return claim.strip(" :;-").strip()

    # Enough shared vocabulary that the chunk is plausibly what the sentence was
    # drawn from. Tuned to be conservative: a miss costs a neutral source label,
    # a false positive puts a real document name behind a claim it never made.
    _SUPPORT_RATIO = 0.34
    _STOPWORDS = frozenset(
        "that this with from have been they were which when what will shall "
        "such does other than into over under about their there where whether "
        "these those must should would could also used using".split()
    )

    @classmethod
    def _chunk_supports(cls, chunk: dict, claim: str) -> bool:
        """Does this chunk's own text share enough vocabulary with the claim?"""
        def tokens(text: str) -> set[str]:
            words = re.findall(r"[a-z]{4,}", str(text).lower())
            return {w for w in words if w not in cls._STOPWORDS}

        claim_tokens = tokens(claim)
        if len(claim_tokens) < 4:
            return False
        content = tokens(chunk.get("content", ""))
        if not content:
            return False
        return len(claim_tokens & content) / len(claim_tokens) >= cls._SUPPORT_RATIO

    @classmethod
    def _backfill_evidences(
        cls, parsed: dict, retrieved_chunks: list[dict] | None,
        tool_calls: list[dict] | None,
    ) -> list[dict]:
        """
        Rebuild the evidences array from the answer's own citations.

        Returns [] for a genuinely uncited answer — a refusal, an out-of-scope
        guard, or a retrieval miss. Those SHOULD render as having no citations;
        inventing entries for them is the failure mode this whole module exists
        to prevent.
        """
        text = "\n".join(
            str(parsed.get(key, "") or "")
            for key in ("final_answer", "aggregated_summary")
        )
        chunks = retrieved_chunks or []

        out: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for line in text.split("\n"):
            refs = cls._CHUNK_REF.findall(line)
            if not refs:
                continue
            claim = cls._clean_claim(cls._CHUNK_REF.sub("", line))
            if not claim:
                continue
            for ref in refs:
                index = int(ref)
                # 1-indexed, exactly as the model was shown them.
                chunk = chunks[index - 1] if 1 <= index <= len(chunks) else None
                # [Chunk N] is NOT a stable key: several tools emit their own
                # numbered lists, so the N the model wrote may belong to a
                # different list than the one that survived in retrieved_chunks.
                # Observed live: an Ind AS 115 para-35 claim resolved to an EAC
                # opinions document. Naming the wrong source is worse than
                # naming none, so the descriptor is attached only when the
                # chunk's own text corroborates the claim.
                if chunk is not None and cls._chunk_supports(chunk, claim):
                    source = cls._chunk_citation(chunk)
                else:
                    source = (
                        f"cited in the answer as [Chunk {index}] — see the Retrieved "
                        "Knowledge Chunks panel; the source document could not be "
                        "matched to this claim automatically"
                    )
                key = (str(index), claim)
                if key in seen:
                    continue
                seen.add(key)
                out.append({"chunk_id": str(index), "source": source, "claim": claim})
                if len(out) >= cls._MAX_BACKFILLED:
                    return out

        if out:
            return out

        # No inline chunk references. If tools produced the answer, record which
        # ones — provenance the reader can act on, and true by construction.
        # Deliberately NOT a synthesised claim: it says where the answer came
        # from, and nothing about what it said.
        names: list[str] = []
        for call in tool_calls or []:
            name = call.get("tool") if isinstance(call, dict) else None
            if name and name not in names:
                names.append(name)
        return [
            {
                "chunk_id": f"tool:{name}",
                "source": f"{name} (deterministic tool output)",
                "claim": "Figures and findings in this answer were taken from this tool's "
                         "output; the tool's own Source lines identify the underlying rows.",
            }
            for name in names[: cls._MAX_BACKFILLED]
        ]

    def _parse_answer(
        self, raw_text: str, data_gaps: str | None = None,
        tool_calls: list[dict] | None = None,
        retrieved_chunks: list[dict] | None = None,
    ) -> tuple[str, str]:
        """
        Parse the LLM's final response into rendered markdown (moved from
        llm.py). Returns (markdown, intent) — intent is the agent's own
        Step-0 self-classification (see prompt.md), corrected from the tools it
        actually called when it reports the GENERAL fallback.
        """
        parsed = self._extract_json_from_text(raw_text)
        if not parsed:
            return (
                self._strip_tool_artifacts(raw_text).strip() or raw_text,
                self._intent_from_tools(tool_calls) or "GENERAL",
            )

        intent = str(parsed.get("intent", "GENERAL")).strip().upper()
        if intent not in self._VALID_INTENTS:
            intent = "GENERAL"
        # The self-reported label is flaky at the GENERAL boundary — measured, the
        # same going-concern question was labelled GOING_CONCERN_ASSESSMENT on one
        # run and GENERAL on the next. A specific label the model chose is left
        # alone; only the GENERAL fallback is corrected, and only when a tool
        # names the intent outright.
        if intent == "GENERAL":
            intent = self._intent_from_tools(tool_calls) or "GENERAL"

        md_lines = []

        reasoning = parsed.get("reasoning_trace", "")
        if reasoning:
            md_lines.append("<details>")
            md_lines.append("<summary>🧠 <strong>Reasoning Trace (Chain-of-Thought)</strong></summary>\n")
            md_lines.append(f"{reasoning}\n")
            md_lines.append("</details>\n")

        summary = parsed.get("aggregated_summary", "")
        if summary:
            md_lines.append(f"### Summary\n{summary}\n")

        evidences = parsed.get("evidences", [])
        if not evidences:
            evidences = self._backfill_evidences(parsed, retrieved_chunks, tool_calls)
        if evidences:
            md_lines.append("### Evidences & Citations")
            for ev in evidences:
                chunk_id = ev.get("chunk_id", "?")
                source = ev.get("source", "Unknown")
                claim = ev.get("claim", "")
                cross_refs = ev.get("cross_references", [])
                ev_str = f"- **[{chunk_id}] {source}**: {claim}"
                if cross_refs and isinstance(cross_refs, list):
                    refs = ", ".join(str(r) for r in cross_refs)
                    if refs:
                        ev_str += f" *(Cross-refs: {refs})*"
                md_lines.append(ev_str)
            md_lines.append("")

        final_answer = parsed.get("final_answer", "")
        if final_answer:
            md_lines.append(f"### Final Answer\n{final_answer}\n")

        confidence = str(parsed.get("confidence", "")).strip()
        capped, cap_reason = self._cap_confidence(confidence, data_gaps)
        if capped:
            md_lines.append(f"**Confidence**: {capped}")
            if cap_reason:
                md_lines.append(
                    f"> Confidence was reduced from *{confidence}* automatically: {cap_reason}"
                )

        tools_used = parsed.get("tools_used", [])
        if tools_used:
            md_lines.append(f"**Tools used**: {', '.join(tools_used)}")
        elif not tools_used:
            md_lines.append(
                "> **No tool was called for this answer** — it was not verified against the "
                "knowledge base or the annual reports. Treat it as unsourced."
            )

        return "\n".join(md_lines), intent

    # ------------------------------------------------------------------
    # Confidence capping
    # ------------------------------------------------------------------

    _CONFIDENCE_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}

    # A tool saying the document or statement is absent entirely. Nothing built
    # on top of that deserves High confidence.
    _SEVERE_GAP_MARKERS = (
        "no annual report found",
        "no standalone",
        "was not located in this document",
        "no note table found",
        "no relevant chunks found",
        "could not be located",
    )
    # A tool saying part of the data is missing — figures not extracted, an
    # identity that could not be checked, a ratio that could not be computed.
    _PARTIAL_GAP_MARKERS = (
        "not extracted",
        "not available",
        "cannot compute",
        "not assessable",
        "no chunks were retrieved",
        "data quality note",
        "coverage caveat",
    )

    @classmethod
    def detect_data_gaps(cls, tool_calls: list[dict]) -> str | None:
        """
        'severe' | 'partial' | None, from what the TOOLS actually reported.

        Read off tool output rather than the model's prose: the model reliably
        states the gap in its answer but still self-reports High confidence
        (verified on Coal India, whose P&L is absent from 6 of its 7 ingested
        reports, and on a document whose balance sheet was never ingested).
        Confidence is a signal users act on, so it is derived here instead.
        """
        severe = partial = False
        for call in tool_calls or []:
            result = call.get("result") or {}
            if isinstance(result, dict):
                text = str(result.get("result") or result.get("error") or "")
            else:
                text = str(result)
            lowered = text.lower()
            if any(m in lowered for m in cls._SEVERE_GAP_MARKERS):
                severe = True
            elif any(m in lowered for m in cls._PARTIAL_GAP_MARKERS):
                partial = True
        if severe:
            return "severe"
        return "partial" if partial else None

    @classmethod
    def _cap_confidence(cls, reported: str, gap: str | None) -> tuple[str, str | None]:
        """(confidence_to_show, reason_if_reduced). Never raises the model's own value."""
        if not reported:
            return reported, None
        ceiling = {"severe": "Low", "partial": "Medium"}.get(gap or "")
        if not ceiling:
            return reported, None
        current = cls._CONFIDENCE_RANK.get(reported.upper())
        if current is None or current <= cls._CONFIDENCE_RANK[ceiling.upper()]:
            return reported, None
        reason = (
            "a tool reported that the document or statement could not be located, so the "
            "answer rests on incomplete data."
            if gap == "severe" else
            "a tool reported figures it could not extract or checks it could not perform, "
            "so parts of this answer rest on incomplete data."
        )
        return ceiling, reason

    # ------------------------------------------------------------------
    # fs-agent run (single agentic loop)
    # ------------------------------------------------------------------

    def _run_fs_agent(
        self, query: str, conn, retrieved_chunks: list[dict], conn_reports=None,
    ) -> tuple[str, str, list[dict], dict]:
        """Runs fs-agent end to end. Returns (answer_text, intent, tool_calls_log, llm_stats)."""
        from yukta import AgentCallbackHandler

        class _StatsCallback(AgentCallbackHandler):
            def __init__(self):
                self.prompt_tokens = 0
                self.completion_tokens = 0

            def on_llm_end(self, response):
                usage = getattr(response, "usage", None) or {}
                self.prompt_tokens += usage.get("prompt_tokens", 0)
                self.completion_tokens += usage.get("completion_tokens", 0)

        stats_cb = _StatsCallback()
        agent = self._build_fs_agent(
            conn, retrieved_chunks, conn_reports, callbacks=stats_cb, query=query
        )

        _t_start = time.perf_counter()
        result = agent.run(user_message=f"Query: {query}", llm_kwargs={"temperature": 0.1})
        elapsed = round(time.perf_counter() - _t_start, 2)

        if not result.get("success"):
            raise RuntimeError(result.get("error", "fs-agent failed"))

        tool_calls_log = [
            {
                "tool": tc["tool"],
                "args": tc["arguments"],
                "iteration": i,
                "result_preview": str(
                    tc["result"].get("result", tc["result"].get("error", ""))
                )[:300],
            }
            for i, tc in enumerate(result.get("tool_calls", []), start=1)
        ]

        llm_stats = {
            "prompt_tokens": stats_cb.prompt_tokens,
            "completion_tokens": stats_cb.completion_tokens,
            "total_tokens": stats_cb.prompt_tokens + stats_cb.completion_tokens,
            "llm_elapsed_seconds": elapsed,
        }

        raw_text = result.get("response", "")
        # Derived from what the tools reported, not from the model's own claim —
        # see detect_data_gaps.
        data_gaps = self.detect_data_gaps(result.get("tool_calls", []))
        answer_text, intent = self._parse_answer(
            raw_text, data_gaps=data_gaps, tool_calls=result.get("tool_calls", []),
            retrieved_chunks=retrieved_chunks,
        )
        return answer_text, intent, tool_calls_log, llm_stats

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def answer(self, query: str, conn=None, conn_reports=None) -> dict:
        """
        Run the end-to-end RAG pipeline for a single query.

        Parameters
        ----------
        query : str — the user's natural language question.
        conn  : psycopg2 connection, optional (opened internally if None).
        conn_reports : psycopg2 connection, optional (opened internally if
            configured and None; stays None if the reports DB isn't
            configured at all — reports-DB tools are simply not registered).
        """
        api_calls: list[str] = []
        _pipeline_start = time.perf_counter()

        _own_conn = False
        if conn is None:
            try:
                conn = tool_names.Database.get_connection()
                _own_conn = True
            except Exception as e:
                return {"error": "database", "message": str(e)}

        _own_conn_reports = False
        if conn_reports is None:
            try:
                conn_reports = tool_names.Database.get_reports_connection()
                _own_conn_reports = True
            except Exception:
                conn_reports = None

        def _close_owned_conns():
            if _own_conn:
                try:
                    conn.close()
                except Exception:
                    pass
            if _own_conn_reports and conn_reports is not None:
                try:
                    conn_reports.close()
                except Exception:
                    pass

        # Mutated in place by the search_knowledge_base tool during the
        # agentic loop (see ToolRegistry._search_knowledge_base in
        # tools_fs.py) — starts empty since retrieval is now a tool call,
        # not a precomputed Python stage.
        retrieved_chunks: list[dict] = []

        try:
            answer_text, intent, tool_calls_made, llm_stats = self._run_fs_agent(
                query, conn, retrieved_chunks, conn_reports=conn_reports,
            )
            api_calls.append(
                f"fs-agent ({tool_names.Config.LLM_MODEL_NAME}) → "
                f"{tool_names.Config.LLM_BASE_URL}/v1/chat/completions "
                f"[agentic loop, {len(tool_calls_made)} tool call(s)]"
            )
        except Exception as e:
            _close_owned_conns()
            error_str = str(e)
            is_context_overflow = "context length" in error_str.lower() or "input_tokens" in error_str.lower()
            if is_context_overflow:
                message = (
                    "This request required more data than could fit in a single call — the "
                    "underlying tools were called successfully, but their combined output "
                    "exceeded the model's context window. Try narrowing the request, e.g. ask "
                    "about specific schedules (PPE, Provisions, Inventory) separately, or reduce "
                    "the number of years/statements covered in one question."
                )
            else:
                message = (
                    f"The agent's tool-calling run failed for this request ({error_str}). This is "
                    "a technical/infrastructure issue, not a sign that the underlying data is "
                    "unavailable — please retry, or narrow the request."
                )
            return {"error": "llm", "message": message}

        _close_owned_conns()

        return {
            "query": query,
            "intent": intent,
            "answer": answer_text,
            "retrieved_chunks": retrieved_chunks,
            "tool_calls_made": tool_calls_made,
            "num_chunks_retrieved": len(retrieved_chunks),
            "api_calls_summary": api_calls,
            "llm_stats": llm_stats,
            "total_elapsed_seconds": round(time.perf_counter() - _pipeline_start, 2),
        }


# ---------------------------------------------------------------------------
# Self-test (run: python agent.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    orchestrator = Orchestrator()
    test_query = "What does Ind AS 115 say about revenue recognition?"
    print(f"Running end-to-end for: '{test_query}'\n")
    result = orchestrator.answer(test_query)
    if "error" in result:
        print(f"ERROR at stage '{result['error']}': {result['message']}")
    else:
        print(f"Intent: {result['intent']}")
        print(f"Chunks retrieved: {result['num_chunks_retrieved']}")
        print(f"Tool calls made: {len(result.get('tool_calls_made', []))}")
        print(f"\nAnswer:\n{result['answer']}")
