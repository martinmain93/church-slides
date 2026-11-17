#!/usr/bin/env bash
# Build the public repository: the committed tree at REF, minus what stays
# private, as a fresh repo with a single commit. Then scan it. Nothing is
# pushed; publishing is a separate, manual step.
#
#     scripts/public_release/export.sh [REF] [OUT]
#
# REF defaults to HEAD, OUT to build/public. The commit is authored with
# PUBLIC_AUTHOR_NAME / PUBLIC_AUTHOR_EMAIL (default: your git name and your
# GitHub noreply address, which you should set).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
REF="${1:-HEAD}"
OUT="${2:-$ROOT/build/public}"

# Kept out of the public tree. Everything else that's committed goes.
PRIVATE=(
  .claude                                    # personal tool config
  research/dead_ends                         # abandoned engines, superseded
  research/spikes/competitor_import/samples  # duplicates data/fixtures/other_apps
)

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

[[ -z "$(git status --porcelain)" ]] || echo "note: uncommitted changes aren't exported (exporting $REF)"
[[ ! -e "$OUT" ]] || die "$OUT exists; remove it first"

say "Exporting $REF to $OUT"
mkdir -p "$OUT"
git archive --format=tar "$REF" | tar -x -C "$OUT"
for p in "${PRIVATE[@]}"; do rm -rf "${OUT:?}/$p"; done

say "Scanning"
fail=0
# This computer's home folder, in any file (text or binary).
if grep -rlF --exclude-dir=.git "/Users/$(id -un)/" "$OUT"; then
  echo "^ files with this computer's home-folder path"; fail=1
fi
if grep -rIlE --exclude-dir=.git -e 'gsk_[A-Za-z0-9]{20,}' -e 'BEGIN [A-Z ]*PRIVATE KEY' \
     -e 'ghp_[A-Za-z0-9]{30,}' -e 'sk-[A-Za-z0-9]{32,}' "$OUT"; then
  echo "^ secrets"; fail=1
fi
find "$OUT" -type f \( -name '*.local.env' -o -name '*.key' -o -name '.env' -o -name '*.pem' \) | grep . && { echo "^ secret-looking files"; fail=1; }
if command -v gitleaks >/dev/null; then
  gitleaks dir "$OUT" --no-banner || fail=1
else
  echo "gitleaks not installed (brew install gitleaks): skipped its scan"
fi
python3 "$ROOT/scripts/public_release/scan_lyrics.py" "$OUT" || fail=1

say "Committing"
git -C "$OUT" init -q -b main
git -C "$OUT" add -A
name="${PUBLIC_AUTHOR_NAME:-$(git config user.name)}"
email="${PUBLIC_AUTHOR_EMAIL:-}"
[[ -n "$email" ]] || die "set PUBLIC_AUTHOR_EMAIL (your GitHub noreply address) and run again"
GIT_AUTHOR_NAME="$name" GIT_AUTHOR_EMAIL="$email" GIT_COMMITTER_NAME="$name" GIT_COMMITTER_EMAIL="$email" \
  git -C "$OUT" commit -q -m "Church Slides, open source" -s
du -sh "$OUT/.git" | awk '{print "repo size: " $1}'
(( fail == 0 )) || die "scans found something; read the output above before publishing"
say "Ready in $OUT. Review it, then push it to the new public repo."
