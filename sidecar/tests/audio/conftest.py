"""Synthetic WAV fixtures.

We avoid checking binary audio into git. Tests build short tones on the
fly via numpy + the `wave` stdlib module; `_write_wav` is the shared
helper.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest


def _write_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    """Write float32 mono samples in [-1, 1] as 16-bit PCM."""
    clipped = np.clip(samples, -1.0, 1.0)
    int16 = (clipped * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(int16.tobytes())


@pytest.fixture()
def short_wav_16khz(tmp_path: Path) -> Path:
    """1 second of a 440 Hz sine, 16 kHz mono."""
    sample_rate = 16_000
    duration = 1.0
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    samples = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    path = tmp_path / "tone.wav"
    _write_wav(path, samples, sample_rate)
    return path


@pytest.fixture()
def short_wav_44khz(tmp_path: Path) -> Path:
    """0.5 seconds of a 880 Hz sine, 44.1 kHz mono — needs resampling."""
    sample_rate = 44_100
    duration = 0.5
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    samples = (0.3 * np.sin(2 * np.pi * 880 * t)).astype(np.float32)
    path = tmp_path / "tone_44k.wav"
    _write_wav(path, samples, sample_rate)
    return path


@pytest.fixture()
def stereo_wav_16khz(tmp_path: Path) -> Path:
    """1 second of stereo 16 kHz audio — needs downmixing to mono."""
    sample_rate = 16_000
    duration = 1.0
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    left = 0.3 * np.sin(2 * np.pi * 440 * t)
    right = 0.3 * np.sin(2 * np.pi * 660 * t)
    stereo = np.empty(left.size * 2, dtype=np.float32)
    stereo[0::2] = left
    stereo[1::2] = right
    int16 = (np.clip(stereo, -1.0, 1.0) * 32767).astype(np.int16)
    path = tmp_path / "tone_stereo.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(int16.tobytes())
    return path
