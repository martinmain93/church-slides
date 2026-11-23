"""Server-side dispatch tests for the audio session start flow.

We monkeypatch `_get_or_load_whisper_backend` so the test doesn't trigger
a real model download. The point of these tests is to assert that the
server routes correctly between backends, caches the Whisper backend on
repeated requests, and propagates load failures as broadcast errors.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from auto_presenter import server as server_mod
from auto_presenter.asr.backend import MockASRBackend
from auto_presenter.asr.model_manager import ModelManager
from auto_presenter.pipeline.audio_session import StateUpdate
from auto_presenter.protocol import StartAudioSessionRequest


@pytest.fixture(autouse=True)
def reset_server_state():
    """Each test starts with a clean server state singleton."""
    original_clients = server_mod._state.clients
    original_session = server_mod._state.audio_session
    original_whisper = server_mod._state.whisper_backend
    original_size = server_mod._state.whisper_model_size
    original_matcher = server_mod._state.matcher
    original_director = server_mod._state.director
    original_last_start = server_mod._state.last_start_audio

    server_mod._state.clients = set()
    server_mod._state.audio_session = None
    server_mod._state.whisper_backend = None
    server_mod._state.whisper_model_size = None
    server_mod._state.matcher = None
    server_mod._state.director = None
    server_mod._state.last_start_audio = None
    server_mod._state.last_slide = None
    server_mod._state.dev_tools = False

    yield

    # Stop any session the test may have started before restoring.
    if server_mod._state.audio_session is not None:
        try:
            server_mod._state.audio_session.stop()
            server_mod._state.audio_session.join(timeout=0.5)
        except Exception:
            pass

    server_mod._state.clients = original_clients
    server_mod._state.audio_session = original_session
    server_mod._state.whisper_backend = original_whisper
    server_mod._state.whisper_model_size = original_size
    server_mod._state.matcher = original_matcher
    server_mod._state.director = original_director
    server_mod._state.last_start_audio = original_last_start


@pytest.fixture()
def fake_capture():
    """Replace `LiveCapture` so we don't open PortAudio in tests."""
    fake = MagicMock()
    instance = MagicMock()
    instance.start = MagicMock()
    instance.stop = MagicMock()
    instance.stream = MagicMock(return_value=iter([]))
    fake.return_value = instance
    with patch.object(server_mod, "LiveCapture", fake):
        yield fake


@pytest.fixture()
def fake_ws():
    ws = MagicMock()

    async def _send(_data):
        pass

    ws.send = MagicMock(side_effect=_send)
    return ws


# --- whisper dispatch + caching ---------------------------------------------


@pytest.mark.asyncio
async def test_whisper_backend_loaded_via_to_thread(fake_capture, fake_ws):
    loaded_sizes: list[str] = []

    def fake_loader(size: str, *, progress_callback=None):
        loaded_sizes.append(size)
        backend = MockASRBackend(schedule=[], backend_name=f"fake-whisper-{size}")
        return backend

    with patch.object(server_mod, "_get_or_load_whisper_backend", fake_loader):
        msg = StartAudioSessionRequest(
            id=1, backend="whisper", model_size="tiny.en"
        )
        await server_mod._handle_start_audio(fake_ws, msg)

    assert loaded_sizes == ["tiny.en"]


@pytest.mark.asyncio
async def test_mock_backend_does_not_load_whisper(fake_capture, fake_ws):
    """When `backend="mock"`, the whisper loader is never invoked."""
    loader_calls: list[str] = []

    def fake_loader(size: str):
        loader_calls.append(size)
        raise AssertionError("should not be called for mock backend")

    with patch.object(server_mod, "_get_or_load_whisper_backend", fake_loader):
        msg = StartAudioSessionRequest(id=1, backend="mock")
        await server_mod._handle_start_audio(fake_ws, msg)

    assert loader_calls == []


@pytest.mark.asyncio
async def test_whisper_load_failure_broadcasts_error(fake_capture, fake_ws):
    broadcasts: list[StateUpdate] = []

    async def capture_state(update, **_):
        broadcasts.append(update)

    def boom(_size: str, *, progress_callback=None):
        raise RuntimeError("network down")

    with (
        patch.object(server_mod, "_broadcast_state_update", capture_state),
        patch.object(server_mod, "_get_or_load_whisper_backend", boom),
    ):
        msg = StartAudioSessionRequest(id=1, backend="whisper", model_size="tiny.en")
        await server_mod._handle_start_audio(fake_ws, msg)

    states = [u.state for u in broadcasts]
    assert "error" in states
    err_update = next(u for u in broadcasts if u.state == "error")
    assert err_update.error is not None
    assert "network down" in err_update.error


