"""TB-v2 mode router — implements the mode-gateway contract the existing
finance_llm_integrated_v1 frontend expects (see frontend/src/api/client.js).
Every route is thin: parse input, call one tool directly (deterministic
lookups) or hand off to the agent (multi-step reasoning), return JSON.

Some routes below call tools that sibling agents are porting in parallel
(process_input_documents, extract_grouping_mapping, build_*). Those calls go
through `call_tool()`, which raises ToolNotAvailableError with a clear 424
response if the module isn't registered yet — this file does not hardcode
their exact signatures beyond what PROMPT.md documents, since the parallel
work may still shift parameter names slightly.

Note: frontend/src/api/client.js also calls `/pdfs/health`, `POST /pdfs`, and
`GET/DELETE /pdfs` -- a separate PDF-evidence/embedding sub-feature with its
own database and embedding endpoint, not part of the Trial Balance pipeline
this router implements. Deliberately not built here; flagged so it isn't
rediscovered as a missing-endpoint gap later.
"""

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from modes.trial_balance.pipeline.agent import ToolNotAvailableError, call_tool, get_agent
from modes.trial_balance.pipeline.tools import PipelineDBError, PipelineFileError, resolve_output_dir, verify_packs
from modes.trial_balance.pipeline.config import settings
from modes.trial_balance.pipeline.db import add_findings, create_session, find_latest_session, update_session_status

logger = logging.getLogger(__name__)

# Fail fast if any audit-knowledge pack is missing or malformed. Several packs are
# read at module scope in pipeline/tools.py because their values are imported by
# name; without this check a missing pack would surface only as a tool quietly
# registering with degraded data, which on an audit pipeline is a silent
# capability loss rather than a visible error.
verify_packs()

router = APIRouter(prefix="/api/trial-balance", tags=["trial-balance"])

_UPLOAD_ROOT = settings.OUTPUT_DIR / "uploads"
_UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)

# In-process token -> upload metadata store, backing /preview and /upload-mapped.
# TB-v2 has no shared preview-token store yet (only backend/db.py's
# pipeline_sessions CRUD exists) -- this is intentionally process-local, matching
# TB-v1's single-worker deployment assumption.
_PREVIEW_STORE: dict = {}


def _tool_error_response(exc: Exception):
    if isinstance(exc, ToolNotAvailableError):
        return JSONResponse(status_code=424, content={"detail": str(exc)})
    if isinstance(exc, (PipelineFileError, PipelineDBError)):
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    logger.exception("Unhandled tool error")
    return JSONResponse(status_code=500, content={"detail": str(exc)})


# ── Request models ───────────────────────────────────────────────────────────

class UploadMappedRequest(BaseModel):
    token: str
    column_mapping: dict


class AskRequest(BaseModel):
    doc_id: Optional[str] = None
    question: str
    session_id: Optional[str] = None


class AskGeneralRequest(BaseModel):
    question: str
    session_id: Optional[str] = None
    upload_doc_ids: Optional[list] = None


class AuditRequest(BaseModel):
    doc_id: str
    doc_id_prior: Optional[str] = None
    entity: Optional[str] = None
    engagement_context: Optional[str] = None
    framework: Optional[str] = None
    grouping_token: Optional[str] = None
    upload_doc_ids: Optional[list] = None


class ValidateRequest(BaseModel):
    doc_id: str
    doc_id_prior: Optional[str] = None


class AuditWorkbookRequest(BaseModel):
    doc_id: str
    doc_id_prior: Optional[str] = None
    upload_doc_ids: Optional[list] = None
    format: Optional[str] = None  # "xlsx" | "docx", default "xlsx"


# ── Health ───────────────────────────────────────────────────────────────────

@router.get("/health")
def health():
    return {"status": "ok", "mode": "trial-balance", "available": True}


# ── Upload / preview / mapped-upload ────────────────────────────────────────

@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    token = str(uuid.uuid4())
    dest_dir = _UPLOAD_ROOT / token
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / file.filename
    with open(dest_path, "wb") as f:
        f.write(await file.read())

    _PREVIEW_STORE[token] = {"file_path": str(dest_path), "filename": file.filename}

    try:
        result = call_tool("preview_excel_data", excel_path=str(dest_path))
    except ToolNotAvailableError as exc:
        return _tool_error_response(exc)

    if result.get("execution_status") != "SUCCESS":
        raise HTTPException(status_code=422, detail={"needs_mapping": True, "preview_token": token, **result})

    _PREVIEW_STORE[token]["preview"] = result
    return {"token": token, "filename": file.filename, **result}


