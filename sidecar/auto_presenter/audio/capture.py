"""Audio capture sources.

`AudioCapture` is the streaming protocol every source implements. Phase 4
Slice 1A ships:

- `FileCapture` — reads a WAV from disk, yields frames at either real-time
  or fast-as-possible pace. Used by the eval harness's replay.
- A protocol/ABC for live capture; the `sounddevice`-backed implementation
  lands in Slice 1B/2 alongside the audio setup wizard.
"""

from __future__ import annotations

import threading
import time
import wave
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

import numpy as np

from auto_presenter.audio.types import (
    DEFAULT_FRAME_LENGTH,
    SAMPLE_RATE,
    AudioFrame,
)


class AudioCapture(Protocol):
    """Anything that yields a stream of `AudioFrame`s in time order."""

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def stream(self) -> Iterator[AudioFrame]: ...


class FileCapture:
    """Stream a WAV file as `AudioFrame`s.

    Reads at most one frame's worth of samples per iteration. By default
    the iterator paces itself to real time so a recorded service replays
    at 1×; pass `realtime=False` for batch evaluation, where you want the
    whole file processed as fast as possible.

    Resamples cheaply via linear interpolation when the WAV's native rate
    isn't 16 kHz. This is the right tradeoff for offline replay; live
    capture should ask the device for 16 kHz directly.

    `start` / `stop` match `LiveCapture` so `AudioSession` can own either
    source. Eval callers that only call `stream()` keep working: stop is
    a no-op until someone asks for it.
    """

    def __init__(
        self,
        path: Path,
        *,
        realtime: bool = True,
        frame_length: int = DEFAULT_FRAME_LENGTH,
        t_start: float = 0.0,
    ) -> None:
        self.path = Path(path)
        self.realtime = realtime
        self.frame_length = frame_length
        self.t_start = t_start
        self._stop = threading.Event()

    def start(self) -> None:
        self._stop.clear()

    def stop(self) -> None:
        self._stop.set()

    def stream(self) -> Iterator[AudioFrame]:
        samples, native_rate = _read_wav_mono_float32(self.path)
        if native_rate != SAMPLE_RATE:
            samples = _resample_linear(samples, native_rate, SAMPLE_RATE)
        return _emit_frames(
            samples,
            sample_rate=SAMPLE_RATE,
            frame_length=self.frame_length,
            t_origin=self.t_start,
            realtime=self.realtime,
            stop=self._stop,
        )


def _read_wav_mono_float32(path: Path) -> tuple[np.ndarray, int]:
    """Read a WAV (any width / channels), return (mono float32, sample_rate)."""
    with wave.open(str(path), "rb") as wav:
        n_channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        n_frames = wav.getnframes()
        raw = wav.readframes(n_frames)

    if sample_width == 2:
        ints = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sample_width == 4:
        # int32 PCM
        ints = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    elif sample_width == 1:
        # 8-bit unsigned PCM
        ints = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"unsupported sample width: {sample_width} bytes")

    if n_channels > 1:
        ints = ints.reshape(-1, n_channels).mean(axis=1).astype(np.float32)
    return ints.astype(np.float32, copy=False), sample_rate


def _resample_linear(
    samples: np.ndarray, src_rate: int, dst_rate: int
) -> np.ndarray:
    """Cheap linear interpolation. Good enough for offline eval replay; live
    capture should request `dst_rate` from the device directly."""
    if src_rate == dst_rate:
        return samples
    src_n = samples.shape[0]
    dst_n = int(round(src_n * dst_rate / src_rate))
    if dst_n <= 0 or src_n <= 1:
        return samples.astype(np.float32, copy=False)
    src_indices = np.arange(src_n, dtype=np.float64)
    dst_indices = np.linspace(0, src_n - 1, dst_n, dtype=np.float64)
    out = np.interp(dst_indices, src_indices, samples)
    return out.astype(np.float32, copy=False)


def _emit_frames(
    samples: np.ndarray,
    *,
    sample_rate: int,
    frame_length: int,
    t_origin: float,
    realtime: bool,
    stop: threading.Event | None = None,
) -> Iterator[AudioFrame]:
    seconds_per_frame = frame_length / sample_rate
    wall_start = time.monotonic()
    pos = 0
    frame_idx = 0
    while pos < samples.shape[0]:
        if stop is not None and stop.is_set():
            return
        end = min(pos + frame_length, samples.shape[0])
        chunk = samples[pos:end]
        # Pad the final frame so consumers always get `frame_length` samples
        # at the configured rate. Optional, but it simplifies downstream code.
        if chunk.shape[0] < frame_length:
            chunk = np.concatenate(
                [chunk, np.zeros(frame_length - chunk.shape[0], dtype=np.float32)]
            )
        t_start = t_origin + frame_idx * seconds_per_frame
        t_end = t_start + seconds_per_frame
        if realtime:
            target = wall_start + (t_start - t_origin)
            if not _wait_until(target, stop):
                return
        yield AudioFrame(
            samples=chunk.astype(np.float32, copy=False),
            t_start=t_start,
            t_end=t_end,
            sample_rate=sample_rate,
        )
        pos = end
        frame_idx += 1


def _wait_until(target: float, stop: threading.Event | None) -> bool:
    """Sleep until `target` (monotonic) or `stop`. False if stopped."""
    while True:
        if stop is not None and stop.is_set():
            return False
        remaining = target - time.monotonic()
        if remaining <= 0:
            return True
        if stop is None:
            time.sleep(remaining)
            return True
        stop.wait(timeout=min(0.05, remaining))
