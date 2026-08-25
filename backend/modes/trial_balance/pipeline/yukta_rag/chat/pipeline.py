"""FinanceRAG — Query Planner (3 queries) -> retrieve both corpora -> one synthesis.

The Query Planner turns the user question into 3 search queries (the original plus
2 reformulations). The Research Analyst retrieves from BOTH corpora for every query
(programmatic, in parallel — no per-query LLM call), merges the chunks, and produces
ONE final answer. This collapses the old "3 sub-answers + combine" (8 heavy
generations) down to 2 (query-gen + final), the dominant cost on a single-GPU model
server. List/enumeration questions short-circuit to the Ind AS listing path with no
generation at all.
"""

from __future__ import annotations

import json
import math
import re
from concurrent.futures import ThreadPoolExecutor

from yukta_rag.agents.agents import build_splitter
from yukta_rag.core.config import (
    GENERATION_CONTEXT_TOKENS,
    GENERATION_MAX_OUTPUT_TOKENS,
    TOP_K,
    UPLOAD_TOP_K,
)
from yukta_rag.core.llm import build_llm
from yukta_rag.chat.memory import format_history
from yukta_rag.retrieval.retrieval import (
    _INDAS_RE,
    _order_by_document,
    detect_basis,
    detect_financial_stmt_type,
    list_docs_citing_ind_as,
    list_ind_as_catalog,
    list_ind_as_in_docs,
    resolve_documents,
    retrieve_annual_reports,
    retrieve_ind_as,
)
from yukta_rag.tools.tools import (
    _fmt_ar,
    _fmt_catalog,
    _fmt_docs_citing,
    _fmt_ind_as,
    _fmt_list_in_docs,
    _fmt_uploaded,
    ar_blocks,
    ind_as_blocks,
    uploaded_blocks,
)
from yukta_rag.chat.guardrails import SCOPE_REPLY, check_input, no_evidence_reply
from yukta_rag.core.financial_math import evaluate as _fm_evaluate
from yukta_rag.retrieval.reference_retrieval import reference_blocks, format_reference, retrieve_reference
from yukta_rag.uploads.uploads import retrieve_uploaded

# max uploaded page-chunks blended into one answer (across all planned queries)
UPLOAD_CAP = 8

# reference corpora (EAC / SA 700 / Schedule III / CARO / Ind AS appendix): retrieved
# once per question; only hits above this cosine-similarity floor are blended in, so
# they surface for treatment/presentation/audit questions but not pure metric ones.
REFERENCE_PER_CORPUS_K = 3
REFERENCE_SIM_FLOOR = 0.5
REFERENCE_CAP = 6

# EAC opinions are only relevant to accounting-treatment / accounting-issue / opinion
# questions; otherwise they leak into unrelated answers. They are blended in ONLY when
# the question shows this intent. (The other reference corpora are not gated this way.)
_EAC_INTENT_RE = re.compile(
    r"\beac\b|expert advisory|\bopinion\b|accounting treatment|treatment of|"
    r"how to account|how (should|to|do|does|is|are|would|can|must)\b[^?]*\b"
    r"(account|treat|recognis|recogniz|measur|classif|capitalis|capitaliz|amortis|"
    r"amortiz|depreciat|impair|provision|present|disclos)",
    re.I,
)


def _est_tokens(text: str) -> int:
    """Conservative char-based token estimate (tiktoken isn't installed).

    Uses 3.2 chars/token: financial tables are dense with digits, commas and
    pipe separators that tokenize to MORE tokens per char than prose, so a looser
    ratio would under-count and overflow the model's context window.
    """
    return math.ceil(len(text) / 3.2)


# headroom (tokens) kept free beyond the estimate, to absorb estimator error and
# the chat template's own framing tokens — prevents off-by-a-little 400 overflows.
_TOKEN_SAFETY = 1024

