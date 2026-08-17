# FDR — Build Roadmap

**Spec:** `Adv Rag FDR/SRS/FDR Audit Planning Intelligence Specification v3.docx` (v3.0)
**Status:** pre-implementation. `fdr/` is empty; nothing in this document is built yet.
**Related:** [`../fs_db/STRUCTURE_ENGINE_DESIGN.md`](../fs_db/STRUCTURE_ENGINE_DESIGN.md) ·
[`../fs_db/RATIO_ENGINE_DESIGN.md`](../fs_db/RATIO_ENGINE_DESIGN.md) ·
[`../fs_db/appendix_g.py`](../fs_db/appendix_g.py) · [`../rag/audit_model.py`](../rag/audit_model.py)

---

## Contents

1. [Positioning — what the FDR is, and is not](#1-positioning--what-the-fdr-is-and-is-not)
2. [What already exists](#2-what-already-exists)
3. [The three gaps](#3-the-three-gaps)
4. [The build order, and why it runs backwards](#4-the-build-order-and-why-it-runs-backwards)
5. [M1 — The signal contract](#5-m1--the-signal-contract)
6. [M2 — The planning knowledge base](#6-m2--the-planning-knowledge-base)
7. [M3 — The walking skeleton](#7-m3--the-walking-skeleton)
8. [M4 — Input Quality Grade and Data Integrity Score](#8-m4--input-quality-grade-and-data-integrity-score)
9. [M5 — Business understanding](#9-m5--business-understanding)
10. [M6 — The entity-year panel](#10-m6--the-entity-year-panel)
11. [M7–M9 — Layers 2, 3, 4 as signal producers](#11-m7m9--layers-2-3-4-as-signal-producers)
12. [M10–M12 — Layer 5, prioritisation, integration](#12-m10m12--layer-5-prioritisation-integration)
13. [Milestone table and gates](#13-milestone-table-and-gates)
14. [Cross-cutting requirements](#14-cross-cutting-requirements)
15. [What not to build](#15-what-not-to-build)
16. [Decisions required](#16-decisions-required)

---

## 1. Positioning — what the FDR is, and is not

The FDR is the **fourth** specification in the suite, not a feature of the existing ones. Its
question is *"what do these statements imply for audit planning, risk assessment and the allocation
of audit effort?"* — distinct from the Financial Statement Analysis (FSA) question, *"what do the
statements contain, disclose and comply with?"*, which is what `fs_db` and `rag/` already implement.

| Spec | Repo home today |
|---|---|
| Financial Statement Analysis | `fs_db/` (recall, arithmetic, Schedule III, ratios) + `rag/` (audit spine, retrieval) |
| Trial Balance Analysis | `agent_for_grouping/recon/` |
| Statutory Auditor's Report Analysis | partially in `rag/` (CARO / auditor-report corpus) |
| **Financial Diagnostic Report** | **`fdr/` — empty** |

Three properties of the spec drive every decision below:

- **The deliverable is the audit-planning matrix (§10.4), not the diagnostics.** §6: *"A computed
  number that is not interpreted has no place in the FDR output; it belongs in the supporting
  schedules."* Ratios and structure are raw material.
- **Standalone-capable (§1.3).** A complete FDR must run from the financial statements alone.
  Corroboration from TB / FSA / auditor-report analysis is additive and never a precondition.
- **Non-duplicative (Appendix A).** The FDR does not reproduce the FSA ratio library, the Schedule
  III ratios, the account-area library, disclosure/compliance testing, the going-concern review, the
  red-flag catalogue or the materiality bands. Where FSA output exists, the FDR *consumes* it.

The structure lens designed in
[`../fs_db/STRUCTURE_ENGINE_DESIGN.md`](../fs_db/STRUCTURE_ENGINE_DESIGN.md) is **§7 of this spec** —
one of five layers, and a signal producer for Layer 5. It is correct to build; it is wrong to build
it standalone and first.

---

## 2. What already exists

More than the gap list suggests. The FDR should be assembled from these, not alongside them.

| Spec requirement | Existing asset | State |
|---|---|---|
| §8.1 computational substrate; Appendix G's 16 diagnostics | [`../fs_db/appendix_g.py`](../fs_db/appendix_g.py) | **Done.** Closed, fixed-shape, deterministic block. Its docstring records that the "let the LLM assemble the set" failure was already hit and solved here |
| Ratio computation and binding | `computations.py`, `ratio_pipeline.py`, `binding.py` | Built; correctness work outstanding (ratio doc §9, §16) |
| §4.3 Data Integrity Score components | `precheck.py`, `arithmetic.py`, `coverage_report.py` | Computed but **not graded and not surfaced** — defect D4 |
| §17 safe-language rules | `rag/audit_output.py` safe-wording lint | Reusable as-is |
| §16 explainability / no fabricated numbers | `fs_db/numeric_guard.py`, `tracing.py` | Reusable; needs the ratio-doc §11 fixes |
| §18.2 attributable, reproducible output | `rag/run_store.py` (run manifests, working papers) | Reusable |
| Finding taxonomy, assertions, source lineage | `rag/audit_model.py` | Reusable — the FDR needs **no new finding type** |
| Per-entity learned profiles, persisted and reused | `agent_for_grouping/recon` | The pattern to copy for business profile + entity comparability |

---

## 3. The three gaps

| # | Gap | What it blocks | Spec basis |
|---|---|---|---|
| **G1** | No **entity-year panel** — 3–5 comparable years of BS **+ P&L + Cash Flow** lines | Layers 2, 3 **and** 4 at once. §9.5 forbids trend statistics on fewer than three years, and *"a two-point movement is never presented as a trend"* | §4.1, §7, §8, §9.5 |
| **G2** | No **business understanding** — business model, revenue/cost/financing structure, value drivers, sector inherent-risk expectations | Everything. §2.1 *prohibits* interpreting any ratio before it is formed | §5, §2.1, §15.1 |
| **G3** | No **Layer 5** — clustering, risk-interaction, materiality filter, prioritisation, planning matrix | The deliverable itself | §10, §11, §14 |

G1 is larger than the structure lens alone implied: Layer 3 decomposition (DuPont, margin bridge)
and Layer 4 quality (accruals ratio, cash conversion, FCF) need multi-year **P&L and cash-flow**
series, not just balance-sheet lines. Build the panel once, for all three layers.

---

## 4. The build order, and why it runs backwards

The instinctive order is Layer 2 → 3 → 4 → 5. That is the wrong build order, for two reasons:

1. **Layer 5 defines the requirement for Layers 2–4.** Appendix D already names, for each of the six
   clusters, exactly which signals compose it. Any diagnostic no cluster consumes is not in scope;
   any signal every cluster needs is mandatory. Building the layers first produces diagnostics
   nobody consumes and misses ones every cluster needs.
2. **The spec's mandatory output is honest, not complete.** §4.4 requires a pre-analysis validation
   note and a "diagnostics not run, and why" list *whatever the grade*. A report that runs
   end-to-end and abstains on everything with reasons is **already spec-compliant**. Every
   diagnostic added after that is an increment against a frozen contract, gated by a real gate.

So: fix the contract and the output shape first, then fill them.

```
M1 signal contract  ──┐
M2 planning KB      ──┼──> M3 walking skeleton (everything ABSTAIN, spec-compliant)
M4 input grading    ──┘         │
                                ├──> M5 business understanding  (unblocks interpretation)
                                └──> M6 entity-year panel       (unblocks L2/L3/L4)
                                          │
                                          ├── M7 Layer 2 structure signals
                                          ├── M8 Layer 3 performance + decomposition
                                          └── M9 Layer 4 quality
                                                    │
                                                    └──> M10 Layer 5 clustering
                                                         M11 prioritisation
                                                         M12 integration + composites
```

M1–M4 need **no database and no model**. They are the fastest path to something a reviewer can
challenge, which is the point of §16.

---

## 5. M1 — The signal contract

**The first thing to build.** Appendix D is effectively a finished rule table; make it machine-readable.

Every signal gets an ID, the layer that produces it, its canonical inputs, its emission rule, and
the clusters that consume it. Twenty-two signals are named across the six Appendix D clusters —
that is the complete v1 target set for Layers 2–4.

### 5.1 The registry, derived from Appendix D

`avail` = can it be produced from face-of-statement lines via the panel (**F**), or does it need
note-level data (**N**), which is materially harder and abstains more?

| ID | Signal (Appendix D wording) | Layer | Cluster | avail |
|---|---|---|---|---|
| S01 | Deteriorating cash-conversion cycle | 4 | WC/liquidity | F |
| S02 | Payables funding growth | 2 | WC/liquidity | F |
| S03 | Net current-liability position | 2 | WC/liquidity | F |
| S04 | Weak or negative operating cash flow | 4 | WC/liquidity | F |
| S05 | Receivables outpacing revenue | 2/3 | receivable quality | F |
| S06 | Accruals-heavy earnings | 4 | receivable quality | F |
| S07 | Period-end revenue concentration | 3 | receivable quality | **N** (needs interim/segment) |
| S08 | Related-party receivable concentration | 2 | receivable quality | **N** (RPT note) |
| S09 | Ageing CWIP | 2 | asset/capitalisation | **N** (CWIP ageing note) |
| S10 | Rising non-current-other share | 2 | asset/capitalisation | F |
| S11 | Depreciation not moving with the asset base | 3 | asset/capitalisation | F |
| S12 | Impairment timing | 4 | asset/capitalisation | **N** |
| S13 | Leverage-driven ROE | 3 | funding/solvency | F (DuPont) |
| S14 | Short-term funding of long-term assets | 2 | funding/solvency | F |
| S15 | Finance cost not moving with borrowings | 3 | funding/solvency | F |
| S16 | Provision volatility and reversals | 4 | estimate quality | **N** (provisions movement) |
| S17 | Useful-life changes | 4 | estimate quality | **N** (policy note) |
| S18 | Non-cash gains | 4 | estimate quality | F |
| S19 | Other-income sustainability | 3/4 | estimate quality | F |
| S20 | High government-support dependency index | 4 | dependency/grant | F |
| S21 | Unspent-grant build-up | 2 | dependency/grant | **N** (grant note) |
| S22 | Administered-pricing reliance | 1 | dependency/grant | **N** (business understanding) |

Fourteen of twenty-two are face-derivable. **That is the honest v1 scope**, and it is enough: every
one of the six clusters has at least one face-derivable contributing signal, so no cluster is
structurally unreachable. The note-derived signals arrive in a second wave and, until they do, each
cluster's package states which contributing signals could not be evaluated (§4.4).

> **Built.** This registry is implemented — [`signals.py`](signals.py), [`clusters.py`](clusters.py),
> [`assertions.py`](assertions.py), validated at import and by
> [`test_registry.py`](test_registry.py). `python -m fdr` prints the contract report;
> `python -m fdr --gaps` prints only the outstanding audit-authoring work. Stdlib only, no DB,
> no model. RC-DEP is the thinnest cluster — one face signal of three.
>
> **Extended by [`derivations.py`](derivations.py).** The registry says WHAT each signal is;
> the derivation registry says HOW it is computed and WHERE in the annual report its inputs
> live — statement, note, or mandatory Schedule III schedule — together with the proxies that
> stand in for lines an Indian annual report does not disclose separately, the reconciling
> items to eliminate before a signal is raised, and the sector overlays that MODIFY a
> derivation rather than suppressing it. Import-time validation binds the two registries: a
> signal with no derivation, or a derivation whose required inputs or window disagree with the
> registry, fails at import. `python -m fdr derivations` prints it; `--signal S15` prints one.
>
> Four **supporting derivations** (ECL adequacy against the receivables ageing, borrowing-cost
> capitalisation, covenant and default exposure, grant-condition exposure) and four
> **cross-cutting reads** (the Schedule III ratio note's >25% variance explanations, the MD&A
> physical drivers §8.2 needs, the §12 assurance corroboration boundary, and the persistence
> requirement) live there too. They are deliberately OUTSIDE the closed Appendix D set —
> promoting one into `signals.py` changes what the system flags for every entity and is
> decision 3 in §16, an audit call rather than an engineering one.

### 5.2 What a signal declaration carries

```python
Signal   id · layer · title · clusters: tuple
         inputs: tuple[canonical_key]      # what the fact layer must supply
         window: int                        # years required; ≥3 for any trend signal
         rule: Callable                     # deterministic, pure
         severity: HIGH|MED|LOW             # of the risk, NOT the confidence
         availability: FACE | NOTE
         basis: str                         # the spec sentence it implements
         not_applicable_frameworks: frozenset
```

Rules are pure Python over panel facts. **No model in the signal path** — P1 of `finance-core`.
Signal emission and *confidence* are separate outputs (§14.1 below).

### 5.3 Reconciling with the structure-engine catalogue

F1–F15 in [`../fs_db/STRUCTURE_ENGINE_DESIGN.md`](../fs_db/STRUCTURE_ENGINE_DESIGN.md#9-s4--the-signal-catalogue)
map onto S02, S03, S05, S10, S14, S21 and a few beyond Appendix D. Any structure signal with **no
consuming cluster** is either evidence that Appendix D needs an addition (an audit-side decision,
§16) or is out of scope. It does not get built on the strength of being interesting.

---

## 6. M2 — The planning knowledge base

Static, versioned data files. Authorable **today**, no code dependency, reviewable by the audit side.

| File | Source | Content |
|---|---|---|
| `clusters.yaml` | App D, §10.1 | 6 clusters → contributing signal IDs → affected assertions |
| `assertions.yaml` | §10.3 | the eight assertions and their definitions |
| `evidence.yaml` | App F | cluster/signal → evidence to request → likely specialist |
| `sectors.yaml` | App B, C, §13 | business model → value drivers, funding structure, emphasised diagnostics, **suppressed** diagnostics, estimate-heavy areas |
| `alt_explanations.yaml` | §2.2, §10.3 | per signal: the non-error explanations and what would distinguish them |
| `safe_language.yaml` | §17.1, §17.2 | tempted-to-say → must-say table; the prohibited-word list |
| `confidence.yaml` | App H | the High/Med/Low rubric and its inputs |
| `priority.yaml` | §11.1 | the nine priority dimensions and the disclosed combination method |

**This is audit methodology, not code.** Per §18.2 and §16 it needs a named owner on the audit side,
review before each version bump, and a changelog — a change to `clusters.yaml` changes what the
system flags across every entity. Every FDR output stamps the KB version it used.

Two spec rules that must be encoded here, not in prose:

- **Suppression is as important as emphasis** (§13.1, §15.2). The power-utility worked example
  (§20) turns on *not* flagging high receivables for a tariff-based utility. `sectors.yaml` must
  carry an explicit suppression list per business model, and suppression must be **stated in the
  output**, not silent.
- **No fabricated benchmarks** (§13, §4.2). Peer / sector / tariff values exist only as supplied
  inputs with source and date. There is no default, no fallback, no "typical" value anywhere in the
  KB.

---

## 7. M3 — The walking skeleton

Run the full pipeline end-to-end with **every diagnostic abstaining**, producing:

- §14.1 blocks 1–10 in order;
- the §14.3 machine-readable JSON, schema-valid;
- a complete `diagnostics_not_run` list with a reason per entry;
- the mandatory pre-analysis validation note and standing caveats (§17.3).

This is spec-compliant on day one. It also forces, early, the three things that are hardest to
retrofit: the output contract, the safe-language discipline, and the source trace on every item.

Gate: a reviewer reads the empty FDR and can state exactly what the system would need in order to
say anything. If they cannot, the coverage note is not doing its job.

> **Built**, and **taken ahead of M2** — M2 is audit authoring that waits on people, M3 is
> engineering that does not. [`model.py`](model.py) · [`evaluate.py`](evaluate.py) ·
> [`assemble.py`](assemble.py) · [`render.py`](render.py) · [`safe_language.py`](safe_language.py),
> with 18 checks in [`test_skeleton.py`](test_skeleton.py).
> `python -m fdr skeleton "Entity" FY2023-24 FY2024-25` (add `--json` for the §14.3 payload,
> `--model` / `--framework` to exercise suppression and applicability).
>
> Two behaviours are **real already**, because neither needs the panel: `NOT_APPLICABLE` is decided
> from the reporting framework, and `SUPPRESSED` from the business model. Both are stated in the
> output and named in the cluster's reason — a silently suppressed signal is indistinguishable from
> a missed one, which is the failure §15.2 exists to prevent.
>
> The §17.2 lint runs on the rendered report before release and is a hard fail, not a warning.

---

## 8. M4 — Input Quality Grade and Data Integrity Score

§4.3 is mandatory and **caps the confidence of every diagnostic downstream** — so it must exist
before any diagnostic is trusted, not after.

Most of the work is already computed and thrown away:

| §4.3 measure | Existing source |
|---|---|
| Data Integrity Score | `arithmetic.py` (bs_equation, footing, note_to_face, cash_flow), `precheck.py` |
| Financial-statement completeness | `repository.py` statement selection, `coverage_report.py` |
| Line-item mapping confidence | `binding.py` `BoundLine` provenance + verdict |
| Extraction confidence | ratio doc §5 fidelity verdicts (`EXACT` / `DEGRADED` / `CORRUPT`) |
| Framework and entity-type confidence | ratio doc §7 framework detection |

Build: a single `grading.py` that consumes these and emits the A–E grade plus the per-figure
mapping confidence, with the rule from §4.3 enforced mechanically — *a low-confidence mapping must
not independently drive a high-risk conclusion.*

**Confidence and severity are separate axes and must never be merged** (§4.4, App H). This is the
one cross-cutting model decision that has to be made once, centrally, and is listed in §16.

---

## 9. M5 — Business understanding

§2.1 makes this a hard precondition: *"Beginning interpretation with ratios, before business
understanding, is prohibited."* Nothing in the repo does it today.

This is the **one place in the FDR where an LLM is the right tool** — classification of a messy
input, not arithmetic and not a pass/fail decision. It must therefore:

- classify from the statements and notes into the §5.1 business-model set, with a stated confidence;
- **abstain** rather than guess, and apply diagnostics cautiously where confidence is low (§5.1);
- run **once per entity**, be human-confirmed, and persist to an entity profile reused next year —
  the `recon` pattern. Year two is a diff, not a re-classification;
- never import outside information about a named entity from memory (§18.2). The classification is
  grounded in the filing, with a source trace.

Output: business model, revenue model, cost structure, financing structure, value drivers, sector
inherent-risk expectations (§5.2, §5.3) — the interpretive lens every later layer reads through, and
block 3 of the report.

---

## 10. M6 — The entity-year panel

As specified in
[`../fs_db/STRUCTURE_ENGINE_DESIGN.md` §5](../fs_db/STRUCTURE_ENGINE_DESIGN.md#5-s0--the-entity-year-panel),
with scope widened to P&L and cash-flow lines. The comparability gate (C1–C7) and the cross-filing
check are unchanged and carry directly.

Its prerequisites are the ratio-engine fact-layer steps (P0–P4 in that document's rollout). Those
remain the critical path for every number the FDR publishes, and no amount of Layer 5 work removes
them.

§9.5 is the hard rule this milestone enforces: **fewer than three years ⇒ trend diagnostics are
marked *not run*, never computed on a short series.**

---

## 11. M7–M9 — Layers 2, 3, 4 as signal producers

> **All fourteen face-derivable signals have a rule** — [`rules.py`](rules.py),
> [`thresholds.py`](thresholds.py), with 44 checks in [`test_rules.py`](test_rules.py). Each
> rule is a pure function of the panel, returns an `Outcome` carrying observation, trace,
> confidence and the proxies actually applied, and abstains rather than guessing.
>
> Four corrections the derivation table forced, each of which had been silently wrong:
> S06 divides by **average** total assets, not the closing balance; S18 divides by **PBT**,
> not PAT; S15 leads with the **implied rate on average borrowings** rather than a growth
> divergence alone; and S01's payables leg **discloses** its purchases proxy and is capped at
> MEDIUM for it. S13 now traces each DuPont factor across every year rather than comparing
> endpoints, which is what §8.2 asks for and what makes the §18.1 decomposition case testable
> — it is no longer in `test_spec_cases.BLOCKED`.
>
> **Two of the fourteen cannot run on any entity in the corpus**, and the cause is the fact
> layer, not the rule: `other_non_current_assets` (S10) has no `LineSpec` in
> [`../fs_db/binding.py`](../fs_db/binding.py) at all, and `total_non_current_assets` (S14)
> has one that binds for zero entities. Both abstain naming the missing key. Closing them is
> fs_db binder work.
>
> The eight note-derived signals have **no rule and will not get one** until note-level
> extraction lands — a rule over figures the fact layer cannot supply would abstain forever or
> guess. They carry their derivation and source schedules instead, so the abstain is a work
> instruction rather than a shrug.

Each layer implements only the signals the M1 registry assigns to it.

- **M7 · Layer 2 — structure** (§7). The structure-engine design, minus its standalone signal
  catalogue, emitting S02, S03, S05, S10, S14, S21.
- **M8 · Layer 3 — performance and decomposition** (§8). The distinctive requirement is §8.2:
  *"always decompose before flagging."* DuPont / extended DuPont, ROCE, margin bridge, and the
  structural-vs-temporary and operating-vs-financing split. S13's whole point is that a
  leverage-driven ROE gain is reclassified from a performance story to a solvency one — the
  decomposition **changes which cluster the signal joins**, so it cannot be an afterthought.
- **M9 · Layer 4 — quality** (§9). Accruals, cash conversion, FCF and capex coverage, recurring vs
  one-off, capitalisation behaviour, working capital as an interacting system, dependency and
  estimate quality.

Composites (§9.4) are explicitly deferred — see §15.

---

## 12. M10–M12 — Layer 5, prioritisation, integration

> **M10 and M11 are built** — taken ahead of M4–M9 because the planning package, the matrix and
> most of the ranking are authoring and logic, not data. [`planning.py`](planning.py) ·
> [`priority.py`](priority.py), with 18 checks in [`test_planning.py`](test_planning.py).
>
> **Review mode** is the reason it was worth doing early:
> `python -m fdr skeleton "Entity" FY2024-25 --assume S02,S05,S21` asserts signals present so the
> audit side can read and correct the planning packages, matrix rows and rankings the system would
> produce — before any engine exists to produce them. The output is banner-marked throughout, the
> assumed signals are named in the coverage block, and the payload's pipeline version records it.
> Business-model suppression still wins over an assumption, and is tested.
>
> **Two things the build had to settle.** The specification tabulates no candidate responses
> anywhere, and Appendix F has no row for the working-capital cluster — so that content is drafted
> in `planning.py` and tagged `PROPOSED`, listed by `python -m fdr contract --gaps`, pending audit
> sign-off. And a priority dimension that cannot be assessed scores `None`, not zero: scoring it
> zero would penalise a cluster for the system's own missing data. The count of assessed dimensions
> is published with every rank, so a rank resting on six of nine is visibly weaker than one resting
> on nine.

- **M10 · Clustering (§10.1–10.3).** Signals → clusters via `clusters.yaml`, de-duplicated into one
  theme per underlying issue. Each cluster is assembled into the full §10.3 package. §10.2's
  risk-interaction matrix — reinforcing raises priority, offsetting moderates and is stated.
  §15.2's false-positive rules (sector relevance, one-off identification, business-model
  consistency) are applied here, each recorded with its reason.
- **M11 · Prioritisation (§11).** The nine dimensions, a disclosed and reproducible combination
  method, the **by-nature override** (regularity / propriety / public interest elevates a
  quantitatively small matter), a shortlist proportionate to audit capacity, and per-item reasoning
  for why it ranks where it does *and why another ranks lower*. §11.2: a bare composite score is not
  an acceptable output.
- **M12 · Integration (§12).** Consume TB / FSA / auditor-report outputs as corroboration, using the
  five-way relationship classification. The rule that shapes the data model: **a contradiction is
  retained, stated with both positions and sources, and referred — never averaged away.** That means
  corroboration is stored as a list of positions, not folded into a single score.

---

## 13. Milestone table and gates

| # | Milestone | Needs DB | Needs model | Effort | Gate |
|---|---|---|---|---|---|
| M1 | Signal contract | no | no | ~2d | every Appendix D signal has an ID, inputs and a consuming cluster; every registry entry traces to a spec sentence |
| M2 | Planning knowledge base | no | no | ~3d + audit review | audit-side owner signs off; suppression lists present per sector |
| M3 | Walking skeleton | no | no | ~3d | schema-valid JSON; all 10 blocks; reviewer can state what is missing and why |
| M4 | Input grade + integrity score | yes | no | ~2d | grade caps confidence mechanically; §4.4 note generated on a real filing |
| M5 | Business understanding | yes | **yes** | ~4d + review | classification abstains under threshold; persisted profile reused on rerun |
| M6 | Entity-year panel | yes | no | ~3d | N entities with a ≥3-year comparable panel; C4 discrepancy count |
| M7 | Layer 2 signals | yes | no | ~3d | 6 face signals emit with trace; shares close |
| M8 | Layer 3 + decomposition | yes | no | ~4d | leverage-driven-ROE example (§20) reclassifies correctly |
| M9 | Layer 4 quality | yes | no | ~4d | accruals / cash-conversion reproduce on golden entities |
| M10 | Clustering + interaction | no | no | ~3d | weak-signals-combine case (§18.1) raises the cluster, not the isolates |
| M11 | Prioritisation | no | no | ~2d | ranking reproducible from stated inputs; by-nature override fires |
| M12 | Integration + corroboration | yes | no | ~3d | contradiction case (§20) retained, not averaged |
| M13 | Composites | no | no | ~2d | components shown; guardrail wording present; never a bare score |

**Prerequisite, running alongside from the start:** the ratio-engine fact-layer steps P0–P4
([`../fs_db/RATIO_ENGINE_DESIGN.md` §16](../fs_db/RATIO_ENGINE_DESIGN.md#16-rollout-plan)). M6 and
everything after it depend on them.

---

## 14. Cross-cutting requirements

These apply to every milestone and are tested in the §18.1 evaluation cases.

0. **Every diagnostic states its derivation and its source, whatever its status.** `formula`,
   `ar_source`, `proxies_used` and `reconciling_items` travel on every `SignalResult` — fired,
   not fired, abstained, suppressed or not applicable — into the §14.3 JSON, the rendered
   report (blocks 4, 5, 6 and 9) and the exported matrix. An abstain that does not say what
   would have been computed and which schedule holds the inputs is not §4.4-compliant.
1. **Confidence ≠ severity** (§4.4, App H). Two independent fields on every diagnostic, signal,
   cluster and ranked item. A high-severity risk on a low-confidence input is reported as
   *important-if-true*, with the evidence that would raise confidence. Neither may inflate the other.
2. **Every ranked item states the full chain** (§14.2): observation → interpretation → risk →
   assertion → confidence → recommended response → evidence required.
3. **Reproducibility** (§16, §18.3). Same inputs and thresholds → same output. All version stamps —
   KB version, signal registry version, framework profile, binder, panel hash — travel on the output.
4. **Safe language** (§17). Enforced by lint on the generated narrative, not by prompt alone.
   Prohibited words are a hard fail, not a warning.
5. **No fabrication** (§15.3, §13). No peer benchmark, sector norm, probability or legal conclusion
   is ever synthesised. `numeric_guard` extends to cover the FDR narrative.
6. **Leads, not findings** (core principle, §17.3). The standing caveats appear in every output.
7. **Non-duplication** (§18.3, App A). An acceptance criterion in its own right: the FDR output must
   not reproduce FSA compliance or disclosure content.

---

## 15. What not to build

| Don't | Why |
|---|---|
| The structure engine as a standalone first deliverable | It is §7 of five layers and its output is not a deliverable. Fold it in at M7 against the M1 contract |
| More ratios, or the Schedule III ratio set | Appendix G is done in [`../fs_db/appendix_g.py`](../fs_db/appendix_g.py); §8.1 and App A forbid re-deriving the FSA library |
| The account-area library, disclosure or policy review, the going-concern review, the red-flag catalogue, materiality bands | All owned by the FSA specification (App A). Consuming them is in scope; reproducing them is a spec violation |
| Composites — distress, earnings-management, dependency index (§9.4) | The highest-risk output in the document. They need components, comparison basis, guardrail wording and per-component confidence in place first. **M13, last** |
| A scoring model that outputs a rank without narrative | §11.2 and §16: black-box scoring is prohibited; a bare composite is not an acceptable output |
| Any note-derived signal before the face-derived twelve work | Ten of twenty-two signals need note-level extraction. Attempting them early converts a solvable problem into an extraction project |

---

## 16. Decisions required

1. **Confidence model.** The FDR needs qualitative High/Med/Low per diagnostic, capped by the input
   grade and held separate from severity. `fs_db` has verdicts (`CONFIRMED` / `STATEMENT_DISCREPANCY`
   / `SUSPECT_EXTRACTION` / `UNCONFIRMED` / `NOT_APPLICABLE`). The mapping between the two must be
   defined once, centrally, and owned — it is the axis every ranked item carries.
2. **Business-model classification ownership.** Who confirms the first batch of entity
   classifications, and what confidence floor triggers an abstain? Without a confirmer, M5 cannot
   close and §2.1 blocks every layer behind it.
3. **Appendix D additions.** Structure-lens signals with no consuming cluster (e.g. rising
   `other_unallocated`, liquid-asset quality drift) — extend `clusters.yaml`, or leave out of scope?
   This is an audit-methodology call, not an engineering one.
4. **Standalone or consolidated as the FDR's primary lens** — the same question as the structure
   doc's open decision 2, and it should be answered once for both.
5. **Shortlist size.** §11.2 requires a shortlist "proportionate to the audit team's capacity". That
   number is a parameter someone must set, and it determines what the prioritisation engine is
   tuned to.
6. **Who owns the planning knowledge base** (§6 above), and at what cadence is it reviewed?
