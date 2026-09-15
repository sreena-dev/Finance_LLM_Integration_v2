"""
sar_report_pipeline.py — SAR Report Generation Orchestrator (v3)
=================================================================
Python orchestrator for SAR report generation.
NOT an LLM agent — pure Python coordinating all steps sequentially.

WHAT CHANGED FROM v2:
  - FetchTools and CheckTools imported from sar_prod_v3.tool_sar (real DB queries)
  - _resolve_doc_id() calls FetchTools.resolve_doc_id() — real DB lookup
  - financial_tables: handles table_md key from new HTML→MD conversion

GAP-CLOSURE PHASE 1 (see ../GAP_CLOSURE_LOG.md for the full write-up):
  - Step 7b added: UDIN / SA 706.8 closing-sentence / report-date-sequence
    checks, which previously existed in tool_sar.py but were never wired in.
  - All deterministic observations (pre-flight + coherence + formal) now
    flow through observation.Observation instead of ad-hoc dicts, so every
    observation carries a confidence rating and is subject to the §11
    source-lineage suppression rule before it reaches the writer.
  - `review_status` (complete | provisional | blocked) is now computed and
    both returned to the caller and handed to the writer agent.

PIPELINE FLOW:
  Step 1:  Resolve doc_id from company + FY              (FetchTools.resolve_doc_id)
  Step 2:  Fetch all data in parallel                    (FetchTools + ReferenceTools)
  Step 3:  Deterministic pre-flight checks               (CheckTools.run_preflight_checks)
  Step 4:  Financial metrics                             (ComputeTools.compute_financial_ratios)
  Step 5:  Run 3 Extractor agents in parallel            (build_extractor_main/caro/ifc)
  Step 6:  Merge extracted JSON
  Step 7:  Coherence checks                              (CheckTools.check_opinion_coherence_signals)
  Step 7b: Formal checks                                 (formal_review.run_formal_checks)
  Step 7c: Lineage suppression + review_status + confidence (observation.py + formal_review.py)
  Step 8:  Report Writer agent                           (build_report_writer)
  Step 9:  Return final report + parsed JSON + observations + review_status

TOTAL LLM CALLS: 4 (3 extractors + 1 writer; 3 extractors run in parallel)
WALL-CLOCK TARGET: < 40 seconds on LAN vLLM endpoint

USAGE:
    from sar_prod_v3.pipeline.sar_report_pipeline import SARReportPipeline

    pipeline = SARReportPipeline()
    result = pipeline.run(company="THDC", fy_start=2023, fy_end=2024)
    print(result["report"])          # Final markdown report
    print(result["parsed"])          # Structured JSON from extractors
    print(result["review_status"])   # complete | provisional | blocked
"""

from __future__ import annotations

import json
import logging
import re
import concurrent.futures

logger = logging.getLogger("sar_prod_v3.pipeline.sar_report_pipeline")

_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


