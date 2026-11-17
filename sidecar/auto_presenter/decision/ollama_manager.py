"""Whether ollama is reachable, what's installed, and pulling what isn't.

Mirrors `asr.model_manager`'s shape for the same reason: the UI needs a
progress bar for a multi-gigabyte download, and that needs structured
events, not a blocking call. Ollama's own `/api/pull` already streams
exactly that as newline-delimited JSON — no need to reimplement a transfer
protocol the way Whisper's loader made necessary.

Unlike Whisper, ollama itself is not something this app can install. It is
a separate system-level program (`brew install ollama` / the equivalent
installer on Windows and Linux) that the app can detect and drive, but not
bundle or silently set up. `is_available()` existing and returning `False`
is the expected, common case on a machine nobody has configured yet — the
UI's job is to say so plainly, not to treat it as an error.
"""

from __future__ import annotations

import json
import logging
import urllib.error
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Literal

from auto_presenter.http_client import open_get, open_json_post, read_json

log = logging.getLogger(__name__)

DEFAULT_HOST = "http://localhost:11434"


@dataclass(frozen=True)
class PullProgressEvent:
    """One line of ollama's pull stream, translated to our shape."""

    model_id: str
    bytes_downloaded: int
    bytes_total: int
    state: Literal["starting", "downloading", "completed", "failed"]
    detail: str = ""

    @property
    def fraction(self) -> float:
        if self.bytes_total <= 0:
            return 0.0
        return min(1.0, self.bytes_downloaded / self.bytes_total)


def is_available(host: str = DEFAULT_HOST) -> bool:
    """Whether an ollama server is reachable at all. Cheap — a version
    check, not a model load."""
    try:
        with open_get(f"{host}/api/version", timeout_seconds=2.0):
            return True
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def installed_models(host: str = DEFAULT_HOST) -> set[str]:
    """Model tags ollama already has pulled. Empty set (not an error) if
    ollama isn't reachable — callers already have `is_available()` to tell
    those cases apart if they need to."""
    try:
        with open_get(f"{host}/api/tags", timeout_seconds=5.0) as resp:
            body = read_json(resp)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return set()
    return {m["name"] for m in body.get("models", [])}


def pull(
    pull_name: str,
    *,
    host: str = DEFAULT_HOST,
    progress_callback: Callable[[PullProgressEvent], None] | None = None,
) -> None:
    """Download a model, streaming progress through `progress_callback`.

    Blocking — callers wanting non-blocking behavior run this on a worker
    thread, same convention as `ModelManager.ensure`. Raises on failure
    (connection refused, unknown model tag, disk full) after emitting a
    `failed` event, so a caller that doesn't check return values still
    finds out.
    """
    emit = progress_callback or (lambda _e: None)
    emit(PullProgressEvent(pull_name, 0, 0, "starting", "starting download"))
    failed_detail: str | None = None
    try:
        with open_json_post(
            f"{host}/api/pull", {"model": pull_name, "stream": True}, timeout_seconds=None
        ) as resp:
            for event in _iter_pull_lines(pull_name, resp):
                emit(event)
                if event.state == "failed":
                    # `_iter_pull_lines` already emitted the "failed" event
                    # (it has the real detail message); just remember it
                    # happened so the "completed" emit below doesn't
                    # contradict it. Ollama's stream ends the response after
                    # an error line, so there's nothing left to iterate.
                    failed_detail = event.detail
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        emit(PullProgressEvent(pull_name, 0, 0, "failed", str(exc)))
        raise
    if failed_detail is not None:
        raise RuntimeError(f"ollama pull failed for {pull_name!r}: {failed_detail}")
    emit(PullProgressEvent(pull_name, 1, 1, "completed", "downloaded"))


def _iter_pull_lines(pull_name: str, resp) -> Iterator[PullProgressEvent]:
    """Ollama streams one JSON object per line: `{"status": "...",
    "total": N, "completed": N}` while a layer downloads, and bare
    `{"status": "..."}` for other lifecycle messages (verifying, writing
    manifest). Only lines carrying both `total` and `completed` map to a
    progress fraction; the rest are surfaced as `detail` on a
    zero-progress `downloading` event so the UI still shows *something*
    moving during the parts that aren't byte-countable.
    """
    for raw in resp:
        if not raw.strip():
            continue
        try:
            line = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if "error" in line:
            yield PullProgressEvent(pull_name, 0, 0, "failed", str(line["error"]))
            return
        total = line.get("total", 0)
        completed = line.get("completed", 0)
        yield PullProgressEvent(
            pull_name, completed, total,
            "downloading", line.get("status", ""),
        )
