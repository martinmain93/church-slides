"""A session start that fails must say so on the request that asked for it.

The presentation view learns whether the AI started from the reply to its
`start_audio_session` request. A microphone that failed to open used to be
reported only as an `audio_session_state` broadcast after the request had
already succeeded, so the pill sat on "AI: starting" for the whole service.
Around that: the device list must rescan after such a failure (a newly
plugged mic is the usual fix), an unexpected handler error must still answer
its request, and a recording opened for a start that then fails is closed.
"""

from __future__ import annotations

import asyncio
import json
import threading
from unittest.mock import MagicMock, patch

import pytest

from auto_presenter import server as server_mod
from auto_presenter.audio import devices as devices_mod
from auto_presenter.protocol import StartAudioSessionRequest


@pytest.fixture(autouse=True)
def reset_server_state():
    saved = {
        name: getattr(server_mod._state, name)
        for name in (
            "clients",
            "audio_session",
            "matcher",
            "director",
            "last_start_audio",
            "active_recording",
            "recordings_root",
        )
    }
    server_mod._state.clients = set()
    server_mod._state.audio_session = None
    server_mod._state.matcher = None
    server_mod._state.director = None
    server_mod._state.active_recording = None
    yield
    session = server_mod._state.audio_session
    if session is not None:
        session.stop()
        session.join(timeout=1.0)
    for name, value in saved.items():
        setattr(server_mod._state, name, value)
    # asyncio locks bind to the loop they were first contended on; each test
    # has its own loop.
    server_mod._whisper_load_async_locks.clear()
    server_mod._mlx_download_locks.clear()


@pytest.fixture()
def sent():
    """A fake socket that keeps every message sent to it."""
    messages: list[dict] = []
    ws = MagicMock()

    async def send(data):
        messages.append(json.loads(data))

    ws.send = MagicMock(side_effect=send)
    return ws, messages


@pytest.fixture()
def rescans(monkeypatch):
    calls: list[None] = []
    monkeypatch.setattr(devices_mod, "rescan_input_devices", lambda: calls.append(None))
    return calls


def _capture(*, fails: bool = False, hold: threading.Event | None = None):
    instance = MagicMock()
    if fails:
        instance.start = MagicMock(side_effect=RuntimeError("Error opening InputStream"))

    def frames():
        if hold is not None:
            hold.wait(5.0)
        return
        yield  # pragma: no cover - makes this a generator

    instance.stream = MagicMock(side_effect=lambda: frames())
    instance.stop = MagicMock(side_effect=lambda: hold.set() if hold else None)
    return MagicMock(return_value=instance)


@pytest.mark.asyncio
async def test_a_microphone_that_fails_to_open_fails_the_start_request(sent, rescans):
    ws, messages = sent
    with patch.object(server_mod, "LiveCapture", _capture(fails=True)):
        await server_mod._handle_start_audio(
            ws, StartAudioSessionRequest(id=1, backend="mock")
        )
    replies = [m for m in messages if m.get("id") == 1]
    assert [m["kind"] for m in replies] == ["error"]
    assert "Error opening InputStream" in replies[0]["message"]
    # Devices are rescanned before the mic is opened: one plugged in since
    # the engine started is otherwise invisible to PortAudio.
    assert len(rescans) == 1