# enumeration intent: a genuine LISTING request, e.g. "list the Ind AS …",
# "list all Ind AS standards", or "which/what Ind AS are mentioned/cited/used in
# <report>". NOT topic identification like "which Ind AS governs leases" (that
# names ONE standard and must go through normal retrieval, not the catalogue).
_LIST_RE = re.compile(
    r"\blist\b[^?]*\bind\s*as\b"
    r"|\bind\s*as\b[^?]*\blist\b"
    # only unambiguous "listed in a report" verbs — NOT "references/governs/applies
    # to <topic>", which name a single standard and must go through retrieval
    r"|\b(which|what)\b[^?]*\bind\s*as\b[^?]*\b("
    r"mentioned|mentions|cited|cites|listed|disclosed|discloses|enumerated)\b",
    re.I,
)
# "which DOCUMENTS cite Ind AS N" — names a document/report AND a specific number
_DOCS_FOR_STD_RE = re.compile(r"\b(document|report|doc|filing)s?\b", re.I)
_STD_NUM_RE = re.compile(r"ind\s*as[\s-]*\d", re.I)

# relative-period references in a follow-up ("previous year value") — resolve them
# against the year discussed in the previous turn (the anchor) instead of guessing.
_REL_PREV_RE = re.compile(
    r"\b(previous|prior|last|preceding|earlier)\s+year\b|\byear\s+before\b"
    r"|\bprior\s+period\b|\bpy\b", re.I)
_REL_NEXT_RE = re.compile(r"\b(next|following|subsequent)\s+year\b", re.I)
_FY_YEAR_RE = re.compile(r"(?:fy\s*)?(20\d{2})\s*[-/]\s*\d{2,4}|\b(20\d{2})\b", re.I)


def _years_in(text: str) -> list[int]:
    return [int(m.group(1) or m.group(2)) for m in _FY_YEAR_RE.finditer(text or "")]

# calculation intent: the question asks for a computed value (ratio / % / growth /
# margin / change / CAGR / per-share / multiple). Such questions get the
# extract -> compute (financial_math) -> narrate path so the ARITHMETIC IS
# DETERMINISTIC (the model never does the division).
_CALC_INTENT_RE = re.compile(
    r"\bratio\b|\bmargin\b|\bpercentage\b|\bpercent\b|%|\bproportion\b|\bgrowth\b"
    r"|\bcagr\b|\byoy\b|year[\s-]*over[\s-]*year|year[\s-]*on[\s-]*year"
    r"|\b(increase|decrease|change|rise|fall|grew|grow|declined?)\b"
    r"|\bper\s+share\b|\btimes\b|\bmultiple\b|how much (more|less|higher|lower)"
    r"|\bdebt[\s-]*(to[\s-]*)?equity\b|\breturn on\b|\bcompute\b|\bcalculate\b",
    re.I,
)

# extraction step: the model ONLY pulls the figures + formula; it must not compute.
_CALC_EXTRACT_SYSTEM = (
    "You prepare a numeric calculation for a deterministic calculator. From the "
    "excerpts, identify the exact figures the question needs and output STRICT JSON "
    "and nothing else:\n"
    '{"label": "<what is being computed>", '
    '"expression": "<formula using variable names and + - * / ** % and abs/round/min/max/sum>", '
    '"variables": {"<name>": <number>, ...}, "unit": "<unit or currency, or empty>"}\n'
    "Rules: use ONLY figures that appear in the excerpts (copy the exact numbers, no "
    "thousands separators); read the correctly LABELLED row and the COLUMN for the "
    "asked year. Numerator and denominator are usually DIFFERENT figures — never reuse "
    "the same number for both (e.g. a current ratio uses 'Total current assets' AND a "
    "separate 'Total current liabilities'; do NOT use a balance-sheet grand total that "
    "is equal on both sides). Variable names are lowercase words; do NOT compute the "
    "result yourself. If the needed distinct figures are not clearly present, output "
    '{"expression": null}.'
)

