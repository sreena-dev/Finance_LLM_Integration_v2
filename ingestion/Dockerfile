# ---------------------------------------------------------------------------
# Ingestion service — scanned financial statements -> verified tables.
#
# Two stages, because the model weights are the expensive part of this image and
# they must not be re-downloaded on every code change. The builder installs the
# Python dependencies and prefetches the docling checkpoints; the runtime copies
# both in and adds the source last, so editing a .py file rebuilds one small
# layer rather than several gigabytes.
# ---------------------------------------------------------------------------

ARG PYTHON_VERSION=3.11

# =========================================================================
# Builder
# =========================================================================
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# libgl/libglib are OpenCV's runtime shared objects. opencv-python-headless
# still links libglib even though it draws nothing; without it the import fails
# with "libgl1.so.1: cannot open shared object file", which reads like a missing
# display rather than a missing package.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# The CPU wheel index, exactly as backend/requirements.txt already instructs for
# the FDR reranker. docling[models-local] declares a bare `torch`, so without
# this pip resolves the CUDA build: ~2.5GB of kernels this service will not
# reach on a CPU host. Remove the flag only if the compose file is also giving
# this container a GPU.
RUN pip install --prefix=/install -r requirements.txt \
      --extra-index-url https://download.pytorch.org/whl/cpu

# Bake the layout, TableFormer and OCR checkpoints into the image. Without this
# docling reaches Hugging Face the first time a user uploads anything: a
# multi-minute stall on an otherwise healthy request, and an outright failure on
# an air-gapped host. INGEST_DOCLING_ARTIFACTS points here at run time.
# download_models() is a library function, not a runnable module -- there is no
# argparse in docling/utils/model_downloader.py, only `docling-tools models
# download` on top of it. Calling the function directly rather than the CLI
# keeps this independent of the console-script shims landing on PATH under
# --prefix, which they do not reliably do.
#
# Layout, TableFormer and RapidOCR only. The code/formula and picture-classifier
# checkpoints are on by default but this pipeline never enables the enrichments
# that use them (do_code_enrichment, do_formula_enrichment and
# do_picture_classification are all left off in convert.py), so they would be
# several hundred megabytes of image that no request can reach.
ENV PYTHONPATH=/install/lib/python${PYTHON_VERSION}/site-packages
RUN python -c "\
from pathlib import Path; \
from docling.utils.model_downloader import download_models; \
download_models(output_dir=Path('/models'), progress=False, \
                with_layout=True, with_tableformer=True, with_rapidocr=True, \
                with_code_formula=False, with_picture_classifier=False)"

# =========================================================================
# Runtime
# =========================================================================
FROM python:${PYTHON_VERSION}-slim

ARG INGEST_PORT=12102
ARG TZ=Asia/Kolkata

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=${TZ} \
    INGEST_PORT=${INGEST_PORT} \
    INGEST_DOCLING_ARTIFACTS=/models \
    # Every library that reaches for a cache directory gets one it can write.
    # The rootfs is read-only below, and a torch or HF cache miss on a
    # read-only path surfaces as a permission error deep inside a model load.
    HF_HOME=/tmp/hf \
    TORCH_HOME=/tmp/torch \
    XDG_CACHE_HOME=/tmp/cache \
    # Each of these libraries spawns threads equal to the core count, and three
    # of them stacking inside one container oversubscribes the CPU limit badly
    # enough to make a conversion slower than single-threaded.
    OMP_NUM_THREADS=4 \
    MKL_NUM_THREADS=4

RUN apt-get update \
 && apt-get install -y --no-install-recommends libglib2.0-0 curl \
 && rm -rf /var/lib/apt/lists/*

# Non-root, matching the uid the gateway image already uses so a shared bind
# mount would not need its ownership reconciled.
RUN useradd --create-home --uid 10001 artha

COPY --from=builder /install /usr/local
COPY --from=builder --chown=artha:artha /models /models

WORKDIR /app
COPY --chown=artha:artha app ./app

USER artha
EXPOSE ${INGEST_PORT}

# A conversion is minutes long and CPU-saturating, so the health check needs a
# generous timeout: a probe that times out under load restarts the container
# mid-ingestion and loses every in-flight job.
HEALTHCHECK --interval=30s --timeout=15s --start-period=90s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${INGEST_PORT}/health" || exit 1

# One worker, deliberately. The job registry is in process memory (see
# app/jobs.py) -- a second worker would not see the first worker's jobs, and a
# progress stream would 404 half the time.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${INGEST_PORT} --workers 1 --timeout-keep-alive 75"]
