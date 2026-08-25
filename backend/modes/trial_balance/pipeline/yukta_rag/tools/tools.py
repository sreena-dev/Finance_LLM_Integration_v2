"""Yukta tools wrapping the two retrieval corpora.

Each tool returns a citation-tagged string for the LLM and also appends the
structured hits to a per-request ``collector`` list so the API/UI can show
sources.
"""

from __future__ import annotations

from yukta import create_custom_tool

from yukta_rag.core.config import TOP_K
from yukta_rag.retrieval.retrieval import (
    list_ind_as_catalog,
    list_ind_as_in_docs,
    retrieve_annual_reports,
    retrieve_ind_as,
)


def ind_as_blocks(rows: list[dict]) -> list[str]:
    """One citation-tagged block per Ind AS chunk (full text; no truncation)."""
    out = []
    for r in rows:
        para = r.get("paragraph_no") or "-"
        cite = f"[Ind AS {r['standard_number']} — {r['section_title']}, para {para}, p{r['page_no']}]"
        out.append(f"{cite}\n{r['text']}")
    return out


def _fmt_ind_as(rows: list[dict]) -> str:
    return "\n\n---\n\n".join(ind_as_blocks(rows)) if rows else "No Ind AS results found."


def _fmt_catalog(items: list[tuple[int, str]]) -> str:
    lines = [f"- Ind AS {n}: {subj}" if subj else f"- Ind AS {n}" for n, subj in items]
    return f"Ingested Indian Accounting Standards ({len(items)}):\n" + "\n".join(lines)


def _fmt_list_in_docs(d: dict) -> str:
    head = ", ".join(f"{c} FY{fs}-{str(fe)[-2:]}" for _, c, fs, fe in d["docs"])
    stds = d["standards"]
    if not stds:
        return f"No Ind AS standards were found substantively cited in: {head}."
    lines = [f"- Ind AS {n}: {subj} ({m} mention{'s' if m != 1 else ''})" if subj
             else f"- Ind AS {n} ({m} mention{'s' if m != 1 else ''})"
             for n, subj, m in stds]
    return (f"Ind AS standards substantively cited in {head} "
            f"({len(stds)} found, most-cited first):\n" + "\n".join(lines) +
            "\n\n(One-off incidental mentions are excluded.)")


def _docs_str(docs: list) -> str:
    return ", ".join(f"{c} FY{fs}-{str(fe)[-2:]}" for _, c, fs, fe in docs)


def _fmt_docs_citing(res: dict) -> str:
    scope = f" among {res['company']} reports" if res.get("company") else ""
    lines = []
    for s in res["standards"]:
        n, subj = s["number"], s["subject"]
        head = f"Ind AS {n}" + (f" ({subj})" if subj else "")
        if s["literal"]:
            lines.append(f"{head} is cited in: {_docs_str(s['literal'])}")
        elif s["topical"]:
            lines.append(
                f'No report{scope} cites "Ind AS {n}" by number, but its topic '
                f"({subj}) is covered in: {_docs_str(s['topical'])}"
            )
        else:
            lines.append(f"No report{scope} mentions Ind AS {n}.")
    return "\n".join(lines)


# cap on a single table's markdown fed to the model (keeps the prompt bounded
# while preserving the rows/numbers a financial question needs)
_TABLE_MD_CAP = 4000


def ar_blocks(rows: list[dict]) -> list[str]:
    """One citation-tagged block per annual-report chunk (full content).

    For table rows the block carries the actual table markdown (the cell values),
    so numerical questions can be answered from the real numbers rather than from
    a description of the table's structure.
    """
    out = []
    for r in rows:
        fy = f"FY{r['fy_start']}-{str(r['fy_end'])[-2:]}"
        if r["kind"] == "table":
            cite = f"[{r['company']} {fy}, Table, p{r['page']}]"
            out.append(f"{cite}\n{_table_body(r)}")
        else:
            tag = r.get("section") or "text"
            cite = f"[{r['company']} {fy}, {tag}, p{r['page']}]"
            out.append(f"{cite}\n{r['content']}")
    return out


