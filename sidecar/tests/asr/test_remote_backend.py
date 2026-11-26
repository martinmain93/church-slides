"""The speech-server client: words in, fallback while the server is away."""

import json
import queue
import threading
import time

import numpy as np

from auto_presenter.asr import remote_backend as rb
from auto_presenter.asr.remote_backend import RemoteStreamingBackend
from auto_presenter.asr.types import TranscriptEvent, Word
from auto_presenter.audio.types import AudioFrame


class FakeWS:
    """Answers each audio frame with one final word at that frame's time."""

    def __init__(self):
        self.inbox: queue.Queue = queue.Queue()
        self.t = 0.0

    def send(self, item):
        if isinstance(item, bytes):
            n = len(item) // 2 / 16000
            word = ["sung", self.t, self.t + n, 0.9]
            self.t += n
            self.inbox.put(json.dumps({"type": "words", "final": True, "segment_id": 0,
                                       "ends_segment": False, "words": [word]}))
        elif json.loads(item)["type"] == "start":
            self.t = json.loads(item)["t0"]
        else:
            self.inbox.put(None)

    def __iter__(self):
        while (m := self.inbox.get()) is not None:
            yield m

    def close(self):
        self.inbox.put(None)


class Local:
    """A windowed engine: one word per pass."""

    def name(self):
        return "local"

    def transcribe(self, frames):
        f = list(frames)[-1]
        return iter([TranscriptEvent(words=[Word(text="local", t_start=f.t_start, t_end=f.t_end,
                                                 confidence=1.0)], is_final=True)])


def frames(n, dt=0.1, sleep=0.01):
    for i in range(n):
        time.sleep(sleep)
        yield AudioFrame(samples=np.zeros(int(16000 * dt), np.float32), t_start=i * dt,
                         t_end=(i + 1) * dt)


def texts(events):
    return [w.text for e in events for w in e.words]


def test_words_come_back_from_the_server(monkeypatch):
    be = RemoteStreamingBackend("wss://x", lambda: "t")
    monkeypatch.setattr(be, "connect", lambda: FakeWS())
    out = texts(be.streamer().transcribe(frames(20)))
    assert out.count("sung") >= 15


def test_falls_back_while_the_server_is_away_and_returns(monkeypatch):
    monkeypatch.setattr(rb, "RETRY_S", 0.05)
    up = threading.Event()

    def connect():
        if not up.is_set():
            raise ConnectionError("cold")
        return FakeWS()

    be = RemoteStreamingBackend("wss://x", lambda: "t", fallback=Local())
    monkeypatch.setattr(be, "connect", connect)
    monkeypatch.setattr(rb, "MAX_BACKOFF_S", 0.05)
    s = be.streamer()
    seen = []

    def feed():
        for i, f in enumerate(frames(200, dt=0.1, sleep=0.005)):
            if i == 100:
                up.set()
            yield f

    for e in s.transcribe(feed()):
        seen.extend(w.text for w in e.words)
    assert "local" in seen and "sung" in seen
    assert seen.index("local") < len(seen) - seen[::-1].index("sung") - 1
    assert not s.falling_back