@router.get("/preview")
def preview(token: str):
    entry = _PREVIEW_STORE.get(token)
    if not entry:
        raise HTTPException(status_code=404, detail="Unknown or expired preview token.")
    return entry.get("preview", entry)


@router.post("/upload-mapped")
def upload_mapped(request: UploadMappedRequest):
    entry = _PREVIEW_STORE.get(request.token)
    if not entry:
        raise HTTPException(status_code=404, detail="Unknown or expired preview token.")

    output_dir = str(resolve_output_dir(request.token))
    try:
        result = call_tool(
            "process_input_documents",
            tb_excel_path=entry["file_path"],
            tb_column_mapping=request.column_mapping,
            output_dir=output_dir,
        )
    except ToolNotAvailableError as exc:
        return _tool_error_response(exc)

    entry["processed"] = result

    if result.get("execution_status") == "SUCCESS":
        try:
            validation = call_tool(
                "validate_layer1_tb",
                tb_excel_path=entry["file_path"],
                tb_column_mapping=request.column_mapping,
                output_dir=output_dir,
            )
        except ToolNotAvailableError as exc:
            validation = _tool_error_response(exc)
        entry["layer1_validation"] = validation
        result["layer1_validation"] = validation

    return result


# ── Documents (MAIN, read-only) ─────────────────────────────────────────────

@router.get("/documents")
def list_documents(entity_id: Optional[str] = None, financial_year: Optional[str] = None):
    try:
        result = call_tool("list_db_documents", entity_id=entity_id, financial_year=financial_year)
    except ToolNotAvailableError as exc:
        return _tool_error_response(exc)
    return result


@router.get("/documents/{doc_id}")
def get_document(doc_id: str):
    try:
        result = call_tool("load_tb_from_db", tb_doc_id=doc_id)
    except ToolNotAvailableError as exc:
        return _tool_error_response(exc)
    if result.get("execution_status") != "SUCCESS":
        raise HTTPException(status_code=404, detail=result.get("message", "Document not found."))

    canonical_tb_file = (result.get("artifacts") or [None])[0]
    if canonical_tb_file:
        try:
            validation = call_tool(
                "validate_layer1_tb",
                canonical_tb_file=canonical_tb_file,
                output_dir=str(Path(canonical_tb_file).parent),
            )
        except ToolNotAvailableError as exc:
            validation = _tool_error_response(exc)
        result["layer1_validation"] = validation

    return result


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str):
    try:
        result = call_tool("delete_db_document", tb_doc_id=doc_id)
    except ToolNotAvailableError as exc:
        return _tool_error_response(exc)
    return result


# ── Chat ─────────────────────────────────────────────────────────────────────

@router.post("/ask")
def ask(request: AskRequest):
    """A question about a specific uploaded/analyzed Trial Balance (doc_id)."""
    session_id = create_session(mode="CHAT_QUERY", source="ask", tb_doc_id=request.doc_id)
    try:
        prompt = f"Trial Balance doc_id: {request.doc_id}\nQuestion: {request.question}"
        response = get_agent().invoke(prompt)
        update_session_status(session_id, "SUCCESS")
        return {"answer": response, "session_id": session_id}
    except Exception as e:
        update_session_status(session_id, "FAILED", error_message=str(e))
        return _tool_error_response(e)


@router.post("/ask-general")
def ask_general(request: AskGeneralRequest):
    """A question with no Trial Balance attached — answered from reference corpora."""
    session_id = create_session(mode="CHAT_QUERY", source="ask-general")
    try:
        response = get_agent().invoke(request.question)
        update_session_status(session_id, "SUCCESS")
        return {"answer": response, "session_id": session_id, "computed": None, "guardrail": None}
    except Exception as e:
        update_session_status(session_id, "FAILED", error_message=str(e))
        return _tool_error_response(e)


# ── Audit (single-TB or comparison) ─────────────────────────────────────────

_LAYER1_SEVERITY_MAP = {"Blocking": "high", "Warning": "medium", "Info": "low"}


