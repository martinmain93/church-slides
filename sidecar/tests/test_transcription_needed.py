"""Live transcription runs only while the AI is on.

With the AI off, a presentation session used to
run Whisper and the matcher for the whole service anyway — the same cost as
the AI being on (docs/done/PERF_AUDIT_2026-09-26.md, finding 5).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from auto_presenter import server_state
from auto_presenter.server_state import _state, sync_transcribing, transcription_needed


class FakeSession:
    def __init__(self) -> None:
        self.transcribing = True

    def set_transcribing(self, on: bool) -> None:
        self.transcribing = on


@pytest.fixture(autouse=True)
def _restore(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(_state, "director", None)
    monkeypatch.setattr(_state, "audio_session", None)
    monkeypatch.setattr(_state, "spoken_instructions_enabled", False)


@pytest.mark.parametrize(
    ("mode", "spoken", "needed"),
    [
        ("off", False, False),
        ("off", True, False),  # AI off means nothing listens, voice included
        ("shadow", False, True),
        ("active", False, True),
    ],
)
def test_rule(mode: str, spoken: bool, needed: bool) -> None:
    _state.director = SimpleNamespace(mode=mode)
    _state.spoken_instructions_enabled = spoken
    assert transcription_needed() is needed


def test_transcript_only_session_always_transcribes() -> None:
    # No setlist → no director: the session exists only to transcribe.
    assert transcription_needed() is True


def test_sync_follows_mode_changes() -> None:
    session = FakeSession()
    _state.audio_session = session
    director = SimpleNamespace(mode="off")
    _state.director = director
    sync_transcribing()
    assert session.transcribing is False
    director.mode = "active"
    sync_transcribing()
    assert session.transcribing is True


def test_sync_without_a_session_is_harmless() -> None:
    assert server_state._state.audio_session is None
    sync_transcribing()


def test_ai_off_stops_the_per_frame_music_and_follower_work(monkeypatch):
    """Engine review 2026-11-10, finding 8: "off" paused transcription only,
    while the chord analysis, the follower and the next-line check (Whisper's
    encoder four times a second) ran on for the whole service."""
    import asyncio

    import numpy as np

    from auto_presenter import server_follower
    from auto_presenter.audio.types import AudioFrame
    from auto_presenter.server_state import _state

    calls = []

    class Followers:
        def on_frame(self, frame, update):
            calls.append(frame)

        def take_flow(self):
            return None

    class Director:
        mode = "off"

    monkeypatch.setattr(_state, "tunables", None)
    monkeypatch.setattr(_state, "matcher", object())
    monkeypatch.setattr(_state, "followers", Followers())
    monkeypatch.setattr(_state, "director", Director())
    monkeypatch.setattr("auto_presenter.server_music._new_analyser", lambda *a: None)
    observe = server_follower.follower_frame_observer(asyncio.new_event_loop())
    frame = AudioFrame(samples=np.zeros(1600, np.float32), t_start=0.0, t_end=0.1)
    observe(frame)
    assert calls == []
    Director.mode = "active"
    observe(frame)
    assert len(calls) == 1
