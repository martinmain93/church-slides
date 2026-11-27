"""Speech from a streaming engine on a server (docs/roadmap/HOSTED_SPEECH.md, A).

The server runs the same streaming Qwen3-ASR engine as Apple silicon
(`asr.qwen_backend.QwenStreamer`) on a rented GPU; this end sends the room's
audio over one websocket and hands the words that come back to the session,
as `QwenStreamer` would. The server and its wire protocol: speech-server/
(README.md).

Nothing here waits on the network in the audio path: audio goes out through
a sender thread, words come in on a receiver thread, and the session's
thread only queues frames and drains words. A dropped connection is retried
with backoff; while it's down, no words arrive (the fallback to the
computer's own engine, `fallback`, listens until the server answers again:
the same ladder as `hosted_backend` — a few seconds of silent retrying, then
the fallback, with backoff on the reconnects. That also covers the minute a
cold GPU takes to wake.
"""

from __future__ import annotations

import json
import logging
import queue
import random
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

import numpy as np

from auto_presenter.asr.types import TranscriptEvent, Word
from auto_presenter.audio.types import AudioFrame

log = logging.getLogger(__name__)

#: A pause longer than this starts a new stream (`qwen_backend.GAP_RESET_SECONDS`).
GAP_RESET_SECONDS = 0.5
#: Audio kept for the follower's side passes (`qwen_backend.SIDE_WINDOW_SECONDS`).
SIDE_WINDOW_SECONDS = 6.0
#: How long the server may take to wake (a cold GPU) before a connect fails.
CONNECT_TIMEOUT_S = 120.0
MAX_BACKOFF_S = 30.0
#: Seconds without the server before the computer's own engine takes over.
RETRY_S = 3.0


class RemoteStreamingBackend:
    """`ASRBackend` whose words come from a speech server's websocket."""

    def __init__(
        self, url: str, token: Callable[[], str | None], *, whisper=None, fallback=None
    ) -> None:
        self.url = url.rstrip("/")
        self._token = token
        #: The computer's own windowed engine (Whisper on the processor), used
        #: while the server can't be reached.
        self.fallback = fallback
        #: The session's own Whisper, for the follower (as `QwenStreamingBackend.whisper`).
        self.whisper = whisper
        self.ready_info: dict[str, Any] = {}
        #: The next-line check's phones, when the follower runs it on the
        #: server: `(t_end, logits[T, P], server_ms)` per tick, on the
        #: receiver thread (`next_line.RemotePhones.push` queues them).
        self.on_phones: Callable[..., None] | None = None

    def name(self) -> str:
        return "remote-qwen3-asr"

    def connect(self):
        from websockets.sync.client import connect

        token = self._token()
        if not token:
            raise ConnectionError("no speech token")
        ws = connect(
            f"{self.url}/stream?token={token}",
            open_timeout=CONNECT_TIMEOUT_S,
            max_size=2**22,
            compression=None,
        )
        ready = json.loads(ws.recv(timeout=CONNECT_TIMEOUT_S))
        if ready.get("type") != "ready":
            ws.close()
            raise ConnectionError(f"unexpected first message {ready!r}")
        self.ready_info = ready
        return ws

    def warm(self) -> None:
        """Start waking the server (a cold GPU takes a minute or more) without
        waiting for it, and warm the computer's own engines."""

        def wake() -> None:
            t = time.monotonic()
            try:
                self.connect().close()
                took = time.monotonic() - t
                log.info("speech server ready in %.1f s (%s)", took, self.ready_info)
            except Exception as exc:
                log.warning("speech server didn't wake: %s", exc)

        threading.Thread(target=wake, daemon=True).start()
        for engine in (self.fallback, self.whisper):
            if engine is not None and hasattr(engine, "warm"):
                engine.warm()

    def streamer(self, **kwargs: Any) -> RemoteStreamer:
        return RemoteStreamer(self, **kwargs)

    def transcribe(self, frames: Iterable[AudioFrame]) -> Iterator[TranscriptEvent]:
        return (e for e in self.streamer().transcribe(frames) if e.is_final)