def _run_layer1_precheck(
    session_id: str, tb_doc_id: str, output_dir: str, run_full_analytics: bool = True
) -> Optional[dict]:
    """Deterministically load a DB-sourced doc and run validate_layer1_tb against it,
    recording any HALTED/WARNING rule outcomes as pipeline_findings. Returns the
    validation tool response, or None if the doc isn't DB-resolvable (e.g. an
    upload token) -- in that case the agent's own dynamic flow handles it later.

    run_full_analytics=False skips the full single-TB analytics chain below (used for
    the COMPARISON PY leg, which only ever needs canonical_tb.parquet -- running
    materiality/exceptions/reasoning against a period nothing downstream consumes would
    be pure waste)."""
    try:
        loaded = call_tool("load_tb_from_db", tb_doc_id=tb_doc_id, output_dir=output_dir)
    except ToolNotAvailableError:
        return None
    if loaded.get("execution_status") != "SUCCESS":
        return None

    canonical_tb_file = loaded["artifacts"][0]

    # TB-R18: entity_profile.json must exist deterministically, before the agent's own
    # analytics phase runs -- build_materiality.py's basis selector and build_sensitive_
    # detector.py's related-party register both read it. Live testing (Phase 1) showed
    # the agent's tool-calling loop is not reliable for pipeline-critical steps the
    # response contract depends on, so this is not left to agent discretion.
    try:
        call_tool("build_entity_profile", canonical_tb_file=canonical_tb_file, output_dir=output_dir)
    except ToolNotAvailableError:
        logger.warning("build_entity_profile not available -- downstream tools fall back to their own defaults.")
    except Exception:
        logger.exception("build_entity_profile failed for output_dir=%s", output_dir)

    # TB-R12: estimation-exposure is a size-ranked screen independent of movement/dormancy,
    # needs only canonical_tb.parquet -- deterministic for the same reliability reason as
    # build_entity_profile above.
    try:
        call_tool("build_estimation_exposure", canonical_tb_file=canonical_tb_file, output_dir=output_dir)
    except ToolNotAvailableError:
        logger.warning("build_estimation_exposure not available -- report skips that section.")
    except Exception:
        logger.exception("build_estimation_exposure failed for output_dir=%s", output_dir)

    # TB-R11: gated on the entity_profile.json flag just written above, so this must run
    # after it, not concurrently.
    try:
        call_tool("build_fx_exposure", canonical_tb_file=canonical_tb_file, output_dir=output_dir)
    except ToolNotAvailableError:
        logger.warning("build_fx_exposure not available -- report skips that section.")
    except Exception:
        logger.exception("build_fx_exposure failed for output_dir=%s", output_dir)

    # TB-R19: needs only canonical_tb.parquet -- deterministic for the same reliability
    # reason as the other analytics screens above.
    try:
        call_tool("build_mapping_quality", canonical_tb_file=canonical_tb_file, output_dir=output_dir)
    except ToolNotAvailableError:
        logger.warning("build_mapping_quality not available -- report skips that section.")
    except Exception:
        logger.exception("build_mapping_quality failed for output_dir=%s", output_dir)

    try:
        validation = call_tool("validate_layer1_tb", canonical_tb_file=canonical_tb_file, output_dir=output_dir)
    except ToolNotAvailableError as exc:
        return _tool_error_response(exc)

    if validation.get("execution_status") == "SUCCESS" and validation.get("artifacts"):
        try:
            with open(validation["artifacts"][0]) as f:
                rule_results = json.load(f)
            findings = [
                {
                    "category": "layer1_validation",
                    "severity": _LAYER1_SEVERITY_MAP.get(r.get("severity"), "medium"),
                    "statement": f"[{r['rule']}] {r['rule_name']}: {r['message']}",
                    "evidence_uids": [r["rule"]],
                }
                for r in rule_results
                if r.get("status") in ("HALTED", "WARNING")
            ]
            if findings:
                add_findings(session_id, findings)
        except Exception:
            logger.exception("Failed to record layer1_validation findings for session %s", session_id)

    if run_full_analytics:
        _run_core_analytics_chain(canonical_tb_file, output_dir)
    else:
        # Chat-query coverage (Q25, common-size % change vs PY): the PY leg otherwise never
        # gets a snapshot_drilldown.parquet at all, so chat_query_fsli_table's common_size
        # metric has nothing to diff CY against for this comparison run. build_fsli_summary
        # is a prerequisite for build_financial_snapshot (see _run_core_analytics_chain's own
        # note) -- both are cheap, deterministic, and don't touch materiality/exceptions/
        # reasoning, which stay skipped for PY as before.
        try:
            call_tool("build_fsli_summary", canonical_tb_file=canonical_tb_file, output_dir=output_dir)
            call_tool("build_financial_snapshot", canonical_tb_file=canonical_tb_file, output_dir=output_dir)
        except ToolNotAvailableError:
            logger.warning("build_fsli_summary/build_financial_snapshot not available for PY leg -- common-size PY comparison unavailable.")
        except Exception:
            logger.exception("PY-leg financial snapshot chain failed for output_dir=%s", output_dir)

    return validation