# --- groq dispatch -----------------------------------------------------------


@pytest.mark.asyncio
async def test_groq_backend_is_built_from_the_saved_api_key(fake_capture, fake_ws):
    from auto_presenter.asr.groq_backend import GroqWhisperBackend

    with patch.object(server_mod, "get_instruction_api_key", lambda provider: "gk_test"):
        msg = StartAudioSessionRequest(
            id=1, backend="groq", model_size="whisper-large-v3-turbo"
        )
        await server_mod._handle_start_audio(fake_ws, msg)

    session = server_mod._state.audio_session
    assert session is not None
    assert isinstance(session._wrapper.backend, GroqWhisperBackend)
    assert session._wrapper.backend.name() == "groq-whisper-large-v3-turbo"


@pytest.mark.asyncio
async def test_groq_backend_without_a_key_sends_an_error_not_a_crash(fake_capture, fake_ws):
    sent_errors = []

    async def capture_error(_ws, _id, message):
        sent_errors.append(message)

    with (
        patch.object(server_mod, "get_instruction_api_key", lambda provider: None),
        patch.object(server_mod, "_send_error", capture_error),
    ):
        msg = StartAudioSessionRequest(id=1, backend="groq", model_size="whisper-large-v3-turbo")
        await server_mod._handle_start_audio(fake_ws, msg)

    assert len(sent_errors) == 1
    assert "api key" in sent_errors[0].lower()
    assert server_mod._state.audio_session is None


# --- caching ----------------------------------------------------------------


def test_get_or_load_whisper_caches_by_model_size(tmp_path):
    server_mod._state.whisper_backend = None
    server_mod._state.whisper_model_size = None

    load_calls: list[str] = []

    def fake_loader(size: str, *, device, compute_type, **_kw):
        load_calls.append(size)
        return ("fake-model", size)

    original_manager = server_mod._state.model_manager
    server_mod._state.model_manager = ModelManager(
        cache_dir=tmp_path,
        whisper_loader=fake_loader,
    )
    try:
        a = server_mod._get_or_load_whisper_backend("tiny.en")
        b = server_mod._get_or_load_whisper_backend("tiny.en")
        assert a is b
        # Second call should hit the in-memory backend cache; the loader
        # is only invoked once.
        assert load_calls == ["tiny.en"]
    finally:
        server_mod._state.model_manager = original_manager


def test_get_or_load_whisper_reloads_when_size_changes(tmp_path):
    server_mod._state.whisper_backend = None
    server_mod._state.whisper_model_size = None

    load_calls: list[str] = []

    def fake_loader(size: str, *, device, compute_type, **_kw):
        load_calls.append(size)
        return ("fake-model", size)

    original_manager = server_mod._state.model_manager
    server_mod._state.model_manager = ModelManager(
        cache_dir=tmp_path,
        whisper_loader=fake_loader,
    )
    try:
        a = server_mod._get_or_load_whisper_backend("tiny.en")
        b = server_mod._get_or_load_whisper_backend("base.en")
        assert a is not b
        assert load_calls == ["tiny.en", "base.en"]
    finally:
        server_mod._state.model_manager = original_manager


# --- matcher / director wiring ----------------------------------------------


def _setlist_content(*items):
    """Helper: build a SetlistContent payload from `(idx, song_id, [[words],...])`."""
    from auto_presenter.protocol import SetlistContent, SetlistItemContent

    return SetlistContent(
        items=[
            SetlistItemContent(item_idx=i, song_id=sid, slides=slides)
            for (i, sid, slides) in items
        ]
    )


@pytest.mark.asyncio
async def test_start_audio_with_setlist_content_creates_matcher_and_director(
    fake_capture, fake_ws
):
    """When the UI pushes setlist content, the server builds the matcher."""
    from auto_presenter.protocol import StartAudioSessionRequest

    msg = StartAudioSessionRequest(
        id=1,
        backend="mock",
        setlist_content=_setlist_content(
            (0, "song_a", [["amazing", "grace"]]),
        ),
        ai_mode="shadow",
    )
    await server_mod._handle_start_audio(fake_ws, msg)

    assert server_mod._state.matcher is not None
    assert server_mod._state.director is not None
    assert server_mod._state.director.mode == "shadow"
    assert server_mod._state.matcher.current_item_idx == 0


@pytest.mark.asyncio
async def test_start_audio_without_setlist_content_skips_matcher(
    fake_capture, fake_ws
):
    from auto_presenter.protocol import StartAudioSessionRequest

    msg = StartAudioSessionRequest(id=1, backend="mock")
    await server_mod._handle_start_audio(fake_ws, msg)

    assert server_mod._state.matcher is None
    assert server_mod._state.director is None