def _strip_code_fence(text: str) -> str:
    """Extract JSON from an LLM response, tolerating a fence and/or
    prose around it.

    Extractor prompts ask for raw JSON, but the LLM wraps it in a markdown
    fence often enough that `json.loads()` on the unstripped string reliably
    failed with "Expecting value: line 1 column 1" — the leading backtick
    isn't valid JSON. That failure was silent: `_call_extractor` catches it
    and returns `{}`, so the pipeline still completes, just with that
    extractor's structured data (feeding the coherence checks) quietly empty.

    BUG FIX — the previous version anchored the fence pattern to the WHOLE
    string (`^```...```$`), which only strips a response that is *nothing
    but* one fenced block. Confirmed against a real live run
    (RVNL_2024_2025, CARO extractor) that this genuinely still fails: a
    response with so much as one stray leading/trailing token around the
    fence falls straight through to the original unstripped (invalid) text
    and the extractor silently comes back empty — exactly the failure mode
    the docstring above already describes, just not fully closed by the
    anchored version. Fixed by (1) searching for a fenced block ANYWHERE in
    the text rather than requiring it to be the entire string, and (2)
    falling back to a bracket-depth scan for the first top-level `{...}`
    object when there is no fence at all — covers a model that drops the
    fence but still wraps the JSON in a sentence.
    """
    stripped = text.strip()
    m = _FENCE_RE.search(stripped)
    if m:
        return m.group(1).strip()

    if stripped.startswith("{"):
        return stripped  # already bare JSON — nothing to strip

    start = stripped.find("{")
    if start == -1:
        return stripped  # no JSON object found at all — let json.loads raise its own error
    depth = 0
    for i in range(start, len(stripped)):
        if stripped[i] == "{":
            depth += 1
        elif stripped[i] == "}":
            depth -= 1
            if depth == 0:
                return stripped[start:i + 1]
    return stripped[start:]  # unbalanced braces (truncated response) — best effort


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
        self, merged_json: dict, fin_metrics: dict, preflight_obs: list[dict], applicability: dict
    ) -> list:
        """
        Runs deterministic coherence checks using CheckTools.

        Returns a list of `observation.Observation` objects (Gap-closure
        Phase 1 — previously this returned plain dicts with no confidence
        field and no lineage enforcement; see observation.py). Observation
        IDs are NOT assigned here any more — that now happens once, in
        run(), after formal-check observations are merged in too, so IDs
        stay sequential across the whole package rather than restarting per
        category.

        `applicability` (Gap-closure Phase 2, Gap #4 — see applicability.py)
        gates the CARO- and IFC-derived signals: when CARO/IFC applicability
        is not "applicable" (i.e. "uncertain" or "not_applicable"), the
        corresponding count/flag is zeroed out before
        CheckTools.check_opinion_coherence_signals runs, so CHK-COH-01/02
        simply don't fire on data whose relevance hasn't been confirmed.
        applicability.applicability_observations() already raises the
        AUDIT_POINTER that explains why (see run()) — this just stops a
        second, downstream signal firing on top of an unresolved question.
        """
        from sar_prod_v3.tool_sar import CheckTools
        from sar_prod_v3.observation import from_check_result, from_preflight_dict

        meta = merged_json.get("_meta", {})
        caro_ok = (applicability.get("caro") or {}).get("status") == "applicable"
        ifc_ok = (applicability.get("ifc") or {}).get("status") == "applicable"
        coherence_checks = CheckTools.check_opinion_coherence_signals(
            opinion_type=meta.get("main_opinion_type", ""),
            caro_adverse_count=meta.get("caro_adverse_count", 0) if caro_ok else 0,
            ifc_has_material_weakness=meta.get("ifc_has_material_weakness", False) if ifc_ok else False,
            going_concern_distress_signals=fin_metrics.get("distress_signals", []),
        )

        obs_list = [from_preflight_dict(pf) for pf in preflight_obs]
        obs_list.extend(
            from_check_result(chk) for chk in coherence_checks if not chk.passed
        )
        return obs_list

    # ------------------------------------------------------------------
    # Step 7b: Formal checks (Gap-closure Phase 1, Gap #5)
    # ------------------------------------------------------------------

    def _run_formal_checks(self, merged_json: dict) -> dict:
        """Runs the deterministic UDIN / SA 706.8 / report-date-sequence
        checks (formal_review.run_formal_checks) that previously existed in
        tool_sar.py but were never called from anywhere. See
        GAP_CLOSURE_LOG.md for why this exists as its own pipeline step
        rather than being folded into `_run_coherence_checks`: it is pure
        and DB/LLM-free, so it is unit-tested directly (tests/test_formal_review.py)
        without needing this class (which builds LLM agents) to be
        instantiated at all.
        """
        from sar_prod_v3.formal_review import run_formal_checks

        return run_formal_checks(merged_json)

    # ------------------------------------------------------------------
    # Step 6b: Applicability gates (Gap-closure Phase 2, Gap #4)
    # ------------------------------------------------------------------

    def _resolve_applicability(self, merged_json: dict, data: dict) -> dict:
        """Resolves CARO/IFC/KAM applicability (applicability.py) from the
        merged extractor JSON plus whether the CARO/IFC text was actually
        found by the fetch step. Deliberately reads `data["caro_text"]` /
        `data["ifc_text"]` (the raw ToolResults from Step 2) rather than
        only the extractor's own output — the "was any CARO/IFC text found
        at all" signal is what distinguishes "not applicable" (explicit
        package statement) from "uncertain" (nothing found, no statement
        either), and that distinction only exists at the fetch layer.
        """
        from sar_prod_v3.applicability import resolve_applicability

        caro_result = data["caro_text"]
        ifc_result = data["ifc_text"]
        return resolve_applicability(
            merged_json,
            caro_text_found=caro_result.is_usable(),
            caro_raw_text=caro_result.data if caro_result.is_usable() else "",
            ifc_text_found=ifc_result.is_usable(),
            ifc_raw_text=ifc_result.data if ifc_result.is_usable() else "",
        )

    # ------------------------------------------------------------------
    # Step 7c: Consistency / silence engine (Gap-closure Phase 2, Gap #3)
    # ------------------------------------------------------------------

    def _run_consistency_checks(self, merged_json: dict, fin_metrics: dict, applicability: dict) -> list:
        """Runs the deterministic consistency-matrix / silence rules
        (consistency_engine.py) — a partial implementation covering 7 of the
        source spec §26 table's ~15 rows; see that module's docstring for
        exactly which rows and why the rest are deferred.
        """
        from sar_prod_v3.consistency_engine import run_consistency_checks

        return run_consistency_checks(merged_json, fin_metrics, applicability)

    # ------------------------------------------------------------------
    # Step 7c2: C&AG §143(5) directions engine (Gap-closure Phase 5, Gap #2)
    # ------------------------------------------------------------------

    def _run_cag_directions_checks(self, merged_json: dict) -> list:
        """Runs cag_directions_engine.run_cag_directions_checks — matched by
        the auditor's report date against the live standing-directions
        document's own effective window (see that module's docstring for
        why report date, not entity/FY, is the right key). Returns []
        (no observations, not an error) when the report date is missing/
        unparseable or nothing in REFERENCE_DSN covers it.
        """
        from sar_prod_v3.cag_directions_engine import run_cag_directions_checks
        from sar_prod_v3.tool_sar import ReferenceTools

        report_date = (merged_json.get("formal_checks") or {}).get("report_date")
        return run_cag_directions_checks(merged_json, report_date, tables=ReferenceTools)

    # ------------------------------------------------------------------
    # Step 7d: Pervasiveness + modification-quantification (Gap-closure
    # Phase 3, Gap #8)
    # ------------------------------------------------------------------

    def _run_pervasiveness_checks(self, merged_json: dict, fs_tables: dict) -> list:
        """Runs pervasiveness.run_pervasiveness_checks — see that module's
        docstring for the important caveat that this depends on the
        `modification` block added to EXTRACTOR_MAIN's schema in PROMPT.md,
        which is unvalidated against a live model in this sandbox. Every
        function in pervasiveness.py degrades to "no observation" (not an
        error) when that block is absent, so this is safe to call
        unconditionally regardless of which prompt version produced
        `merged_json`.
        """
        from sar_prod_v3.pervasiveness import run_pervasiveness_checks

        return run_pervasiveness_checks(merged_json, fs_tables)

    # ------------------------------------------------------------------
    # Step 6c / 9b: Prior-year continuity (Gap-closure Phase 3, Gap #6)
    # ------------------------------------------------------------------

    def _fetch_prior_year_result(self, data: dict) -> dict | None:
        """Returns the prior-year trend record if one was found (Step 2's
        `data["prior_year"]`), else None. Was fetched but never consumed
        before this phase — see prior_year_continuity.py's module
        docstring."""
        prior = data.get("prior_year")
        return prior.data if prior is not None and prior.is_usable() else None

    def _save_prior_year_record(
        self, company: str, fy_end: int, merged_json: dict, all_obs_objs: list, review_status: str,
    ) -> None:
        """Best-effort write of this run's trend record for next year's
        continuity check. Never raises — a failure here (e.g. the DB user
        lacks CREATE TABLE privilege) must not fail report generation;
        losing this year's trend record is a next-year inconvenience."""
        from sar_prod_v3.prior_year_continuity import build_prior_year_summary
        from sar_prod_v3.tool_sar import FetchTools

        try:
            record = build_prior_year_summary(merged_json, all_obs_objs, review_status)
            FetchTools.save_sar_result(company, fy_end, record)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not save SAR trend record for %s FY-end %s: %s", company, fy_end, exc)

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
              "report":                str,   # Final markdown report
              "parsed":                dict,  # Merged structured JSON from extractors
              "observations":          list,  # All tagged observations — each dict now
                                                # additionally carries "confidence",
                                                # "quoted_report_text", "evidence_required",
                                                # "caveats", etc. (observation.Observation.to_dict();
                                                # Gap-closure Phase 1 — see GAP_CLOSURE_LOG.md)
              "review_status":         str,   # "complete" | "provisional" | "blocked" (Phase 1)
              "review_status_reasons": list,  # Why, when not "complete"
              "quality_flags":         dict,  # Quality flags per fetch tool call
              "doc_meta":              dict,  # Document metadata (entity_name, fy, cin, etc.)
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

        fs_tables  = data["fs_tables"].data  if data["fs_tables"].is_usable()  else {}
        dir_report = data["dir_report"].data if data["dir_report"].is_usable() else ""

        # Step 6b: Applicability gates — CARO / IFC / KAM (Gap-closure Phase 2,
        # Gap #4). Resolved before coherence/consistency checks run, so both
        # can gate on it. See applicability.py's module docstring for why
        # this can only gate downstream reasoning, not the extraction calls
        # themselves (those already ran in Step 5).
        logger.info("Step 6b: Resolving CARO / IFC / KAM applicability...")
        applicability = self._resolve_applicability(merged_json, data)
        from sar_prod_v3.applicability import applicability_observations
        applicability_obs = applicability_observations(applicability)

        # Step 7: Coherence checks
        logger.info("Step 7: Running coherence checks...")
        coherence_obs = self._run_coherence_checks(
            merged_json, fin_metrics, preflight["observations"], applicability
        )

        # Step 7b: Formal checks — UDIN, SA 706.8 closing sentence, report-date
        # sequencing (Gap-closure Phase 1, Gap #5). These three checks already
        # existed in tool_sar.py but were never called anywhere before this.
        logger.info("Step 7b: Running formal checks (UDIN / EoM closing sentence / report-date sequence)...")
        formal_result = self._run_formal_checks(merged_json)

        # Step 7c: Consistency / silence engine (Gap-closure Phase 2, Gap #3) —
        # a partial implementation; see consistency_engine.py's module
        # docstring for exactly which of the source spec §26 rows are covered.
        logger.info("Step 7c: Running consistency/silence checks...")
        consistency_obs = self._run_consistency_checks(merged_json, fin_metrics, applicability)

        # Step 7c2: C&AG §143(5) directions engine (Gap-closure Phase 5, Gap #2)
        logger.info("Step 7c2: Running C&AG directions checks...")
        cag_directions_obs = self._run_cag_directions_checks(merged_json)

        # Step 7d: Pervasiveness + modification quantification (Gap-closure
        # Phase 3, Gap #8). See pervasiveness.py — depends on an
        # EXTRACTOR_MAIN schema addition unvalidated against a live model;
        # degrades to no observations when that data is absent.
        logger.info("Step 7d: Running pervasiveness / modification-quantification checks...")
        pervasiveness_obs = self._run_pervasiveness_checks(merged_json, fs_tables)

        # Step 7e: Prior-year continuity (Gap-closure Phase 3, Gap #6).
        # prior_result is None on this company's first-ever run, or if the
        # trend-record write from a previous run failed — both handled as
        # "nothing to compare against" by every function below.
        logger.info("Step 7e: Running prior-year continuity checks...")
        from sar_prod_v3.prior_year_continuity import check_opinion_trend, elevate_recurring_observations
        prior_result = self._fetch_prior_year_result(data)
        opinion_trend_obs = check_opinion_trend(merged_json, prior_result)
        prior_year_obs = [opinion_trend_obs] if opinion_trend_obs is not None else []

        # Merge every deterministic observation into one list, then apply
        # the cross-cutting rules from observation.py / prior_year_continuity.py /
        # public_sector_lens.py / evidence_catalogue.py, in order:
        #   1. lineage suppression (§11)        — untraceable FINDING/RISK_FLAG -> AUDIT_POINTER
        #   2. prior-year recurrence elevation   — repeated check_id -> risk +1 level
        #   3. public-sector-lens elevation (§33)— sensitive category -> risk +1 level, lens attached
        #   4. evidence-catalogue enrichment (§42) — fills evidence_required where empty
        #   5. review_status                    (§47) — complete | provisional | blocked
        #   6. confidence downgrade             (§29.1/§38) — capped at Medium when provisional
        # before assigning final sequential observation IDs.
        from sar_prod_v3.observation import (
            suppress_untraceable, apply_confidence_downgrade, assign_observation_ids, stamp_entity_context,
        )
        from sar_prod_v3.formal_review import compute_review_status
        from sar_prod_v3.public_sector_lens import apply_public_sector_lens
        from sar_prod_v3.evidence_catalogue import enrich_evidence_required
        from sar_prod_v3.output_classification import apply_output_classification

        all_obs_objs = suppress_untraceable(
            coherence_obs + formal_result["observations"] + applicability_obs
            + consistency_obs + cag_directions_obs + pervasiveness_obs + prior_year_obs
        )
        all_obs_objs = elevate_recurring_observations(all_obs_objs, prior_result)
        all_obs_objs = apply_public_sector_lens(all_obs_objs)
        all_obs_objs = enrich_evidence_required(all_obs_objs)

        review_status, review_status_reasons = compute_review_status(
            main_text_usable=data["main_text"].is_usable(),
            fs_tables=fs_tables,
            formal_summary=formal_result["summary"],
        )
        all_obs_objs = apply_confidence_downgrade(all_obs_objs, review_status)

        # Gap-closure Phase 4 (output-spec §18): classification (consistency_type /
        # sa_framework / source / recommended_audit_action / candidate_143_6) must
        # run AFTER every risk-elevation pass above so candidate_143_6's
        # risk_rating check reflects the final rating, not a pre-elevation one.
        all_obs_objs = apply_output_classification(all_obs_objs)
        all_obs_objs = stamp_entity_context(all_obs_objs, entity=company, financial_year=fy_label)
        all_obs_objs = assign_observation_ids(all_obs_objs)
        all_observations = [o.to_dict() for o in all_obs_objs]

        # Best-effort: persist this run's trend record for next year's
        # continuity check (Gap #6). Never blocks/fails report generation.
        self._save_prior_year_record(company, fy_end, merged_json, all_obs_objs, review_status)

        # Gap-closure Phase 4/5 (output-spec §2/§3/§4): all three formats —
        # built by templating over all_obs_objs directly, not by a second LLM
        # call, so they cannot disagree with each other (§17 "Single Source
        # of Truth"). Format 3 (detailed_report) embeds the writer's own
        # narrative for its two genuinely-prose sections only — see
        # output_formats.py's module docstring.
        from sar_prod_v3.output_formats import (
            PackageContext, render_detailed_report, render_display_response, render_executive_summary,
        )

        package_ctx = PackageContext(
            entity=company, financial_year=fy_label, scope=scope,
            opinion_type=(merged_json.get("opinion") or {}).get("type", "unclear"),
            review_status=review_status, review_status_reasons=review_status_reasons,
        )
        display_response = render_display_response(package_ctx, all_obs_objs)
        executive_summary = render_executive_summary(package_ctx, all_obs_objs)

        # Step 8: Report Writer agent
        logger.info("Step 8: Running report writer agent...")

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
            formal_checks_summary=formal_result["summary"],
            review_status=review_status,
            review_status_reasons=review_status_reasons,
            applicability=applicability,
        )

        report = self._call_writer(user_msg)

        # Format 3 — built last: it embeds `report` (the writer's narrative,
        # just produced above) for its two prose-only sections, alongside
        # the same deterministic sections Formats 1/2 already used.
        quality_flags_dict = {k: v.quality_flag for k, v in data.items() if hasattr(v, "quality_flag")}
        detailed_report = render_detailed_report(
            package_ctx, all_obs_objs,
            merged_json=merged_json, applicability=applicability,
            quality_flags=quality_flags_dict, doc_meta=doc_meta,
            existing_report_md=report,
        )

        logger.info("SAR pipeline v3 complete: %s %s (review_status=%s)", company, fy_label, review_status)

        return {
            "report": report,
            "display_response": display_response,
            "executive_summary": executive_summary,
            "detailed_report": detailed_report,
            "parsed": merged_json,
            "observations": all_observations,
            "review_status": review_status,
            "review_status_reasons": review_status_reasons,
            "applicability": applicability,
            "quality_flags": quality_flags_dict,
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
