"""Retrieval over the Ind AS and annual-report corpora.

Ind AS: dense (pgvector) search over ``ind_as_chunks``.

Annual reports: a hybrid that combines
  * a dense vector pass over ``text_chunks`` + ``table_chunks``, on a query that
    is *expanded* with the subject of any "Ind AS N" it references
    (e.g. "Ind AS 2" -> "... Inventories"), and
  * a lexical pass that pulls chunks literally containing the subject keywords
    (or a cited "Ind AS-N"), ranked by vector similarity,
so company disclosures surface even though annual reports describe accounting by
topic rather than by standard number.
"""

from __future__ import annotations

import re

from yukta_rag.core.db import get_connection
from yukta_rag.core.embeddings import to_vector_literal
from yukta_rag.retrieval.indas_subjects import IND_AS_SUBJECTS

_INDAS_RE = re.compile(r"ind\s*as[\s\-]*?(\d{1,3})", re.I)
_STOPWORDS = {"the", "of", "and", "in", "for", "to", "a", "an", "on", "as"}
# generic terms dropped when deriving lexical keywords from the user's query
_QUERY_STOP = {
    "accounting", "policy", "policies", "report", "reports", "annual", "company",
    "companies", "financial", "statement", "statements", "related", "value", "values",
    "amount", "amounts", "under", "about", "detail", "details", "regarding", "which",
    "information", "during", "between", "please", "provide", "total", "their", "there",
    "what", "where", "list", "show", "given", "year", "years", "assets",
}

_IND_AS_SQL = """
    SELECT d.standard_number, d.document_title, c.section_title, c.paragraph_no,
           c.page_no, c.seq, c.text,
           1 - (c.embedding <=> %s::vector) AS score
    FROM ind_as_chunks c
    JOIN ind_as_documents d ON d.doc_id = c.doc_id
    WHERE c.embedding IS NOT NULL{filt}
    ORDER BY c.embedding <=> %s::vector
    LIMIT %s
"""

# {filt} is replaced with an optional "AND dc.doc_id = ANY(%s)" company/year scope.
_AR_TEXT_SQL = """
    SELECT 'text' AS kind, dc.company, dc.fy_start, dc.fy_end, dc.doc_name,
           c.section, c.page_ocr_start AS page, c.chunk_id AS pos_id, c.content,
           1 - (c.embedding <=> %s::vector) AS score
    FROM text_chunks c
    JOIN documents dc ON dc.doc_id = c.doc_id
    WHERE c.embedding IS NOT NULL{filt}
    ORDER BY c.embedding <=> %s::vector
    LIMIT %s
"""

# The lexical pass SQL is built dynamically in retrieve_annual_reports (it counts
# how many query/subject terms each chunk matches, to rank literal hits).

# {filt} = company/year scope; {tfilt} = optional financial-statement-type narrow.
_AR_TABLE_SQL = """
    SELECT 'table' AS kind, dc.company, dc.fy_start, dc.fy_end, dc.doc_name,
           t.section, t.page_ocr_start AS page, t.table_id AS pos_id,
           NULLIF(TRIM(BOTH ': ' FROM COALESCE(t.table_title, '') || ': '
                  || COALESCE(t.table_description, '')), '') AS content,
           t.table_md, t.unit, t.currency,
           1 - (t.description_embedding <=> %s::vector) AS score
    FROM table_chunks t
    JOIN documents dc ON dc.doc_id = t.doc_id
    WHERE t.description_embedding IS NOT NULL{filt}{tfilt}
    ORDER BY t.description_embedding <=> %s::vector
    LIMIT %s
"""


def _query(sql: str, params: tuple) -> list[dict]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [c.name for c in cur.description]
            rows = cur.fetchall()
    finally:
        conn.close()
    return [dict(zip(cols, r)) for r in rows]