@pytest.mark.asyncio
async def test_stop_audio_tears_down_matcher_and_director(fake_capture, fake_ws):
    from auto_presenter.protocol import (
        StartAudioSessionRequest,
        StopAudioSessionRequest,
    )

    await server_mod._handle_start_audio(
        fake_ws,
        StartAudioSessionRequest(
            id=1,
            backend="mock",
            setlist_content=_setlist_content((0, "song_a", [["a"]])),
        ),
    )
    assert server_mod._state.matcher is not None

    await server_mod._handle_stop_audio(fake_ws, StopAudioSessionRequest(id=2))
    assert server_mod._state.matcher is None
    assert server_mod._state.director is None


def test_slide_change_with_operator_source_forwards_to_director(
    fake_capture, fake_ws
):
    """Operator overrides (manual/override) should re-aim the director."""
    from auto_presenter.protocol import (
        RecordSlideChangeNotification,
        SetlistContent,
        SetlistItemContent,
    )
    from auto_presenter.index.library import build_index
    from auto_presenter.decision.production import build_engine
    from auto_presenter.decision.director import Director

    setlist = build_index(
        SetlistContent(
            items=[
                SetlistItemContent(item_idx=0, song_id="a", slides=[["x"]]),
                SetlistItemContent(item_idx=1, song_id="b", slides=[["y"]]),
            ]
        )
    )
    matcher = build_engine(setlist)
    director = Director(
        matcher=matcher,
        on_decision_broadcast=lambda d: None,
        on_status_broadcast=lambda s: None,
        mode="active",
    )
    server_mod._state.matcher = matcher
    server_mod._state.director = director

    server_mod._handle_slide_change(
        RecordSlideChangeNotification(
            id=None, item_idx=1, slide_idx=0, source="override"
        )
    )

    assert matcher.current_item_idx == 1
    assert director.state == "following_human"


def test_slide_change_with_ai_source_does_not_loop_into_director(
    fake_capture, fake_ws
):
    """AI-sourced changes are the director's own output — don't echo back."""
    from auto_presenter.decision.director import Director
    from auto_presenter.index.library import build_index
    from auto_presenter.decision.production import build_engine
    from auto_presenter.protocol import (
        RecordSlideChangeNotification,
        SetlistContent,
        SetlistItemContent,
    )

    setlist = build_index(
        SetlistContent(items=[SetlistItemContent(item_idx=0, song_id="a", slides=[["x"]])])
    )
    matcher = build_engine(setlist)
    director = Director(
        matcher=matcher,
        on_decision_broadcast=lambda d: None,
        on_status_broadcast=lambda s: None,
        mode="active",
    )
    server_mod._state.matcher = matcher
    server_mod._state.director = director

    server_mod._handle_slide_change(
        RecordSlideChangeNotification(id=None, item_idx=0, slide_idx=0, source="ai")
    )
    # `ai` source should not transition to `following_human`.
    assert director.state in {"idle", "tracking"}


@pytest.mark.asyncio
async def test_set_ai_mode_request_routes_to_director(fake_ws):
    from auto_presenter.decision.director import Director
    from auto_presenter.index.library import build_index
    from auto_presenter.decision.production import build_engine
    from auto_presenter.protocol import (
        SetAIModeRequest,
        SetlistContent,
        SetlistItemContent,
    )

    setlist = build_index(
        SetlistContent(items=[SetlistItemContent(item_idx=0, song_id="a", slides=[["x"]])])
    )
    matcher = build_engine(setlist)
    director = Director(
        matcher=matcher,
        on_decision_broadcast=lambda d: None,
        on_status_broadcast=lambda s: None,
        mode="shadow",
    )
    server_mod._state.director = director
    await server_mod._handle_set_ai_mode(fake_ws, SetAIModeRequest(id=1, mode="active"))
    assert director.mode == "active"


