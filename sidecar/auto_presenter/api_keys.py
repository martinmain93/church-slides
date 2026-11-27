"""API keys for hosted providers, shared across features.

Deprecated (2026-11-23): Groq's free tier can't carry a church's use and
paid tiers aren't available, so Groq integrations are on their way out.

Originally written for hosted spoken-instruction models
(`decision.instructions`); a provider's key is just an account credential,
not a feature credential, so the same "groq" entry a user saves here also
covers `asr.groq_backend`'s hosted Whisper calls — one key, two consumers,
no extra setup.

Stored as plaintext JSON in the app data directory — the same trust
boundary as `library.db` and every recording next to it, not OS-keychain
hardened secret storage. That's a real limitation, not an oversight: this
app has no backend and no user accounts, so there's no server-side vault to
put a key in, and the alternative (env vars only) doesn't support "type a key into the UI," which is what
downloadable-and-switchable hosted models need. An env var
(`AUTO_PRESENTER_{PROVIDER}_API_KEY`) is checked first and always wins, so a
developer or a more security-conscious operator can avoid this file
entirely.

Whoever can read this app's data directory can read these keys. On a
shared or multi-user machine that matters; document it, don't pretend
otherwise.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from auto_presenter.config import app_data_dir

log = logging.getLogger(__name__)

_FILENAME = "instruction_api_keys.json"


#: Where to find the key store instead (the performance suite points it at an
#: empty folder so a run never calls a hosted service unless asked).
KEYS_FILE_ENV = "AUTO_PRESENTER_API_KEYS_FILE"


def _path() -> Path:
    override = os.environ.get(KEYS_FILE_ENV)
    return Path(override) if override else app_data_dir() / _FILENAME


def load_keys() -> dict[str, str]:
    """provider -> key, for whatever has been saved via `save_key`."""
    p = _path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
        return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("could not read %s: %s", p, exc)
        return {}


def save_key(provider: str, api_key: str) -> None:
    """Write the key store, readable only by this user.

    These are account credentials in plaintext. The app data directory is
    already per-user on every platform we ship to, so this is defence in
    depth rather than a fix for a live exposure — but `0o600` costs nothing
    and narrows a shared or backed-up machine.

    `chmod` after writing rather than an `opener`: `write_text` creates the
    file if it does not exist, and on an *existing* file only the chmod
    tightens permissions that a previous version left at the umask default.
    """
    keys = load_keys()
    if api_key:
        keys[provider] = api_key
    else:
        keys.pop(provider, None)
    path = _path()
    path.write_text(json.dumps(keys, indent=2))
    try:
        path.chmod(0o600)
    except OSError as exc:  # pragma: no cover - platform dependent
        # Windows and some network filesystems have no usable POSIX mode.
        # Failing to tighten permissions must not lose the key.
        log.debug("could not restrict permissions on %s: %s", path, exc)


def get_key(provider: str) -> str | None:
    """Env var wins if set; otherwise whatever's on disk for this provider."""
    env_key = os.environ.get(f"AUTO_PRESENTER_{provider.upper()}_API_KEY")
    if env_key:
        return env_key
    return load_keys().get(provider)


#: Where the app keeps the key for its organization's AI allowance, when
#: signed in to one (`src-tauri/src/hosted_ai.rs`). The app passes the path,
#: since its folder depends on its identifier.
HOSTED_AI_FILE_ENV = "CHURCH_SLIDES_HOSTED_AI_FILE"

#: Providers the server's AI gateway stands in for.
_GATEWAY_PROVIDERS = {"groq"}


def hosted_gateway() -> tuple[str, str] | None:
    """(base_url, key) of the organization's AI gateway, while it's valid."""
    path = os.environ.get(HOSTED_AI_FILE_ENV)
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    base, key, expires = data.get("base_url"), data.get("key"), data.get("expires_at")
    if not (isinstance(base, str) and isinstance(key, str) and isinstance(expires, (int, float))):
        return None
    if expires <= time.time() * 1000:
        return None
    return base.rstrip("/"), key


def hosted_speech() -> tuple[str, str] | None:
    """(url, token) for the organization's live speech server, while the
    token is valid: the AI key answer's `speech`, kept beside the gateway key
    (`src-tauri/src/hosted_ai.rs`). None when the plan has no speech, or this
    computer isn't signed in. Read fresh each time: the app renews the token
    every few minutes."""
    path = os.environ.get(HOSTED_AI_FILE_ENV)
    if not path:
        return None
    try:
        speech = json.loads(Path(path).read_text()).get("speech")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(speech, dict):
        return None
    url, token, expires = speech.get("url"), speech.get("token"), speech.get("expires_at")
    if not (isinstance(url, str) and isinstance(token, str) and isinstance(expires, (int, float))):
        return None
    if expires <= time.time() * 1000:
        return None
    return url.rstrip("/"), token


def endpoint(provider: str, own_base_url: str) -> tuple[str, str] | None:
    """Where to send a hosted call: (base_url, key).

    The person's own key for the provider wins. Without one, the
    organization's AI allowance through the Church Slides server, when this
    computer is signed in to one. None when neither is available.
    """
    own = get_key(provider)
    if own:
        return own_base_url.rstrip("/"), own
    return gateway_for(provider)


def gateway_for(provider: str) -> tuple[str, str] | None:
    """The organization's AI gateway for `provider`, when it stands in for
    that provider and this computer has a valid key for it."""
    return hosted_gateway() if provider in _GATEWAY_PROVIDERS else None
