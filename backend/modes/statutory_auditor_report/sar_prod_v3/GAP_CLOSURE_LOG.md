# SAR Gap-Closure Log

Running record of work closing the gaps between the SAR mode and the
*Developer Wiki — LLM Programming Specification for Statutory Auditor
Report Review under C&AG Supplementary Audit* (source spec). Update this
file every time a gap is touched — new entry at the top of its phase,
never edit a past entry's "What changed" after the fact (append a note
instead, e.g. "superseded by commit X").

**Versioning approach:** one git branch per phase (`sar-gap-closure-phase1`,
`sar-gap-closure-phase2`, ...), off `main`. Each gap (or a natural slice of
one) is its own commit — small enough that `git revert <sha>` undoes just
that slice without touching anything else. This file records the sha, the
files it touched, and what reverting it would give back, so "undo this one
thing" never requires re-deriving what "this one thing" was from a diff.

> **Current status: Phases 1–5 are being committed to `sar-gap-closure-phase1`
> and pushed, on explicit instruction, after live validation against real
> infrastructure (see Phase 5).** Phase 1 was originally committed as 6
> commits (shas `68e5aea`…`dac207c`), then undone via `git reset --mixed
> e5213f7` for review — those specific shas are gone from history and
> `git show`/`git revert` won't find them; the *design* record they left
> behind (what each slice did) is still accurate. The commit(s) actually
> made for this push are recorded at the point they happen — check `git
> log` on this branch for the real, current shas rather than trusting any
> sha mentioned in prose above this line.

---

## How to revert something in this log

**While nothing is committed (current state):** discard a specific file with
`git checkout -- <path>`, or see everything changed with `git status` /
`git diff` and discard selectively. There is no commit to `git revert` yet.

**Once a phase's commits are actually made** (this section will be updated
with real, current shas at that point):

```bash
# Undo one commit, keeping everything after it (creates a new commit that
# reverses it — safe on a shared branch, doesn't rewrite history):
git revert <sha>

# Throw away the whole phase and go back to where it branched from main:
git checkout main
git branch -D sar-gap-closure-phase1

# See exactly what one commit changed before deciding whether to revert it:
git show <sha>
```

---

## Phase 1 — Gap #1 (structured observation register) + Gap #5 (wire formal checks)

Branch: `sar-gap-closure-phase1` (off `main` @ `e5213f7`). **Uncommitted —
see status note above.** The commit table below is the grouping that was
used (and will be reused on re-commit); shas are historical/design
references only, not live.

| Commit (historical ref) | What it did |
|---|---|
| `68e5aea` | New `observation.py` + `check_registry.py` — the Observation model and check-ID registry. No wiring yet. |
| `05dc7a5` | `tool_sar.py`: new `CheckTools.check_report_date_sequence()`; shared `_parse_flexible_date()`; `check_eom_closing_sentence()` evidence fix. |
| `9e83ecb` | New `formal_review.py` — runs the three formal checks against merged extractor JSON; derives `review_status`. |
| `08eb800` | Wires all of the above into `sar_report_pipeline.py`, `agent.py`, `schemas.py`, `adapter.py`. |
| `3cceca9` | `tests/` — 43 test cases across 5 files covering everything above. |

### What changed, and why

**Gap #1 — every deterministic finding is now a structured `Observation`,
not an ad-hoc dict.**

Before: `CheckTools.CheckResult` and the pipeline's own dict-building each
had a different shape; nothing carried a confidence rating; nothing
enforced the source spec's §11 rule that an untraceable finding must be
suppressed or downgraded. Now: every deterministic check's result flows
through `observation.Observation`, and three pure functions run over the
merged list before it reaches the writer or the caller:

1. `suppress_untraceable()` — §11. A `FINDING`/`RISK_FLAG` with no quoted
   text, report reference, or FS reference is retagged `AUDIT_POINTER` and
   gets a caveat explaining why. (Discovered while wiring this in:
   `check_eom_closing_sentence`'s "absent" branch used to return
   `evidence=""`, which this rule would have wrongly downgraded — fixed in
   `05dc7a5` by having it return the searched text instead, since absence
   *is* directly verifiable from the package.)
2. `compute_review_status()` — §47. `complete` / `provisional` / `blocked`,
   returned to the caller and handed to the writer.
3. `apply_confidence_downgrade()` — §29.1/§38. Every `High`-confidence
   observation capped at `Medium` when the package is `provisional`.

`check_registry.py` is the byproduct named in the original gap analysis
(H8): a central `check_id -> metadata` map, with a test
(`test_no_unregistered_check_ids_in_live_modules`) that fails the moment a
new check is added without a registry entry.

**Gap #5 — the three formal checks that already existed but were never
called.**

`validate_udin_format`, `check_eom_closing_sentence` and (new)
`check_report_date_sequence` are now actually invoked, via
`formal_review.run_formal_checks()`, once per report:

- UDIN: once per auditor (joint audits get one check per auditor).
- EoM closing sentence: only when the extractor found an EoM section at all
  (otherwise pre-flight's PRE-03 already covers "no EoM identified" and a
  second check would be redundant).
- Report-date sequencing: only when both dates were extracted. This is a
  **new** check (`CHK-DATE-01`), not the pre-existing
  `ComputeTools.compute_report_date_gap` — that function measures a
  different thing (FY-end-to-report-date gap, for timeliness, with
  90/180-day informational thresholds) and was left alone. `CHK-DATE-01`
  implements the source spec's actual §30 rule — report date must not
  precede the FS approval date — with **no** numeric benchmark, matching
  both the spec and the writer prompt's existing instruction.

### Backward compatibility

- `Observation.to_dict()` keeps every key the frontend
  (`frontend/src/components/report/ReportDocument.jsx`) and
  `agent.build_writer_user_message` already read (`check_id`, `tag`,
  `component`, `observation`, `risk_rating`, `evidence`, `obs_id`) and adds
  the new fields alongside them. No frontend change was needed or made.
- `ReportResponse` (schemas.py) and `adapter.generate_report()` gained
  `review_status` / `review_status_reasons` as new, defaulted fields —
  additive only.