def _run_core_analytics_chain(canonical_tb_file: str, output_dir: str) -> None:
    """Deterministically runs the full single-TB analytics chain (financial snapshot ->
    FSLI summary -> materiality -> relationship analytics -> risk indicators -> sensitive
    detector -> anomaly scanner -> exception consolidator -> pipeline validation -> data
    sufficiency -> audit reasoning).

    Root cause this fixes: live testing (re-running the same EPIL/APCPL documents multiple
    times) showed the agent's tool-calling loop skips a DIFFERENT step of this chain on
    different runs of the SAME document -- materiality.json missing on one run, consolidated_
    exceptions.json/audit_reasoning.json missing on another, both fully correct on a third.
    Not a data or taxonomy bug (same document, same data, different outcome), and not
    fixable by better agent prompting -- the same "don't trust agent discretion for
    response-contract-critical output" fix already applied to report generation and the
    Phase-2 screens above, extended to cover this chain too.

    Each step degrades independently on failure (log and continue, matching every other
    tool call in this file) -- one missing prerequisite means that section reads "not
    available" in the report, not that the whole /audit response fails."""
    out_dir = Path(output_dir)

    def run(tool_name: str, **kwargs):
        try:
            return call_tool(tool_name, **kwargs)
        except ToolNotAvailableError:
            logger.warning("%s not available -- downstream steps/report sections degrade accordingly.", tool_name)
        except Exception:
            logger.exception("%s failed for output_dir=%s", tool_name, output_dir)
        return None

    # build_financial_snapshot actually requires fsli_summary.parquet to already exist
    # (confirmed by direct reproduction -- its own PipelineFileError names
    # "FSLI Summary (run build_fsli_summary first)"), the reverse of what the file names
    # suggest. fsli_summary must run first.
    # ── context and normalisation (spec sec 2.1, sec 4.2) ────────────────────
    # Both run first for a stated reason. build_engagement_context writes the
    # applicability gates that build_caro_indicators and build_public_sector_lens
    # read, so it must precede them. build_normalisation_note records what was done
    # to the numbers before analysis, and sec 4.2 requires that note to precede any
    # audit finding.
    run("build_engagement_context", canonical_tb_file=canonical_tb_file, output_dir=output_dir)
    run(
        "build_normalisation_note",
        canonical_tb_file=canonical_tb_file,
        layer1_results_file=str(out_dir / "layer1_results.json"),
        output_dir=output_dir,
    )

    run("build_fsli_summary", canonical_tb_file=canonical_tb_file, output_dir=output_dir)
    run("build_financial_snapshot", canonical_tb_file=canonical_tb_file, output_dir=output_dir)
    # chat_get_financial_ratios (Q41/Q83) reads financial_ratios.json -- depends on
    # snapshot_drilldown.parquet/financial_snapshot_statistics.json just written above.
    run("build_financial_ratios", canonical_tb_file=canonical_tb_file, output_dir=output_dir)
    # Audit-analytical ratios (sec 8.3) -- debtor/creditor/inventory intensity,
    # depreciation proxy, finance-cost ratio. Complements build_financial_ratios'
    # liquidity/leverage set rather than replacing it; reads the same snapshot.
    run("build_audit_ratio_pack", canonical_tb_file=canonical_tb_file, output_dir=output_dir)
    run("build_materiality", canonical_tb_file=canonical_tb_file, output_dir=output_dir)
    # build_risk_indicators' own required-file check demands relationship_analytics.json
    # already exist, so relationship_analytics must run before it, not after.
    run(
        "build_relationship_analytics",
        canonical_tb_file=canonical_tb_file,
        fsli_summary_file=str(out_dir / "fsli_summary.parquet"),
        output_dir=output_dir,
    )
    # ── relationship screens (sec 6, sec 7, sec 5.3) ─────────────────────────
    # All three read canonical_tb + fsli_summary + materiality, so they sit after
    # build_materiality and before the risk engines that consume their findings.
    # build_counterpart_screen answers "is the counterpart there at all?";
    # build_relationship_expectations answers "given both are there, is the
    # relationship plausible?" -- they are complementary, not alternatives.
    for _screen in (
        "build_counterpart_screen",
        "build_relationship_expectations",
        "build_abnormal_sign_screen",
    ):
        run(_screen, canonical_tb_file=canonical_tb_file, output_dir=output_dir)

    run("build_risk_indicators", canonical_tb_file=canonical_tb_file, output_dir=output_dir)
    run("build_sensitive_detector", canonical_tb_file=canonical_tb_file, output_dir=output_dir)

    # Materiality by nature/context (sec 2.2, sec 8.1) reads sensitive_accounts.json,
    # so it must follow build_sensitive_detector -- NOT build_materiality, which is
    # where it would naturally seem to belong. Without the sensitive population it
    # can elevate nothing, which is the whole point of the tool.
    run(
        "build_materiality_lens",
        layer1_results_file=str(out_dir / "layer1_results.json"),
        output_dir=output_dir,
    )

    run(
        "build_anomaly_scanner",
        canonical_tb_file=canonical_tb_file,
        output_dir=output_dir,
        llm_client=get_agent().llm_client,
    )

    # ── India-specific and public-sector screens (sec 9.16, sec 10, sec 12) ───
    # build_override_indicators reads anomaly_findings.json to reframe the
    # round-sum/Benford signals rather than recomputing them, so it follows the
    # anomaly scanner. The CARO and public-sector screens read the applicability
    # gates written by build_engagement_context at the top of this chain.
    for _screen in (
        "build_statutory_screen",
        "build_public_sector_lens",
        "build_going_concern_screen",
        "build_caro_indicators",
        "build_override_indicators",
    ):
        run(_screen, canonical_tb_file=canonical_tb_file, output_dir=output_dir)

    run("build_exception_consolidator", canonical_tb_file=canonical_tb_file, output_dir=output_dir)

    # Re-maps the exceptions just consolidated from source-engine-derived assertions
    # ("Ledgers", "Contracts/Agreements") to account-area assertions and named
    # records (Appendix B/C). Must follow build_exception_consolidator.
    run("build_assertion_evidence_map", output_dir=output_dir)
    run("validate_tb_pipeline", output_dir=output_dir)
    layer1_results_file = out_dir / "layer1_results.json"
    if layer1_results_file.exists():
        run(
            "build_data_sufficiency_grade",
            layer1_results_file=str(layer1_results_file),
            canonical_tb_file=canonical_tb_file,
            output_dir=output_dir,
        )
    # ── consolidated output (sec 13, sec 14, sec 15.1) ───────────────────────
    # build_finding_records collects every screen's findings into the specification's
    # record shape; build_request_lists then turns them into the de-duplicated
    # evidence list and the separate management-query list sec 15.1 requires.
    run(
        "build_finding_records",
        data_sufficiency_file=str(out_dir / "data_sufficiency.json"),
        output_dir=output_dir,
    )
    run("build_request_lists", output_dir=output_dir)

    run(
        "build_audit_reasoning",
        consolidated_exceptions_file=str(out_dir / "consolidated_exceptions.json"),
        validation_report_file=str(out_dir / "validation_report.json"),
        data_sufficiency_file=str(out_dir / "data_sufficiency.json"),
        output_dir=output_dir,
        llm_client=get_agent().llm_client,
    )

    # Runs LAST so it records what actually executed rather than what was planned --
    # it infers the tool list from the artifacts present on disk (sec 16.2).
    run("build_run_log", output_dir=output_dir)


