"""Recording roundtrip + ground-truth iterator + Replayer tests."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from auto_presenter.eval.replay import (
    Replayer,
    iter_ground_truth,
    load_recording,
    save_recording,
)
from auto_presenter.eval.types import (
    DeviationMarker,
    GroundTruthEvent,
    Recording,
)


@pytest.fixture()
def sample_recording() -> Recording:
    return Recording(
        id="rec_001",
        setlist_id="sl_test",
        started_at_ms=1700000000000,
        duration_seconds=120.0,
        ground_truth=[
            GroundTruthEvent(t_seconds=0.0, item_idx=0, slide_idx=0),
            GroundTruthEvent(t_seconds=12.5, item_idx=0, slide_idx=1),
            GroundTruthEvent(t_seconds=30.0, item_idx=1, slide_idx=0),
        ],
        deviation_markers=[
            DeviationMarker(t_seconds=12.5, kind="chorus_repeat"),
        ],
        notes="example",
    )


def test_save_then_load_is_lossless(tmp_path: Path, sample_recording: Recording) -> None:
    path = tmp_path / "rec.json"
    save_recording(sample_recording, path)
    loaded = load_recording(path)
    assert loaded == sample_recording


def test_load_accepts_directory(tmp_path: Path, sample_recording: Recording) -> None:
    rec_dir = tmp_path / "rec_001"
    save_recording(sample_recording, rec_dir / "recording.json")
    # Pass the directory rather than the JSON file directly.
    loaded = load_recording(rec_dir)
    assert loaded == sample_recording


def test_iter_ground_truth_returns_chronological(sample_recording: Recording) -> None:
    # Shuffle the input to verify the iterator sorts.
    out_of_order = sample_recording.model_copy(
        update={"ground_truth": list(reversed(sample_recording.ground_truth))}
    )
    times = [g.t_seconds for g in iter_ground_truth(out_of_order)]
    assert times == sorted(times)


def test_save_creates_parent_dirs(tmp_path: Path, sample_recording: Recording) -> None:
    path = tmp_path / "deep" / "nested" / "rec.json"
    save_recording(sample_recording, path)
    assert path.exists()


# --- Replayer --------------------------------------------------------------


def _write_synthetic_wav(path: Path, *, seconds: float = 1.0) -> None:
    """Write a short int16 WAV at 16 kHz so we can exercise FileCapture."""
    rate = 16_000
    n = int(seconds * rate)
    samples = (np.sin(2 * np.pi * 220 * np.arange(n) / rate) * 0.2 * 32767).astype(
        np.int16
    )
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(samples.tobytes())


def test_replayer_yields_audio_frames_for_recording_with_wav(
    tmp_path: Path, sample_recording: Recording
) -> None:
    wav_path = tmp_path / "audio.wav"
    _write_synthetic_wav(wav_path, seconds=0.5)
    recording = sample_recording.model_copy(update={"audio_path": str(wav_path)})

    replayer = Replayer(recording, realtime=False)
    frames = list(replayer.audio_frames())

    assert frames, "expected at least one frame from a 0.5 s WAV"
    # 0.5 s of 16 kHz audio at 100 ms frame length = 5 frames.
    assert len(frames) == 5
    assert all(f.sample_rate == 16_000 for f in frames)
    assert frames[0].t_start == 0.0
    assert frames[-1].t_end > 0.0


def test_replayer_stream_is_alias_for_audio_frames(
    tmp_path: Path, sample_recording: Recording
) -> None:
    wav_path = tmp_path / "audio.wav"
    _write_synthetic_wav(wav_path, seconds=0.2)
    recording = sample_recording.model_copy(update={"audio_path": str(wav_path)})

    replayer = Replayer(recording, realtime=False)
    via_stream = list(replayer.stream())
    via_audio = list(Replayer(recording, realtime=False).audio_frames())
    assert len(via_stream) == len(via_audio)


def test_replayer_rejects_recording_without_audio(sample_recording: Recording) -> None:
    # sample_recording has no audio_path set.
    with pytest.raises(ValueError, match="audio_path"):
        Replayer(sample_recording)


def test_replayer_ground_truth_is_chronological(
    tmp_path: Path, sample_recording: Recording
) -> None:
    wav_path = tmp_path / "audio.wav"
    _write_synthetic_wav(wav_path, seconds=0.1)
    out_of_order = sample_recording.model_copy(
        update={
            "audio_path": str(wav_path),
            "ground_truth": list(reversed(sample_recording.ground_truth)),
        }
    )
    replayer = Replayer(out_of_order, realtime=False)
    times = [g.t_seconds for g in replayer.ground_truth()]
    assert times == sorted(times)
