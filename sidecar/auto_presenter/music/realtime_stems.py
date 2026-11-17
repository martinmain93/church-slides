"""Realtime stem split.

The weights are already trained. This module is the part the rest of the
app holds onto: wait until a window is full, run it, and release the samples
no later window can change. Demucs itself stays in another process
(`scripts/run_demucs_mlx.py`), the same arrangement as Beat This.

`htdemucs` reads 7.8 s. With a quarter overlap the next window starts 5.85 s
later, and everything before that start is final. The stem therefore runs
about two seconds behind the audio, plus the tenth of a second the chunk
takes to compute.

Drums, bass, other, and vocals. Chords read the harmonic leftover and
pitch reads the vocals. Beats stay on the full mix.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import threading
import wave
from pathlib import Path

import numpy as np

from auto_presenter.config import app_data_dir
from auto_presenter.music.cqt import StreamResampler

STEMS: tuple[str, ...] = ("drums", "bass", "other", "vocals")
# Pitch and chords read a stem. Beats stay on the mix: Beat This loses the
# grid when it only hears drums.
STEM_FOR = {"pitch": "vocals", "chords": "other"}

# HTDemucs training window. The running model reports its own length; this
# is the figure the delay is designed around.
SEGMENT_S = 7.8
OVERLAP = 0.25

STEM_DIR = "stems"


class StemUnavailable(RuntimeError):
    """demucs-mlx is not installed, or the model process could not start."""


def python_path() -> Path | None:
    override = os.environ.get("AUTO_PRESENTER_DEMUCS_PYTHON")
    if override and Path(override).exists():
        return Path(override)
    candidate = app_data_dir() / "models" / "demucs-mlx-venv" / "bin" / "python"
    if candidate.exists():
        return candidate
    return None


def script_path() -> Path:
    # sidecar/auto_presenter/music/realtime_stems.py -> repo/scripts/run_demucs_mlx.py
    return Path(__file__).resolve().parents[3] / "scripts" / "run_demucs_mlx.py"


def triangular(n: int) -> np.ndarray:
    """The same window demucs-mlx uses for overlap-add, peak 1 in the middle."""
    half = n // 2
    weight = np.concatenate(
        [
            np.arange(1, half + 1, dtype=np.float64),
            np.arange(n - half, 0, -1, dtype=np.float64),
        ]
    )
    return weight / weight.max()


class OverlapAdd:
    """Sum windowed chunks and hand back the samples that are finished.

    Chunk k covers `[k * stride, k * stride + segment)`. After chunks
    `0 .. n-1` have arrived, nothing later reaches below `n * stride`.
    """

    def __init__(self, segment: int, overlap: float, n_sources: int) -> None:
        if segment < 2:
            raise ValueError("segment must cover at least two samples")
        if not 0.0 <= overlap < 1.0:
            raise ValueError("overlap must be in [0, 1)")
        self.segment = int(segment)
        self.stride = int((1.0 - overlap) * segment)
        if self.stride < 1:
            raise ValueError("overlap leaves no stride")
        self.n_sources = n_sources
        self.weight = triangular(self.segment)
        self._acc = np.zeros((n_sources, 0), dtype=np.float64)
        self._wsum = np.zeros(0, dtype=np.float64)
        self._n = 0
        self.finalized = 0
        self.covered = 0

    def add(self, chunk: np.ndarray) -> np.ndarray:
        """`chunk` is `(sources, valid)` raw model output, starting at this window."""
        valid = int(chunk.shape[-1])
        if chunk.shape[0] != self.n_sources:
            raise ValueError("chunk has the wrong number of stems")
        if valid < 1 or valid > self.segment:
            raise ValueError("chunk length must be within the segment")
        start = self._n * self.stride
        end = start + valid
        self._ensure(end)
        self._acc[:, start:end] += chunk[:, :valid] * self.weight[:valid]
        self._wsum[start:end] += self.weight[:valid]
        self._n += 1
        self.covered = end
        # A later chunk cannot reach below the next stride. Past the audio
        # we actually have, there is nothing to hold back.
        ready = min(self.covered, self._n * self.stride)
        return self._take(ready)

    def finish(self) -> np.ndarray:
        """The tail a later chunk would have overlapped, had one been coming."""
        return self._take(self.covered)

    def _ensure(self, n: int) -> None:
        have = self._acc.shape[1]
        if have >= n:
            return
        extra = n - have
        self._acc = np.concatenate(
            [self._acc, np.zeros((self.n_sources, extra), dtype=np.float64)],
            axis=1,
        )
        self._wsum = np.concatenate([self._wsum, np.zeros(extra, dtype=np.float64)])

    def _take(self, end: int) -> np.ndarray:
        if end <= self.finalized:
            return np.zeros((self.n_sources, 0), dtype=np.float32)
        sl = slice(self.finalized, end)
        out = self._acc[:, sl] / np.maximum(self._wsum[sl], 1e-12)
        self.finalized = end
        return out.astype(np.float32)


class _DemucsProcess:
    """One loaded htdemucs. A window in, the raw stems for that window out."""

    def __init__(self) -> None:
        self.samplerate = 44100
        self.segment = 0
        self.sources: tuple[str, ...] = STEMS
        self._proc: subprocess.Popen | None = None
        self._channels = 2
        self._err = bytearray()
        self._err_thread: threading.Thread | None = None

    def start(self) -> None:
        py = python_path()
        script = script_path()
        if py is None or not script.exists():
            raise StemUnavailable(
                "demucs-mlx is not installed "
                "(models/demucs-mlx-venv, or AUTO_PRESENTER_DEMUCS_PYTHON)"
            )
        self._proc = subprocess.Popen(
            [str(py), str(script), "--serve"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert self._proc.stderr is not None
        # The first launch converts weights and prints a long log. Leaving
        # that unread fills the pipe and the process never reaches its hello.
        self._err = bytearray()
        self._err_thread = threading.Thread(
            target=_drain_stderr,
            args=(self._proc.stderr, self._err),
            daemon=True,
        )
        self._err_thread.start()
        assert self._proc.stdout is not None
        line = self._proc.stdout.readline()
        if not line:
            raise StemUnavailable(self._failure("stem model did not start"))
        hello = json.loads(line.decode("utf-8"))
        if not hello.get("ready"):
            raise StemUnavailable(str(hello.get("error") or "stem model did not start"))
        self.samplerate = int(hello["samplerate"])
        self.segment = int(hello["segment"])
        self._channels = int(hello.get("channels", 2))
        self.sources = tuple(hello.get("sources") or STEMS)
        missing = [name for name in STEMS if name not in self.sources]
        if missing:
            self.close()
            raise StemUnavailable(f"stem model has no {', '.join(missing)}")

    def infer(self, mono: np.ndarray, valid: int) -> np.ndarray:
        """`(segment,)` padded mono at the model rate. Returns `(stems, valid)`."""
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdout is None:
            raise StemUnavailable("stem model is not running")
        window = np.asarray(mono, dtype=np.float32).reshape(-1)
        if window.shape[0] != self.segment:
            raise ValueError("window must be one segment long")
        if self._channels == 1:
            payload = window
        else:
            payload = np.stack([window] * self._channels, axis=0)
        proc.stdin.write(struct.pack("<II", self.segment, int(valid)))
        proc.stdin.write(np.ascontiguousarray(payload, dtype="<f4").tobytes())
        proc.stdin.flush()
        got = _read_exact(proc.stdout, 4)
        (n,) = struct.unpack("<I", got)
        if n != valid:
            raise StemUnavailable(self._failure(f"stem model returned {n} samples"))
        raw = _read_exact(proc.stdout, 4 * len(self.sources) * n)
        stems = np.frombuffer(raw, dtype="<f4").reshape(len(self.sources), n).copy()
        order = {name: i for i, name in enumerate(self.sources)}
        return np.stack([stems[order[name]] for name in STEMS], axis=0)

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.write(struct.pack("<II", 0, 0))
                proc.stdin.flush()
                proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    def _failure(self, fallback: str) -> str:
        proc = self._proc
        if proc is None:
            return fallback
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        if self._err_thread is not None:
            self._err_thread.join(timeout=2)
        err = bytes(self._err).decode("utf-8", "replace").strip()
        line = err.splitlines()[-1] if err else ""
        return line or fallback


def _drain_stderr(stream, buf: bytearray) -> None:
    while True:
        chunk = stream.read(4096)
        if not chunk:
            return
        buf += chunk
        if len(buf) > 24000:
            del buf[:-12000]


def _read_exact(stream, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            raise StemUnavailable("stem model closed the stream")
        buf += chunk
    return bytes(buf)


class RealtimeStems:
    """Push 16 kHz mono audio. Pull the stems that are finished, at 16 kHz.

    One instance, one thread. `push` returns whatever became final. `finish`
    flushes the tail once the recording (or the live take) has ended.
    """

    def __init__(self, engine: _DemucsProcess | None = None) -> None:
        self._engine = engine if engine is not None else _DemucsProcess()
        self._ola: OverlapAdd | None = None
        self._up: StreamResampler | None = None
        self._down: dict[str, StreamResampler] = {}
        self._audio = np.zeros(0, dtype=np.float32)
        self._origin = 0
        self._next = 0
        self.samplerate = 0
        self.segment = 0

    @property
    def through_seconds(self) -> float:
        if self._ola is None or self.samplerate <= 0:
            return 0.0
        return self._ola.finalized / self.samplerate

    def start(self) -> None:
        self._engine.start()
        self.samplerate = self._engine.samplerate
        self.segment = self._engine.segment
        self._ola = OverlapAdd(self.segment, OVERLAP, len(STEMS))
        self._up = StreamResampler(16000, self.samplerate)
        self._down = {name: StreamResampler(self.samplerate, 16000) for name in STEMS}
        self._audio = np.zeros(0, dtype=np.float32)
        self._origin = 0
        self._next = 0

    def push(self, audio_16k: np.ndarray) -> dict[str, np.ndarray]:
        if self._up is None or self._ola is None:
            raise RuntimeError("start the stem session before pushing audio")
        up = self._up(np.asarray(audio_16k, dtype=np.float32).reshape(-1))
        if len(up):
            self._audio = np.concatenate([self._audio, up])
        return self._drain(final=False)

    def finish(self) -> dict[str, np.ndarray]:
        if self._ola is None:
            return {name: np.zeros(0, dtype=np.float32) for name in STEMS}
        drained = self._drain(final=True)
        # Samples the resampler was still holding, then the overlap tail.
        tail = self._ola.finish()
        return _merge(drained, self._to_16k(tail))

    def close(self) -> None:
        self._engine.close()

    def _drain(self, final: bool) -> dict[str, np.ndarray]:
        assert self._ola is not None
        pieces: list[np.ndarray] = []
        stride = self._ola.stride
        while True:
            start = self._next * stride
            have = self._origin + len(self._audio)
            if start >= have:
                break
            valid = min(self.segment, have - start)
            if valid < self.segment and not final:
                break
            if valid < 1:
                break
            # A short tail is centre-padded, the same way Demucs builds it,
            # so the trimmed model output lines up with [start, start+valid).
            delta = self.segment - valid
            pad_left = delta // 2
            window = self._window(start - pad_left, start - pad_left + self.segment)
            raw = self._engine.infer(window, valid)
            pieces.append(self._ola.add(raw))
            self._next += 1
            self._drop_spent()
            if valid < self.segment:
                break
        if not pieces:
            return {name: np.zeros(0, dtype=np.float32) for name in STEMS}
        return self._to_16k(np.concatenate(pieces, axis=1))

    def _window(self, start: int, end: int) -> np.ndarray:
        """Absolute `[start, end)`, zero outside the audio held so far."""
        out = np.zeros(end - start, dtype=np.float32)
        have = self._origin + len(self._audio)
        src_lo = max(start, self._origin, 0)
        src_hi = min(end, have)
        if src_hi > src_lo:
            src = self._audio[src_lo - self._origin : src_hi - self._origin]
            out[src_lo - start : src_hi - start] = src
        return out

    def _drop_spent(self) -> None:
        """Forget audio the next window no longer needs, keeping its left pad."""
        assert self._ola is not None
        keep = max(0, self._next * self._ola.stride - self.segment // 2)
        drop = keep - self._origin
        if drop > self.samplerate:
            self._audio = self._audio[drop:]
            self._origin = keep

    def _to_16k(self, model_rate: np.ndarray) -> dict[str, np.ndarray]:
        out: dict[str, np.ndarray] = {}
        for i, name in enumerate(STEMS):
            samples = model_rate[i] if model_rate.shape[1] else np.zeros(0, dtype=np.float32)
            out[name] = self._down[name](samples) if len(samples) else np.zeros(0, dtype=np.float32)
        return out


def _merge(a: dict[str, np.ndarray], b: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    for name in STEMS:
        parts = [x for x in (a.get(name), b.get(name)) if x is not None and len(x)]
        out[name] = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
    return out


def write_stem_wavs(
    directory: Path, stems: dict[str, np.ndarray], sample_rate: int = 16000
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in STEMS:
        _write_wav(directory / f"{name}.wav", stems.get(name, np.zeros(0, np.float32)), sample_rate)


def read_stem_wavs(directory: Path) -> dict[str, np.ndarray] | None:
    paths = [directory / f"{name}.wav" for name in STEMS]
    if not all(path.exists() for path in paths):
        return None
    return {name: _read_wav(directory / f"{name}.wav") for name in STEMS}


def _write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    pcm = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    frames = (pcm * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(frames.tobytes())


def _read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        sw = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    if sw == 2:
        return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if sw == 4:
        return np.frombuffer(raw, dtype=np.float32).copy()
    raise ValueError(f"unsupported sample width {sw}")
