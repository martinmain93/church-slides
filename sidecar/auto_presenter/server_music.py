"""Live chord and beat analysis: wiring, broadcasting, and its two toggles.

Split out of `server.py` as a self-contained optional listener that never
reaches a decision. See
docs/research/CHORD_DOWNBEAT.md for what it is measured to be worth; the short version
is that the chord feature is worth about +1% to the matcher at this detector's
accuracy, which is why it is a diagnostic you can watch rather than something
wired into the decision path.

Frames arrive on the capture thread and broadcasts are scheduled onto the
asyncio loop.
"""

from __future__ import annotations

import asyncio
import logging

from websockets.asyncio.server import ServerConnection

from auto_presenter.audio.types import AudioFrame
from auto_presenter.music.analyser import MusicAnalyser, MusicUpdate
from auto_presenter.music.btc import BtcChordBackend
from auto_presenter.protocol import (
    ChordCandidatePayload,
    MusicAnalysisMessage,
    PongMessage,
    SetMusicAnalysisRequest,
)
from auto_presenter.server_state import _broadcast, _send, _state

log = logging.getLogger(__name__)


def music_enabled() -> bool:
    return bool(_state.music_chords or _state.music_beats)


def _new_analyser(chords: bool, beats: bool) -> MusicAnalyser:
    """Build an analyser, upgrading to the learned recogniser if it is installed.

    Loading is attempted once per analyser rather than per frame: an absent
    model is the common case and probing the filesystem ten times a second to
    rediscover that would be silly.
    """
    backend: BtcChordBackend | None = None
    try:
        candidate = BtcChordBackend()
        _state.music_backend_status = candidate.status
        if candidate.available:
            backend = candidate
    except Exception:
        log.exception("chord model failed to load; using template matching")
        _state.music_backend_status = "load_failed"
    return MusicAnalyser(
        chords_enabled=chords, beats_enabled=beats, chord_backend=backend
    )


def observe_frame(frame: AudioFrame) -> None:
    """Feed one captured frame to the analyser and broadcast the result.

    Called from the capture thread. Analysis costs about 0.002 realtime, so it
    is done inline rather than on a queue; if that ever stops being true this is
    the place that has to change.
    """
    if not music_enabled():
        return
    analyser = _state.music_analyser
    if analyser is None:
        analyser = _new_analyser(_state.music_chords, _state.music_beats)
        _state.music_analyser = analyser
    analyser.chords_enabled = _state.music_chords
    analyser.beats_enabled = _state.music_beats
    try:
        update = analyser.feed(frame)
    except Exception:
        # A diagnostic must never take the audio pipeline down with it.
        log.exception("music analysis raised; disabling it for this session")
        _state.music_chords = False
        _state.music_beats = False
        return
    _state.last_chord = update.chord
    _state.last_chord_t = update.t_end
    loop = _state.loop
    if loop is None:
        return
    asyncio.run_coroutine_threadsafe(_broadcast_music(update), loop)


async def _broadcast_music(update: MusicUpdate) -> None:
    await _broadcast(
        MusicAnalysisMessage(
            t_end=update.t_end,
            levels=[round(v, 5) for v in update.levels],
            chord=update.chord,
            candidates=[
                ChordCandidatePayload(name=c.name, confidence=round(c.confidence, 4))
                for c in update.candidates
            ],
            beats=update.beats,
            bpm=round(update.bpm, 1),
            chords_enabled=_state.music_chords,
            beats_enabled=_state.music_beats,
            realtime_factor=round(update.realtime_factor, 5),
            chord_backend=update.backend,
            backend_status=_state.music_backend_status,
        )
    )


async def _handle_set_music_analysis(
    ws: ServerConnection, msg: SetMusicAnalysisRequest
) -> None:
    was_on = music_enabled()
    _state.music_chords = msg.chords
    _state.music_beats = msg.beats
    if not was_on and music_enabled():
        # Start from a clean slate rather than resuming a stale tempo estimate
        # and half-filled onset envelope from whenever it was last switched off.
        _state.music_analyser = _new_analyser(msg.chords, msg.beats)
    elif not music_enabled():
        _state.music_analyser = None
    log.info("music analysis: chords=%s beats=%s", msg.chords, msg.beats)
    await _send(ws, PongMessage(id=msg.id))