def _order_by_document(rows, doc_key_fn, pos_key_fn, score_fn=None):
    """Reorder selected rows into reading order without changing selection.

    Documents are ordered by their strongest hit (so the most relevant document
    leads); within a document, chunks are ordered by reading position.
    """
    if score_fn is None:
        score_fn = lambda r: r["score"]
    best: dict = {}
    for r in rows:
        k = doc_key_fn(r)
        s = score_fn(r)
        if k not in best or s > best[k]:
            best[k] = s
    return sorted(rows, key=lambda r: (-best[doc_key_fn(r)], doc_key_fn(r), pos_key_fn(r)))


def expand_ar_query(query: str) -> tuple[str, list[str]]:
    """Expand a query with Ind AS subjects and return ``(expanded, keywords)``.

    ``keywords`` are ILIKE patterns for the lexical pass: the subject of each
    referenced standard plus the literal "Ind AS N"/"Ind AS-N" citation forms.
    """
    nums = [int(n) for n in _INDAS_RE.findall(query)]
    subjects = [IND_AS_SUBJECTS[n] for n in nums if n in IND_AS_SUBJECTS]

    expanded = query
    if subjects:
        expanded = f"{query} ({'; '.join(subjects)})"

    # "specific" keywords (Ind AS subjects + citation forms) are cheap/precise and
    # safe to use even on a global search.
    specific: list[str] = []
    for subj in subjects:
        for w in re.split(r"[^A-Za-z]+", subj):
            if len(w) > 3 and w.lower() not in _STOPWORDS:
                specific.append(f"%{w}%")
    for n in nums:
        specific.append(f"%Ind AS {n}%")
        specific.append(f"%Ind AS-{n}%")
    # "query terms" are broad (e.g. %revenue%) — only useful when the search is
    # scoped to a company, else they match hundreds of thousands of rows.
    qterms: list[str] = []
    for w in re.findall(r"[A-Za-z][A-Za-z-]{4,}", query):
        if w.lower() not in _QUERY_STOP:
            qterms.append(f"%{w}%")

    def _dedupe(items):
        seen, out = set(), []
        for k in items:
            if k.lower() not in seen:
                seen.add(k.lower())
                out.append(k)
        return out

    return expanded, _dedupe(specific), _dedupe(qterms)


def retrieve_ind_as(query: str, top_k: int = 5,
                    standard_numbers: list[int] | None = None,
                    vec: str | None = None) -> list[dict]:
    """Return the top_k Ind AS chunks (selected by relevance, presented in order).

    When ``standard_numbers`` is given, the dense search is restricted to those
    standards — so a question that names "Ind AS 115" retrieves that standard's
    own text rather than a scatter of paragraphs across the whole corpus that
    merely mention it.

    ``vec`` is an optional precomputed pgvector literal for ``query`` (callers
    batching many queries embed once and pass it in).
    """
    vec = vec or to_vector_literal(query)
    if standard_numbers:
        sql = _IND_AS_SQL.format(filt=" AND d.standard_number = ANY(%s)")
        rows = _query(sql, (vec, list(standard_numbers), vec, top_k))
    else:
        sql = _IND_AS_SQL.format(filt="")
        rows = _query(sql, (vec, vec, top_k))
    return _order_by_document(
        rows,
        doc_key_fn=lambda r: r["standard_number"],
        pos_key_fn=lambda r: r["seq"],
    )


# lexical (topic-matched) hits are boosted for ordering so they rank above
# generic semantic matches when an Ind AS subject is involved.
_KEYWORD_BOOST = 0.15

# minimum number of financial-table chunks guaranteed a place in annual-report
# results, so numerical questions always see real cell values (tables score low).
_TABLE_FLOOR = 4
# how many exact-metric answer tables to PIN (force into results regardless of score)
_PIN_MAX = 2

