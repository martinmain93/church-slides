"""Speech tokens: `<payload>.<signature>`, both base64url without padding.

The payload is JSON, `{"exp": <unix seconds>, "org": "<organization id>"}`
(`org` is optional). The signature is HMAC-SHA256 of the payload's base64
text with the secret the Church Slides server and the speech server share
(`SPEECH_TOKEN_SECRET`). The Church Slides server mints them beside the AI
key (`POST /v1/orgs/{orgId}/ai/key`); `mint` here is the same thing for tests
and for running the speech server by hand.

    SPEECH_TOKEN_SECRET=... python tokens.py [hours] [org]
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _sign(secret: str, payload: str) -> str:
    return _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())


def mint(secret: str, seconds: float, org: str | None = None, *, now: float | None = None) -> str:
    claims: dict = {"exp": int((time.time() if now is None else now) + seconds)}
    if org is not None:
        claims["org"] = org
    payload = _b64(json.dumps(claims, separators=(",", ":")).encode())
    return f"{payload}.{_sign(secret, payload)}"


def verify(secret: str, token: str, *, now: float | None = None) -> dict | None:
    """The token's claims, or None if it's malformed, forged or expired."""
    try:
        payload, sig = token.split(".")
        if not hmac.compare_digest(sig, _sign(secret, payload)):
            return None
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if float(claims["exp"]) <= (time.time() if now is None else now):
            return None
        return claims
    except Exception:
        return None


if __name__ == "__main__":
    import os
    import sys

    hours = float(sys.argv[1]) if len(sys.argv) > 1 else 4
    print(
        mint(
            os.environ["SPEECH_TOKEN_SECRET"],
            hours * 3600,
            sys.argv[2] if len(sys.argv) > 2 else None,
        )
    )
