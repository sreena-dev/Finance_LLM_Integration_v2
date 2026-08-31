# Structure Engine — Design Document

**Status:** proposal, pre-implementation
**Scope:** the *financial structure* analysis lens — asset mix, capital structure, liquidity
structure, funding structure, concentration — from multi-year common-size and trend analysis
**Related:** [`RATIO_ENGINE_DESIGN.md`](RATIO_ENGINE_DESIGN.md) · [`SCHEMA.md`](SCHEMA.md) ·
[`../rag/audit_model.py`](../rag/audit_model.py) · [`../rag/audit_checks.py`](../rag/audit_checks.py)

> **Naming.** The requirement calls this "Layer 2 — Financial structure". That is a layer of the
> **analysis** (what the auditor reads), not of the **pipeline**. `RATIO_ENGINE_DESIGN.md` already
> uses "Layer 2" for framework detection. To avoid a permanent collision this document uses
> **stages S0–S6** for pipeline steps and reserves "Layer N" for the ratio engine's meaning.
> The requirement's lens is referred to throughout as **the structure lens**.

---

## Contents

1. [What "the requirement is met" means](#1-what-the-requirement-is-met-means)
2. [Why this cannot be built first](#2-why-this-cannot-be-built-first)
3. [Principles inherited and added](#3-principles-inherited-and-added)
4. [Target architecture — S0…S6](#4-target-architecture--s0s6)
5. [S0 — The entity-year panel](#5-s0--the-entity-year-panel)
6. [S1 — Common-sizing](#6-s1--common-sizing)
7. [S2 — Structure metrics](#7-s2--structure-metrics)
8. [S3 — Drift detection](#8-s3--drift-detection)
9. [S4 — The signal catalogue](#9-s4--the-signal-catalogue)
10. [S5 — Concentration](#10-s5--concentration)
11. [S6 — Narration under guard](#11-s6--narration-under-guard)
12. [Thresholds — where every number comes from](#12-thresholds--where-every-number-comes-from)
13. [Framework applicability](#13-framework-applicability)
14. [Data contracts](#14-data-contracts)
15. [Enterprise concerns](#15-enterprise-concerns)
16. [Test and measurement strategy](#16-test-and-measurement-strategy)
17. [Rollout plan](#17-rollout-plan)
18. [Open decisions](#18-open-decisions)

---

## 1. What "the requirement is met" means

The requirement is not "produce common-size tables". It is: **before performance is judged, the
system reads the shape of the entity and raises planning signals a human auditor would raise.**

Acceptance is therefore defined on the *signals*, not the tables:

| # | Acceptance criterion |
|---|---|
| A1 | For an entity with ≥3 usable filings, the system emits a **structure profile**: asset mix, capital structure, liquidity structure, funding structure, concentration — each a set of shares with a multi-year series |
| A2 | Every share and every drift figure names the printed cells it came from, on both the numerator and the denominator side |
| A3 | Each of the five dimensions yields either a **planning signal** (`RISK_FLAG` / `AUDIT_POINTER`), an explicit **"nothing structural"** coverage note, or an **`ABSTAIN` with a reason and a queue entry** — never silence |
| A4 | A signal fires on **structural drift over the window**, not on a single-year level. Level-only observations are stated as context, never as a finding |
| A5 | Signals are **framework-aware**: a Division III (NBFC) entity is never told it has "no current ratio"; the liquidity dimension reports `NOT_APPLICABLE` with the framework named |
| A6 | Zero wrong signals on the golden set. A wrong structural signal misdirects the whole audit plan and costs more than a missing one |
| A7 | Reruns of the same entity-years reproduce byte-identical numbers and signals, with the profile/dictionary versions stamped |

A3 is the one that makes this enterprise-grade rather than a demo. The failure mode of an
analytics feature in an audit product is not a wrong chart — it is a **silently empty dimension**
that a reviewer reads as "nothing to see here".

---

## 2. Why this cannot be built first

The structure lens is a **consumer** of the fact layer, and the fact layer is not yet strong enough
to feed it. This is the single most important planning fact in this document, so it is stated
before the architecture.

| Structure lens needs | Measured today (`RATIO_ENGINE_DESIGN.md` §3) | Consequence if built anyway |
|---|---|---|
| `total_assets` as a denominator | binds 87.5% | usable |
| `total_current_liabilities` | binds **17.5%** | the entire liquidity + funding dimension is empty for 4 filings in 5 |
| `total_non_current_liabilities` | **no LineSpec exists** | capital structure cannot be closed |
| Both statements resolving | **160 / 346 (46.2%)** | half the corpus produces no structure profile at all |
| A failed tie-out downgrading a binding | **does not happen** (defect D1) | a shifted column becomes a confident "structural drift" signal — the worst output this system can produce |
| 3–5 comparable years per entity | **no entity-year panel exists anywhere in the repo** | there is no trend to analyse |
| Framework detection | hardcoded Division II | NBFC filings get a nonsense asset-mix profile (they have no current/non-current split) |

**Ranked prerequisites** (all already specified in the ratio engine doc; this document adds none
of its own to the fact layer):

1. **Verification + localization** (ratio doc §9) — without rejection, drift signals are computed
   on unvalidated numbers. Non-negotiable, and it is the safety foundation.
2. **Structural derivation** (§8/3d) + the missing `total_non_current_liabilities` spec — this is
   what takes `current_liabilities` from 17.5% to usable, and liquidity/funding depend on it.
3. **Cross-filing corroboration** (§5.5) — already needed for extraction QA, and it is *exactly*
   the entity-year join the structure lens needs. Build once, use twice.
4. **Framework profiles** (§7) — required for A5.
5. Soften the flavor filter (§6 of the rollout) — takes intake from 46% to a target ~85%.

The honest sequencing statement: **the structure lens is roughly a two-week build sitting behind
about three weeks of fact-layer work.** Attempting it earlier produces a feature that demos well
and is wrong often, which in an audit context is a liability, not a milestone.

---

## 3. Principles inherited and added

P1–P9 from [`RATIO_ENGINE_DESIGN.md`](RATIO_ENGINE_DESIGN.md#2-principles) apply unchanged — in
particular P1 (the LLM never does arithmetic or decides pass/fail), P3 (abstain > guess), P4
(never fabricate a zero) and P9 (no hand-picked thresholds).

Four additions specific to structure:

| # | Principle | Rationale |
|---|---|---|
| S-P1 | **A share is only as trustworthy as its denominator** | A 4pp move in "CWIP share" can come entirely from a mis-bound `total_assets`. Numerator *and* denominator carry verdicts; the share inherits the weaker of the two |
| S-P2 | **Comparability is a precondition, not an assumption** | Different units, restated comparatives, a merger, a changed framework, or standalone-vs-consolidated drift all invalidate a trend. The panel must *prove* comparability before any series is computed |
| S-P3 | **Signals describe shape, never cause** | "CWIP share rose 9pp over three years" is the output. "Project delays" is not — that is a hypothesis for the auditor, and asserting it is how the tool loses credibility |
| S-P4 | **A dimension with insufficient data reports that, loudly** | Per A3. An empty asset-mix panel and a healthy asset-mix panel must be impossible to confuse in the UI, the API and the memorandum |

---

## 4. Target architecture — S0…S6

```
                                                    GUARD ON FAILURE
──────────────────────────────────────────────────────────────────────────────
S0  Panel assembly        entity × year × canonical fact       ABSTAIN entity
      comparability gate: units · framework · flavor · restatement
S1  Common-sizing         each line ÷ its statement total      ABSTAIN dimension
S2  Structure metrics     shares, splits, mixes (deterministic) ABSTAIN metric
S3  Drift detection       level · delta · slope · persistence   —
S4  Signal rules          drift + corpus band → Finding         ABSTAIN signal
S5  Concentration         note-level + RPT extraction           ABSTAIN (default)
S6  Narration             LLM prose over finished numbers       WARN (numeric guard)
```

S0–S4 are pure Python over `CanonicalFact` / `BoundLine`. **No model call in the request path.**
S5 is the only stage where extraction AI is permitted, and it abstains by default. S6 is prose
only, and publishes no number the guard cannot match to a computed value.

Reuse, not reinvention:

| Existing asset | Used for |
|---|---|
| `binding.Resolver` / `BoundLine` | every numerator and denominator, with its verdict |
| framework profiles (ratio doc §7) | applicability, `not_applicable` sets, expected concept sets |
| `models.Finding` + `rag/audit_model.Finding` | the output taxonomy — no new finding type |
| `numeric_guard.py` | S6 |
| `tracing.py` | trace strings on every share |
| `recon`'s per-entity profile pattern | the entity panel's persisted comparability profile |

---

## 5. S0 — The entity-year panel

The missing primitive. Everything else is arithmetic once this exists.

### 5.1 Assembly

```python
# fs_db/panel.py
def build_panel(company: str, *, flavor: str, window: int = 5) -> Panel
```

1. Resolve all `documents` rows for the company (`DOC["company"]`, `fy_end`) — the registry already
   supports this; `cli.py docs COMPANY` does the lookup today.
2. For each filing, run the existing single-doc path to `BoundLine`s.
3. Join on canonical key × fiscal year. Each filing contributes **two** observations (current and
   prior column), giving an overlap that is the comparability evidence in 5.2.

### 5.2 The comparability gate

A trend across incomparable years is worse than no trend. Each check below either passes, or the
panel is truncated to the longest comparable run and the truncation is reported.

| ID | Check | On failure |
|---|---|---|
| C1 | **Unit coherence** — all years in the same scale, after normalising ₹ lakh / crore / million | rescale if declared and unambiguous; else `ABSTAIN` the panel |
| C2 | **Framework stability** — same `profile_id` across the window | split the panel at the change; analyse each side; emit a `COVERAGE_NOTE` naming the change |
| C3 | **Flavor coherence** — standalone throughout, or consolidated throughout | never mix (already a `fs_db` invariant); truncate to the coherent run |
| C4 | **Overlap agreement** — filing *Y*'s prior column equals filing *Y−1*'s current column | this is the cross-filing check (ratio doc §5.5). Disagreement ⇒ either a **restatement** (legitimate; flag it) or an **extraction error** (block that year) |
| C5 | **Restatement detection** — C4 fails but the filing's own SoCE / notes disclose a restatement | use the restated series, emit `AUDIT_POINTER` (restatements are themselves a planning signal) |
| C6 | **Window sufficiency** — ≥3 comparable years for drift, ≥2 for a delta | <3 ⇒ level-only context, no drift signals, explicit coverage note |
| C7 | **Entity continuity** — merger / demerger / scheme of arrangement disclosed | `COVERAGE_NOTE`; comparatives are not comparable and the panel says so |

C4 is doing double duty: it is the extraction regression suite *and* the restatement detector. That
is the highest-leverage single check in this design.

### 5.3 Persistence

The panel is expensive (N filings × full bind) and perfectly deterministic, so it is **computed
once and cached**, keyed by `(company, flavor, doc_id set, binder version, profile version)`. Any
version bump invalidates the key — no stale numbers can survive a code change. See §15.2.

Alongside it, persist the **entity comparability profile** (unit history, framework history,
restatement events, known merger dates), following the pattern `agent_for_grouping/recon` already
uses: year two on a known entity is near-instant and needs no re-setup.

---

## 6. S1 — Common-sizing

Each line as a percentage of its statement total.

| Statement | Denominator | Sourcing rule |
|---|---|---|
| Balance sheet | `total_assets` | must be `CONFIRMED` by ≥1 identity (`assets_split` or `bs_equation`), never a bare label match |
| P&L | `total_income` (revenue + other income) | if `total_income` is absent, derive and require it to tie to the printed total |
| Cash flow | not common-sized | shares of a net figure that can be near zero are meaningless — use absolute flows in S4/F4 instead |

Rules:

- **Denominator verdict propagates** (S-P1). `share.verdict = min(numerator, denominator)` on the
  ladder `CONFIRMED > STATEMENT_DISCREPANCY > UNCONFIRMED > SUSPECT_EXTRACTION`.
- **Shares must close.** Σ(component shares within a section) must equal the section share within
  the arithmetic tolerance of §12. A residual beyond tolerance means components are missing → the
  residual is materialised as an explicit `other_unallocated` bucket, never silently dropped. A
  large `other_unallocated` is itself reportable — the requirement's own example ("a rising
  non-current-**other** share") depends on this bucket existing.
- **Never fabricate a zero** (P4). A line absent from the statement yields `None`, not `0.0`; a
  line printed as nil yields `0.0`. The two render differently and drift is not computed across a
  `None`.

---

## 7. S2 — Structure metrics

Deterministic, framework-scoped, one module: `fs_db/structure.py`. Each metric is a frozen spec
mirroring `computations.RatioSpec` (same status ladder `OK | ABSTAIN | NOT_APPLICABLE`, same trace
discipline) so the existing report plumbing and tests carry over.

### 7.1 Asset mix

`ppe · cwip · intangibles · investments_nc · investments_c · inventories · trade_receivables ·
cash_and_equivalents · other_nc_assets · other_c_assets · other_unallocated` — each ÷ `total_assets`.

Derived: `cwip / ppe` (capitalisation backlog), `non_current_share`, `receivables_share`.

### 7.2 Liability and capital structure

`total_debt` (framework-defined component set — Division III has four separate debt lines, see the
ratio doc §12.3) · `total_equity` · `government_grants_and_support` · `retained_earnings` ·
`trade_payables` · `provisions` · `other_liabilities`, each ÷ `total_equity_and_liabilities`.

Derived: `debt_to_equity`, `debt / (debt + equity)`, `grant_share`,
`internal_accrual_share = Δretained_earnings / Δtotal_capital_employed`,
`maturity_mix = short_term_debt / total_debt`.

### 7.3 Liquidity structure

`current_assets_share`, `current_liabilities_share`, `net_working_capital = CA − CL`,
`nwc_share = NWC / total_assets`, `liquid_share = (cash + marketable_securities) / current_assets`.

`current_ratio` / `quick_ratio` already exist in `computations.py` and are **referenced, not
recomputed** — D11 (two divergent ratio engines) must not become D11′.

### 7.4 Funding structure

Sources-and-uses, built from year-over-year balance-sheet deltas and reconciled to the cash flow
statement:

```
uses    = Δ(non-current assets, gross) + Δ(working capital)
sources = OCF + Δdebt + Δequity + Δgrants + Δtrade_payables + Δother
gate    : |Σsources − Σuses| ≤ tolerance(n, magnitude)   else ABSTAIN the dimension
```

That gate is what makes the funding dimension trustworthy: an unreconciled sources-and-uses
statement is arithmetic that has not closed, and it abstains rather than attributing funding to a
residual bucket.

Derived: `capex_funding_mix` (OCF vs debt vs grant vs payables share of the period's uses),
`payables_days` and `payables_days` trend vs `inventory_days`.

### 7.5 Concentration

See §10 — different data source, different confidence regime.

---

## 8. S3 — Drift detection

> *"Structural drift — a persistent change in the shape — is often a stronger lead than any
> single-year ratio."* That sentence is the specification for this stage.

For each metric series over the comparable window, compute deterministically:

| Statistic | Definition | Role |
|---|---|---|
| `level` | latest value | context only, never a signal on its own (A4) |
| `delta` | latest − earliest, in percentage **points** | magnitude of the shape change |
| `slope` | least-squares slope, pp per year | direction and rate |
| `persistence` | count of consecutive same-sign year-over-year moves | separates drift from noise |
| `r2` | fit quality of the slope | a low `r2` with a large delta means a step change, not drift |
| `step_year` | largest single-year move, if it exceeds the rest combined | a step change is a *different* audit question from a drift |

**Drift is asserted only when direction and persistence agree.** Formally:

```
drift  ⇔  |delta| ≥ band(metric)  AND  persistence ≥ ⌈(n−1)/2⌉+1  AND  sign(slope) = sign(delta)
step   ⇔  |delta| ≥ band(metric)  AND  step_year explains > 60% of delta
noise  ⇔  otherwise → no signal, recorded as INFO
```

The distinction matters operationally: **drift** points at a trend to plan around; a **step**
points at one year's transaction to examine. Reporting a step as a drift sends the auditor to the
wrong place.

Percentage points, never percent-of-percent. "Receivables share rose from 8% to 12%" is +4pp, not
+50% — the latter is the classic way analytics features overstate a move.

---

## 9. S4 — The signal catalogue

Each row is one rule: deterministic inputs, a corpus-calibrated band, a fixed severity, a fixed
tag, and the fixed sentence it emits. Nothing here is improvised at runtime.

| ID | Dimension | Fires when | Tag | Emits |
|---|---|---|---|---|
| F1 | Asset mix | `cwip_share` drift up ≥ band, or `cwip/ppe` above corpus p90 for ≥2 years | `RISK_FLAG` | capitalisation / project-progress area; possible long-idle CWIP |
| F2 | Asset mix | `other_nc_assets_share` or `other_unallocated` drift up ≥ band | `AUDIT_POINTER` | growing unclassified balances — obtain the composition |
| F3 | Asset mix | `receivables_share` drift up ≥ band **and** receivables growth > revenue growth | `RISK_FLAG` | recoverability / ECL adequacy |
| F4 | Asset mix | `inventories_share` drift up while `revenue` flat or down | `RISK_FLAG` | obsolescence / NRV |
| F5 | Capital | `debt/(debt+equity)` drift up ≥ band | `RISK_FLAG` | leverage build-up; check covenants and finance-cost trend |
| F6 | Capital | `maturity_mix` (ST/total debt) drift up ≥ band | `RISK_FLAG` | refinancing dependence |
| F7 | Capital | `grant_share` above corpus p75 **and** `internal_accrual_share` ≤ 0 | `AUDIT_POINTER` | reliance on continued government support — feeds going-concern and the dependency lens |
| F8 | Liquidity | `NWC < 0` in the latest year (any framework with a current/non-current split) | `RISK_FLAG` | net current-liability position |
| F9 | Liquidity | `NWC < 0` **and** `Δnon_current_assets > 0` in the same year | `RISK_FLAG` **HIGH** | long-term assets funded by short-term liabilities — a maturity mismatch |
| F10 | Liquidity | `liquid_share` drift down ≥ band | `AUDIT_POINTER` | deteriorating quality of current assets |
| F11 | Funding | `payables_days` drift up ≥ band **and** OCF share of uses drift down | `RISK_FLAG` | operations funded through supplier credit / delayed payments — working-capital stress. Cross-check MSME disclosures |
| F12 | Funding | capex funded > corpus p75 by borrowing while OCF ≤ 0 | `RISK_FLAG` | growth funded entirely externally |
| F13 | Concentration | any concentration metric above its corpus band | `RISK_FLAG` | see §10 |
| F14 | Any | dimension computable but **no** rule fires | `COVERAGE_NOTE` | "structure read, nothing structural" — required by A3 |
| F15 | Any | dimension not computable | `ABSTAIN` + queue | names the missing canonical input and the filing year that broke it |

Every emitted `Finding` carries `trace` (the shares and the drift statistics), `evidence`
(table/page/row per contributing cell, per year) and the framework/dictionary versions. F9 and F11
are the two the requirement calls out by name, and both are cross-dimension — which is why the
signal layer must sit above all five dimensions rather than inside each one.

**Deduplication.** F3/F4/F11 frequently co-fire on a working-capital-stressed entity. Signals are
grouped into a single planning theme with the strongest as the head, following the SRS "deduplicate
linked issues" step, so a memorandum shows one working-capital theme, not four findings.

---

## 10. S5 — Concentration

Structurally different from S1–S4: the data is **not on the face of the statements**. It lives in
note tables (borrowings by lender, receivables ageing, investments schedule), the related-party
note, and segment disclosures — narrative-adjacent, inconsistently structured, and in the corpus
often only as prose.

Design consequences:

1. **Deterministic-first.** Where a typed note table exists in `table_chunks` and its total ties to
   the face, compute concentration exactly (top-1 / top-3 share, HHI). This is the only path that
   produces a `FINDING`-grade number.
2. **Extraction-assisted, abstain-by-default.** Where only prose exists, `rag/` retrieval may
   locate the disclosure, but the output is an `AUDIT_POINTER` with a quote and page reference —
   **never a computed concentration percentage**. P1 forbids the model producing the number.
3. **Government / related-party concentration is a defined sub-case.** For CPSUs, government
   receivables, government grants and related-party revenue are the dependency signal the
   requirement points at; these are individually named canonical concepts, not a generic top-N.
4. **Absence is reported.** "No customer concentration disclosure located" is a `COVERAGE_NOTE`,
   because for a single-customer PSU its absence is itself a disclosure gap worth raising.

Concentration is the dimension most likely to abstain in v1. That is the correct outcome, and §15.5
(the human queue) is what makes an abstain useful rather than a dead end.

---

## 11. S6 — Narration under guard

The LLM receives the finished structure profile — shares, drift statistics, fired signals — and
writes the planning narrative. It does not receive raw statements, and it computes nothing.

- **Input:** the S4 output object only.
- **Output guard:** `numeric_guard.py`, with the §11 fixes from the ratio doc (kind-scoped matching,
  magnitude-relative tolerance, matched-source reported). Structure adds a new *kind* — percentage
  **points** — which must be matched only against the pp pool, never against the ratio pool.
- **Safe wording:** reuse the existing lint (`audit_output.py`). Per S-P3, the narrative states
  shape and asks the question; it does not assert cause.
- **On abstain:** the narrative must say which dimension is missing and why. A narrative that
  quietly covers four of five dimensions violates A3.

---

## 12. Thresholds — where every number comes from

Per P9 and the ratio doc §13.2, every constant names its source: `STRUCTURAL`, `ERROR_MODEL`, or
`CALIBRATED(corpus@date)`.

| Constant | Source | Derivation |
|---|---|---|
| Share closure tolerance | `ERROR_MODEL` | `tolerance(n, magnitude) = n × 0.005 + REL_TOL × magnitude` — the same scaled tolerance the ratio doc specifies; a 60-row section cannot use a flat 0.05 |
| Sources-and-uses closure | `ERROR_MODEL` | same, over the delta count |
| `band(metric)` — the pp move that counts as drift | `CALIBRATED(corpus)` | **per metric**, the distribution of 3-year deltas across all comparable entity-windows in the 346-filing corpus; band = p90 of \|delta\|. A move is a signal because it is unusual *in this population*, not because someone likes the number 5 |
| Level bands (p75 / p90) | `CALIBRATED(corpus)` | empirical percentile of the metric across the corpus, computed per framework profile |
| `persistence` requirement | `STRUCTURAL` | ⌈(n−1)/2⌉+1 — a majority of the available year-over-year moves. No tuning parameter |
| `step` share (60%) | `CALIBRATED` | set on the golden set at the point where step/drift classification is unambiguous |
| Window length | `STRUCTURAL` | 3 minimum for drift, 5 preferred |

Two rules that keep this honest:

- **Bands are per framework.** A Division III NBFC's normal `debt/(debt+equity)` is a Division II
  manufacturer's crisis. One pooled distribution would flag every NBFC and no bank-like entity.
- **A band breach is a flag, never a suppression.** Genuine distress produces genuine outliers; the
  band decides *what to report*, never *what to hide*.

**Band provenance is versioned.** Bands are computed by a batch job, frozen to a data file with a
date stamp, and referenced by version in every `Finding`. Recomputing bands changes historical
outputs, so it is a deliberate, reviewed, version-bumped event — not a silent recalculation.

---

## 13. Framework applicability

Each `FrameworkProfile` (ratio doc §7.4) gains a structure section:

```python
structure_dimensions: frozenset   # which of the five apply
not_applicable:       frozenset   # e.g. {"liquidity"} for sch3_div3
mix_components:       tuple       # the component set that must close to 100%
debt_components:      tuple       # framework-defined; four lines under Division III
```

| Framework | Asset mix | Capital | Liquidity | Funding | Concentration |
|---|---|---|---|---|---|
| Sch III Div II (Ind AS) | ✓ | ✓ | ✓ | ✓ | ✓ |
| Sch III Div III (NBFC) | ✓ (financial / non-financial split) | ✓ (4 debt lines) | **NOT_APPLICABLE** — no current/non-current classification | ✓ | ✓ (a core dimension here) |
| Sch III Div I (AS) | ✓ (Fixed Assets / Investments / Current Assets) | ✓ (Shareholders' Funds) | ✓ | ✓ | ✓ |
| Common Format (autonomous body) | ✓ | Corpus/Capital Fund — no equity concept | ✓ | Receipts & Payments, not cash flow | ✓ |

Adding an entity class is authoring a data file, not editing `structure.py`. This is the difference
between a system that scales to hundreds of entities and one that accumulates special cases.

---

## 14. Data contracts

```python
Panel        company · flavor · years: tuple[str, ...]
             facts: dict[(key, year)] -> BoundLine
             comparability: list[Check]        # C1..C7, each pass/fail/reason
             truncated_from: tuple | None
             profile_id · profile_version · binder_version · panel_hash

Share        metric · year · numerator: BoundLine · denominator: BoundLine
             value_pct · verdict (inherited, S-P1) · trace · evidence

Series       metric · points: tuple[Share, ...]
             level · delta_pp · slope_pp_yr · persistence · r2 · step_year
             classification: DRIFT | STEP | NOISE | INSUFFICIENT

StructureProfile  dimension -> {status: OK|ABSTAIN|NOT_APPLICABLE,
                                series: list[Series], reason: str|None}
                  signals: list[Finding]         # existing taxonomy, no new type
                  bands_version · profile_version · aliases_version
```

Backward compatibility: `StructureProfile` is a new top-level key in the report payload; existing
`RatioReport.to_dict()` keys are untouched. UI changes are additive panels.

---

## 15. Enterprise concerns

The part that separates this from a notebook.

### 15.1 Determinism and reproducibility

No model call in S0–S4. Every output stamps `binder_version`, `profile_version`, `aliases_version`,
`bands_version` and `panel_hash`. Given the same versions and the same `doc_id` set, the numbers are
byte-identical years later (P2, and an audit-evidence requirement in its own right).

### 15.2 Batch, backfill and cost

Panel assembly is O(filings per entity) and the corpus is 346 filings across ~100+ entities. Design
for a **precompute job**, not request-time assembly:

- Nightly / on-ingest batch builds panels and structure profiles for all entities.
- Cache key includes every version stamp (§5.3) — a code change invalidates cleanly, and a stale
  number can never be served.
- API reads the cache; a cold entity computes on demand with a bounded timeout and returns a
  partial profile marked `PENDING` rather than blocking.
- The band-calibration job (§12) is separate, runs after backfill, and is version-bumped manually.

### 15.3 Observability

Trace S0–S6 through the existing Phoenix/OTel setup with per-stage spans and these operational
metrics, published on a dashboard:

| Metric | Why it is the one to watch |
|---|---|
| entities with a ≥3-year comparable panel | the real coverage number for this feature |
| comparability failures by check (C1…C7) | tells you *which* fact-layer problem to fix next |
| dimensions ABSTAIN by reason | drives the roadmap |
| signals fired per entity, by rule | a rule firing on 80% of entities is mis-banded, not insightful |
| C4 discrepancies | doubles as extraction-quality monitoring for the whole corpus |
| queue depth and age | the human-in-the-loop SLA |

### 15.4 Security and data handling

Both KBs stay **read-only**. Panels and profiles are written to the working-papers store
(`run_store.py` pattern), never back into the KB. Entity-level access control is inherited from the
existing API — but note that a structure profile is a **cross-year, cross-document** artefact, so
authorisation must be checked against every contributing `doc_id`, not just the requested entity.

### 15.5 Human-in-the-loop

Every `ABSTAIN` becomes a queue item with: entity, year, dimension, the missing canonical input, the
filing and page where it should be, and the one action that would resolve it. Resolutions feed back
as **persisted entity profile entries** (the `recon` pattern), so the same abstain does not recur
next year. Reviewer decisions are recorded in `working_papers/*.jsonl` alongside run manifests.

Without this loop, coverage is capped at whatever the binder achieves unaided, and every year costs
the same as the first.

### 15.6 Failure and degradation policy

| Situation | Behaviour |
|---|---|
| One year in the window fails extraction | truncate the window, report the truncation, still emit drift over the remainder if ≥3 years survive |
| Denominator `SUSPECT_EXTRACTION` | the whole dimension abstains — a bad denominator poisons every share (S-P1) |
| Bands file missing or version mismatch | serve levels and deltas, suppress all signals, emit a system coverage note. **Never fall back to a hardcoded band** |
| Framework ambiguous | no structure profile; the document already abstains upstream |
| Panel cache miss under load | partial profile marked `PENDING`; never a synchronous full backfill |

### 15.7 Governance

The signal catalogue (§9) and the bands (§12) are **audit methodology**, not code. They need a named
owner on the audit side, a review before each version bump, and a changelog — because a change to F5
changes what a statutory audit plan flags across every entity in the system.

---

## 16. Test and measurement strategy

**Nothing in §5–§11 ships before this exists.**

| Asset | Purpose |
|---|---|
| `fs_db/tools/panel_sweep.py` | build every entity panel, snapshot comparability outcomes and profiles; `--against baseline.json` shows exactly what moved |
| **Golden panels** — 5 entities × 5 years, human-confirmed shares for ~10 lines | the only measure of precision; also the calibration source for the step/drift boundary |
| **Signal adjudication set** — ~40 entity-windows where a CA has recorded "would you plan around this?" | the only way to know whether the *signals* are right, as opposed to the arithmetic |
| Synthetic drift fixtures | a constructed series with known slope/persistence proves S3 classification without touching the DB |
| C4 cross-filing regression | free, corpus-wide, and it protects the fact layer the whole feature stands on |
| Existing hermetic tests | must stay green throughout |

The metric:

```
primary    wrong structural signals published  →  0        (A6)
secondary  dimensions covered with a real signal or an explicit coverage note → maximise
tertiary   abstains carrying an actionable queue entry → 100%
```

A signal that fires on most entities is not a finding; it is a mis-set band. Track fire-rate per
rule as a first-class quality metric.

---

## 17. Rollout plan

Prerequisites first — these are the ratio-engine steps from its §16 that this feature hard-depends
on. Each step merges only if the sweep says it helped.

| # | Step | Depends on | Effort | Gate |
|---|---|---|---|---|
| **P0** | Verification + localization (ratio §9) | — | ~4h | zero wrong ratios on golden set |
| **P1** | Structural derivation + `total_non_current_liabilities` | P0 | ~4h | `current_liabilities` bind rate ≫ 17.5% |
| **P2** | Cross-filing check (ratio §5.5) | — | ~3h | discrepancy count; **this is C4** |
| **P3** | Soften flavor filter | — | ~1h | resolution 46% → ~85% |
| **P4** | Framework detection + profiles | — | ~1–2d | Division III stops mis-abstaining |
| 1 | `panel.py` — assembly + comparability gate (C1–C7) | P2, P3, P4 | ~3d | ≥N entities with a 3-year comparable panel |
| 2 | `structure.py` — S1 common-sizing + asset mix, capital structure | 1, P1 | ~3d | shares close on golden panels |
| 3 | S3 drift statistics + synthetic fixtures | 2 | ~1d | classification correct on fixtures |
| 4 | Band calibration job + bands data file | 2, 3 | ~2d | per-framework distributions published and reviewed |
| 5 | Liquidity + funding dimensions (incl. sources-and-uses gate) | 2, P1 | ~3d | S&U closes or abstains; F9/F11 reproduce on known cases |
| 6 | S4 signal catalogue + dedup into themes | 3, 4, 5 | ~2d | fire-rate per rule sane; zero wrong on adjudication set |
| 7 | Precompute + cache + observability | 6 | ~2d | full-corpus backfill inside the batch window |
| 8 | S6 narration + pp-aware numeric guard | 6 | ~1d | guard rejects an injected fabricated pp figure |
| 9 | Concentration (deterministic path only) | 6 | ~3d | ties to face or abstains |
| 10 | Concentration (retrieval-assisted pointers) | 9 | ~2d | no computed % from prose, ever |

Steps 1–3 and 8 need no new DB work. **Step 4 cannot be skipped or stubbed** — a hardcoded band is
exactly the hand-picked threshold P9 exists to prevent, and it would silently define what the whole
system considers unusual.

---

## 18. Open decisions

Each changes what gets built and needs a human answer.

1. **Window length and recency weighting** — is a 5-year window with equal weight right, or should
   the most recent year weigh more? Affects `slope` and every band.
2. **Standalone or consolidated as the primary lens?** Structure differs materially between them
   for a CPSU with subsidiaries. Running both doubles panel cost and the memorandum's length.
3. **Is `other_unallocated` reportable at what size?** It is the requirement's own example signal,
   but a large residual is often an extraction artefact — is the first response a finding or an
   extraction investigation?
4. **Do structure signals enter the `rag/audit_model.Finding` taxonomy** and flow into the
   memorandum, or stay in the `fs_db` report? (Same shape as ratio-doc open decision 5, and it
   should be answered once for both.)
5. **Who owns the signal catalogue and the bands** (§15.7), and what is the review cadence?
6. **Minimum viable framework scope for v1** — Division II only, or II + III? III is where
   concentration matters most and where a wrong asset-mix profile is most misleading.
7. **Peer/sector comparison** — explicitly a non-goal here, but the corpus makes it possible. Is it
   deferred or excluded?