# Title keywords for the primary financial statements. When a question targets a
# statement, the table holding its headline totals (total assets, total revenue…)
# is matched by TITLE — the stored financial_stmt_type is unreliable (e.g. some
# P&L tables are mislabelled balance_sheet) — and strongly boosted so it is never
# crowded out by higher-scoring prose or note tables.
_STMT_TITLE_KW = {
    "balance_sheet": ["balance sheet", "statement of financial position"],
    "profit_loss": ["profit and loss", "profit or loss", "statement of profit",
                    "statement of income", "income and retained"],
    "cash_flow": ["cash flow"],
    "statement_of_equity": ["changes in equity"],
}
_CANON_BOOST = 0.5

# words dropped when deriving the metric phrase a numerical question asks about
# (kept: total, assets, income, revenue, … the financial nouns that name a row).
_METRIC_STOP = {
    "what", "were", "was", "is", "are", "the", "of", "as", "at", "on", "in", "to",
    "for", "and", "or", "a", "an", "be", "been", "how", "much", "many", "value",
    "values", "amount", "amounts", "figure", "figures", "basis", "please", "provide",
    "give", "tell", "show", "company", "companys", "report", "annual", "during",
    "year", "years", "ended", "ending", " end", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december", "crore",
    "crores", "lakh", "lakhs", "million", "billion", "rs", "inr", "their", "its",
}
_METRIC_NOUNS = {
    "assets", "liabilities", "equity", "income", "revenue", "revenues", "profit",
    "loss", "expenses", "expenditure", "borrowings", "debt", "reserves", "surplus",
    "dividend", "tax", "ebitda", "turnover", "sales", "capital", "worth", "cash",
}


_METRIC_QUAL = {"total", "net", "gross"}


def _table_metric_phrases(query: str) -> list[str]:
    """Metric phrases to match against table_md, MOST SPECIFIC FIRST.

    A "total/net/gross <noun>" bigram (e.g. "total assets") names a statement's
    headline row and is the strongest signal, so it leads; broader noun bigrams
    and bare financial nouns follow for recall. ``phrases[0]`` is the salient
    phrase used to order the exact answer table to the top.
    """
    toks = [t for t in re.findall(r"[a-z]+", query.lower())
            if len(t) > 2 and t not in _METRIC_STOP]
    bigrams = [f"{toks[i]} {toks[i+1]}" for i in range(len(toks) - 1)]
    primary = [p for p in bigrams
               if (set(p.split()) & _METRIC_QUAL) and (set(p.split()) & _METRIC_NOUNS)]
    metric_bi = [p for p in bigrams if p not in primary and (set(p.split()) & _METRIC_NOUNS)]
    nouns = [t for t in toks if t in _METRIC_NOUNS]
    other = [p for p in bigrams if p not in primary and p not in metric_bi]
    seen, out = set(), []
    for p in primary + metric_bi + nouns + other:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out[:6]

_STMT_PATTERNS = [
    ("balance_sheet",
     r"balance\s*sheet|statement of financial position|total\s+assets|total\s+liabilit"
     r"|total\s+equity|net\s+worth|shareholders?'?\s+funds?|non[-\s]current\s+asset"
     r"|current\s+(asset|liabilit)|borrowing|inventor|trade\s+(receivable|payable)"
     r"|provision|investment|reserves|share\s+capital|lease\s+liabilit"
     # derived balance-sheet ratios — pull the statement so both numerator and
     # denominator are available for the model to compute
     r"|current\s+ratio|quick\s+ratio|debt[-\s]*(to[-\s]*)?equity|working\s+capital"),
    ("cash_flow", r"cash\s*flow|operating activities|investing activities|financing activities"),
    ("statement_of_equity", r"changes in equity|statement of equity"),
    ("profit_loss",
     r"profit\s*(and|&|or)?\s*loss|p\s*&\s*l|income statement|statement of profit"
     r"|total income|total revenue|revenue from operations|earnings per share|\beps\b"
     r"|net\s+profit|profit\s+(before|after|for)|finance\s+cost|tax\s+expense"
     r"|total\s+expense|other\s+income|deprecia|depletion|amortis|\bebitda\b"
     # derived P&L ratios/metrics
     r"|profit\s+margin|pbt\s+margin|pat\s+margin|net\s+margin|operating\s+margin"),
]


