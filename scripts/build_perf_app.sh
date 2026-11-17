#!/usr/bin/env bash
# Build the app the performance suite drives (scripts/perf_suite.py app.*).
#
#   scripts/build_perf_app.sh                    # reuse the frozen engine in src-tauri/resources/sidecar
#   scripts/build_perf_app.sh --rebuild-engine   # freeze the engine from the source first
#
# An optimised build, like a release, plus the agent UI driver and the
# engine's dev tools (the `ui-driver` Cargo feature), under its own name and
# identifier: "Church Slides Perf", com.churchslides.app.perf, so it keeps
# its own data folder and can never be mistaken for, or overwrite, the real
# app. Built in src-tauri/target-perf so a release build never picks up the
# feature. The app lands in build/perf-app/. Not signed for distribution:
# it runs on this Mac only.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

engine=src-tauri/resources/sidecar/auto-presenter-sidecar
arch="$(uname -m)"; [[ "$arch" == arm64 ]] && arch=aarch64
# A release leaves the last architecture it built (Intel) staged.
if [[ "${1:-}" == "--rebuild-engine" || ! -x "$engine" ]] \
   || [[ "$(lipo -archs "$engine" 2>/dev/null)" != "$(uname -m)" ]]; then
  uv run --project sidecar python scripts/build_sidecar.py --arch "$arch"
fi

conf="$(mktemp -t perf-app).json"
trap 'rm -f "$conf"' EXIT
cat > "$conf" <<JSON
{
  "productName": "Church Slides Perf",
  "identifier": "com.churchslides.app.perf",
  "bundle": {
    "targets": ["app"],
    "createUpdaterArtifacts": false,
    "resources": { "resources/sidecar/": "sidecar/" }
  }
}
JSON

APPLE_SIGNING_IDENTITY=- CARGO_TARGET_DIR="$PWD/src-tauri/target-perf" \
  pnpm tauri build --config "$conf" --features ui-driver

app="src-tauri/target-perf/release/bundle/macos/Church Slides Perf.app"
rm -rf build/perf-app
mkdir -p build/perf-app
cp -R "$app" build/perf-app/
echo "Built build/perf-app/Church Slides Perf.app"
