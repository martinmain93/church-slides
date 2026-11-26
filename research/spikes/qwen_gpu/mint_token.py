"""Mint a speech token for the prototype (`modal_app.py`), as our server would.

    SPEECH_TOKEN_SECRET=... python mint_token.py [hours]
"""

import base64
import hashlib
import hmac
import json
import os
import sys
import time


def mint(secret: str, seconds: float) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": int(time.time() + seconds)}).encode()).rstrip(b"=").decode()
    sig = base64.urlsafe_b64encode(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
    return f"{payload}.{sig}"


if __name__ == "__main__":
    print(mint(os.environ["SPEECH_TOKEN_SECRET"], float(sys.argv[1] if len(sys.argv) > 1 else 4) * 3600))