def _run_comparison_analytics_chain(report_dir: Path, cy_dir: Path, py_dir: Path) -> None:
    """Deterministically runs the PY-vs-CY comparison analytics tools (precheck,
    structural delta, variance, sign-check, reasoning) that build_comparison_report
    reads from -- live testing showed the agent's tool-calling loop reliably runs
    the CY single-period analytics chain but was skipping this whole comparison
    chain entirely (agent-driven ordering per system_prompt.py has no fixed
    sequence requirement), leaving build_comparison_report with an empty
    PY-comparison shell. Mirrors the same "don't trust agent discretion for
    outputs the response contract depends on" fix already applied to report
    generation. Skips a step whose output file already exists (agent may have
    already run it), same idempotent-and-harmless pattern as the report calls."""
    py_canonical_tb = py_dir / "canonical_tb.parquet"
    cy_canonical_tb = cy_dir / "canonical_tb.parquet"
    if not py_canonical_tb.exists() or not cy_canonical_tb.exists():
        logger.warning("Skipping comparison analytics chain: PY or CY canonical_tb.parquet missing.")
        return

    report_dir.mkdir(parents=True, exist_ok=True)
    common_kwargs = {
        "py_canonical_tb": str(py_canonical_tb),
        "cy_canonical_tb": str(cy_canonical_tb),
        "output_dir": str(report_dir),
    }

    # TB-QA-followup: these four always run, unconditionally -- they used to be skipped
    # when their output file already existed, on the assumption that an existing file
    # means a prior, correct run. Live testing showed the agent's own tool-calling loop can
    # call run_comparison_variance before CY's materiality.json is ready, writing a
    # NO_THRESHOLD-poisoned comparison_variance.parquet that this "skip if exists" guard
    # then permanently locked in, since it never got recomputed once materiality.json
    # became available. Matches the same "calling these even when the agent already built
    # them is harmless, they overwrite deterministically" pattern already used for report
    # generation in _finalize_audit_result.
    try:
        call_tool("run_comparison_prechecks", **common_kwargs)
        call_tool("run_comparison_structural", **common_kwargs)
        materiality_file = cy_dir / "materiality.json"
        call_tool(
            "run_comparison_variance",
            materiality_file=str(materiality_file) if materiality_file.exists() else None,
            **common_kwargs,
        )
        call_tool("run_comparison_sign_check", **common_kwargs)
        if not (report_dir / "comparison_reasoning.json").exists():
            call_tool(
                "build_comparison_reasoning",
                precheck_file=str(report_dir / "precheck_results.json"),
                structural_file=str(report_dir / "structural_delta.json"),
                variance_file=str(report_dir / "comparison_variance.parquet"),
                sign_check_file=str(report_dir / "sign_convention_flags.json"),
                output_dir=str(report_dir),
                llm_client=get_agent().llm_client,
            )
    except ToolNotAvailableError:
        logger.warning("Comparison analytics tool(s) not available -- skipping.")
    except Exception:
        logger.exception("Comparison analytics chain failed for report_dir=%s", report_dir)


