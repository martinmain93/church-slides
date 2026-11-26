"""Online speech's failure ladder: retry silently, fall back, back off, recover."""

import urllib.error

import numpy as np

from auto_presenter.asr import hosted_backend as hb
from auto_presenter.asr.hosted_backend import HostedCredentials, HostedSpeechBackend
from auto_presenter.asr.types import TranscriptEvent, Word
from auto_presenter.audio.types import AudioFrame

CREDS = HostedCredentials(base_url="https://speech.example/v1", model="m", token="t")
FRAMES = [AudioFrame(samples=np.zeros(16000, np.float32), t_start=10.0, t_end=11.0)]
OK = {"words": [{"word": "grace", "start": 0.2, "end": 0.5}]}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Fallback:
    def __init__(self):
        self.calls = 0

    def transcribe(self, frames):
        self.calls += 1
        list(frames)
        word = Word(text="local", t_start=10.0, t_end=10.2, confidence=1.0)
        return iter([TranscriptEvent(words=[word], is_final=True, segment_id=0)])


def make(results, fallback=None, creds=CREDS):
    clock, sent = Clock(), []

    def call(c, wav, prompt=None):
        sent.append(c)
        r = results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    b = HostedSpeechBackend(lambda: creds, fallback=fallback, clock=clock, call=call)
    return b, clock, sent


def words(events):
    return [w.text for e in events for w in e.words]


def test_words_are_placed_on_the_window_clock():
    b, _, _ = make([OK])
    (event,) = list(b.transcribe(FRAMES))
    assert event.words[0].text == "grace"
    assert event.words[0].t_start == 10.2


def test_retries_silently_then_falls_back_then_recovers(monkeypatch):
    monkeypatch.setattr(hb.random, "uniform", lambda a, b: 1.0)
    fb = Fallback()
    down = urllib.error.URLError("down")
    b, clock, sent = make([down, down, down, down, OK], fallback=fb)

    assert words(b.transcribe(FRAMES)) == []  # t=0: first failure, silent
    clock.t = 1.0
    assert words(b.transcribe(FRAMES)) == []  # still inside the retry window
    assert not b.falling_back
    clock.t = 3.5
    assert words(b.transcribe(FRAMES)) == ["local"]  # gave up: fallback, backoff 2 s
    assert b.falling_back and not b.offline
    clock.t = 4.5
    assert words(b.transcribe(FRAMES)) == ["local"]  # backing off: no call
    assert len(sent) == 3
    clock.t = 5.6
    assert words(b.transcribe(FRAMES)) == ["local"]  # tried, failed: backoff 4 s
    assert len(sent) == 4
    clock.t = 9.7
    assert words(b.transcribe(FRAMES)) == ["grace"]  # online again
    assert not b.falling_back and fb.calls == 3


def test_no_fallback_means_offline_and_backoff_caps(monkeypatch):
    monkeypatch.setattr(hb.random, "uniform", lambda a, b: 1.0)
    down = urllib.error.URLError("down")
    b, clock, _ = make([down] * 20)
    for t in (0.0, 1.0, 3.0):
        clock.t = t
        assert words(b.transcribe(FRAMES)) == []
    assert b.offline
    waits = []
    for _ in range(8):
        clock.t = b._next_try
        before = clock.t
        b.transcribe(FRAMES)
        waits.append(b._next_try - before)
    assert waits[:3] == [4.0, 8.0, 16.0] and max(waits) == hb.MAX_BACKOFF_S


def test_no_key_counts_as_a_failure():
    fb = Fallback()
    b, clock, sent = make([], fallback=fb, creds=None)
    b.transcribe(FRAMES)
    clock.t = 3.0
    assert words(b.transcribe(FRAMES)) == ["local"] and sent == []