@pytest.mark.asyncio
async def test_after_a_failed_mic_the_device_list_rescans_and_retry_opens_it(
    sent, rescans, monkeypatch
):
    ws, messages = sent
    with patch.object(server_mod, "LiveCapture", _capture(fails=True)):
        await server_mod._handle_start_audio(
            ws, StartAudioSessionRequest(id=1, backend="mock")
        )
    # The failed session is torn down at once, recording and all.
    assert server_mod._state.audio_session is None
    assert server_mod._state.active_recording is None

    asked: list[bool] = []
    monkeypatch.setattr(
        devices_mod, "list_input_devices", lambda *, rescan=False: asked.append(rescan) or []
    )
    await server_mod._dispatch(ws, json.dumps({"kind": "list_audio_devices", "id": 2}))
    assert asked == [True]

    hold = threading.Event()
    with patch.object(server_mod, "LiveCapture", _capture(hold=hold)):
        await server_mod._handle_start_audio(
            ws, StartAudioSessionRequest(id=3, backend="mock")
        )
    assert [m["kind"] for m in messages if m.get("id") == 3] == ["pong"]
    assert len(rescans) == 2

    # And never while a stream is open.
    await server_mod._dispatch(ws, json.dumps({"kind": "list_audio_devices", "id": 4}))
    assert asked == [True, False]
    hold.set()


@pytest.mark.asyncio
async def test_an_unexpected_handler_error_still_answers_the_request(sent, monkeypatch):
    ws, messages = sent

    async def boom(_ws, _raw):
        raise ValueError("kaboom")

    monkeypatch.setattr(server_mod, "_dispatch", boom)
    await server_mod._dispatch_logged(ws, json.dumps({"kind": "start_audio_session", "id": 7}))
    assert len(messages) == 1
    assert messages[0]["kind"] == "error" and messages[0]["id"] == 7
    assert messages[0]["message"] == "internal error: kaboom"


@pytest.mark.asyncio
async def test_a_recording_opened_before_the_matcher_fails_is_closed(
    sent, tmp_path, monkeypatch
):
    from auto_presenter.protocol import SetlistContent, SetlistItemContent

    ws, messages = sent
    server_mod._state.recordings_root = tmp_path

    def broken_index(_content):
        raise RuntimeError("bad setlist")

    monkeypatch.setattr(server_mod, "build_index", broken_index)
    content = SetlistContent(
        items=[SetlistItemContent(item_idx=0, song_id="s", slides=[["amazing", "grace"]])]
    )
    with patch.object(server_mod, "LiveCapture", _capture()):
        await server_mod._handle_start_audio(
            ws,
            StartAudioSessionRequest(
                id=1, backend="mock", record=True, setlist_id="set", setlist_content=content
            ),
        )
    assert "matcher init failed" in messages[-1]["message"]
    assert server_mod._state.active_recording is None
    assert list(tmp_path.iterdir()) == []


def test_editor_and_settings_requests_do_not_queue_behind_a_session_start():
    for kind in (
        "get_learning_state",
        "delete_recording",
        "delete_performance",
        "inspect_lookback",
        "save_golden",
        "set_spoken_instructions",
        "set_instruction_model",
        "set_instruction_model_api_key",
        "set_blank_when_idle",
        "set_music_analysis",
    ):
        assert kind in server_mod.CONCURRENT_KINDS, kind
    # These change what a session start reads or builds, so they stay in order.
    for kind in ("start_audio_session", "stop_audio_session", "set_tunables"):
        assert kind not in server_mod.CONCURRENT_KINDS, kind


@pytest.mark.asyncio
async def test_queued_model_loads_wait_on_the_loop_not_in_a_worker_thread(monkeypatch):
    """Each "Download now" during a first download used to park an executor
    thread for the whole download."""
    release = threading.Event()
    entered: list[str] = []

    def slow_load(model_size, *, progress_callback=None):
        entered.append(model_size)
        release.wait(5.0)
        return object()

    monkeypatch.setattr(server_mod, "_get_or_load_whisper_backend", slow_load)
    tasks = [
        asyncio.create_task(server_mod._load_whisper_backend_async("small.en"))
        for _ in range(3)
    ]
    for _ in range(50):
        if entered:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    assert entered == ["small.en"]  # the others wait on the asyncio lock
    release.set()
    await asyncio.gather(*tasks)
    assert len(entered) == 3