@pytest.mark.asyncio
async def test_assist_to_active_takes_over_from_the_screen(fake_ws):
    """Assist lets the engine run free of the screen; going active starts it
    from the slide on screen, not from its own guess (live, 2026-10-28)."""
    from auto_presenter.decision.director import Director
    from auto_presenter.index.library import build_index
    from auto_presenter.decision.production import build_engine
    from auto_presenter.protocol import SetAIModeRequest, SetlistContent, SetlistItemContent

    setlist = build_index(
        SetlistContent(
            items=[
                SetlistItemContent(item_idx=0, song_id="a", slides=[["x"], ["y"]]),
                SetlistItemContent(item_idx=1, song_id="b", slides=[["p"], ["q"], ["r"]]),
            ]
        )
    )
    matcher = build_engine(setlist)
    director = Director(
        matcher=matcher,
        on_decision_broadcast=lambda d: None,
        on_status_broadcast=lambda s: None,
        mode="assist",
    )
    server_mod._state.director = director
    server_mod._state.last_slide = (1, 2)
    try:
        await server_mod._handle_set_ai_mode(fake_ws, SetAIModeRequest(id=1, mode="active"))
    finally:
        server_mod._state.last_slide = None
    assert director.mode == "active"
    assert matcher.current_item_idx == 1
    assert director.state == "following_human"


@pytest.mark.asyncio
async def test_set_ai_mode_without_active_session_returns_error(fake_ws):
    from auto_presenter.protocol import SetAIModeRequest

    server_mod._state.director = None
    await server_mod._handle_set_ai_mode(fake_ws, SetAIModeRequest(id=1, mode="active"))
    fake_ws.send.assert_called()
    # The last call should be an error message; assert its content contains "no active session".
    last_payload = fake_ws.send.call_args.args[0]
    assert "no active session" in last_payload


@pytest.mark.asyncio
async def test_transcript_handler_feeds_matcher_finals_only():
    """Partial (non-final) transcript events must not reach the matcher.

    The matcher consumes stable/final events only; partials re-emit words
    that later finalize, so feeding them double-counts lyrics and stops the
    belief from ever converging. Regression test for AI never advancing in
    a live session.
    """
    from auto_presenter.asr.types import TranscriptEvent

    seen_finals: list[bool] = []

    class _RecordingMatcher:
        current_item_idx = 0

        def update(self, event):  # noqa: ANN001
            seen_finals.append(event.is_final)
            return None

    handler = server_mod._make_transcript_handler(_RecordingMatcher(), MagicMock())

    partial = TranscriptEvent(
        segment_id=0, is_final=False, text="there", t_start=0.0, t_end=0.5, words=[]
    )
    final = TranscriptEvent(
        segment_id=0, is_final=True, text="there is", t_start=0.0, t_end=1.0, words=[]
    )
    await handler(partial)
    await handler(final)

    assert seen_finals == [True]  # only the final reached the matcher


# --- update_setlist_content (operator edits the running setlist) -------------


def _index(server_items):
    from auto_presenter.index.library import build_index
    from auto_presenter.protocol import SetlistContent, SetlistItemContent

    return build_index(
        SetlistContent(
            items=[
                SetlistItemContent(item_idx=i, song_id=f"s{i}", slides=slides)
                for i, slides in enumerate(server_items)
            ]
        )
    )


def _content(server_items):
    from auto_presenter.protocol import SetlistContent, SetlistItemContent

    return SetlistContent(
        items=[
            SetlistItemContent(item_idx=i, song_id=f"s{i}", slides=slides)
            for i, slides in enumerate(server_items)
        ]
    )


def _last_sent(fake_ws):
    import json

    raw = fake_ws.send.call_args.args[0]
    return json.loads(raw)


@pytest.mark.asyncio
async def test_update_setlist_content_reindexes_matcher(fake_ws):
    from auto_presenter.decision.production import build_engine
    from auto_presenter.protocol import UpdateSetlistContentRequest

    server_mod._state.matcher = build_engine(_index([[["a"]]]))
    new = _content([[["x"]], [["y"]], [["z"]]])
    msg = UpdateSetlistContentRequest(id=5, setlist_content=new, current_item_idx=2)

    await server_mod._handle_update_setlist_content(fake_ws, msg)

    assert server_mod._indexed_setlist(server_mod._state.matcher).item_count == 3
    assert server_mod._state.matcher.current_item_idx == 2
    reply = _last_sent(fake_ws)
    assert reply["kind"] == "setlist_content_updated"
    assert reply["applied"] is True
    assert reply["item_count"] == 3


@pytest.mark.asyncio
async def test_update_setlist_content_noop_without_matcher(fake_ws):
    from auto_presenter.protocol import UpdateSetlistContentRequest

    server_mod._state.matcher = None
    msg = UpdateSetlistContentRequest(
        id=6, setlist_content=_content([[["x"]]]), current_item_idx=0
    )
    await server_mod._handle_update_setlist_content(fake_ws, msg)
    reply = _last_sent(fake_ws)
    assert reply["kind"] == "setlist_content_updated"
    assert reply["applied"] is False


