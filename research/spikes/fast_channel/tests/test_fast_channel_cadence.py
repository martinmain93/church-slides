"""The panel must stay alive between words.

Broadcasting only when a word lands tied the refresh rate to the speech rate —
about 0.44 words a second — so the latency and CPU figures froze for seconds and
the panel looked stalled while the channel was working fine.
"""
from __future__ import annotations

import numpy as np
import pytest

from auto_presenter import server_fast_channel as server_mod
from auto_presenter.asr.fast_channel import FastWord


class _Channel:
    available = True
    status = "ready"
    realtime_factor = 0.02

    def __init__(self, words_on_call: dict[int, list[FastWord]]):
        self._words = words_on_call
        self.calls = 0

    def feed(self, samples):
        self.calls += 1
        return self._words.get(self.calls, [])


class _Frame:
    samples = np.zeros(1600, dtype=np.float32)


@pytest.fixture
def wired(monkeypatch):
    sent: list[list[FastWord]] = []

    async def fake_broadcast(words):
        sent.append(list(words))

    monkeypatch.setattr(server_mod, "_broadcast_fast_channel", fake_broadcast)

    class _Loop:
        def is_running(self):
            return True

    def run_threadsafe(coro, loop):
        """Drive the coroutine to completion synchronously.

        The real call schedules onto the event loop from the capture thread;
        here there is no loop, and the broadcast is a plain coroutine with
        nothing to await, so stepping it once finishes it.
        """
        try:
            coro.send(None)
        except StopIteration:
            pass

    monkeypatch.setattr(server_mod.asyncio, "run_coroutine_threadsafe",
                        run_threadsafe)
    monkeypatch.setattr(server_mod._state, "loop", _Loop())
    monkeypatch.setattr(server_mod._state, "fast_latencies", [])
    monkeypatch.setattr(server_mod._state, "fast_last_broadcast", 0.0)
    monkeypatch.setattr(server_mod._state, "fast_feeds_matcher", False)
    monkeypatch.setattr(server_mod._state, "fast_listen", True)
    return sent


def test_the_panel_refreshes_while_nobody_is_singing(wired, monkeypatch):
    channel = _Channel({})
    monkeypatch.setattr(server_mod._state, "fast_channel", channel)
    observe = server_mod._frame_fanout(None)

    clock = {"t": 100.0}
    monkeypatch.setattr(server_mod.time, "monotonic", lambda: clock["t"])

    # Two seconds of silent frames, at capture's 10 a second.
    for _ in range(20):
        observe(_Frame())
        clock["t"] += 0.1

    # Not one update per word (there are none), and not one per frame either.
    assert 4 <= len(wired) <= 12, f"got {len(wired)} refreshes over 2 s"


def test_a_word_is_sent_the_moment_it_arrives(wired, monkeypatch):
    word = FastWord("grace", t_start=1.0, t_emitted=1.4)
    channel = _Channel({1: [word]})
    monkeypatch.setattr(server_mod._state, "fast_channel", channel)
    observe = server_mod._frame_fanout(None)
    monkeypatch.setattr(server_mod.time, "monotonic", lambda: 100.0)

    observe(_Frame())
    # Even though no cadence interval has elapsed, the word goes out at once.
    assert wired and wired[0][0].text == "grace"


def test_a_live_service_does_not_feed_sherpa_until_asked(wired, monkeypatch):
    """Session start used to load and run the model on every capture frame.

    The debug tab is the only thing that needs those words. Leaving inference
    off until it asks keeps ~2% of a core for Whisper.
    """
    channel = _Channel({})
    monkeypatch.setattr(server_mod._state, "fast_channel", channel)
    monkeypatch.setattr(server_mod._state, "fast_listen", False)
    monkeypatch.setattr(server_mod._state, "fast_feeds_matcher", False)
    observe = server_mod._frame_fanout(None)
    observe(_Frame())
    assert channel.calls == 0
    assert wired == []
