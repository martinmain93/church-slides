"""A constant-Q transform in numpy, matching what BTC was trained on.

BTC consumes `librosa.cqt(y, sr=22050, n_bins=144, bins_per_octave=24,
hop_length=2048)` followed by `log(|.| + 1e-6)`. The sidecar ships numpy and
onnxruntime and deliberately not librosa, so the transform has to be rebuilt
here — and it has to be *close*, because a model fed a subtly different feature
distribution does not fail loudly, it produces confident nonsense.

Method is Brown & Puckette (1992): build one complex kernel per bin, take its
spectrum once, and evaluate the transform as a sparse frequency-domain product
against each frame's FFT. Sparse because a constant-Q kernel is narrowband by
construction — the whole matrix is a few percent dense, which is what makes
this affordable at all.

Agreement with librosa is asserted by `research/eval/check_btc_backend.py`, which
compares both the features and the chord labels that come out of them.
"""

from __future__ import annotations

import numpy as np

#: librosa's default `fmin` for `cqt` — C1.
FMIN_C1 = 32.70319566257483

#: Below this share of a kernel's peak magnitude, a frequency bin contributes
#: nothing an audio model can hear. librosa thresholds its own kernels the same
#: way and for the same reason.
SPARSITY = 0.01


def _kernel_length(freq: float, q: float, sr: int) -> int:
    return int(np.ceil(q * sr / freq))


def cqt_kernel(
    sr: int = 22050,
    n_bins: int = 144,
    bins_per_octave: int = 24,
    fmin: float = FMIN_C1,
    filter_scale: float = 1.0,
) -> tuple[np.ndarray, int]:
    """(kernel spectra, n_fft) for a sparse frequency-domain CQT.

    Returned as a dense complex array that is mostly zeros; callers multiply
    against it directly, which numpy does fast enough that a sparse container
    is not worth the dependency.
    """
    q = filter_scale / (2 ** (1.0 / bins_per_octave) - 1)
    freqs = fmin * 2.0 ** (np.arange(n_bins) / bins_per_octave)
    longest = _kernel_length(float(freqs[0]), q, sr)
    n_fft = int(2 ** np.ceil(np.log2(longest)))

    kernel = np.zeros((n_bins, n_fft), dtype=np.complex64)
    for k, f in enumerate(freqs):
        length = _kernel_length(float(f), q, sr)
        if length > n_fft:
            length = n_fft
        # Hann-windowed complex exponential, L1-normalised so every bin
        # reports comparable magnitude regardless of how long its filter is.
        win = np.hanning(length + 2)[1:-1]
        t = np.arange(length)
        atom = win * np.exp(2j * np.pi * f * t / sr)
        atom /= np.abs(atom).sum()
        start = (n_fft - length) // 2
        kernel[k, start:start + length] = atom

    spec = np.fft.fft(kernel, axis=1).astype(np.complex64)
    peak = np.abs(spec).max(axis=1, keepdims=True)
    spec[np.abs(spec) < SPARSITY * np.maximum(peak, 1e-12)] = 0

    # Two corrections, both derived rather than fitted, that bring this onto
    # librosa's scale — which matters because BTC normalises with fixed
    # statistics baked into its checkpoint, so a constant offset in the
    # features is not harmless, it moves the whole input distribution.
    #
    #   1/n_fft   the convolution theorem's factor: evaluating the inner
    #             product in the frequency domain gives N times its value.
    #   sqrt(len) librosa's `scale=True`, which compensates for longer filters
    #             at low frequencies accumulating more energy.
    #
    # Measured against librosa these leave a residual spread of 0.07 in log
    # space across all 144 bins, against 2.06 uncorrected.
    lengths = np.array(
        [min(_kernel_length(float(f), q, sr), n_fft) for f in freqs],
        dtype=np.float32,
    )
    scale = (np.sqrt(lengths) / n_fft).astype(np.float32)[:, None]
    spec = spec * scale
    # Conjugated once here rather than per frame.
    return np.conj(spec), n_fft


