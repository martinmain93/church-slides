"""Server-side dispatch tests for the Golden Recording Editor handlers."""

from __future__ import annotations

import json
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from auto_presenter import server as server_mod
from auto_presenter import server_recordings
from auto_presenter.eval.types import (
    GroundTruthEvent,
    Recording,
    SetlistItemSnapshot,
)
from auto_presenter.protocol import (
    InspectMusicRequest,
    LoadRecordingAudioRequest,
    LoadRecordingRequest,
    ReplayRecordingRequest,
    SaveGoldenRequest,
)
from auto_presenter.recording.golden import load_golden


@pytest.fixture()
def fake_ws():
    ws = MagicMock()
    sent: list[dict] = []

    async def _send(data):
        sent.append(json.loads(data))

    ws.send = MagicMock(side_effect=_send)
    ws.sent = sent
    return ws


@pytest.fixture()
def recordings_root(tmp_path: Path):
    original = server_mod._state.recordings_root
    server_mod._state.recordings_root = tmp_path
    yield tmp_path
    server_mod._state.recordings_root = original


def _make_recording_dir(root: Path, rec_id: str, gt: list[GroundTruthEvent]) -> Path:
    rec_dir = root / rec_id
    rec_dir.mkdir(parents=True)
    rec = Recording(
        id=rec_id,
        setlist_id="sl_test",
        started_at_ms=1000,
        duration_seconds=1.0,
        ground_truth=gt,
    )
    (rec_dir / "recording.json").write_text(rec.model_dump_json(indent=2))
    with wave.open(str(rec_dir / "audio.wav"), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(np.zeros(16000, dtype=np.int16).tobytes())
    return rec_dir


def _make_replayable_recording_dir(root: Path, rec_id: str) -> Path:
    """Like `_make_recording_dir`, plus the setlist snapshot the replay
    handler requires before it will build a matcher at all."""
    rec_dir = _make_recording_dir(
        root, rec_id, [GroundTruthEvent(t_seconds=0.1, item_idx=0, slide_idx=1)]
    )
    rec = Recording(
        id=rec_id,
        setlist_id="sl_test",
        started_at_ms=1000,
        duration_seconds=1.0,
        ground_truth=[GroundTruthEvent(t_seconds=0.1, item_idx=0, slide_idx=1)],
        audio_path=str(rec_dir / "audio.wav"),
        setlist_content=[
            SetlistItemSnapshot(
                item_idx=0, song_id="song_a", slides=[[], ["amazing", "grace"]]
            )
        ],
    )
    (rec_dir / "recording.json").write_text(rec.model_dump_json(indent=2))
    return rec_dir

@pytest.mark.asyncio
async def test_load_recording_returns_metadata(fake_ws, recordings_root):
    _make_recording_dir(
        recordings_root, "rec_1", [GroundTruthEvent(t_seconds=2.0, item_idx=0, slide_idx=1)]
    )
    await server_mod._handle_load_recording(
        fake_ws, LoadRecordingRequest(id=1, recording_id="rec_1")
    )
    reply = fake_ws.sent[-1]
    assert reply["kind"] == "load_recording_response"
    assert reply["error"] is None
    assert reply["recording"]["ground_truth"][0]["t_seconds"] == 2.0
    assert reply["golden_ground_truth"] is None


@pytest.mark.asyncio
async def test_load_recording_unknown_id_errors(fake_ws, recordings_root):
    await server_mod._handle_load_recording(
        fake_ws, LoadRecordingRequest(id=1, recording_id="nope")
    )
    reply = fake_ws.sent[-1]
    assert reply["recording"] is None
    assert "unknown recording" in reply["error"]


@pytest.mark.asyncio
async def test_load_recording_audio_returns_b64(fake_ws, recordings_root):
    _make_recording_dir(recordings_root, "rec_2", [])
    await server_mod._handle_load_recording_audio(
        fake_ws, LoadRecordingAudioRequest(id=1, recording_id="rec_2")
    )
    reply = fake_ws.sent[-1]
    assert reply["kind"] == "load_recording_audio_response"
    assert reply["sample_rate"] == 16000
    assert abs(reply["duration_seconds"] - 1.0) < 1e-6
    assert reply["audio_base64"]


@pytest.mark.asyncio
async def test_load_recording_audio_missing_errors(fake_ws, recordings_root):
    await server_mod._handle_load_recording_audio(
        fake_ws, LoadRecordingAudioRequest(id=1, recording_id="ghost")
    )
    reply = fake_ws.sent[-1]
    assert reply["audio_base64"] is None
    assert reply["error"]


@pytest.mark.asyncio
async def test_save_golden_writes_sibling_file(fake_ws, recordings_root):
    rec_dir = _make_recording_dir(
        recordings_root, "rec_3", [GroundTruthEvent(t_seconds=2.0, item_idx=0, slide_idx=0)]
    )
    await server_mod._handle_save_golden(
        fake_ws,
        SaveGoldenRequest(
            id=1,
            recording_id="rec_3",
            ground_truth=[
                GroundTruthEvent(t_seconds=5.0, item_idx=1, slide_idx=2),
            ],
            notes="corrected",
        ),
    )
    reply = fake_ws.sent[-1]
    assert reply["kind"] == "save_golden_response"
    assert reply["saved"] is True

    golden = load_golden(rec_dir)
    assert golden is not None
    assert golden.ground_truth[0].item_idx == 1
    assert golden.notes == "corrected"
    # recording.json untouched.
    raw = json.loads((rec_dir / "recording.json").read_text())
    assert raw["ground_truth"][0]["t_seconds"] == 2.0


@pytest.mark.asyncio
async def test_save_golden_unknown_id_errors(fake_ws, recordings_root):
    await server_mod._handle_save_golden(
        fake_ws,
        SaveGoldenRequest(id=1, recording_id="missing", ground_truth=[]),
    )
    reply = fake_ws.sent[-1]
    assert reply["saved"] is False
    assert "unknown recording" in reply["error"]


@pytest.mark.asyncio
async def test_replay_traces_with_the_requested_margin(
    fake_ws, recordings_root, monkeypatch
):
    """The Model Inspector replays the engine the app runs (`trace_recording`
    builds it with `decision.production.build_engine`); the request only sets
    the scheduler's margin."""
    _make_replayable_recording_dir(recordings_root, "rec_replay")
    seen: dict = {}

    def fake_trace(recording, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(samples=[], decisions=[], notes="")

    monkeypatch.setattr("auto_presenter.eval.trace.trace_recording", fake_trace)
    await server_recordings._handle_replay_recording(
        fake_ws, ReplayRecordingRequest(id=1, recording_id="rec_replay", target_margin_ms=250)
    )
    assert seen == {"target_margin_ms": 250}


@pytest.mark.asyncio
async def test_inspect_music_returns_layers(fake_ws, recordings_root, monkeypatch):
    (recordings_root / "rec_music").mkdir()

    def fake_iter(_recording_dir):
        yield {
            "duration_seconds": 4.0,
            "beats": [0.5, 1.0],
            "downbeats": [0.5],
            "beat_backend": "beat_this",
            "chords": [{"name": "G", "t_start": 0.0, "t_end": 4.0}],
            "chord_backend": "btc",
            "pulse": [{"t_seconds": 1.0, "bpm": 120.0, "lock": 0.8}],
            "onsets": [{"band": "kick", "hz_lo": 30.0, "hz_hi": 90.0, "times": [0.5]}],
            "pitch": [{"t_seconds": 1.0, "midi": 67.0, "confidence": 0.4}],
            "notes": [{"t_start": 0.8, "t_end": 1.4, "midi": 67, "confidence": 0.6}],
            "pitch_backend": "basic_pitch",
            "partial": False,
            "through_seconds": 4.0,
        }

    monkeypatch.setattr("auto_presenter.music.inspect.iter_analyse", fake_iter)
    await server_recordings._handle_inspect_music(
        fake_ws, InspectMusicRequest(id=7, recording_id="rec_music")
    )
    reply = fake_ws.sent[-1]
    assert reply["kind"] == "inspect_music_response"
    assert reply["beat_backend"] == "beat_this"
    assert reply["beats"] == [0.5, 1.0]
    assert reply["chords"][0]["name"] == "G"
    assert reply["onsets"][0]["band"] == "kick"
    assert reply["pitch"][0]["midi"] == 67.0
    assert reply["error"] is None
    assert reply["source"] == "mix"


@pytest.mark.asyncio
async def test_inspect_music_stems_uses_the_stem_iterator(
    fake_ws, recordings_root, monkeypatch
):
    (recordings_root / "rec_music").mkdir()
    seen: dict[str, str] = {}

    def fake_mix(_recording_dir):
        seen["which"] = "mix"
        yield {"duration_seconds": 1.0, "partial": False, "through_seconds": 1.0}

    def fake_stems(_recording_dir):
        seen["which"] = "stems"
        yield {
            "duration_seconds": 1.0,
            "source": "stems",
            "stage": "analysing",
            "partial": False,
            "through_seconds": 1.0,
            "onsets": [{"band": "vocals", "hz_lo": 0.0, "hz_hi": 0.0, "times": []}],
        }

    monkeypatch.setattr("auto_presenter.music.inspect.iter_analyse", fake_mix)
    monkeypatch.setattr("auto_presenter.music.inspect.iter_analyse_stems", fake_stems)
    await server_recordings._handle_inspect_music(
        fake_ws, InspectMusicRequest(id=8, recording_id="rec_music", source="stems")
    )
    reply = fake_ws.sent[-1]
    assert seen["which"] == "stems"
    assert reply["source"] == "stems"
    assert reply["onsets"][0]["band"] == "vocals"


@pytest.mark.asyncio
async def test_inspect_music_replies_when_the_cache_is_malformed(
    fake_ws, recordings_root, monkeypatch
):
    rec = recordings_root / "rec_music"
    rec.mkdir()
    cache = rec / "music_layers.json"
    cache.write_text('{"version": 2}')

    def fake_iter(_recording_dir):
        yield {"chords": [{"name": "G"}]}

    monkeypatch.setattr("auto_presenter.music.inspect.iter_analyse", fake_iter)
    await server_recordings._handle_inspect_music(
        fake_ws, InspectMusicRequest(id=8, recording_id="rec_music")
    )
    reply = fake_ws.sent[-1]
    assert reply["error"]
    assert not cache.exists()


@pytest.mark.asyncio
async def test_inspect_music_waits_during_a_live_session(fake_ws, recordings_root):
    (recordings_root / "rec_music").mkdir()
    original = server_mod._state.audio_session
    server_mod._state.audio_session = SimpleNamespace(is_live=True)
    try:
        await server_recordings._handle_inspect_music(
            fake_ws, InspectMusicRequest(id=9, recording_id="rec_music")
        )
    finally:
        server_mod._state.audio_session = original
    reply = fake_ws.sent[-1]
    assert "live session" in reply["error"]