@pytest.mark.asyncio
async def test_service_session_preempts_a_transcript_only_session(
    fake_capture, fake_ws
):
    """A debug transcript stream must not lock out the service session.

    There is one microphone and one session, and the server used to reject
    whoever asked second. Leaving the debug panel's Transcript tab open
    therefore meant the presentation's matcher session was refused: words
    still appeared, the belief panel stayed empty, and the AI never advanced
    a slide — with the only clue an error on a request the operator never saw.
    """
    from auto_presenter.protocol import StartAudioSessionRequest

    # Debug panel gets in first: no setlist content, so no matcher.
    await server_mod._handle_start_audio(
        fake_ws, StartAudioSessionRequest(id=1, backend="mock")
    )
    assert server_mod._state.matcher is None
    # The fake capture's stream ends immediately, so the session would report
    # itself stopped and the contention guard would never engage. Hold it
    # "running" so the test exercises the branch it exists for.
    server_mod._state.audio_session.state = "running"

    # The service session arrives and must win.
    await server_mod._handle_start_audio(
        fake_ws,
        StartAudioSessionRequest(
            id=2,
            backend="mock",
            setlist_content=_setlist_content((0, "song_a", [["amazing", "grace"]])),
            ai_mode="active",
        ),
    )
    assert server_mod._state.matcher is not None, (
        "transcript-only session blocked the service session"
    )
    assert server_mod._state.director is not None


@pytest.mark.asyncio
async def test_a_second_service_session_is_still_refused(fake_capture, fake_ws):
    """Preemption is a ranking, not a free-for-all — two matcher sessions
    fighting over one microphone helps nobody."""
    from auto_presenter.protocol import StartAudioSessionRequest

    content = _setlist_content((0, "song_a", [["amazing", "grace"]]))
    await server_mod._handle_start_audio(
        fake_ws, StartAudioSessionRequest(id=1, backend="mock", setlist_content=content)
    )
    first = server_mod._state.matcher
    server_mod._state.audio_session.state = "running"
    await server_mod._handle_start_audio(
        fake_ws, StartAudioSessionRequest(id=2, backend="mock", setlist_content=content)
    )
    assert server_mod._state.matcher is first


@pytest.mark.asyncio
async def test_a_starting_service_session_is_not_clobbered(fake_capture, fake_ws):
    """The contention guard has to count a session that is still *starting*.

    `AudioSession.start()` returns as soon as the worker thread is spawned;
    `is_running` only goes true later, once that thread has opened the
    capture device. The guard read `is_running`, so for the whole of that
    window a second start passed it and replaced `_state.matcher`.

    Reproduced against a live sidecar: firing the debug panel's
    transcript-only start 0-500 ms after a service start left `matcher`
    None every time. Transcripts flowed, so the app looked alive, while the
    belief panel sat on "waiting for the matcher" and no slide ever moved.
    At a 3 s gap the same pair behaved correctly — which is exactly why this
    only bit on a cold app start, when loading Whisper widens the window.
    """
    from auto_presenter.protocol import StartAudioSessionRequest

    await server_mod._handle_start_audio(
        fake_ws,
        StartAudioSessionRequest(
            id=1,
            backend="mock",
            setlist_content=_setlist_content((0, "song_a", [["amazing", "grace"]])),
            ai_mode="active",
        ),
    )
    matcher = server_mod._state.matcher
    assert matcher is not None
    # The window under test: session exists, capture thread has not yet
    # reported "running". Deliberately NOT forced to "running" — that is the
    # state the old guard was blind to.
    server_mod._state.audio_session.state = "starting"

    await server_mod._handle_start_audio(
        fake_ws, StartAudioSessionRequest(id=2, backend="mock")
    )

    assert server_mod._state.matcher is matcher, (
        "a transcript-only start destroyed a service session that was still starting"
    )
    # And the loser is told, rather than being left to believe it won.
    sent = [c.args[0] for c in fake_ws.send.call_args_list]
    assert any("already running" in s for s in sent)


@pytest.mark.asyncio
async def test_a_service_session_still_preempts_a_starting_transcript_session(
    fake_capture, fake_ws
):
    """The ranking must survive the fix: counting "starting" as live must not
    also block the service session from taking over a transcript-only one."""
    from auto_presenter.protocol import StartAudioSessionRequest

    await server_mod._handle_start_audio(
        fake_ws, StartAudioSessionRequest(id=1, backend="mock")
    )
    assert server_mod._state.matcher is None
    server_mod._state.audio_session.state = "starting"

    await server_mod._handle_start_audio(
        fake_ws,
        StartAudioSessionRequest(
            id=2,
            backend="mock",
            setlist_content=_setlist_content((0, "song_a", [["amazing", "grace"]])),
            ai_mode="active",
        ),
    )
    assert server_mod._state.matcher is not None