def _service_start(**kw) -> StartAudioSessionRequest:
    from auto_presenter.protocol import SetlistContent, SetlistItemContent

    content = SetlistContent(
        items=[SetlistItemContent(item_idx=0, song_id="s", slides=[["amazing", "grace"]])]
    )
    return StartAudioSessionRequest(
        id=1, backend="mock", setlist_id="set", setlist_content=content, **kw
    )


@pytest.mark.asyncio
async def test_a_later_start_step_that_raises_leaves_no_recording_or_half_session(
    sent, tmp_path, monkeypatch
):
    ws, _messages = sent
    server_mod._state.recordings_root = tmp_path

    def broken_search():
        raise RuntimeError("library search exploded")

    monkeypatch.setattr(server_mod, "prepare_library_search", broken_search)
    with patch.object(server_mod, "LiveCapture", _capture()):
        with pytest.raises(RuntimeError, match="library search exploded"):
            await server_mod._handle_start_audio(ws, _service_start(record=True))
    assert server_mod._state.active_recording is None
    assert server_mod._state.matcher is None
    assert server_mod._state.director is None
    assert server_mod._state.audio_session is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_a_failure_after_the_session_started_stops_it(sent, tmp_path, monkeypatch):
    ws, _messages = sent
    server_mod._state.recordings_root = tmp_path
    hold = threading.Event()

    async def broken_wait(_session):
        raise RuntimeError("wait exploded")

    monkeypatch.setattr(server_mod, "_wait_for_capture", broken_wait)
    with patch.object(server_mod, "LiveCapture", _capture(hold=hold)):
        with pytest.raises(RuntimeError, match="wait exploded"):
            await server_mod._handle_start_audio(ws, _service_start(record=True))
    assert server_mod._state.audio_session is None
    assert server_mod._state.active_recording is None
    assert hold.is_set()  # the capture was stopped
    # And through the dispatcher, the request still gets its answer.
    hold.clear()
    with patch.object(server_mod, "LiveCapture", _capture(hold=hold)):
        await server_mod._dispatch_logged(
            ws, _service_start(record=True).model_dump_json()
        )
    assert _messages[-1]["kind"] == "error" and "wait exploded" in _messages[-1]["message"]


@pytest.mark.asyncio
async def test_session_state_broadcasts_say_whose_session_it_is(sent, rescans):
    """The presentation view acts only on its own session's state; the debug
    panel's transcript-only stream failing must not read as the service's."""
    ws, messages = sent
    server_mod._state.clients = {ws}
    with patch.object(server_mod, "LiveCapture", _capture(fails=True)):
        await server_mod._handle_start_audio(ws, _service_start())
    await asyncio.sleep(0.05)  # the capture thread's broadcast hops the loop
    errors = [m for m in messages if m["kind"] == "audio_session_state" and m["state"] == "error"]
    assert errors and all(m["setlist_id"] == "set" for m in errors)

    messages.clear()
    with patch.object(server_mod, "LiveCapture", _capture(fails=True)):
        await server_mod._handle_start_audio(
            ws, StartAudioSessionRequest(id=2, backend="mock")
        )
    await asyncio.sleep(0.05)
    errors = [m for m in messages if m["kind"] == "audio_session_state" and m["state"] == "error"]
    assert errors and all(m["setlist_id"] is None for m in errors)


@pytest.mark.asyncio
async def test_a_session_that_fails_mid_service_does_not_block_the_rescan(
    sent, rescans, monkeypatch
):
    """A device unplugged mid-service ends the session after the start
    replied; it stays in `_state.audio_session`, which alone used to block
    every rescan for the rest of the presentation."""
    ws, _messages = sent
    hold = threading.Event()
    with patch.object(server_mod, "LiveCapture", _capture(hold=hold)):
        await server_mod._handle_start_audio(
            ws, StartAudioSessionRequest(id=1, backend="mock")
        )
    session = server_mod._state.audio_session
    session._fail("capture loop failed: device unplugged")
    session.join(timeout=1.0)
    asked: list[bool] = []
    monkeypatch.setattr(
        devices_mod, "list_input_devices", lambda *, rescan=False: asked.append(rescan) or []
    )
    await server_mod._dispatch(ws, json.dumps({"kind": "list_audio_devices", "id": 2}))
    assert asked == [True]