class CQT:
    """Frame-synchronous constant-Q magnitudes at a fixed hop."""

    def __init__(
        self,
        sr: int = 22050,
        hop_length: int = 2048,
        n_bins: int = 144,
        bins_per_octave: int = 24,
    ) -> None:
        self.sr = int(sr)
        self.hop_length = int(hop_length)
        self.n_bins = int(n_bins)
        self.kernel, self.n_fft = cqt_kernel(
            sr=self.sr,
            n_bins=self.n_bins,
            bins_per_octave=int(bins_per_octave),
        )
        # The kernel is narrowband: BTC's covers 3213 of 32768 frequency
        # columns, all below Nyquist. So frames take a real FFT (half the
        # work of a complex one, same values below Nyquist) and multiply
        # only the columns any bin uses. Same magnitudes as the dense product
        # (zeros skipped), at ~10x less work: the live chord analyser's CQT
        # went from 45 ms to a few ms per audio second.
        self._cols = np.flatnonzero(np.abs(self.kernel).sum(axis=0) > 0)
        if self._cols.size and self._cols.max() > self.n_fft // 2:
            self._cols = None  # energy above Nyquist: keep the full complex FFT
        else:
            self._kernel_cols_t = np.ascontiguousarray(self.kernel[:, self._cols].T)

    def magnitudes(self, frames: np.ndarray) -> np.ndarray:
        """|CQT| of time-domain frames [n, n_fft] -> [n, n_bins] float32."""
        if self._cols is None:
            return np.abs(np.fft.fft(frames, axis=1) @ self.kernel.T).astype(np.float32)
        spec = np.fft.rfft(frames, axis=1)[:, self._cols]
        return np.abs(spec @ self._kernel_cols_t).astype(np.float32)

    def __call__(self, y: np.ndarray) -> np.ndarray:
        """(frames, n_bins) magnitudes, centred like librosa's `center=True`."""
        pad = self.n_fft // 2
        padded = np.pad(np.asarray(y, dtype=np.float32), pad, mode="constant")
        n_frames = 1 + max(0, (len(padded) - self.n_fft) // self.hop_length)
        if n_frames <= 0:
            return np.zeros((0, self.n_bins), dtype=np.float32)
        idx = (
            np.arange(self.n_fft)[None, :]
            + (np.arange(n_frames) * self.hop_length)[:, None]
        )
        # (frames, n_fft) @ (n_fft, bins); the kernel is already conjugated
        return self.magnitudes(padded[idx])


    def trailing(self, y: np.ndarray) -> np.ndarray:
        """(frames, n_bins) where each frame *ends* at its own timestamp.

        librosa centres frames, so a frame labelled t is built from audio up to
        t + n_fft/2 — half a second of the future. A live analyser has no such
        audio, so frames here look only backwards. The cost of the mismatch is
        measured in `research/eval/check_btc_backend.py` rather than assumed away.

        Frames are independent, so they are transformed a few dozen at a time.
        The index for a whole eight-minute file is about 3 GB.
        """
        y = np.asarray(y, dtype=np.float32)
        if len(y) < self.n_fft:
            y = np.pad(y, (self.n_fft - len(y), 0))
        n_frames = 1 + (len(y) - self.n_fft) // self.hop_length
        if n_frames <= 0:
            return np.zeros((0, self.n_bins), dtype=np.float32)
        return self.trailing_span(y, 0, n_frames)

    def trailing_span(self, y: np.ndarray, frame_lo: int, frame_hi: int) -> np.ndarray:
        """Frames `[frame_lo, frame_hi)`, each the same window `trailing` builds.

        A frame depends only on its own samples, so a later call for the next
        range continues the same grid. Nothing is recomputed at the join.
        """
        y = np.asarray(y, dtype=np.float32)
        frame_lo = int(frame_lo)
        frame_hi = int(frame_hi)
        if len(y) < self.n_fft:
            y = np.pad(y, (self.n_fft - len(y), 0))
        count = frame_hi - frame_lo
        if count <= 0:
            return np.zeros((0, self.n_bins), dtype=np.float32)
        out = np.empty((count, self.n_bins), dtype=np.float32)
        offsets = np.arange(self.n_fft)
        for start in range(0, count, _TRAILING_BLOCK):
            n = min(_TRAILING_BLOCK, count - start)
            frames = frame_lo + start + np.arange(n)
            starts = (frames * self.hop_length)[:, None]
            out[start : start + n] = self.magnitudes(y[offsets[None, :] + starts])
        return out


# 64 frames of a 32768-point FFT stays around 20 MB. The frames do not
# overlap in the transform, so the block size does not change the result.
_TRAILING_BLOCK = 64


def log_cqt(magnitudes: np.ndarray) -> np.ndarray:
    """The exact compression BTC was trained with."""
    return np.log(magnitudes + 1e-6).astype(np.float32)


class StreamResampler:
    """Rate conversion that tiles continuously across chunk boundaries.

    Resampling each 100 ms chunk independently is the obvious thing and is
    wrong: the output positions of one chunk stop short of its final input
    sample, so every chunk drops a fraction of a sample and inserts a phase
    discontinuity. Measured, that lost ~2 samples per chunk and moved BTC's
    features by 2.3 normalised units — enough to change the predicted chord
    from C to "no chord" — while the offline path stayed correct, which is the
    kind of divergence that would never have shown up outside a test.
    """

    def __init__(self, src_sr: int, dst_sr: int) -> None:
        self.step = src_sr / dst_sr
        self._buf = np.zeros(0, dtype=np.float32)
        self._pos = 0.0

    def reset(self) -> None:
        self._buf = np.zeros(0, dtype=np.float32)
        self._pos = 0.0

    def __call__(self, chunk: np.ndarray) -> np.ndarray:
        self._buf = np.concatenate([self._buf, np.asarray(chunk, dtype=np.float32)])
        if len(self._buf) < 2:
            return np.zeros(0, dtype=np.float32)
        n = int(np.floor((len(self._buf) - 1 - self._pos) / self.step)) + 1
        if n <= 0:
            return np.zeros(0, dtype=np.float32)
        idx = self._pos + np.arange(n) * self.step
        out = np.interp(idx, np.arange(len(self._buf)), self._buf)
        nxt = self._pos + n * self.step
        drop = int(np.floor(nxt))
        self._pos = nxt - drop
        self._buf = self._buf[drop:]
        return out.astype(np.float32)


def resample_linear(y: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """Cheap rate conversion, matching what the capture layer already does.

    The pipeline runs at 16 kHz for Whisper and BTC wants 22.05 kHz, so this
    runs on every frame. Linear interpolation is a mild low-pass on the way up,
    which is the benign direction; `check_btc_backend.py` measures what it
    costs in chord agreement rather than assuming it is free.
    """
    if src_sr == dst_sr:
        return np.asarray(y, dtype=np.float32)
    n_out = round(len(y) * dst_sr / src_sr)
    if n_out <= 1:
        return np.zeros(0, dtype=np.float32)
    src_t = np.arange(len(y), dtype=np.float64)
    dst_t = np.arange(n_out, dtype=np.float64) * (src_sr / dst_sr)
    return np.interp(dst_t, src_t, np.asarray(y, dtype=np.float64)).astype(np.float32)