@pytest.mark.asyncio
async def test_a_failing_side_observer_cannot_drop_the_slide_decision(monkeypatch):
    """The core path must survive an optional feature blowing up.

    The transcript handler computes the matcher's decision, then awaits four
    reporting/side steps, then applies it. An exception in any of those
    skipped `director.on_decision` entirely — so a spoken-instructions model
    returning a non-JSON body (an unauthenticated proxy's HTML error page)
    silently threw away a real slide change. `matcher.update` was already
    guarded against exactly this; the observers were not.

    Driven through `_make_transcript_handler` rather than the individual
    functions because the defect was the *ordering*, which only exists here.
    """
    from auto_presenter.asr.types import TranscriptEvent, Word
    from auto_presenter.matcher.types import MatcherDecision

    decision = MatcherDecision(item_idx=0, slide_idx=3, t_seconds=1.0, confidence=0.9)

    class _Matcher:
        current_item_idx = 0

        def update(self, event):
            return decision

    class _Director:
        def __init__(self):
            self.applied = []

        def on_decision(self, d):
            self.applied.append(d)

    director = _Director()
    handler = server_mod._make_transcript_handler(_Matcher(), director)

    async def boom(*a, **k):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(server_mod, "_broadcast_transcript_event", noop)
    monkeypatch.setattr(server_mod, "_broadcast_belief_for_engine", noop)
    monkeypatch.setattr(server_mod, "_observe_singing", noop)
    monkeypatch.setattr(server_mod, "_observe_spoken_instructions", boom)

    event = TranscriptEvent(
        words=[Word(text="hallelujah", t_start=0.0, t_end=1.0)],
        is_final=True,
        segment_id=1,
    )
    await handler(event)

    assert director.applied == [decision], (
        "a failing spoken-instructions call threw away the matcher's slide change"
    )


@pytest.mark.asyncio
async def test_mlx_backend_is_built_when_selected(fake_capture, fake_ws):
    """Apple-silicon path. Measured better than the portable default on both
    axes over the seven approved goldens — wrong-slide 0.330 -> 0.276 and
    median cue delay 1.4 s -> 0.8 s — so `recommend_model` now defaults to
    it when mlx-whisper is installed. This pins that selecting `backend=mlx`
    still constructs the Metal backend (here without Qwen3-ASR, the engine's
    fallback when Qwen isn't there or fails to load).
    """
    from auto_presenter.asr.mlx_backend import MLXWhisperBackend
    from auto_presenter.asr.qwen_backend import QwenStreamingBackend
    from auto_presenter.protocol import StartAudioSessionRequest

    async def already_downloaded(_size):
        return None

    with (
        patch.object(MLXWhisperBackend, "is_available", staticmethod(lambda: True)),
        patch.object(MLXWhisperBackend, "warm", lambda self: None),
        patch.object(QwenStreamingBackend, "is_available", staticmethod(lambda: False)),
        patch.object(server_mod, "_download_mlx_model", already_downloaded),
    ):
        await server_mod._handle_start_audio(
            fake_ws, StartAudioSessionRequest(id=1, backend="mlx", model_size="small.en")
        )

    session = server_mod._state.audio_session
    assert session is not None
    assert isinstance(session._wrapper.backend, MLXWhisperBackend)
    assert session._wrapper.backend.name() == "mlx-whisper-small.en"


@pytest.mark.asyncio
async def test_an_mlx_session_transcribes_with_qwen_by_default(fake_capture, fake_ws):
    """Qwen3-ASR is the on-device speech engine (2026-11-23): an mlx session
    with no tunables runs on it, with Whisper kept for the follower."""
    from auto_presenter.asr.mlx_backend import MLXWhisperBackend
    from auto_presenter.asr.qwen_backend import QwenStreamingBackend
    from auto_presenter.protocol import StartAudioSessionRequest

    async def already_downloaded(_size):
        return None

    with (
        patch.object(MLXWhisperBackend, "is_available", staticmethod(lambda: True)),
        patch.object(MLXWhisperBackend, "warm", lambda self: None),
        patch.object(QwenStreamingBackend, "is_available", staticmethod(lambda: True)),
        patch.object(QwenStreamingBackend, "warm", lambda self: None),
        patch.object(server_mod, "_download_mlx_model", already_downloaded),
    ):
        await server_mod._handle_start_audio(
            fake_ws, StartAudioSessionRequest(id=1, backend="mlx", model_size="small.en")
        )

    session = server_mod._state.audio_session
    assert session is not None
    assert isinstance(session._wrapper.backend, QwenStreamingBackend)


