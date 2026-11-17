"""The CQT is verified against physics, not against itself.

It exists to reproduce what BTC was trained on, and a transform that is subtly
wrong does not fail — it feeds the model a shifted distribution and gets
confident nonsense back. `research/eval/check_btc_backend.py` pins agreement with
librosa on real audio (99.8% of chord labels); these are the cheap invariants
that can run without librosa or the model present.
"""

from __future__ import annotations

import numpy as np

from auto_presenter.music.cqt import CQT, FMIN_C1, log_cqt, resample_linear

SR = 22050


def _tone(freq: float, seconds: float, sr: int = SR) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _bin_of(freq: float, bins_per_octave: int = 24) -> int:
    return round(bins_per_octave * np.log2(freq / FMIN_C1))


def test_a_pure_tone_peaks_in_its_own_bin():
    c = CQT()
    for note in (110.0, 220.0, 440.0, 880.0):
        mags = c(_tone(note, 3.0))
        peak = int(np.argmax(mags.mean(axis=0)))
        assert abs(peak - _bin_of(note)) <= 1, f"{note} Hz landed in bin {peak}"


def test_bins_are_a_quarter_tone_apart():
    """24 bins per octave means an octave is exactly 24 bins up."""
    c = CQT()
    low = int(np.argmax(c(_tone(220.0, 2.0)).mean(axis=0)))
    high = int(np.argmax(c(_tone(440.0, 2.0)).mean(axis=0)))
    assert high - low == 24


def test_octaves_report_comparable_magnitude():
    """The sqrt(len) scaling exists so a low note is not louder by construction.

    Without it the lowest bins read several log units above the highest, which
    is the error that shifted the whole feature distribution off BTC's
    normalisation.
    """
    c = CQT()
    levels = [
        float(np.max(c(_tone(f, 2.0)).mean(axis=0))) for f in (110.0, 440.0)
    ]
    ratio = max(levels) / min(levels)
    assert ratio < 4.0, f"octave imbalance {ratio:.1f}x"


def test_trailing_frames_do_not_look_into_the_future():
    """A trailing frame must be unaffected by audio that comes after it."""
    c = CQT()
    head = _tone(440.0, 3.0)
    quiet = np.concatenate([head, np.zeros(SR * 2, dtype=np.float32)])
    loud = np.concatenate([head, _tone(110.0, 2.0)])
    a = c.trailing(quiet)
    b = c.trailing(loud)
    n = min(len(a), len(b))
    # The first frames cover only the shared prefix.
    assert np.allclose(a[: n // 3], b[: n // 3], atol=1e-4)


def test_trailing_block_edges_match_a_single_frame():
    """A frame on either side of a block boundary is the same FFT as alone."""
    c = CQT()
    y = _tone(440.0, 8.0)
    got = c.trailing(y)
    hop = c.hop_length
    n_fft = c.n_fft
    for i in (0, 63, 64):
        assert i < len(got)
        frame = y[i * hop : i * hop + n_fft]
        alone = np.abs(np.fft.fft(frame) @ c.kernel.T).astype(np.float32)
        assert np.allclose(got[i], alone, atol=1e-4)
    span = c.trailing_span(y, 60, 70)
    assert np.allclose(span, got[60:70], atol=1e-4)


def test_log_compression_matches_the_training_transform():
    x = np.array([[0.0, 1.0]], dtype=np.float32)
    assert np.allclose(log_cqt(x), np.log(x + 1e-6))


def test_resampling_preserves_a_tone():
    y = _tone(440.0, 1.0, sr=16000)
    up = resample_linear(y, 16000, 22050)
    assert abs(len(up) - 22050) <= 2
    c = CQT()
    peak = int(np.argmax(c(up).mean(axis=0)))
    assert abs(peak - _bin_of(440.0)) <= 1


def test_resampling_is_a_no_op_at_the_same_rate():
    y = _tone(440.0, 0.1, sr=16000)
    assert np.shares_memory(resample_linear(y, 16000, 16000), y) or np.allclose(
        resample_linear(y, 16000, 16000), y
    )


def test_the_sparse_real_fft_path_matches_the_dense_product():
    """`CQT.magnitudes` takes a real FFT and multiplies only the kernel's
    nonzero columns: the same magnitudes as the full complex product."""
    cqt = CQT(sr=22050, hop_length=2048, n_bins=144, bins_per_octave=24)
    assert cqt._cols is not None and len(cqt._cols) < cqt.n_fft // 4
    rng = np.random.default_rng(0)
    frames = rng.normal(size=(5, cqt.n_fft)).astype(np.float32)
    sparse = cqt.magnitudes(frames)
    dense = np.abs(np.fft.fft(frames, axis=1) @ cqt.kernel.T)
    assert np.allclose(sparse, dense, rtol=1e-4, atol=1e-4 * dense.max())
