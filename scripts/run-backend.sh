#!/usr/bin/env bash
# Starts the Artha backend (FastAPI gateway) with the project venv.
# Single worker only: Trial Balance's `ask` mode keeps in-process session
# state, and each mode's pipeline is lazily imported into whichever worker
# serves it first. Scale with replicas, not --workers.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
VENV_DIR="$ROOT_DIR/venv"
PORT="${ARTHA_BACKEND_PORT:-12101}"

if [ -f "$VENV_DIR/Scripts/activate" ]; then
    source "$VENV_DIR/Scripts/activate"
elif [ -f "$VENV_DIR/bin/activate" ]; then
    source "$VENV_DIR/bin/activate"
else
    echo "No venv found at $VENV_DIR" >&2
    exit 1
fi

cd "$BACKEND_DIR"
exec uvicorn app.main:app --reload --port "$PORT"
