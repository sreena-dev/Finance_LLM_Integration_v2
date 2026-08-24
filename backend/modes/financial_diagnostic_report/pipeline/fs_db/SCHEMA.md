# finance_llm → flow schema map

Mapped live on 2026-07-22 against `finance_llm @ 192.168.200.29:5478` (PostgreSQL 16).
Every SQL identifier the package uses is centralised in [`schema.py`](schema.py) — this
file is the human-readable version. **On DB migration, edit `schema.py` + `config.py` only.**

## Tables the flow reads

| table | rows | flow use | key columns |
|---|---|---|---|
| `documents` | 346 | req1 entity/period · req2 quality gate | `company, fy_start, fy_end, total_pages_ocr, total_pages_pdf, total_tables, total_chunks` |
| `table_chunks` (typed) | ~9.5k | Part-A recall · req3 arithmetic | `financial_stmt_type` (balance_sheet/profit_loss/cash_flow/statement_of_equity), **`table_md`**, `unit, currency, section, note_refs, page_pdf_start` |
| `table_chunks` (untyped, `is_financial`) | ~22k | req3a note-to-face | note schedules; note no. parsed from `table_title`/`section` |
| `text_chunks` | 993k | req5 auditor report / CARO (later stage) | `content, section, chunk_type, note_refs` |
| `ind_as_chunks` / `ind_as_documents` | 3.6k / 40 | Part-A compliance (Ind AS grounding, stage 2) | `standard_number, paragraph_no, text, embedding` (bge-m3 1024-d) |

`tb_input_data`, `fdr_input_data` are the reconciliation-side inputs — **not used** by this flow.

## The one transform that makes it work

`table_md` is stored as clean markdown pipe-tables (100% filled on financial tables) but as
*presentation*. [`md_parser.py`](md_parser.py) turns each into computable rows by **profiling
columns** (label / note / period detected by content, not position — the label is often not
col 0, there's a `Sr. No.` column, notes are alphanumeric like `2A`, values use Indian
grouping `1,11,647.03` and `(x)` for negatives). Output → `CanonicalFact` rows that the
arithmetic core runs on.

## Known DB-side data issues (surfaced by the flow, not caused by it)

- **Truncated balance sheets**: several typed BS tables are cut off before the
  `Total Equity and Liabilities` grand-total row (e.g. `GAIL … tbl_0068`). The flow flags
  this (`bs_equation` → truncation RISK_FLAG) instead of fabricating a result.
- **Noisy `toc_section`**: frequently mis-attributed (a real BS tagged `FIVE YEAR PROFILE`).
  The flow keys statement-flavor and relevance off `section`/`title` only.
- **Standalone + consolidated coexist**: notes exist twice; the flow stays within one
  *flavor* (default standalone; `--consolidated`) to avoid cross-tying.
- **Format variance across companies**: GAIL parses cleanly; some (e.g. BPCL) put a
  `₹ in crore` unit row where the parser expects the header — parser hardening backlog.
