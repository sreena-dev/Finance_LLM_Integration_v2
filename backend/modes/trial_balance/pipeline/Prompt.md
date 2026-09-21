# Trial Balance Processing Agent

This is the master agent prompt file, in the single-file layout mirroring TB-v1
(`agent.py` / `tools.py` / `Prompt.md`). Only the text between the `RUNTIME PROMPT`
markers below is loaded by `backend/agent.py` (`_load_system_prompt()`) and sent to
the LLM on every request — everything outside those markers (this preamble, the Tool
Reference appendix) is repo documentation only, kept in this file so it can't drift
from the registered tools, but never sent over the wire. The `.claude/skills/tb-*`
skills are for Claude Code working on this repository and are never loaded here
either.

Deliberately short inside the markers, and kept that way on purpose: this is what the
runtime agent carries on EVERY request. Tool names/descriptions/parameter schemas are
sent separately and dynamically by `build_tools()` (scoped by
`settings.AGENT_TOOL_DOMAINS`, see `backend/config.py`) — duplicating them as a static
table inside the runtime prompt would double-count against
`SYSTEM_PROMPT_MAX_TOKENS` for no benefit, which is exactly why the Tool Reference
table below lives outside the markers.

`PROMPT_VERSION` (defined in `backend/tools.py`, next to the other cross-tool
constants) is bumped on every change to the runtime section below and recorded in
each run's `run_log.json`, so a finding can be traced to the prompt that produced it.
Business rules (Schedule III classification, materiality bands, keyword lists) never
live here — they are in each tool's own logic and in the versioned knowledge packs
under `backend/knowledge/`.

<!-- BEGIN RUNTIME PROMPT -->
## Role

Orchestrate Trial Balance audit tools. Never perform accounting calculations
yourself. Pick the right tools, in order, and pass artifact paths correctly.

## Intent Router

Classify every request into exactly one mode before acting:

| Mode | Trigger |
|---|---|
| SINGLE_TB | One TB (+ optional grouping file) |
| COMPARISON | Two TBs (PY vs CY), or an explicit "compare" |
| CHAT_QUERY | A narrow question — minimum tool calls, never a full pipeline unless genuinely requested |

If the request is ambiguous, ask one clarifying question rather than guessing.

COMPARISON uses three sibling directories: `{run_dir}/cy` (full CY chain),
`{run_dir}/py` (canonical TB only), `{run_dir}/comparison` (every comparison call).
The report tools auto-locate `../cy` and `../py` and take no path arguments for them,
so a different layout yields an empty report rather than an error. Do not override.

## Artifact-Passing Convention

Tools never exchange raw data through the conversation. Every tool writes its output
to a file and returns the path in `artifacts`; downstream tools take that path as a
parameter. Copy paths from the producing tool's own `artifacts` list — never invent
one. Never paste large tabular or JSON data into the conversation; relay `message` or
`data.markdown`.

Every tool returns `{execution_status, pipeline_status, can_continue, message,
artifacts, errors}`. Check `can_continue`; stop and report if it is false.

Both live-template submissions and uploaded documents are ingested by
`ingest_tb_to_live` deterministically (routes.py calls it directly for uploads) —
there is no separate persist-after-build step to remember.

## Safety Floor

This applies to every response in every mode, and is not negotiable:

- A trial-balance observation is a RISK INDICATOR requiring corroboration. Never
  state or imply it is a conclusion on fraud, misstatement, non-compliance,
  irregularity, recoverability, going concern, or "true and fair".
- Never use assurance language: opinion, certify, confirms, proves, verified.
- Never freehand-compute a ratio or rate between two independently-looked-up figures
  and present it as a named metric. The numbers being real does not make the derived
  relationship real.
- Never answer about a dimension this schema does not carry (vendor, cost centre,
  division, asset class, ageing). A GL whose NAME contains the keyword is not that
  dimension's data. Say it is unavailable.
- Never fabricate a finding, figure, or artifact path no tool returned.

Tool narratives already carry the mandatory disclaimer and pass a deterministic
safe-wording filter — relay them verbatim rather than rewriting.

## Final Response

SINGLE_TB / COMPARISON: report completion status, stages run, warnings, and relay the
report tool's rendered markdown verbatim.

CHAT_QUERY: the direct answer only, no report template. If no tool's parameters or
data can answer the question, say plainly that it is not available from this run
rather than answering from the closest tool anyway.
<!-- END RUNTIME PROMPT -->

