"""Response models shared across modes."""

from __future__ import annotations

from pydantic import BaseModel, field_validator


# ---------------------------------------------------------------------------
# Chat-style modes (financial statement, diagnostic report)
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    query: str
    # Which conversation this turn belongs to. Optional and defaulted, so an
    # existing caller that sends only `query` keeps working unchanged; the
    # server starts a new conversation when it is absent. The prior turns are
    # NOT sent by the client — they are read from the database against the
    # authenticated user, so the client cannot rewrite its own history and a
    # thread survives a refresh or a move to another machine.
    conversation_id: str | None = None

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
    # The conversation this answer was recorded in — the client stores it and
    # sends it back on the next turn.
    conversation_id: str = ""
    # What was actually sent to the pipeline, when a follow-up was resolved into
    # a standalone question. Empty when the question was used as typed. Surfaced
    # rather than hidden: an answer that addresses something subtly different
    # from what was asked is otherwise impossible to explain after the fact.
    rewritten_query: str = ""
    # Both financial-statement-only: populated when the conversation has an
    # upload in scope, otherwise left at their defaults so every other mode's
    # response is unaffected. `response_model=QueryResponse` silently drops any
    # key a route returns that is not declared here -- these two were computed
    # correctly in modes/financial_statement/adapter.py and dropped at exactly
    # this boundary for as long as they were missing from this shared model.
    materiality_legend: dict | None = None
    uploaded_documents: list[dict] = []
    # Set only when the uploaded-document store (Redis) couldn't be reached
    # for this question — see modes/financial_statement/adapter.py. None means
    # either no upload was relevant or the store answered normally; it is not
    # a signal that a document exists.
    upload_store_notice: str | None = None
    # How the answer was checked: confidence (and what it was lowered from and
    # why), the tools used, and whether no tool was called. Financial-statement
    # only; None for every other mode. Shown collapsed, never above the answer.
    checks: dict | None = None


# ---------------------------------------------------------------------------
# Trial Balance — request/response models live inline in
# modes/trial_balance/router.py instead of here (TB-v2's own convention).
# ---------------------------------------------------------------------------


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
