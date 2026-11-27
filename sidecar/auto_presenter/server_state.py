"""The WS server's shared singleton state, and the broadcast primitives
that read it.

Split out of `server.py` so the handler-group modules (`server_recordings`,
`server_music`, `server_instructions`, and `server.py` itself) can
all reach `_state`/`_broadcast`/`_send`/`_send_error` without importing
from each other — every one of them imports from here instead, which is
what keeps the split from becoming a ring of circular imports.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import websockets
from pydantic import BaseModel
from websockets.asyncio.server import ServerConnection

from auto_presenter.asr.model_manager import ModelManager
from auto_presenter.asr.whisper_backend import WhisperBackend
from auto_presenter.decision.director import Director
from auto_presenter.decision.engine import MatchingEngine
from auto_presenter.decision.flow import FlowFollower
from auto_presenter.decision.instruction_models import DEFAULT_MODEL_ID
from auto_presenter.decision.instructions import (
    InstructionEngine,
    SentenceGate,
    UtteranceBuffer,
)
from auto_presenter.decision.library_recovery import OffSetlistWatcher
from auto_presenter.decision.singing import SingingDetector
from auto_presenter.music.band import BandPresence
from auto_presenter.index.fingerprint import FingerprintIndex
from auto_presenter.library_file import open_library_path
from auto_presenter.music.analyser import MusicAnalyser
from auto_presenter.pipeline.audio_session import AudioSession
from auto_presenter.pipeline.stage_cost import StageCost
from auto_presenter.protocol import ErrorMessage, ParticleFilterTunables, StartAudioSessionRequest
from auto_presenter.recording.sink import RecordingSink

log = logging.getLogger(__name__)


class _ServerState:
    """Shared mutable state across the WS handler.

    Singleton-per-process. The audio session is also a singleton — there's
    only one mic and we don't want competing capture loops fighting over
    PortAudio. The Whisper model, once loaded, is cached here so a stop +
    re-start with the same model size doesn't re-download / re-load.
    `recordings_root` is set by `serve()` from the app-data dir Tauri
    passes via `--db-path`.
    """

    @property
    def library_db_path(self) -> Path | None:
        """The open library's file, so off-setlist recovery, the follower and
        library search read the songs the operator is working with."""
        return open_library_path(self.home_db_path)

    @library_db_path.setter
    def library_db_path(self, value: Path | None) -> None:
        self.home_db_path = value

    def __init__(self) -> None:
        self.clients: set[ServerConnection] = set()
        self.audio_session: AudioSession | None = None
        #: Bumped for every session started, so a replaced session's late
        #: reports can be told from the current one's (`_session_state_handler`).
        self.session_generation: int = 0
        self.active_recording: RecordingSink | None = None
        self.whisper_backend: WhisperBackend | None = None
        self.whisper_model_size: str | None = None
        self.recordings_root: Path | None = None
        #: This computer's own `library.db` (`--db-path`). The library that's
        #: open may be another file: see `library_db_path`. None in tests
        #: that don't pass `--db-path`.
        self.home_db_path: Path | None = None
        #: The delayed library-search index warm-up (server_library_search).
        self.library_search_warmup: asyncio.Task[None] | None = None
        self.library_index: FingerprintIndex | None = None
        self.off_setlist: OffSetlistWatcher | None = None
        #: Last chord the music analyser emitted, for the "still hearing
        #: music" gate. `None` until a frame has been analysed.
        self.last_chord: str | None = None
        self.last_chord_t: float | None = None
        self.model_manager: ModelManager = ModelManager()
        #: Matcher + director live for the duration of a single audio
        #: session. Created when `start_audio_session` includes
        #: `setlist_content`; torn down on stop. The matcher is a
        #: `MatchingEngine` (NaiveMatcher or ParticleFilterEngine).
        self.matcher: MatchingEngine | None = None
        self.director: Director | None = None
        #: Continuous-mode position follower. Rebuilt whenever the matcher's
        #: index changes, since its word offsets are that index's slides.
        self.flow_follower: FlowFollower | None = None
        #: The operator override a reading's track last restarted from
        #: (`Director.last_override_t`); see `server._observe_flow`.
        self.flow_override_t: float | None = None
        #: Active tunables. Mirrored to all clients as
        #: `TunablesUpdatedMessage` on change.
        self.tunables: ParticleFilterTunables = ParticleFilterTunables()
        #: Live chord/beat analysis — diagnostic only, off by default. See
        #: `server_music` and docs/research/CHORD_DOWNBEAT.md.
        self.music_analyser: MusicAnalyser | None = None
        self.music_chords: bool = False
        self.music_beats: bool = False
        self.music_backend_status: str = "model_missing"
        #: The score follower for this session (`server_follower`), when any
        #: setlist song has a lead sheet; with pipeline-health streaks that
        #: step its scorer down and back up.
        self.followers = None
        #: The other songs' opening-line watch (`follower.openings`), or None.
        self.openings = None
        #: A song it heard once while no band was playing, waiting for a
        #: second hearing: (item_idx, audio seconds) — `apply_song_heard`.
        self.openings_pending: tuple[int, float] | None = None
        #: The song the AI last moved the screen into (`server_follower.note_ai_move`).
        self.last_ai_item: int | None = None
        #: An AI song change on probation: (item_idx, audio seconds of the
        #: change, the listener's gains for that song since) — `SONG_PROBATION_S`.
        self.song_probation: tuple[int, float, list[float]] | None = None
        #: The song the AI changed to was then not heard: blank until it is.
        self.song_unconfirmed: bool = False
        #: Whether a band is playing, from the session's chord analyser
        #: (`music.band`).
        self.band = BandPresence()
        #: How far behind real time the pipeline last reported itself
        #: (`PipelineHealth.lag_seconds`): Assist on a slower computer says
        #: nothing from evidence this late (`server_assist`).
        self.pipeline_lag_s = 0.0
        #: This session's online speech (`asr.remote_backend`), or None when
        #: it listens on this computer only.
        self.remote_speech: Any = None
        #: The session's audio clock at the last health report, and when
        #: (time.monotonic) it arrived: gives an operator's click a time on
        #: the session clock (`audio_now`).
        self.audio_clock: tuple[float, float] | None = None
        #: An AI slide change sent to the app and not yet reported back:
        #: (item, slide, time.monotonic sent). The app declines some (out of
        #: a scripture or text item, in continuous mode) without saying so.
        self.ai_move_pending: tuple[int, int, float] | None = None
        #: The learning observer (`server_learning`), when collecting.
        self.learning = None
        self.follower_healthy: int = 0
        #: What the music follower and the chord analyser cost per frame
        #: (`pipeline.stage_cost`), reported in pipeline health.
        #: The running session's speech backend ("whisper", "mlx", "groq"):
        #: whether switching live AI on needs this computer's machine check.
        self.session_asr_backend: str | None = None
        self.session_asr_model: str | None = None
        self.follower_cost = StageCost()
        self.chord_cost = StageCost()
        self.follower_unhealthy: int = 0
        #: The session's transcript handler: the one path every word stream
        #: takes to the matcher.
        self.transcript_handler = None
        #: Whether anyone is singing the words on the screen, and whether we
        #: are acting on that by blanking. On by default since 2026-10-16, for
        #: long silence (`SingingDetector.long_silence`): a live service sat on
        #: stale lyrics through the gaps between songs and long instrumental
        #: breaks. A passage a person put up is never blanked (the screens
        #: hold it — `lib/scripture/liveScripture`).
        self.singing: SingingDetector = SingingDetector()
        self.blank_when_idle: bool = True
        #: Also blank — and hold the AI's moves — when the words heard are not
        #: the ones on screen (`SingingDetector.off_book`). Off: on real room
        #: audio the transcript of *singing* is often garbage, which reads as
        #: talking. Replayed on the 2026-10-02 live set it called 75 s of
        #: Awesome God "not these words" (a black screen mid-song), and the
        #: hold cost wrong-slide 0.273 -> 0.306. Silence is the trigger that
        #: holds up; this stays for when the talking signal does.
        self.blank_on_talking: bool = False
        self.blanking: bool = False
        #: Spoken instructions ("let's go back to verse one"). Off by
        #: default and unmeasured on real utterances — see
        #: `decision.instructions`. `instruction_engine` is built lazily on
        #: first enable, since it needs a model name and there is no point
        #: paying ollama's connection setup for a feature nobody turned on.
        self.spoken_instructions_enabled: bool = False
        #: The passage a person has on screen ("" for none), and the last one
        #: shown, so a spoken request can be relative to it ("the next few
        #: verses"). Told by the UI (`live_scripture`), whoever put it up.
        self.scripture_on_screen: str = ""
        self.scripture_last: str = ""
        self.instruction_engine: InstructionEngine | None = None
        self.utterance_buffer: UtteranceBuffer = UtteranceBuffer()
        #: Guards against an out-of-order race: `engine.decide()` is a ~1s+
        #: network/model call dispatched via `asyncio.to_thread`, so two
        #: utterances spoken close together can have overlapping in-flight
        #: calls that finish in a different order than they started (a
        #: hosted backend's latency varies call to call — see
        #: `docs/done/SPOKEN_INSTRUCTIONS.md`'s Groq/Gemini measurements). Bumped
        #: at dispatch time in `_observe_spoken_instructions`; a call whose
        #: sequence number no longer matches when it returns has been
        #: superseded by a newer utterance and its result is dropped,
        #: regardless of arrival order.
        self.voice_dispatch_seq: int = 0
        #: Holds a request until it's finished being said (`SentenceGate`).
        self.sentence_gate: SentenceGate = SentenceGate()
        #: One model call at a time: while one is out, the newest released
        #: request waits here (older ones are dropped — superseded).
        self.voice_busy: bool = False
        self.voice_next: str | None = None
        #: Session time of the latest transcript event, for a request the
        #: gate releases between events.
        self.voice_last_t: float = 0.0
        self.voice_gate_ticker = None
        #: Which catalog entry (`decision.instruction_models.CATALOG`) the
        #: engine above runs on. Switching this while the feature is enabled
        #: rebuilds the engine immediately — see `_handle_set_instruction_model`.
        #: Inlined rather than calling `_default_instruction_model_id()`
        #: (defined further down): `_state = _ServerState()` runs at import
        #: time, before that later definition exists in the module namespace.
        self.instruction_model_id: str = os.environ.get(
            "AUTO_PRESENTER_INSTRUCTIONS_MODEL", DEFAULT_MODEL_ID
        )
        #: The asyncio loop, so the capture thread can schedule broadcasts.
        self.loop: asyncio.AbstractEventLoop | None = None
        #: Serializes `_handle_start_audio`. Starting a session is slow and
        #: `await`-heavy — loading Whisper takes a second from cache and
        #: minutes on a first download — and for that whole window
        #: `audio_session.is_running` is still False. Two starts arriving in
        #: that window therefore *both* passed the "already running" guard
        #: and both ran to completion, with the loser's matcher silently
        #: overwritten by the winner. Reproduced by firing the debug panel's
        #: transcript-only start 50 ms after a service start: the transcript
        #: session won, `matcher` was left None, and the belief panel sat on
        #: "waiting for the matcher" while transcripts flowed normally.
        self.audio_start_lock: asyncio.Lock = asyncio.Lock()
        #: Last *successful* `start_audio_session`. Debug inject-audio
        #: reuses this so a file source can replace the mic without the UI
        #: resending setlist / backend / tunables. Production never reads it.
        self.last_start_audio: StartAudioSessionRequest | None = None
        #: Last slide the UI reported via `record_slide_change` — the
        #: operator window's idea of what is on screen.
        self.last_slide: tuple[int, int] | None = None
        self.last_slide_source: str | None = None
        #: Set by `serve(dev_tools=True)`. Production spawn never passes
        #: that flag, so the `auto_presenter.dev` handlers stay unloaded.
        self.dev_tools: bool = False

    def audio_now(self) -> float | None:
        """The session's audio clock now: the last health report's reading
        carried forward by the wall clock (at most 5 s), or None before one."""
        if self.audio_clock is None:
            return None
        import time

        audio_s, at = self.audio_clock
        return audio_s + min(5.0, max(0.0, time.monotonic() - at))


_state = _ServerState()


async def _broadcast(message: BaseModel) -> None:
    if not _state.clients:
        return
    data = message.model_dump_json()
    # Snapshot the set so concurrent disconnects can't mutate during iteration.
    targets = list(_state.clients)
    await asyncio.gather(
        *(_safe_send(ws, data) for ws in targets), return_exceptions=True
    )


async def _safe_send(ws: ServerConnection, data: str) -> None:
    try:
        await ws.send(data)
    except (websockets.ConnectionClosedOK, websockets.ConnectionClosedError):
        _state.clients.discard(ws)


async def _send(ws: ServerConnection, message: BaseModel) -> None:
    await ws.send(message.model_dump_json())


async def _send_error(ws: ServerConnection, request_id: int | None, message: str) -> None:
    await _send(ws, ErrorMessage(id=request_id, message=message))


# --- Matcher introspection --------------------------------------------------
# Shared by `server.py`'s `_observe_singing` (audio-session core, stays
# there) and `server_instructions.py`'s `_observe_spoken_instructions` —
# living here instead of in either one avoids those two importing from each
# other.


def _matcher_belief(matcher: MatchingEngine):
    """Reach the production engine's `ForwardBelief` (`.item_idx`,
    `.slide_idx`, `.evidence`, ...).

    Not part of the `MatchingEngine` Protocol — nothing needed it at that
    level until singing detection did. `None` for an engine without one (a
    test double); callers treat a missing belief as "nothing to do here"
    rather than an error.
    """
    filt = getattr(getattr(matcher, "filter", None), "filter", None)
    return getattr(filt, "belief", None)


def _indexed_setlist(matcher: MatchingEngine):
    """Reach the `IndexedSetlist` a matcher is scanning over.

    Not part of the `MatchingEngine` Protocol — nothing else needed it at
    that level. The production forward-filter engine nests it two levels
    down, the same private path `_matcher_belief` already reaches for
    `.belief`; a test double may expose `.setlist` directly.
    """
    setlist = getattr(matcher, "setlist", None)
    if setlist is not None:
        return setlist
    inner = getattr(getattr(matcher, "filter", None), "filter", None)
    return getattr(inner, "setlist", None)


def transcription_needed() -> bool:
    """Whether anything in the running session wants a live transcript.

    Only the AI does: in any mode but "off" it tracks the words, and spoken
    instructions ride on the same transcript — with the AI off, nothing
    listens at all, voice commands included. A session started without a
    setlist has no director and exists only to transcribe. Otherwise, running
    Whisper with the AI off would cost CPU, GPU and memory for the whole
    service to feed a transcript nobody uses.
    """
    director = _state.director
    return director is None or director.mode != "off"


def sync_transcribing() -> None:
    """Point the running session's ASR at `transcription_needed()`.

    Called wherever an input to that rule changes: session start and the AI
    mode switch.
    """
    session = _state.audio_session
    if session is None:
        return
    needed = transcription_needed()
    if session.transcribing != needed:
        log.info("live transcription %s", "on" if needed else "off (nothing needs it)")
    session.set_transcribing(needed)


# --- The engine's record of the screen ------------------------------------
# The scheduler notes a move as shown the moment it decides. Whether it reaches
# the screen is decided later (the director's cooldown, the hold for talk, the
# app declining it), so every path that does not show a move puts the record
# back on the slide the app last reported. Otherwise the engine sits a slide
# behind, or in a song the screen never went to, with nothing to notice.

#: How long an AI move may go unreported before it counts as declined.
AI_MOVE_ECHO_S = 1.5
#: The same for a move to another song. With nobody at the laptop, the app
#: holds one for the band leader's OK for up to 3 s (`jumpHold.ts`), so the
#: engine has to wait that long before counting it declined.
AI_MOVE_ECHO_SONG_S = 4.5


def restore_screen_record() -> None:
    """Tell the engine what the screen really shows, after a move it decided
    did not reach it. Only while the AI drives the screen: in shadow and
    assist the engine runs free of the screen by design."""
    matcher, director, shown = _state.matcher, _state.director, _state.last_slide
    if matcher is None or shown is None or getattr(director, "mode", None) != "active":
        return
    restore = getattr(matcher, "note_not_shown", None)
    if restore is None:
        return
    try:
        restore(*shown)
    except Exception:  # pragma: no cover - defensive
        log.exception("restoring the engine's screen record failed")


def deliver_decision(decision, director=None) -> bool:
    """Hand an engine decision to the director (the session's, by default).
    True when it went to the app; when it did not, the engine's record of the
    screen is put back."""
    director = director if director is not None else _state.director
    if director is None:
        return False
    if director.on_decision(decision):
        import time

        _state.ai_move_pending = (decision.item_idx, decision.slide_idx, time.monotonic())
        return True
    restore_screen_record()
    return False


def check_ai_move_landed() -> None:
    """An AI move the app never reported back was declined (out of a scripture
    or text item, continuous mode): put the engine's record back."""
    pending = _state.ai_move_pending
    if pending is None:
        return
    import time

    shown = _state.last_slide
    other_song = shown is None or shown[0] != pending[0]
    if time.monotonic() - pending[2] < (AI_MOVE_ECHO_SONG_S if other_song else AI_MOVE_ECHO_S):
        return
    _state.ai_move_pending = None
    if _state.last_slide != pending[:2]:
        log.info("AI move to item %d slide %d was not shown; engine re-anchored", *pending[:2])
        restore_screen_record()