_ANSWER_SYSTEM = (
    "You are a financial analyst. Answer the question using ONLY the provided "
    "excerpts, citing sources inline exactly as they appear in the brackets "
    "(e.g. (Ind AS 115, p44), (ONGC FY2020-21, p410), (Schedule III, Division I), "
    "(EAC opinion, query 5), (SA 700), or (CARO 2020)). Reference excerpts (EAC "
    "opinions, SA 700, Schedule III, CARO, Ind AS appendix) are authoritative for "
    "accounting-treatment, presentation and audit-reporting questions — use and cite "
    "them when they answer the question; ignore reference excerpts that are off-topic. "
    "Companies apply accounting standards by describing accounting policies and "
    "figures, not by citing the standard's number — present a company's relevant "
    "policy/notes/figures as its application of the standard. Never say a standard "
    "is 'not explicitly mentioned' merely because its number is absent; lead with "
    "the substance you have. Do not invent facts.\n"
    "GROUNDING: If the excerpts genuinely do NOT contain what is needed to answer the "
    "question (wrong company/period, or the figure/topic simply isn't present), reply "
    "with EXACTLY the single token NOT_FOUND and nothing else. Only do this when you "
    "truly cannot answer from the excerpts — if you have the substance, answer normally.\n"
    "SCOPE: Answer ONLY the question asked and include ONLY information directly "
    "relevant to it. Do NOT add recommendations, advice, suggestions, opinions, "
    "action items or next steps, follow-up questions, unrequested background, or "
    "closing caveats / disclaimers / verification notes. Stop as soon as the "
    "question is answered.\n"
    "NUMBERS: When a figure is asked for, read it from the table markdown — find "
    "the row whose label matches and the COLUMN for the requested year (the header "
    "row lists the years), and report that exact value with its unit/currency. If "
    "the precise label is absent, use the closest reported total (e.g. 'Total "
    "Revenues') and say which line it is. By default quote reported figures; do "
    "not invent numbers.\n"
    "CALCULATIONS: If a 'VERIFIED COMPUTATION' block is provided, that result was "
    "computed deterministically — state it as the answer VERBATIM and show its basis; "
    "do NOT recompute or alter it. If NO such block is provided and the question asks "
    "for a ratio / percentage / margin / growth / change, report the underlying "
    "figures from the excerpts and state that the exact computed value is unavailable "
    "rather than doing unreliable mental arithmetic. Use only figures in the excerpts.\n"
    "FORMAT (Markdown): begin with a one-sentence direct answer, then organise the "
    "detail under short bold headings or bullet points. Keep it concise, professional "
    "and well-structured.\n"
    "If a 'Recent conversation' section is provided, use it only to resolve what the "
    "current question refers to (pronouns, follow-ups like 'what about ONGC?'); the "
    "excerpts remain the sole source of facts."
)

_ANSWER_TMPL = """{history}Question: {q}
{computed}
{uploaded}=== Indian Accounting Standards (Ind AS) excerpts ===
{ind}

=== Company annual-report excerpts ===
{ar}
{reference}
Using only the excerpts above, write one clear, complete, well-structured answer
to the question, with inline citations."""

# a deterministic financial_math result the answer MUST use as-is (no re-computation)
_COMPUTED_BLOCK = ("\n=== VERIFIED COMPUTATION (authoritative — use this exact figure; "
                   "do NOT recompute) ===\n{label}: {basis}{unit}\n")

_HISTORY_BLOCK = "=== Recent conversation (for context only) ===\n{hist}\n\n"
# uploaded-document excerpts come FIRST so a user's own PDF leads the context
_UPLOADED_BLOCK = "=== Uploaded document excerpts ===\n{up}\n\n"
# reference corpora (EAC opinions, SA 700, Schedule III, CARO, Ind AS appendix)
_REFERENCE_BLOCK = "\n=== Reference excerpts (EAC / SA 700 / Schedule III / CARO / Ind AS appendix) ===\n{ref}\n"

# Map step (only used when the chunks don't fit the context window): a LOSSLESS,
# question-focused extraction — keeps every relevant detail + citations.
_MAP_SYSTEM = (
    "You are extracting source material, NOT answering yet. From the excerpts, "
    "copy out EVERY fact, figure, policy, definition and statement that could be "
    "relevant to the question. Preserve the inline bracket citations exactly as "
    "given. Do not omit, round, summarise away, or merge details — keep them "
    "verbatim and complete. Output only the extracted points."
)
_MAP_TMPL = "Question: {q}\n\nExcerpts:\n{blocks}\n\nExtract all relevant details (with citations):"