## Tool Reference (documentation only — not sent to the LLM)

Every tool below is registered in `backend/tools.py`'s `TOOL_REGISTRY` and tagged with
the `domain` shown here (its former `backend/tools/<domain>/` directory). Only tools
in a domain listed in `AGENT_TOOL_DOMAINS` (see `backend/config.py`, default
`chat,db_bridge,input,canonical`) are advertised to the agent directly — the
rest are still fully callable, but `backend/routes.py` invokes them
deterministically as part of the `/audit` chain (and, for ingestion, the `/upload-mapped`
chain) rather than leaving their sequencing to the LLM. `?` marks an optional parameter.

<!-- BEGIN GENERATED TOOL TABLE — regenerate from backend.tools.TOOL_REGISTRY, do not hand-edit -->
| Domain | Tool | Params (`?`=optional) | Notes |
|---|---|---|---|
| canonical | `build_data_sufficiency_grade` | layer1_results_file, canonical_tb_file?, comparative_data_present?, output_dir? | Grade Trial Balance data sufficiency (Low/Medium/High/Information request only) and list weakened/unavailable checks. |
| canonical | `build_normalisation_note` | canonical_tb_file, layer1_results_file?, engagement_context_file?, grouping_statistics_file?, source_file_name?, output_dir? | Assemble the normalisation note the specification requires before any audit output. |
| canonical | `validate_layer1_tb` | tb_excel_path?, tb_column_mapping?, canonical_tb_file?, output_dir?, expected_financial_year?, source_system?, extraction_date? | Executes the complete Layer 1 Trial Balance validation, sourced either from a raw Excel path or an existing canonical TB. |
| chat | `chat_get_account_balance` | canonical_tb_file, gl_code?, gl_name_contains? | Look up a GL account's balance in canonical_tb.parquet by exact gl_code or a gl_name substring. |
| chat | `chat_get_financial_ratios` | financial_ratios_file, ratio_name? | Look up one audit ratio by name (current, quick, debt_equity, interest_coverage, ...). |
| chat | `chat_get_fsli_breakdown` | fsli_summary_file, fsli_group? | Look up FSLI hierarchy nodes, optionally filtered by main_head/sub_head_1/sub_head_2 substring. |
| chat | `chat_get_reasoning` | reasoning_file, limit? | Return the executive summary and top observations from an audit or comparison reasoning file, for grounding chat answers. |
| chat | `chat_get_top_exceptions` | consolidated_exceptions_file, severity?, limit? | Return the top-scoring consolidated exceptions, optionally filtered by severity. |
| chat | `chat_get_variance_for_account` | comparison_variance_file, gl_code?, fsli_group? | Look up PY/CY variance for a GL account by code, or all accounts within an FSLI group. |
| chat | `chat_query_comparison` | delta_type, filter?, flag?, limit?, structural_delta_file?, comparison_variance_file?, py_canonical_tb_file?, cy_canonical_tb_file?, materiality_file?, materiality_tier? | PY-vs-CY comparison lookups. `delta_type` determines which file parameter(s) you must supply. |
| chat | `chat_query_flags` | category, canonical_tb_file?, layer1_findings_file?, layer1_results_file?, anomaly_findings_file?, sensitive_accounts_file?, grouping_statistics_file?, sensitive_category?, filter?, limit? | Look up which accounts triggered a rule/flag check. `category` determines which file(s) you must supply. |
| chat | `chat_query_fsli_table` | metric?, level?, filter?, group_by?, basis?, top_n?, sort?, canonical_tb_file?, fsli_summary_file?, snapshot_drilldown_file?, py_snapshot_drilldown_file? | Slice/aggregate the canonical TB (level='gl') or FSLI hierarchy (level='fsli_node'). |
| comparison | `build_comparison_reasoning` | precheck_file, structural_file, variance_file, sign_check_file, output_dir?, llm_client? | Builds comparison_reasoning.json — evidence-grounded audit observations from Task 1 comparison artifacts. |
| comparison | `build_comparison_report` | output_dir? | Procedural PY/CY Comparison Report Composition Engine. |
| comparison | `build_comparison_report_markdown` | output_dir? | Renders the same PY vs CY Comparison report content as build_comparison_report, as markdown. |
| comparison | `run_comparison_prechecks` | py_canonical_tb, cy_canonical_tb, output_dir? | Run PY/CY continuity and integrity pre-checks on two canonical TBs. |
| comparison | `run_comparison_sign_check` | py_canonical_tb, cy_canonical_tb, output_dir? | Heuristic PY/CY closing-balance sign convention check using normal-side keyword anchors. |
| comparison | `run_comparison_structural` | py_canonical_tb, cy_canonical_tb, output_dir? | Compute PY vs CY ledger count delta and new/removed GL codes / FS groupings. |
| comparison | `run_comparison_variance` | py_canonical_tb, cy_canonical_tb, materiality_file?, output_dir? | Compute PY vs CY closing-balance variance per GL code, flagged against materiality thresholds. |
| db_bridge | `delete_db_document` | tb_doc_id | Delete a Trial Balance document and its GL lines from LIVE staging by tb_doc_id. |
| db_bridge | `ingest_tb_to_live` | tb_grouping_template_path, grouping_file_path?, output_dir?, standard?, accept_data_quality_risk?, custom_fields? | Single entry point for both live-template and uploaded TB(+Grouping) submissions into LIVE staging -- native auto-detecting parser + classification engine, one or two files, Scenario A/B/C/D auto-detected from content. |
| db_bridge | `list_db_documents` | entity_id?, financial_year? | List Trial Balance documents already ingested into the MAIN database, optionally filtered. |
| db_bridge | `load_tb_from_db` | tb_doc_id?, entity_id?, financial_year?, output_filename?, output_dir? | Load a Trial Balance from the MAIN database (document_table/tb_table) into a canonical file. |
| entity | `build_engagement_context` | canonical_tb_file, engagement_context?, accounting_framework?, entity_name?, period_end?, currency?, scale?, consolidation_basis?, source_system?, caro_applicable?, government_company?, output_dir? | Record the engagement context, reporting framework, currency, scale, period, etc. |
| entity | `build_entity_profile` | canonical_tb_file, output_dir? | Classifies the entity's business shape from the canonical TB itself. |
| fsli | `build_audit_ratio_pack` | canonical_tb_file, fsli_summary_file?, materiality_file?, output_dir? | Compute the audit-analytical ratios — debtor, creditor and related turnover metrics. |
| fsli | `build_financial_ratios` | canonical_tb_file?, output_dir? | Compute Current Ratio, Quick Ratio, Debt-Equity, Interest Coverage, Gross Margin, etc. |
| fsli | `build_financial_snapshot` | canonical_tb_file, metadata_file?, output_dir? | Enrich the FSLI rollup tree with percent-of-parent/percent-of-main-head and materiality rank. |
| fsli | `build_fsli_summary` | canonical_tb_file, metadata_file?, output_dir? | Aggregate canonical TB GL rows into a main_head/sub_head_1/sub_head_2 rollup tree. |
| fsli | `build_mapping_quality` | canonical_tb_file, output_dir? | Flags near-duplicate main_head labels and other mapping-quality issues. |
| input | `preview_excel_data` | excel_path, is_grouping?, output_dir? | Reads the given Excel file to provide raw columns and sample structural headers to the Agent. |
| materiality | `build_materiality` | canonical_tb_file, metadata_file?, output_dir? | Compute overall/performance/trivial materiality and material FSLI/GL populations. |
| materiality | `build_materiality_lens` | materiality_file?, sensitive_accounts_file?, layer1_results_file?, audit_approved_materiality?, output_dir? | Layer materiality by nature and context over the computed quantitative thresholds. |
| reasoning | `build_assertion_evidence_map` | consolidated_exceptions_file?, sensitive_accounts_file?, output_dir? | Map every consolidated exception to the assertions its account area exposes. |
| reasoning | `build_audit_reasoning` | consolidated_exceptions_file, validation_report_file, max_observations?, output_dir?, llm_client?, data_sufficiency_file? | Select the highest-priority exception clusters, tag each with an evidence reference. |
| reasoning | `build_finding_records` | output_dir?, data_sufficiency_file? | Consolidate every screen's findings into the canonical audit-finding record. |
| reasoning | `build_request_lists` | finding_records_file?, assertion_evidence_map_file?, output_dir? | Consolidate every finding's evidence into one de-duplicated, risk-ordered request list. |
| reasoning | `build_run_log` | output_dir?, source_file_path?, tb_doc_id?, session_id? | Record this run's provenance: source file name and SHA-256, every artifact, prompt version. |
| reasoning | `validate_tb_pipeline` | output_dir? | Check pipeline artifact completeness, financial (assets vs liabilities+equity). |
| reports | `build_docx_report` | output_dir?, canonical_tb_file?, tb_metadata_file?, processing_manifest_file?, mapping_summary_file?, grouping_metadata_file?, grouping_statistics_file?, materiality_file?, sensitive_accounts_file?, consolidated_exceptions_file?, audit_reasoning_file?, fsli_summary_file?, estimation_exposure_file?, fx_exposure_file?, mapping_quality_file? | Composes TB_Audit_Report.docx from canonical artifacts only. |
| reports | `build_excel_report` | canonical_tb_file?, output_dir?, entity_label?, tb_metadata_file?, materiality_file?, fsli_summary_file?, consolidated_exceptions_file?, sensitive_accounts_file?, estimation_exposure_file?, fx_exposure_file?, mapping_quality_file? | Creates TB_Audit.xlsx — the Single TB Analytical Review workbook — with 10 sheets. |
| reports | `build_report_markdown` | output_dir?, canonical_tb_file?, tb_metadata_file?, processing_manifest_file?, mapping_summary_file?, materiality_file?, sensitive_accounts_file?, consolidated_exceptions_file?, audit_reasoning_file?, fsli_summary_file?, estimation_exposure_file?, fx_exposure_file?, mapping_quality_file? | Renders the Single-TB report content as a markdown string in data.markdown. |
| risk | `build_abnormal_sign_screen` | canonical_tb_file, materiality_file?, output_dir? | Name every account carrying a closing balance on the opposite side to its normal side. |
| risk | `build_anomaly_scanner` | canonical_tb_file, output_dir?, llm_client?, tb_year? | Run statistical/pattern anomaly checks (Benford's Law, round-numbers, duplicate descriptions). |
| risk | `build_caro_indicators` | canonical_tb_file, engagement_context_file?, output_dir? | Flag trial-balance indicators relevant to CARO 2020 areas and Companies Act sections. |
| risk | `build_counterpart_screen` | canonical_tb_file, fsli_summary_file?, materiality_file?, output_dir? | Flag expected account pairs where the source head is material but its counterpart is missing/small. |
| risk | `build_estimation_exposure` | canonical_tb_file, output_dir? | Flags every account whose carrying value rests on management estimate/judgment. |
| risk | `build_exception_consolidator` | canonical_tb_file, materiality_file?, financial_snapshot_file?, risk_indicators_file?, sensitive_accounts_file?, relationship_analytics_file?, relationship_graph_file?, layer1_results_file?, variance_analysis_file?, anomaly_findings_file?, output_dir? | Correlate validation/risk/sensitivity/variance/relationship/anomaly analytics into one list. |
| risk | `build_fx_exposure` | canonical_tb_file, output_dir?, entity_profile_file? | FX/translation-risk screen: classifies FCY-tagged accounts as monetary/non-monetary. |
| risk | `build_going_concern_screen` | canonical_tb_file, materiality_file?, output_dir? | Screen the trial balance for going-concern indicators. |
| risk | `build_override_indicators` | canonical_tb_file, anomaly_findings_file?, materiality_file?, output_dir? | Gather management-override and irregularity indicators under one screen. |
| risk | `build_public_sector_lens` | canonical_tb_file, engagement_context_file?, materiality_file?, output_dir? | Run the public-sector regularity and propriety questions. |
| risk | `build_relationship_analytics` | canonical_tb_file, fsli_summary_file, variance_analysis_file?, output_dir? | Build a deterministic relationship graph (GL -> FSLI -> business process, peers). |
| risk | `build_relationship_expectations` | canonical_tb_file, fsli_summary_file?, materiality_file?, output_dir? | Compute each expected trial-balance relationship (receivables/revenue, etc.). |
| risk | `build_risk_indicators` | canonical_tb_file, materiality_file?, output_dir? | Assign a composite risk score to each GL account, sourced from materiality, variance, etc. |
| risk | `build_sensitive_detector` | canonical_tb_file, output_dir? | Classify sensitive accounts (grant/subsidy, related-party, statutory dues, etc.) by keyword match. |
| risk | `build_statutory_screen` | canonical_tb_file, materiality_file?, output_dir? | Reconcile statutory dues against the bases they arise from. |
| variance | `build_variance_analysis` | canonical_tb_file, materiality_file?, output_dir? | Compute materiality-aware opening->closing movement analytics at GL and hierarchy level. |
<!-- END GENERATED TOOL TABLE -->
