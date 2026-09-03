"""
sar_report_pipeline.py — SAR Report Generation Orchestrator (v3)
=================================================================
Python orchestrator for SAR report generation.
NOT an LLM agent — pure Python coordinating all steps sequentially.

WHAT CHANGED FROM v2:
  - FetchTools and CheckTools imported from sar_prod_v3.tool_sar (real DB queries)
  - _resolve_doc_id() calls FetchTools.resolve_doc_id() — real DB lookup
  - financial_tables: handles table_md key from new HTML→MD conversion

PIPELINE FLOW:
  Step 1: Resolve doc_id from company + FY              (FetchTools.resolve_doc_id)
  Step 2: Fetch all data in parallel                    (FetchTools + ReferenceTools)
  Step 3: Deterministic pre-flight checks               (CheckTools.run_preflight_checks)
  Step 4: Financial metrics                             (ComputeTools.compute_financial_ratios)
  Step 5: Run 3 Extractor agents in parallel            (build_extractor_main/caro/ifc)
  Step 6: Merge extracted JSON
  Step 7: Coherence checks                              (CheckTools.check_opinion_coherence_signals)
  Step 8: Report Writer agent                           (build_report_writer)
  Step 9: Return final report + parsed JSON

TOTAL LLM CALLS: 4 (3 extractors + 1 writer; 3 extractors run in parallel)
WALL-CLOCK TARGET: < 40 seconds on LAN vLLM endpoint

USAGE:
    from sar_prod_v3.pipeline.sar_report_pipeline import SARReportPipeline

    pipeline = SARReportPipeline()
    result = pipeline.run(company="THDC", fy_start=2023, fy_end=2024)
    print(result["report"])   # Final markdown report
    print(result["parsed"])   # Structured JSON from extractors
"""

from __future__ import annotations

import json
import logging
import re
import concurrent.futures

logger = logging.getLogger("sar_prod_v3.pipeline.sar_report_pipeline")

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)


def _strip_code_fence(text: str) -> str:
    """Strip a wrapping ```json ... ``` / ``` ... ``` fence, if present.

    Extractor prompts ask for raw JSON, but the LLM wraps it in a markdown
    fence often enough that `json.loads()` on the unstripped string reliably
    failed with "Expecting value: line 1 column 1" — the leading backtick
    isn't valid JSON. That failure was silent: `_call_extractor` catches it
    and returns `{}`, so the pipeline still completes, just with that
    extractor's structured data (feeding the coherence checks) quietly empty.
    Confirmed against real failing responses (SAR CARO/IFC extractor output)
    before applying — both parse cleanly once the fence is removed.
    """
    stripped = text.strip()
    m = _FENCE_RE.match(stripped)
    return m.group(1).strip() if m else stripped


