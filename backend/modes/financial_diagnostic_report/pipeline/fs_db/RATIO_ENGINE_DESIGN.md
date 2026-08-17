# Ratio Engine — Design Document

**Status:** proposal, pre-implementation
**Scope:** `fs_db/` ratio path — extraction fidelity → binding → computation → output guard
**Supersedes:** nothing (first design doc for this subsystem)
**Related:** [`SCHEMA.md`](SCHEMA.md) · [`../rag/audit_model.py`](../rag/audit_model.py) · [`../agent_for_grouping/RECONCILIATION_FLOW.md`](../agent_for_grouping/RECONCILIATION_FLOW.md)

---

## Contents

1. [Purpose](#1-purpose)
2. [Principles](#2-principles)
3. [Measured baseline](#3-measured-baseline)
4. [Target architecture](#4-target-architecture)
5. [Layer 0 — Extraction fidelity (HTML → MD)](#5-layer-0--extraction-fidelity-html--md)
6. [Layer 1 — Parse integrity](#6-layer-1--parse-integrity)
7. [Layer 2 — Framework detection and profiles](#7-layer-2--framework-detection-and-profiles)
8. [Layer 3 — Binding](#8-layer-3--binding)
9. [Layer 4 — Verification and error localization](#9-layer-4--verification-and-error-localization)
10. [Layer 5 — Computation](#10-layer-5--computation)
11. [Layer 6 — Output guard](#11-layer-6--output-guard)
12. [Worked example — Debt Ratio, end to end](#12-worked-example--debt-ratio-end-to-end)
13. [The guardrail framework](#13-the-guardrail-framework)
14. [Data contracts](#14-data-contracts)
15. [Test and measurement strategy](#15-test-and-measurement-strategy)
16. [Rollout plan](#16-rollout-plan)
17. [Open decisions](#17-open-decisions)
18. [Appendix — defects found during design](#18-appendix--defects-found-during-design)

---

## 1. Purpose

Compute financial ratios from CPSU / autonomous-body annual reports such that **every published
number is provably traceable to a printed cell, and every number that cannot be so traced is
withheld with a stated reason.**

The engine is not trying to maximise how many ratios it produces. It is trying to make the set it
produces trustworthy enough that an auditor can sign under it, and to make everything it withholds
legible enough that a human knows what to do next.

### Non-goals

- Interpreting ratios (that is the LLM's job, downstream, from pre-computed values)
- Benchmarking against peers or industry norms
- Any market-price-dependent ratio (P/E, dividend yield, Tobin's Q — permanently disabled)

---

## 2. Principles

These are inherited from `finance-core` and are not negotiable in this subsystem.

| # | Principle | Consequence in this design |
|---|---|---|
| P1 | The LLM never does arithmetic and never decides pass/fail | All computation is pure Python; the model receives finished numbers |
| P2 | Same inputs → same numbers, forever | No model call in the request path. Vocabulary and profiles are frozen data with version stamps |
| P3 | Abstain > guess | Every layer has an explicit "not sure" exit that propagates to `ABSTAIN` with a reason |
| P4 | Never fabricate a zero | "Not found" and "is zero" must be distinguishable at every hop |
| P5 | Every finding carries its formula and its evidence | `trace` + `evidence` + page ref on every output, success or abstain |
| P6 | `fs_db` imports nothing from `rag/` | Embedders and LLM clients are dependency-injected or offline-only |
| P7 | Surface data quirks, don't silently repair them | A truncated statement is a flag, not something to paper over |

Two additions specific to this document:

| # | Principle | Rationale |
|---|---|---|
| P8 | **"Not applicable" ≠ "not found"** | A ratio undefined under the entity's reporting framework must say so, not report a failed lookup |
| P9 | **No hand-picked thresholds** | Every numeric threshold is structural, derived from an error model, or calibrated on labelled data — see §13.2 |

---

## 3. Measured baseline

Measured against `finance_llm` (346 filings with tables) prior to any change. These are the numbers
every proposal below is judged against.

| Metric | Value |
|---|---|
| Filings where both BS and P&L resolve | **160 / 346 (46.2%)** |
| Ratios computed per filing, among those 160 | median **6 of 35** (mean 7.5, min 0, max 21) |
| Filings sampled at random from all 346 producing **zero** ratios | **5 of 12** |
| `current_liabilities` bind rate | **17.5%** |
| `pat` bind rate | **37.5%** (vs `pbt` 60%) |
| `assets_split` tie-out outcome | PASS 15 · **FAIL 5** · SKIP 20 |
| `capital_gearing` values observed | **0.0 in 25 of 25 filings** |
| BS tables with unparseable period headers | **43 of 120 (36%)** |
| BS tables with >2 value columns | 22 of 120 |
| BS tables with reversed period order | **0 of 77 determinable** |
| Semantic (embedding) binds ever promoted to trusted | **2 of 27** across 5 filings |
| Canonical inputs with no binder at all | **16** |

---

## 4. Target architecture

```
                                        GUARD ON FAILURE
─────────────────────────────────────────────────────────────────────
L0  Extraction fidelity   HTML/source → table_md is loss-free    BLOCK doc
L1  Parse integrity       md → rows/columns/periods correct      BLOCK table
L2  Framework detection   which Schedule III division / format   ABSTAIN doc
L3  Binding               label → canonical concept              ABSTAIN input
      3a normalize   3b alias   3c regex   3d derive   3e fuzzy
L4  Verification          identities + localization              ABSTAIN or FINDING
L5  Computation           formula → number + trace               ABSTAIN ratio
L6  Output guard          LLM prose contains only our numbers    WARN
```

Each layer may only **propose**. Layer 4 is the sole authority that **disposes** — this is the
inversion the current code gets wrong (rule-based bindings are trusted by construction and Layer 4
can only upgrade, never reject).

---

## 5. Layer 0 — Extraction fidelity (HTML → MD)

> *"How do I ensure the values in the table that was in .html were properly included in the .md?"*

### 5.1 The problem

`table_chunks.table_md` is stored as markdown pipe-tables. Markdown pipe-tables are **strictly
rectangular and have no concept of merged cells.** The source HTML does. Every fidelity failure
below stems from that impedance mismatch.

Known failure modes, ranked by how much damage they do:

| # | Failure | Effect on numbers |
|---|---|---|
| F1 | `rowspan` / `colspan` collapsed | **Column shift** — values land under the wrong period. Silent and catastrophic |
| F2 | Multi-row header flattened to one | Period headers lost; parser reads a units caption as the header. **Confirmed: 36% of BS tables** |
| F3 | Empty `<td>` dropped instead of emitted | Row shortens, all subsequent cells shift left |
| F4 | `<sup>` footnote markers inlined | `Revenue²` → `revenue2`; `1,234*` may fail numeric parse |
| F5 | `&nbsp;` / thin space / unicode minus inside numerics | `1 234` or `−45` fails or mis-parses |
| F6 | Parenthesis-negative split across cell boundary | `(1,234)` → `(1,234` — sign lost |
| F7 | Nested tables flattened inline | Extra rows injected into the parent statement |
| F8 | Right-aligned cells producing leading/trailing pipes | Off-by-one column indexing |

**F1 and F3 are the dangerous ones**, because they do not corrupt any individual number — they move
correct numbers into wrong columns. Every downstream check that looks at *values* passes. Only a
**positional** check catches them.

### 5.2 There is no HTML in the database

Confirmed against [`schema.py`](schema.py): `table_chunks` has no HTML column. The HTML exists only
inside the ingestion pipeline. Therefore fidelity verification splits into two tracks.

### 5.3 Track A — Post-hoc verification (works on the corpus today, no re-ingestion)

The ingestion pipeline wrote three columns that constitute an **independent record of what the
extractor saw**, and which nothing currently checks against the markdown:

| Column | What it independently attests |
|---|---|
| `row_count` | how many rows the table had **before** markdown serialisation |
| `column_headers` | the header cells as extracted, **before** flattening |
| `note_refs` | note numbers present in the table |
| `unit` / `currency` | scale declared at extraction |

**New module: `fs_db/fidelity.py`**

```python
def verify_parsed(pt: ParsedTable, meta: dict) -> FidelityReport
```

Checks:

| ID | Check | Catches |
|---|---|---|
| A1 | `len(pt.rows) == meta["row_count"]` | F3, F7 — rows dropped or injected |
| A2 | `ncol(pt) == len(meta["column_headers"])` | F1 — colspan collapse |
| A3 | Parsed period headers ⊆ `column_headers` | F2 — header row flattened away |
| A4 | Parsed note column values ⊆ `note_refs` | note/serial column misidentified |
| A5 | `pt.unit` consistent with `meta["unit"]` | scale ambiguity |
| A6 | Rectangularity: every row had the same cell count **before** padding | F3, F8 |

`md_parser` currently pads ragged rows silently
([`md_parser.py:107`](md_parser.py#L107)). **A6 requires recording the pre-pad widths** — a
one-line change that converts a silent repair into evidence. This is the highest-value change in
Layer 0 and is independent of everything else in this document.

A2 mismatching is the direct, testable signature of the `(₹ in crore) | col_3` corruption already
measured in 36% of balance sheets.

### 5.4 Track B — Ingestion-time verification (requires the extraction pipeline)

If and when extraction is re-run, the strongest checks become available because both
representations exist simultaneously.

```python
def verify_conversion(html: str, md: str) -> FidelityReport
```

| ID | Check | Strength |
|---|---|---|
| B1 | **Numeric multiset equality** — every numeric token in HTML cells, sorted, equals every numeric token in MD cells, sorted | Proves no value was lost, gained, or altered. Order-independent, formatting-independent |
| B2 | **Cell-count conservation** — `Σ HTML cells (expanding rowspan/colspan) == MD cells` | Proves no cell dropped |
| B3 | **Positional spot-check** — a sample of `(row, col) → value` pairs match | Catches F1/F3, which B1 alone cannot |
| B4 | **Merged-cell census** — count `rowspan`/`colspan` attributes | High-risk marker; a table with merges gets DEGRADED even if B1–B3 pass |
| B5 | **Footing invariance** — if the HTML table's own subtotals foot, the MD's must foot to the same values | End-to-end semantic check |

**B1 + B2 + B3 together are the complete statement**: same values (B1), same count (B2), same
positions (B3). Any one alone is insufficient — B1 passes happily when columns are transposed.

### 5.5 Track C — Cross-filing corroboration (no HTML needed, unusually strong)

This one exploits data you already have and needs neither HTML nor re-ingestion.

**Every balance sheet prints two columns: current year and prior year. The prior-year column of
FY2024-25 must equal the current-year column of the FY2023-24 filing.**

Two separate PDFs, two separate extraction runs, one ground truth. If they disagree, one of the two
extractions is wrong — and you have located it to a specific line item in a specific filing without
any human reading a PDF.

```python
def cross_filing_check(doc_current, doc_prior) -> list[Discrepancy]
```

With 346 filings spanning multiple years per company, this is a large, free, fully deterministic
regression suite over the extraction layer. **It is also the only check in this document that can
detect a systematically wrong-but-internally-consistent extraction** (e.g. every value off by a
factor of 10 due to a unit misread — internally everything foots, and only an external reference
exposes it).

I would build this early. It is cheap and it validates the extraction of the *entire corpus*, not
just the filing being processed.

### 5.6 Verdicts and gating

| Verdict | Condition | Action |
|---|---|---|
| `EXACT` | all applicable checks pass | proceed |
| `DEGRADED` | non-positional checks pass; a positional or merge check is uncertain | proceed, attach caveat, **bar this table from being the sole source of any published ratio** |
| `CORRUPT` | any conservation check fails | **BLOCK** — the table is not used; the document abstains with `extraction_integrity_failed` |

`CORRUPT` blocks at the *document* level, not the ratio level. If the balance sheet's columns are
shifted, no ratio drawn from it is salvageable, and abstaining on twelve ratios individually would
obscure the single actual cause.

---

## 6. Layer 1 — Parse integrity

Given a table that passed Layer 0, confirm the parse itself is sound.

| ID | Check | Action on failure |
|---|---|---|
| P1 | ≥1 value column identified | BLOCK table |
| P2 | Label column identified by header match, not fallback | DEGRADED (currently warns silently) |
| P3 | **Period column identity resolved by date parsing, not position** | see below |
| P4 | Value columns are not segment/analysis columns | BLOCK table |
| P5 | Unit resolved (from `unit`, a caption row, or the title) | DEGRADED, and bar cross-statement ratios |

### P3 — period identification

Current behaviour: `periods[0]` is assumed to be the current period, purely because it is the
leftmost value column ([`binding.py:211`](binding.py#L211)). There is no date parsing anywhere.

Measured: **0 of 77 determinable tables were reversed**, so the assumption currently holds. But it
is unguarded, and a single reversed filing silently inverts every ratio and every average.

Replacement:

1. Parse a date from each value-column header.
2. Sort by date descending → current, prior.
3. If dates are unparseable (36% of tables), fall back to position **and record
   `period_order="assumed"`**, which bars the comparative from being used for averages and YoY
   movements — the two uses that are actually order-sensitive. Current-period ratios still compute.
4. If a header parses to a date **equal to** another (e.g. `Total March, 2023` alongside
   `As at 31st March, 2023`), it is not a period column → exclude it.

That last rule addresses a real observed case where `periods[1]` was a same-year total column, which
would have made `_avg()` compute `(current + same-year-total) / 2` and silently corrupt every
average-based ratio.

---

## 7. Layer 2 — Framework detection and profiles

### 7.1 The problem

`fs_db` currently assumes every filing is **Schedule III Division II** (Ind AS company). That
assumption is hardcoded in four places:

| Location | Assumption |
|---|---|
| `_BS_SECTION_RULES` | a current / non-current classification exists |
| `REGISTRY` (26 LineSpecs) | Division II vocabulary |
| `_VALIDATIONS` | `CA + NCA = Total Assets` is meaningful |
| `CATALOG` | `current_ratio` is a defined concept |

All four are false for entities already in the corpus:

| Entity class | Framework | Why the assumption breaks |
|---|---|---|
| IRFC, PFC, REC | **Schedule III Division III** (NBFC) | **No current/non-current classification at all.** Assets split Financial / Non-Financial. Debt appears as *Debt Securities*, *Borrowings (other than debt securities)*, *Subordinated Liabilities* — three or four separate lines |
| Older / smaller PSUs | **Division I** (AS) | `Shareholders' Funds`, `Reserves and Surplus`, `Fixed Assets`, `Sundry Debtors` |
| Autonomous bodies | Common Format of Accounts | `Corpus Fund` / `Capital Fund`; no equity concept; Receipts & Payments instead of cash flow |
| State electricity boards | sector-specific | present in corpus (e.g. Maharashtra State Electricity Distribution) |

Today all of these are processed as Division II. They abstain — but **for the wrong reason, with a
misleading explanation.**

### 7.2 Reuse what already exists

[`rag/audit_model.py:115`](../rag/audit_model.py#L115) already defines:

```python
class FrameworkDetection:
    framework: str = "unknown"     # Ind AS | AS | NBFC Ind AS | unknown
    division: str | None = None    # Schedule III Division I/II/III
```

This is not a new concept in the repo — it exists in the audit spine and never reached `fs_db`.
`fs_db` should populate and consume the same type (structurally duplicated, not imported, per P6).

### 7.3 Detection is structural, never nominal

Detect from **evidence in the parsed table**, never from the company's name or sector:

| Signal | Implies |
|---|---|
| `Financial Assets` / `Financial Liabilities` section headers | Division III |
| `Debt Securities` + `Subordinated Liabilities` present | Division III |
| `Corpus Fund` / `Capital Fund` | autonomous body |
| `Shareholders' Funds` + `Reserves and Surplus` | Division I |
| current/non-current headers + `Other Equity` | Division II |

Score each profile; take the best. **If the top two scores are within a margin, abstain the
document** rather than guess — a wrong framework produces confidently wrong ratios, which is the
worst possible outcome.

### 7.4 A profile is data, not code

```python
@dataclass(frozen=True)
class FrameworkProfile:
    id: str                     # "sch3_div2" | "sch3_div3" | "sch3_div1" | "common_format"
    version: str                # stamped onto every result
    section_rules: tuple        # or () if the framework has no section concept
    aliases: dict               # canonical key -> surface forms
    identities: tuple           # which tie-outs are applicable
    expected_concepts: frozenset
    not_applicable: frozenset   # concepts that DO NOT EXIST in this framework
    applicable_ratios: frozenset
```

Supporting a new entity category becomes **authoring a data file**, not editing the resolver.

### 7.5 The `not_applicable` distinction

`Computation.status` gains a third value:

```
OK  |  ABSTAIN  |  NOT_APPLICABLE
```

For IRFC today the output would read:

> Current Ratio: **ABSTAIN** — missing inputs: current_liabilities

which reads as a failure of the tool. The correct output is:

> Current Ratio: **NOT APPLICABLE** — the entity reports under Schedule III Division III, which does
> not use a current / non-current classification.

Per P8. This is a small dataclass change and a large change in how credible the output is to a
reviewer.

### 7.6 Canonical keys are economic concepts

`total_equity` genuinely exists in Division I (Shareholders' Funds), Division II (Total Equity) and
in an autonomous body (Corpus + Capital Fund). That is a **mapping** problem the profile solves.

`current_liabilities` genuinely does not exist under Division III. That is a **concept gap to be
declared**, not a mapping gap to be filled. Keeping these two apart is what stops profiles from
degenerating into a pile of per-company special cases.

### 7.7 Per-entity profiles

`agent_for_grouping/recon` already implements the right pattern: per-entity learned profiles,
persisted and reused next year with no re-setup.

Same here. Once a filing binds and a human confirms it, persist that entity's label→key map. Next
year, load it, apply it, and **diff** — surfacing only what changed. Year two on a known entity
becomes near-instant and near-perfect, and the system scales to hundreds of entities without
hand-authoring each one.

---

## 8. Layer 3 — Binding

Five sub-layers, tried in order. Each may only propose; Layer 4 disposes.

### 3a · Normalization — `fs_db/labels.py`

**Contract:** *normalization may only remove text that carries no semantic content. If removing it
could change which concept a label refers to, it is not normalization — it belongs in the alias
dictionary.*

Pipeline: NFKC → lowercase → unify dashes/quotes → strip enumeration prefixes (looping; `(a) (i) X`
has two) → drop note references → drop qualifier parentheticals (`/(loss)`, `(net)`, `(audited)`,
`(restated)`) → `&`→`and` → punctuation to space → collapse whitespace.

```
"(b) Profit/(Loss) for the year (Refer Note 32)"  →  "profit for the year"
```

**Enforcement — two mechanisms, both required:**

1. **Corpus-generated distinctness test.** Normalize every distinct label in all 346 filings; assert
   no two labels mapping to *different* canonical keys ever collapse to the same string. An
   over-aggressive rule fails the test naming the exact pair it broke. This test strengthens as the
   corpus grows; it is not a hand-written list.
2. **Collapse-ratio metric.** distinct-raw → distinct-normalized. A healthy normalizer collapses
   ~25–35%. A rule that pushes it to 60% is destroying information and shows up immediately.

**Blast radius:** a normalization miss causes a non-match → `ABSTAIN`. It cannot produce a wrong
number. Normalization risk is bounded to *coverage*, never *correctness*.

**Hidden work item:** the 26 existing `LineSpec` patterns were written against the old `_strip()`
output and several contain literal punctuation that will never match normalized text, e.g.
`r"^\(?[a-z]\)?\s*property,? plant and equipment\b"`. **Every pattern must be re-audited against the
new normal form.** This is the bulk of the effort in 3a, not the normalizer itself.

### 3b · Alias dictionary — `fs_db/aliases.py`

Canonical key → exact normalized surface forms, compiled at import into a reverse index with a
**collision check that raises at import time** if two keys claim the same alias.

This is the property regex cannot provide: today, overlapping patterns resolve silently by list
order and a conflict is never surfaced.

**Population workflow:**

1. `fs_db/tools/harvest_labels.py` → census of every normalized label with frequency, section, role,
   example docs, plus the coverage curve.
2. **Auto-seed:** every label the *current* regex already binds is recorded as a confirmed alias.
   Free, high-precision, and it means the dictionary **starts at parity with today** — it can only
   improve.
3. Remaining high-frequency unmatched labels → batched **once, offline** to an LLM with the canonical
   key list and their `INPUT_TRACE` descriptions, asked to propose a key or `NONE`.
4. Human reviews the diff.
5. Merged, version-stamped, frozen. **The runtime never calls a model** (P2).

### 3c · Regex — demoted

Kept as a third-tier fallback for genuinely templated labels. No longer the primary mechanism.

Also fixed here: several current patterns are unanchored and use `.search()`, e.g.
`depreciat.*amort`, which will match `Add: Depreciation and amortisation adjustment` inside a
reconciliation block.

### 3d · Structural derivation — `fs_db/derive.py`

A concept that cannot be *matched* can often be *computed from the table's own structure*.

`total_current_liabilities` binds only 17.5%, largely because many typed BS tables are truncated and
the row is simply absent. No matcher can find a row that does not exist — but the section tagger
already knows precisely which rows are in that block.

```
candidates = rows where section == target and role == "line" and value is not None
derived    = Σ candidates
```

| Situation | Outcome |
|---|---|
| Matched subtotal exists **and ties** | mark it `CONFIRMED` — corroboration, not a new binding |
| Matched subtotal exists **and does not tie** | feeds localization (§9) — this is what isolates the culprit |
| No matched subtotal (truncated) | bind the derived value, `provenance="derived"`, contributing rows in the trace |

**Anti-double-count guards:** sum only `role == "line"` (never `sum`/`total` — the role classifier
already separates them); require ≥2 contributing rows; and require the derived figure to satisfy the
balance-sheet equation.

That last guard is why deriving is *safer* than matching here: `total_assets` binds 87.5%, so
`Equity + NCL + CL = Total Assets` is usually available as an **independent** check on the derived
subtotal. Structural derivation is corroborated arithmetic; label matching is a guess about English.

This requires adding a `total_non_current_liabilities` LineSpec, currently **missing** from the
registry — which is why the full balance-sheet equation is not checkable today.

### 3e · Fuzzy — `fs_db/fuzzy.py`

Stdlib only (`difflib` + token Jaccard), section- and role-scoped, deterministic.

Two gates: an **absolute floor**, and a **margin** — best must beat runner-up by a set gap, else
abstain with a legible reason:

```
ambiguous: "profit for the year from continuing operations"
           matched pat(0.81) and pbt(0.79) — margin 0.02 < 0.10
```

**The margin rule applies retroactively to 3b and 3c.** Today the regex layer takes the *first*
matching row and never asks whether a second row also matched. Refinement: abstain if two or more
rows match **at the same pattern index within the same section** — a tie at equal specificity is
genuine ambiguity, whereas a specific pattern beating a loose one is the system working correctly.

---

## 9. Layer 4 — Verification and error localization

> *"If there is an error in the BS itself — a subtotal error — the tie-out will fail. What happens to
> the check on whether the extracted number is right?"*

### 9.1 The conflation

A failed tie-out has two possible causes with **opposite** correct responses:

| Cause | Correct response |
|---|---|
| We grabbed the wrong row | **Abstain.** Do not publish. Our bug |
| The company's balance sheet genuinely does not foot | **Publish, and raise a FINDING.** This is the single most valuable thing the tool can detect |

A design that abstains on every tie-out failure **suppresses genuine audit findings** — the exact
opposite of the system's purpose. Any binary CONFIRMED/CONTRADICTED scheme has this defect.

### 9.2 Localization by redundancy

You cannot separate the two causes from one identity. You can from **several overlapping ones** —
which is how a human auditor localizes a footing error.

Available identities, once §8/3d exists:

```
CA + NCA                       = Total Assets
Equity + NCL + CL              = Total Equity & Liabilities
Total Assets                   = Total Equity & Liabilities
Σ(rows in section S)           = printed subtotal of S          [per section]
Revenue + Other Income         = Total Income
PBT − Tax                      = PAT
…and every one of the above also holds for the PRIOR-YEAR column
```

Build a bipartite graph of `line ↔ identities it participates in`, then:

- **One identity fails; every other identity touching that line passes** → the line is corroborated
  from independent directions. The extraction is right; **the printed statement is wrong.**
- **Multiple identities touching one line fail** → that line is the common factor. **The extraction
  is wrong.**

### 9.3 The prior-year column is the sharpest discriminator

Nearly free and unusually powerful: **the same row mapping produces two independent datasets.**

| Observation | Conclusion |
|---|---|
| Identity holds for prior year, fails for current year | **Mapping is fine.** Same rows, same logic, one year works → real current-year discrepancy |
| Identity fails for **both** years | **Mapping is suspect.** A company rarely misfoots two years running; an extraction bug does it every time |

BPCL's `total_current_assets = 16.80` fails in both columns. A genuine footing error usually does
not.

### 9.4 Gap shape as a supporting signal

Extraction errors and real errors do not look alike:

| Signature | Likely cause |
|---|---|
| Off by orders of magnitude (16.80 vs 105,405.97) | extraction |
| Gap equals exactly one line item in the section | extraction — adjacent row grabbed |
| Clean ×100 / ×1000 | extraction — unit confusion |
| Gap < 1% of statement scale | genuine discrepancy or rounding |

Supporting signal only. The redundancy graph decides; gap shape breaks ties.

### 9.5 Verdicts

```python
CONFIRMED               corroborated by ≥1 identity, no failures
STATEMENT_DISCREPANCY   line corroborated elsewhere, one identity fails
                        → COMPUTE ratios, and RAISE A FINDING
SUSPECT_EXTRACTION      line implicated in multiple failures, or implausible gap shape
                        → ABSTAIN affected ratios, queue for human
UNCONFIRMED             no identity could run
NOT_APPLICABLE          concept does not exist in the detected framework
```

`STATEMENT_DISCREPANCY` is the state the current design lacks entirely, and it is the one audit
users care about most. It emits a `Finding` with `trace` and `evidence`, consistent with the tie-out
findings in [`../rag/audit_checks.py`](../rag/audit_checks.py).

### 9.6 Expect coverage to fall first

Enabling rejection **will reduce the ratio count** on the first pass. That is the change working —
it is removing numbers that were wrong. The BPCL `Current Ratio = 0.0002x` disappears here. The dip
must not be read as a regression; it is the baseline being corrected before §8's coverage work
raises it again.

---

## 10. Layer 5 — Computation

Largely unchanged — [`computations.py`](computations.py) is the strongest module in the package.
Four changes:

1. **`status` gains `NOT_APPLICABLE`** (§7.5).
2. **`optional` inputs in a numerator require an `any_of` group.** The `capital_gearing` defect
   (§18) is caused by three optional numerator inputs with no `any_of` gate, producing `0.0` in
   25 of 25 filings. Audit every spec for this shape.
3. **Definitional integrity check** (§12.4) — the `formula` string and the `num`/`den` callables must
   agree. Currently two ratios violate this.
4. **Every `Computation` records the profile id + version and dictionary version** that produced it,
   so a number can be reproduced years later (P2, and an audit requirement in its own right).

---

## 11. Layer 6 — Output guard

[`numeric_guard.py`](numeric_guard.py) is sound in structure; one weakness:

A percentage is matched against the *entire* computed pool (~150 numbers: 35 ratios + ~25 inputs +
24 movements × 4) at ±0.15 absolute, sign-agnostic. Collisions are likely, so an invented figure can
be stamped "verified". **The guard fails open.**

Fixes:
- Match a percentage only against values of the same *kind* (ratio results, not raw inputs).
- Tolerance relative to the value's own magnitude, not a flat 0.15.
- Report the matched source in the verification payload, so "verified" is auditable rather than
  asserted.

---

## 12. Worked example — Debt Ratio, end to end

> *"I need to calculate the debt ratio — Total Borrowings / Net Assets (Total Assets − Current
> Liabilities). How do I make sure the right Total Borrowings and Net Assets are taken from the
> balance sheet concerned?"*

### 12.1 The dependency tree

```
debt_ratio
├── numerator   Total Borrowings
│   ├── long_term_borrowings     BS · non-current liabilities
│   └── short_term_borrowings    BS · current liabilities
└── denominator Net Assets
    ├── total_assets             BS · assets (grand total)
    └── current_liabilities      BS · current liabilities (subtotal)
```

Neither input is a single printed line. **Total Borrowings is a sum across two different sections of
the balance sheet**, and Net Assets is a derived difference. Both are exactly the cases where "took
the right number" is non-trivial.

### 12.2 Guard chain for `long_term_borrowings`

Six independent mechanisms establish that the bound figure is the right one:

| # | Guard | What it rules out |
|---|---|---|
| G1 | **Section scoping** — must come from the `non_current_liabilities` block | A row labelled `Borrowings` exists in *both* liability sections. Without scoping, the pattern `^borrowings\b` matches whichever comes first — a 50/50 coin flip |
| G2 | **Role gating** — `line`, `total` or `sum`, never `header` | Section captions with no value |
| G3 | **Ambiguity margin** — if two rows in the section match at equal specificity, **abstain** | Filings that print both `Borrowings` and `Borrowings (current maturities)` |
| G4 | **Note-reference cross-check** — the row's note number must resolve to a borrowings note, and that note's total must tie to the face figure | Grabbing a row that happens to be worded similarly but is disclosed under an unrelated note |
| G5 | **Structural corroboration** — Σ(non-current liability lines) must equal the NCL subtotal | A *missing* component. Critical for Division III (see 12.3) |
| G6 | **Temporal corroboration** — prior-year column present and plausible; prior-year value equals the previous filing's current-year value (§5.5) | Systematic extraction error invisible within a single filing |

G1 and G5 do most of the work. G4 is the strongest but requires note-table resolution, which is a
later increment.

### 12.3 The framework trap — why this matters concretely

Under **Schedule III Division III (NBFC)** — which covers IRFC, PFC and REC in your corpus — debt is
**not one line.** The financial-liabilities section prints, separately:

```
Debt Securities
Borrowings (other than debt securities)
Subordinated Liabilities
Deposits
```

The current registry pattern `^borrowings\b` would bind **one** of these and silently report it as
Total Borrowings. For an NBFC — an entity whose entire balance sheet is debt — this could
**understate leverage by 60–80%.**

And nothing today catches it: the figure is real, it comes from the right section, it has the right
sign, it is a plausible magnitude. The ratio would be computed and published with full confidence.
It would simply be wrong.

**Two guards catch it, and both are framework-aware:**

- The **profile declares the complete debt component set** for that framework. If the profile expects
  four components and two bound, that is a declared gap → `ABSTAIN`, not a silent partial sum.
- **G5** — Σ(bound financial-liability lines) must reconcile to the section subtotal. A missing debt
  component breaks that sum, and localization (§9) points at the liabilities section.

This is the clearest illustration of why framework profiles (§7) are not an optional refinement.
Without them, the debt ratio is not merely unavailable for NBFCs — it is **confidently wrong**, which
is far worse.

### 12.4 The denominator — a real defect in the current code

Your stated formula is `Net Assets = Total Assets − Current Liabilities`.
The catalog's `formula` string says the same thing.
The code computes something different:

```python
# computations.py
def _net_assets(x):       return x["net_fixed_assets"] + (x["current_assets"] - x["current_liabilities"])
def _capital_employed(x): return x["total_assets"] - x["current_liabilities"]

RatioSpec("debt_ratio", ..., "Total Borrowings / Net Assets (Total Assets – Current Liabilities)",
          num=_total_borrowings, den=_net_assets, ...)
```

`_net_assets` = `net_fixed_assets + current_assets − current_liabilities`.
The documented formula = `total_assets − current_liabilities`.

These are equal **only if** `Total Assets = Net Fixed Assets + Current Assets` — i.e. only if the
entity holds no non-current investments, no CWIP, no intangibles, no deferred tax assets, no
long-term loans. For a real PSU that is essentially never true. Note that `_capital_employed`, which
*is* `total_assets − current_liabilities`, already exists in the same file and is used by `roce`.

**`debt_ratio` and `equity_ratio` therefore compute a denominator that contradicts their own
documented formula, and the `trace` shown to the reviewer states the definition the code did not
use.** This is a guardrail failure of a distinct kind: extraction was perfect, binding was perfect,
arithmetic was perfect, and the answer is still not the ratio anyone asked for.

**Mitigation — the definitional integrity check.** Every `RatioSpec` gains a machine-checkable
declaration of its denominator's composition, and a test asserts that the declared composition and
the callable agree on a synthetic input set where all the candidate definitions differ. This class
of defect is invisible to every other guard in this document, because nothing else compares the
*intent* to the *implementation*.

**Decision required** (see §17): which definition of Net Assets is authoritative for `debt_ratio` and
`equity_ratio` — the source Excel's `Fixed Assets + Working Capital`, or the formula string's
`Total Assets − Current Liabilities`? Whichever is chosen, the string and the code must be made to
agree, and the trace must state it unambiguously.

### 12.5 The published trace

What the reviewer must be able to see, without rerunning anything:

```
Debt Ratio = 0.3421 x
  Total Borrowings 12,450.00 / Net Assets 36,392.00

  Total Borrowings   = long_term_borrowings 9,800.00 + short_term_borrowings 2,650.00
    long_term_borrowings   9,800.00   BS p.112 tbl_0071 row 34
      "(a) Borrowings"  · section: non-current liabilities · note 18
      alias-match · CONFIRMED by ncl_section_sum, prior-year identity
    short_term_borrowings  2,650.00   BS p.113 tbl_0071 row 51
      "(a) Borrowings"  · section: current liabilities · note 22
      alias-match · CONFIRMED by cl_section_sum
  Net Assets = total_assets 108,265.00 − current_liabilities 71,873.00
    total_assets      108,265.00  row 29 · CONFIRMED by assets_split, bs_equation
    current_liabilities 71,873.00 row 58 · DERIVED from 11 rows · CONFIRMED by bs_equation

  framework: sch3_div2 v1.2 · aliases v2026.07 · fidelity: EXACT
```

Every number names its cell, its section, its note, how it was bound, and which identities confirm
it. That is the deliverable — not the ratio.

---

## 13. The guardrail framework

> *"How is the guardrail for each calculated or fetched number determined?"*

### 13.1 The tiers

Each guard answers a different question. They are listed with what they catch and — importantly —
what they **cannot** catch.

| Tier | Guard | Question answered | Blind to |
|---|---|---|---|
| **T0** | Provenance | Does every number trace to a cell? | whether it is the *right* cell |
| **T1** | Extraction fidelity (§5) | Did the value survive HTML→MD intact? | semantically wrong but faithfully converted |
| **T2** | Parse integrity (§6) | Right column, right period, right unit? | correct parse of the wrong table |
| **T3** | Identity / tie-out (§9) | Do the numbers cohere internally? | consistent-but-wrong (whole statement off by ×10) |
| **T4** | Cross-statement | Does the note tie to the face? | errors present in both |
| **T5** | Temporal (§5.5, §9.3) | Prior column, and prior filing, agree? | an error repeated identically across years |
| **T6** | Definitional (§12.4) | Does the formula match its documentation? | a definition that is agreed but wrong |
| **T7** | Plausibility (§13.2c) | Is the result within observed bounds? | plausible-looking wrong answers |
| **T8** | Output guard (§11) | Did the LLM invent a number? | a wrong number that we ourselves computed |

**No single tier is sufficient**, and the blind-spot column is why. T3 alone cannot detect a
uniformly-scaled statement; only T5 can. T5 alone cannot detect a first-year filing; only T3 can.
Coverage comes from the tiers being **independent** — see §13.4.

### 13.2 How each threshold is determined

**This is the part that must not be improvised.** Every numeric threshold in the system comes from
exactly one of three sources, and the code must name which one.

#### (a) Structural — no threshold at all

Cell counts, multiset equality, section membership, role membership, framework applicability. These
are binary and exact. **Preferred wherever a check can be phrased this way**, because a threshold
that does not exist cannot be wrong.

Most Layer 0 and Layer 1 checks are in this class.

#### (b) Derived from an error model

Arithmetic identities need a tolerance because financial statements are printed to 2 decimal places
and summing rounded values drifts.

The current constant is a flat `ARITH_ABS_TOL = 0.05` ([`config.py:47`](config.py#L47)). **That is
dimensionally wrong** — the drift from summing *n* values each rounded to 2dp is bounded by
`n × 0.005`, so a 60-row section sum can legitimately drift by 0.30 and would be falsely flagged,
while a 3-row identity tolerating 0.05 is far too loose.

Replacement:

```
tolerance(n, magnitude) = n × 0.005  +  ARITH_REL_TOL × magnitude
```

Derived, not chosen. It scales correctly with both the number of addends and the size of the figures,
and each term has a stated physical justification.

#### (c) Calibrated on labelled data

Fuzzy-match floors and margins, and plausibility bands.

- **Fuzzy thresholds** are set on the golden set (§15) at the operating point where **precision =
  1.0**, then recall is maximised subject to that. Never guessed. Re-calibrated whenever the golden
  set grows.
- **Plausibility bands are derived from the corpus, not from textbooks.** With 346 filings you can
  compute the empirical distribution of each ratio and flag by percentile. A hand-picked "current
  ratio should be between 0.5 and 5" is someone's intuition; the 1st/99th percentile of your actual
  population is evidence.

**Critically: a plausibility failure is never a hard reject.** Genuine distress produces genuine
outliers, and an audit tool that suppresses them is worse than useless. T7 emits a flag for human
attention, never an abstain.

#### The rule

> Every threshold constant lives in [`config.py`](config.py), is overridable by `FSDB_*` env var, and
> carries a comment naming its source: `STRUCTURAL`, `ERROR_MODEL`, or `CALIBRATED(golden_set@date)`.
> A constant with no such provenance does not pass review.

### 13.3 Severity ladder — what a failure does

Detection is worthless without a defined consequence. Each guard maps to exactly one action:

| Action | Meaning | Example |
|---|---|---|
| **BLOCK** | Nothing is published from this document/table | Fidelity `CORRUPT`; framework ambiguous |
| **ABSTAIN** | This input, and every ratio depending on it, is withheld with a reason | `SUSPECT_EXTRACTION`; ambiguous match; missing required input |
| **FINDING** | Publish the number **and** raise an audit finding | `STATEMENT_DISCREPANCY`; Schedule III >25% variance |
| **WARN** | Publish with a visible caveat attached | fidelity `DEGRADED`; `period_order="assumed"`; flavor `assumed` |
| **INFO** | Record in the run manifest only | which profile matched; alias vs fuzzy provenance |

The distinction between ABSTAIN and FINDING is the whole subject of §9, and getting it backwards is
the most consequential error available in this design.

### 13.4 Independence

Two guards drawn from the same data do not compound. Explicitly:

| Independent | Why |
|---|---|
| T3 (identity) vs T5 (prior filing) | different documents, different extraction runs |
| T1 (fidelity) vs T3 (identity) | one is representational, one is semantic |
| T3 (`assets_split`) vs T3 (`bs_equation`) | partially — both use `total_assets` |
| G1 (section) vs G5 (section sum) | **not independent** — both rely on correct section tagging |

Where guards are not independent, the design must not claim their confidence multiplies. Section
tagging is a single point of failure for several guards simultaneously, which is an argument for
verifying it directly (Layer 1 P2) rather than treating its downstream checks as confirmation.

### 13.5 Guard records are output, not logs

Every published number carries its guard record — provenance, verdict, which identities confirmed
it, which caveats attach. This travels in `RatioReport.to_dict()`, into the API payload, and into the
UI.

The current code computes this information and then discards it: `validations` is read by nothing
outside two test scripts. **A guardrail whose result is not surfaced is not a guardrail** — it is a
comment.

---

## 14. Data contracts

```python
FidelityReport  verdict: EXACT|DEGRADED|CORRUPT · checks: list[dict] · flags: list[str]

FrameworkDetection  framework: str · division: str|None · confidence: float
                    profile_id: str · profile_version: str · evidence: list[str]

BoundLine   key · value · prior · label · section · note
            provenance: alias|rule|derived|fuzzy|semantic
            verdict: CONFIRMED|STATEMENT_DISCREPANCY|SUSPECT_EXTRACTION|UNCONFIRMED|NOT_APPLICABLE
            confirmed_by: list[str] · contradicted_by: list[str]
            table_id · page · contributing_rows: list[int]   # when derived

Computation  (existing) + status: OK|ABSTAIN|NOT_APPLICABLE
                        + profile_id · profile_version · aliases_version
                        + guard_record: dict
```

**Backward compatibility:** `BoundLine.validated` survives as a read-only property
(`verdict == "CONFIRMED"`) so [`test_ratio_binding.py`](test_ratio_binding.py) keeps working.
`RatioReport.to_dict()` only ever gains keys. UI changes are additive panels.

---

## 15. Test and measurement strategy

**Nothing in §5–§13 ships before this exists.**

| Asset | Purpose |
|---|---|
| `fs_db/tools/sweep.py` | Run N filings, snapshot every input/verdict/ratio to JSON. `--against baseline.json` prints what moved |
| **Golden set** (~10 filings × ~8 FSLIs, human-confirmed) | The only thing that measures **precision**. ~30 min of CA time, and it is the calibration source for §13.2c |
| Corpus-generated distinctness test (§8/3a) | Normalization safety |
| `test_ratio_flow.py`, `test_computations.py` | Existing hermetic regression — must stay green throughout |
| Cross-filing check (§5.5) | Free regression over the *entire* extraction layer |

**The metric is not "more ratios."** It is:

```
primary    wrong ratios published  →  0        (precision)
secondary  correct ratios published →  maximise (recall)
tertiary   abstains carrying an actionable reason → 100%
```

Optimising recall without the precision gate is how this system gets worse while appearing to
improve. Coverage may legitimately **fall** after §9 lands.

---

## 16. Rollout plan

Each step merges only if the sweep says it helped. Order revised from earlier discussion: binding
correctness lands **before** the flavor filter widens intake, so the binder is self-policing before
it sees roughly twice the traffic.

| # | Step | Needs DB | Effort | Gate |
|---|---|---|---|---|
| 0 | Sweep harness + golden set | yes | ~2h | — |
| 1 | Pre-pad width capture + Layer 0 Track A | no | ~2h | fidelity verdict distribution |
| 2 | Normalization + pattern re-audit | no | ~3h | hermetic tests green; bind rates up |
| 3 | Verification + localization (§9) | no to build | ~4h | zero wrong ratios on golden set |
| 4 | Structural derivation (§8/3d) | build no, verify yes | ~4h | `current_liabilities` bind rate |
| 5 | Cross-filing check (§5.5) | yes | ~3h | discrepancy count |
| 6 | Soften flavor filter (+ coherence) | yes | ~1h | resolution 46% → target ~85% |
| 7 | Framework detection + profiles | yes | ~1–2d | Division III filings stop mis-abstaining |
| 8 | Harvest + alias dictionary | yes | ~0.5d + review | bind rates |
| 9 | Fuzzy + margin | no | ~3h | precision held at 1.0 |

Steps 1–4 require no database and no model. Step 3 is the safety foundation and should not be
deferred behind coverage work.

---

## 17. Open decisions

Each of these changes what gets built and needs a human answer.

1. **Net Assets definition** (§12.4) — is the authoritative denominator for `debt_ratio` /
   `equity_ratio` the sheet's `Fixed Assets + Working Capital`, or `Total Assets − Current
   Liabilities` as the formula string states? Currently they disagree and the trace is misleading.
2. **Framework scope for v1** — Division II only (status quo, but with honest `NOT_APPLICABLE`
   output for others), or Division II + III together? III is ~7+ filings in the corpus and the
   highest-risk silent-error case.
3. **Golden set ownership** — who confirms the ~80 ground-truth figures, and is 10 filings enough to
   calibrate §13.2c thresholds?
4. **Does the ingestion pipeline still exist and can it be re-run?** Determines whether Layer 0
   Track B (§5.4) is available or whether Tracks A + C are the permanent ceiling.
5. **`STATEMENT_DISCREPANCY` routing** — does a genuine footing discrepancy flow into the existing
   `Finding` taxonomy in `rag/audit_model.py`, or stay inside the `fs_db` report?

---

## 18. Appendix — defects found during design

Independently reproducible; each was confirmed against live data or by reading the code.

| # | Defect | Evidence | Severity |
|---|---|---|---|
| D1 | Failed tie-out never downgrades a binding — `_validate` has no `else`, and `trusted()` returns `True` for all rule bindings | BPCL_2022_2023: `assets_split` FAIL, 24 ratios published incl. `Current Ratio = 0.0002x`, `Quick Ratio = -0.5294x` | **Critical** |
| D2 | `capital_gearing` numerator is three `optional` inputs with **no binder in the registry** and **no `any_of` gate** → always `0/x` | **0.0 in 25 of 25 filings**; 2nd most-emitted ratio (67.5%) | **Critical** |
| D3 | `debt_ratio` / `equity_ratio` use `_net_assets` while their `formula` string documents `Total Assets − Current Liabilities` | code read; the two differ unless non-current assets are nil | **High** |
| D4 | `validations` surfaced nowhere — not in `to_dict()`, the prompt, the API, or the UI | grep: only two test scripts read it | **High** |
| D5 | Hard `~* 'standalone'` title filter silently discards over half the corpus | both statements resolve for **160 of 346** | **High** |
| D6 | Current period assumed to be the leftmost column; no date parsing | 43 of 120 tables have unparseable period headers | **Medium** |
| D7 | `ARITH_ABS_TOL` is a flat constant, not scaled by addend count | dimensional analysis; §13.2b | **Medium** |
| D8 | Numeric guard matches percentages against the whole computed pool at ±0.15 sign-agnostic → fails open | code read; pool ≈150 values | **Medium** |
| D9 | Ragged rows silently padded, discarding the strongest available fidelity signal | [`md_parser.py:107`](md_parser.py#L107) | **Medium** |
| D10 | `total_non_current_liabilities` missing from the registry → full BS equation not checkable | registry read | **Medium** |
| D11 | Two divergent ratio engines — `computations.py` and `audit_checks.RATIO_DEFS` (used by `spine.py`) | different names, no traces, no abstains | **Medium** |
| D12 | Semantic tier is architecturally inert — promotion requires a tie-out, and tie-outs mostly SKIP | 2 of 27 binds ever trusted, across 5 filings | **Low** |
| D13 | Ratio-engine exceptions swallowed with no logging | [`company_qa.py:640`](../rag/company_qa.py#L640) | **Low** |
