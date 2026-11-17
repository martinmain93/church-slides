"""The toggle and the fan-out, which is where a diagnostic usually breaks.

Two failure modes this pins, both of which have happened in this codebase:
a frame observer that silently never receives frames, and an analyser that
takes the audio pipeline down when it raises.
"""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from auto_presenter.audio.types import SAMPLE_RATE, AudioFrame
from auto_presenter.protocol import SetMusicAnalysisRequest
from auto_presenter.server_music import (
    _handle_set_music_analysis,
    music_enabled,
    observe_frame,
)
from auto_presenter.server_state import _state


class _WS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, payload: str) -> None:
        self.sent.append(payload)


def _frame(t: float = 0.0) -> AudioFrame:
    n = 1600
    x = (0.2 * np.sin(2 * np.pi * 220 * np.arange(n) / SAMPLE_RATE)).astype(np.float32)
    return AudioFrame(samples=x, t_start=t, t_end=t + n / SAMPLE_RATE)


@pytest.fixture(autouse=True)
def _clean():
    _state.music_chords = False
    _state.music_beats = False
    _state.music_analyser = None
    _state.loop = None
    yield
    _state.music_chords = False
    _state.music_beats = False
    _state.music_analyser = None


async def test_toggle_creates_and_clears_the_analyser():
    ws = _WS()
    await _handle_set_music_analysis(
        ws, SetMusicAnalysisRequest(chords=True, beats=False)
    )
    assert music_enabled()
    assert _state.music_analyser is not None
    assert _state.music_analyser.chords_enabled

    await _handle_set_music_analysis(
        ws, SetMusicAnalysisRequest(chords=False, beats=False)
    )
    assert not music_enabled()
    # Dropped rather than kept, so switching back on does not resume a stale
    # tempo estimate from whenever it was last used.
    assert _state.music_analyser is None


async def test_frames_are_ignored_while_off():
    observe_frame(_frame())
    assert _state.music_analyser is None


async def test_frames_are_analysed_once_on():
    _state.music_chords = True
    _state.music_beats = True
    _state.loop = asyncio.get_running_loop()
    for i in range(6):
        observe_frame(_frame(i * 0.1))
    analyser = _state.music_analyser
    assert analyser is not None
    assert analyser._audio.size > 0


async def test_toggles_take_effect_without_restarting_the_session():
    """The fan-out reads the flags per frame, not once at setup."""
    _state.music_chords = True
    _state.music_beats = False
    _state.loop = asyncio.get_running_loop()
    observe_frame(_frame())
    assert _state.music_analyser is not None
    assert not _state.music_analyser.beats_enabled
    _state.music_beats = True
    observe_frame(_frame(0.1))
    assert _state.music_analyser.beats_enabled


async def test_an_analyser_failure_disables_itself_rather_than_the_pipeline():
    _state.music_chords = True
    _state.loop = asyncio.get_running_loop()
    observe_frame(_frame())

    class Boom:
        chords_enabled = True
        beats_enabled = False

        def feed(self, frame):
            raise RuntimeError("boom")

    _state.music_analyser = Boom()
    observe_frame(_frame(0.1))          # must not raise
    assert not music_enabled()


async def test_backend_status_is_reported_even_when_the_model_is_absent(monkeypatch):
    """The panel must be able to say *why* it is on template matching."""
    import auto_presenter.server_music as sm

    class Missing:
        available = False
        status = "model_missing"

    monkeypatch.setattr(sm, "BtcChordBackend", lambda *a, **k: Missing())
    ws = _WS()
    await _handle_set_music_analysis(
        ws, SetMusicAnalysisRequest(chords=True, beats=False)
    )
    assert _state.music_backend_status == "model_missing"
    assert _state.music_analyser is not None
    assert _state.music_analyser.backend_name == "template"


async def test_the_learned_backend_is_used_when_it_loads(monkeypatch):
    import auto_presenter.server_music as sm

    class Ready:
        available = True
        status = "ready"
        labels = ["C"] * 25

        def reset_stream(self):
            pass

    monkeypatch.setattr(sm, "BtcChordBackend", lambda *a, **k: Ready())
    ws = _WS()
    await _handle_set_music_analysis(
        ws, SetMusicAnalysisRequest(chords=True, beats=True)
    )
    assert _state.music_backend_status == "ready"
    assert _state.music_analyser is not None
    assert _state.music_analyser.backend_name == "btc"
