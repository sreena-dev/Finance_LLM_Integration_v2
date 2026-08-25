# FDR — closing the gap between the engine matrix and the reference report

**Reference:** [`../SRS/fdr_report_ca.html`](../SRS/fdr_report_ca.html) — the ONGC FY2025-26
report, **hand-authored** as the presentation target. Its block 7 carries seven entity-specific
clusters with note-anchored figures.

**Engine when this was written:** six generic Appendix D clusters, of which four ever populate;
73 fired signals across 3,124 evaluations; 53 of 142 runs produce a non-empty matrix.

> **Sections 1 and 2 are the ANALYSIS as it stood before the work, kept unedited so the
> reasoning can be checked against what actually happened. [§2A](#2a-build-status--what-was-done-and-what-it-measured)
> records what was built and what it measured — read it for current state.**

**Spec:** §10.3, §10.4 and Appendix E of `Adv Rag FDR/SRS/FDR Audit Planning Intelligence
Specification v3.docx`.

**Companion:** [`ROADMAP.md`](ROADMAP.md) — this document is its M6–M10 continuation, scoped to
one question: *what has to be true for the engine to emit the reference report's rows?*

---

## 1. Row-by-row gap

Each reference row, against the engine that would have to produce it.

| # | Reference cluster | Engine home | Signals required | Reachable today? |
|---|---|---|---|---|
| 1 | Reserve-linked depletion & impairment estimate quality | RC-EST + RC-CAP | S11 (face, **fires**), S12 (note), `OV-OG` depletion-vs-reserves | **partial** — fires as a generic depreciation signal, not as this theme |
| 2 | Decommissioning / site-restoration provisioning | RC-EST | S16 (note) | no — provisions-movement note |
| 3 | Contingent-liability & arbitration exposure | **no cluster exists** | new | no — new cluster + note |
| 4 | Exploratory-well capitalisation & CWIP ageing | RC-CAP | S09 (note), `OV-OG` | no — CWIP ageing note |
| 5 | Revenue & receivable quality | RC-REC | S05 (**fires**, 10 entities), S06, `D-ECL` | **yes** |
| 6 | Investment-income dependency & FVOCI volatility | **no cluster exists** | S19 (face), S08 (note) | no — new cluster + `investments` key |
| 7 | Administered-price & government-support dependency | RC-DEP | S20 (face, `NOT_COMPUTABLE`), S22 (note/business) | no — dependency inputs never bind |

**One of seven rows is reachable on today's data.** The other six fail for three distinct
reasons, and it matters which: two need a cluster that Appendix D does not define, four need
note-level figures, and one needs a face line the binder does not bind.

---

## 2. The four gaps behind those rows

### N-A · The note fact layer — the large one, but *not* an extraction project

`NEEDS_NOTE_EXTRACTION` blocks 1,136 of 3,124 evaluations and starves RC-EST and RC-DEP
completely. The roadmap warns (§15) that attempting note signals early "converts a solvable
problem into an extraction project." That warning is now stale in one important respect:

> **The reader already exists.** [`../fs_db/repository.py`](../fs_db/repository.py) has
> `note_index()`, `parse_note()` and `find_note_table()`, working over the ~22k untyped
> `is_financial` table_chunks, flavour-coherent, with the note number parsed from the title.
> Note schedules are already fetchable and already parse to `ParsedTable`.

What is missing is not extraction. It is (a) **note `LineSpec`s** in
[`../fs_db/binding.py`](../fs_db/binding.py) — 46 specs today, every one of them face-level — and
(b) note facts **materialised into `fs_facts`** by [`../fs_db/facts.py`](../fs_db/facts.py) so the
panel can serve them. That is binder and store work against a reader that is already built.

**The highest-yield target is the Schedule III mandatory ageing schedules**, because they are
structurally uniform across every filer rather than free-form:

| Note (ONGC) | Schedule | Feeds |
|---|---|---|
| 8 | CWIP ageing (mandatory) | S09 — reference row 4 |
| 12 | Trade-receivables ageing (mandatory) | `D-ECL`, row 5 confidence |
| 14, 24 | Provisions movement + site-restoration fund | S16 — reference row 2 |
| 48 | Impairment (Ind AS 36) | S12 — reference row 1 |
| 50 | Reserves / standardised measure | `OV-OG` depletion basis — row 1 |
| 11, 49 | Investments + related-party | S08 — reference row 6 |
| — | Contingent liabilities & commitments | reference row 3 |
| 30 | Revenue disaggregation | S22 — reference row 7 |
| 53.2 | Schedule III ratios + >25% variance explanations | `X-SCH3RATIO` (already declared) |

Six of these nine are Schedule III mandated in a fixed shape. Build the ageing-schedule parser
once and rows 2, 3, 4 and 5 all improve together.

### N-B · The cluster set is generic; the reference is sector-specific

Two separate problems hide behind this, and they must not be solved with one mechanism.

**B1 — vocabulary.** Rows 1, 4 and 7 are Appendix D clusters *named in the entity's own terms*.
RC-CAP for a petroleum entity is "Exploratory-well capitalisation & CWIP ageing"; the same cluster
for a manufacturer is not. This is a naming and framing overlay on an unchanged cluster, driven by
the business model already classified in [`../rag/fdr_business.py`](../rag/fdr_business.py) —
`petroleum` is in the taxonomy, and [`derivations.py`](derivations.py) already carries an `OV-OG`
overlay targeting exactly S09/S11/S12. **Cheap, no methodology decision, no new data.**

**B2 — two clusters Appendix D does not contain.** Reference rows 3 (contingent liability /
arbitration) and 6 (investment-income dependency / FVOCI) have no home in the six. This is
ROADMAP §16 decision 3 — *"extend clusters.yaml, or leave out of scope?"* — and it is an
**audit-methodology call, not an engineering one**. Adding a cluster changes what the system flags
for every entity in the corpus. It needs the audit-side owner's sign-off before code.

Note that row 3 is admitted by nature, not by value: contractor claims disclosed and not provided.
The by-nature override in [`priority.py`](priority.py) already implements exactly this, so once
the cluster exists the ranking behaves correctly without new ranking logic.

### N-C · The eight note-derived signal rules

S07, S08, S09, S12, S16, S17, S21 and S22 carry derivations and source schedules but no rule, and
correctly abstain. Each becomes writable the moment N-A supplies its inputs. They are pure
functions of the panel like the fourteen already in [`rules.py`](rules.py) — same shape, same
tests, no new architecture.

### N-D · Face binder coverage — the cheapest win, and it is not small

`INPUT_NEVER_BOUND` blocks **910 evaluations**, and the missing keys are keys that bind
*for other entities*: `cost_of_materials_consumed`, `ocf`, `trade_receivables`, `revenue`,
`long_term_borrowings`, `total_assets`. This is per-entity binder fragility, not absent data.
It silently kills S01, S02, S06, S13 and S14 on most of the corpus.

Fixing it lifts the existing four clusters on entities that already have seven years of facts,
**with no note work and no methodology decision.** It is the only workstream with no dependency
and no sign-off gate, and it should start first.

---

## 2A. Build status — what was done, and what it measured

Everything below was implemented and measured against the live corpus (346 filings, 50
entities) on 2026-08-13. Numbers are from the runs recorded in this section, not estimates.

### N-D · Face binder coverage — DONE

Four defects found, each by following a single failing figure rather than by reading code:

| # | Defect | Where | Effect |
|---|---|---|---|
| 1 | A restatement-reconciliation NOTE outscored the real cash-flow statement, because the generic "contains the word *total*" tie-breaker beat a statement that never prints it | `repository.py` — `_completeness`, `_NOT_THE_STATEMENT_KW` | statement selection |
| 2 | Each period printed across TWO physical columns (line items inner, subtotals outer) was read as two separate periods, filing every subtotal under the wrong year | `md_parser.py` — `_period_column_blocks` | **`ocf` recorded as the prior year's figure on a filing that parses perfectly** |
| 3 | Chunks of one split statement were concatenated and given a single column profile, though the halves have different layouts | `ratio_pipeline.py` — `_merge_chunkwise` | half a statement, or a corrupted one |
| 4 | A section whose printed total lost its LABEL in extraction could never tie, so every `subtotal_of_children` figure inside it stayed untrusted | `binding.py` — `_SECTION_TOTAL_KEY`, held-for-sale handling | **`trade_payables` missing on 241 of 346 filings** |

Defect 2 is the serious one: the figure was real, correctly parsed, and attached to the
wrong period, so nothing downstream could detect it. ONGC FY2023-24's operating cash flow
was being recorded as FY2022-23's.

A fifth change was tried and **backed out**: matching statement candidates on `section` as
well as `table_title`. It fixes filings whose title is mis-attributed (BPCL FY2023-24 titles
its balance sheet correctly but its `section` names the NEXT table), and measured over the
corpus it made coverage worse on eleven keys, because `section` in this corpus is largely a
forward reference. What survived from it is `_is_a_different_statement`, which stops a
balance sheet ever being selected as a P&L — an A/B over 60 filings showed it costs nothing
and it closes a real hazard.

**Measured effect, ONGC:** 8 of 14 face signals ready before, **13 of 15 after**. The two
still blocked (S01, S02) need `cost_of_materials_consumed`, which an exploration-and-
production company does not report — abstaining is correct there.

**Measured effect, corpus:** 30 of 50 entities now have at least one reachable cluster.
Seven of the eight clusters are reachable somewhere, against four that ever fired before.

*Not fixed:* 20 entities still have zero ready face signals, and
`cost_of_materials_consumed` is unbound on 235 filings. A distinct class also remains
unaddressed by design — Bangalore Electricity prints "Trade and Other Payables" as one line,
and binding a combined caption to `trade_payables` would overstate it, so it abstains.

### N-B1 · Sector theme vocabulary — DONE

[`sector_themes.py`](sector_themes.py). Five themes over petroleum, mining, power utilities
and infrastructure/EPC. A theme either RENAMES a cluster or SPLITS it, and the split is what
the reference needed: for an E&P company, RC-CAP is two audit problems — depletion and
impairment of producing assets, and the capitalise-versus-expense boundary on exploratory
ones. They share no population, no evidence request and no specialist.

The invariant enforced at import is the partition: the themes for a (model, cluster) pair
must cover every one of that cluster's signals exactly once. Under-coverage would silently
drop a diagnostic from that entity's matrix; double-coverage is the repetition §10.1 forbids.

### N-B2 · Two new clusters — DONE, AWAITING SIGN-OFF

RC-CONT (contingent liability and claims exposure) and RC-INV (investment concentration and
income dependency), with signals S23-S27. Every one is tagged PROPOSED, carries `EXTENSION`
in its `spec_basis`, and is listed under its own heading by `python -m fdr contract --gaps`.
Declining them deletes two blocks and returns the system to Appendix D exactly.

S27 (investment income dependency) is FACE-derivable and has a rule, which is what makes
RC-INV reachable today — it is live on 12 entities. RC-CONT is note-only and says so:
`note_only_basis` records why, and the reachability check now demands that statement rather
than rejecting the cluster outright.

Three side-effects worth recording, each caught by an existing guard rather than by review:

* The §17.2 safe-language lint prohibits "guarantee", which is right for the promise sense
  and wrong for the Ind AS 109 instrument — RC-CONT could not name its own subject matter.
  A narrow carve-out admits the instrument sense only when qualified (`financial guarantee`)
  or doing an instrument's work (`guarantees given`, `issued`, `agreement`). Everything else
  still fails.
* The lint matched line by line, so a permitted phrase broken across a wrap read as a bare
  prohibited word. It now matches each line joined with the next, reporting against the line
  the phrase starts on.
* `test_registry` and `test_derivations` asserted exact counts (6 clusters, 22 signals).
  Counts only ever say "the number changed". They now assert what the counts stood for:
  every Appendix D id is still present, and everything beyond it declares itself an extension.

### N-A1 · Note schedule reader — DONE

`note_index()` returned **zero** notes for ONGC FY2024-25, a filing carrying 186 untyped
financial tables, because it required the literal word "note". Most filings do not write it:
they print "8. Capital Work-in-Progress", "12. Trade receivables- Current". With the bare
numbering recognised (sub-numbering preserved, so 10.1 and 10.2 stay distinct populations),
the same filing yields **49 note schedules**, including the ageing tables the reference cites.

### N-A2 / N-C · Note facts into the panel — PARTIAL, AND THIS IS THE HONEST GAP

[`../fs_db/note_facts.py`](../fs_db/note_facts.py) is written and works for the shape it
claims: Schedule III ageing tables laid out across columns. Measured over 400 candidate
tables it extracts all four buckets from 15, across 6 filings — correct where it fires
(HPCL's CWIP ageing parses exactly), and refusing a partial read, because dropping the
"less than 1 year" bucket inflates the `gt_3y` share that S09 exists to read.

**It is not wired to the panel, and the movement schedules do not work.** Provisions, CWIP
and exploratory-well movements print running subtotals, so two cells in one period-block are
populated on the same row and the column-grouping test correctly refuses them. Reading those
reliably needs more than a column heuristic. Until it lands, S09 and S16 have no rule and the
note-derived reference rows stay blocked — which is what `test_reference_matrix` reports.

### N-E · Reference conformance test — DONE

[`test_reference_matrix.py`](test_reference_matrix.py) transcribes the seven reference rows
and asserts the engine can EXPRESS each: a home cluster or sector theme, the row's
assertions, its specialist referral. Reachability is printed rather than asserted, so a row
blocked on note extraction does not hold the suite red for months and train people to ignore it.

It immediately found four divergences nobody had noticed: RC-REC does not carry `existence`
though a receivables confirmation tests exactly that, RC-CAP does not carry `classification`
though that is the assertion the capitalisation boundary threatens, and RC-DEP carries
neither `accuracy` nor `occurrence` though the reference reads administered pricing as both.
These are Appendix D's own column 3, so widening them is an audit decision, not an edit.
They are recorded in `KNOWN_ASSERTION_DIVERGENCES` with a reason each; a NEW divergence
still fails the suite, and a resolved one that is left behind fails too.

### Test status

`python -m fdr test` — registry, derivations, rules, headline, planning, interactions,
reference matrix and tracing all pass. `fs_db`'s md_parser, statement-selection,
computations, ratio-binding, ratio-flow and llm_bind suites all pass.

**One pre-existing failure is left as found:** `test_skeleton` — "an input quality grade was
invented". `assemble.py` (already modified in the working tree before this work) grades an
ungraded run E with a NOT ASSESSED basis, while `test_skeleton` still expects `None`.
Verified by stashing this work: it fails identically without it. Which side is right is a
judgement about whether "not assessed" is better expressed as a letter or as an absence, so
it is left for the owner rather than resolved by whichever edit makes the suite green.

---

## 3. Build order

```
N-D binder coverage ──────────────> lifts RC-WC / RC-REC / RC-FUND / RC-CAP now
                                    (reference row 5 to full strength)
N-B1 sector vocabulary ───────────> rows 1, 4, 7 correctly FRAMED
                                    (still thin until N-A lands)
N-B2 cluster extension ──[SIGN-OFF]─> rows 3, 6 become expressible
                                            │
N-A note fact layer ──────────────────────> N-C note rules ──> rows 1,2,3,4,6,7 POPULATED
    (ageing schedules first)
```

| # | Workstream | Files | Sign-off | Effort | Gate |
|---|---|---|---|---|---|
| **N-D** | Face binder coverage | `fs_db/binding.py`, `fs_db/facts.py` | no | ~3d | `INPUT_NEVER_BOUND` under 200; S01/S02/S06/S13 fire on ≥10 entities each |
| **N-B1** | Sector theme vocabulary | `clusters.py`, `planning.py`, `render.py` | no | ~2d | a petroleum entity's RC-CAP row reads as reference row 4's title and framing |
| **N-B2** | Two new clusters (RC-CONT, RC-INV) | `clusters.py`, `assertions.py`, `planning.py` | **yes — audit owner** | ~2d after sign-off | rows 3 and 6 render with full §10.3 packages; by-nature override fires on RC-CONT |
| **N-A1** | Schedule III ageing parser | `fs_db/md_parser.py`, new `fs_db/note_binding.py` | no | ~5d | CWIP + receivables ageing parse on 30 of 50 entities |
| **N-A2** | Note LineSpecs + materialisation | `fs_db/binding.py`, `fs_db/facts.py`, `fdr/facts_store.py` | no | ~5d | note facts in `fs_facts` with the note number carried in the source trace |
| **N-C** | Eight note signal rules | `rules.py`, `thresholds.py`, `test_rules.py` | no | ~5d | each fires on ≥1 golden entity with a trace naming its note |
| **N-E** | Reference conformance test | new `test_reference_matrix.py` | no | ~2d | see below |

**N-E is the acceptance test for the whole plan**, and it should be written first, failing: run
the engine on ONGC FY2025-26 and assert the matrix contains the seven reference themes with the
reference assertions and specialist referrals. It converts a presentation mock-up into a
regression test, which is the only way the reference report stops being a picture.

---

## 4. What stays out of scope

| Don't | Why |
|---|---|
| Author the reference rows as fixtures | The reference is hand-written prose. Copying it into the engine produces a system that reproduces ONGC and nothing else, and it violates §15.3 the first time it prints an unearned figure |
| An LLM in the signal path | P1 of `finance-core`, and ROADMAP §5.2. The model classifies the business (M5) and nothing else. Note rules are deterministic functions of note facts |
| Free-form note parsing before the mandatory schedules | The Schedule III ageing tables are uniform; the narrative notes are not. Doing the uniform ones first is what makes rows 2–5 land together |
| §12 corroboration, to make rows look stronger | Every reference row reads `standalone`. The reference is itself a Grade S report — corroboration is not what is missing |
| Composites (§9.4) | Still M13, still last. No reference row needs one |

---

## 5. Honest expectation

After **N-D + N-B1** (about a week, no sign-off needed): the four live clusters populate on far
more entities and read in the entity's own vocabulary. Reference row 5 is fully there; rows 1, 4
and 7 are correctly named but still thin.

After **N-A + N-C** (about three further weeks): rows 1, 2, 4 and 7 populate with note-anchored
figures and note-numbered traces — the ₹-figure provenance that makes a reference row credible.

Rows 3 and 6 populate **only if N-B2 is signed off.** Without that decision the engine's ceiling
is five of the reference's seven rows, however much extraction work is done.
