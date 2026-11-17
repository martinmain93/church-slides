"""FileCapture tests."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

from auto_presenter.audio.capture import FileCapture
from auto_presenter.audio.types import DEFAULT_FRAME_LENGTH, SAMPLE_RATE


def test_yields_frames_with_monotonic_timestamps(short_wav_16khz: Path) -> None:
    cap = FileCapture(short_wav_16khz, realtime=False)
    frames = list(cap.stream())
    assert frames, "expected at least one frame"
    last_t = -1.0
    for f in frames:
        assert f.t_start >= last_t
        assert f.t_end > f.t_start
        last_t = f.t_start


def test_each_frame_has_default_length(short_wav_16khz: Path) -> None:
    cap = FileCapture(short_wav_16khz, realtime=False)
    for frame in cap.stream():
        assert frame.n_samples == DEFAULT_FRAME_LENGTH
        assert frame.sample_rate == SAMPLE_RATE
        assert frame.samples.dtype == np.float32


def test_resampled_to_16khz(short_wav_44khz: Path) -> None:
    cap = FileCapture(short_wav_44khz, realtime=False)
    frames = list(cap.stream())
    # 0.5 s of 44.1 kHz → ~0.5 s of 16 kHz → ~8000 samples → ~5 default frames.
    total_samples = sum(f.n_samples for f in frames)
    # Allow a one-frame fudge for padding at the end.
    assert SAMPLE_RATE * 0.5 - DEFAULT_FRAME_LENGTH <= total_samples
    assert total_samples <= SAMPLE_RATE * 0.5 + DEFAULT_FRAME_LENGTH * 2
    for frame in frames:
        assert frame.sample_rate == SAMPLE_RATE


def test_stereo_downmixed_to_mono(stereo_wav_16khz: Path) -> None:
    cap = FileCapture(stereo_wav_16khz, realtime=False)
    for frame in cap.stream():
        assert frame.samples.ndim == 1
        assert frame.sample_rate == SAMPLE_RATE


def test_realtime_paces_to_wall_clock(short_wav_16khz: Path) -> None:
    """A 1-second WAV played back at `realtime=True` should take roughly
    1 second (give or take overhead)."""
    cap = FileCapture(short_wav_16khz, realtime=True)
    start = time.monotonic()
    frames = list(cap.stream())
    elapsed = time.monotonic() - start
    duration = frames[-1].t_end
    assert duration == pytest.approx(1.0, abs=0.1)
    # We allow some slop on the upper bound (sleep granularity, GC pauses)
    # and accept a small lower-bound undershoot from the final padded frame.
    assert duration - 0.1 <= elapsed <= duration + 0.5


def test_stop_interrupts_realtime_stream(short_wav_16khz: Path) -> None:
    """AudioSession.stop() must be able to unwind a 1x file playback."""
    import threading

    cap = FileCapture(short_wav_16khz, realtime=True)
    cap.start()
    n = {"frames": 0}

    def _run() -> None:
        for _ in cap.stream():
            n["frames"] += 1

    t = threading.Thread(target=_run)
    t.start()
    time.sleep(0.15)
    cap.stop()
    t.join(timeout=1.0)
    assert not t.is_alive()
    assert n["frames"] < 10


def test_non_realtime_is_fast(short_wav_16khz: Path) -> None:
    cap = FileCapture(short_wav_16khz, realtime=False)
    start = time.monotonic()
    list(cap.stream())
    elapsed = time.monotonic() - start
    # 1 second of audio should process well under a second when batch.
    assert elapsed < 0.5


def test_t_start_origin_offset(short_wav_16khz: Path) -> None:
    cap = FileCapture(short_wav_16khz, realtime=False, t_start=100.0)
    frames = list(cap.stream())
    assert frames[0].t_start == pytest.approx(100.0, abs=0.01)
