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

    # Jobs are held in memory and reaped by age. A job's payload is the whole
    # extracted document, so this bounds RAM, not just bookkeeping.
    MAX_CONCURRENT_JOBS = _int("INGEST_MAX_CONCURRENT_JOBS", 2)
    JOB_RETENTION_SECONDS = _int("INGEST_JOB_RETENTION_SECONDS", 3600)
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

    # ---- docling ---------------------------------------------------------
    # Pre-downloaded model directory. Baked into the image by the Dockerfile so
    # nothing reaches Hugging Face at request time -- a request-time download is
    # a multi-minute stall on first use and an outright failure air-gapped.
    ARTIFACTS_PATH = os.getenv("INGEST_DOCLING_ARTIFACTS") or None
    OCR_LANG = [s for s in (os.getenv("INGEST_OCR_LANG", "english").split(",")) if s]
    TABLEFORMER_ACCURATE = _bool("INGEST_TABLEFORMER_ACCURATE", True)
    DOCUMENT_TIMEOUT = _float("INGEST_DOCUMENT_TIMEOUT", 600.0)

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

    # ---- VLM second read -------------------------------------------------
    # Same endpoint the FS agent generates against.
    VLM_ENABLED = _bool("INGEST_VLM_ENABLED", True)
    VLM_BASE_URL = (os.getenv("LLM_BASE_URL") or "").rstrip("/")
    VLM_MODEL = os.getenv("INGEST_VLM_MODEL") or os.getenv("LLM_MODEL_NAME") or ""
    VLM_API_KEY = os.getenv("GENERATION_API_KEY") or ""
    VLM_TIMEOUT = _float("INGEST_VLM_TIMEOUT", 180.0)
    # A table image is sent at this scale relative to the 300 DPI page. 1.0 is
    # already ~300 DPI of the crop; going higher mostly costs tokens.
    VLM_CROP_SCALE = _float("INGEST_VLM_CROP_SCALE", 1.0)
    VLM_MAX_TABLES = _int("INGEST_VLM_MAX_TABLES", 60)

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