def detect_financial_stmt_type(query: str) -> str | None:
    """Map a query to a financial_stmt_type value, or None."""
    q = query.lower()
    for stmt, pat in _STMT_PATTERNS:
        if re.search(pat, q):
            return stmt
    return None


def detect_basis(query: str) -> str | None:
    """'consolidated' / 'standalone' if the query names one, else None.

    None means 'not specified' — the retriever then PREFERS standalone (the
    primary statements these reports default to) without excluding other tables.
    """
    q = query.lower()
    if "consolidated" in q:
        return "consolidated"
    if "standalone" in q or "stand alone" in q:
        return "standalone"
    return None


def retrieve_annual_reports(query: str, top_k: int = 5, doc_ids: list[str] | None = None,
                            financial_stmt_type: str | None = None,
                            basis: str = "auto") -> list[dict]:
    """Hybrid (dense + lexical) annual-report search, scoped by optional metadata.

    ``doc_ids`` restricts to a company/year document set; ``financial_stmt_type``
    narrows the table search to that financial statement. ``basis`` is
    'standalone' / 'consolidated' / None; the default 'auto' detects it from the
    query. The caller passes an explicit basis (from the ORIGINAL question) so a
    reformulated query can't flip standalone<->consolidated mid-retrieval.
    """
    if basis == "auto":
        basis = detect_basis(query)
    expanded, specific_kw, query_kw = expand_ar_query(query)
    # broad query-term keywords only when scoped (else they scan most of the corpus)
    keywords = (specific_kw + query_kw if doc_ids else specific_kw)[:10]
    vec = to_vector_literal(expanded)

    # optional company/year scope (documents.doc_id) and table statement filter
    filt = " AND dc.doc_id = ANY(%s)" if doc_ids else ""
    tfilt = " AND t.is_financial AND t.financial_stmt_type = %s" if financial_stmt_type else ""
    doc_p = [doc_ids] if doc_ids else []
    stmt_p = [financial_stmt_type] if financial_stmt_type else []

    text_sql = _AR_TEXT_SQL.format(filt=filt)
    table_sql = _AR_TABLE_SQL.format(filt=filt, tfilt=tfilt)

    # (boost, row); pinned_keys = exact-metric answer tables forced into results;
    # primary_keys = the authoritative primary statements (BS/P&L/…) kept alongside
    tagged: list[tuple[float, dict]] = []
    pinned_keys: set = set()
    primary_keys: set = set()
    for r in _query(text_sql, (vec, *doc_p, vec, top_k)):
        tagged.append((0.0, r))
    for r in _query(table_sql, (vec, *doc_p, *stmt_p, vec, max(2, top_k // 2))):
        if r["content"]:
            tagged.append((0.0, r))
    if keywords:
        # lexical pass: rank by how MANY query/subject terms a chunk contains
        cnt = " + ".join(["(c.content ILIKE %s)::int"] * len(keywords))
        kw_sql = f"""
            SELECT 'text' AS kind, dc.company, dc.fy_start, dc.fy_end, dc.doc_name,
                   c.section, c.page_ocr_start AS page, c.chunk_id AS pos_id, c.content,
                   1 - (c.embedding <=> %s::vector) AS score, ({cnt}) AS kwmatches
            FROM text_chunks c
            JOIN documents dc ON dc.doc_id = c.doc_id
            WHERE c.embedding IS NOT NULL AND c.content ILIKE ANY(%s){filt}
            ORDER BY kwmatches DESC, c.embedding <=> %s::vector
            LIMIT %s
        """
        params = (vec, *keywords, keywords, *doc_p, vec, top_k)
        for r in _query(kw_sql, params):
            tagged.append((_KEYWORD_BOOST * max(1, r.get("kwmatches") or 0), r))

    # primary-statements pass (company-scoped financial question): always pull the
    # AUTHORITATIVE Balance Sheet and Statement of Profit & Loss (plus cash-flow /
    # equity when asked) by title, for the requested basis, picking the FULL
    # statement (most rows) over summaries / notes / "at a glance" / reconciliations.
    # This guarantees the P&L is present even when its stmt_type is mislabelled or
    # the asked metric ("net profit") doesn't match the statement's row label, and
    # covers questions spanning several statements. Kept (never dropped) via
    # ``primary_keys``.
    phrases = _table_metric_phrases(query) if doc_ids else []
    if phrases:
        prim_bl = "%consolidated%" if basis == "consolidated" else "%standalone%"
        prim_specs = [
            (["%balance sheet%", "%statement of financial position%"], "%balance sheet%"),
            (["%profit and loss%", "%profit or loss%", "%statement of profit%"], "%statement of profit%"),
        ]
        if financial_stmt_type == "cash_flow":
            prim_specs.append((["%cash flow%"], "%cash flow%"))
        elif financial_stmt_type == "statement_of_equity":
            prim_specs.append((["%changes in equity%"], "%changes in equity%"))
        for where_kws, header_kw in prim_specs:
            likes = " OR ".join(
                ["(t.table_title ILIKE %s OR t.section ILIKE %s)"] * len(where_kws))
            lp = []
            for kw in where_kws:
                lp += [kw, kw]
            prim_sql = f"""
                SELECT 'table' AS kind, dc.company, dc.fy_start, dc.fy_end, dc.doc_name,
                       t.section, t.page_ocr_start AS page, t.table_id AS pos_id,
                       NULLIF(TRIM(BOTH ': ' FROM COALESCE(t.table_title, '') || ': '
                              || COALESCE(t.table_description, '')), '') AS content,
                       t.table_md, t.unit, t.currency,
                       1 - (t.description_embedding <=> %s::vector) AS score
                FROM table_chunks t
                JOIN documents dc ON dc.doc_id = t.doc_id
                WHERE t.is_financial AND t.doc_id = ANY(%s) AND ({likes})
                ORDER BY (t.section ILIKE %s OR COALESCE(t.table_title, '') ILIKE %s) DESC,
                         (COALESCE(t.table_title, '') ILIKE %s) DESC,
                         COALESCE(t.row_count, 0) DESC,
                         t.description_embedding <=> %s::vector
                LIMIT 1
            """
            for r in _query(prim_sql, (vec, doc_ids, *lp, prim_bl, prim_bl, header_kw, vec)):
                if r.get("table_md") or r.get("content"):
                    tagged.append((_CANON_BOOST, r))
                    primary_keys.add(
                        (r["company"], r["fy_start"], r["page"], "table", (r["content"] or "")[:60]))

    # metric pass (company-scoped only): find the financial table(s) whose cells
    # literally contain the asked metric (e.g. "total assets"), preferring the
    # requested basis (standalone / consolidated). This pins the exact answer
    # table even when statement titles/labels are ambiguous or mislabelled.
    if doc_ids:
        if phrases:
            # Explicit basis -> RESTRICT to that basis (wrong-basis statement can't
            # be pinned). No basis stated -> PREFER standalone (the primary
            # statements; what these reports/benchmarks default to) without
            # excluding note tables that omit the word "standalone".
            if basis == "consolidated":
                basis_like, hard = "%consolidated%", True
            elif basis == "standalone":
                basis_like, hard = "%standalone%", True
            else:
                basis_like, hard = "%standalone%", False
            basis_where, basis_order, basis_p = "", "", []
            if hard:
                basis_where = (" AND (t.table_md ILIKE %s OR t.section ILIKE %s "
                               "OR COALESCE(t.table_title,'') ILIKE %s)")
                basis_p = [basis_like, basis_like, basis_like]
            else:
                basis_order = ("(t.section ILIKE %s OR COALESCE(t.table_title,'') ILIKE %s) "
                               "DESC, ")
                basis_p = [basis_like, basis_like]
            # prefer the PRIMARY statement table (exact figures) over summary /
            # "performance at a glance" tables of the same statement type
            stmt_pref, stmt_pref_p = "", []
            canon = _STMT_TITLE_KW.get(financial_stmt_type or "", [])
            if canon:
                conds = " OR ".join(
                    ["t.section ILIKE %s OR COALESCE(t.table_title, '') ILIKE %s"] * len(canon))
                stmt_pref = f"({conds}) DESC, "
                for kw in canon:
                    stmt_pref_p += [f"%{kw}%", f"%{kw}%"]
            # order the table literally containing the salient metric phrase first
            salient = f"%{phrases[0]}%"
            metric_sql = f"""
                SELECT 'table' AS kind, dc.company, dc.fy_start, dc.fy_end, dc.doc_name,
                       t.section, t.page_ocr_start AS page, t.table_id AS pos_id,
                       NULLIF(TRIM(BOTH ': ' FROM COALESCE(t.table_title, '') || ': '
                              || COALESCE(t.table_description, '')), '') AS content,
                       t.table_md, t.unit, t.currency,
                       1 - (t.description_embedding <=> %s::vector) AS score
                FROM table_chunks t
                JOIN documents dc ON dc.doc_id = t.doc_id
                WHERE t.is_financial AND t.doc_id = ANY(%s) AND t.table_md ILIKE ANY(%s){basis_where}
                ORDER BY {basis_order}{stmt_pref}(t.table_md ILIKE %s) DESC,
                         t.description_embedding <=> %s::vector
                LIMIT 4
            """
            mrows = _query(metric_sql,
                           (vec, doc_ids, [f"%{p}%" for p in phrases],
                            *basis_p, *stmt_pref_p, salient, vec))
            for i, r in enumerate(mrows):
                tagged.append((_CANON_BOOST + 0.1, r))
                if i < _PIN_MAX:
                    pinned_keys.add(
                        (r["company"], r["fy_start"], r["page"], "table", (r["content"] or "")[:60]))

    # merge by unique chunk, keeping the highest boost seen for it
    best: dict[tuple, tuple[float, dict]] = {}
    for boost, r in tagged:
        key = (r["company"], r["fy_start"], r["page"], r["kind"], (r["content"] or "")[:60])
        prev = best.get(key)
        keep = r if (prev is None or r["score"] > prev[1]["score"]) else prev[1]
        best[key] = (max(boost, prev[0]) if prev else boost, keep)

    # boosted score drives selection AND per-document ranking
    rows = []
    for boost, r in best.values():
        r["_boosted"] = r["score"] + boost
        rows.append(r)

    # select the most relevant chunks. Financial tables embed on their (structural)
    # description and routinely score below prose, so a numerical question would
    # otherwise drop every table — reserve slots for the top tables so the actual
    # cell values reach the model.
    ranked = sorted(rows, key=lambda r: r["_boosted"], reverse=True)
    budget = top_k + 4

    def _key(r):
        return (r["company"], r["fy_start"], r["page"], r["kind"], (r["content"] or "")[:60])

    # KEEP = the exact-metric answer table(s) + the authoritative primary
    # statements; DROP only the other (summary / wrong-basis / redundant) tables so
    # the model reads the right figures but still has the full statements it needs.
    pinned = [r for r in ranked if _key(r) in pinned_keys][:_PIN_MAX]
    pinned_ids = {id(r) for r in pinned}
    primary = [r for r in ranked if _key(r) in primary_keys and id(r) not in pinned_ids]
    keep = (pinned + primary)[:budget]
    keep_ids = {id(r) for r in keep}
    txts = [r for r in ranked if r["kind"] != "table" and id(r) not in keep_ids]
    if keep:
        selected = keep + txts[: max(0, budget - len(keep))]
    else:
        # no statement/metric tables: reserve slots for the top tables so a
        # numerical question still sees real cell values (tables score below prose).
        tabs = [r for r in ranked if r["kind"] == "table"]
        k_tab = min(len(tabs), _TABLE_FLOOR)
        selected = tabs[:k_tab] + txts[: budget - k_tab]
    if len(selected) < budget:  # backfill with any remaining rows
        chosen = {id(r) for r in selected}
        selected += [r for r in ranked if id(r) not in chosen][: budget - len(selected)]
    return _order_by_document(
        selected,
        doc_key_fn=lambda r: (r["company"], r["fy_start"]),
        pos_key_fn=lambda r: (r["page"], r["pos_id"] or ""),
        score_fn=lambda r: r["_boosted"],
    )


# ---------------------------------------------------------------------------
# Listing / enumeration (mode='list' on the Ind AS tool)
# ---------------------------------------------------------------------------


def list_ind_as_catalog() -> list[tuple[int, str]]:
    """Return all ingested Ind AS standards as ``(number, subject)`` pairs."""
    rows = _query_raw("SELECT standard_number FROM ind_as_documents ORDER BY standard_number", ())
    return [(n, IND_AS_SUBJECTS.get(n, "")) for (n,) in rows]


def _query_raw(sql: str, params: tuple):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def resolve_documents(query: str) -> list[tuple[str, str, int, int]]:
    """Find annual-report docs named in ``query`` -> ``(doc_id, company, fy_start, fy_end)``.

    Matches a company whose (normalised) name appears in the query, then narrows
    to any 4-digit year mentioned. Empty list if no company is recognised.
    """
    q = re.sub(r"[^a-z0-9]+", " ", query.lower())
    docs = _query_raw("SELECT doc_id, company, fy_start, fy_end FROM documents", ())
    matched = []
    for doc_id, company, fs, fe in docs:
        cnorm = re.sub(r"[^a-z0-9]+", " ", (company or "").lower()).strip()
        if cnorm and f" {cnorm} " in f" {q} ":
            matched.append((doc_id, company, fs, fe))

    # narrow by fiscal year: prefer explicit "YYYY-YY"/"YYYY-YYYY" range(s)
    # (each maps to its own fy_start) — a question spanning two years ("FY2023-24
    # to FY2024-25") contains two such ranges, so this must collect ALL of them
    # (findall), not just the first (a bare search() here previously dropped the
    # second year and silently scoped retrieval to only the first report).
    # Also match the short "FY23-24" form (2-digit years) — requires an explicit
    # FY/fy prefix, since a bare "23-24" is genuinely ambiguous with other
    # hyphenated number pairs in financial text (e.g. note references).
    # Else match any single 4-digit year to fy_start/fy_end.
    rngs = re.findall(r"(20\d{2})\s*[-/]\s*\d{2,4}", query)
    targets = {int(y) for y in rngs}
    short_rngs = re.findall(r"fy\s*(\d{2})\s*[-/]\s*\d{2}\b", query, re.I)
    targets |= {2000 + int(y) for y in short_rngs}
    if targets:
        yfilt = [m for m in matched if m[2] in targets]
    else:
        years = {int(y) for y in re.findall(r"20\d{2}", query)}
        if years:
            yfilt = [m for m in matched if m[2] in years or m[3] in years]
        else:
            # no year stated -> default to each matched company's MOST RECENT report
            latest: dict = {}
            for m in matched:
                comp = m[1]
                if comp not in latest or m[2] > latest[comp][2]:
                    latest[comp] = m
            yfilt = list(latest.values())
    return yfilt if yfilt else matched


# A standard must be cited at least this many times in a report to count as
# SUBSTANTIVELY applied; below it a mention is treated as incidental (e.g. a
# one-off historical footnote referencing the superseded Ind AS 18).
_MIN_CITATIONS = 2


def list_ind_as_in_docs(query: str):
    """List Ind AS standards SUBSTANTIVELY cited in the report(s) named in ``query``.

    Counts every "Ind AS N" mention in the report text, then keeps only valid
    framework standards (present in ``IND_AS_SUBJECTS``) cited at least
    ``_MIN_CITATIONS`` times — so incidental one-off mentions and invalid numbers
    are excluded. Returns ``{"docs": [...], "standards": [(num, subject, mentions),
    ...]}`` sorted most-cited first, or ``None`` if no company/document could be
    resolved from the query.
    """
    docs = resolve_documents(query)
    if not docs:
        return None
    doc_ids = [d[0] for d in docs]
    rows = _query_raw(
        r"""
        SELECT num, count(*) FROM (
            SELECT (regexp_matches(content, '(?i)ind\s*as[\s-]*([0-9]{1,3})', 'g'))[1] AS num
            FROM text_chunks
            WHERE doc_id = ANY(%s)
        ) m
        GROUP BY num
        """,
        (doc_ids,),
    )
    counted = {}
    for num, mentions in rows:
        if not num:
            continue
        n = int(num)
        if 1 <= n <= 141 and n in IND_AS_SUBJECTS and mentions >= _MIN_CITATIONS:
            counted[n] = int(mentions)
    standards = [(n, IND_AS_SUBJECTS.get(n, ""), c)
                 for n, c in sorted(counted.items(), key=lambda kv: (-kv[1], kv[0]))]
    return {"docs": docs, "standards": standards}


def _docs_matching(pattern: str, cfilter, literal: bool):
    """Distinct (doc_id, company, fy_start, fy_end) whose text matches ``pattern``.

    ``literal=True`` uses a case-insensitive regex (~*), else ILIKE. ``cfilter``
    optionally restricts to a set of doc_ids.
    """
    op = "~*" if literal else "ILIKE"
    params: list = [pattern]
    clause = ""
    if cfilter:
        clause = " AND t.doc_id = ANY(%s)"
        params.append(cfilter)
    sql = f"""
        SELECT DISTINCT d.doc_id, d.company, d.fy_start, d.fy_end
        FROM text_chunks t JOIN documents d ON d.doc_id = t.doc_id
        WHERE t.content {op} %s{clause}
        ORDER BY d.company, d.fy_start
    """
    return [(r[0], r[1], r[2], r[3]) for r in _query_raw(sql, tuple(params))]


def _subject_keyword(subject: str) -> str:
    """A distinctive single keyword from an Ind AS subject (for topical fallback)."""
    words = [w for w in re.split(r"[^A-Za-z]+", subject) if len(w) > 4]
    return max(words, key=len) if words else subject


def list_docs_citing_ind_as(query: str):
    """Which documents cite the Ind AS number(s) in ``query``.

    For each standard number: the reports that literally cite "Ind AS N"; if none
    do, a topical fallback listing reports whose text covers the standard's
    subject. Restricted to a company if one is named. ``None`` if no number.
    """
    nums = sorted({int(n) for n in _INDAS_RE.findall(query)})
    if not nums:
        return None
    cdocs = resolve_documents(query)
    cfilter = [d[0] for d in cdocs] if cdocs else None
    company = cdocs[0][1] if cdocs else None

    standards = []
    for n in nums:
        literal = _docs_matching(rf"ind\s*as[\s-]*{n}\y", cfilter, literal=True)
        topical = None
        subject = IND_AS_SUBJECTS.get(n, "")
        if not literal and subject:
            topical = _docs_matching(f"%{_subject_keyword(subject)}%", cfilter, literal=False)
        standards.append({"number": n, "subject": subject, "literal": literal, "topical": topical})
    return {"company": company, "standards": standards}