class _Stream:
    """One websocket, opened and fed by its own threads.

    Connecting can take a minute (a cold GPU), so it happens off the audio
    path: frames wait meanwhile, only the newest `PENDING_S` of them, and
    the stream starts at the oldest one kept.
    """

    PENDING_S = 4.0

    def __init__(self, backend: RemoteStreamingBackend, words: queue.Queue) -> None:
        self.backend = backend
        self.words = words
        self.closed = threading.Event()
        self.connected = threading.Event()
        self._lock = threading.Lock()
        self._pending: list[AudioFrame] = []
        self._out: queue.Queue = queue.Queue()
        self.ws: Any = None
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            self.ws = self.backend.connect()
        except Exception as exc:
            log.warning("speech server unreachable: %s", exc)
            self.closed.set()
            return
        with self._lock:
            pending, self._pending = self._pending, []
            self.connected.set()
        try:
            t0 = pending[0].t_start if pending else None
            self.ws.send(
                json.dumps(
                    {
                        "type": "start",
                        "t0": t0 if t0 is not None else 0.0,
                        "phones": self.backend.on_phones is not None,
                    }
                )
            )
            for f in pending:
                self.ws.send(_pcm(f))
            threading.Thread(target=self._receiver, daemon=True).start()
            while (item := self._out.get()) is not None:
                self.ws.send(item)
            self.ws.send(json.dumps({"type": "end"}))
        except Exception as exc:
            log.warning("speech stream send failed: %s", exc)
            self.closed.set()

    def _receiver(self) -> None:
        try:
            for raw in self.ws:
                m = json.loads(raw)
                if m.get("type") == "words":
                    self.words.put(m)
                elif m.get("type") == "phones" and (on := self.backend.on_phones) is not None:
                    on(float(m["t_end"]), _logits(m), m.get("ms"))
        except Exception as exc:
            log.warning("speech stream closed: %s", exc)
        finally:
            self.closed.set()

    def send(self, frame: AudioFrame) -> None:
        with self._lock:
            if not self.connected.is_set():
                self._pending.append(frame)
                while self._pending and frame.t_end - self._pending[0].t_start > self.PENDING_S:
                    self._pending.pop(0)
                return
        self._out.put(_pcm(frame))

    def finish(self, timeout: float = 10.0) -> None:
        """Flush the last words (waits up to `timeout`), then close."""
        self._out.put(None)
        if self.connected.is_set():
            self.closed.wait(timeout)
        try:
            if self.ws is not None:
                self.ws.close()
        except Exception:
            pass
        self.closed.set()


def _logits(m: dict) -> np.ndarray:
    """A "phones" message's logits: base64 float16, `shape` [frames, phones]."""
    import base64

    raw = np.frombuffer(base64.b64decode(m["logits"]), dtype="<f2")
    return raw.reshape(m["shape"]).astype(np.float32)


