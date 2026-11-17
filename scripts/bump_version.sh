#!/usr/bin/env bash
# Set the app version everywhere it lives, commit, and tag it.
#
#   scripts/bump_version.sh 0.0.2
#   git push origin main v0.0.2     # the pre-push hook then releases it
set -euo pipefail

version="${1:?usage: scripts/bump_version.sh X.Y.Z}"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "not X.Y.Z: $version" >&2; exit 1; }

cd "$(dirname "${BASH_SOURCE[0]}")/.."
[[ -z "$(git status --porcelain)" ]] || { echo "commit your changes first" >&2; exit 1; }

python3 - "$version" <<'PY'
import json, re, sys
from pathlib import Path

v = sys.argv[1]
for path in ("package.json", "src-tauri/tauri.conf.json"):
    p = Path(path)
    text = p.read_text()
    text, n = re.subn(r'^(\s*"version":\s*)"[^"]*"', rf'\g<1>"{v}"', text, count=1, flags=re.M)
    assert n == 1, path
    p.write_text(text)
    json.loads(text)

cargo = Path("src-tauri/Cargo.toml")
text, n = re.subn(r'^version = "[^"]*"', f'version = "{v}"', cargo.read_text(), count=1, flags=re.M)
assert n == 1, cargo
cargo.write_text(text)
PY

# What's new: the Unreleased notes become this version's (see CHANGELOG.md).
python3 scripts/changelog.py release "$version"
python3 scripts/changelog.py has "$version" \
  || { echo "add a line about this release under '## Unreleased' in CHANGELOG.md first" >&2; git checkout -- CHANGELOG.md package.json src-tauri/tauri.conf.json src-tauri/Cargo.toml; exit 1; }

# Cargo.lock records the crate's own version too.
(cd src-tauri && cargo update --workspace --offline >/dev/null 2>&1 || cargo metadata --format-version 1 >/dev/null)

git add package.json src-tauri/tauri.conf.json src-tauri/Cargo.toml src-tauri/Cargo.lock CHANGELOG.md
git commit -m "Release $version"
git tag -a "v$version" -m "Church Slides $version"
echo "Tagged v$version. Release it with: git push origin main v$version"