def _est_block_tokens(block: str) -> int:
    return _est_tokens(block) + 4  # + separator overhead


def _batch_blocks(blocks: list[str], budget: int) -> list[list[str]]:
    """Greedily pack rank-ordered blocks into batches under ``budget`` tokens."""
    batches, cur, cur_tok = [], [], 0
    for b in blocks:
        bt = _est_block_tokens(b)
        if cur and cur_tok + bt > budget:
            batches.append(cur)
            cur, cur_tok = [], 0
        cur.append(b)
        cur_tok += bt
    if cur:
        batches.append(cur)
    return batches


def _parse_planner(text: str) -> tuple[str, list[str]]:
    """Extract (scope, reformulated queries) from the planner's JSON output.

    Scope defaults to 'in_scope' when unstated/unparseable — a parsing hiccup must
    never wrongly refuse a legitimate question.
    """
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            qs = [s.strip() for s in data.get("queries", []) if s and s.strip()]
            scope = "out_of_scope" if str(data.get("scope", "")).lower() == "out_of_scope" else "in_scope"
            return scope, qs[:2]
        except (json.JSONDecodeError, AttributeError):
            pass
    return "in_scope", []


def _merge(rows_lists, key_fn, doc_key_fn, pos_key_fn, cap):
    """Dedupe chunks across queries, keep the top ``cap`` by score, then present
    them grouped by document in reading order."""
    seen, merged = set(), []
    for rows in rows_lists:
        for r in rows:
            k = key_fn(r)
            if k in seen:
                continue
            seen.add(k)
            merged.append(r)
    # rank by the boosted score when present (preserves the lexical/keyword boost
    # from retrieve_annual_reports) so literal-match chunks survive the cap
    merged.sort(key=lambda r: r.get("_boosted", r.get("score", 0.0)), reverse=True)
    return _order_by_document(merged[:cap], doc_key_fn, pos_key_fn)


def _hashable(v):
    """Make a value usable in a set/dict key. Reference rows are SELECT * dicts, so a
    field can be a Postgres array (psycopg2 -> list) which is unhashable; coerce
    lists/dicts to tuples recursively, leave scalars as-is."""
    if isinstance(v, (list, tuple)):
        return tuple(_hashable(x) for x in v)
    if isinstance(v, dict):
        return tuple(sorted((k, _hashable(x)) for k, x in v.items()))
    return v