def _pcm(frame: AudioFrame) -> bytes:
    return (np.clip(frame.samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


class _Local:
    """The computer's own windowed engine on its own thread, fed frame by frame."""

    def __init__(self, backend, words: queue.Queue, pass_timer) -> None:
        from auto_presenter.asr.streaming import SlidingWindowWrapper

        self._frames: queue.Queue = queue.Queue()
        hooks = {"pass_timer": pass_timer} if pass_timer is not None else {}
        self._wrapper = SlidingWindowWrapper(
            backend, emission_observer=lambda ev, at: words.put((ev, at)), **hooks
        )
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        def frames():
            while (f := self._frames.get()) is not None:
                yield f

        try:
            for _ in self._wrapper.transcribe(frames()):
                pass  # words reach the session through the emission observer
        except Exception:
            log.exception("the computer's own engine stopped")

    def send(self, frame: AudioFrame) -> None:
        self._frames.put(frame)

    def stop(self, wait: bool = False) -> None:
        self._frames.put(None)
        if wait:
            self._thread.join(10.0)


@dataclass
class RemoteStreamer:
    """Stands where `QwenStreamer` stands in `AudioSession`, with its hooks."""

    backend: RemoteStreamingBackend
    emission_observer: Callable[[TranscriptEvent, float], None] | None = None
    pass_timer: Callable[[], AbstractContextManager[object]] | None = None
    side_pass: Callable[[list[AudioFrame], float], None] | None = None
    side_pass_seconds: float = 2.0

    def name(self) -> str:
        return f"streaming({self.backend.name()})"

    #: True while the computer's own engine is listening in the server's place.
    falling_back: bool = False

    def transcribe(self, frames: Iterable[AudioFrame]) -> Iterator[TranscriptEvent]:
        words: queue.Queue = queue.Queue()
        stream: _Stream | None = None
        down_since: float | None = None
        local: _Local | None = None
        next_try = 0.0
        backoff = 0.0
        side: list[AudioFrame] = []
        last_side_t = float("-inf")
        expected_t: float | None = None
        latest_t = 0.0
        for frame in frames:
            if expected_t is not None and frame.t_start - expected_t > GAP_RESET_SECONDS and stream:
                stream.finish(timeout=0)  # paused and resumed: a new stream, as on the Mac
                stream = None
                yield from self._drain(words, latest_t)
            expected_t = latest_t = frame.t_end
            side.append(frame)
            while side and frame.t_end - side[0].t_start > SIDE_WINDOW_SECONDS:
                side.pop(0)

            if stream is not None and stream.closed.is_set():
                # never connected, or dropped: try again with backoff
                was_up = stream.connected.is_set()
                stream = None
                backoff = 0.0 if was_up else min(MAX_BACKOFF_S, max(2.0, backoff * 2))
                next_try = time.monotonic() + backoff * random.uniform(0.8, 1.2)
            if stream is None and time.monotonic() >= next_try:
                stream = _Stream(self.backend, words)
            if stream is not None:
                stream.send(frame)
            now = time.monotonic()
            if stream is not None and stream.connected.is_set() and not stream.closed.is_set():
                down_since = None
                if local is not None:
                    log.info("speech server answering; the computer's own engine stops")
                    local.stop()
                    local = None
            else:
                down_since = now if down_since is None else down_since
                fallback = self.backend.fallback
                if local is None and fallback is not None and now - down_since >= RETRY_S:
                    log.warning("speech server unavailable; the computer's own engine takes over")
                    local = _Local(fallback, words, self.pass_timer)
            self.falling_back = local is not None
            if local is not None:
                local.send(frame)

            if self.side_pass is not None and frame.t_end - last_side_t >= self.side_pass_seconds:
                last_side_t = frame.t_end
                try:
                    self.side_pass(side, frame.t_end)
                except Exception:
                    log.exception("side pass failed; transcription carries on")
            yield from self._drain(words, latest_t)
        if stream is not None:
            stream.finish()
        if local is not None:
            local.stop(wait=True)
        yield from self._drain(words, latest_t)

    def _drain(self, words: queue.Queue, now_t: float) -> Iterator[TranscriptEvent]:
        while True:
            try:
                m = words.get_nowait()
            except queue.Empty:
                return
            if isinstance(m, tuple):  # from the computer's own engine
                event, available_at = m
                if self.emission_observer is not None:
                    self.emission_observer(event, available_at)
                yield event
                continue
            out = [Word(text=w[0], t_start=w[1], t_end=w[2], confidence=w[3]) for w in m["words"]]
            if not out:
                continue
            event = TranscriptEvent(
                words=out,
                is_final=m["final"],
                segment_id=m["segment_id"],
                ends_segment=m["ends_segment"],
            )
            if self.emission_observer is not None:
                # when this end could first act: the audio clock as it arrived
                self.emission_observer(event, now_t)
            yield event