def _finalize_audit_result(mode: str, run_dir: Path, entity_hint: Optional[str] = None) -> dict:
    """Deterministically generate all 3 report formats and assemble the structured
    `result` object the frontend actually reads -- the agent's raw closing text
    (get_agent().invoke()'s return value) is never returned to the client on its own.
    build_excel_report/build_docx_report are the full-fidelity downloadable
    deliverables (served by /audit/workbook); build_report_markdown is the in-chat
    display outcome. All three are complementary, not alternatives, so all three
    always run here rather than depending on the agent's tool-calling discretion --
    that's what was leaving TB_Audit.xlsx/TB_Audit_Report.docx uncalled before."""
    is_comparison = mode == "COMPARISON"
    report_dir = run_dir / "comparison" if is_comparison else run_dir
    cy_dir = run_dir / "cy" if is_comparison else run_dir
    py_dir = run_dir / "py"
    canonical_tb_file = str(cy_dir / "canonical_tb.parquet")

    result: dict = {}
    if entity_hint:
        result["entity"] = entity_hint

    if not Path(canonical_tb_file).exists():
        logger.warning("Skipping report generation: canonical_tb.parquet not found at %s", canonical_tb_file)
        return result

    if is_comparison:
        _run_comparison_analytics_chain(report_dir, cy_dir, py_dir)

    try:
        if is_comparison:
            call_tool("build_comparison_report", output_dir=str(report_dir))
            markdown_response = call_tool("build_comparison_report_markdown", output_dir=str(report_dir))
        else:
            call_tool("build_excel_report", canonical_tb_file=canonical_tb_file, output_dir=str(report_dir))
            call_tool("build_docx_report", canonical_tb_file=canonical_tb_file, output_dir=str(report_dir))
            markdown_response = call_tool(
                "build_report_markdown", canonical_tb_file=canonical_tb_file, output_dir=str(report_dir)
            )
        result["report"] = (markdown_response.get("data") or {}).get("markdown")
    except ToolNotAvailableError:
        logger.warning("Report tool(s) not available -- skipping deterministic report generation.")
    except Exception:
        logger.exception("Deterministic report generation failed for run_dir=%s", run_dir)

    materiality_path = cy_dir / "materiality.json"
    if materiality_path.exists():
        try:
            with open(materiality_path) as f:
                mat = json.load(f)
            result["materiality"] = {
                "overall_materiality": mat.get("selected_materiality", {}).get("overall_materiality"),
                "benchmark_used": mat.get("summary", {}).get("benchmark_used"),
                "benchmark_analysis": mat.get("benchmark_analysis", []),
            }
        except Exception:
            logger.exception("Failed to read materiality.json for run_dir=%s", run_dir)

    if is_comparison:
        # build_comparison_reasoning writes comparison_reasoning.json into the same
        # output_dir build_comparison_report/_markdown read it from (report_dir, i.e.
        # run_dir/comparison) -- shape: {"summary": {"executive_summary": ...},
        # "observations": [{"observation": {priority, title, executive_summary,
        # detailed_observation, affected_assertions, recommended_procedures, ...}}]}.
        # No cluster_id (unlike SINGLE_TB's audit_reasoning.json), so reference_id
        # falls back to an F01/F02/... index, matching build_comparison_report.py's
        # own DOCX heading convention.
        comparison_reasoning_path = report_dir / "comparison_reasoning.json"
        if comparison_reasoning_path.exists():
            try:
                with open(comparison_reasoning_path) as f:
                    reasoning = json.load(f)
                result["context_note"] = reasoning.get("summary", {}).get("executive_summary")
                findings = []
                summary: dict = {}
                for i, obs_wrap in enumerate(reasoning.get("observations", []), start=1):
                    obs = obs_wrap.get("observation", obs_wrap) if isinstance(obs_wrap, dict) else {}
                    severity = str(obs.get("priority", "")).lower()
                    summary[severity] = summary.get(severity, 0) + 1
                    findings.append({
                        "risk_rating": severity,
                        "reference_id": f"F{i:02d}",
                        "observation": obs.get("detailed_observation") or obs.get("executive_summary"),
                        "evidence_requested": obs.get("recommended_procedures", []),
                    })
                result["findings"] = findings
                result["findings_summary"] = summary
            except Exception:
                logger.exception("Failed to read comparison_reasoning.json for run_dir=%s", run_dir)
    else:
        audit_reasoning_path = run_dir / "audit_reasoning.json"
        if audit_reasoning_path.exists():
            try:
                with open(audit_reasoning_path) as f:
                    reasoning = json.load(f)
                result["context_note"] = reasoning.get("data_sufficiency_note")
                findings = []
                summary: dict = {}
                for finding in reasoning.get("findings", []):
                    severity = str(finding.get("severity", "")).lower()
                    summary[severity] = summary.get(severity, 0) + 1
                    findings.append({
                        "risk_rating": severity,
                        "reference_id": finding.get("cluster_id"),
                        "observation": finding.get("statement"),
                        "evidence_requested": finding.get("recommended_procedures", []),
                    })
                result["findings"] = findings
                result["findings_summary"] = summary
            except Exception:
                logger.exception("Failed to read audit_reasoning.json for run_dir=%s", run_dir)

    return result


