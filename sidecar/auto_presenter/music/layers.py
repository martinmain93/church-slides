"""Multi-band onsets, a tempo that is allowed to move, and chord spans.

Numpy only. Beat This and Basic Pitch live beside this, because a single
BPM drawn across a whole phrase is what made the earlier grid look wrong
when the band sped up or slowed down inside it.

The pulse is a tempo estimate on a short trailing-or-centred window, stepped
along the file. Each window picks its own lag, so a ritardando shows up as
a slope rather than a smear against a fixed grid.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np

from auto_presenter.audio.types import SAMPLE_RATE
from auto_presenter.music.features import CHORD_NAMES, chord_scores, chroma

SR = SAMPLE_RATE
N_FFT = 2048
HOP = 512
FPS = SR / HOP

BANDS: tuple[tuple[str, float, float], ...] = (
    ("kick", 30.0, 90.0),
    ("bass", 90.0, 250.0),
    ("body", 250.0, 700.0),
    ("strum", 700.0, 2500.0),
    ("air", 2500.0, 7500.0),
)

TEMPO_MIN = 50.0
TEMPO_MAX = 180.0
PULSE_WINDOW_S = 6.0
PULSE_HOP_S = 0.5


@dataclass(frozen=True)
class ChordSpan:
    name: str
    t_start: float
    t_end: float


@dataclass(frozen=True)
class PulsePoint:
    t_seconds: float
    bpm: float
    lock: float


def _stft_mag(audio: np.ndarray) -> np.ndarray:
    x = np.ascontiguousarray(audio, dtype=np.float32)
    if len(x) < N_FFT:
        x = np.pad(x, (0, N_FFT - len(x)))
    n = 1 + (len(x) - N_FFT) // HOP
    frames = np.lib.stride_tricks.as_strided(
        x,
        shape=(n, N_FFT),
        strides=(x.strides[0] * HOP, x.strides[0]),
    )
    window = np.hanning(N_FFT).astype(np.float32)
    return np.abs(np.fft.rfft(frames * window, axis=1)).astype(np.float32)


def _onset_env(energy: np.ndarray) -> np.ndarray:
    loge = np.log1p(energy.astype(np.float64))
    delta = np.diff(loge, prepend=loge[0])
    delta[delta < 0] = 0.0
    return np.convolve(delta, np.array([0.25, 0.5, 0.25]), mode="same")


def band_onsets(audio: np.ndarray) -> dict[str, list[float]]:
    """Peak times, in seconds, of the onset envelope inside each band."""
    mag = _stft_mag(audio)
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / SR)
    out: dict[str, list[float]] = {}
    for name, lo, hi in BANDS:
        env = _onset_env(mag[:, (freqs >= lo) & (freqs < hi)].sum(axis=1))
        if env.size == 0 or float(env.max()) <= 0:
            out[name] = []
            continue
        out[name] = _peak_times(env)
    return out


def signal_onsets(audio: np.ndarray) -> list[float]:
    """Peak times of the whole signal, the same picker the bands use."""
    mag = _stft_mag(audio)
    return _peak_times(_onset_env(mag.sum(axis=1)))


def _peak_times(env: np.ndarray) -> list[float]:
    if env.size == 0 or float(env.max()) <= 0:
        return []
    min_gap = round(0.05 * FPS)
    thresh = float(np.median(env) + np.std(env))
    times: list[float] = []
    last = -min_gap
    for i in range(1, len(env) - 1):
        if i - last < min_gap:
            continue
        if env[i] >= thresh and env[i] >= env[i - 1] and env[i] > env[i + 1]:
            # The frame is labelled at the window centre. Labelling it at
            # the window start puts every tick a tenth of a second early,
            # because the click is already inside the window by then.
            times.append(round((i * HOP + N_FFT / 2) / SR, 3))
            last = i
    return times


def _autocorr(env: np.ndarray) -> np.ndarray:
    x = env - float(env.mean())
    n = len(x)
    spec = np.fft.rfft(x, n=2 * n)
    ac = np.fft.irfft(spec * np.conj(spec), n=2 * n)[:n]
    if ac[0] <= 1e-12:
        return np.zeros(n)
    return ac / ac[0]


def _tempo_of(env: np.ndarray) -> tuple[float, float]:
    """BPM of one window, walking off the half-tempo peak when the finer lag holds.

    A steady quarter-note grid also lines up every two beats, and that longer
    lag is often the taller one. A kick with empty offbeats stays slow,
    because the halfway lag is weak.
    """
    ac = _autocorr(env)
    min_lag = max(2, round(FPS * 60.0 / TEMPO_MAX))
    max_lag = min(len(ac) - 1, round(FPS * 60.0 / TEMPO_MIN))
    if max_lag <= min_lag:
        return 0.0, 0.0
    lag = int(np.argmax(ac[min_lag:max_lag])) + min_lag
    while True:
        finer = lag // 2
        if finer < min_lag:
            break
        lo = max(min_lag, finer - 1)
        hi = min(max_lag, finer + 2)
        finer = int(np.argmax(ac[lo:hi])) + lo
        if ac[finer] < 0.8 * ac[lag]:
            break
        lag = finer
    if 0 < lag < len(ac) - 1:
        a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
        denom = a - 2 * b + c
        delta = 0.0 if abs(denom) < 1e-9 else float(np.clip(0.5 * (a - c) / denom, -0.5, 0.5))
    else:
        delta = 0.0
    return 60.0 * FPS / (lag + delta), float(ac[lag])


def pulse_track(audio: np.ndarray) -> list[PulsePoint]:
    """Local tempo along the file. Each point is the centre of its window."""
    mag = _stft_mag(audio)
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / SR)
    mixed = np.zeros(mag.shape[0], dtype=np.float64)
    for _name, lo, hi in BANDS:
        mixed += _onset_env(mag[:, (freqs >= lo) & (freqs < hi)].sum(axis=1))
    win = round(PULSE_WINDOW_S * FPS)
    hop = round(PULSE_HOP_S * FPS)
    if win < 8 or len(mixed) < win:
        bpm, lock = _tempo_of(mixed) if len(mixed) else (0.0, 0.0)
        if bpm <= 0:
            return []
        return [PulsePoint(round(len(audio) / SR / 2, 2), round(bpm, 1), round(lock, 3))]
    points: list[PulsePoint] = []
    for i in range(0, len(mixed) - win + 1, hop):
        bpm, lock = _tempo_of(mixed[i : i + win])
        # Parabolic interpolation can step just outside the search band.
        # Those points are an octave error, and one of them yanks the line.
        if not (TEMPO_MIN <= bpm <= TEMPO_MAX):
            continue
        t = (i + win / 2) / FPS
        points.append(PulsePoint(round(t, 2), round(bpm, 1), round(lock, 3)))
    return points


def _collapse(names: list[str], times: list[float], duration: float) -> list[ChordSpan]:
    spans: list[ChordSpan] = []
    if not names:
        return spans
    start = times[0]
    current = names[0]
    for name, t in zip(names[1:], times[1:], strict=True):
        if name != current:
            if current and current != "N":
                spans.append(ChordSpan(current, round(start, 3), round(t, 3)))
            current = name
            start = t
    end = duration
    if current and current != "N":
        spans.append(ChordSpan(current, round(start, 3), round(end, 3)))
    return spans


def template_chords(audio: np.ndarray) -> list[ChordSpan]:
    """Triad spans from the numpy template matcher. The fallback when BTC is absent."""
    ch = chroma(audio)
    if len(ch) == 0:
        return []
    hop_s = 2048 / SR
    names: list[str] = []
    times: list[float] = []
    for i, frame in enumerate(ch):
        scores = chord_scores(frame)
        k = int(np.argmax(scores))
        if float(scores[k]) < 0.55:
            names.append("N")
        else:
            names.append(CHORD_NAMES[k])
        # Centre of the 256 ms analysis window, not its start.
        times.append(i * hop_s + (4096 / 2) / SR)
    return _collapse(names, times, len(audio) / SR)


class ChordRun:
    """BTC blocks on the same frame grid a full-file pass uses.

    Frames are computed in order and the model only sees a complete block of
    `timestep` frames, the same blocks `btc_chords` would. The last block is
    padded only when the file is finished, which is also when a full pass
    pads it. Publishing earlier does not move a chord that was already decided.
    """

    def __init__(self, audio: np.ndarray):
        self.spans: list[ChordSpan] = []
        self.backend = "template"
        self.committed = 0.0
        self._audio = audio
        self._duration = len(audio) / SR if len(audio) else 0.0
        self._ready = False
        self._mode = "template"
        self._btc = None
        self._y: np.ndarray | None = None
        self._sr = 22050.0
        self._hop = 2048
        self._n_fft = 0
        self._n_frames = 0
        self._computed = 0
        self._feat: np.ndarray | None = None

    def advance_to(self, t: float) -> None:
        self._ensure()
        if self._mode != "btc" or self._btc is None or self._y is None:
            self.committed = self._duration
            return
        final = t >= self._duration - 1e-3
        if final:
            want = self._n_frames
        else:
            want = int(np.floor((t * self._sr - self._n_fft) / self._hop)) + 1
            want = max(0, min(self._n_frames, want))
        self._compute(want)
        self._label(final)

    def _ensure(self) -> None:
        if self._ready:
            return
        self._ready = True
        from auto_presenter.music.btc import BtcChordBackend

        backend = BtcChordBackend()
        if not backend.available or backend._sess is None or backend._cqt is None:
            self._use_template()
            return
        from auto_presenter.music.cqt import resample_linear

        sr = int(backend._meta["sample_rate"])
        self._y = resample_linear(self._audio, 16000, sr)
        self._sr = float(sr)
        # A float hop makes `//` return a float, and the last chunk then
        # hands that count to `range` and `np.empty`.
        self._hop = int(backend._meta["hop_length"])
        self._n_fft = int(backend._cqt.n_fft)
        self._n_frames = 1 + max(0, (len(self._y) - self._n_fft) // self._hop)
        if self._n_frames < backend.timestep:
            self._use_template()
            return
        self._btc = backend
        self._mode = "btc"
        self.backend = "btc"

    def _use_template(self) -> None:
        self.spans = template_chords(self._audio)
        self.backend = "template"
        self._mode = "template"
        self.committed = self._duration

    def _compute(self, want: int) -> None:
        if self._btc is None or self._y is None or want <= self._computed:
            return
        from auto_presenter.music.cqt import log_cqt

        mags = self._btc._cqt.trailing_span(self._y, self._computed, want)
        feat = (log_cqt(mags) - float(self._btc._meta["mean"])) / float(self._btc._meta["std"])
        self._feat = feat if self._feat is None else np.concatenate([self._feat, feat], axis=0)
        self._computed = want

    def _label(self, final: bool) -> None:
        if self._btc is None or self._feat is None or len(self._feat) == 0:
            return
        n = self._btc.timestep
        limit = int(self._n_frames) if final else (len(self._feat) // n) * n
        if limit <= 0:
            return
        labels = self._btc.labels
        names: list[str] = []
        for start in range(0, limit, n):
            block = self._feat[start : start + n]
            if len(block) < n:
                if not final:
                    break
                pad = np.repeat(block[-1:], n - len(block), axis=0)
                block = np.concatenate([block, pad], axis=0)
            logits = self._btc._sess.run(
                ["logits"], {"features": block[None, :, :].astype(np.float32)}
            )[0][0]
            use = int(min(n, (int(self._n_frames) if final else limit) - start))
            for j in range(use):
                names.append(labels[int(np.argmax(logits[j]))])
        times = [(i * self._hop + self._n_fft / 2) / self._sr for i in range(len(names))]
        end = self._duration if final else (times[-1] if times else 0.0)
        self.spans = _collapse(names, times, end)
        self.committed = end
        self.backend = "btc"


def btc_chords(audio: np.ndarray) -> tuple[list[ChordSpan], str]:
    """Chord spans from the shipped BTC model, or the template matcher."""
    run = ChordRun(audio)
    run.advance_to(1e9)
    return run.spans, run.backend


def pulse_from_beats(beats: list[float]) -> list[PulsePoint]:
    """Local tempo from the gaps between detected beats.

    This is the pulse the beat row is already drawing. An onset
    autocorrelation beside it can sit an octave away and look like a
    second, contradictory grid.
    """
    if len(beats) < 3:
        return []
    gaps: list[float] = []
    at: list[float] = []
    for a, b in itertools.pairwise(beats):
        if b <= a:
            continue
        gaps.append(b - a)
        at.append(b)
    points: list[PulsePoint] = []
    for i, t in enumerate(at):
        window = gaps[max(0, i - 3) : i + 1]
        dt = float(np.median(window))
        if not (60.0 / TEMPO_MAX <= dt <= 60.0 / TEMPO_MIN):
            continue
        points.append(PulsePoint(round(t, 3), round(60.0 / dt, 1), 1.0))
    return points
