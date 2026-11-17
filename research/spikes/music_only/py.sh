#!/bin/sh
# Run a spike script with this worktree's sidecar first on the path and the
# main checkout's venv (never `uv run`: it prunes the ML extras).
HERE="$(cd "$(dirname "$0")/../.." && pwd)"
export PYTHONPATH="$HERE/sidecar"
export SPIKE_MUSIC_DIR="${SPIKE_MUSIC_DIR:-$HERE/build/spike_music_only}"
cd "$HERE" || exit 1
# MAIN_CHECKOUT: the checkout whose sidecar/.venv has the ML extras (a
# worktree has none of its own); defaults to this one.
exec "${MAIN_CHECKOUT:-$HERE}/sidecar/.venv/bin/python" "$@"
