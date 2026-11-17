"""`server._observe_spoken_instructions` — the layer between a flushed
utterance and the director/broadcast, exercised directly rather than
through the full WS dispatch (which needs a running session, matcher, and
setlist just to reach this function).
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from auto_presenter import server_instructions as server_mod
from auto_presenter.asr.types import TranscriptEvent, Word
from auto_presenter.decision.instructions import GoToSlide, ShowScripture


class _FakeUtteranceBuffer:
    """Flushes a scripted utterance on every call — the lexical trigger isn't
    what's under test here (see `decision/test_instructions.py` for that)."""

    def __init__(self, utterances: list[str | None]):
        self._utterances = iter(utterances)

    def observe(self, words, t_seconds, *, ends_segment=False):
        return next(self._utterances, None)

    def reset(self) -> None:
        pass

    def consume(self) -> None:
        pass

    def keep_listening(self, t_seconds) -> None:
        self.kept_listening = t_seconds


class _FakeMatcher:
    current_item_idx = 0
    setlist = object()  # anything non-None; `engine.decide` below ignores it


class _RecordingDirector:
    def __init__(self, mode: str = "active"):
        self.mode = mode
        self.overrides: list[tuple[int, int]] = []

    def on_override(self, item_idx, slide_idx, *, t_seconds=None):
        self.overrides.append((item_idx, slide_idx))


class _ControllableEngine:
    """`decide()` blocks per-utterance until released, so a test can force
    two calls to *finish* in a different order than they *started* — the
    exact shape of the race a variable-latency hosted backend produces."""

    def __init__(self, results: dict[str, GoToSlide | None]):
        self._results = results
        self._gates = {u: threading.Event() for u in results}
        self.started: list[str] = []

    def release(self, utterance: str) -> None:
        self._gates[utterance].set()

    def decide(
        self, utterance, setlist, current_item_idx, current_slide_idx=None, library=None,
        scripture=None, passage_on_screen="", passage_last="",
    ):
        self.started.append(utterance)
        if utterance not in self._results:
            return None  # as a real backend does for anything it cannot act on
        assert self._gates[utterance].wait(timeout=2.0), f"{utterance!r} was never released"
        return self._results[utterance]


def _event(text: str, t_end: float, *, ends_segment: bool = True) -> TranscriptEvent:
    """A finished sentence by default, so the sentence gate lets it straight
    through; the gate itself is tested in `decision/test_instructions.py`."""
    words = [Word(text=w, t_start=t_end - 1.0, t_end=t_end) for w in text.split()]
    return TranscriptEvent(words=words, is_final=True, segment_id=0, ends_segment=ends_segment)


@pytest.fixture(autouse=True)
def _reset_state():
    original = (
        server_mod._state.spoken_instructions_enabled,
        server_mod._state.instruction_engine,
        server_mod._state.utterance_buffer,
        server_mod._state.voice_dispatch_seq,
    )
    server_mod._state.sentence_gate.reset()
    server_mod._state.voice_busy = False
    server_mod._state.voice_next = None
    yield
    (
        server_mod._state.spoken_instructions_enabled,
        server_mod._state.instruction_engine,
        server_mod._state.utterance_buffer,
        server_mod._state.voice_dispatch_seq,
    ) = original


@pytest.fixture()
def broadcasts(monkeypatch):
    sent = []

    async def fake_broadcast(msg):
        sent.append(msg)

    monkeypatch.setattr(server_mod, "_broadcast", fake_broadcast)
    return sent


@pytest.mark.asyncio
async def test_a_resolved_utterance_overrides_the_director_and_broadcasts(broadcasts):
    server_mod._state.spoken_instructions_enabled = True
    server_mod._state.utterance_buffer = _FakeUtteranceBuffer(["go to the chorus"])
    engine = _ControllableEngine({"go to the chorus": GoToSlide(0, 3, None, "chorus:1")})
    engine.release("go to the chorus")
    server_mod._state.instruction_engine = engine
    director = _RecordingDirector()

    await server_mod._observe_spoken_instructions(_FakeMatcher(), director, _event("go to the chorus", 10.0))

    assert director.overrides == [(0, 3)]
    slide_changes = [m for m in broadcasts if getattr(m, "kind", None) == "slide_change"]
    assert len(slide_changes) == 1
    assert slide_changes[0].source == "voice"
    assert slide_changes[0].item_idx == 0
    assert slide_changes[0].slide_idx == 3