@router.post("/audit")
def audit(request: AuditRequest):
    """Routes to the SINGLE_TB chain when doc_id_prior is absent, or the
    COMPARISON chain when both doc_id and doc_id_prior are given."""
    mode = "COMPARISON" if request.doc_id_prior else "SINGLE_TB"
    session_id = create_session(
        mode=mode,
        source="audit",
        tb_doc_id=request.doc_id,
        tb_doc_id_prior=request.doc_id_prior,
        entity_id=request.entity,
    )
    run_dir = resolve_output_dir(f"sessions/{session_id}")
    try:
        cy_validation = _run_layer1_precheck(session_id, request.doc_id, f"{run_dir}/cy" if mode == "COMPARISON" else str(run_dir))
        py_validation = None

        if mode == "COMPARISON":
            py_validation = _run_layer1_precheck(session_id, request.doc_id_prior, f"{run_dir}/py", run_full_analytics=False)
            prompt = (
                f"Run a COMPARISON audit. Current-year doc_id: {request.doc_id}. "
                f"Prior-year doc_id: {request.doc_id_prior}. "
                f"Use output_dir={run_dir}/cy for the CY single-TB chain, "
                f"output_dir={run_dir}/py for the PY load, and "
                f"output_dir={run_dir}/comparison for every comparison tool call. "
                f"Layer 1 validation has already run for both periods (see session findings) "
                f"-- do not re-run validate_layer1_tb unless a downstream tool explicitly needs its artifacts."
            )
        else:
            prompt = (
                f"Run a SINGLE_TB audit on doc_id: {request.doc_id}. "
                f"Use output_dir={run_dir} for every tool call in this run. "
                f"Layer 1 validation has already run (see session findings) "
                f"-- do not re-run validate_layer1_tb unless a downstream tool explicitly needs its artifacts."
            )
        if request.engagement_context:
            prompt += f"\nEngagement context: {request.engagement_context}"
        if request.framework:
            prompt += f"\nFramework: {request.framework}"
        if request.grouping_token:
            entry = _PREVIEW_STORE.get(request.grouping_token)
            if entry:
                prompt += f"\nGrouping file: {entry['file_path']}"

        get_agent().invoke(prompt)
        result = _finalize_audit_result(mode, Path(run_dir), entity_hint=request.entity)
        update_session_status(session_id, "SUCCESS")
        return {
            "session_id": session_id,
            "mode": mode,
            "result": result,
            "layer1_validation": {"cy": cy_validation, "py": py_validation},
        }
    except Exception as e:
        update_session_status(session_id, "FAILED", error_message=str(e))
        return _tool_error_response(e)


