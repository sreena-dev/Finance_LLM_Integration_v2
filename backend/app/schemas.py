"""Response models shared across modes."""

from __future__ import annotations

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Chat-style modes (financial statement, diagnostic report)
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    query: str


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


class TBAuditRequest(BaseModel):
    doc_id: str
    doc_id_prior: str | None = None
    entity: str | None = None
    engagement_context: str | None = None
    framework: str | None = None


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
# Mode discovery
# ---------------------------------------------------------------------------

class ModeInfo(BaseModel):
    id: str
    label: str
    short_label: str
    description: str
    branch: str
    ui: str                 # "chat" | "report" | "upload-chat"
    base_path: str
    integrated: bool
    available: bool
    reason: str | None = None
