# Phase 3 — GPU-backed horizontal page-batch workers

**Status: design only, not implemented.** This is an infrastructure/budget decision (new
hosting, a GPU-per-worker pool) that no amount of code in this repo can substitute for, and
its core hypothesis is **untested** — the only hardware available while writing this had a
single GPU, which cannot validate the thing this design exists to check. Read the whole
"What this design cannot tell you" section before treating any of this as a green light.

## Why this phase exists

The rest of this pipeline's ingestion work (Phases 1–2) made the *wait* for a 100–200 page
scanned filing honest and more usable — real progress reporting, tables available before
the whole document finishes. Neither touches the actual wall-clock time, which is
25–35+ minutes for a document that size, against a stated practical target of 3–5 minutes.
This is the only phase in the plan that could plausibly close that gap, and it requires
hardware this repo doesn't have.

## The measured fact this design has to work around

`ingestion/app/convert.py`'s `convert()` processes an entire document as **one single
blocking call** — `converter.convert(stream, raises_on_error=False)`. This is deliberate,
not an oversight: `ingestion/app/config.py` records a real measurement that splitting a
document into 2–3 parallel page-chunks made conversion **slower**, not faster:

| Chunks | Time (8 pages) | vs. single call |
|---|---|---|
| 1 (current) | 34.0s | — |
| 2 | 38.6s | 14% slower |
| 3 | 72.4s | 2.1x slower |

Measured on a 12-CPU, 1-GPU machine (the same machine this design doc was written on,
confirmed directly: `torch.cuda.device_count() == 1`, `os.cpu_count() == 12`). The stated
reason: OCR already saturates every CPU core per call, and TableFormer shares the one GPU —
parallel conversions on this hardware don't get more resources, they fight over the same
ones. Production is worse-provisioned still (4 CPU, no GPU at all).

## The hypothesis

That regression is **single-GPU contention**, not a property of chunking itself. If each
parallel chunk gets its own dedicated GPU (or GPU slice) instead of sharing one, the
contention that made chunking counterproductive should go away, and pages genuinely convert
in parallel rather than one after another — the only way to plausibly get a 150-page
document's `convert()` stage from ~20 minutes down to something in single-digit minutes.

**This has not been tested.** It needs at least 2 GPUs to even attempt — one machine with 2+
GPUs, or 2+ machines each with their own — and none were available while writing this.

## Architecture, if the hypothesis holds

```
                    ┌─────────────────┐
  upload ──────────▶│  page dispatcher │
                    └────────┬─────────┘
                             │ splits N pages into K batches
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        ┌───────────┐  ┌───────────┐  ┌───────────┐
        │ worker 1  │  │ worker 2  │  │ worker K  │
        │ (own GPU) │  │ (own GPU) │  │ (own GPU) │
        │ convert() │  │ convert() │  │ convert() │
        └─────┬─────┘  └─────┬─────┘  └─────┬─────┘
              │              │              │
              └──────────────┼──────────────┘
                             ▼
                    ┌─────────────────┐
                    │  batch merger    │
                    └────────┬─────────┘
                             ▼
                     one Converted result
```

### The dispatcher

A new component in front of `ingestion/app/convert.py`. Docling's own `DocumentConverter.
convert()` already accepts a `page_range: tuple[int, int]` parameter (confirmed directly
against the installed API, `docling==2.123.1` — `inspect.signature(DocumentConverter.
convert)`), so batching by page range doesn't need new PDF-splitting code; it needs a
dispatcher that:

1. Decides batch boundaries (page count / worker count, or a fixed batch size).
2. Sends each `(pdf_bytes, page_range)` pair to an available worker (its own process, its
   own GPU visible via `CUDA_VISIBLE_DEVICES` or equivalent).
3. Collects each worker's `Converted` result.

### The merger — the real engineering risk, not the dispatch

This is where most of the actual risk in this phase lives, and it's mostly *correctness*
risk, not performance risk:

- **Page numbering.** Each worker's `Converted` result numbers pages from 1 within its own
  batch, not from its true position in the document. Every downstream consumer of `page_no`
  (`TableRecord.page_ocr_start`, citations, the document pane) needs the merger to
  re-offset every page number by the batch's starting page — miss one code path and a
  citation points at the wrong page silently, not with an error.
- **Cross-page table continuations.** This corpus already has `structure_repair.py`
  machinery (`_fetch_untitled_continuations` and friends) specifically because financial
  statements split tables across page boundaries constantly. If a table starts on the last
  page of batch 1 and continues on the first page of batch 2, EACH worker sees only its own
  half and has no way to know the other half exists — that continuation-detection logic
  needs the whole document's pages in one place to work at all. The merger has to either
  (a) re-run continuation-stitching across batch boundaries after merging, most likely by
  reusing the existing `_fetch_untitled_continuations` path with awareness of *which* pages
  sat at a batch seam, or (b) overlap batches by one page at each seam and de-duplicate —
  both are real work, not a data concatenation.
- **`doc_id`/confidence bookkeeping.** `_absorb_confidence` (`convert.py`) maps docling's
  per-page confidence scores onto `PageQuality` **positionally**, not by page number
  (`convert.py`'s own comment explains why — a previous by-number version silently
  misattributed scores when pages were dropped). A merger has to preserve that positional
  correctness across batch boundaries too, not just concatenate lists.
- **One converter instance per worker, not a shared one.** `convert.py`'s `_converter()` is
  a process-wide singleton specifically because building `DocumentConverter` loads the
  layout + TableFormer models into memory once, guarded by a lock so two concurrent jobs
  don't double-load it (`convert.py`'s own comment: "building this twice concurrently would
  load two copies of the models into a container sized for one"). Each GPU worker is its own
  process by construction here, so this constraint is naturally satisfied — worth stating
  explicitly so a future implementer doesn't assume it needs solving.

None of this is exotic, but all of it is real, and none of it is validated by anything in
this repo today.

## What this design cannot tell you

- **Whether dedicated-GPU-per-worker actually removes the measured contention.** That is
  the entire premise, and it is unverified. If it turns out GPU memory bandwidth or some
  other shared resource still bottlenecks two TableFormer instances running concurrently
  even on separate GPUs, this whole phase does not deliver the speedup it's justified by.
- **What K (worker count) is worth paying for.** More workers means more GPU cost for
  diminishing wall-clock return past some point (OCR/layout have fixed per-page costs;
  merge overhead grows with K). No data exists to size this.
- **Whether the merge correctness work is bug-free without real multi-batch documents to
  test against.** The corpus's genuinely hard cases (tables split across pages, multi-page
  schedules) are exactly the cases a batch-seam bug would silently break, and they need to
  be deliberately tested against a chunked run, not assumed correct by construction.

## Before spending budget on this

1. **Re-run the contention benchmark on real multi-GPU hardware first**, before building
   any of the above. Same methodology as the original (`convert.py`'s comment): same 8
   real corpus pages, 1 vs. 2 vs. 3 parallel chunks, each chunk pinned to its own GPU via
   `CUDA_VISIBLE_DEVICES`. If 2-GPU chunking isn't at least close to 2x faster than 1 chunk
   on 1 GPU, the hypothesis is wrong and this whole design should be shelved, not built.
2. Only after that benchmark confirms the hypothesis: size K against real cost, then build
   the dispatcher + merger above, with the continuation-stitching correctness case as a
   first-class test, not an afterthought.
3. Nothing in `ingestion/app/verify.py`'s escalation ladder needs to change for any of
   this — Phase 3 is purely about running the same, already-verified pipeline on more
   hardware, not about changing how correctness is checked.