@router.post("/audit/upload-grouping")
async def audit_upload_grouping(
    file: UploadFile = File(...),
    doc_id: Optional[str] = Form(None),
    doc_id_2: Optional[str] = Form(None),
):
    token = str(uuid.uuid4())
    dest_dir = _UPLOAD_ROOT / token
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / file.filename
    with open(dest_path, "wb") as f:
        f.write(await file.read())

    _PREVIEW_STORE[token] = {
        "file_path": str(dest_path),
        "filename": file.filename,
        "doc_id": doc_id,
        "doc_id_2": doc_id_2,
        "is_grouping": True,
    }

    try:
        result = call_tool("preview_excel_data", excel_path=str(dest_path), is_grouping=True)
    except ToolNotAvailableError as exc:
        return _tool_error_response(exc)

    if result.get("execution_status") != "SUCCESS":
        raise HTTPException(status_code=422, detail={"needs_mapping": True, "preview_token": token, **result})

    _PREVIEW_STORE[token]["preview"] = result
    return {"token": token, "filename": file.filename, **result}


# ── Validation ───────────────────────────────────────────────────────────────

@router.post("/validate")
def validate(request: ValidateRequest):
    try:
        result = call_tool("load_tb_from_db", tb_doc_id=request.doc_id)
        if result.get("execution_status") != "SUCCESS":
            return result
        canonical_tb_file = result["artifacts"][0]
        output_dir = str(Path(canonical_tb_file).parent)
        validation = call_tool("validate_layer1_tb", canonical_tb_file=canonical_tb_file, output_dir=output_dir)
        return validation
    except ToolNotAvailableError as exc:
        return _tool_error_response(exc)


# ── Report download ──────────────────────────────────────────────────────────

# (mode, format) -> filename, matching what build_excel_report/build_docx_report/
# build_comparison_report actually write (see backend/tools/reports/build_excel_report.py,
# build_docx_report.py and backend/tools/comparison/build_comparison_report.py).
_WORKBOOK_FILENAMES = {
    ("SINGLE_TB", "xlsx"): "TB_Audit.xlsx",
    ("SINGLE_TB", "docx"): "TB_Audit_Report.docx",
    ("COMPARISON", "xlsx"): "TB_Comparison_Audit.xlsx",
    ("COMPARISON", "docx"): "TB_Comparison_Report.docx",
}
_WORKBOOK_MEDIA_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _tb_filename_suffix(run_dir: Path, mode: str) -> str:
    """Filesystem-safe suffix for downloadable report filenames, derived from the
    input TB's own identity -- tb_metadata.json's source_file (the uploaded
    filename) if present, else entity_name/company_name, else nothing. Read from
    the CY period's dir for COMPARISON (that's where load_tb_from_db writes it),
    the run's own dir for SINGLE_TB."""
    meta_dir = run_dir / "cy" if mode == "COMPARISON" else run_dir
    meta_path = meta_dir / "tb_metadata.json"
    if not meta_path.exists():
        return ""
    try:
        with open(meta_path, "r") as f:
            meta = json.load(f)
    except Exception:
        return ""
    raw = Path(meta.get("source_file", "")).stem or meta.get("company_name") or meta.get("entity_name") or ""
    safe = re.sub(r"[^A-Za-z0-9]+", "_", raw).strip("_")
    return f"_{safe}" if safe else ""


@router.post("/audit/workbook")
def audit_workbook(request: AuditWorkbookRequest):
    """Binary download of the generated report for a completed /audit run.
    The frontend requests this by doc_id (not session_id), so this looks up
    the most recent successful session for that doc_id/doc_id_prior pair and
    recomputes its run_dir the exact same way /audit itself does -- no extra
    pipeline_sessions column needed."""
    mode = "COMPARISON" if request.doc_id_prior else "SINGLE_TB"
    session = find_latest_session(request.doc_id, request.doc_id_prior)
    if not session:
        raise HTTPException(
            status_code=404,
            detail=f"No completed audit run found for doc_id={request.doc_id!r} "
            f"doc_id_prior={request.doc_id_prior!r}.",
        )

    run_dir = resolve_output_dir(f"sessions/{session['session_id']}")
    report_dir = Path(run_dir) / "comparison" if mode == "COMPARISON" else Path(run_dir)

    fmt = (request.format or "xlsx").lower()
    base_filename = _WORKBOOK_FILENAMES.get((mode, fmt))
    if not base_filename:
        raise HTTPException(status_code=400, detail=f"No report available for mode={mode!r} format={fmt!r}.")

    file_path = report_dir / base_filename
    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Report not found for session {session['session_id']} (expected {file_path}).",
        )

    stem, ext = base_filename.rsplit(".", 1)
    download_filename = f"{stem}{_tb_filename_suffix(Path(run_dir), mode)}.{ext}"

    return FileResponse(path=str(file_path), filename=download_filename, media_type=_WORKBOOK_MEDIA_TYPES[fmt])