@pytest.mark.asyncio
async def test_mlx_without_the_extra_installed_errors_rather_than_crashing(
    fake_capture, fake_ws
):
    """`mlx_whisper` is an optional extra and does not exist off Apple
    silicon. Selecting it there must say so, not raise ImportError out of the
    start handler — the same shape as Groq's missing-key path."""
    from auto_presenter.asr.mlx_backend import MLXWhisperBackend
    from auto_presenter.protocol import StartAudioSessionRequest

    sent_errors = []

    async def capture_error(_ws, _id, message):
        sent_errors.append(message)

    with (
        patch.object(MLXWhisperBackend, "is_available", staticmethod(lambda: False)),
        patch.object(server_mod, "_send_error", capture_error),
    ):
        await server_mod._handle_start_audio(
            fake_ws, StartAudioSessionRequest(id=1, backend="mlx", model_size="small.en")
        )

    assert len(sent_errors) == 1
    assert "mlx" in sent_errors[0].lower()
    assert server_mod._state.audio_session is None


@pytest.mark.asyncio
async def test_mlx_start_without_the_model_offline_fails_and_says_why(
    fake_capture, fake_ws, tmp_path, monkeypatch
):
    """It used to start anyway: warm() swallowed the failed download, the
    session came up, and every pass returned nothing — "AI: ready" with
    nothing ever tracked. It must fail the start with a reason instead."""
    import huggingface_hub

    from auto_presenter.asr.mlx_backend import MLXWhisperBackend

    class LocalEntryNotFoundError(Exception):
        pass

    def offline(**_kw):
        raise LocalEntryNotFoundError("no connection")

    progress, errors = [], []

    async def capture_progress(event):
        progress.append(event)

    async def capture_error(_ws, _id, message):
        errors.append(message)

    monkeypatch.setattr(server_mod._state, "model_manager", ModelManager(cache_dir=tmp_path))
    monkeypatch.setattr(huggingface_hub, "snapshot_download", offline)
    monkeypatch.setattr(server_mod, "_broadcast_model_progress", capture_progress)
    monkeypatch.setattr(server_mod, "_send_error", capture_error)
    with patch.object(MLXWhisperBackend, "is_available", staticmethod(lambda: True)):
        await server_mod._handle_start_audio(
            fake_ws, StartAudioSessionRequest(id=1, backend="mlx", model_size="small.en")
        )

    assert server_mod._state.audio_session is None
    assert [e.state for e in progress] == ["starting", "failed"]
    assert "internet connection" in progress[-1].detail
    assert errors and "internet connection" in errors[0]


async def test_doubtful_words_are_kept_from_the_matcher_but_not_from_the_rest(
    monkeypatch,
):
    """Traced 2026-09-29: background talk ("Thank", probability 0.004) at the
    start of a session moved a slide. With nobody established as singing, a
    near-zero-probability word is kept from the matcher — but the singing
    detector and spoken instructions still see it."""
    from auto_presenter.asr.types import TranscriptEvent, Word

    matcher_saw: list[list[str]] = []
    observers_saw: list[list[str]] = []

    class _Matcher:
        current_item_idx = 0

        def update(self, event):
            matcher_saw.append([w.text for w in event.words])
            return None

    async def noop(*a, **k):
        return None

    async def spy(_matcher, _director, event):
        observers_saw.append([w.text for w in event.words])

    monkeypatch.setattr(server_mod, "_broadcast_transcript_event", noop)
    monkeypatch.setattr(server_mod, "_broadcast_belief_for_engine", noop)
    monkeypatch.setattr(server_mod, "_observe_singing", noop)
    monkeypatch.setattr(server_mod, "_observe_spoken_instructions", spy)
    monkeypatch.setattr(server_mod, "observe_off_setlist", noop)
    server_mod._state.singing.reset()

    handler = server_mod._make_transcript_handler(_Matcher(), MagicMock())
    await handler(TranscriptEvent(
        words=[
            Word(text="Thank", t_start=0.0, t_end=0.4, confidence=0.004),
            Word(text="you", t_start=0.4, t_end=0.8, confidence=0.97),
        ],
        is_final=True,
    ))

    assert matcher_saw == [["you"]]
    assert observers_saw == [["Thank", "you"]]


