#!/usr/bin/env bash
# Deploy the speech server (speech-server/) to a Modal environment.
#
#   scripts/deploy_speech.sh staging
#   scripts/deploy_speech.sh production     # needs a clean tree at a pushed commit
#
# Modal credentials come from ~/.modal.toml (`modal token new`) or
# MODAL_TOKEN_ID / MODAL_TOKEN_SECRET. Each environment needs, once:
#   modal environment create <env>
#   modal secret create --env <env> church-slides-speech SPEECH_TOKEN_SECRET=<random>
# with the same secret set on the Church Slides server for that environment.
set -euo pipefail

env="${1:-}"
case "$env" in
  staging|production) ;;
  *) echo "usage: $0 staging|production" >&2; exit 2 ;;
esac

cd "$(dirname "$0")/.."
build="$(git describe --always --dirty --abbrev=8)"
if [[ "$env" == production ]]; then
  if [[ "$build" == *-dirty ]]; then
    echo "production deploys need a clean tree (got $build)" >&2; exit 1
  fi
  if ! git branch -r --contains HEAD | grep -q .; then
    echo "production deploys need a pushed commit" >&2; exit 1
  fi
fi

echo "deploying speech server $build to $env"
SPEECH_ENV="$env" SPEECH_BUILD="$build" \
  uvx --from modal==1.6.4 modal deploy --env "$env" speech-server/app.py
url="$(uvx --from modal==1.6.4 python -c "
import modal
s = modal.Cls.from_name('church-slides-speech', 'Speech', environment_name='$env')
print(s().web.get_web_url())")"
[[ "$url" == https://* ]] || { echo "no web URL for $env" >&2; exit 1; }

echo "waking $url"
# A container of the previous build can stay warm and answer for a while
# after a deploy (Modal drains it), so wait for the new one.
for _ in $(seq 1 30); do
  health="$(curl -fsS -m 180 "$url/health" || true)"
  grep -q "\"build\":\"$build\"" <<<"$health" && break
  sleep 10
done
echo "$health"
grep -q "\"build\":\"$build\"" <<<"$health" || { echo "health still reports another build" >&2; exit 1; }
echo "speech server $build is live on $env: ${url/https:/wss:}"
