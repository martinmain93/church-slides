#!/usr/bin/env bash
# Build both Mac installers, publish them, and redeploy the download page.
#
#   scripts/release.sh              release the version tag at HEAD
#   scripts/release.sh --site-only  redeploy the page without rebuilding
#   scripts/release.sh --build-only build the working tree into build/release,
#                                   publish nothing (for trying an installer)
#   scripts/release.sh --windows [vX.Y.Z]
#                                   add the (experimental) Windows installer to
#                                   a published release; run it after the push
#   SKIP_TESTS=1 scripts/release.sh skip the test suites
#   SKIP_PERF=1 scripts/release.sh  skip the engine performance runs
#   FULL_PERF=1 scripts/release.sh  also run the comprehensive suite before a
#                                   major release (scripts/perf_suite.py, about
#                                   1.5 hours: a long service, scripture,
#                                   search, chords, the app itself). Warns,
#                                   never fails.
#
# Normally run by the pre-push hook (scripts/githooks/pre-push) when you push a
# `v*` tag; see scripts/bump_version.sh for making one. Everything runs on this
# Mac: GitHub Actions is off for this repo because macOS runners cost 10x.
#
# Where things go:
#   installers -> GitHub release <tag> on $RELEASES_REPO (public, so the links
#                 work without a GitHub login; the source repo stays private)
#   updates    -> the same release: a signed .app.tar.gz per Mac and latest.json,
#                 which installed apps check at startup (see tauri.conf.json)
#   page       -> Netlify site $NETLIFY_SITE (the page links to
#                 releases/latest/download/..., so old links keep working),
#                 plus changelog.html built from CHANGELOG.md
#   windows    -> built by .github/workflows/windows.yml (PyInstaller can't
#                 cross-compile), then signed here and added to the release
#
# The installers are built from the working tree, so the tree must be clean
# and HEAD must be the tag: what ships is exactly what the tag names.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# Destinations, signing and telemetry addresses: scripts/release.local.env
# (git-ignored; start from release.env.example).
[[ -f scripts/release.local.env ]] \
  || { echo "release: copy scripts/release.env.example to scripts/release.local.env and fill it in" >&2; exit 1; }
# shellcheck source=release.env.example
source scripts/release.local.env
# Compiled into the shell (option_env!) and the UI (Vite); empty = none.
export CHURCH_SLIDES_SENTRY_DSN="${CHURCH_SLIDES_SENTRY_DSN:-}"
export VITE_CHURCH_SLIDES_STATS_URL="${VITE_CHURCH_SLIDES_STATS_URL:-}"
[[ -n "$CHURCH_SLIDES_SENTRY_DSN" ]] || unset CHURCH_SLIDES_SENTRY_DSN

