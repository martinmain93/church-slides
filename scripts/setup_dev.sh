#!/usr/bin/env bash
# Set up a development checkout: JS packages, the AI engine's Python
# environment, generated types and (optionally) the bundled models.
#
#     scripts/setup_dev.sh            # everything needed to run `pnpm tauri:dev`
#     scripts/setup_dev.sh --models   # also the chord, search and next-line models
#
# Safe to run again. It never runs a bare `uv sync`, which is exact and would
# remove the optional packages installed below (MLX on Apple silicon, and the
# tools the evals and perf scripts use). Use `uv run` day to day.
#
# Windows: run it from Git Bash.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

models=0
for arg in "$@"; do
  case "$arg" in
    --models) models=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) die "unknown option: $arg" ;;
  esac
done

say "Checking tools"
need() { command -v "$1" >/dev/null 2>&1 || die "missing $1: $2"; }
need node  "install Node 20+ (https://nodejs.org or nvm)"
need pnpm  "install pnpm 9+ (corepack enable, or https://pnpm.io)"
need cargo "install Rust via https://rustup.rs"
need uv    "install uv: https://docs.astral.sh/uv/"
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || die "Node 20+ needed (found $(node --version))"

say "Installing JS packages"
pnpm install

say "Installing the AI engine's Python environment (sidecar/.venv)"
(
  cd sidecar
  # --inexact: add what the lock needs, keep the extras below.
  uv sync --inexact
  if [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]]; then
    # Whisper on the Apple-silicon GPU. mlx-whisper declares torch but never
    # imports it while transcribing; removing it saves ~500 MB.
    uv pip install -e ".[mlx]"
    uv pip uninstall torch 2>/dev/null || true
  fi
  # Used by the evals and the perf scripts, not by the app.
  uv pip install soundfile psutil
)

say "Generating TypeScript types from the engine's models"
pnpm codegen

if (( models )); then
  say "Installing the bundled models"
  uv run --project sidecar python scripts/setup_chord_model.py
  uv run --project sidecar python scripts/setup_search_model.py
  uv run --project sidecar python scripts/setup_next_line_model.py
fi

say "Done. Run the app with: pnpm tauri:dev"
(( models )) || echo "Optional: scripts/setup_dev.sh --models adds the chord, search and next-line models (the app runs without them)."
