"""Shared plumbing for this app's few hand-rolled `urllib` HTTP clients.

Three call sites (`decision.instructions.GenericLLMBackend`,
`decision.ollama_manager`, `asr.groq_backend`) each built their own thin
wrapper around `urllib.request`, and each independently rediscovered the
same real bug: Cloudflare, sitting in front of Groq's API, blocks
`urllib`'s default User-Agent (`Python-urllib/3.x`) as a bot signature —
a plain 403 with no auth-related message, found testing Groq with a live
key. One place for the header (and the request-building it sits in) means
that fix, and the next one like it, only has to land once.

Deliberately low-level: this returns the open `urlopen` response, not a
parsed body. `GenericLLMBackend`/`GroqWhisperBackend` want the whole
response read and parsed as one JSON object; `ollama_manager.pull()` wants
to iterate the same response line-by-line as it streams. Forcing a single
"read and parse" shape here would have meant `pull()` reimplementing this
module's header logic on the side anyway — the exact duplication this
exists to remove.

None of these functions catch anything. Every call site already had its
own idea of what "the request failed" should mean for it — `decide()`
returns `None`, `is_available()` returns `False`, `pull()` raises after
emitting a `failed` progress event — and centralizing the exception
handling here would have flattened that back out. Catch `REQUEST_ERRORS`
at the call site.

That constant exists because naming the exceptions individually got it
wrong twice. This docstring used to prescribe `URLError`, `TimeoutError`,
`OSError`, and both `GenericLLMBackend.decide` and `GroqWhisperBackend`
copied exactly that — which omits `json.JSONDecodeError`, a `ValueError`
and so outside all three. A body that is not JSON (a proxy's HTML error
page, a truncated response, an empty one) therefore escaped a `decide()`
whose own docstring promised it never raises, and the exception unwound
into the transcript handler and took a real slide change with it. Only
`ollama_manager` had listed it. One name for the set means the next
consumer inherits the complete answer.
"""

from __future__ import annotations

import functools
import json
import ssl
import urllib.error
import urllib.request
import uuid
from typing import Any

#: See the module docstring. Harmless to send to a local server (ollama)
#: that has no reason to care.
USER_AGENT = "church-slides-sidecar/1.0"

#: Everything a request through this module can fail with, including reading
#: the body back. `urllib.error.HTTPError` is a `URLError` is an `OSError`, so
#: the network cases collapse; `json.JSONDecodeError` is a `ValueError` and
#: does not, which is exactly how it was missed. Catch this, not a hand-written
#: subset.
REQUEST_ERRORS = (
    urllib.error.URLError,
    TimeoutError,
    OSError,
    json.JSONDecodeError,
)


@functools.cache
def _ssl_context() -> ssl.SSLContext:
    """Verify against `certifi`'s CA bundle rather than the interpreter's.

    A frozen (PyInstaller) sidecar carries the build machine's idea of where
    the system CA file lives, which on another machine is often nowhere — every
    HTTPS call then fails verification. Local plain-HTTP servers (ollama) never
    consult this.
    """
    import certifi

    return ssl.create_default_context(cafile=certifi.where())


def _urlopen(req: urllib.request.Request, timeout_seconds: float | None):
    return urllib.request.urlopen(req, timeout=timeout_seconds, context=_ssl_context())


def _headers(
    *, content_type: str | None = None, api_key: str | None = None
) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT}
    if content_type:
        headers["Content-Type"] = content_type
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def open_get(url: str, *, timeout_seconds: float | None = 5.0):
    """Open a GET request. Returns the `urlopen` response (a context manager)."""
    req = urllib.request.Request(url, headers=_headers())
    return _urlopen(req, timeout_seconds)


def open_json_post(
    url: str,
    payload: dict[str, Any],
    *,
    api_key: str | None = None,
    timeout_seconds: float | None = 8.0,
):
    """Open a POST request with a JSON body. Returns the `urlopen` response."""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers=_headers(content_type="application/json", api_key=api_key),
    )
    return _urlopen(req, timeout_seconds)


def open_multipart_post(
    url: str,
    fields: dict[str, str],
    *,
    file_field: str,
    filename: str,
    file_content_type: str,
    file_bytes: bytes,
    api_key: str | None = None,
    timeout_seconds: float | None = 8.0,
):
    """Open a POST request with a `multipart/form-data` body (plain string
    fields plus one file). Returns the `urlopen` response."""
    body, content_type = _multipart_body(
        fields,
        file_field=file_field,
        filename=filename,
        file_content_type=file_content_type,
        file_bytes=file_bytes,
    )
    req = urllib.request.Request(
        url,
        data=body,
        headers=_headers(content_type=content_type, api_key=api_key),
        method="POST",
    )
    return _urlopen(req, timeout_seconds)


def read_json(resp: Any) -> dict[str, Any]:
    """Read and parse a whole response body as JSON."""
    return json.loads(resp.read())


def _multipart_body(
    fields: dict[str, str],
    *,
    file_field: str,
    filename: str,
    file_content_type: str,
    file_bytes: bytes,
) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
        f'filename="{filename}"\r\nContent-Type: {file_content_type}\r\n\r\n'.encode()
    )
    parts.append(file_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"