async def test_an_event_of_only_doubtful_words_never_reaches_the_matcher(monkeypatch):
    """Passed on word-less, it still ran the scheduler — and at session start
    the untouched prior reads as certain of the first lyric slide."""
    from auto_presenter.asr.types import TranscriptEvent, Word

    calls: list[int] = []

    class _Matcher:
        current_item_idx = 0

        def update(self, event):
            calls.append(len(event.words))
            return None

    async def noop(*a, **k):
        return None

    for name in ("_broadcast_transcript_event", "_broadcast_belief_for_engine",
                 "_observe_singing",
                 "_observe_spoken_instructions", "observe_off_setlist"):
        monkeypatch.setattr(server_mod, name, noop)
    server_mod._state.singing.reset()

    handler = server_mod._make_transcript_handler(_Matcher(), MagicMock())
    await handler(TranscriptEvent(
        words=[Word(text="Thank", t_start=0.0, t_end=0.4, confidence=0.004)],
        is_final=True,
    ))
    assert calls == []


@pytest.mark.parametrize(("verdict", "mode"), [("insufficient", "off"), ("full", "active")])
@pytest.mark.asyncio
async def test_a_session_starts_with_live_ai_only_where_it_was_earned(
    fake_capture, fake_ws, tmp_path, monkeypatch, verdict, mode
):
    """Off Apple silicon a session asked to start with live AI on gets it
    only on a computer that passed its machine check. Starting active used to
    skip the gate that switching it on later went through: the pill said
    unavailable while the engine moved slides."""
    from auto_presenter.asr import machine_check as mc
    from auto_presenter.protocol import StartAudioSessionRequest

    monkeypatch.setattr(server_mod.MLXWhisperBackend, "is_available", staticmethod(lambda: False))
    monkeypatch.setattr(server_mod._state, "library_db_path", tmp_path / "library.db")
    monkeypatch.setattr("auto_presenter.follower.scorer_models.is_downloaded", lambda *a: True)
    mc.save(mc.Assessment(verdict, "whisper", "why", 0.7), tmp_path / "machine-check.json")

    def fake_loader(size: str, *, progress_callback=None):
        return MockASRBackend(schedule=[], backend_name=f"fake-whisper-{size}")

    with patch.object(server_mod, "_get_or_load_whisper_backend", fake_loader):
        await server_mod._handle_start_audio(
            fake_ws,
            StartAudioSessionRequest(
                id=1, backend="whisper", model_size="small.en", ai_mode="active",
                setlist_content=_setlist_content((0, "song_a", [["amazing", "grace"]])),
            ),
        )
    try:
        assert server_mod._state.director is not None
        assert server_mod._state.director.mode == mode
    finally:
        # this session builds score followers (the CPU path): none may
        # outlive the test (they took over scrolling in later tests)
        await server_mod._stop_audio_session()


@pytest.mark.asyncio
async def test_a_microphone_that_never_stops_does_not_freeze_the_engine(monkeypatch):
    """PortAudio's stop deadlocked in CoreAudio once (2026-10-22). Run on the
    event loop, that hung every later request; now the engine carries on."""
    import asyncio
    import threading

    release = threading.Event()

    class StuckSession:
        def stop(self) -> None:
            release.wait(5)

        def join(self, timeout=None) -> None:
            pass

    monkeypatch.setattr(server_mod, "_AUDIO_STOP_SECONDS", 0.2)
    server_mod._state.audio_session = StuckSession()
    ticks = 0

    async def heartbeat() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.02)

    beat = asyncio.create_task(heartbeat())
    try:
        await asyncio.wait_for(server_mod._stop_audio_session(), 3)
    finally:
        beat.cancel()
        release.set()
    assert server_mod._state.audio_session is None
    assert ticks > 3  # the loop kept running while the stop was stuck


async def test_a_replaced_sessions_late_error_is_not_broadcast(monkeypatch):
    """A microphone open that hung is abandoned when its session is replaced;
    when it finally fails, the app must not read it as the new session
    failing (2026-10-26: it ended a service running fine on its replacement)."""
    sent = []

    async def capture_state(update, setlist_id=None):
        sent.append(update.state)

    monkeypatch.setattr(server_mod, "_broadcast_state_update", capture_state)
    monkeypatch.setattr(server_mod._state, "session_generation", 1)
    old = server_mod._session_state_handler(None, None, generation=1)
    await old(StateUpdate(state="starting"))
    server_mod._state.session_generation = 2  # replaced
    current = server_mod._session_state_handler(None, None, generation=2)
    await current(StateUpdate(state="running"))
    await old(StateUpdate(state="error", error="capture start failed: PortAudio -9986"))
    assert sent == ["starting", "running"]
    await current(StateUpdate(state="error", error="device gone"))
    assert sent == ["starting", "running", "error"]
