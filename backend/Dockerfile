# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# Artha.AI — integrated gateway (backend)
#
# Two stages: a builder that compiles/collects wheels into a virtualenv, and a
# slim runtime that copies only that venv. Keeps compilers out of the shipped
# image and keeps the layer that reinstalls dependencies separate from the
# layer that changes on every code edit.
# ---------------------------------------------------------------------------

ARG PYTHON_VERSION=3.11


# ===========================================================================
# Stage 1 — build the virtualenv
# ===========================================================================
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# gcc/g++ and libpq-dev are only needed if pip falls back to an sdist for any
# pinned dependency (psycopg2-binary/lxml/pandas normally ship manylinux
# wheels). Present here, absent from the runtime stage.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential \
      libpq-dev \
 && rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/venv
RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

RUN pip install --upgrade pip setuptools wheel

# Dependencies before source: editing a .py file must not re-run this layer.
COPY requirements.txt ./
# The CPU wheel index is added for torch, which the FDR mode pulls in for local
# cross-encoder reranking. requirements.txt asks for the CPU build in a comment,
# but a comment cannot select an index: plain PyPI resolves torch to the CUDA
# build, which is ~2.5GB of wheels and drivers this image has no GPU to use.
# --extra-index-url (not --index-url) so PyPI stays the source for everything
# else; pip prefers the CPU wheel because only that index publishes it.
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt

# `yukta` is not on PyPI. It is supplied as a wheel in backend/vendor/ (build
# one with scripts/build-yukta-wheel.sh). Without it, Financial Statements,
# SAR report *generation* and Trial Balance ask/audit all report themselves
# unavailable — 3 of the 4 modes — so a missing wheel fails the build rather
# than shipping a mostly-dead image. Set ALLOW_MISSING_YUKTA=1 to override.
ARG ALLOW_MISSING_YUKTA=0
COPY vendor/ /tmp/vendor/
RUN set -eu; \
    if ls /tmp/vendor/*.whl >/dev/null 2>&1; then \
        echo "Installing vendored wheels:"; ls -1 /tmp/vendor/*.whl; \
        pip install /tmp/vendor/*.whl; \
    elif [ "$ALLOW_MISSING_YUKTA" = "1" ]; then \
        echo "WARNING: no wheel in backend/vendor/ — yukta is absent."; \
        echo "WARNING: Financial Statements, SAR generation and TB ask/audit"; \
        echo "WARNING: will report themselves unavailable at runtime."; \
    else \
        echo "ERROR: no wheel found in backend/vendor/."; \
        echo "ERROR: yukta is not on PyPI and 3 of 4 modes need it."; \
        echo "ERROR: Run scripts/build-yukta-wheel.sh, or rebuild with"; \
        echo "ERROR:   --build-arg ALLOW_MISSING_YUKTA=1 to proceed without it."; \
        exit 1; \
    fi; \
    rm -rf /tmp/vendor

# Fail the build — not the first request — if a mode's imports are broken.
RUN python -c "import fastapi, uvicorn, psycopg2, pandas, openpyxl, xlrd, lxml; print('core imports OK')"


# ===========================================================================
# Stage 2 — runtime
# ===========================================================================
FROM python:${PYTHON_VERSION}-slim AS runtime

# Asia/Kolkata by default: every timestamp this service logs, and every
# financial-year boundary the pipelines derive from "now", is IST. Overridable
# per-deployment via the TZ build arg / the TZ environment variable.
ARG TZ=Asia/Kolkata
ENV TZ=${TZ}

# libpq5 is psycopg2's runtime shared library; tzdata is what makes TZ mean
# anything inside the container. Both are runtime-only — no compilers here.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libpq5 \
      tzdata \
 && ln -snf "/usr/share/zoneinfo/${TZ}" /etc/localtime \
 && echo "${TZ}" > /etc/timezone \
 && rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    PYTHONPATH=/app

COPY --from=builder /opt/venv /opt/venv

# Non-root, fixed uid/gid so a mounted volume has predictable ownership
# regardless of where the image runs.
RUN groupadd --system --gid 10001 artha \
 && useradd --system --uid 10001 --gid artha --no-create-home --shell /usr/sbin/nologin artha

# Two writable locations, both owned by artha at build time so that a named
# volume mounted over /var/lib/artha inherits this ownership when Docker first
# creates it (a volume with no matching directory in the image comes up
# root-owned and unwritable by a non-root user).
#
# /var/lib/artha — durable state. `yukta` persists every agent's chat session
#   here as JSON (yukta/core/storage.py). It defaults to ~/.yukta; YUKTA_DATA_DIR
#   redirects it somewhere explicit and backed by a volume instead.
#
# /var/lib/artha/hf — the Hugging Face cache (HF_HOME). The FDR reranker
#   (BAAI/bge-reranker-v2-m3) is 2.29GB; HF's default location is under $HOME,
#   which is a 64MB tmpfs here, so the model could never be cached there no
#   matter what the network does — it reported itself unavailable with a
#   "couldn't connect to huggingface.co" message that pointed at the wrong
#   cause. On the volume it downloads once and survives restarts.
#
# /home/artha — HOME must exist and be writable even though nothing should need
#   it: the user was created with --no-create-home, so Path.home() resolved to a
#   missing directory and any library reaching for $HOME (a ~/.cache, a
#   tokenizer download) failed with "[Errno 30] Read-only file system" from deep
#   inside a pipeline, where it reads as an LLM error rather than a mount
#   problem. Compose mounts a tmpfs here so such writes are scratch, not state.
RUN mkdir -p /home/artha /var/lib/artha/yukta /var/lib/artha/hf \
 && chown -R artha:artha /home/artha /var/lib/artha \
 && chmod 700 /home/artha

ENV HOME=/home/artha \
    YUKTA_DATA_DIR=/var/lib/artha/yukta \
    HF_HOME=/var/lib/artha/hf

WORKDIR /app
COPY --chown=artha:artha . /app

# This app's own code writes nothing to disk — uploaded trial balances go
# straight to Postgres and the audit workbook is built in an in-memory buffer —
# so the root filesystem is read-only (set in docker-compose.yml). Its
# dependencies are a different matter: see the two writable paths above.
USER artha

# The gateway's own port. Kept identical inside and outside the container so a
# log line quoting a port matches what you'd curl from the host.
ARG BACKEND_PORT=12101
ENV ARTHA_BACKEND_PORT=${BACKEND_PORT}
EXPOSE ${BACKEND_PORT}

# /api/health does not probe the pipelines (that is /api/modes?probe=true,
# which opens DB connections and can take tens of seconds) — so this stays a
# cheap liveness signal and never marks a healthy gateway unhealthy just
# because one downstream LLM endpoint is slow.
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import os,sys,urllib.request;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ['ARTHA_BACKEND_PORT']+'/api/health',timeout=8).status==200 else 1)"]

# Single worker is deliberate, not a placeholder. Trial Balance's `ask` mode
# keeps conversation memory in an in-process SessionStore, and each mode's
# pipeline is imported lazily into the worker that first serves it. A second
# worker would scatter follow-up questions across processes that do not share
# that memory, and duplicate every pipeline's resident footprint. Scale this
# service by running more replicas behind the proxy, not more workers.
CMD ["sh", "-c", "exec uvicorn app.main:app \
     --host 0.0.0.0 \
     --port \"${ARTHA_BACKEND_PORT}\" \
     --workers \"${ARTHA_BACKEND_WORKERS:-1}\" \
     --timeout-keep-alive \"${ARTHA_KEEPALIVE_TIMEOUT:-75}\" \
     --proxy-headers \
     --forwarded-allow-ips '*' \
     --log-level \"${ARTHA_LOG_LEVEL:-info}\""]