def _dedupe_sources(rows: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in rows:
        key = _hashable((
            r.get("source"),
            r.get("company") or r.get("standard_number"),
            r.get("page") or r.get("page_no"),
            (r.get("content") or r.get("text") or "")[:40],
        ))
        if key in seen:
            continue
        seen.add(key)
        out.append(_serialize_source(r))
    return out


def _serialize_source(r: dict) -> dict:
    """Project a raw retrieval row into a compact JSON-safe source record."""
    if r.get("source") == "upload":
        return {
            "source": "upload",
            "label": r.get("filename") or "Uploaded document",
            "page": r.get("page_no"),
            "score": round(float(r.get("score", 0)), 4),
            "snippet": (r.get("text") or "")[:300],
        }
    if r.get("source") == "ind_as":
        return {
            "source": "ind_as",
            "label": f"Ind AS {r.get('standard_number')} — {r.get('section_title')}",
            "page": r.get("page_no"),
            "paragraph_no": r.get("paragraph_no"),
            "score": round(float(r.get("score", 0)), 4),
            "snippet": (r.get("text") or "")[:300],
        }
    if r.get("source") in ("eac", "sa700", "schedule_iii", "caro", "ind_as_appendix"):
        from yukta_rag.retrieval.reference_retrieval import _tag
        pg = r.get("page_no")
        return {
            "source": r["source"],
            "label": _tag(r).strip("[]"),
            "page": pg if isinstance(pg, int) else None,
            "score": round(float(r.get("score", 0)), 4),
            "snippet": (r.get("text") or "")[:300],
        }
    fy = f"FY{r.get('fy_start')}-{str(r.get('fy_end'))[-2:]}"
    return {
        "source": "annual_report",
        "label": f"{r.get('company')} {fy} ({r.get('kind')})",
        "page": r.get("page"),
        "section": r.get("section"),
        "score": round(float(r.get("score", 0)), 4),
        "snippet": (r.get("content") or "")[:300],
    }


class FinanceRAG:
    """Query-expansion retrieval pipeline over the finance_llm corpora."""

    def __init__(self):
        self.llm = build_llm()
        self.splitter = build_splitter(self.llm)

    @staticmethod
    def _retrieve_both(query: str, doc_ids=None, stmt=None, std_nums=None, skip_ar=False,
                       basis="auto"):
        """Retrieve from both corpora for one query (no LLM).

        ``doc_ids`` / ``stmt`` / ``basis`` scope the annual-report search to a
        company/year, financial-statement type and standalone/consolidated basis.
        ``std_nums`` restricts the Ind AS search to the standard(s) named in the
        question. ``skip_ar`` drops the annual-report corpus entirely — for a pure
        standard-definition question the answer must come from the standard's own
        text, not company applications of it.
        """
        ind_k = TOP_K * 2 if skip_ar else TOP_K  # more standard text when AR is dropped
        ind = retrieve_ind_as(query, ind_k, standard_numbers=std_nums)
        ar = [] if skip_ar else retrieve_annual_reports(
            query, TOP_K, doc_ids=doc_ids, financial_stmt_type=stmt, basis=basis)
        return (ind, ar)

    def _answer_list(self, question: str) -> dict:
        # "which documents cite Ind AS N" — needs a document word AND a number
        if _DOCS_FOR_STD_RE.search(question) and _STD_NUM_RE.search(question):
            res = list_docs_citing_ind_as(question)
            if res is not None:
                return {"answer": _fmt_docs_citing(res), "sub_queries": [question],
                        "sub_answers": [], "sources": []}
        # otherwise "which Ind AS are in <doc>" / catalogue
        in_docs = list_ind_as_in_docs(question)
        answer = _fmt_list_in_docs(in_docs) if in_docs is not None else _fmt_catalog(list_ind_as_catalog())
        return {"answer": answer, "sub_queries": [question], "sub_answers": [], "sources": []}

    def ask(self, question: str, history: list[dict] | None = None,
            upload_doc_ids: list[str] | None = None) -> dict:
        # ``history`` is the recent conversation (oldest first), already bounded
        # to the last few turns by the caller, used to resolve follow-ups.
        # ``upload_doc_ids`` (when set) blends the user's uploaded PDF page chunks
        # into retrieval alongside the Ind AS and annual-report corpora.
        hist_text = format_history(history or [])
        upload_doc_ids = upload_doc_ids or None

        # GUARDRAIL 1 (deterministic, pre-LLM): block prompt-injection / jailbreak and
        # decline investment-advice requests before any retrieval or generation.
        blocked = check_input(question)
        if blocked:
            return {"answer": blocked["answer"], "sub_queries": [question],
                    "sub_answers": [], "sources": [], "guardrail": blocked["kind"]}

        # 0. enumeration questions: list standards directly (no generation).
        #    A specific standard number ("Ind AS 115") signals a substantive
        #    question, not a catalogue list — so only enumerate when no number is
        #    named, or when asking which documents/reports cite that number.
        if _LIST_RE.search(question) and (
            not _STD_NUM_RE.search(question) or _DOCS_FOR_STD_RE.search(question)
        ):
            return self._answer_list(question)

        # 1. plan: the planner classifies SCOPE and generates reformulated queries
        #    (given the conversation, so follow-ups stay in scope + self-contained).
        planner_input = (
            f"Recent conversation:\n{hist_text}\n\nCurrent question: {question}"
            if hist_text else question
        )
        gen = self.splitter.run(planner_input, reset_conversation=True)
        scope, extra_queries = _parse_planner(gen.get("response", ""))

        # GUARDRAIL 2 (scope): off-domain questions get the capabilities reply.
        if scope == "out_of_scope":
            return {"answer": SCOPE_REPLY, "sub_queries": [question],
                    "sub_answers": [], "sources": [], "guardrail": "out_of_scope"}

        queries: list[str] = [question]
        for q in extra_queries:
            if q.lower() not in {x.lower() for x in queries}:
                queries.append(q)

        # metadata scope — conversation-aware so a follow-up that omits the company
        # ("what was the previous year value?") still scopes to the right report.
        own = resolve_documents(question)
        prior_users = " ".join(t.get("question", "") for t in (history or []))
        if own:
            scope_docs, scope_text = own, question
        else:
            # follow-up: resolve the company from the recent user turns, then from
            # the planner's (self-contained) reformulations.
            scope_text = f"{prior_users}\n{question}".strip()
            scope_docs = resolve_documents(scope_text) or \
                next((d for q in queries[1:] if (d := resolve_documents(q))), [])

        # relative-year: "previous/next year" shifts the target off the anchor year
        # (the year discussed in the last turn), so we fetch the correct report
        # rather than drifting to an unrelated year.
        if scope_docs and not _years_in(question):
            delta = -1 if _REL_PREV_RE.search(question) else (1 if _REL_NEXT_RE.search(question) else 0)
            if delta and history:
                anchor = max(_years_in(history[-1].get("question", ""))
                             or _years_in(prior_users), default=None)
                if anchor:
                    company, tgt = scope_docs[0][1], anchor + delta
                    retarget = resolve_documents(f"{company} FY{tgt}-{str(tgt + 1)[-2:]}")
                    if retarget:
                        scope_docs = retarget

        doc_filter = [d[0] for d in scope_docs] or None
        # statement type / basis inherit from the conversation when the follow-up omits them
        stmt = detect_financial_stmt_type(question) or detect_financial_stmt_type(scope_text)
        # standalone vs consolidated decided ONCE (question first, else the context)
        basis = detect_basis(question) or detect_basis(scope_text)

        # standards named in the question (e.g. "Ind AS 115") restrict the Ind AS
        # search to that standard's own text. A question that names a standard but
        # no company is a standard-DEFINITION question — answer it purely from the
        # standard, dropping annual-report (company-application) excerpts.
        std_nums = sorted({int(n) for n in _INDAS_RE.findall(question)}) or None
        standard_focused = bool(std_nums) and not doc_filter

        # 2. retrieve every corpus for every query, concurrently (cheap, no LLM).
        #    Uploaded page chunks (when a doc is active) are fetched in the same
        #    fan-out so they add no extra latency.
        def _retrieve_all(q):
            ind, ar = self._retrieve_both(q, doc_filter, stmt, std_nums,
                                          standard_focused, basis)
            up = retrieve_uploaded(q, upload_doc_ids, UPLOAD_TOP_K) if upload_doc_ids else []
            return ind, ar, up

        with ThreadPoolExecutor(max_workers=len(queries)) as pool:
            per = list(pool.map(_retrieve_all, queries))

        up_rows = _merge(
            [p[2] for p in per],
            key_fn=lambda r: (r["doc_id"], r["page_no"]),
            doc_key_fn=lambda r: r["doc_id"],
            pos_key_fn=lambda r: r["page_no"],
            cap=UPLOAD_CAP,
        ) if upload_doc_ids else []

        ind_cap, ar_cap = (16, 0) if standard_focused else (10, 14)
        ind_rows = _merge(
            [p[0] for p in per],
            key_fn=lambda r: (r["standard_number"], r["page_no"], (r["text"] or "")[:60]),
            doc_key_fn=lambda r: r["standard_number"],
            pos_key_fn=lambda r: r["seq"],
            cap=ind_cap,
        )
        ar_rows = _merge(
            [p[1] for p in per],
            key_fn=lambda r: (r["company"], r["fy_start"], r["page"], r["kind"], (r["content"] or "")[:60]),
            doc_key_fn=lambda r: (r["company"], r["fy_start"]),
            pos_key_fn=lambda r: (r["page"], r["pos_id"] or ""),
            cap=ar_cap,
        )

        # 2b. reference corpora pass (once, on the original question). Hits above the
        #     similarity floor are blended in. EAC opinions are additionally held back
        #     unless the question is about an accounting treatment / issue / opinion,
        #     so they don't leak into unrelated answers. Other reference corpora
        #     (SA 700 / Schedule III / CARO / Ind AS appendix) are unaffected.
        ref_rows = [r for r in retrieve_reference(question, REFERENCE_PER_CORPUS_K)
                    if (r.get("score") or 0) >= REFERENCE_SIM_FLOOR]
        if not _EAC_INTENT_RE.search(question):
            ref_rows = [r for r in ref_rows if r.get("source") != "eac"]
        ref_rows = sorted(ref_rows, key=lambda r: r.get("score", 0.0), reverse=True)[:REFERENCE_CAP]

        # 2c. deterministic calculation: for a calc question, extract the figures
        #     with the model then compute with financial_math (the model never does
        #     the arithmetic). The verified result is handed to the synthesis.
        computed = None
        if _CALC_INTENT_RE.search(question):
            computed = self._extract_and_compute(question, ind_rows, ar_rows, up_rows, ref_rows)

        # GUARDRAIL 3 (no evidence): if nothing was retrieved from ANY corpus, don't
        # ask the model to answer from thin air — return the canned not-found reply.
        if not (ind_rows or ar_rows or up_rows or ref_rows):
            return {"answer": no_evidence_reply(), "sub_queries": queries,
                    "sub_answers": [], "sources": [], "guardrail": "no_evidence"}

        # 3. synthesize the answer (direct if it fits the context, else map-reduce)
        final = self._synthesize(question, ind_rows, ar_rows, hist_text, up_rows, ref_rows, computed)

        # GUARDRAIL 3 (grounding): the excerpts didn't contain the answer -> the model
        # was told to emit exactly NOT_FOUND; convert that to the canned reply.
        if final.strip().upper().startswith("NOT_FOUND"):
            return {"answer": no_evidence_reply(), "sub_queries": queries,
                    "sub_answers": [], "sources": [], "guardrail": "no_evidence"}

        sources = _dedupe_sources(
            [{**r, "source": "upload"} for r in up_rows]
            + [{**r, "source": "ind_as"} for r in ind_rows]
            + [{**r, "source": "annual_report"} for r in ar_rows]
            + ref_rows  # already tagged with their source (eac/sa700/schedule_iii/caro/…)
        )
        return {"answer": final, "sub_queries": queries, "sub_answers": [],
                "sources": sources, "computed": computed, "guardrail": None}

    def _generate(self, system: str, user: str) -> str:
        return self.llm.generate(messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]).content

    def _extract_and_compute(self, question: str, ind_rows, ar_rows, up_rows, ref_rows) -> dict | None:
        """Extract calculation inputs from the excerpts, then compute deterministically.

        The model ONLY returns {label, expression, variables, unit}; the arithmetic
        is done by ``financial_math.evaluate``. Returns the computed record, or None
        if the figures aren't present / extraction fails (the answer then falls back
        to the normal synthesis).
        """
        context = "\n\n".join(filter(None, [
            _fmt_uploaded(up_rows or []) if up_rows else "",
            _fmt_ar(ar_rows), _fmt_ind_as(ind_rows),
            format_reference(ref_rows) if ref_rows else "",
        ]))
        user = (f"Question: {question}\n\nExcerpts:\n{context}\n\n"
                "Return the calculation JSON.")
        try:
            raw = self._generate(_CALC_EXTRACT_SYSTEM, user)
        except Exception:  # noqa: BLE001 - extraction is best-effort
            return None
        m = re.search(r"\{.*\}", raw or "", re.DOTALL)
        if not m:
            return None
        try:
            spec = json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            return None
        expr = spec.get("expression")
        variables = spec.get("variables") or {}
        if not expr or not isinstance(variables, dict):
            return None
        # coerce variable values to numbers (strip any stray separators)
        clean_vars = {}
        for k, v in variables.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                clean_vars[k] = v
            elif isinstance(v, str):
                try:
                    clean_vars[k] = float(v.replace(",", "").strip())
                except ValueError:
                    continue
        res = _fm_evaluate(str(expr), clean_vars)
        if res.get("result") is None:
            return None
        return {"label": spec.get("label") or "result", "unit": spec.get("unit") or "",
                "result": res["result"], "basis": res["basis"]}

    def _synthesize(self, question: str, ind_rows: list[dict], ar_rows: list[dict],
                    hist_text: str = "", up_rows: list[dict] | None = None,
                    ref_rows: list[dict] | None = None, computed: dict | None = None) -> str:
        """Answer from the retrieved chunks; fall back to batch map-reduce only
        when the direct prompt would exceed the model's context window.

        ``up_rows`` are uploaded-PDF page chunks; when present they lead the
        excerpts so the user's own document anchors the answer. ``ref_rows`` are
        reference-corpora hits (EAC / SA 700 / Schedule III / CARO / Ind AS appendix).
        ``computed`` is a deterministic financial_math result the answer must use
        verbatim instead of doing arithmetic.
        """
        up_rows = up_rows or []
        ref_rows = ref_rows or []
        hist = _HISTORY_BLOCK.format(hist=hist_text) if hist_text else ""
        uploaded = _UPLOADED_BLOCK.format(up=_fmt_uploaded(up_rows)) if up_rows else ""
        reference = _REFERENCE_BLOCK.format(ref=format_reference(ref_rows)) if ref_rows else ""
        comp = ""
        if computed:
            unit = f" {computed['unit']}" if computed.get("unit") else ""
            comp = _COMPUTED_BLOCK.format(
                label=computed["label"], basis=computed["basis"], unit=unit)
        prompt = _ANSWER_TMPL.format(history=hist, q=question, uploaded=uploaded,
                                     ind=_fmt_ind_as(ind_rows), ar=_fmt_ar(ar_rows),
                                     reference=reference, computed=comp)
        budget = GENERATION_CONTEXT_TOKENS - GENERATION_MAX_OUTPUT_TOKENS - _TOKEN_SAFETY

        if _est_tokens(_ANSWER_SYSTEM) + _est_tokens(prompt) <= budget:
            return self._generate(_ANSWER_SYSTEM, prompt)  # fits -> single call (unchanged path)

        # overflow -> losslessly extract details batch-by-batch, then answer.
        # Uploaded blocks are folded into the extraction so their detail survives.
        blocks = (uploaded_blocks(up_rows) + ind_as_blocks(ind_rows) + ar_blocks(ar_rows)
                  + reference_blocks(ref_rows))
        # per-batch input budget reserves room for the map system prompt, the map
        # template's own framing (question + wrapper), the model output, and safety
        map_overhead = _est_tokens(_MAP_SYSTEM) + _est_tokens(_MAP_TMPL.format(q=question, blocks=""))
        batch_budget = GENERATION_CONTEXT_TOKENS - GENERATION_MAX_OUTPUT_TOKENS - map_overhead - _TOKEN_SAFETY
        for _ in range(3):  # recurse: re-extract if extractions still overflow
            extractions = []
            for batch in _batch_blocks(blocks, batch_budget):
                user = _MAP_TMPL.format(q=question, blocks="\n\n---\n\n".join(batch))
                extractions.append(self._generate(_MAP_SYSTEM, user))
            combined = "\n\n".join(extractions)
            reduce_prompt = _ANSWER_TMPL.format(history=hist, q=question, uploaded="",
                                                ind=combined,
                                                ar="(see extracted points above)", reference="",
                                                computed=comp)
            if _est_tokens(_ANSWER_SYSTEM) + _est_tokens(reduce_prompt) <= budget:
                return self._generate(_ANSWER_SYSTEM, reduce_prompt)
            blocks = extractions  # still too big: extract again over the extractions
        # safety: if it never fit, answer over the final extractions anyway
        return self._generate(_ANSWER_SYSTEM, reduce_prompt)
