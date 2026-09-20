# The accuracy scoreboard

Runs real filings from `data/` through the real pipeline and scores the figures
against what a human read off the printed page.

It exists because, until it did, there was no way to answer "did that change
help?" except by opening one document and looking. Every extraction fix in this
codebase was validated that way — which tells you whether it fixed *that* file
and nothing about the other twenty-five.

## The four outcomes

| | meaning |
|---|---|
| `CORRECT` | the figure matches, within `verify._tolerance` |
| **`WRONG`** | a figure was emitted and it contradicts the page |
| `WITHHELD` | an `[unreadable: ...]` marker sits there — a **disclosed** miss |
| `MISSING` | no row, no table, nothing — **silent** loss |

`WRONG` is the only one that makes the tool dangerous rather than
disappointing. An auditor told nothing goes and checks the statement; one told
`8` when the page says `24,91,771.03` may not. It is a hard gate and it is not
tradeable against gains elsewhere: a change that recovers thirty figures and
invents one is a bad change.

`MISSING` is kept separate from `WITHHELD` on purpose. Every worst defect found
in this corpus produces `MISSING`: a born-digital filing that extracted zero
tables while reporting a clean conversion, four scanned pages of an annual
report that routed down the no-OCR path, a row swallowed when TableFormer
merged two grid rows. Collapse the two and a document can score perfectly by
emitting almost nothing.

## Running it

```bash
# from ingestion/, with the service up on :12102
INGEST_ACCURACY=1 venv/Scripts/python -m pytest tests/accuracy -q

# score by hand, and see the per-figure detail
venv/Scripts/python -m tests.accuracy.runner score-all
venv/Scripts/python -m tests.accuracy.runner score tests/accuracy/cases/sk_2022_23_sfs.yaml
```

The `test_harness.py` tests are fast, need no services, and run in the normal
suite. Only `test_accuracy.py` is gated behind `INGEST_ACCURACY=1`.

Two execution modes: `http` (default) drives the running service exactly as the
gateway does and needs nothing installed locally; `--mode inproc` calls
`pipeline.run` directly, which is faster to iterate on but needs the full
docling stack.

## Adding a case

```bash
venv/Scripts/python -m tests.accuracy.runner propose \
    ../data/<entity>/<fy>/<file>.pdf > tests/accuracy/cases/<name>.yaml
```

That dumps every figure the pipeline currently emits, each marked
`verified: false`. **Unverified entries are not scored.** Open the PDF, check
each figure against the printed page, correct it, and set `verified: true`.

The gate matters. A proposal records what the system *did*, not what the page
*says*; scoring one unchecked would certify today's bugs as correct and lock
them in — the exact failure `tests/fixtures/README.md` describes. It caught a
live example immediately: on `SK-SPSU-SSLSA-010` page 2 the proposal offered
`Fixed Assets → 8.0`, its *Appendix number*, because the "Appendix" header
wasn't recognised as a note column (`tables._NOTE_HDR_RE` matched "Note"/
"Notes" only) — **now fixed** (`_NOTE_HDR_RE` matches "Appendix" too).

Worth being precise about that fix's actual scope, since it was initially
overstated in conversation: it stops Appendix numbers being treated as money
anywhere on any document — real and verified (the row-1 Appendix cell had also
been garbling the note-column *fallback* check, via the same merge described
below, so this closed both paths at once). It does **not** touch the row-merge
that produces 3 of the current baseline's 4 `MISSING` entries; that is a
separate, harder defect described next, still open.

Also **name the column.** An expectation with no `column_label` is matched
leniently across every value column, which is forgiving of column order but
cannot catch a column shift — the prior year's figure appearing under the
current year's heading. `propose` always emits one.

## Updating the baseline

`baseline.json` is the committed high-water mark. Refreshing it is a deliberate,
reviewed commit, never a drive-by:

```bash
venv/Scripts/python -m tests.accuracy.runner score-all --write-baseline
```

## Current baseline

Three cases, 68 verified expectations total: **58 correct, 0 WRONG, 0
withheld, 10 missing.**

- `SK-SPSU-SSLSA-010 2022-23 SFS` — 16 correct, 0 WRONG, 0 withheld, 4 missing
  (of 20).
- `OD-SPSU-SO-032 2023-24 SFS` — 24 correct, 0 WRONG, 0 withheld, 6 missing
  (of 30).
- `MH-CPSU-ITSL-048 2024-25 SFS` — 18 correct, 0 WRONG, 0 withheld, 0 missing
  (of 18) — clean.

`WITHHELD` went to zero with Phase 3 increment 1, and **the explanation first
recorded here was wrong.** It said the blind prompt made the vision model's
read independent and that its agreement stopped six false `readers_disagree`
withholdings. Probing the live endpoint directly later showed the model was
not perceiving images at all: it read an image printing "HELLO 12345 TOTAL" as
"text", said "no image was provided" for a full balance sheet, and when pushed
invented an income statement that appears nowhere in the filing. Every blind
read was simply unusable ("second read did not contain a table", or 0 of N rows
matched), and an unusable read withholds nothing — so the six figures were
released on their own arithmetic, not corroborated. The earlier primed read had
been echoing its seed, which is where the six false disagreements came from.

`CORRECT` 52 → 58 is real (every one is scored against the printed page), but
it measures arithmetic-only verification, not a working second reader.
`vlm_read.perceives()` now disables the second read whenever the model cannot
read back a random number printed in a test image, and `compare()` discards any
read that fails to reproduce the table's own figures. Until the vision model is
served so that it can see, there is no second reader in this pipeline.

The ten remaining `MISSING` are not noise; each is a known defect this
measurement exists to drive out, and every one traces to the same underlying
class: **a label ends up on the wrong row relative to its value**, either
merged with a neighbour's label or split across two rows of its own. Phase 3
increment 1 (blind read, `Alignment` carrying real content, VLM-only-row
insertion) deliberately does not touch this class — inserting a fresh row from
the VLM's read is only safe when docling emitted *nothing* for that line;
every one of these ten is a case where docling emitted *something*, just
wrong, which `insert_unclaimed_rows`'s own duplicate-guard correctly declines
to touch. That is next: structural arbitration by footing, still open.

- `Balance being excess of Expenditure over Income (B-A)` — MISSING. Its label
  was merged with `Transfer to Special Reserve` (no ruling between them, and no
  enumerator to split on), so no row carries the true label.
- `Corpus/Capital Fund` ×2 and `Earmarked/Endownment Funds` — MISSING. Four
  labels were glommed onto one row on page 2 while their values stayed on the
  rows beneath, so the labels and the figures no longer line up.
- `Trade payables` ×2 and `Other current liabilities` ×2 — MISSING. The row is
  found (the label matches) but carries no figure: the label and its note
  number/values landed on adjacent, separately-mislabelled rows — a different
  shape of the same merge defect.
- `Property, Plant and Equipment` ×2 — MISSING. The printed label wraps across
  two lines; docling split it into two table rows, with the note number and
  both values attached to the second fragment (`"[and Intangible assets]"`),
  which the expectation's row label does not resemble at all. The opposite of
  the merge above: one item's own label split across two rows instead of
  several items' labels glommed onto one.

`MH-CPSU-ITSL-048` is clean today. Its own near-miss is worth recording: the
EPS row's printed label (`"(1) Basic (in rupees)"`) comes back merged with its
section heading (`"XIII. Earning per Equity Share..."`, OCR'd as `"Xill."`) —
same merge class as above — but here the *value* stays correctly attached to
that (verbose) label, so it still scores correct once the case file's
`row_label` names the label as actually extracted. A labelling-quality
instance of the defect, not a data-loss one; see the case file's own note.