@pytest.mark.asyncio
async def test_no_action_overrides_nothing_but_still_reports_the_utterance(broadcasts):
    server_mod._state.spoken_instructions_enabled = True
    server_mod._state.utterance_buffer = _FakeUtteranceBuffer(["amen"])
    engine = _ControllableEngine({"amen": None})
    engine.release("amen")
    server_mod._state.instruction_engine = engine
    director = _RecordingDirector()

    await server_mod._observe_spoken_instructions(_FakeMatcher(), director, _event("amen", 5.0))

    assert director.overrides == []
    heard = [m for m in broadcasts if getattr(m, "kind", None) == "spoken_instruction"]
    assert len(heard) == 1
    assert heard[0].applied is False
    assert heard[0].utterance == "amen"


@pytest.mark.asyncio
async def test_a_passage_request_goes_to_the_screens_without_moving_the_slide(broadcasts):
    """A verse lays over the lyrics: the AI keeps following the song
    underneath, so nothing overrides the director."""
    said = "bring up jeremiah 29 11"
    server_mod._state.spoken_instructions_enabled = True
    server_mod._state.utterance_buffer = _FakeUtteranceBuffer([said])
    engine = _ControllableEngine({said: ShowScripture("Jeremiah 29:11", "full")})
    engine.release(said)
    server_mod._state.instruction_engine = engine
    director = _RecordingDirector()

    await server_mod._observe_spoken_instructions(_FakeMatcher(), director, _event(said, 5.0))

    assert director.overrides == []
    shown = [m for m in broadcasts if getattr(m, "kind", None) == "show_scripture"]
    assert [(m.reference, m.display) for m in shown] == [("Jeremiah 29:11", "full")]
    heard = [m for m in broadcasts if getattr(m, "kind", None) == "spoken_instruction"]
    assert heard[0].applied is True
    assert heard[0].scripture_reference == "Jeremiah 29:11"
    assert not any(getattr(m, "kind", None) == "slide_change" for m in broadcasts)


@pytest.mark.asyncio
async def test_nothing_listens_while_the_ai_is_off(broadcasts):
    # AI off means off, voice included. Transcription is paused then; this is
    # the last pass still in flight when the operator switches it off.
    server_mod._state.spoken_instructions_enabled = True
    server_mod._state.utterance_buffer = _FakeUtteranceBuffer(["go to the chorus"])
    engine = _ControllableEngine({"go to the chorus": GoToSlide(0, 3, None, "chorus:1")})
    engine.release("go to the chorus")
    server_mod._state.instruction_engine = engine
    director = _RecordingDirector(mode="off")

    await server_mod._observe_spoken_instructions(_FakeMatcher(), director, _event("go to the chorus", 10.0))

    assert director.overrides == []
    assert broadcasts == []


@pytest.mark.asyncio
async def test_disabled_feature_does_nothing(broadcasts):
    server_mod._state.spoken_instructions_enabled = False
    server_mod._state.instruction_engine = None
    director = _RecordingDirector()

    await server_mod._observe_spoken_instructions(_FakeMatcher(), director, _event("go to the chorus", 1.0))

    assert director.overrides == []
    assert broadcasts == []


@pytest.mark.asyncio
async def test_a_slow_stale_utterance_does_not_clobber_a_newer_one(broadcasts):
    """The race this pins: two utterances spoken close together dispatch
    overlapping `engine.decide()` calls. A hosted backend's latency varies
    call to call (measured 0.2-2s+ for Groq/Gemini this session), so the
    *first*-spoken utterance can finish *after* a *later* one. Without a
    sequence guard, the correct, more recent decision gets silently
    overwritten by a stale one arriving late.
    """
    server_mod._state.spoken_instructions_enabled = True
    server_mod._state.utterance_buffer = _FakeUtteranceBuffer(
        ["go to the chorus", "no wait the bridge"]
    )
    engine = _ControllableEngine({
        "go to the chorus": GoToSlide(0, 1, None, "chorus:1"),
        "no wait the bridge": GoToSlide(0, 5, None, "bridge:1"),
    })
    server_mod._state.instruction_engine = engine
    director = _RecordingDirector()

    # Dispatch the first utterance; its decide() call blocks (not released
    # yet), simulating a slow hosted call.
    task_a = asyncio.create_task(
        server_mod._observe_spoken_instructions(
            _FakeMatcher(), director, _event("go to the chorus", 10.0)
        )
    )
    for _ in range(200):
        await asyncio.sleep(0)
        if engine.started == ["go to the chorus"]:
            break
    assert engine.started == ["go to the chorus"]

    # A second, more recent utterance is dispatched and resolves quickly.
    engine.release("no wait the bridge")
    task_b = asyncio.create_task(
        server_mod._observe_spoken_instructions(
            _FakeMatcher(), director, _event("no wait the bridge", 12.0)
        )
    )
    await task_b

    # Only now does the slow first call finally finish.
    engine.release("go to the chorus")
    await task_a

    # The later, correct decision must win — the slow stale one must not
    # apply after the fact, no matter when it finishes.
    assert director.overrides == [(0, 5)]
    slide_changes = [m for m in broadcasts if getattr(m, "kind", None) == "slide_change"]
    assert [(m.item_idx, m.slide_idx) for m in slide_changes] == [(0, 5)]