- Nothing in `tool_sar.py`'s existing public functions changed behaviour;
  `compute_report_date_gap` is provably unchanged
  (`test_compute_report_date_gap_unchanged_after_refactor`).

### What's tested, and how

**43 test cases**, `sar_prod_v3/tests/test_observation.py`,
`test_check_registry.py`, `test_tool_sar_checks.py`,
`test_formal_review.py`, `test_pipeline_wiring.py`. All are pure-Python —
no DB connection, no LLM endpoint, no `yukta` import required to run them
(`test_pipeline_wiring.py` calls `SARReportPipeline.__new__()` to bypass
`__init__`'s agent construction, since the methods under test don't touch
`self`).

**Execution environment note:** this sandbox has no `pytest` installed and
no pip/network access to install it. Every test case above was still
actually executed — with a ~40-line local shim standing in for
`pytest.raises` / `pytest.mark.parametrize` (not committed to the repo) —
and all 43 passed before any of this was committed. That proves the test
*logic* is correct; it does not exercise pytest's own fixture/collection
machinery. **Run `pytest backend/modes/statutory_auditor_report/sar_prod_v3/tests/`
in a real dev environment or CI before trusting this beyond that.**

**Not tested, because it needs a live endpoint/DB this sandbox doesn't
have:** the full `SARReportPipeline.run()` (extractor + writer LLM calls,
real Postgres `FetchTools`/`ReferenceTools` queries). `test_pipeline_wiring.py`
covers the exact merge/suppress/status/confidence sequence `run()`
performs, called the same way `run()` calls it — but not the DB fetch or
LLM steps around it. **Before relying on this in production, run one real
`pipeline.run(...)` against a live entity/FY and read the resulting
`report_md` and `observations` — particularly the new "FORMAL CHECKS
SUMMARY" block reaching the writer prompt as intended.**

### Known Phase-1 limitations (intentional, tracked for Phase 2)

- `review_status` reacts only to: main report unusable, mandatory FS
  tables missing, UDIN unresolved. It does **not** yet react to CARO/IFC
  applicability being unresolved — that's Gap #4 (Applicability Gates),
  not started in Phase 1.
- `report_reference` (page/paragraph) is not populated by any caller yet —
  the DB layer concatenates chunks without preserving a per-quote page
  number at the point a `CheckTools` method runs. The lineage check
  currently runs against `evidence` (verbatim text) only.
- `public_sector_lens`, `safe_candidate_wording`, `evidence_required` exist
  on `Observation` (so Gaps #7/#9 don't need another schema migration) but
  nothing populates them yet.
- The writer LLM's own prose findings (Part 2 of the memorandum) do **not**
  flow through `Observation` yet — only the deterministic checks
  (pre-flight, coherence, formal) do. Folding the writer's findings into
  this schema (the "two-pass writer" idea from the original plan) is
  deliberately deferred: it changes LLM-facing prompts that can't be
  validated without a live model endpoint, and is large enough to be its
  own phase rather than bundled into this one.
- `apply_confidence_downgrade` is global (every observation, not a
  per-observation "is this actually report-dependent" judgement) — a
  documented simplification of the source spec's literal wording, since a
  per-observation classifier would be guesswork without more signal than
  Phase 1 has.

---

## Phase 2 — Gap #4 (applicability gates) + Gap #3 (consistency/silence engine, partial)

**Uncommitted** — built directly on top of Phase 1's uncommitted changes,
in the same working tree, same branch (`sar-gap-closure-phase1`; not yet
renamed/re-branched to `-phase2` since nothing has been committed either
way — see status note at the top of this file). No commit table yet for
the same reason as Phase 1's note above: nothing here has a real sha.

**Gap #2 (§143(5) directions engine) was explicitly put on hold this
phase** — see "Gap #2 — why it's on hold" below. Everything else in the
agreed sequencing (#4, then #3) proceeded.

### New files

- `applicability.py` — resolves CARO / IFC / KAM applicability to
  `"applicable" | "not_applicable" | "uncertain"`, each with a `basis`
  string, plus `applicability_observations()` which raises one
  `AUDIT_POINTER` per area left `"uncertain"` (`APPL-CARO-01`,
  `APPL-IFC-01`, `APPL-KAM-01`).
- `consistency_engine.py` — 7 deterministic consistency-matrix / silence
  rules (source spec §26/§27/§28), gated on `applicability` where the rule
  depends on CARO or IFC data. See that module's own docstring for the
  full list, the exact source-spec row each maps to, and which rows are
  **not** covered yet and why.
- `tests/test_applicability.py`, `tests/test_consistency_engine.py` — unit
  tests for both modules.
- `tests/test_pipeline_wiring.py` — **rewritten** (not just extended) to
  mirror `run()`'s actual current step sequence (6b → 7 → 7b → 7c →
  merge), including the applicability-gating scenario as its own explicit
  test (`test_unresolved_applicability_gates_coherence_but_still_flags_itself`).

### What changed in already-existing files

- `sar_report_pipeline.py`:
  - New `_resolve_applicability()` (Step 6b) and `_run_consistency_checks()`
    (Step 7c).
  - `_run_coherence_checks()` signature gained an `applicability` parameter
    — `caro_adverse_count` / `ifc_has_material_weakness` are zeroed out
    before `CheckTools.check_opinion_coherence_signals` runs when the
    corresponding area isn't `"applicable"`, so `CHK-COH-01`/`02` cannot
    fire on data whose relevance is unconfirmed. This is the actual gate;
    `APPL-CARO-01`/`APPL-IFC-01` explain *why* it didn't fire.
  - `run()`'s merge step now folds in `applicability_observations(...)`
    and `consistency_engine`'s output alongside the Phase 1 sources,
    before the same suppress → review_status → confidence → assign-IDs
    sequence.
  - Return dict gained `"applicability"`.
- `agent.py` (`build_writer_user_message`): new `applicability` parameter
  and an **APPLICABILITY SUMMARY** block in the assembled message,
  instructing the writer not to raise its own "missing clause"/"missing
  KAM" finding on an area already marked `UNCERTAIN` (mirrors how the
  Phase 1 FORMAL CHECKS SUMMARY block already works).