class SARReportPipeline:
    """
    Production SAR Report Generation Pipeline (v3).

    Usage:
        pipeline = SARReportPipeline()
        result = pipeline.run(company="ONGC", fy_start=2023, fy_end=2024)
        print(result["report"])   # Final markdown report
        print(result["parsed"])   # Structured JSON from extractors

    Optional: inject a custom LLM factory for testing:
        pipeline = SARReportPipeline(llm_factory=my_mock_llm_factory)
    """

    def __init__(self, llm_factory=None):
        """
        Args:
            llm_factory: Optional callable(temperature, max_tokens) → LLM client.
                         If None, agents use the default _build_llm_client() from agent.py.
        """
        self._llm_factory = llm_factory
        self._setup_agents()

    def _setup_agents(self):
        """Initialise all 4 agents once at startup (not per request)."""
        from sar_prod_v3.agent import (
            build_extractor_main,
            build_extractor_caro,
            build_extractor_ifc,
            build_report_writer,
        )

        if self._llm_factory:
            self.extractor_main = build_extractor_main(self._llm_factory(temperature=0.0, max_tokens=3000))
            self.extractor_caro = build_extractor_caro(self._llm_factory(temperature=0.0, max_tokens=6000))
            self.extractor_ifc  = build_extractor_ifc(self._llm_factory(temperature=0.0, max_tokens=2000))
            self.report_writer  = build_report_writer(self._llm_factory(temperature=0.1, max_tokens=16000))
        else:
            # Agents auto-build their own LLM clients from env vars
            self.extractor_main = build_extractor_main()
            self.extractor_caro = build_extractor_caro()
            self.extractor_ifc  = build_extractor_ifc()
            self.report_writer  = build_report_writer()

    # ------------------------------------------------------------------
    # Step 1: Resolve doc_id
    # ------------------------------------------------------------------

    def _resolve_doc_id(self, company: str, fy_start: int, fy_end: int) -> str:
        """
        Resolves doc_id from company + FY using FetchTools.resolve_doc_id().
        Raises ValueError if no document is found.
        """
        from sar_prod_v3.tool_sar import FetchTools

        doc_id = FetchTools.resolve_doc_id(company, fy_start, fy_end)
        if not doc_id:
            raise ValueError(
                f"No document found for company='{company}', "
                f"fy_start={fy_start}, fy_end={fy_end}. "
                f"Check that the document has been ingested into the documents table."
            )
        return doc_id

    # ------------------------------------------------------------------
    # Step 2: Fetch all data in parallel
    # ------------------------------------------------------------------

    def _fetch_all_data(self, doc_id: str, company: str, fy_end: int, scope: str) -> dict:
        """
        Fires all DB tool calls simultaneously using ThreadPoolExecutor.
        Returns a dict with all fetched ToolResult objects.
        """
        from sar_prod_v3.tool_sar import FetchTools, ReferenceTools

        with concurrent.futures.ThreadPoolExecutor(max_workers=9) as executor:
            futures = {
                "main_text":  executor.submit(FetchTools.fetch_main_sar_text, doc_id, scope),
                "caro_text":  executor.submit(FetchTools.fetch_caro_text, doc_id),
                "ifc_text":   executor.submit(FetchTools.fetch_ifc_text, doc_id),
                "fs_tables":  executor.submit(FetchTools.fetch_financial_tables, doc_id),
                "appendixes": executor.submit(FetchTools.fetch_appendix_tables, doc_id),
                "dir_report": executor.submit(FetchTools.fetch_directors_report, doc_id),
                "prior_year": executor.submit(FetchTools.fetch_prior_year_result, company, fy_end),
                "sa_ref":     executor.submit(ReferenceTools.get_sar_report_sa_context),
                "caro_ref":   executor.submit(ReferenceTools.get_sar_report_caro_context),
            }
            return {key: future.result() for key, future in futures.items()}

    # ------------------------------------------------------------------
    # Step 3 + 4: Deterministic checks and financial metrics
    # ------------------------------------------------------------------

    def _run_deterministic_checks(self, data: dict) -> dict:
        """Runs pre-flight checks and computes financial metrics. No LLM."""
        from sar_prod_v3.tool_sar import CheckTools, ComputeTools

        main_text = data["main_text"].data if data["main_text"].is_usable() else ""
        caro_text = data["caro_text"].data if data["caro_text"].is_usable() else ""
        ifc_text  = data["ifc_text"].data  if data["ifc_text"].is_usable()  else ""

        preflight = CheckTools.run_preflight_checks(main_text, caro_text, ifc_text)

        fs_tables = data["fs_tables"].data if data["fs_tables"].is_usable() else {}
        bs_md = (fs_tables.get("balance_sheet") or {}).get("table_md", "")
        pl_md = (fs_tables.get("profit_loss")   or {}).get("table_md", "")
        cf_md = (fs_tables.get("cash_flow")     or {}).get("table_md", "")
        fin_metrics = ComputeTools.compute_financial_ratios(bs_md, pl_md, cf_md)

        return {"preflight": preflight, "fin_metrics": fin_metrics}

    # ------------------------------------------------------------------
    # Step 5: Run 3 Extractor agents in parallel
    # ------------------------------------------------------------------

    def _run_extractors_parallel(self, data: dict, preflight_summary: str) -> dict:
        """
        Runs the 3 Extractor agents simultaneously.
        Each receives only its relevant text — context windows stay small.
        """
        main_text = data["main_text"].to_prompt_block()
        caro_text = data["caro_text"].to_prompt_block()
        ifc_text  = data["ifc_text"].to_prompt_block()

        # Append CARO appendix tables as evidence
        appendixes = data["appendixes"].data if data["appendixes"].is_usable() else {}
        if appendixes.get("appendix_1"):
            caro_text += f"\n\n--- APPENDIX 1 (Title Deeds) ---\n{appendixes['appendix_1']}"
        if appendixes.get("appendix_2"):
            caro_text += f"\n\n--- APPENDIX 2 (Disputed Dues) ---\n{appendixes['appendix_2']}"

        prefix = (
            f"PRE-FLIGHT NOTE: {preflight_summary}\n\n"
            "Extract the JSON from the following text:\n\n"
        )

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            f_main = executor.submit(self._call_extractor, self.extractor_main, prefix + main_text)
            f_caro = executor.submit(self._call_extractor, self.extractor_caro, prefix + caro_text)
            f_ifc  = executor.submit(self._call_extractor, self.extractor_ifc,  prefix + ifc_text)
            p_main = f_main.result()
            p_caro = f_caro.result()
            p_ifc  = f_ifc.result()

        return {"SAR_MAIN": p_main, "CARO_2020": p_caro, "IFC_REPORT": p_ifc}

    def _call_extractor(self, agent, text: str) -> dict:
        """Safe wrapper for extractor agent calls. Returns empty dict on failure."""
        try:
            result = agent(text) if callable(agent) else agent.run(text)
            if isinstance(result, str):
                result = json.loads(_strip_code_fence(result))
            # Handle yukta agent run() return format
            if isinstance(result, dict) and "response" in result:
                raw = result["response"]
                if isinstance(raw, str):
                    result = json.loads(_strip_code_fence(raw))
                elif isinstance(raw, dict):
                    result = raw
            return result or {}
        except Exception as exc:
            logger.error("Extractor agent failed: %s", exc)
            return {}

    # ------------------------------------------------------------------
    # Step 6: Merge extracted JSON
    # ------------------------------------------------------------------

    def _merge_parsed(self, parsed: dict) -> dict:
        """
        Merges 3 extractor outputs into one package.
        Surfaces CARO adverse count and IFC weakness flag for coherence checking.
        """
        caro = parsed.get("CARO_2020", {})
        ifc  = parsed.get("IFC_REPORT", {})
        main = parsed.get("SAR_MAIN", {})

        return {
            **main,
            "CARO_2020": caro,
            "IFC_REPORT": ifc,
            "_meta": {
                "caro_adverse_count": caro.get("total_adverse_count", 0),
                "caro_adverse_clauses": caro.get("adverse_clause_nos", []),
                "ifc_has_material_weakness": bool(
                    ifc.get("material_weaknesses", {}).get("present")
                ),
                "ifc_opinion_type": ifc.get("ifc_opinion", {}).get("type", "unclear"),
                "main_opinion_type": main.get("opinion", {}).get("type", "unclear"),
            },
        }

    # ------------------------------------------------------------------
    # Step 7: Coherence checks
    # ------------------------------------------------------------------

    def _run_coherence_checks(
        self, merged_json: dict, fin_metrics: dict, preflight_obs: list[dict]
    ) -> list[dict]:
        """
        Runs deterministic coherence checks using CheckTools.
        Returns the combined observation list (preflight + coherence).
        """
        from sar_prod_v3.tool_sar import CheckTools

        meta = merged_json.get("_meta", {})
        coherence_checks = CheckTools.check_opinion_coherence_signals(
            opinion_type=meta.get("main_opinion_type", ""),
            caro_adverse_count=meta.get("caro_adverse_count", 0),
            ifc_has_material_weakness=meta.get("ifc_has_material_weakness", False),
            going_concern_distress_signals=fin_metrics.get("distress_signals", []),
        )

        obs_list = list(preflight_obs)
        for chk in coherence_checks:
            if not chk.passed:
                obs_list.append({
                    "check_id": chk.check_id,
                    "tag": chk.tag,
                    "component": chk.component,
                    "observation": chk.observation,
                    "risk_rating": chk.risk_rating,
                    "evidence": chk.evidence,
                })

        for i, obs in enumerate(obs_list, 1):
            obs.setdefault("obs_id", f"SAR-{i:02d}")

        return obs_list

    # ------------------------------------------------------------------
    # Public API: run()
    # ------------------------------------------------------------------

    def run(
        self,
        company: str,
        fy_start: int,
        fy_end: int,
        doc_id: str | None = None,
        scope: str = "standalone",
    ) -> dict:
        """
        Runs the full SAR Report Generation pipeline.

        Args:
            company:  Company name (must match entity_name in documents table)
            fy_start: Financial year start (e.g. 2023)
            fy_end:   Financial year end   (e.g. 2024)
            doc_id:   Optional explicit doc_id. If None, resolved from company + FY.
            scope:    "standalone" | "consolidated"

        Returns:
            {
              "report":        str,   # Final markdown report
              "parsed":        dict,  # Merged structured JSON from extractors
              "observations":  list,  # All tagged observations
              "quality_flags": dict,  # Quality flags per fetch tool call
              "doc_meta":      dict,  # Document metadata (entity_name, fy, cin, etc.)
            }

        Raises:
            ValueError: if document not found for company + FY
            EnvironmentError: if FINANCE_DSN is not set
        """
        from sar_prod_v3.agent import build_writer_user_message
        from sar_prod_v3.tool_sar import FetchTools

        fy_label = f"FY {fy_start}-{str(fy_end)[-2:]}"
        logger.info("Starting SAR pipeline v3: %s %s scope=%s", company, fy_label, scope)

        # Step 1: Resolve doc_id
        if not doc_id:
            doc_id = self._resolve_doc_id(company, fy_start, fy_end)
        doc_meta = FetchTools.get_document_meta(doc_id)
        logger.info("Resolved doc_id=%s for %s %s", doc_id, company, fy_label)

        # Step 2: Fetch all data in parallel
        logger.info("Step 2: Fetching all data in parallel...")
        data = self._fetch_all_data(doc_id, company, fy_end, scope)

        # Step 3 + 4: Deterministic checks + financial metrics
        logger.info("Step 3: Running deterministic checks and financial metrics...")
        checks = self._run_deterministic_checks(data)
        preflight = checks["preflight"]
        fin_metrics = checks["fin_metrics"]

        # Step 5: Run 3 extractor agents in parallel
        logger.info("Step 5: Running 3 extractor agents in parallel...")
        parsed_parts = self._run_extractors_parallel(data, preflight["preflight_summary"])

        # Step 6: Merge extracted JSON
        logger.info("Step 6: Merging extractor outputs...")
        merged_json = self._merge_parsed(parsed_parts)

        # Step 7: Coherence checks
        logger.info("Step 7: Running coherence checks...")
        all_observations = self._run_coherence_checks(
            merged_json, fin_metrics, preflight["observations"]
        )

        # Step 8: Report Writer agent
        logger.info("Step 8: Running report writer agent...")
        fs_tables  = data["fs_tables"].data  if data["fs_tables"].is_usable()  else {}
        dir_report = data["dir_report"].data if data["dir_report"].is_usable() else ""

        # sa_ref / caro_ref may be ToolResult or plain strings
        sa_ref   = data["sa_ref"]   if isinstance(data["sa_ref"], str)   else ""
        caro_ref = data["caro_ref"] if isinstance(data["caro_ref"], str) else ""

        user_msg = build_writer_user_message(
            merged_json=merged_json,
            coherence_observations=all_observations,
            sa_reference=sa_ref,
            caro_reference=caro_ref,
            financial_tables=fs_tables,
            directors_report=dir_report,
            preflight_summary=preflight["preflight_summary"],
            company=company,
            fy_label=fy_label,
            scope=scope,
        )

        report = self._call_writer(user_msg)
        logger.info("SAR pipeline v3 complete: %s %s", company, fy_label)

        return {
            "report": report,
            "parsed": merged_json,
            "observations": all_observations,
            "quality_flags": {
                k: v.quality_flag for k, v in data.items()
                if hasattr(v, "quality_flag")
            },
            "doc_meta": doc_meta,
        }

    def _call_writer(self, user_message: str) -> str:
        """Safe wrapper for the Report Writer agent."""
        try:
            result = self.report_writer(user_message) if callable(self.report_writer) \
                     else self.report_writer.run(user_message)
            # Handle yukta agent run() return format
            if isinstance(result, dict):
                result = result.get("response", "") or str(result)
            return result or ""
        except Exception as exc:
            logger.error("Report writer failed: %s", exc)
            return f"# SAR Report Generation Failed\n\nError: {exc}"
