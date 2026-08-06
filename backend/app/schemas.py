"""Response models shared across modes."""

from __future__ import annotations

from pydantic import BaseModel, field_validator


# ---------------------------------------------------------------------------
# Chat-style modes (financial statement, diagnostic report)
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    query: str

    @field_validator("query")
    @classmethod
    def _normalise_whitespace(cls, value: str) -> str:
        """Strip trailing whitespace from every line, not just the whole string.

        This is not cosmetic. A single space typed before Shift+Enter — invisible
        in the chat bubble, and left untouched by `.strip()` because it sits in
        the *interior* of the string — changes the tokenisation of the prompt
        enough to flip the agent's first tool choice.

        Measured on the Financial Statements mode, same container, same endpoint:

            "...unfavorable.\\nOutput: Table"    (147 chars)
                -> summarize_annual_report, get_audit_report_highlights
                -> answers in 3 iterations, 20+ consecutive successes

            "...unfavorable. \\nOutput: Table"   (148 chars, one added space)
                -> search_company_disclosures, then lookup_report_reference x7
                -> exhausts MAX_TOOL_ITERATIONS, 3/3 failures

        Deterministic in both directions. Normalising here rather than in one
        router because every chat-style mode feeds its text to the same class of
        agent and is exposed to the same trap. Interior blank lines and
        single spaces *within* a line are preserved — the user's phrasing and
        deliberate line structure are theirs, and only invisible trailing runs
        are removed.
        """
        return "\n".join(line.rstrip() for line in value.splitlines()).strip()


class QueryResponse(BaseModel):
    mode: str
    query: str
    summary: str = ""
    final_answer: str = ""
    evidences_md: str = ""
    chunks: list[dict] = []
    num_tables_searched: int = 0
    num_chunks_retrieved: int = 0
    elapsed_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Trial Balance — upload once, then ask / audit / validate against a doc_id
# ---------------------------------------------------------------------------

class TBUploadResponse(BaseModel):
    # Present for parity with the source API, where /api/upload accepted both
    # Excel and PDF and callers used `kind` to tell the two responses apart. Here
    # the routes are already separate (/upload vs /pdfs), so it is a constant —
    # kept so a client written against either API reads the same field.
    kind: str = "trial_balance"
    doc_id: str
    filename: str
    sheet: str | None = None
    periods: list[str]
    accounts: int
    parse_report: dict


class TBPreviewResponse(BaseModel):
    filename: str | None = None
    sheets: list[dict]


class TBUploadMappedRequest(BaseModel):
    token: str
    sheet_name: str
    header_row: int
    account_col: int
    debit_col: int | None = None
    credit_col: int | None = None
    balance_col: int | None = None
    code_col: int | None = None


class TrialBalanceInfo(BaseModel):
    doc_id: str
    filename: str
    sheet: str | None = None
    periods: list[str]
    uploaded_at: str | None = None


class TBAskRequest(BaseModel):
    doc_id: str
    question: str
    session_id: str | None = None


class TBAskResponse(BaseModel):
    doc_id: str
    answer: str
    sources: list[dict] = []


class TBGeneralAskRequest(BaseModel):
    """A question with no trial balance attached — answered from the corpora."""
    question: str
    session_id: str | None = None
    upload_doc_ids: list[str] | None = None


class TBGeneralAskResponse(BaseModel):
    answer: str
    sub_queries: list[str] = []
    sub_answers: list[str] = []
    sources: list[dict] = []
    # Deterministic financial_math result, when the question was arithmetic.
    computed: dict | None = None
    # Set when a guardrail fired (out_of_scope / injection / advice / no_evidence).
    # The answer is still returned; this says the pipeline declined to source it.
    guardrail: str | None = None


class TBAuditRequest(BaseModel):
    doc_id: str
    doc_id_prior: str | None = None
    entity: str | None = None
    engagement_context: str | None = None
    framework: str | None = None
    # Optional client chart-of-accounts / management FSLI grouping, issued by
    # POST /audit/upload-grouping. Omitted -> the keyword mapping engine is used.
    grouping_token: str | None = None
    # Optional annual-report / auditor-comment PDFs (ids from POST /pdfs), used
    # to quantify document-sourced risk items. Omitted -> doc_evidence_status
    # reports "no_docs" and the report's Quantified Risk Areas stay empty.
    upload_doc_ids: list[str] | None = None