def _table_body(r: dict) -> str:
    """Header (title + unit/currency) followed by the table's markdown cells."""
    header = (r.get("content") or "").strip()  # "title: description"
    unit, cur = r.get("unit"), r.get("currency")
    units = " · ".join(x for x in (cur, unit) if x)
    if units:
        header = f"{header} ({units})" if header else f"(amounts in {units})"
    md = (r.get("table_md") or "").strip()
    if md:
        if len(md) > _TABLE_MD_CAP:
            md = md[:_TABLE_MD_CAP].rstrip() + "\n… (table truncated)"
        return f"{header}\n{md}" if header else md
    return header or "(table content unavailable)"


def _fmt_ar(rows: list[dict]) -> str:
    return "\n\n---\n\n".join(ar_blocks(rows)) if rows else "No annual report results found."


def uploaded_blocks(rows: list[dict]) -> list[str]:
    """One citation-tagged block per uploaded-PDF page chunk (full text).

    Citation form ``[<filename>, p<page>]`` matches the bracketed style the
    answer system prompt tells the model to echo inline.
    """
    out = []
    for r in rows:
        cite = f"[{r.get('filename') or 'Uploaded document'}, p{r.get('page_no')}]"
        out.append(f"{cite}\n{r.get('text') or ''}")
    return out


def _fmt_uploaded(rows: list[dict]) -> str:
    return "\n\n---\n\n".join(uploaded_blocks(rows)) if rows else "No uploaded-document results found."


def build_tools(collector: list):
    """Build the two retrieval tools, wired to append hits to ``collector``."""

    def search_ind_as(query: str, top_k: int = TOP_K, mode: str = "search") -> str:
        # mode='list' -> enumerate standards instead of semantic retrieval:
        # cited in a named company/year report, else the full ingested catalog.
        if str(mode).lower() == "list":
            in_docs = list_ind_as_in_docs(query)
            if in_docs is not None:
                return _fmt_list_in_docs(in_docs)
            return _fmt_catalog(list_ind_as_catalog())
        rows = retrieve_ind_as(query, int(top_k))
        collector.extend({**r, "source": "ind_as"} for r in rows)
        return _fmt_ind_as(rows)

    def search_annual_reports(query: str, top_k: int = TOP_K) -> str:
        rows = retrieve_annual_reports(query, int(top_k))
        collector.extend({**r, "source": "annual_report"} for r in rows)
        return _fmt_ar(rows)

    ind_as_tool = create_custom_tool(
        name="search_ind_as",
        description=(
            "Search Indian Accounting Standards (Ind AS): accounting rules, "
            "definitions, recognition / measurement / disclosure principles and "
            "paragraph-level standard text. Use for questions about how something "
            "should be accounted for or what a standard requires.\n"
            "Set mode='list' for ENUMERATION questions, e.g. 'list/which Ind AS "
            "are mentioned in <company>'s <year> report' (lists the standards "
            "cited in that annual report) or 'list all Ind AS standards' (returns "
            "the full ingested catalogue). Use mode='search' (default) otherwise."
        ),
        parameters=[
            {"name": "query", "type": "string",
             "description": "Search query, or for mode='list' the listing request (may name a company/year)", "required": True},
            {"name": "top_k", "type": "integer",
             "description": "Number of chunks to retrieve in search mode (default 5)", "required": False},
            {"name": "mode", "type": "string",
             "description": "'search' (default) for semantic retrieval, or 'list' to enumerate Ind AS standards", "required": False},
        ],
        function=search_ind_as,
    )

    ar_tool = create_custom_tool(
        name="search_annual_reports",
        description=(
            "Search company annual reports: financial figures, performance, "
            "management discussion, and financial-statement table descriptions for "
            "specific companies and fiscal years. Use for company-specific facts "
            "and numbers."
        ),
        parameters=[
            {"name": "query", "type": "string",
             "description": "Search query about a company's annual report", "required": True},
            {"name": "top_k", "type": "integer",
             "description": "Number of chunks to retrieve (default 5)", "required": False},
        ],
        function=search_annual_reports,
    )

    return [ind_as_tool, ar_tool, build_calculate_tool()] + build_reference_tools(collector)


