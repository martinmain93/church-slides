"""Basic Pitch (Bittner et al., ICASSP 2022) via its ONNX model.

The sidecar already has onnxruntime. The model consumes raw 22.05 kHz audio,
so this does not pull in TensorFlow or librosa. Windowing matches
`basic_pitch.inference`: 2 s windows, 30 frames of overlap, half of that
overlap discarded from each end of the prediction.

What comes back is a pitch trace — the contour head's loudest bin, kept only
when it is actually confident — plus note spans from the note head. Both are
for looking at, on the inspector timeline.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from auto_presenter.config import app_data_dir

SR_IN = 16000
SR = 22050
FFT_HOP = 256
FPS = SR // FFT_HOP  # 86
AUDIO_N_SAMPLES = SR * 2 - FFT_HOP  # 43844
OVERLAP_FRAMES = 30
PAD = (OVERLAP_FRAMES // 2) * FFT_HOP
HOP = AUDIO_N_SAMPLES - OVERLAP_FRAMES * FFT_HOP
# Contour bins: 3 per semitone, and bin 1 (not 0) is A0 / MIDI 21.
CONTOUR_MIDI0 = 21
CONTOUR_CENTER_BIN = 1
NOTE_MIDI0 = 21
# Stored loose on purpose. The inspector raises these with sliders and redraws
# at once; a tighter cut here would throw the quiet frames away.
CONTOUR_FLOOR = 0.05
NOTE_FLOOR = 0.15
MIN_NOTE_FLOOR = 0.04
# About one point every 23 ms. Denser than the old 80 ms step, so a noisy
# contour still has something to draw when the threshold comes down.
CONTOUR_STEP = 2


@dataclass(frozen=True)
class PitchPoint:
    t_seconds: float
    midi: float
    confidence: float


@dataclass(frozen=True)
class PitchNote:
    t_start: float
    t_end: float
    midi: int
    confidence: float


def bundled_model_path() -> Path | None:
    """The copy a release build carries (sidecar.spec puts it in the frozen
    engine's `_internal/models/basic_pitch`); None when not frozen."""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) / "models" / "basic_pitch" / "nmp.onnx" if base else None


def model_path() -> Path:
    """An explicit `AUTO_PRESENTER_BASIC_PITCH`, else the copy bundled in a
    release build, else the app data folder (a development install)."""
    override = os.environ.get("AUTO_PRESENTER_BASIC_PITCH", "")
    if override:
        return Path(override)
    bundled = bundled_model_path()
    if bundled is not None and bundled.is_file():
        return bundled
    return app_data_dir() / "models" / "basic_pitch" / "nmp.onnx"


def _resample(audio_16k: np.ndarray) -> np.ndarray:
    x = np.ascontiguousarray(audio_16k, dtype=np.float32)
    n_out = round(len(x) * SR / SR_IN)
    if n_out <= 1 or len(x) == 0:
        return np.zeros(0, dtype=np.float32)
    pos = np.linspace(0, len(x) - 1, n_out)
    i = np.floor(pos).astype(np.int32)
    frac = (pos - i).astype(np.float32)
    i2 = np.minimum(i + 1, len(x) - 1)
    return (x[i] * (1.0 - frac) + x[i2] * frac).astype(np.float32)


def frame_times(n_windows: int) -> np.ndarray:
    """Time of each kept model frame, in seconds of the unpadded audio.

    Concatenating frames and dividing by the frame rate runs long. Each
    window advances by `HOP` samples, which is one frame short of the 142
    frames we keep, so the error grows by about 11 ms a window — roughly
    two seconds by minute five.
    """
    n_olap = OVERLAP_FRAMES // 2
    n_out = 172  # model frames per 2 s window
    times = np.empty(n_windows * (n_out - OVERLAP_FRAMES), dtype=np.float64)
    k = 0
    for w in range(n_windows):
        origin = w * HOP / SR - PAD / SR
        for frame in range(n_olap, n_out - n_olap):
            times[k] = origin + frame / FPS
            k += 1
    return times


def _unwrap(chunks: list[np.ndarray], n_audio: int) -> np.ndarray:
    """Drop the overlap from each window and concatenate, as basic-pitch does."""
    n_olap = OVERLAP_FRAMES // 2
    trimmed = [c[n_olap:-n_olap] if n_olap else c for c in chunks]
    if not trimmed:
        return np.zeros((0, chunks[0].shape[-1]) if chunks else (0, 1), np.float32)
    cat = np.concatenate(trimmed, axis=0)
    n_expected = n_audio / HOP
    n_keep = int(n_expected * ((2 * FPS) - OVERLAP_FRAMES))
    return cat[: max(0, n_keep)]


class PitchRun:
    """The same Basic Pitch windows as `transcribe`, published as they finish.

    Each window is independent and starts on the same hop a full-file run
    uses, so the frames already produced do not change when the next window
    arrives. A note that is still sounding at the frontier grows on the next
    publish; it is not cut and started again.
    """

    def __init__(self, audio_16k: np.ndarray):
        self.points: list[PitchPoint] = []
        self.notes: list[PitchNote] = []
        self.backend = "basic_pitch"
        self.committed = 0.0
        self._audio = np.asarray(audio_16k, dtype=np.float32)
        self._ready = False
        self._missing = False
        self._i = 0
        self._starts: list[int] = []
        self._note_chunks: list[np.ndarray] = []
        self._contour_chunks: list[np.ndarray] = []
        self._padded: np.ndarray | None = None
        self._resampled_len = 0
        self._sess = None

    def advance_to(self, t: float) -> None:
        self._setup()
        if self._missing:
            return
        while self._i < len(self._starts) and self.committed < t:
            self._run_one()
        self._publish()

    def _setup(self) -> None:
        if self._ready:
            return
        self._ready = True
        path = model_path()
        if not path.exists():
            self.backend = "model_missing"
            self._missing = True
            self.committed = 1e9
            return
        try:
            import onnxruntime as ort
        except ImportError:
            self.backend = "runtime_missing"
            self._missing = True
            self.committed = 1e9
            return
        audio = _resample(self._audio)
        if len(audio) < SR // 2:
            self.backend = "too_short"
            self._missing = True
            self.committed = 1e9
            return
        self._resampled_len = len(audio)
        self._padded = np.concatenate([np.zeros(PAD, np.float32), audio])
        opts = ort.SessionOptions()
        # The inspector runs in the same process as live Whisper. Two threads
        # keeps a song to a few seconds without taking the whole machine.
        opts.intra_op_num_threads = 2
        opts.inter_op_num_threads = 1
        self._sess = ort.InferenceSession(
            str(path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self._starts = list(range(0, len(self._padded), HOP))

    def _run_one(self) -> None:
        assert self._padded is not None and self._sess is not None
        start = self._starts[self._i]
        window = self._padded[start : start + AUDIO_N_SAMPLES]
        if len(window) < AUDIO_N_SAMPLES:
            window = np.pad(window, (0, AUDIO_N_SAMPLES - len(window)))
        note, _onset, contour = self._sess.run(
            [
                "StatefulPartitionedCall:1",
                "StatefulPartitionedCall:2",
                "StatefulPartitionedCall:0",
            ],
            {"serving_default_input_2:0": window.astype(np.float32)[None, :, None]},
        )
        self._note_chunks.append(note[0])
        self._contour_chunks.append(contour[0])
        self._i += 1
        times = frame_times(self._i)
        self.committed = float(times[-1]) if len(times) else 0.0

    def _publish(self) -> None:
        if not self._note_chunks:
            return
        note_map = _unwrap(self._note_chunks, self._resampled_len)
        contour = _unwrap(self._contour_chunks, self._resampled_len)
        times = frame_times(len(self._note_chunks))
        n = min(len(times), len(note_map), len(contour))
        self.points = _contour_points(contour[:n], times[:n])
        self.notes = _note_spans(note_map[:n], times[:n])


def transcribe(audio_16k: np.ndarray) -> tuple[list[PitchPoint], list[PitchNote], str]:
    """Pitch trace and note spans. `status` is 'basic_pitch' or a reason it is not."""
    run = PitchRun(audio_16k)
    run.advance_to(1e9)
    return run.points, run.notes, run.backend


def _contour_points(contour: np.ndarray, times: np.ndarray) -> list[PitchPoint]:
    if contour.size == 0:
        return []
    # One point about every 80 ms. The model runs at 86 Hz, which is more
    # than a timeline needs and a lot to send for a seven-minute recording.
    step = CONTOUR_STEP
    points: list[PitchPoint] = []
    for i in range(0, len(contour), step):
        row = contour[i]
        k = int(np.argmax(row))
        conf = float(row[k])
        if conf < CONTOUR_FLOOR:
            continue
        midi = CONTOUR_MIDI0 + (k - CONTOUR_CENTER_BIN) / 3.0
        points.append(PitchPoint(round(float(times[i]), 3), round(midi, 2), round(conf, 3)))
    return points


def _note_spans(note_map: np.ndarray, times: np.ndarray) -> list[PitchNote]:
    if note_map.size == 0:
        return []
    spans: list[PitchNote] = []
    active = note_map >= NOTE_FLOOR
    for midi_i in range(note_map.shape[1]):
        col = active[:, midi_i]
        i = 0
        while i < len(col):
            if not col[i]:
                i += 1
                continue
            j = i + 1
            while j < len(col) and col[j]:
                j += 1
            t0 = float(times[i])
            t1 = float(times[min(j, len(times) - 1)])
            if t1 - t0 >= MIN_NOTE_FLOOR:
                conf = float(note_map[i:j, midi_i].mean())
                spans.append(
                    PitchNote(round(t0, 3), round(t1, 3), NOTE_MIDI0 + midi_i, round(conf, 3))
                )
            i = j
    return spans
