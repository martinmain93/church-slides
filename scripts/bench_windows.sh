#!/usr/bin/env bash
# Run scripts/bench_whisper.py on GitHub's Windows runner and fetch the result.
#
#   scripts/bench_windows.sh [ref] [models]     defaults: HEAD's commit, tiny.en,base.en,small.en
#
# The commit must be on GitHub (pushed). Results land in build/bench/windows/
# (bench-windows-x64-ci.json and the printed table). About 20-40 minutes,
# most of it the ONNX export and model downloads.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

ref="${1:-$(git rev-parse HEAD)}"
models="${2:-tiny.en,base.en,small.en}"
git branch -r --contains "$ref" 2>/dev/null | grep -q . \
  || { echo "error: $ref isn't on GitHub yet (push it first)" >&2; exit 1; }

started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
gh workflow run bench.yml --ref main -f ref="$ref" -f models="$models"
run=""
for _ in $(seq 30); do
  sleep 4
  run="$(gh run list --workflow bench.yml --event workflow_dispatch -L 5 \
    --json databaseId,createdAt --jq "[.[] | select(.createdAt >= \"$started\")][0].databaseId // empty")"
  [[ -n "$run" ]] && break
done
[[ -n "$run" ]] || { echo "error: the benchmark didn't start" >&2; exit 1; }
echo "https://github.com/$(gh repo view --json nameWithOwner -q .nameWithOwner)/actions/runs/$run"
gh run watch "$run" --exit-status --interval 30 >/dev/null \
  || { echo "error: the benchmark failed: gh run view $run --log-failed" >&2; exit 1; }

out=build/bench/windows
rm -rf "$out" && mkdir -p "$out"
gh run download "$run" -n bench-windows -D "$out"
cat "$out/bench.txt"