OUT="$ROOT/build/release"
PERF="$ROOT/build/perf"
SITE_OUT="$ROOT/build/site"
NETLIFY=(pnpm --package="netlify-cli@$NETLIFY_CLI_VERSION" dlx netlify)

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# Stage the page with this version stamped into it. index.html keeps the
# version of the last release so a local preview still reads sensibly.
build_site() {
  local version="$1"
  rm -rf "$SITE_OUT" && mkdir -p "$SITE_OUT"
  rsync -a --exclude README.md site/ "$SITE_OUT/"
  # The band page for hosted live rooms (/live/): the same band screen the
  # relay serves, built from this version of the app.
  pnpm -s exec vite build --config relay/vite.band.config.ts --logLevel warn \
    || die "couldn't build the band page"
  cp relay/dist/band/band.js relay/dist/band/band.css "$SITE_OUT/live/"
  # The touch icon comes from the app's own icons, so a new icon reaches the page.
  # The favicon is the bare window (site/favicon.*), not the app icon's square.
  cp src-tauri/icons/128x128@2x.png "$SITE_OUT/icon.png"
  local date; date="$(date '+%-d %B %Y')"
  sed -i '' -E \
    -e "s#<span id=\"version\">[^<]*</span>#<span id=\"version\">$version</span>#" \
    -e "s#<span id=\"released\">[^<]*</span>#<span id=\"released\">$date</span>#" \
    "$SITE_OUT/index.html"
  grep -q "<span id=\"version\">$version</span>" "$SITE_OUT/index.html" \
    || die "couldn't stamp the version into the page"

  # Windows lags the Mac (it's added by --windows after the release), so the
  # page links to the newest release that has a Windows installer, or hides
  # the Windows download when none does.
  local windows_tags
  windows_tags="$(gh api "repos/$RELEASES_REPO/releases?per_page=100" \
    --jq '[.[] | select(any(.assets[]; .name == "Church-Slides-windows-setup.exe")) | .tag_name] | join(",")')" \
    || die "couldn't list the releases on $RELEASES_REPO (gh auth status?)"
  python3 scripts/changelog.py windows "$SITE_OUT/index.html" "$windows_tags"

  # Each release's perf.json (its build's engine performance) becomes a card
  # under that version on the changelog page.
  local perf_cache="$ROOT/build/site-perf" perf_tags tag
  rm -rf "$perf_cache"
  perf_tags="$(gh api "repos/$RELEASES_REPO/releases?per_page=100" \
    --jq '.[] | select(any(.assets[]; .name == "perf.json")) | .tag_name')" \
    || die "couldn't list the releases on $RELEASES_REPO"
  for tag in $perf_tags; do
    gh release download "$tag" -R "$RELEASES_REPO" -p perf.json -D "$perf_cache/$tag" \
      || die "couldn't fetch $tag's perf.json"
  done
  # Versions taken down from the releases repo (0.0.1-0.0.5 bundled GPL
  # code) keep their notes on the page but lose their download links.
  local published_tags
  published_tags="$(gh api "repos/$RELEASES_REPO/releases?per_page=100" --jq '[.[].tag_name] | join(",")')" \
    || die "couldn't list the releases on $RELEASES_REPO"
  python3 scripts/changelog.py page "$SITE_OUT/changelog.html" "$windows_tags" "$perf_cache" "$published_tags"
}

deploy_site() {
  say "Deploying the page to Netlify ($NETLIFY_SITE)"
  "${NETLIFY[@]}" deploy --prod --no-build --dir "$SITE_OUT" --site "$NETLIFY_SITE" \
    --message "$1" \
    || die "Netlify deploy failed. First time? Run: pnpm --package=netlify-cli@$NETLIFY_CLI_VERSION dlx netlify login"
}

current_version() {
  python3 -c 'import json;print(json.load(open("src-tauri/tauri.conf.json"))["version"])'
}

mode="${1:-release}"
if [[ "$mode" == "--site-only" ]]; then
  version="$(current_version)"
  build_site "$version"
  deploy_site "Page only, v$version"
  exit 0
fi