# ---------------------------------------------------------------------------
# Deterministic calculator (shared financial_math) — the agent delegates ALL
# arithmetic here instead of computing ratios / % / growth itself.
# ---------------------------------------------------------------------------


def build_calculate_tool():
    from yukta_rag.core.financial_math import evaluate

    def calculate(expression: str, variables: dict | None = None) -> str:
        res = evaluate(str(expression), variables or {})
        if res.get("error"):
            return f"calculation error: {res['error']} (expression: {expression})"
        return f"{res['basis']}"  # e.g. "(110-100)/abs(100)*100 = 10.0"

    return create_custom_tool(
        name="calculate",
        description=(
            "Deterministically compute any arithmetic — ratios, percentages, "
            "percentage change / growth, margins, CAGR, differences, sums. ALWAYS "
            "use this instead of doing the arithmetic yourself. Pass the figures you "
            "read from the excerpts as 'variables' and the formula as 'expression'. "
            "Examples: expression='current_assets/current_liabilities' with "
            "variables={\"current_assets\":100000,\"current_liabilities\":25000}; "
            "or expression='(current-prior)/abs(prior)*100' for % change. Only "
            "numbers, + - * / ** %, parentheses, your named variables and the "
            "functions abs/round/min/max/sum are allowed; division by zero returns "
            "an error. Returns the exact result with the substituted arithmetic."
        ),
        parameters=[
            {"name": "expression", "type": "string",
             "description": "The formula, using variable names and + - * / ** % and abs/round/min/max/sum",
             "required": True},
            {"name": "variables", "type": "object",
             "description": "Map of variable name -> number (the figures to compute with)",
             "required": False},
        ],
        function=calculate,
    )


# ---------------------------------------------------------------------------
# Reference-corpora tools (EAC / SA 700 / Schedule III / CARO / Ind AS appendix)
# ---------------------------------------------------------------------------

# (tool name, description, retriever) — each wraps a reference_retrieval fn
_REFERENCE_TOOLS = [
    ("search_eac_opinions",
     "Search ICAI Expert Advisory Committee (EAC) opinions: authoritative views on "
     "how to account for specific, unusual or judgemental accounting issues. Use for "
     "'how should X be treated / accounted for' questions and accounting-treatment reasoning.",
     "eac"),
    ("search_auditing_standards",
     "Search SA 700 (Revised) - Forming an Opinion and Reporting on Financial "
     "Statements. Use for questions about the auditor's report, types of opinion "
     "(unmodified/qualified/adverse/disclaimer), key audit matters and reporting requirements.",
     "sa700"),
    ("search_schedule_iii",
     "Search Companies Act Schedule III - the prescribed presentation format for the "
     "balance sheet and statement of profit and loss (line items, current/non-current "
     "split, disclosure requirements). Use for presentation / classification / FSLI questions.",
     "schedule_iii"),
    ("search_caro",
     "Search CARO 2020 (Companies Auditor's Report Order) and CAG audit directions: "
     "the specific reporting clauses an auditor must address (fixed assets, inventory, "
     "loans, statutory dues, fraud, etc.). Use for audit-clause / compliance-reporting questions.",
     "caro"),
    ("search_ind_as_appendix",
     "Search Ind AS appendices - illustrative examples and application guidance that "
     "accompany the standards. Use for worked examples and application-level clarification.",
     "ind_as_appendix"),
]


def build_reference_tools(collector: list):
    """Yukta tools over the reference corpora, wired to append hits to ``collector``."""
    from yukta_rag.retrieval.reference_retrieval import _RETRIEVERS, format_reference

    def _make(source_key):
        def _fn(query: str, top_k: int = TOP_K) -> str:
            rows = _RETRIEVERS[source_key](query, int(top_k))
            collector.extend(rows)  # already tagged with 'source'
            return format_reference(rows)
        return _fn

    tools = []
    for name, desc, key in _REFERENCE_TOOLS:
        tools.append(create_custom_tool(
            name=name, description=desc,
            parameters=[
                {"name": "query", "type": "string", "description": "Search query", "required": True},
                {"name": "top_k", "type": "integer",
                 "description": "Number of results (default 5)", "required": False},
            ],
            function=_make(key),
        ))
    return tools
