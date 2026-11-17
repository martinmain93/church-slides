"""Dev-tools WS verbs are gated off unless `--dev-tools` is set."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from auto_presenter import server as server_mod
from auto_presenter.protocol import RecordSlideChangeNotification, StartAudioSessionRequest
from auto_presenter.server_state import _state


def _write_tone(path) -> None:
    import wave

    import numpy as np

    sr = 16_000
    t = np.linspace(0, 0.2, int(sr * 0.2), endpoint=False)
    samples = (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    int16 = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sr)
        wav.writeframes(int16.tobytes())


@pytest.fixture(autouse=True)
async def reset_dev_state():
    original = {
        "clients": _state.clients,
        "session": _state.audio_session,
        "matcher": _state.matcher,
        "director": _state.director,
        "dev_tools": _state.dev_tools,
        "last_start": _state.last_start_audio,
        "last_slide": _state.last_slide,
        "recordings_root": _state.recordings_root,
    }
    _state.clients = set()
    _state.audio_session = None
    _state.matcher = None
    _state.director = None
    _state.dev_tools = False
    _state.last_start_audio = None
    _state.last_slide = None
    _state.last_slide_source = None
    yield
    if _state.audio_session is not None:
        try:
            _state.audio_session.stop()
            _state.audio_session.join(timeout=0.5)
        except Exception:
            pass
        _state.audio_session = None
    _state.clients = original["clients"]
    _state.audio_session = original["session"]
    _state.matcher = original["matcher"]
    _state.director = original["director"]
    _state.dev_tools = original["dev_tools"]
    _state.last_start_audio = original["last_start"]
    _state.last_slide = original["last_slide"]
    _state.recordings_root = original["recordings_root"]
    from auto_presenter.dev import inject

    inject.clear()
    await asyncio.sleep(0)


@pytest.fixture()
def fake_capture():
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
    sent: list[dict] = []
    ws = MagicMock()

    async def _send(data: str) -> None:
        sent.append(json.loads(data))

    ws.send = MagicMock(side_effect=_send)
    ws.sent = sent
    return ws


def _kinds(ws) -> list[str]:
    return [m.get("kind") for m in ws.sent]


@pytest.mark.asyncio
async def test_inject_rejected_when_dev_tools_off(fake_ws):
    await server_mod._dispatch(
        fake_ws,
        json.dumps({"kind": "inject_audio", "id": 1, "wav_path": "/tmp/x.wav"}),
    )
    assert _kinds(fake_ws) == ["error"]
    assert "invalid message" in fake_ws.sent[0]["message"]


@pytest.mark.asyncio
async def test_inject_requires_a_prior_session(fake_ws):
    _state.dev_tools = True
    await server_mod._dispatch(
        fake_ws,
        json.dumps({"kind": "inject_audio", "id": 7, "wav_path": "/nope.wav"}),
    )
    assert fake_ws.sent[-1]["kind"] == "inject_audio_response"
    assert fake_ws.sent[-1]["error"]
    assert "start presenting" in fake_ws.sent[-1]["error"]


@pytest.mark.asyncio
async def test_inject_replaces_mic_with_file(fake_capture, fake_ws, tmp_path):
    wav = tmp_path / "song.wav"
    _write_tone(wav)
    _state.dev_tools = True
    await server_mod._handle_start_audio(
        fake_ws, StartAudioSessionRequest(id=1, backend="mock")
    )
    fake_ws.sent.clear()
    await server_mod._dispatch(
        fake_ws,
        json.dumps(
            {
                "kind": "inject_audio",
                "id": 2,
                "wav_path": str(wav),
                "realtime": False,
            }
        ),
    )
    reply = fake_ws.sent[-1]
    assert reply["kind"] == "inject_audio_response"
    assert reply["error"] is None
    assert reply["wav_path"] == str(wav.resolve())
    from auto_presenter.audio.capture import FileCapture

    assert isinstance(_state.audio_session._capture, FileCapture)


@pytest.mark.asyncio
async def test_inject_can_start_its_own_session_without_a_mic(fake_capture, fake_ws, tmp_path):
    """scripts/perf_report.py on a machine with no audio device: the file is
    the capture from the start, and PortAudio is never opened."""
    wav = tmp_path / "song.wav"
    _write_tone(wav)
    _state.dev_tools = True
    await server_mod._dispatch(
        fake_ws,
        json.dumps(
            {
                "kind": "inject_audio",
                "id": 3,
                "wav_path": str(wav),
                "realtime": False,
                "session": {"kind": "start_audio_session", "backend": "mock"},
            }
        ),
    )
    reply = fake_ws.sent[-1]
    assert reply["kind"] == "inject_audio_response"
    assert reply["error"] is None
    from auto_presenter.audio.capture import FileCapture

    assert isinstance(_state.audio_session._capture, FileCapture)
    fake_capture.assert_not_called()


@pytest.mark.asyncio
async def test_snapshot_reports_displayed_slide(fake_ws):
    _state.dev_tools = True
    server_mod._handle_slide_change(
        RecordSlideChangeNotification(item_idx=1, slide_idx=3, source="manual")
    )
    await server_mod._dispatch(fake_ws, json.dumps({"kind": "session_snapshot", "id": 3}))
    snap = fake_ws.sent[-1]["snapshot"]
    assert snap["displayed"] == {"item_idx": 1, "slide_idx": 3, "source": "manual"}
    assert snap["session"] is None


@pytest.mark.asyncio
async def test_wait_for_slide_already_there(fake_ws):
    _state.dev_tools = True
    _state.last_slide = (0, 2)
    await server_mod._dispatch(
        fake_ws,
        json.dumps(
            {
                "kind": "wait_for_slide",
                "id": 4,
                "item_idx": 0,
                "slide_idx": 2,
                "timeout_seconds": 0.2,
            }
        ),
    )
    reply = fake_ws.sent[-1]
    assert reply["kind"] == "wait_for_slide_response"
    assert reply["reached"] is True


@pytest.mark.asyncio
async def test_wait_for_slide_times_out(fake_ws):
    _state.dev_tools = True
    _state.last_slide = (0, 0)
    await server_mod._dispatch(
        fake_ws,
        json.dumps(
            {
                "kind": "wait_for_slide",
                "id": 5,
                "item_idx": 9,
                "slide_idx": 9,
                "timeout_seconds": 0.15,
            }
        ),
    )
    reply = fake_ws.sent[-1]
    assert reply["reached"] is False
    assert reply["error"] == "timeout"


@pytest.mark.asyncio
async def test_a_follower_that_fails_to_start_leaves_the_matcher_running(
    fake_capture, fake_ws, tmp_path
):
    """A broken lead sheet (or anything else the score follower trips on)
    must cost the follower only: the session runs on the production engine."""
    wav = tmp_path / "song.wav"
    _write_tone(wav)
    _state.dev_tools = True
    session = {
        "kind": "start_audio_session",
        "backend": "mock",
        "setlist_content": {
            "items": [
                {"item_idx": 0, "song_id": "s", "slides": [[], ["amazing", "grace"]],
                 "slide_sections": ["other:0", "verse:1"]}
            ]
        },
    }
    with patch.object(server_mod, "build_session_followers", side_effect=RuntimeError("bad sheet")):
        await server_mod._dispatch(
            fake_ws,
            json.dumps({"kind": "inject_audio", "id": 5, "wav_path": str(wav),
                        "realtime": False, "session": session}),
        )
    assert not [m for m in fake_ws.sent if m.get("kind") == "error"]
    assert _state.matcher is not None
    assert _state.followers is None
