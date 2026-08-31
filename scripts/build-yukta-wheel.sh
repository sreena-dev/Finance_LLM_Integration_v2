#!/usr/bin/env bash
#
# Build the `yukta` wheel into backend/vendor/ so the backend image can install it.
#
# yukta is not on PyPI. `pip install yukta` inside a Dockerfile therefore fails,
# and a Docker build context cannot reach a source checkout that lives outside
# the repo — so the dependency has to be materialised as a wheel *inside* the
# context before building. That is all this script does.
#
#   ./scripts/build-yukta-wheel.sh                     # auto-detect the checkout
#   ./scripts/build-yukta-wheel.sh /path/to/yukta      # or point at it
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR_DIR="$REPO_ROOT/backend/vendor"

# Search order: explicit argument, then YUKTA_SRC, then the usual sibling
# locations on a dev box.
CANDIDATES=(
    "${1:-}"
    "${YUKTA_SRC:-}"
    "$HOME/vasanth/yukta"
    "$HOME/AGENTIC_CHUNKING/yukta"
    "$REPO_ROOT/../yukta"
)

YUKTA_DIR=""
for candidate in "${CANDIDATES[@]}"; do
    [ -n "$candidate" ] || continue
    if [ -f "$candidate/pyproject.toml" ] && [ -d "$candidate/yukta" ]; then
        YUKTA_DIR="$(cd "$candidate" && pwd)"
        break
    fi
done

if [ -z "$YUKTA_DIR" ]; then
    echo "ERROR: no yukta source checkout found." >&2
    echo "       Pass its path:  $0 /path/to/yukta" >&2
    echo "       or export YUKTA_SRC=/path/to/yukta" >&2
    exit 1
fi

echo "yukta source : $YUKTA_DIR"
echo "wheel output : $VENDOR_DIR"
mkdir -p "$VENDOR_DIR"

# Build in a throwaway venv. The system Python on Debian/Ubuntu is PEP 668
# "externally managed", so `pip install build` against it fails; and the build
# backend (setuptools + setuptools_scm) must not be installed into whatever
# environment the caller happens to have active.
BUILD_VENV="$(mktemp -d)"
trap 'rm -rf "$BUILD_VENV"' EXIT

python3 -m venv "$BUILD_VENV"
"$BUILD_VENV/bin/pip" install --quiet --upgrade pip build wheel setuptools

# Stale wheels are removed first: the Dockerfile installs vendor/*.whl as a
# glob, so leaving an old version behind makes which one wins depend on shell
# ordering rather than on intent.
rm -f "$VENDOR_DIR"/yukta-*.whl

( cd "$YUKTA_DIR" && "$BUILD_VENV/bin/python" -m build --wheel --outdir "$VENDOR_DIR" )

echo
echo "Built:"
ls -1sh "$VENDOR_DIR"/*.whl
echo
echo "Next:  docker compose up -d --build"
