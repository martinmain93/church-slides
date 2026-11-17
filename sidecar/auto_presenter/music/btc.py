"""BTC chord recognition over ONNX — the learned upgrade to template matching.

BTC (Park et al., ISMIR 2019) is 3 M parameters, MIT-licensed, and roughly twice
as accurate as the template matcher on the goldens. Release builds bundle it
(scripts/build_sidecar.py); a source checkout needs `scripts/setup_chord_model.py`.
It reports `available` and `status`, and the analyser falls back to templates when the model is absent.

Two things about running a model like this live, neither of which the paper has
to care about:

* **BTC is bidirectional.** It consumes a fixed block of 108 frames (about
  10 s) and attends across all of it, so the newest frame is the one with the
  least right-hand context and is where its accuracy is worst. Everything here
  runs on a trailing block and reads the newest frames out of it, which is the
  online penalty the literature measures as several points of F1.
* **Frames are trailing, not centred.** librosa centres each frame, so a frame
  labelled *t* is built from audio up to *t* + 0.74 s. A live analyser cannot
  see that, so frames here end at the present instant. A chord lasts a second
  or more, which is why this is a small mismatch rather than a fatal one —
  `research/eval/check_btc_backend.py` measures the cost rather than assuming it.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path

import numpy as np

from auto_presenter.music.cqt import CQT, StreamResampler, log_cqt, resample_linear

log = logging.getLogger(__name__)

#: The 25-class maj/min vocabulary, in the order BTC's output layer emits.
PITCHES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MAJMIN_LABELS = [f"{p}{q}" for p in PITCHES for q in ("", "m")] + ["N"]

# One loaded model per file for the life of the process. Every AI session
# builds its own analyser (and so its own backend); an ONNX Runtime session per
# service start was never handed back in the frozen engine — about 50 MB of
# native memory and a thread pool per start, which the Windows soak measured
# as 67 MB a pass. `InferenceSession.run` is safe to call from several threads.
_LOADED: dict[str, tuple[object, dict]] = {}
_LOAD_LOCK = threading.Lock()


def bundled_model_dir() -> Path | None:
    """The copy a release build carries (sidecar.spec puts it in the frozen
    engine's `_internal/models/chords`); None when not frozen."""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) / "models" / "chords" if base else None


def default_model_dir() -> Path:
    """Where to load BTC from: an explicit `AUTO_PRESENTER_CHORD_MODEL`, else
    the copy bundled in a release build, else the app data folder, where
    `scripts/setup_chord_model.py` installs it for development."""
    override = os.environ.get("AUTO_PRESENTER_CHORD_MODEL")
    if override:
        return Path(override)
    bundled = bundled_model_dir()
    if bundled is not None and (bundled / "btc_chords.onnx").is_file():
        return bundled
    data_dir = os.environ.get("AUTO_PRESENTER_DATA_DIR")
    if data_dir:
        base = Path(data_dir)
    else:
        # The per-OS app data folder, not a hardcoded macOS one — which is where
        # this used to look on every platform.
        from auto_presenter.config import app_data_dir

        base = app_data_dir()
    return base / "models" / "chords"


class BtcChordBackend:
    """Chord logits for a trailing window of 16 kHz audio."""

    def __init__(self, model_dir: Path | None = None) -> None:
        self.model_dir = Path(model_dir) if model_dir else default_model_dir()
        self.status = "model_missing"
        self._sess = None
        self._meta: dict = {}
        self._cqt: CQT | None = None
        self._load()

    # ------------------------------------------------------------------ load

    def _load(self) -> None:
        onnx_path = self.model_dir / "btc_chords.onnx"
        meta_path = self.model_dir / "btc_chords.json"
        if not onnx_path.exists() or not meta_path.exists():
            self.status = "model_missing"
            return
        try:
            import onnxruntime as ort
        except ImportError:
            # onnxruntime is not a base dependency; the template matcher is the
            # shipped default and this is the upgrade path.
            self.status = "runtime_missing"
            return
        try:
            with _LOAD_LOCK:
                loaded = _LOADED.get(str(onnx_path))
                if loaded is None:
                    meta = json.loads(meta_path.read_text())
                    opts = ort.SessionOptions()
                    # One thread: this runs on the capture thread beside Whisper,
                    # and the point of a side channel is not to compete for cores.
                    opts.intra_op_num_threads = 1
                    opts.inter_op_num_threads = 1
                    sess = ort.InferenceSession(
                        str(onnx_path), opts, providers=["CPUExecutionProvider"]
                    )
                    loaded = _LOADED[str(onnx_path)] = (sess, meta)
            self._sess, meta = loaded
            self._meta = dict(meta)
            self._cqt = CQT(
                sr=self._meta["sample_rate"],
                hop_length=self._meta["hop_length"],
                n_bins=self._meta["n_bins"],
                bins_per_octave=self._meta["bins_per_octave"],
            )
            self.status = "ready"
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("BTC chord model failed to load: %s", exc)
            self.status = "load_failed"

    @property
    def available(self) -> bool:
        return self._sess is not None

    @property
    def labels(self) -> list[str]:
        return MAJMIN_LABELS

    @property
    def timestep(self) -> int:
        return int(self._meta.get("timestep", 108))

    @property
    def window_seconds(self) -> float:
        """Audio needed for one inference block, plus the CQT's own context."""
        sr = float(self._meta.get("sample_rate", 22050))
        hop = float(self._meta.get("hop_length", 2048))
        n_fft = float(self._cqt.n_fft) if self._cqt else 32768.0
        return (self.timestep * hop + n_fft) / sr

    # ------------------------------------------------------------- inference

    def features(self, audio_16k: np.ndarray) -> np.ndarray:
        """(frames, 144) log-CQT on trailing windows, normalised as BTC expects."""
        assert self._cqt is not None
        sr = int(self._meta["sample_rate"])
        y = resample_linear(audio_16k, 16000, sr)
        mags = self._cqt.trailing(y)
        feat = log_cqt(mags)
        return (feat - float(self._meta["mean"])) / float(self._meta["std"])

    def predict(self, audio_16k: np.ndarray) -> np.ndarray | None:
        """Softmax over the 25 chords for the newest frame, or None.

        Returns None rather than guessing when there is not yet a full block:
        BTC has a fixed input length and padding it with silence would put a
        confident "N" on the screen for the first ten seconds.

        Recomputes everything from the given audio; the live path uses `push`
        and `predict_stream` instead, which do not.
        """
        if self._sess is None:
            return None
        feat = self.features(audio_16k)
        n = self.timestep
        if len(feat) < n:
            return None
        return self._infer(feat[-n:])

    def _infer(self, block: np.ndarray) -> np.ndarray:
        assert self._sess is not None
        logits = self._sess.run(
            ["logits"], {"features": block[None, :, :].astype(np.float32)}
        )[0][0]
        newest = logits[-1]
        e = np.exp(newest - newest.max())
        return (e / e.sum()).astype(np.float32)

    # ---------------------------------------------------------------- stream

    def reset_stream(self) -> None:
        self._carry = np.zeros(0, dtype=np.float32)
        self._frames = np.zeros((0, int(self._meta.get("n_bins", 144))), np.float32)
        self._resampler = StreamResampler(16000, int(self._meta.get("sample_rate", 22050)))

    def push(self, audio_16k: np.ndarray) -> int:
        """Add new audio and extend the feature block. Returns frames added.

        Only the CQT frames whose trailing window has newly completed are
        computed. Recomputing the whole eleven-second window on every tick was
        the obvious first version and cost more than the ASR pass it is
        supposed to sit beside.
        """
        if self._sess is None or self._cqt is None:
            return 0
        if not hasattr(self, "_carry"):
            self.reset_stream()
        hop = int(self._meta["hop_length"])
        n_fft = self._cqt.n_fft
        self._carry = np.concatenate([self._carry, self._resampler(audio_16k)])
        if len(self._carry) < n_fft:
            return 0
        n_new = 1 + (len(self._carry) - n_fft) // hop
        if n_new <= 0:
            return 0
        idx = np.arange(n_fft)[None, :] + (np.arange(n_new) * hop)[:, None]
        mags = self._cqt.magnitudes(self._carry[idx])
        feat = (log_cqt(mags) - float(self._meta["mean"])) / float(self._meta["std"])
        self._frames = np.concatenate([self._frames, feat])[-self.timestep:]
        # Keep just enough tail to build the next frame.
        self._carry = self._carry[n_new * hop:]
        return n_new

    def predict_stream(self) -> np.ndarray | None:
        """Softmax for the newest frame of the streamed block, or None."""
        if self._sess is None or not hasattr(self, "_frames"):
            return None
        if len(self._frames) < self.timestep:
            return None
        return self._infer(self._frames)
