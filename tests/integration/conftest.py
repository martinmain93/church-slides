"""Shared fixtures for cross-component integration tests.

Integration tests cross sidecar package boundaries (recording → eval →
ASR), so they live at the repo root rather than under the per-component
`sidecar/tests/<pkg>/`. The conftest:

- Adds `sidecar/` to `sys.path` so `auto_presenter.*` imports work.
- Provides a synthetic-WAV factory shared by every test.
- Provides a `make_recording_session` fixture that simulates a short
  service: it spins up a `RecordingSink`, feeds it real `AudioFrame`s
  from a generated WAV, records ground-truth slide changes, then
  finalizes it back into the format the eval harness consumes.
"""

from __future__ import annotations

import sys
import wave
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SIDECAR_ROOT = REPO_ROOT / "sidecar"
if str(SIDECAR_ROOT) not in sys.path:
    sys.path.insert(0, str(SIDECAR_ROOT))


from auto_presenter.audio.types import (  # noqa: E402
    DEFAULT_FRAME_LENGTH,
    SAMPLE_RATE,
    AudioFrame,
)
from auto_presenter.recording.sink import RecordingSink  # noqa: E402


def _write_wav(path: Path, samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> None:
    clipped = np.clip(samples, -1.0, 1.0)
    int16 = (clipped * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(int16.tobytes())


@pytest.fixture()
def synthetic_wav_factory(tmp_path: Path) -> Callable[[float, float], Path]:
    """Return a factory that writes a sine WAV of `seconds` to a new path.

    The 220 Hz tone is loud enough (0.3 amplitude) that an energy-based
    VAD treats it as active speech, which integration tests for the VAD
    pipeline rely on.
    """

    def _factory(seconds: float, frequency_hz: float = 220.0) -> Path:
        n = int(seconds * SAMPLE_RATE)
        t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
        samples = (0.3 * np.sin(2 * np.pi * frequency_hz * t)).astype(np.float32)
        path = tmp_path / f"synthetic_{seconds:.2f}s.wav"
        _write_wav(path, samples)
        return path

    return _factory


@pytest.fixture()
def make_recording_session(tmp_path: Path):
    """Build a fully-populated RecordingSink → finalized `Recording` workflow.

    Yields a `(setlist_id, ground_truth_events, audio_seconds) -> Recording`
    callable. The callable simulates a service:

      - Opens a RecordingSink in a fresh dir under `tmp_path`.
      - Writes `audio_seconds` of synthetic AudioFrames (220 Hz tone) to
        produce a real WAV the Replayer can consume downstream.
      - Records each ground-truth event onto the sink with `t_seconds`
        spaced out by the configured intervals (we don't bother
        synchronizing to monotonic time — the sink stamps events with
        its own monotonic offset, which is what the eval harness uses).
      - Closes the sink and returns the Recording metadata.

    Tests use this to get a "realistic" recording on disk without
    touching the actual UI / live audio stack.
    """

    def _make(
        *,
        setlist_id: str = "sl_integration",
        audio_seconds: float = 1.0,
        slide_changes: list[tuple[int, int]] | None = None,
    ):
        rec_dir = tmp_path / "rec_integration"
        sink = RecordingSink(
            recording_id="rec_integration",
            output_dir=rec_dir,
            setlist_id=setlist_id,
        )
        sink.open()

        n_samples_per_frame = DEFAULT_FRAME_LENGTH
        seconds_per_frame = n_samples_per_frame / SAMPLE_RATE
        n_frames = max(1, int(audio_seconds / seconds_per_frame))
        # Stable tone so any downstream VAD treats this as continuous voice.
        t = np.arange(n_samples_per_frame, dtype=np.float64) / SAMPLE_RATE
        chunk = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        for i in range(n_frames):
            frame = AudioFrame(
                samples=chunk.copy(),
                t_start=i * seconds_per_frame,
                t_end=(i + 1) * seconds_per_frame,
            )
            sink.write_frame(frame)

        if slide_changes:
            for item_idx, slide_idx in slide_changes:
                sink.record_slide_change(item_idx, slide_idx, source="manual")

        return sink.close()

    return _make