# --- Windows (experimental) ----------------------------------------------------
# GitHub Actions builds it from the pushed tag; this Mac signs it for the
# updater, so the update key never leaves this machine.
if [[ "$mode" == "--windows" ]]; then
  tag="${2:-v$(current_version)}"
  version="${tag#v}"
  [[ -f "$UPDATER_KEY" ]] || die "no update-signing key at $UPDATER_KEY"
  gh release view "$tag" -R "$RELEASES_REPO" >/dev/null 2>&1 \
    || die "$tag isn't released on $RELEASES_REPO yet: push the tag first"
  git ls-remote --exit-code --tags origin "refs/tags/$tag" >/dev/null \
    || die "$tag isn't on GitHub (origin) yet"

  say "Building the Windows installer for $tag on GitHub Actions (about 30 min)"
  started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  # WINDOWS_REF builds from a later commit than the tag, for a Windows-only
  # test fix made after the Mac release went out (the tag is already
  # published). The app it builds must still be this version (checked below).
  # A full commit id: actions/checkout reads a short one as a branch name
  # (and fails to find it), seen with WINDOWS_REF=4a9ca16.
  if [[ -n "${WINDOWS_REF:-}" ]]; then
    WINDOWS_REF="$(git rev-parse --verify "$WINDOWS_REF^{commit}")" \
      || die "WINDOWS_REF isn't a commit here"
  fi
  gh workflow run windows.yml --ref main -f ref="${WINDOWS_REF:-$tag}"
  run=""
  for _ in $(seq 30); do
    sleep 4
    run="$(gh run list --workflow windows.yml --event workflow_dispatch -L 5 \
      --json databaseId,createdAt --jq "[.[] | select(.createdAt >= \"$started\")][0].databaseId // empty")"
    [[ -n "$run" ]] && break
  done
  [[ -n "$run" ]] || die "the Windows build didn't start (see: gh run list --workflow windows.yml)"
  echo "  https://github.com/$(gh repo view --json nameWithOwner -q .nameWithOwner)/actions/runs/$run"
  gh run watch "$run" --exit-status --interval 30 >/dev/null \
    || die "the Windows build failed: gh run view $run --log-failed"

  win="$ROOT/build/windows"
  rm -rf "$win" && mkdir -p "$win"
  gh run download "$run" -n church-slides-windows -D "$win"
  [[ "$(cat "$win/VERSION")" == "$version" ]] \
    || die "the Windows build is version $(cat "$win/VERSION"), not $version"
  setup="$win/Church-Slides-windows-setup.exe"

  say "Signing it for the updater and adding it to $tag"
  pnpm tauri signer sign -f "$UPDATER_KEY" -p "" "$setup" >/dev/null
  gh release download "$tag" -R "$RELEASES_REPO" -p latest.json -p SHA256SUMS.txt -D "$win"
  gh release download "$tag" -R "$RELEASES_REPO" -p perf.json -D "$win" 2>/dev/null || true
  python3 scripts/changelog.py add-platform "$win/latest.json" windows-x86_64 \
    "https://github.com/$RELEASES_REPO/releases/download/$tag/Church-Slides-windows-setup.exe" \
    "$setup.sig"
  (cd "$win" && grep -v windows-setup SHA256SUMS.txt > sums.tmp; shasum -a 256 ./Church-Slides-windows-setup.exe >> sums.tmp; mv sums.tmp SHA256SUMS.txt)
  perf_files=()
  if compgen -G "$win/perf-*.json" >/dev/null; then
    python3 scripts/perf_report.py merge "$win/perf.json" "$win"/perf-*.json
    perf_files=("$win/perf.json")
    cat "$win"/perf-*.md
  fi
  gh release upload "$tag" -R "$RELEASES_REPO" --clobber \
    "$setup" "$win/latest.json" "$win/SHA256SUMS.txt" ${perf_files[@]+"${perf_files[@]}"}

  build_site "$(current_version)"
  deploy_site "Windows installer for $tag"
  say "Added the Windows installer to $tag"
  exit 0
fi

# --- Preconditions -----------------------------------------------------------
[[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]] \
  || die "needs an Apple-silicon Mac (Intel is built under Rosetta)"

# The build's performance run measures this Mac, and in Low Power Mode it
# measures the power setting instead: on 2026-10-14 the engine fell below
# real time and the matcher's p95 went from 8 ms to 900 ms (code from 0.0.7
# failed the same way), failing the release after a quarter of an hour.
if pmset -g 2>/dev/null | grep -Eq '^[[:space:]]*lowpowermode[[:space:]]+1'; then
  die "Low Power Mode is on, which slows the build's performance run several-fold. Turn it off (System Settings → Battery), plug in, and push again."
fi

# The updater bundles are signed with this; tauri build fails without it.
[[ -f "$UPDATER_KEY" ]] \
  || die "no update-signing key at $UPDATER_KEY (restore it from your backup; a new key would strand every installed copy)"
TAURI_SIGNING_PRIVATE_KEY="$(cat "$UPDATER_KEY")"
export TAURI_SIGNING_PRIVATE_KEY TAURI_SIGNING_PRIVATE_KEY_PASSWORD=""

if [[ "$mode" == "--build-only" ]]; then
  version="$(current_version)"
  tag="v$version"
else
  command -v gh >/dev/null || die "needs the GitHub CLI (brew install gh)"
  gh auth status >/dev/null 2>&1 || die "run: gh auth login"

  [[ -z "$(git status --porcelain)" ]] \
    || die "the working tree has uncommitted changes; commit or stash them first"

  tag="$(git describe --exact-match --tags --match 'v*' HEAD 2>/dev/null)" \
    || die "HEAD has no v* tag. Make one with: scripts/bump_version.sh X.Y.Z"
  version="${tag#v}"
  [[ "$(current_version)" == "$version" ]] \
    || die "tag $tag doesn't match tauri.conf.json version $(current_version)"
  sha="$(git rev-parse HEAD)"
  python3 scripts/changelog.py has "$version" \
    || die "CHANGELOG.md has no entry for $version (bump_version.sh names the Unreleased section)"
  # A major release (X.Y.0) needs the privacy and terms pages reviewed for it
  # (docs/RELEASING.md, "Privacy and terms").
  if [[ "$version" == *.0 ]]; then
    for page in site/privacy.html site/terms.html; do
      reviewed="$(sed -n 's/.*<meta name="reviewed-for" content="\([^"]*\)".*/\1/p' "$page")"
      [[ "$reviewed" == "${version%.*}" ]] \
        || die "$page was reviewed for ${reviewed:-no version}, not ${version%.*}: review it (docs/RELEASING.md) and set its reviewed-for"
    done
  fi

  # Already published from this commit? Then a re-push (say, after the page
  # deploy failed and refused the push) only redoes the page.
  if gh release view "$tag" -R "$RELEASES_REPO" --json body -q .body 2>/dev/null \
      | grep -q "$sha"; then
    say "$tag installers are already published from ${sha:0:9}; redeploying the page only"
    build_site "$version"
    deploy_site "Release $tag (${sha:0:9})"
    exit 0
  fi
fi

# --- Tests -------------------------------------------------------------------
if [[ -z "${SKIP_TESTS:-}" ]]; then
  say "Running tests"
  pnpm test
  (cd sidecar && uv run pytest -q -x)
fi

# --- Build -------------------------------------------------------------------
# PyInstaller can't cross-compile, but Intel runs under Rosetta. Each arch
# restages src-tauri/resources/sidecar, so the two builds must run in order.
#
# Both the engine and the app are signed with $SIGNING_IDENTITY. Ad hoc ("-")
# isn't trusted by Gatekeeper, but it's a valid seal, so a downloaded copy gets
# "Open Anyway" in Privacy & Security. An unsigned or half-signed bundle gets
# "is damaged and can't be opened" instead, with no way past it.
export APPLE_SIGNING_IDENTITY="$SIGNING_IDENTITY"
rm -rf "$OUT" && mkdir -p "$OUT"
build_arch() {
  local arch="$1" triple="$2" label="$3"
  say "Building for $label ($triple)"
  uv run --project sidecar python scripts/build_sidecar.py --arch "$arch" --sign "$SIGNING_IDENTITY"
  pnpm tauri build --target "$triple" --config src-tauri/tauri.sidecar.conf.json \
    --config '{"bundle":{"createUpdaterArtifacts":true}}'

  local bundle="src-tauri/target/$triple/release/bundle"
  local app="$bundle/macos/Church Slides.app"
  codesign --verify --deep --strict "$app" \
    || die "$label app fails its code-signature check"
  # Written by scripts/third_party_notices.py during `tauri build` (its
  # beforeBuildCommand), from the engine build_sidecar.py just recorded.
  grep -q "with the AI engine ($arch)" "$app/Contents/Resources/THIRD-PARTY-NOTICES.txt" \
    || die "$label app's THIRD-PARTY-NOTICES.txt is missing or doesn't cover its AI engine"
  local dmg; dmg="$(ls "$bundle"/dmg/*_"$version"_*.dmg 2>/dev/null | head -1)"
  [[ -n "$dmg" ]] || die "no $version DMG in $bundle/dmg"
  cp "$dmg" "$OUT/Church-Slides-$label.dmg"
  # What the in-app updater downloads: the .app, tarred, with its signature.
  local update="$bundle/macos/Church Slides.app.tar.gz"
  [[ -f "$update" && -f "$update.sig" ]] || die "no signed updater bundle in $bundle/macos"
  cp "$update" "$OUT/Church-Slides-$label.app.tar.gz"
  cp "$update.sig" "$OUT/sigs/$label.sig"
  # The built .app has served its purpose (the DMG and the updater archive
  # carry it). Left in target/, Spotlight and Launchpad list it as another
  # identical "Church Slides", easy to open by mistake instead of the
  # installed one (2026-11-24: the Intel build, opened on an M1). Try a build
  # from build/release's DMG instead.
  "$LSREGISTER" -u "$app" 2>/dev/null || true
  rm -rf "$app"
}
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister

# Run the engine that was just frozen on the fixture service and report CPU,
# memory, timings and slide accuracy against what's expected for this Mac
# (scripts/perf_report.py). Only an extreme outlier or a crash stops the build.
perf_arch() {
  [[ -n "${SKIP_PERF:-}" ]] && return 0
  local label="$1"; shift
  say "Profiling the $label engine"
  uv run --project sidecar python scripts/perf_report.py --label "$label" \
    --engine src-tauri/resources/sidecar/auto-presenter-sidecar --out "$PERF" "$@" \
    || die "the $label engine's performance run failed (report: $PERF/perf-$label.md)"
}
# The comprehensive suite (FULL_PERF=1), on the Apple silicon engine just
# frozen, before the Intel build replaces it: every engine scenario, then the
# perf app built from the same engine and every app scenario. Results land in
# build/perf-suite/, each compared with the previous run.
full_perf() {
  [[ -n "${FULL_PERF:-}" ]] || return 0
  say "Comprehensive performance suite (FULL_PERF)"
  uv run --project sidecar python scripts/perf_suite.py run engine --size full \
    --engine src-tauri/resources/sidecar/auto-presenter-sidecar \
    || echo "warning: an engine scenario failed (see above)"
  if scripts/build_perf_app.sh >"$PERF/perf-app-build.log" 2>&1; then
    uv run --project sidecar python scripts/perf_suite.py run app --size full \
      || echo "warning: an app scenario failed (see above)"
  else
    echo "warning: the perf app didn't build ($PERF/perf-app-build.log)"
  fi
}
rm -rf "$PERF"
mkdir -p "$OUT/sigs"
build_arch aarch64 aarch64-apple-darwin apple-silicon
perf_arch macos-apple-silicon
full_perf
build_arch x86_64 x86_64-apple-darwin intel
# Under Rosetta, which says little about a real Intel Mac's speed: a short
# smoke run for crashes and leaks, not a benchmark.
perf_arch macos-intel-rosetta --quick
(cd "$OUT" && shasum -a 256 ./*.dmg ./*.app.tar.gz > SHA256SUMS.txt)

# The update manifest. Installed apps fetch releases/latest/download/latest.json
# and offer the update when its version is newer than theirs.
python3 scripts/changelog.py manifest "$OUT" "$version" \
  "https://github.com/$RELEASES_REPO/releases/download/$tag"
rm -r "$OUT/sigs"

if compgen -G "$PERF/perf-*.json" >/dev/null; then
  python3 scripts/perf_report.py merge "$OUT/perf.json" "$PERF"/perf-*.json
  say "Performance report ($PERF)"
  cat "$PERF"/perf-*.md
fi

if [[ "$mode" == "--build-only" ]]; then
  say "Built $version into $OUT (not published)"
  ls -lh "$OUT"
  exit 0
fi

# --- Publish -----------------------------------------------------------------
say "Publishing $tag to $RELEASES_REPO"
notes="Church Slides $version

$(python3 scripts/changelog.py notes "$version")

- Apple silicon (M1 or newer): Church-Slides-apple-silicon.dmg
- Intel: Church-Slides-intel.dmg
- Windows (experimental, added a little later): Church-Slides-windows-setup.exe

The .app.tar.gz files and latest.json are for the app's own updater;
perf.json is the build's engine performance report.

Built from ${sha}"
if gh release view "$tag" -R "$RELEASES_REPO" >/dev/null 2>&1; then
  gh release upload "$tag" -R "$RELEASES_REPO" --clobber "$OUT"/*
  gh release edit "$tag" -R "$RELEASES_REPO" --notes "$notes" --latest
else
  gh release create "$tag" -R "$RELEASES_REPO" --title "Church Slides $version" \
    --notes "$notes" --latest "$OUT"/*
fi

build_site "$version"
deploy_site "Release $tag (${sha:0:9})"

say "Released $tag"
echo "  page:       $SITE_URL"
echo "  installers: https://github.com/$RELEASES_REPO/releases/tag/$tag"