@pytest.mark.asyncio
async def test_a_failed_mic_leaves_no_recording_behind(sent, rescans, tmp_path):
    """Each Retry after a failed mic used to leave one more empty recording."""
    ws, _messages = sent
    server_mod._state.recordings_root = tmp_path
    for _ in range(3):
        with patch.object(server_mod, "LiveCapture", _capture(fails=True)):
            await server_mod._handle_start_audio(ws, _service_start(record=True))
    assert server_mod._state.active_recording is None
    assert server_mod._state.matcher is None and server_mod._state.director is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_stopping_a_session_with_no_audio_discards_its_recording(sent, tmp_path):
    from auto_presenter.protocol import StopAudioSessionRequest

    ws, _messages = sent
    server_mod._state.recordings_root = tmp_path
    hold = threading.Event()
    with patch.object(server_mod, "LiveCapture", _capture(hold=hold)):
        await server_mod._handle_start_audio(ws, _service_start(record=True))
    assert server_mod._state.active_recording is not None
    await server_mod._handle_stop_audio(ws, StopAudioSessionRequest(id=9))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_a_dropped_socket_during_the_start_reply_keeps_the_session(sent, monkeypatch):
    import websockets

    ws, _messages = sent
    hold = threading.Event()

    async def closed(_data):
        raise websockets.ConnectionClosedOK(None, None)

    ws.send = MagicMock(side_effect=closed)
    with patch.object(server_mod, "LiveCapture", _capture(hold=hold)):
        with pytest.raises(websockets.ConnectionClosed):
            await server_mod._handle_start_audio(
                ws, StartAudioSessionRequest(id=1, backend="mock")
            )
    session = server_mod._state.audio_session
    assert session is not None and session.is_live
    assert not hold.is_set()  # the capture was not stopped
    hold.set()


@pytest.mark.asyncio
async def test_stopping_a_session_detaches_its_score_followers(sent):
    from auto_presenter.protocol import StopAudioSessionRequest

    ws, _messages = sent
    hold = threading.Event()
    with patch.object(server_mod, "LiveCapture", _capture(hold=hold)):
        await server_mod._handle_start_audio(
            ws, StartAudioSessionRequest(id=1, backend="mock")
        )
    followers = MagicMock()
    server_mod._state.followers = followers
    await server_mod._handle_stop_audio(ws, StopAudioSessionRequest(id=2))
    followers.detach.assert_called_once()
    assert server_mod._state.followers is None


@pytest.mark.asyncio
async def test_a_cached_small_model_does_not_wait_for_a_big_download(monkeypatch):
    release = threading.Event()
    loaded: list[str] = []

    def load(model_size, *, progress_callback=None):
        if model_size == "large-v3":
            release.wait(5.0)
        loaded.append(model_size)
        return object()

    monkeypatch.setattr(server_mod, "_get_or_load_whisper_backend", load)
    big = asyncio.create_task(server_mod._load_whisper_backend_async("large-v3"))
    await asyncio.sleep(0.05)
    await asyncio.wait_for(server_mod._load_whisper_backend_async("small.en"), 1.0)
    assert loaded == ["small.en"]
    release.set()
    await big


@pytest.mark.asyncio
async def test_an_mlx_model_on_disk_does_not_wait_for_another_download(monkeypatch):
    monkeypatch.setattr(
        server_mod._state.model_manager,
        "is_downloaded",
        lambda size, backend="whisper": size == "small.en",
    )
    lock = server_mod._mlx_download_locks.setdefault("large-v3", asyncio.Lock())
    await lock.acquire()
    try:
        await asyncio.wait_for(server_mod._download_mlx_model("small.en"), 1.0)
    finally:
        lock.release()