class TBPdfResponse(BaseModel):
    doc_id: str
    filename: str
    pages: int
    chunks: int          # page-chunks stored; very dense pages are split
    pages_with_text: int


class TBPdfInfo(BaseModel):
    doc_id: str
    filename: str
    total_pages: int
    total_chunks: int
    uploaded_at: str | None = None


class TBGroupingMappedRequest(BaseModel):
    """Explicit column mapping for a grouping file whose layout couldn't be
    auto-detected. `heading_mode` selects the line-item-heading layout, where the
    FSLI name occupies its own row rather than a column of its own."""
    token: str
    sheet_name: str
    header_row: int           # 0-based row index
    code_col: int | None = None
    name_col: int | None = None
    group_col: int | None = None
    heading_mode: bool = False
    # The trial balance(s) this grouping applies to — used only to report how
    # many of their accounts the mapping actually covers.
    doc_id: str | None = None
    doc_id_2: str | None = None


class TBGroupingResponse(BaseModel):
    grouping_token: str
    n_entries: int      # lookup keys parsed (> the file's row count: code + name each)
    n_matched: int      # accounts of the selected TB(s) this grouping will classify
    n_tb_accounts: int
    n_rows: int         # deprecated alias of n_entries, for parity with the source API


class TBValidateRequest(BaseModel):
    doc_id: str
    doc_id_prior: str | None = None
    tolerance_pct: float | None = None
    documented_convention: str | None = None
    engagement_period: str | None = None
    target_currency: str | None = None
    rounding_account_threshold: float | None = None
    variance_materiality_pct: float | None = None
    never_invert_accounts: list[str] | None = None
    approved_group_master: list[str] | None = None
    required_heads: list[str] | None = None
    tax_head_sign_map: dict[str, str] | None = None
    sub_ledger_ref: list[str] | None = None
    target_company_code: str | None = None
    external_pl_figure: float | None = None
    tb_period_year: int | None = None


class TBValidateResponse(BaseModel):
    phase_reached: str
    layer1_results: dict
    structural_result: dict | None = None
    variance_rows: list[dict] | None = None
    full_table_rows: list[dict] | None = None
    formula_flags: list[dict] = []
    summary_narrative: str


# ---------------------------------------------------------------------------
# Report-style modes (statutory auditor's report)
# ---------------------------------------------------------------------------

class ReportRequest(BaseModel):
    entity: str
    fy_start: int
    fy_end: int
    scope: str = "standalone"


class ReportResponse(BaseModel):
    mode: str
    entity: str
    fy_label: str
    scope: str
    report_md: str
    parsed: dict = {}
    observations: list = []
    quality_flags: dict = {}
    doc_meta: dict = {}
    elapsed_seconds: float = 0.0


# ---------------------------------------------------------------------------
# SAR Q&A chat (statutory auditor's report, conversational)
#
# Not QueryRequest: this mode is scoped to one entity and financial year picked
# from the same catalog the report mode uses, and it carries prior turns, so the
# pipeline's rewriter can resolve "it"/"that clause" against the conversation.
# ---------------------------------------------------------------------------

class SARChatRequest(BaseModel):
    query: str
    company: str
    fy_start: int
    history: list[dict] = []

    @field_validator("query")
    @classmethod
    def _normalise_whitespace(cls, value: str) -> str:
        """Same per-line trailing-whitespace strip as QueryRequest.

        Applied here too because this mode feeds free text to the same class of
        tool-calling agent, and so is exposed to the same trap: an invisible
        space before a newline survives `.strip()` and can change which tool the
        agent reaches for first. See QueryRequest for the measured case.
        """
        return "\n".join(line.rstrip() for line in value.splitlines()).strip()


class SARChatResponse(BaseModel):
    mode: str
    query: str
    final_answer: str = ""
    evidences_md: str = ""


# ---------------------------------------------------------------------------
# Mode discovery
# ---------------------------------------------------------------------------

class ModeInfo(BaseModel):
    id: str
    label: str
    short_label: str
    description: str
    branch: str
    ui: str                 # "chat" | "report" | "report-chat" | "trial-balance"
    base_path: str
    integrated: bool
    # Set on sub-modes that the UI folds into another mode rather than listing
    # separately — see Mode.companion_of in registry.py.
    companion_of: str | None = None
    available: bool
    reason: str | None = None