- `check_registry.py`: 10 new entries (`APPL-CARO-01`, `APPL-IFC-01`,
  `APPL-KAM-01`, `CONS-CARO-IX-01`, `CONS-CARO-XI-01`, `CONS-CARO-XIII-01`,
  `CONS-CARO-VII-01`, `CONS-R11G-01`, `CONS-CAGDIR-01`, `CONS-KAM-01`).
  `tests/test_check_registry.py`'s drift-guard list extended to match.

### Why this design — the gating mechanism specifically

Applicability can only be resolved *after* the CARO/IFC extractors have
already run (the signals it uses — the extractor's own `caro_applicable`
field, whether any CARO/IFC text was found at all — don't exist before
extraction). So this cannot stop an inapplicable annexure from being
extracted; it stops the *downstream reasoning* (coherence checks here,
consistency-engine rules) from treating that extracted data as meaningful
when applicability is unresolved. `applicability.py`'s module docstring
has the full explanation, including what it would take to move the gate
upstream of extraction (a second, sequential LLM call) — not done here.

### Rules implemented in `consistency_engine.py` (7 of ~15 source-spec §26 rows)

| Check ID | Fires on | Source-spec anchor |
|---|---|---|
| `CONS-CARO-IX-01` | CARO (ix) default / (xix) adverse, no MURGC/GC discussion | §26 rows "MURGC↔CARO(xix)", "CARO(ix)↔GC"; **matches EVAL-03** |
| `CONS-CARO-XI-01` | CARO (xi) fraud flag, no fraud/143(12) echo elsewhere | §28 named silence scenario |
| `CONS-CARO-XIII-01` | CARO (xiii) RPT flag, no KAM/EoM echo | §28 named silence scenario |
| `CONS-CARO-VII-01` | CARO (vii) statutory-dues flag, no Rule 11/KAM/EoM echo | §26 row "CARO(vii)↔Statutory dues" |
| `CONS-R11G-01` | Rule 11(g) audit-trail adverse, IFC unmodified with no IT-control weakness | §26 row "IFC↔Rule 11(g)"; **matches EVAL-06** |
| `CONS-CAGDIR-01` | C&AG directions present, pending count > 0, no reconciling text | §25.1 non-response rule (lightweight stand-in, not the full engine — see Gap #2 note) |
| `CONS-KAM-01` | KAM section exists but doesn't cover an already-computed distress signal | §19 (KAM vs. high-risk FS areas) |

**Not covered yet** (listed in full in `consistency_engine.py`'s
docstring, not just here): opinion-vs-basis pervasiveness and modified-
amount-vs-FS-note reconciliation (both need Gap #8, not started); EoM-vs-
FS-disclosure existence (nothing in this pipeline extracts FS notes text
to check against); prior-modification-vs-current and prior-C&AG-comment-
vs-current (need Gap #6's `sar_results` table, not started); the full §25
four-dimension directions test (Gap #2, on hold).

### Gap #2 — why it's on hold

Investigated where "actual C&AG directions" might already be ingested.
Found `public.cag_directions_chunks` (via the Financial Statement mode's
`tools_fs.py`), living in the **reference/rules DB**
(`Config.get_connection()` — structurally the same role as SAR's own
`REFERENCE_DSN`, not the entity-documents DB), with columns `doc_name,
effective_from, effective_to, is_amended` plus embeddings — i.e. the
**text of C&AG's standing directions by notification**, with no
entity/company/FY column. That is not, by itself, a record of which
directions were issued to a specific company for a specific year, which
is what source spec §25's four-dimension test (`addressed / responsive /
impact_reconciled / cross_report_consistent`) needs to run against.
Presented this finding and asked how to proceed; the decision was to hold
Gap #2 entirely for this phase rather than build against a table that
might not be the right target. `check_cag_directions_unquantified` in
consistency_engine.py is *not* Gap #2 — it is one cheap, self-contained
completeness check (does the SAR's own reproduced directions text look
substantive) that doesn't depend on this open question at all.

### What's tested, and how

**32 new test cases** (`test_applicability.py`, `test_consistency_engine.py`,
plus the rewritten `test_pipeline_wiring.py`), on top of Phase 1's 43 — 75
total. Same execution caveat as Phase 1: no `pytest` in this sandbox; run
via the same local shim, all 75 passed before this write-up. **Run the
real `pytest` suite, and one live `pipeline.run(...)`, before trusting
this beyond "the logic is internally consistent."**

Specifically exercised: each of the 7 consistency rules firing and not
firing; the CARO/IFC applicability gate suppressing `CONS-CARO-*-01` /
`CONS-R11G-01` and `CHK-COH-01`/`02` when applicability is `"uncertain"`
even with nonzero `caro_adverse_count` / `ifc_has_material_weakness` in
`_meta` (`test_unresolved_applicability_gates_coherence_but_still_flags_itself`
— this is the test that would fail first if the gate ever got wired
backwards); all three applicability states for all three areas; and that
`run_consistency_checks` never raises on missing/empty input.

### Known Phase-2 limitations (intentional, tracked for later)

- Applicability gates extraction's *output*, not extraction itself (see
  "Why this design" above) — a false-positive-heavy deployment might
  eventually justify a two-call sequential extraction to gate this
  earlier; not done here.
- `review_status` still does not react to applicability being
  `"uncertain"` (only to main-report/FS-missing and UDIN, per Phase 1) —
  an `"uncertain"` CARO/IFC/KAM does not, by itself, make the memorandum
  `"provisional"`. This was a deliberate choice, not an oversight: pretty
  much every real-world SAR review will have *some* applicability
  ambiguity (e.g. KAM is "uncertain" unless a KAM section happens to be
  present, since listed-status isn't resolved anywhere), and downgrading
  every such review to `"provisional"` would make that status
  meaningless. Revisit once §6's `assignment_context` (listed_status
  etc.) is actually resolved from somewhere.
- The 6 CARO/IFC-dependent consistency rules re-derive "is this
  applicable" via `_is_applicable()` rather than the pipeline pre-filtering
  which rules even get called — a deliberate style choice (each rule
  stays independently readable/testable) but means adding a new
  CARO-dependent rule requires remembering to add the same one-line gate,
  not something the runner enforces structurally. `check_registry.py`'s
  `applicability_condition` field documents the requirement per check but
  doesn't enforce it either.

---

## Phase 3 — Gap #6 (prior-year continuity) + Gap #8 (pervasiveness) + Gap #7 (public-sector lens) + Gap #9 (evidence catalogue)

**Uncommitted** — same working tree, same reasons as Phases 1–2 (see
status note at the top of this file). All four of the previously-"Next
up" gaps were closed in this pass, at the user's request ("fix the 4 open
gaps"), leaving only Gap #2 (§143(5) directions) on hold.

### New files

- `evidence_catalogue.py` (Gap #9) — the wiki's §42 eight issue-type ->
  evidence-request lists, verbatim, plus `classify_issue_type()` (keyword
  classifier over an observation's component+text) and
  `enrich_evidence_required()` (fills `evidence_required` only where a
  check hasn't already set one — never overwrites a rule-specific list).
- `public_sector_lens.py` (Gap #7) — `classify_public_sector_dimension()`
  matches §33's sensitive categories (grants/subsidies, waivers/write-offs,
  guarantees, idle/stalled assets, IT/data/cyber, regulatory
  non-compliance, related-party/Government-linked) against an
  observation's text; `apply_public_sector_lens()` tags the matching
  observation's `public_sector_lens` field and elevates its risk rating
  one level (§33: "materiality by nature/context, not only amount"),
  skipping `"Information request only"` observations (no risk rating to
  elevate) and never raising past `High`.
- `prior_year_continuity.py` (Gap #6) — `build_prior_year_summary()`
  (compact trend record: opinion type, review_status, high-risk
  observations only — not the full report), `check_opinion_trend()` (new
  `PRIOR-OPN-01` observation comparing prior vs. current opinion type —
  §26's "Prior modification <-> Current report" row), and
  `elevate_recurring_observations()` (matches current observations against
  the prior year's stored `check_id`s and elevates risk one level on a
  match — the wiki's explicit recurrence rule).
- `pervasiveness.py` (Gap #8) — `check_opinion_type_vs_pervasiveness()`
  (new `PERV-01`: Qualified opinion with ≥3 of 5 recorded pervasiveness
  cues — a RISK_FLAG for human review, never a re-classification of the
  stated opinion, per §14) and `check_modification_amount_reconciles()`
  (new `PERV-02`: a quantified modification amount not found verbatim in
  the fetched FS tables — an AUDIT_POINTER, not a FINDING, since this may
  be a markdown-extraction formatting artefact rather than a genuine
  discrepancy).
- `tests/test_evidence_catalogue.py`, `test_public_sector_lens.py`,
  `test_prior_year_continuity.py`, `test_pervasiveness.py` — new. Plus
  `elevate_risk_rating` tests appended to `test_observation.py`, and
  `test_pipeline_wiring.py` extended again (new
  `test_pervasiveness_and_prior_year_and_public_sector_lens_all_fire_together`)
  to cover all three new engines merging together in one run.

### What changed in already-existing files

- `observation.py`: new `elevate_risk_rating()` — a single shared
  risk-rating ladder (capped at `High`, never touches `"Information
  request only"`) used by both `prior_year_continuity.py` and
  `public_sector_lens.py`, so the two elevation rules can't drift onto
  different scales.
- `tool_sar.py`: new `FetchTools.save_sar_result()` — writes the trend
  record via `INSERT`, and (since `sar_results` has never existed in this
  deployment — `fetch_prior_year_result`'s own `EXISTS` check always
  returned false) issues `CREATE TABLE IF NOT EXISTS` first. Self-
  bootstrapping: the first report generated after this change creates the
  table and writes a row; the *second* report for that company is the
  first one `fetch_prior_year_result` can actually find something for.
- `PROMPT.md` (`EXTRACTOR_MAIN` schema) — **the one schema/prompt change
  in this phase; unvalidated against a live model (see caveat below)**.
  Added a `modification` block: `quantified_amount`,
  `quantified_amount_unit`, `affected_line_items`, `affected_note_ref`,
  `tax_effect_quote`, and 5 boolean `pervasiveness_cues`, plus extraction
  instructions immediately above the JSON schema (only populate when
  `opinion.type` is qualified/adverse/disclaimer; each cue true only when
  directly evidenced, not inferred).
- `sar_report_pipeline.py`:
  - New `_run_pervasiveness_checks()`, `_fetch_prior_year_result()`,
    `_save_prior_year_record()`.
  - `run()`'s merge sequence extended: pervasiveness + prior-opinion-trend
    observations folded in alongside Phase 1/2's sources *before*
    suppression; `elevate_recurring_observations` ->
    `apply_public_sector_lens` -> `enrich_evidence_required` now run, in
    that order, between suppression and `review_status` computation.
  - After the final observation list is built, `_save_prior_year_record()`
    is called (best-effort, try/except + log-warning, never raises) to
    persist this run's trend record for next year.
- `check_registry.py`: 3 new entries (`PERV-01`, `PERV-02`,
  `PRIOR-OPN-01`). `tests/test_check_registry.py`'s drift-guard list
  extended to match.

### Why this order (elevate-recurring -> public-sector-lens -> evidence-enrich)

All three are "cross-cutting" passes over the *whole* merged observation
list, applied after every rule engine has already run and after lineage
suppression (an untraceable observation shouldn't get a confident-looking
elevation or evidence list attached to it) but before `review_status` /
confidence / ID assignment (which need the final risk ratings settled).
The two elevation passes can compound on the same observation (recurring
*and* public-sector-sensitive both add one level each, capped at `High`)
— deliberately allowed rather than guarded against: the wiki doesn't say
they're mutually exclusive, and a matter that is both recurring and
sensitive-by-nature is plausibly the most material kind of finding a
supplementary audit review can surface.

### Gap #8's caveat — read before trusting PERV-01/PERV-02 in production

Both new pervasiveness checks depend entirely on the new `modification`
block in `PROMPT.md` being populated well by the extractor LLM.
`pervasiveness.py` is written so that a `merged_json` with no
`modification` block — either because the report was generated with the
OLD prompt, or because the live model ignores the new instructions and
omits the fields — produces zero observations, never an error. But
**nothing in this sandbox can confirm the extractor actually fills these
fields sensibly**; the tests here (test_pervasiveness.py) construct
`modification` blocks by hand and only prove the deterministic reasoning
over whatever it's handed. **Before relying on PERV-01/PERV-02 in
production: generate a real qualified/adverse-opinion report against a
live model and inspect the `modification` block in the parsed JSON.**

### What's tested, and how

**42 new test cases** (evidence_catalogue: 7, public_sector_lens: 6,
prior_year_continuity: 9, pervasiveness: 10, plus 3 new
`elevate_risk_rating` cases in test_observation.py and 1 new pipeline-
wiring scenario), on top of Phase 1+2's 75 — **117 total**. Same execution
caveat as Phases 1–2: no `pytest` in this sandbox; run via the local shim,
all 117 passed before this write-up.

Specifically exercised: `classify_issue_type`/`classify_public_sector_dimension`
NOT false-positive-matching PRE-04's own "...Legal and Regulatory
Requirements" component name (the exact trap the module docstrings warn
about); `enrich_evidence_required` never overwriting a rule-set list;
`apply_public_sector_lens` skipping `"Information request only"` and never
exceeding `High`; `check_opinion_trend`'s four prior/current opinion-type
combinations; `elevate_recurring_observations` matching, not-matching, and
already-High cases; `PERV-01` firing only for Qualified with ≥3 cues (not
Adverse, not Unmodified, not absent data); `PERV-02` firing only on a
genuine amount/table mismatch; and one full merged run exercising all
three Phase-3 engines together
(`test_pervasiveness_and_prior_year_and_public_sector_lens_all_fire_together`).
`save_sar_result`'s failure path was exercised directly (this sandbox has
no `psycopg2` at all) and confirmed to log a warning rather than raise —
its success path (actually creating the table and inserting a row) has
**not** been run against a real Postgres instance.

### Known Phase-3 limitations (intentional, tracked for later)

- `elevate_recurring_observations` matches by exact `check_id` recurrence
  only (documented in `prior_year_continuity.py`'s module docstring) — a
  substantively-the-same matter that happens to route through a different
  check_id between years is not caught.
- `public_sector_lens`'s keyword list covers 7 of §33's ~18 example
  categories (deliberately excludes "losses" and bare "regulatory" as
  keywords — both too generic, would over-fire on ordinary audit
  language; see the module's own docstring).
- `review_status` still does not react to any Phase-3 signal (a Qualified-
  with-high-pervasiveness-cues package, or a recurring prior-year matter,
  does not by itself make the memorandum `"provisional"`) — consistent
  with the same reasoning recorded for Phase 2's applicability gates.
- `save_sar_result`'s `CREATE TABLE IF NOT EXISTS` approach means the
  first report generated after this lands effectively "loses" that run's
  own prior-year comparison opportunity for whoever set this up
  originally (there's nothing to compare against yet) — this is
  unavoidable without a separate migration step, and self-corrects from
  the second run onward.

---

## Phase 4 — Alignment with LLM_Output_Specification_CAG_Statutory_Auditor_Report_Review.md

**Uncommitted**, same working tree, same reasons as Phases 1–3. This is a
*different* source document from the original Developer Wiki — a
user-facing output-design spec, not one of the original 10 priority gaps
— so it gets its own phase here rather than being forced into that list.

**Verdict before any of this was built: the existing output did NOT match
the new spec.** One long LLM-written markdown document (`report`) was the
only output; the new spec wants three distinct formats (a 1-2 screen
Display Response, a 2-4 page Executive Summary, an 8-15 page Detailed
Report), generated from one shared structured register so they can never
disagree (§17), plus nine fields on the observation object that didn't
exist (`entity`, `financial_year`, `source`, `consistency_type`,
`sa_framework`, `recommended_audit_action`, `candidate_143_6`,
`reviewer_status`, `reviewer_comments`).

### What was built

- **`observation.py`** — nine new fields added to `Observation` (listed
  above), all additive with safe defaults; `VALID_CONSISTENCY_TYPES` /
  `VALID_REVIEWER_STATUSES` enums enforced in `__post_init__`; new
  `stamp_entity_context()`; `to_dict()` now also emits `quoted_text` as a
  single-element list wrapping `evidence` (the spec's shape, not a real
  multi-quote decomposition — see limitations).
- **`output_classification.py`** (new) — a static `check_id -> {consistency_type,
  sa_framework, recommended_action}` table (same pattern as
  `check_registry.py`), `is_candidate_143_6()` (FINDING + High risk +
  traceable), and `apply_output_classification()` which stamps all of
  this plus a best-effort `source` bucketing onto every observation.
- **`output_formats.py`** (new) — `render_display_response()` and
  `render_executive_summary()`: **Formats 1 and 2 of the output spec,
  built by plain Python string templating over the Observation list —
  no LLM call.** This is what actually makes §17's "Single Source of
  Truth" requirement real rather than aspirational: the two formats
  cannot present a different conclusion than each other, or than the
  underlying observation list, because they're templated from the exact
  same objects, not independently summarised by a second model call.
- Pipeline wiring: `apply_output_classification()` runs after every
  risk-elevation pass (so `candidate_143_6` sees final risk ratings),
  followed by `stamp_entity_context()`; `run()`'s return dict gains
  `display_response` and `executive_summary` (additive, alongside the
  existing `report`); `schemas.py` / `adapter.py` propagate both.
- Tests: `test_output_classification.py`, `test_output_formats.py`, new
  cases in `test_observation.py`, and `test_pipeline_wiring.py` extended
  to assert the Display Response and Executive Summary actually agree
  with the observation list on every run (the concrete, checkable form
  of §17). **142 total tests**, same sandbox-shim execution caveat as
  every prior phase.

### Difficulties and challenges — read this before assuming Phase 4 is "done"

This is the part you specifically asked me to not soften, so:

1. **`source` and `consistency_type` are best-effort approximations, not
   a true per-document decomposition.** The spec's example object implies
   each observation cleanly separates into what the FS said / what the
   Audit Report said / what CARO said, as distinct quotes. Every check in
   this codebase captures exactly **one** `evidence` string, sometimes a
   synthesised fact spanning two documents ("Opinion type: Unmodified |
   CARO adverse clauses: 2"), never multiple separately-attributed
   quotes. `output_classification.py` buckets that single string into
   whichever `source` slots the check's `consistency_type` implies — it
   satisfies the spec's *shape*, not its apparent intent. Building the
   real thing would require every check to capture and tag its evidence
   per originating document at construction time — a rewrite of most of
   `consistency_engine.py`/`tool_sar.py`'s check bodies, not a tagging
   pass on top of them.
2. **There is no A↔C engine (FS vs. CARO directly) at all**, and Phase 4
   didn't add one, because it can't: an A↔C check like "CARO reports
   statutory-dues arrears but the FS notes don't reflect them" needs FS
   *notes* text, and this pipeline has never extracted FS notes — only
   the BS/P&L/CF numeric tables (`FetchTools.fetch_financial_tables`).
   Every A↔C row in the new spec's §6.5 summary table will show empty
   for every report this pipeline processes until FS-notes extraction
   exists. That's a real, structural gap, not a classification oversight.
3. **The Detailed Report (Format 3) was not restructured to the spec's
   16-section TOC.** The existing LLM writer (`agent.py` / `PROMPT.md`)
   still produces its own PART 1 / PART 2 structure. Rewriting it to the
   new TOC — Report Control Sheet, the specific §6 A/B/C consistency
   sub-structure with its worked-example format, Annexures A–F — is a
   prompt change of the same class as Gap #8's `modification` schema
   addition: it cannot be validated without a live model run, and it's
   large enough that I did not want to make it without flagging it
   first. **This is the single biggest remaining piece of Phase 4.**
4. **`reviewer_status` / `reviewer_comments` are data fields with no
   workflow behind them.** Nothing in this backend lets a human actually
   set them — there's no API endpoint, no frontend control, no
   persistence of a reviewer's decision anywhere. The field exists so a
   future review-workflow feature doesn't need another schema migration,
   but calling Gap "done" here would overstate it — the actual reviewer
   workflow the spec's §18/§19 imagine (a person clicking
   Accept/Modify/Reject per observation) is a cross-stack feature
   (API + auth + frontend), well outside what a backend Python pass can
   deliver, and not attempted.
5. **`recommended_audit_action` is my own judgement-call mapping**, not
   something the spec derives mechanically — I built it from the spec's
   own §15 "Typical actions" list, matched to check_ids by hand. Reasonable
   people could map some of these differently (e.g. whether an IFC/Rule
   11(g) tension should say "obtain ITGC report" vs. "raise audit query
   first" as the *primary* action) — treat the mapping in
   `output_classification.py` as a first draft, not settled doctrine.
6. **`candidate_143_6` is a narrow, conservative proxy** for the spec's
   fuller §11 inclusion threshold (clear trace + materiality by value/
   nature/context + "sufficiently developed" + evidence considered-or-
   pending). Only `tag==FINDING and risk_rating==High and is_traceable()`
   is mechanically checkable; "sufficiently developed" and "evidence
   considered or explicitly pending" are judgement calls no deterministic
   rule can make today. Concretely, this means `PERV-01` (a High-risk
   *pervasiveness* signal, correctly tagged RISK_FLAG because it never
   re-classifies the auditor's own opinion) will **never** be
   candidate_143_6 under this rule, even though a human reviewer might
   reasonably decide it belongs there after review — that's intentional
   (a RISK_FLAG is explicitly not "clear source trace" per the wiki's own
   tag definitions) but worth knowing the boundary is drawn there.

### What's tested, and how

142 total test cases (25 new this phase). Same caveat as every prior
phase: sandbox-shim execution, no real `pytest`, no live LLM/DB run.
Specifically new here: both render functions' zero-observation case
(§48-equivalent for Format 1); ordering by materiality; the 10-observation
display cap; all-four-combinations-empty vs. one-combination-populated
consistency alerts; the executive summary's conditional 143(6) section;
and — the test that most directly proves §17 — a pipeline-wiring
assertion that every FINDING/RISK_FLAG observation's identity appears in
*both* rendered formats from one merged run.

---

## Phase 5 — Live infrastructure validation, Gap #2, Format 3, and 3 real bugs found and fixed

**This phase is different from 1–4: it ran against real infrastructure**
(`.env`'s FINANCE_DSN / REFERENCE_DSN / EMBEDDING_BASE_URL / RERANKER_BASE_URL,
all confirmed reachable; GENERATION_BASE_URL's configured host was down —
`10.10.180.48:30004` — so `10.10.116.215:30004`, serving the same model
`gemma-4-26b-a4b-it`, was used instead for this phase's live runs; this is
a note for whoever operates the deployment, not a code change — `.env`
itself was left untouched), not synthetic hand-typed JSON. Two full runs
against real entities (`RVNL` FY2024-25, `southeastern_Coalfield` FY2023-24
— both real Government-company annual reports already in the `documents`
table) completed successfully end-to-end: real DB fetch, real 3-parallel-
extractor LLM calls, real writer LLM call, real Formats 1/2/3.

### Three real bugs found by this — not hypothetical, confirmed against live data

1. **`fetch_caro_text`'s heading routing was silently wrong on real filings.**
   RVNL's actual CARO annexure is headed *"Annexure - A To The Independent
   Auditors' Report"* — it never says "CARO" in its own heading at all
   (this is the standard convention, not an anomaly: Annexure A = CARO,
   Annexure B = IFC, confirmed against both test entities). The old
   `regulation_patterns` (`%CARO%`, `%Companies Auditor%Report%Order%`)
   never matched that, so every run fell through to a fallback whose
   `%annexure%` pattern then matched *every* unrelated annexure in the
   annual report (director Code-of-Conduct declarations, CSR-committee
   annexures) and concatenated all of it as if it were CARO. The extractor,
   given that garbage, correctly said `caro_applicable: false` — a false
   negative dressed up as a clean answer, with `quality_flag="reliable"`
   masking that anything was wrong at all.
   **Fixed:** `fetch_caro_text` / `fetch_ifc_text` (`tool_sar.py`) now match
   the "Annexure - A/B" convention directly, `%annexure%` alone is removed
   from the fallback, and the primary tier's `toc_patterns=["%auditor%"]`
   AND-filter was dropped — confirmed against real data that
   `toc_section` under an annexure is frequently the *audit firm's own
   name* ("CNK & Associates LLP Chartered Accountants"), not anything
   containing "auditor", so that filter was silently zeroing out otherwise-
   correct matches.
2. **JSON-fence stripping was still fragile after an earlier fix.** A real
   CARO-extractor response on garbage input (see bug 1) came back as a
   clean ` ```json {...} ``` ` block that `_strip_code_fence`'s *anchored*
   regex (`^```...```$`) should have stripped — and did, in isolation — but
   the live run still logged `Extractor agent failed: Expecting value:
   line 1 column 1`, meaning some real response shape (a preamble/epilogue
   token around the fence) defeated the anchor. **Fixed:**
   `sar_report_pipeline._strip_code_fence` now searches for a fenced block
   anywhere in the text rather than requiring the whole string to be
   exactly one fence, and falls back to a bracket-depth scan for the first
   top-level `{...}` object when there's no fence at all.
3. **`ReferenceTools` had never once returned real data in this deployment.**
   It queried a `reference_chunks` table with a `standard_code` column —
   confirmed by direct schema inspection that this table has never existed
   in REFERENCE_DSN. The real schema has one physical table per corpus
   (`sa_700_chunks`, `cag_caro_chunks`, `cag_directions_chunks`, ...), each
   with different column names. Every call was silently logging "relation
   does not exist" and returning empty reference context — the writer has
   never received a real SA 700/705 or CARO-framework citation from this
   class before this phase. **Fixed:** `_vector_search` now takes real
   table names and a per-table column mapping (`_TABLE_SCHEMAS`), queries
   each table separately, and merges/re-sorts by score. Verified against a
   real embedding call: a query about SA 705/adverse opinions now returns
   genuine SA 705/570/720 paragraphs from `sa_700_chunks`.

### Gap #2 (§143(5) directions) — resolved, not just unblocked

Direct inspection of `cag_directions_chunks` (7 rows) found it holds
exactly one standing document, "CAG's Revised Directions for Statutory
Auditors under Section 143(5)", with 5 substantive direction themes
(fair valuation of investments, IT-system processing, grants/subsidy
funds, risk management/data assets, SEBI/RBI/regulatory compliance — an
exact match to source spec §25.2's own theme list) and a closing note
(chunk 7) stating its own applicability rule verbatim: directions apply
"from the date of issue" — i.e. keyed to the **auditor's report date**,
not the entity or financial year at all. That resolves the original "no
entity/FY linkage" concern: there was never supposed to be one — this is
a government-wide standing document, and the date window *is* the
applicability rule.

New `cag_directions_engine.py`: `resolve_applicable_directions()` (via new
`ReferenceTools.fetch_cag_directions_for_date()`) queries by report date;
`check_direction_addressed()` raises one Observation per direction theme
not evidenced in the SAR's own reproduced `cag_directions` text (FINDING
if no text at all, RISK_FLAG if text exists but doesn't cover that theme —
source spec §25.1's rule, applied literally). New check IDs `DIR-I-01`
through `DIR-V-01`.

**Honest limitation:** "addressed" is a keyword-in-text proxy (verified
against the 5 real direction texts), not semantic confirmation — the same
class of approximation as every other echo-search check in this codebase.
On both live test runs, RVNL's `report_date` extraction came back empty
(a separate, pre-existing MAIN-extractor gap, not fixed in this phase —
see "Known Phase-5 limitations" below), so the directions engine correctly
returned no observations rather than a wrong one; it has not yet been
observed firing on a report where the date extracted cleanly. `_DIRECTION_
THEMES` is hand-curated against the one live document version — a future
second version would need it reviewed, not auto-derived.

### Format 3 (Detailed Review Report) — built, hybrid by design

`output_formats.render_detailed_report()` implements the full 16-section
TOC. 13 of 16 sections (1, 3, 4, 7–16) are deterministic templating over
the same `Observation` list Formats 1/2 use — same non-contradiction
guarantee. Sections 5–6 (Opinion Analysis narrative, Detailed Component
Review) embed the existing LLM writer's PART 2 output verbatim, clearly
labelled — real prose synthesis (judging hedged wording, boilerplate KAMs)
that a template shouldn't fake. Verified on both live runs: a real
23,859-character detailed report was produced for RVNL with all 16
sections present, Annexure C correctly listing all 21 real CARO clauses
with their real adverse/partial/clean status, and Annexure A/B/E reflecting
real quality flags and applicability. Wired into the pipeline as a third
additive output key (`detailed_report`), propagated through `schemas.py`/
`adapter.py` alongside `display_response`/`executive_summary`.

### What's tested, and how — this phase changes the caveat that applied to every phase before it

**164 total tests, run with a REAL `pytest` installation** (`pip install
pytest` succeeded in the actual project venv found at `rvenv` — a
different Python than this sandbox's own minimal one) — **not** the local
shim every prior phase had to rely on. Beyond that: **two full live
pipeline runs against real entities**, real DB, real LLM, real embedding
endpoint. This is the first phase where "tested" means what it normally
means, not "verified against hand-typed synthetic input."

**Still not tested:** a report where a direction theme IS evidenced and
DIR-*-01 correctly stays silent (both live runs had an empty report_date,
so the directions engine never got to run its comparison); a report with
a genuinely adverse/qualified opinion feeding real `modification` data to
PERV-01/02 (both live entities were unmodified); the reranker endpoint
(confirmed reachable, never actually exercised — `USE_RERANKER=False` is
still hardcoded in `chat_tools.py`, unrelated to this phase's scope); and
Trial Balance / Financial Statement / FDR modes (out of scope — SAR only).

### Known Phase-5 limitations (intentional, tracked for later)

- RVNL's `formal_checks.report_date` and `.auditors` both extracted empty
  on the live run — a real MAIN-extractor miss (likely the signature block
  fell outside the fetched chunk window, or got lost in a long prompt),
  found by this phase's live testing but **not fixed** — it's an
  extraction-prompt issue, not a bug in any deterministic check, and
  fixing it responsibly needs the same kind of live-model iteration this
  phase already spent on the CARO routing bug. Flagged, not silently
  patched over.
- `_DIRECTION_THEMES` is hand-curated, not derived from `cag_directions_
  chunks`' text automatically — a future new direction document (any
  `effective_from` after this one) would silently fall back to being
  tested against the *old* five themes unless this table is reviewed by
  a human when that happens.
- The `10.10.116.215:30004` LLM endpoint substitution for this phase's live
  runs was a per-process environment override for testing, not a change to
  `backend/.env` — whoever operates the deployment should confirm whether
  `10.10.180.48:30004` (the documented SAR/Financial-Statements endpoint,
  with the larger 262144-token context window this mode's prompts may
  assume) is back up before relying on `.env` as committed.

---

## Phase status — the 10 CAG-auditor-priority gaps

| # | Gap | Status |
|---|---|---|
| 1 | Structured, traceable observation register + confidence | **Done (Phase 1)**, validated live (Phase 5) — deterministic layer only; writer findings still prose-only |
| 5 | Wire formal checks (UDIN / EoM closing sentence / report-date) + review_status | **Done (Phase 1)**, validated live (Phase 5) |
| 4 | Applicability gates (CARO/KAM/IFC/year) | **Done (Phase 2)**, validated live (Phase 5) — gates extraction's output, not the extraction call itself |
| 3 | Deterministic consistency + silence engine | **Partial (Phase 2)**, validated live (Phase 5 — CONS-CARO-VII-01 fired correctly on real RVNL data) — 7 of ~15 source-spec §26 rows; rest blocked on missing FS-notes extraction / semantic matching |
| 6 | Prior-year continuity (`sar_results` table) | **Done (Phase 3)** — self-bootstrapping table creation; matches by exact check_id recurrence only; not yet exercised on a real second-year run |
| 8 | Pervasiveness cues + modification quantification | **Done (Phase 3)** — deterministic logic solid; the `modification` schema addition parsed correctly on a real unmodified-opinion run (Phase 5) but has not yet been observed on a real modified opinion |
| 7 | Public-sector lens on every observation | **Done (Phase 3)** — 7 of §33's ~18 example categories covered |
| 9 | Evidence Request Catalogue | **Done (Phase 3)**, validated live (Phase 5) — wiki's 8 categories verbatim + keyword classifier |
| 2 | §143(5) directions engine | **Done (Phase 5)** — real applicable-directions data found and used; date-window applicability rule taken verbatim from the source document itself, not assumed |
| 10 | Eval harness (11 EVAL scenarios) | Not started as a dedicated suite — 164 tests cover the deterministic checks built so far (including exact matches for EVAL-03 and EVAL-06), not a suite structured around the wiki's own 11 scenario IDs |

## Output-spec alignment status (separate from the 10-gap table above)

| Output-spec item | Status |
|---|---|
| Format 1 — LLM Display Response | **Done**, validated live (Phase 5) — deterministic, no LLM call |
| Format 2 — Executive Summary | **Done**, validated live (Phase 5) — deterministic, no LLM call |
| Format 3 — Detailed Review Report (16-section TOC) | **Done (Phase 5)** — 13/16 sections deterministic; sections 5-6 embed the LLM writer's own narrative by design |
| §18 Structured Observation Object | **Mostly done** — all fields present; `source`/`consistency_type` are best-effort approximations (see Phase 4/5 limitations) |
| §6/7 FS↔AR↔CARO three-way consistency engine | **Partial** — AR↔CARO and FS↔AR covered via existing consistency rules now tagged; **no A↔C (FS↔CARO) engine exists** |
| §7 SA 700/705 decision overlay (`sa_framework`) | **Partial** — populated for opinion-related checks only, via a hand-built mapping |
| §13 Candidate 143(6) as a structured flag | **Done** (narrow, conservative proxy) |
| §15 Reviewer action worklist | **Done (Phase 5)** — Section 15 of the Detailed Report renders it from `recommended_audit_action` |
| Reviewer workflow (`reviewer_status`/comments) | **Data field only** — no API/frontend behind it |

## Next up

All ten original priority gaps have now been addressed (7 "Done", 1
"Partial", 1 "Done" as of Phase 5 resolving the earlier "on hold" — only
Gap #3's consistency engine remains partial); Gap #10 (a dedicated eval
harness against the wiki's own EVAL-01..11 scenarios) is the only one with
no dedicated work, though it is exercised incidentally. All three output
formats now exist. What's left, in priority order:

1. **The RVNL `report_date`/`auditors` extraction miss** (Phase 5's own
   finding) — worth investigating first since it silently disables both
   the UDIN check and the C&AG directions engine whenever it recurs.
2. **The A↔C (FS vs. CARO direct) consistency engine** — still genuinely
   missing; needs FS-notes text extraction this pipeline has never done.
3. **A real test against a modified (qualified/adverse) opinion** — both
   live validation runs happened to be unmodified-opinion entities, so
   PERV-01/02 and the pervasiveness-cue schema are proven parseable but
   not yet proven correct on the case they were built for.
4. Gap #10's dedicated eval harness; the reviewer workflow's API/frontend.

This phase's commits are pushed per explicit instruction — check `git log`
on this branch for the actual, current shas rather than any sha quoted in
prose above this line.
