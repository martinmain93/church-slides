"""Chroma and onset features for the music side channel. numpy only.

Deliberately numpy rather than a learned model: the sidecar ships
onnxruntime and numpy and nothing else, and the measured cost of this front
end is around 0.002 realtime against Whisper's ~0.31, so it can run beside
the ASR pass without competing for the cores that keep it inside budget.

What this is **not**: accurate. Measured against chord charts on the approved
goldens, template matching over plain-FFT chroma sits near the break-even
point where a chord feature starts helping the matcher rather than hurting it
(see docs/research/CHORD_DOWNBEAT.md section 7). A learned recogniser — BTC is 3 M
parameters and MIT — is roughly twice as accurate and is the intended
replacement; `music/analyser.py` keeps the seam for it.
"""

from __future__ import annotations

import numpy as np

from auto_presenter.audio.types import SAMPLE_RATE

#: Harmony front end. 256 ms is long enough to resolve the low notes that
#: decide a triad's root; chords move at about 1 Hz so the hop can be coarse.
CH_NFFT = 4096
CH_HOP = 2048

#: Onset front end. 16 ms hop, because beat *phase* is the quantity of
#: interest and a coarser hop would put much of the error in the grid itself.
ON_NFFT = 1024
ON_HOP = 256
ON_FPS = SAMPLE_RATE / ON_HOP

PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _log_freq_weights(n_fft: int = CH_NFFT) -> np.ndarray:
    """(12, bins): each pitch class gathered from +/-50 cents around every one
    of its octaves, A1..B6, each band normalised by its width so the spectral
    tilt does not let bass swamp the triad."""
    freqs = np.fft.rfftfreq(n_fft, 1.0 / SAMPLE_RATE)
    w = np.zeros((12, len(freqs)), dtype=np.float32)
    for midi in range(33, 96):
        f0 = 440.0 * 2 ** ((midi - 69) / 12.0)
        band = (freqs > f0 * 2 ** (-0.5 / 12)) & (freqs < f0 * 2 ** (0.5 / 12))
        if band.any():
            w[midi % 12, band] += 1.0 / band.sum()
    return w


_CH_W = _log_freq_weights()


def _frames(audio: np.ndarray, n_fft: int, hop: int) -> np.ndarray:
    n = 1 + max(0, (len(audio) - n_fft) // hop)
    if n <= 0:
        return np.zeros((0, n_fft // 2 + 1), dtype=np.float32)
    idx = np.arange(n_fft)[None, :] + (np.arange(n) * hop)[:, None]
    win = np.hanning(n_fft).astype(np.float32)
    return np.abs(np.fft.rfft(audio[idx] * win, axis=1)).astype(np.float32)


def chroma(audio: np.ndarray) -> np.ndarray:
    """(frames, 12), each frame L2-normalised."""
    mag = _frames(audio, CH_NFFT, CH_HOP)
    if not len(mag):
        return np.zeros((0, 12), dtype=np.float32)
    ch = mag @ _CH_W.T
    return ch / np.maximum(np.linalg.norm(ch, axis=1, keepdims=True), 1e-9)


def onset_envelope(audio: np.ndarray) -> np.ndarray:
    """Half-wave-rectified log-magnitude spectral flux.

    Log first: a drum hit over a loud band is a small *linear* change and a
    large ratio, and worship mixes are loud throughout.
    """
    mag = _frames(audio, ON_NFFT, ON_HOP)
    if len(mag) < 2:
        return np.zeros(len(mag), dtype=np.float32)
    logm = np.log1p(1000.0 * mag)
    diff = np.diff(logm, axis=0, prepend=logm[:1])
    return np.maximum(diff, 0.0).sum(axis=1).astype(np.float32)


def chord_templates() -> tuple[np.ndarray, list[str]]:
    """24 triads, root weighted double.

    Without the root emphasis a binary triad mask cannot separate C major from
    A minor's upper voices — they share two of three notes.
    """
    rows, names = [], []
    for root in range(12):
        for third, suffix in ((4, ""), (3, "m")):
            v = np.zeros(12, dtype=np.float32)
            v[root] = 2.0
            v[(root + third) % 12] = 1.0
            v[(root + 7) % 12] = 1.0
            rows.append(v / np.linalg.norm(v))
            names.append(PITCH_NAMES[root] + suffix)
    return np.array(rows, dtype=np.float32), names


CHORD_TEMPLATES, CHORD_NAMES = chord_templates()


def chord_scores(frame_chroma: np.ndarray) -> np.ndarray:
    """Cosine similarity of one chroma vector against each of the 24 triads."""
    if frame_chroma.ndim != 1:
        raise ValueError("chord_scores expects a single chroma vector")
    n = np.linalg.norm(frame_chroma)
    if n < 1e-9:
        return np.zeros(len(CHORD_NAMES), dtype=np.float32)
    return (CHORD_TEMPLATES @ (frame_chroma / n)).astype(np.float32)


def softmax(x: np.ndarray, temperature: float = 0.05) -> np.ndarray:
    """Turn template similarities into something readable as confidence.

    Cosines against triad templates bunch in a narrow band (typically
    0.6-0.95), so a plain normalisation shows every chord at roughly 1/24 and
    tells the eye nothing. The temperature spreads them; it is a display
    decision, not a calibrated probability, and nothing downstream depends on
    the value being one.
    """
    z = (x - x.max()) / max(temperature, 1e-6)
    e = np.exp(z)
    s = e.sum()
    return (e / s).astype(np.float32) if s > 0 else np.full_like(e, 1.0 / len(e))