@pytest.mark.asyncio
async def test_a_spoken_command_reaches_the_engine_end_to_end(broadcasts):
    """Pinning the whole path, because both defects here were in the *wiring*.

    First the buffer was handed `SingingDetector.score` — the smoothed value
    built for blanking — which caps a two-event command at 0.667 under a 0.8
    bar. Fixing that revealed the second: the acoustic score cannot represent
    a short command at all. "Go to the chorus" was transcribed perfectly and
    scored 0.21 with a *ceiling* of 0.74. The gate is lexical now, and the
    model decides.
    """
    from auto_presenter.decision.instructions import UtteranceBuffer

    server_mod._state.spoken_instructions_enabled = True
    server_mod._state.utterance_buffer = UtteranceBuffer()
    # The utterance carries the recent context window with it, sung words
    # included — deliberately generous, since the model is a better judge of
    # what is an instruction than any rule the buffer could apply.
    spoken = "hallelujah hallelujah go to the chorus"
    engine = _ControllableEngine({spoken: GoToSlide(0, 3, None, "chorus:1")})
    engine.release(spoken)
    server_mod._state.instruction_engine = engine
    director = _RecordingDirector()

    # Singing, then the command split across two events exactly as the live
    # trace caught it. No pause afterwards: the old buffer waited for one.
    t = 0.0
    for words in ("hallelujah hallelujah", "go to the", "chorus"):
        t += 1.0
        await server_mod._observe_spoken_instructions(
            _FakeMatcher(), director, _event(words, t)
        )

    assert director.overrides == [(0, 3)], "the spoken command never reached the engine"
    assert any(getattr(m, "kind", None) == "slide_change" for m in broadcasts)
    # Singing was never offered to the model; the partial command was, and the
    # model declined it. Both are correct, and neither depends on a threshold.
    # Pure singing is never offered to the model at all. The partial command
    # is, and the model declines it — neither depends on a threshold.
    assert "hallelujah hallelujah" not in engine.started
    assert engine.started == ["hallelujah hallelujah go to the", spoken]


@pytest.mark.parametrize(
    ("choice", "key", "on"),
    [
        (None, "gsk_test", True),   # never chose, key saved: on
        (None, None, False),        # never chose, no key: off
        (False, "gsk_test", False),  # chose off: off, key or not
        (True, None, True),         # chose on: on (the on-device fallback)
    ],
)
def test_a_service_starts_with_spoken_requests_on_when_a_hosted_key_is_saved(
    monkeypatch, choice, key, on
):
    monkeypatch.setattr(server_mod, "get_instruction_api_key", lambda provider: key)
    monkeypatch.setattr(server_mod, "_rebuild_instruction_engine", lambda: None)
    monkeypatch.setattr(server_mod._state, "instruction_model_id", "groq-llama-3.1-8b-instant")
    assert server_mod.apply_session_spoken_instructions(choice) is on
    assert server_mod._state.spoken_instructions_enabled is on


def test_readings_are_followed_only_on_mlx(monkeypatch):
    """Following a reading needs every spoken word: only MLX was measured to
    keep up (the processor path dropped 21-48%)."""
    from auto_presenter import server
    from auto_presenter.protocol import ParticleFilterTunables
    from auto_presenter.server_state import _state

    for backend, want in (("mlx", True), ("whisper", False), ("groq", False), ("mock", False), (None, False)):
        monkeypatch.setattr(_state, "session_asr_backend", backend)
        assert server._follow_readings(None) is want, backend
    monkeypatch.setattr(_state, "session_asr_backend", "mlx")
    assert server._follow_readings(ParticleFilterTunables(follow_readings=False)) is False
