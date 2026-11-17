"""Streaming chord and beat analysis for the music side channel.

Fed one `AudioFrame` at a time from the capture fan-out, alongside the ASR
pass rather than in place of it. Emits a small update roughly ten times a
second: recent audio levels, the chord it currently believes, the three
candidates it is choosing between, and any beats it placed.

Everything here is **causal**. The spike work that preceded it was offline and
could look forward; a live analyser cannot, and that difference is not
cosmetic — online downbeat tracking scores 46.8 F1 in the literature against
78.3 offline. Read what this emits with that in mind.

Honest limits, measured (Plan/CHORD_DOWNBEAT.md):

* Chords come from template matching over plain-FFT chroma. Against chord
  charts on the approved goldens that lands near the break-even point at which
  a chord feature helps the matcher at all. It is here to be *looked at* first
  and trusted second; `ChordBackend` is the seam where a learned recogniser
  replaces it.
* Beat tracking is a causal tempo-and-phase tracker over spectral flux. On
  three of seven approved goldens the offline version of this sat at or below
  its own null. It is a visualisation aid, and nothing decides anything on it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from auto_presenter.audio.types import SAMPLE_RATE, AudioFrame
from auto_presenter.music.features import (
    CH_NFFT,
    CHORD_NAMES,
    ON_FPS,
    ON_HOP,
    ON_NFFT,
    chord_scores,
    chroma,
    softmax,
)

#: Seconds of audio kept for harmonic analysis.
CHORD_WINDOW_S = 1.2

#: Seconds of onset envelope kept for tempo estimation. Long enough that the
#: autocorrelation has several bars to work with, short enough to follow a band
#: that drifts.
ONSET_WINDOW_S = 8.0

#: Level samples per second sent to the UI. 50 Hz draws a recognisable envelope
#: in a 10 s window without sending audio over the socket.
LEVEL_HZ = 50

TEMPO_MIN, TEMPO_MAX = 50.0, 190.0
TEMPO_PRIOR_BPM = 100.0
TEMPO_PRIOR_OCTAVES = 1.0

#: How often the tempo estimate is refreshed. Tempo moves slowly and the
#: autocorrelation is the most expensive thing here.
TEMPO_INTERVAL_S = 0.5

#: Smoothing on the chord scores. Chords last about a bar; without this the
#: display flickers on every passing note and is unreadable.
CHORD_SMOOTHING = 0.6

#: How often the learned recogniser runs. Chords change about once a second, so
#: inferring ten times a second buys nothing and costs 8.5% of a core against
#: 3.4% at this rate — measurable beside Whisper's ~31%.
BTC_INTERVAL_S = 0.25


@dataclass(frozen=True)
class ChordCandidate:
    name: str
    confidence: float


@dataclass(frozen=True)
class MusicUpdate:
    """One tick of analysis, covering the audio since the previous tick."""

    t_end: float
    #: Envelope of the new audio only, at LEVEL_HZ. The UI keeps the scroll
    #: buffer, so each message carries about 100 ms rather than the window.
    levels: list[float]
    #: Best chord, or None when harmony is off or the audio is too quiet.
    chord: str | None
    #: The three the analyser is choosing between, best first.
    candidates: list[ChordCandidate]
    #: Beat times placed within this tick.
    beats: list[float]
    bpm: float
    #: Share of one core the analyser used, averaged over recent ticks.
    realtime_factor: float
    #: "btc" or "template" — which recogniser produced `chord`.
    backend: str = "template"
    #: BTC's full 25-class distribution (C, Cm, ..., B, Bm, N) behind `chord`,
    #: when the learned recogniser produced it — what the score follower
    #: scores against the lead sheet's chords. None from templates.
    chord_probs: np.ndarray | None = None


@dataclass
class _BeatState:
    period: float = 0.0
    next_beat: float = 0.0
    established: bool = False


@dataclass
class MusicAnalyser:
    """Causal chord and beat analysis over a live audio stream."""

    chords_enabled: bool = True
    beats_enabled: bool = True
    #: The learned recogniser, when its model is installed. Absent by default,
    #: in which case chords come from template matching — about half as
    #: accurate, and the panel says which is in use.
    chord_backend: object | None = None

    _audio: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    _t_end: float = 0.0
    #: Audio not yet consumed by the onset framer (shorter than one FFT).
    _onset_carry: np.ndarray = field(
        default_factory=lambda: np.zeros(0, dtype=np.float32)
    )
    _prev_logm: np.ndarray | None = None
    _env: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    _env_t0: float = 0.0
    _scores: np.ndarray | None = None
    _beat: _BeatState = field(default_factory=_BeatState)
    _last_tempo_at: float = -1e9
    _last_btc_at: float = -1e9
    _btc_probs: np.ndarray | None = None
    _cost: float = 0.0
    _elapsed: float = 0.0

    @property
    def backend_name(self) -> str:
        b = self.chord_backend
        return "btc" if (b is not None and getattr(b, "available", False)) else "template"

    # ------------------------------------------------------------------ feed

    def feed(self, frame: AudioFrame) -> MusicUpdate:
        started = time.perf_counter()
        self._t_end = frame.t_end
        samples = np.asarray(frame.samples, dtype=np.float32)

        keep = int(max(CHORD_WINDOW_S, 0.5) * SAMPLE_RATE) + CH_NFFT
        self._audio = np.concatenate([self._audio, samples])[-keep:]

        levels = self._levels(samples)
        if self.beats_enabled:
            self._advance_onsets(samples)
        beats = self._track_beats() if self.beats_enabled else []
        if self.chords_enabled:
            self._advance_btc(samples)
            chord, candidates = self._track_chords()
        else:
            chord, candidates = None, []

        # Cost is reported as a share of one core, exponentially smoothed, so
        # the panel can show that this is not competing with the ASR pass.
        spent = time.perf_counter() - started
        self._cost += spent
        self._elapsed += frame.duration_seconds
        rtf = self._cost / self._elapsed if self._elapsed > 0 else 0.0

        return MusicUpdate(
            t_end=frame.t_end,
            levels=levels,
            chord=chord,
            candidates=candidates,
            beats=beats,
            bpm=60.0 / self._beat.period if self._beat.period > 0 else 0.0,
            realtime_factor=rtf,
            backend=self.backend_name,
            chord_probs=(
                self._btc_probs if self.chords_enabled and self._btc_probs is not None else None
            ),
        )

    # -------------------------------------------------------------- internals

    def _levels(self, samples: np.ndarray) -> list[float]:
        block = max(1, SAMPLE_RATE // LEVEL_HZ)
        n = len(samples) // block
        if n <= 0:
            return []
        trimmed = samples[: n * block].reshape(n, block)
        return [float(v) for v in np.sqrt((trimmed ** 2).mean(axis=1))]

    def _advance_onsets(self, samples: np.ndarray) -> None:
        """Extend the onset envelope with the new audio only.

        Recomputing the whole window every tick would be correct but wasteful;
        the carry buffer and the retained previous spectrum make this
        incremental, which is what keeps the cost negligible.
        """
        buf = np.concatenate([self._onset_carry, samples])
        n = 1 + (len(buf) - ON_NFFT) // ON_HOP if len(buf) >= ON_NFFT else 0
        if n <= 0:
            self._onset_carry = buf
            return
        idx = np.arange(ON_NFFT)[None, :] + (np.arange(n) * ON_HOP)[:, None]
        win = np.hanning(ON_NFFT).astype(np.float32)
        mag = np.abs(np.fft.rfft(buf[idx] * win, axis=1))
        logm = np.log1p(1000.0 * mag).astype(np.float32)
        prev = self._prev_logm if self._prev_logm is not None else logm[:1]
        stacked = np.concatenate([prev, logm], axis=0)
        flux = np.maximum(np.diff(stacked, axis=0), 0.0).sum(axis=1).astype(np.float32)
        self._prev_logm = logm[-1:]
        self._onset_carry = buf[n * ON_HOP:]

        self._env = np.concatenate([self._env, flux])
        cap = int(ONSET_WINDOW_S * ON_FPS)
        if len(self._env) > cap:
            dropped = len(self._env) - cap
            self._env = self._env[-cap:]
            self._env_t0 += dropped / ON_FPS

    def _estimate_tempo(self) -> float:
        env = self._env
        if len(env) < ON_FPS * 3:
            return 0.0
        x = env - env.mean()
        ac = np.correlate(x, x, mode="full")[len(x) - 1:]
        if ac[0] <= 0:
            return 0.0
        ac = ac / ac[0]
        lags = np.arange(len(ac)) / ON_FPS
        lo, hi = 60.0 / TEMPO_MAX, 60.0 / TEMPO_MIN
        ok = (lags >= lo) & (lags <= hi)
        if not ok.any():
            return 0.0
        bpm = np.zeros(len(ac))
        bpm[ok] = 60.0 / np.maximum(lags[ok], 1e-9)
        # A log-normal prior on tempo. Without it the autocorrelation is just as
        # happy at half or double the true rate, which is the classic failure.
        prior = np.zeros(len(ac))
        prior[ok] = np.exp(
            -0.5 * (np.log2(bpm[ok] / TEMPO_PRIOR_BPM) / TEMPO_PRIOR_OCTAVES) ** 2
        )
        scored = np.where(ok, ac * prior, -np.inf)
        return float(lags[int(np.argmax(scored))])

    def _track_beats(self) -> list[float]:
        if len(self._env) < ON_FPS * 3:
            return []
        now = self._t_end
        if now - self._last_tempo_at >= TEMPO_INTERVAL_S:
            self._last_tempo_at = now
            period = self._estimate_tempo()
            if period > 0:
                self._beat.period = (
                    period if self._beat.period <= 0
                    else 0.7 * self._beat.period + 0.3 * period
                )
        period = self._beat.period
        if period <= 0:
            return []

        if not self._beat.established:
            # Start the grid on the strongest recent onset rather than on an
            # arbitrary instant, so the first beat is at least plausible.
            peak = int(np.argmax(self._env))
            self._beat.next_beat = self._env_t0 + peak / ON_FPS + period
            self._beat.established = True
            return []

        out: list[float] = []
        tol = period * 0.25
        env_end = self._env_t0 + len(self._env) / ON_FPS
        # A beat cannot be confirmed until the envelope has passed it.
        #
        # Placing it the instant it falls due leaves only the *left* half of the
        # search window in existence, so the snap finds the previous onset,
        # biases every beat early, and the error compounds through the
        # re-anchoring. Waiting a quarter period costs ~150 ms of latency at
        # 100 BPM and is what makes the placement correct.
        guard = 0
        while self._beat.next_beat + tol <= env_end and guard < 8:
            guard += 1
            target = self._beat.next_beat
            a = int((target - tol - self._env_t0) * ON_FPS)
            b = int((target + tol - self._env_t0) * ON_FPS)
            a, b = max(0, a), min(len(self._env), b)
            if b > a:
                local = self._env_t0 + (a + int(np.argmax(self._env[a:b]))) / ON_FPS
                placed = local if abs(local - target) <= tol else target
            else:
                placed = target
            out.append(round(placed, 3))
            # Re-anchor phase on where the beat actually landed, but keep the
            # period from the tempo estimate.
            self._beat.next_beat = placed + period
        return out

    def _advance_btc(self, samples: np.ndarray) -> None:
        """Feed the learned recogniser, and run it on its own slower cadence."""
        b = self.chord_backend
        if b is None or not getattr(b, "available", False):
            return
        try:
            b.push(samples)
            if self._t_end - self._last_btc_at >= BTC_INTERVAL_S:
                self._last_btc_at = self._t_end
                probs = b.predict_stream()
                if probs is not None:
                    self._btc_probs = probs
        except Exception:
            # Fall back to templates rather than losing chords entirely.
            self.chord_backend = None
            self._btc_probs = None

    def _chords_from_btc(self) -> tuple[str | None, list[ChordCandidate]] | None:
        probs = self._btc_probs
        b = self.chord_backend
        if probs is None or b is None:
            return None
        labels = list(getattr(b, "labels", []))
        if len(labels) != len(probs):
            return None
        order = np.argsort(probs)[::-1][:3]
        cands = [
            ChordCandidate(name=labels[int(i)], confidence=float(probs[int(i)]))
            for i in order
        ]
        top = cands[0].name
        # "N" is BTC's no-chord class. Reporting it as a chord named N would
        # draw a band over silence and instrumental passages.
        return (None if top == "N" else top), cands

    def _track_chords(self) -> tuple[str | None, list[ChordCandidate]]:
        learned = self._chords_from_btc()
        if learned is not None:
            return learned
        need = CH_NFFT
        if len(self._audio) < need:
            return None, []
        window = self._audio[-int(CHORD_WINDOW_S * SAMPLE_RATE):]
        frames = chroma(window)
        if not len(frames):
            return None, []
        vec = frames.mean(axis=0)
        if np.linalg.norm(vec) < 1e-6:
            return None, []
        scores = chord_scores(vec)
        self._scores = (
            scores if self._scores is None
            else CHORD_SMOOTHING * self._scores + (1 - CHORD_SMOOTHING) * scores
        )
        probs = softmax(self._scores)
        order = np.argsort(probs)[::-1][:3]
        cands = [
            ChordCandidate(name=CHORD_NAMES[int(i)], confidence=float(probs[int(i)]))
            for i in order
        ]
        return cands[0].name, cands

    def reset(self) -> None:
        self._audio = np.zeros(0, dtype=np.float32)
        self._onset_carry = np.zeros(0, dtype=np.float32)
        self._prev_logm = None
        self._env = np.zeros(0, dtype=np.float32)
        self._env_t0 = 0.0
        self._scores = None
        self._beat = _BeatState()
        self._last_tempo_at = -1e9
        self._last_btc_at = -1e9
        self._btc_probs = None
        if self.chord_backend is not None and hasattr(self.chord_backend, "reset_stream"):
            self.chord_backend.reset_stream()
        self._cost = 0.0
        self._elapsed = 0.0
