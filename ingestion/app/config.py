"""Configuration for the ingestion service.

Every value is read from the environment once, at import. The service is
stateless apart from its in-flight job registry, so there is nothing to
reconfigure at run time and a restart is the intended way to change any of this.

Names are ``INGEST_*`` prefixed except where this service deliberately reuses a
name the gateway already defines (``LLM_BASE_URL``, ``LLM_MODEL_NAME``,
``GENERATION_API_KEY``) -- the VLM second-read talks to the same vLLM endpoint
the Financial Statement agent does, and giving it a second name for the same URL
is how the two silently drift apart.
"""

from __future__ import annotations

import os
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


class Config:
    # ---- service ---------------------------------------------------------
    PORT = _int("INGEST_PORT", 12102)
    LOG_LEVEL = os.getenv("INGEST_LOG_LEVEL", "info")

    # Which build of the extraction pipeline this is. BUMP IT whenever a change
    # alters what the pipeline would produce for the same bytes -- an OCR or
    # preprocessing change, a table-structure fix, a verification rule.
    #
    # It is load-bearing in two places. The conversion cache keys on it, so a
    # cached result from an older build is never served in place of running the
    # fix that was just deployed; and the gateway stores it against the
    # extraction, so a 30-day-old row can be told apart from a current one
    # instead of silently being trusted as though it came from today's code.
    PIPELINE_VERSION = os.getenv("INGEST_PIPELINE_VERSION", "").strip() or "2026.09.17"

    # Jobs are held in memory and reaped by age. A job's payload is the whole
    # extracted document, so this bounds RAM, not just bookkeeping.
    MAX_CONCURRENT_JOBS = _int("INGEST_MAX_CONCURRENT_JOBS", 2)
    JOB_RETENTION_SECONDS = _int("INGEST_JOB_RETENTION_SECONDS", 3600)
    # How many completed extractions are kept for the content-hash cache, so
    # re-uploading a byte-identical file skips the conversion instead of
    # spending minutes of OCR reproducing it. Small on purpose: an entry is a
    # WHOLE extracted document including its page images (several MB), so this
    # is bounded by memory in the same way MAX_CONCURRENT_JOBS is.
    RESULT_CACHE_ENTRIES = _int("INGEST_RESULT_CACHE_ENTRIES", 8)
    MAX_UPLOAD_BYTES = _int("INGEST_MAX_UPLOAD_BYTES", 64 * 1024 * 1024)
    MAX_PAGES = _int("INGEST_MAX_PAGES", 400)

    # ---- rasterisation ---------------------------------------------------
    # The corpus is 200 DPI (measured: 1654x2338 on A4 across every sample).
    # OCR engines are trained at ~300; rendering at the target rather than
    # upscaling a 200 DPI bitmap afterwards keeps the interpolation out of the
    # glyph edges, which is where thin CCITT strokes are already marginal.
    RENDER_DPI = _int("INGEST_RENDER_DPI", 300)
    # Below this the page is reported as low resolution regardless of anything
    # else. 150 is roughly where 8pt table digits stop surviving binarisation.
    MIN_ACCEPTABLE_DPI = _int("INGEST_MIN_ACCEPTABLE_DPI", 150)

    # ---- preprocessing ---------------------------------------------------
    DESKEW_ENABLED = _bool("INGEST_DESKEW", True)
    # Below this the rotation costs more in resampling blur than it recovers.
    DESKEW_MIN_ANGLE = _float("INGEST_DESKEW_MIN_ANGLE", 0.15)
    # Above this it is not skew, it is a mis-detected page; refuse and say so.
    DESKEW_MAX_ANGLE = _float("INGEST_DESKEW_MAX_ANGLE", 12.0)
    CLAHE_ENABLED = _bool("INGEST_CLAHE", True)
    DESPECKLE_ENABLED = _bool("INGEST_DESPECKLE", True)
    # Unsharp mask on pages precheck measured as soft.
    #
    # OFF, because it was measured and it LOST. Enabled, it sharpened 27 of 27
    # qualifying pages by ~1.7x Laplacian variance -- and cost 5 correct
    # figures across the three accuracy cases (58 -> 53 CORRECT, 10 -> 12
    # MISSING, WRONG unmoved at 0). On SK-SPSU-SSLSA-010 four figures stopped
    # being found at all, reported as "no row matching that label was
    # extracted": a STRUCTURAL failure, not a legibility one. The likely
    # mechanism is that the mask rings around table rules, and row-boundary
    # detection is exactly what that document is already hardest at.
    #
    # Kept rather than deleted because the step is sound and the tuning is
    # not: a gentler amount, a lower blur threshold, or restricting it to
    # bilevel scans may well win. Anyone who turns this on owes the harness a
    # run -- `venv/Scripts/python -m tests.accuracy.runner score-all` -- and
    # should expect to have to earn back those five figures.
    SHARPEN_ENABLED = _bool("INGEST_SHARPEN", False)

    # ---- docling ---------------------------------------------------------
    # Pre-downloaded model directory. Baked into the image by the Dockerfile so
    # nothing reaches Hugging Face at request time -- a request-time download is
    # a multi-minute stall on first use and an outright failure air-gapped.
    ARTIFACTS_PATH = os.getenv("INGEST_DOCLING_ARTIFACTS") or None
    OCR_LANG = [s for s in (os.getenv("INGEST_OCR_LANG", "english").split(",")) if s]
    TABLEFORMER_ACCURATE = _bool("INGEST_TABLEFORMER_ACCURATE", True)
    # Seconds docling may spend on ONE document. It cannot be scaled per call
    # (the converter, and the models behind it, are built once per process and
    # keyed on these options), so it has to cover the biggest file expected:
    # measured ~8 s/page with full-page OCR, and a 120-page annual report took
    # more than the old 600 s -- pages 80-120 came back empty. Whatever is still
    # unfinished at the limit is now named in the notes rather than dropped.
    DOCUMENT_TIMEOUT = _float("INGEST_DOCUMENT_TIMEOUT", 1800.0)

    # Where the layout and TableFormer models run: auto | cpu | cuda | cuda:N.
    #
    # `auto` is docling's own default and picks CUDA when torch can see it, so
    # this exists to *force* a choice rather than to enable one. Two reasons to
    # reach for it: pinning to `cpu` when a small GPU is running out of memory
    # mid-conversion, and pinning to `cuda` to make a silent fallback loud —
    # auto degrades quietly, which is how a machine with a GPU ends up doing
    # layout on the CPU for weeks without anyone noticing.
    #
    # Whether CUDA is even available is decided by the torch BUILD, not here: a
    # `+cpu` wheel reports no devices whatever this says.
    ACCELERATOR_DEVICE = os.getenv("INGEST_ACCELERATOR_DEVICE", "auto").strip() or "auto"
    # Docling defaults to 4. Only used for CPU inference and for the parts of the
    # pipeline that never move to the GPU.
    NUM_THREADS = _int("INGEST_NUM_THREADS", 4)

    # RapidOCR's angle classifier runs on EVERY detected text box to decide
    # whether it is rotated 180 degrees. Statement pages are printed upright
    # and deskewed before OCR, so it never changes a read -- measured on 8 OD
    # table pages: tables byte-identical with it off, conversion 34.0s -> 30.0s.
    # Turn it back on only for a corpus with genuinely upside-down text blocks.
    OCR_USE_ANGLE_CLASSIFIER = _bool("INGEST_OCR_USE_CLS", False)

    # Pages checked and corrected at once, BEFORE docling. These per-page steps
    # are independent (each reads and mutates only its own page), so they run
    # in threads; OpenCV and numpy release the GIL for the heavy work.
    #
    # Deliberately NOT applied to docling itself. Measured on 8 OD table pages
    # with 12 CPUs and a GPU: converting 2 page chunks in parallel was 14%
    # SLOWER (34.0s -> 38.6s) and 3 chunks 2.1x slower (72.4s), because OCR
    # already uses every core per call and table structure shares one GPU --
    # parallel conversions only fight over the same resources. Production has
    # 4 CPUs and no GPU, where that contention is worse.
    PAGE_WORKERS = _int("INGEST_PAGE_WORKERS", 4)

    # ---- VLM second read -------------------------------------------------
    # Same endpoint the FS agent generates against.
    VLM_ENABLED = _bool("INGEST_VLM_ENABLED", True)
    VLM_BASE_URL = (os.getenv("LLM_BASE_URL") or "").rstrip("/")
    VLM_MODEL = os.getenv("INGEST_VLM_MODEL") or os.getenv("LLM_MODEL_NAME") or ""
    VLM_API_KEY = os.getenv("GENERATION_API_KEY") or ""
    VLM_TIMEOUT = _float("INGEST_VLM_TIMEOUT", 180.0)
    # Upsampling applied to a TARGETED RESCUE CROP before it is sent -- a
    # factor on the already-rendered 300 DPI page, not a fraction of it. 1.0
    # sends the strip at native render scale; 2.0 doubles it. Whole-table
    # reads (transcribe(), above) are always sent at native scale regardless
    # of this setting: a full schedule is already at the model's resolution
    # limit and upsampling the whole thing only costs tokens. A two-row strip
    # (see vlm_read.row_band) is small enough that doubling it is nearly free
    # and recovers the thin strokes that made the cell unreadable in the
    # first place. Previously declared but unused as a whole-page-relative
    # scale; repurposed here rather than adding a second knob.
    VLM_CROP_SCALE = _float("INGEST_VLM_CROP_SCALE", 2.0)
    VLM_MAX_TABLES = _int("INGEST_VLM_MAX_TABLES", 60)
    # Output room for one table's transcription. A wide PPE roll-forward runs
    # to several thousand tokens of markdown; a read cut off by this limit is
    # discarded rather than compared as a prefix (see vlm_read.transcribe), so
    # raising it buys corroboration on big schedules that would otherwise fall
    # back to arithmetic alone.
    VLM_MAX_TOKENS = _int("INGEST_VLM_MAX_TOKENS", 8000)
    # How many tables' second reads are in flight at once. Each is an
    # independent network round trip (~15-20s observed) with nothing to share
    # between them, and running them one at a time is what makes a real
    # filing with 40+ tables take 5-10 minutes when docling's own layout+OCR
    # pass on the same pages takes a fraction of that. Bounded rather than
    # unbounded because this hits the SAME vLLM endpoint the live chat agent
    # generates against (see this module's docstring) -- a large ingestion
    # job must not be able to starve every other user's chat latency.
    VLM_CONCURRENCY = _int("INGEST_VLM_CONCURRENCY", 4)
    # Blind by default: no seed, no column pinning to docling's grid. A primed
    # read (docling's markdown pasted into the prompt, asked to "correct" it)
    # is not independent evidence -- agreement with a read the model was shown
    # first is not a second opinion, it is an echo. See vlm_read.transcribe.
    VLM_BLIND_READ = _bool("INGEST_VLM_BLIND_READ", True)

    # ---- Gross page-orientation (90/180/270) detection --------------------
    # OFF by default. Unlike plain skew correction (a few degrees, applied
    # for decades of use in this pipeline), this rotates the page a full
    # quadrant via np.rot90 whenever it fires -- a false positive does not
    # degrade the page, it destroys the layout: a table's grid lines are
    # themselves a strong row/column-banding signal, which is exactly what
    # the detector's scoring function looks for, so a real table is a
    # plausible way to trigger a false rotation rather than a corner case.
    # Measured against real filings, not just the synthetic masks the unit
    # tests use, before this should default to True.
    ORIENTATION_DETECTION_ENABLED = _bool("INGEST_ORIENTATION_DETECTION_ENABLED", False)

    # ---- VLM targeted rescue (recovering an individual unreadable cell) --
    # Off switch independent of VLM_ENABLED: a deployment may want the
    # existing whole-table second read but not the extra per-row round trips.
    VLM_RESCUE_ENABLED = _bool("INGEST_VLM_RESCUE_ENABLED", True)
    # Per-DOCUMENT cap on rescue calls -- the budget that stops one badly
    # scanned filing from issuing hundreds of extra round trips. When the cap
    # bites, the remaining cells stay withheld for BUDGET reasons rather than
    # for lack of evidence, and the quality report says so (see pipeline.py).
    VLM_MAX_RESCUES = _int("INGEST_VLM_MAX_RESCUES", 40)
    # Deliberately BELOW VLM_CONCURRENCY (4): rescues run in a second wave
    # after the whole-table pass, a bad scan can request dozens of them, and
    # the same starve-live-chat argument above the table-level knob applies
    # with more force to a long tail of small calls against the shared
    # endpoint.
    VLM_RESCUE_CONCURRENCY = _int("INGEST_VLM_RESCUE_CONCURRENCY", 2)
    # A rescue transcribes one row, not a whole schedule -- far less output
    # room is needed than VLM_MAX_TOKENS, and a smaller cap also bounds how
    # long one bad read can run before the finish_reason == "length" discard
    # (see vlm_read.transcribe_row) kicks in.
    VLM_RESCUE_MAX_TOKENS = _int("INGEST_VLM_RESCUE_MAX_TOKENS", 400)

    # ---- VLM band rescue (splitting a row TableFormer merged from several) -
    # A DIFFERENT defect from the single-cell rescue above: here the ROW
    # itself is wrong -- several real line items were merged into one grid
    # row because the source page prints them with no ruling line between
    # them, which TableFormer relies on to find row boundaries (see
    # tables._ENUM_MARKER_RE's docstring and vlm_read._looks_merged). A
    # single-cell rescue cannot fix this: there is no one printed row
    # matching the merged label to re-read. This asks the model to
    # transcribe every distinct line in a small cropped band instead, so a
    # garbled 4-item label can come back as 4 correctly labelled rows.
    VLM_BAND_RESCUE_ENABLED = _bool("INGEST_VLM_BAND_RESCUE_ENABLED", True)
    # Per-DOCUMENT cap, same reasoning as VLM_MAX_RESCUES -- separate budget
    # because this is a different, more expensive call (asks for several
    # rows, not one).
    VLM_MAX_BAND_RESCUES = _int("INGEST_VLM_MAX_BAND_RESCUES", 15)
    # How many consecutive OCR lines a band search will consider merging into
    # one crop. Bounded so a genuinely large, unrelated block of text cannot
    # be swept in by a loose concatenation match.
    VLM_BAND_MAX_LINES = _int("INGEST_VLM_BAND_MAX_LINES", 6)
    # More room than a single-cell rescue (VLM_RESCUE_MAX_TOKENS) since the
    # reply may contain several rows, but still far less than a whole-table
    # transcription (VLM_MAX_TOKENS).
    VLM_BAND_MAX_TOKENS = _int("INGEST_VLM_BAND_MAX_TOKENS", 800)

    # ---- Structural-risk reporting (vlm_read.assess_structural_risk) ------
    # Purely a REPORTING signal since number binding took over deciding
    # whether to replace a table's grid (see below) -- these two thresholds
    # no longer gate a replacement, only whether the note calling a grid
    # "unreliable" fires.
    # Fraction of a table's rows that must look like several merged line
    # items (vlm_read._looks_merged, aggregated) before the grid is flagged
    # unreliable.
    VLM_STRUCTURE_MERGED_ROW_THRESHOLD = _float("INGEST_VLM_STRUCTURE_MERGED_ROW_THRESHOLD", 0.15)
    # How far apart the OCR-geometry row count and the grid's own row count
    # must be, as a fraction of the larger, before that mismatch alone flags
    # the grid.
    VLM_STRUCTURE_ROW_MISMATCH_THRESHOLD = _float("INGEST_VLM_STRUCTURE_ROW_MISMATCH_THRESHOLD", 0.25)

    # ---- OCR number binding ("the VLM proposes, OCR disposes") -----------
    # The mechanism that makes "the VLM must not hallucinate a number"
    # structural rather than a prompting instruction: every figure the VLM
    # places into a table must bind to an actual OCR-read number at a
    # consistent position, or it never becomes a plain figure. OFF by
    # default -- unproven against the real corpus; see
    # structure_repair.py's module docstring and vlm_read.select_structure
    # for the two earlier, measured-and-discarded designs this replaces.
    # Anyone turning this on should run the accuracy harness across the
    # corpus first and expect to prove WRONG does not increase by one.
    NUMBER_BINDING_ENABLED = _bool("INGEST_NUMBER_BINDING_ENABLED", False)
    # A leftover OCR figure can be PROPOSED for an empty cell on geometric
    # evidence, but is only ever PLACED if a second reader independently
    # reads the same figure there (`vlm_read.confirm_gap_fill`). OCR read the
    # digits, the vision model read the digits, and they agree at the same
    # position -- the strongest evidence a gap fill can have. With the vision
    # model unreachable nothing is placed: there is no fallback to position
    # alone. Separately disableable so binding + coverage selection + the
    # half-read report can run without any gap filling at all.
    NUMBER_BINDING_GAP_FILL = _bool("INGEST_NUMBER_BINDING_GAP_FILL", True)
    # Per-DOCUMENT cap on the targeted re-reads the unbound-cell escape
    # hatch issues, same reasoning as VLM_MAX_BAND_RESCUES -- its own
    # budget since it is a different, additional call.
    VLM_MAX_BINDING_REREADS = _int("INGEST_VLM_MAX_BINDING_REREADS", 40)
    # Per-DOCUMENT cap on gap-fill confirmations. Between VLM_MAX_BAND_RESCUES
    # (15) and VLM_MAX_BINDING_REREADS (40): a gap fill needs a leftover
    # token that survives every refusal in `propose_gap_fills`, so they are
    # rarer than unbound cells, and this stops one badly fragmented scan from
    # issuing hundreds of calls.
    VLM_MAX_GAP_FILL_CONFIRMS = _int("INGEST_VLM_MAX_GAP_FILL_CONFIRMS", 20)
    # One character's width at ~10pt type -- see structure_repair.py's
    # _COLUMN_MARGIN_PT for the full justification (column gutters on this
    # corpus measure 80-100pt, an order of magnitude larger).
    NUMBER_BINDING_COL_MARGIN_PT = _float("INGEST_NUMBER_BINDING_COL_MARGIN_PT", 6.0)

    # `structure_repair.repair_from_geometry` runs on EVERY table at
    # conversion time, so unlike NUMBER_BINDING_ENABLED this is not opt-in: the
    # comparator that decides whether to rebuild a table's grid from OCR
    # geometry is OCR-coverage rather than footing strength, because footing
    # strength was measured on a real document to prefer the broken table.
    # Kept as a switch only so it can be reverted by env var without a deploy;
    # when False the geometry rebuild is skipped entirely (a cleaner kill
    # switch than resurrecting the discredited comparator).
    GEOMETRY_REPAIR_COVERAGE_GATE = _bool("INGEST_GEOMETRY_REPAIR_COVERAGE_GATE", True)

    # Rebuild a table docling's layout model never found (a statement with
    # almost no ruling lines) from its text items' positions, and say so when
    # figures were read but could not be assembled
    # (`structure_repair.synthesize_table`). Rebuilt tables go through the same
    # verification as any other. False skips the rebuild AND the caveat.
    SYNTHESIZE_MISSED_TABLES = _bool("INGEST_SYNTHESIZE_MISSED_TABLES", True)

    # Rejoin a caption that wrapped onto a second printed line with the row
    # carrying its figures, deterministically from the page's own geometry
    # (`structure_repair.join_wrapped_labels`). On by default: it moves no
    # figure -- the merged row is the value row's cells verbatim -- and it is
    # the only fix for this defect when the vision model is unreachable.
    WRAPPED_LABEL_JOIN = _bool("INGEST_WRAPPED_LABEL_JOIN", True)

    # Report an EMPTY cell as withheld when the page's own OCR text proves it
    # held a figure the grid dropped (`structure_repair.classify_label_only_rows`).
    # Without it a line item that lost its figures is indistinguishable from a
    # section heading -- verify.py skips any empty cell -- so silent data loss
    # is structurally undetectable. On by default: it only ever converts a
    # blank into an honest `[unreadable]`, and only on positive evidence.
    LABEL_ONLY_ROW_REPORTING = _bool("INGEST_LABEL_ONLY_ROW_REPORTING", True)

    # Withhold a RECOVERED figure whose sign contradicts its own line (a
    # negative total; a negative beside a comparable positive from the other
    # year). Withhold-only: it never flips a sign or rewrites a figure, and it
    # never vetoes a figure the column's arithmetic proved. Applied to
    # recovery candidates only -- never to cleanly read cells, where it would
    # risk withholding figures that are correct today.
    CONTEXT_SANITY_ENABLED = _bool("INGEST_CONTEXT_SANITY", True)

    # ---- verification ----------------------------------------------------
    # A subtotal is treated as footing if it is within this many currency units
    # OR this relative fraction, whichever is larger. Both are needed: filings
    # round to the nearest thousand (absolute slack) and also present in crore
    # with two decimals (relative slack).
    FOOTING_ABS_TOLERANCE = _float("INGEST_FOOTING_ABS_TOLERANCE", 1.0)
    FOOTING_REL_TOLERANCE = _float("INGEST_FOOTING_REL_TOLERANCE", 0.005)

    @staticmethod
    def artifacts_dir() -> Path | None:
        if not Config.ARTIFACTS_PATH:
            return None
        p = Path(Config.ARTIFACTS_PATH)
        return p if p.exists() else None

    @staticmethod
    def vlm_configured() -> bool:
        return bool(Config.VLM_ENABLED and Config.VLM_BASE_URL and Config.VLM_MODEL)
