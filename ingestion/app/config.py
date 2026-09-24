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
    PIPELINE_VERSION = os.getenv("INGEST_PIPELINE_VERSION", "").strip() or "2026.09.24"

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
    # How often (wall-clock seconds), at most, `pipeline.run`'s `on_partial`
    # callback fires while table records are being verified -- see
    # `Job.publish_partial` (jobs.py). Tables-so-far are cheap to build (no
    # network call happens in that loop; the VLM/rescue calls that DO hit
    # the network already ran earlier, in the `vlm`/`verify` stages), so
    # this exists to bound EVENT volume on a large filing (60+ tables),
    # not compute cost -- firing one SSE frame per table on a 100-table
    # document is needless traffic for a consumer that only wants "roughly
    # how much is ready", and the LAST table always fires one regardless of
    # this interval, so nothing is ever more than one throttle-window stale.
    PARTIAL_RESULT_INTERVAL_SECONDS = _float("INGEST_PARTIAL_RESULT_INTERVAL_SECONDS", 3.0)

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

    # ---- Gemma (structure understanding + second read) --------------------
    # Same endpoint the FS agent generates against.
    VLM_ENABLED = _bool("INGEST_VLM_ENABLED", True)
    VLM_BASE_URL = (os.getenv("LLM_BASE_URL") or "").rstrip("/")
    VLM_MODEL = os.getenv("INGEST_VLM_MODEL") or os.getenv("LLM_MODEL_NAME") or ""
    VLM_API_KEY = os.getenv("GENERATION_API_KEY") or ""
    VLM_TIMEOUT = _float("INGEST_VLM_TIMEOUT", 180.0)
    # Two workloads share the endpoint and must not crowd each other out: a
    # STRUCTURE call returns a long JSON document and holds a slot for ~15-20 s,
    # a second-read call returns one short line and holds it for a second or
    # two. One semaphore for both lets a burst of structure calls queue every
    # cheap read behind them. The endpoint is also shared with live chat, so
    # both caps stay small.
    LLM_STRUCTURE_CONCURRENCY = _int("INGEST_LLM_STRUCTURE_CONCURRENCY", 2)
    LLM_VISION_CONCURRENCY = _int("INGEST_LLM_VISION_CONCURRENCY", 4)
    # Longest image side sent to the model. A full-page table crop at 300 DPI is
    # ~2500 px wide; past this the model gains no legibility, only image tokens.
    LLM_MAX_IMAGE_SIDE = _int("INGEST_LLM_MAX_IMAGE_SIDE", 2200)
    LLM_STRUCTURE_MAX_TOKENS = _int("INGEST_LLM_STRUCTURE_MAX_TOKENS", 6000)
    LLM_VISION_MAX_TOKENS = _int("INGEST_LLM_VISION_MAX_TOKENS", 300)
    # Every request and response is written under <dir>/<doc_id>/llm_logs/ so a
    # bad structure read can be inspected after the fact. Empty disables it.
    LLM_LOG_DIR = os.getenv("INGEST_LLM_LOG_DIR", "out").strip()
    # A structure call covers at most this many candidate rows; a longer table
    # is split, each chunk reusing the column schema the first one produced.
    STRUCTURE_ROWS_PER_CALL = _int("INGEST_STRUCTURE_ROWS_PER_CALL", 40)
    # Per-DOCUMENT cap on second-read row calls, so one huge filing cannot
    # issue thousands of requests. Rows past the cap are emitted OCR-only.
    SECOND_READ_MAX_ROWS = _int("INGEST_SECOND_READ_MAX_ROWS", 400)
    # Upsampling applied to a row crop before the second read: a one-row strip
    # is small enough that doubling it is nearly free and recovers thin strokes.
    SECOND_READ_CROP_SCALE = _float("INGEST_SECOND_READ_CROP_SCALE", 2.0)
    # An OCR token below this recognition score is not trusted on its own.
    OCR_MIN_CONFIDENCE = _float("INGEST_OCR_MIN_CONFIDENCE", 0.6)

    # ---- Page pipeline (concurrency) ---------------------------------------
    # Pages flow through layout -> OCR -> structure -> second read as soon as
    # the stage before them is done with that page, rather than every stage
    # finishing the whole document first. Layout (docling) stays single-file --
    # two converters at once run out of memory -- so the win comes from
    # overlapping it with the OCR and Gemma waits of pages already past it.
    PAGE_PIPELINE_DEPTH = _int("INGEST_PAGE_PIPELINE_DEPTH", 4)
    OCR_WORKERS = _int("INGEST_OCR_WORKERS", 4)
    # Tables docling's layout model never marked: a block of text whose lines
    # are mostly right-aligned numbers is offered as a table candidate.
    DETECT_UNMARKED_TABLES = _bool("INGEST_DETECT_UNMARKED_TABLES", True)
    # Docling reads text and LOCATES tables; table cells come from OCR words.
    # Turn this on only if the layout model does not report a table box without
    # its structure model running (it then reads structure but the boxes are
    # all that is used).
    LAYOUT_TABLE_STRUCTURE = _bool("INGEST_LAYOUT_TABLE_STRUCTURE", False)

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

    # Turn a page scanned sideways (text running bottom-to-top) upright before layout
    # and OCR, decided from OCR's own text-box shapes and recognition confidence
    # (app/orient.py). Separate from the flag above, which belongs to preprocessing and
    # rotates on image statistics. This one only ever acts on a page where most
    # detected text is standing up, and leaves any page it cannot judge alone.
    ORIENTATION_CORRECTION = _bool("INGEST_ORIENTATION_CORRECTION", True)

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
