# fs_db — FS audit flow over `finance_llm`

Self-contained implementation of the agreed scope:

> **Part A** — Basic recall of the FS + compliance with relevant standards for
> (i) Balance Sheet (ii) P&L (iii) Cash Flow (iv) Notes to Accounts.
> **Part B** — Prechecks + arithmetic validation of all tables in the FS and associated notes.

It reads the `finance_llm` Postgres KB (`192.168.200.29:5478`) as the primary source.

## Why this is a separate folder
The DB schema differs between the old KB and this one and will change again on migration.
This package imports **nothing** from the sibling `rag/` — it owns its DB config and schema
map, so it can be deleted in one move when you migrate. **A schema change is a two-file edit:**
[`config.py`](config.py) (connection) and [`schema.py`](schema.py) (identifiers).

## Run
```bash
pip install "psycopg[binary]"          # only dependency for the core flow
python -m fs_db ping                   # connectivity
python -m fs_db map                    # print the DB -> flow schema map + live counts
python -m fs_db docs GAIL              # list documents
python -m fs_db run GAIL_India_2024_2025            # standalone (default)
python -m fs_db run GAIL_India_2024_2025 --consolidated
python -m fs_db run --company HPCL --fy 2025 --json  # resolve by entity+year, JSON out
```

## Pipeline (one growing result per document)
```
resolve doc → [1] doc-quality precheck → [2] statement coverage
            → [3] recall BS/P&L/CF/Notes  (Part A)
            → [4] structural compliance vs Ind AS 1/7  (Part A)
            → [5] arithmetic: bs_equation · footing · note_to_face · cash_flow  (Part B)
```

| module | role |
|---|---|
| `config.py` / `schema.py` | connection + centralised table/column identifiers |
| `db.py` | read-only psycopg3 helper (session `read_only`) |
| `md_parser.py` | **the enabling transform**: `table_md` → `CanonicalFact` (column profiling) |
| `repository.py` | data access: statement/note tables, flavor, note-no extraction |
| `precheck.py` | req2 quality gate + coverage |
| `arithmetic.py` | deterministic validators, each with a reproducible trace + evidence |
| `recall.py` | Part-A statement recall |
| `compliance.py` | structural compliance (stage 1); Ind AS semantic grounding = stage 2 |
| `flow.py` / `cli.py` | orchestrator + report |

## Ratio catalog (deterministic — LLM never computes a ratio)

A second, parallel pipeline: given a company's Balance Sheet + P&L, bind the
Schedule III line items a ratio needs and compute it in pure Python, never in the
LLM. `rag/company_qa.py` wires this into the live company-filings chat (both
review-mode answers and direct ratio questions).

```text
parse_table_md → Resolver.bind_statement  → build_ratio_inputs → run_ratios
                 (binding.py: rules →         (ratio_pipeline.py:  (computations.py:
                  scoped semantic → tie-out    derive ebit/         40-ratio catalog,
                  gate; see its docstring)     averages)            each with a source
                                                                     trace + abstain)
```

| module | role |
|---|---|
| `computations.py` | the **ratio formulas** — `RatioSpec`s with `num`/`den`/`scale`, plus a per-input `InputTrace` (which statement / major head / sub-head / line item each figure comes from), abstain-on-gap |
| `binding.py` | the hybrid resolver — structural section-scoping → anchored-regex rules (authority) → optional section-scoped semantic fallback → arithmetic tie-out gate, so an untrusted guess can never reach a ratio |
| `ratio_pipeline.py` | glue — binds BS+PL, derives EBIT/period-averages, runs the catalog, formats the `COMPUTED FIGURES` prompt block + values for the numeric-hallucination guard |
| `numeric_guard.py` | anti-hallucination check on the LLM's *narration* of a computed ratio — every number in the answer must trace to a source cell or a `computations.py` result |

**Formula source of truth**: [`formulas/Final Ratio Calulation chart - final detailed.xlsx`](../formulas/Final%20Ratio%20Calulation%20chart%20-%20final%20detailed.xlsx)
— re-labels every input to the *exact* term a Schedule III / Ind AS filing prints
(e.g. "Revenue from Operations", not "Sales"; "Finance Costs", not "Interest") and
adds an explicit per-input Financial-Statement/Major-Head/Sub-Head/Line-Item trace,
which `computations.py`'s `INPUT_TRACE` registry is transcribed from directly. It
supersedes the earlier `formulas/Financial_Ratio_Analysis_Reference_Table.xlsx`
(kept for history — same underlying formulas, generic textbook wording). See
`computations.py`'s module docstring for the two substantive fixes that precision
pass forced (COGS and Total Borrowings are not single Schedule III face lines —
each is a derived sum of the lines that actually exist).

## Design rules (inherited from the project)
- **LLM never does arithmetic or pass/fail.** All footing/tie-outs are deterministic Python —
  same inputs → same numbers, every finding carries its formula (`trace`) and where to look (`evidence`).
- **Abstain > guess.** A check that cannot resolve cleanly (ambiguous note, truncated table,
  mismatched columns) reports `ABSTAIN`, never a fabricated pass/fail.
- **Flavor coherence.** Standalone vs consolidated are never mixed.

## Status (validated live on GAIL FY2024-25)
Working: recall of all 4 statements + note schedules; quality/coverage prechecks; **BS footing
(8/8 subtotals foot on real figures)**; structural compliance; confidence-gated note-to-face
(honest abstains + provisional flags); truncation flag on incomplete BS extractions; page-level
evidence on every finding.

## Backlog (next hardening, in priority order)
1. **Parser format-generality** — handle unit-header rows / multi-column layouts (e.g. BPCL `₹ in crore`).
2. **Statement selection & stitching** — merge split statements; better standalone-P&L pick.
3. **P&L / movement-note checks** — targeted identity checks (income−expenses=PBT; note roll-forwards) instead of additive footing.
4. **Compliance stage 2** — Ind AS semantic grounding via `ind_as_chunks` + the rag reranker gate; Schedule III format-completeness once that corpus is ingested (req4).
5. **req5/6** — auditor-report & CARO cross-checks from `text_chunks`.
