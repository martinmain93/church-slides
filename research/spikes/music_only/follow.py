"""Score following: where in the lead sheet is the band, from music alone.

A hidden Markov model over (position in the unrolled sheet, tempo). Each
frame (10 per second) the position advances at the tempo; the tempo drifts
slowly; and at a bar line the band may leave the sheet's printed order and
jump to the start of any slide or section, because bands do not play the
roadmap — the WABN golden repeats chorus 3, tags last lines and cuts the
instrumental short.

Evidence per frame, all from audio:
* melody: Basic Pitch salience at the sheet's note, as a log ratio to the
  mean over pitch classes (octave-folded; zero where the sheet rests);
* chords: BTC's probability of the sheet's chord, as a log ratio to uniform.

Nothing reads a transcript. `smooth` is the offline answer (forward-
backward); `forward` alone is what a live system could know.

The state grid is an eighth note. Tempo bins span 45-90 bpm around nothing
in particular — the printed tempo is not used, since this band plays 13%
under it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

import reference as ref
import score as sc

CELL = 0.5  # quarter notes per state cell (an eighth note)


@dataclass
class Model:
    s: sc.Score
    cell_pos: np.ndarray  # played position (quarter notes) at each cell's centre
    midi: np.ndarray  # sheet note per cell, -1 for rest
    chord: np.ndarray  # sheet chord class per cell, -1 for none
    end_cells: np.ndarray  # last cell of every played bar
    targets: np.ndarray  # cells a jump may land on
    bpm: np.ndarray  # tempo bins
    fps: float
    p_jump: float  # per frame, from a bar's last cell
    p_rate: float  # per frame, to each neighbouring tempo bin

    @property
    def P(self) -> int:
        return len(self.cell_pos)

    @property
    def R(self) -> int:
        return len(self.bpm)


def build(s: sc.Score, slides: list[list[str]], fps: float = 10.0, p_jump: float = 0.05, p_rate: float = 0.02,
          bpm_lo: float = 40, bpm_hi: float = 130, n_rates: int = 16, shuffle_seed: int | None = None,
          transpose: int = 0, key_shift: int = 0) -> Model:
    R = ref.Reference(s)
    P = int(np.floor(s.length / CELL))
    cell_pos = (np.arange(P) + 0.5) * CELL
    midi, chord = R.at(cell_pos)
    bar_start = np.array([pb.start for pb in s.played])
    bar_cells = np.round(bar_start / CELL).astype(int)
    end_cells = np.clip(np.r_[bar_cells[1:], P] - 1, 0, P - 1)
    # jump targets: the bar holding each slide's first word, and each section start
    spans = sc.bind_slides(s, slides)
    tgt_bars = set()
    for sps in spans.values():
        for sp in sps:
            i, _, _ = sc.position_of(s, sp.start)
            tgt_bars.add(i)
    for i, pb in enumerate(s.played):
        if s.bar(pb.bar).get("section") or i == 0:
            tgt_bars.add(i)
    targets = np.array(sorted(bar_cells[i] for i in tgt_bars))
    if shuffle_seed is not None:
        # control: same notes and chords, bars in a random order
        rng = np.random.default_rng(shuffle_seed)
        order = rng.permutation(len(bar_cells))
        m2, c2 = np.full(P, -1), np.full(P, -1)
        out = 0
        for i in order:
            a = bar_cells[i]
            b = bar_cells[i + 1] if i + 1 < len(bar_cells) else P
            n = b - a
            m2[out : out + n] = midi[a:b]
            c2[out : out + n] = chord[a:b]
            out += n
        midi, chord = m2[:P], c2[:P]
    if transpose:
        midi = np.where(midi >= 0, midi + transpose, -1)
    if key_shift:
        # the whole song in another key: melody and chord roots move together
        midi = np.where(midi >= 0, midi + key_shift, -1)
        chord = np.where((chord >= 0) & (chord < 24), ((chord // 2 + key_shift) % 12) * 2 + chord % 2, chord)
    bpm = np.geomspace(bpm_lo, bpm_hi, n_rates)
    return Model(s, cell_pos, midi, chord, end_cells, targets, bpm, fps, p_jump, p_rate)


def chord_histogram(chords: np.ndarray) -> np.ndarray:
    """12 x 2 (root x major/minor) time share from chord probabilities."""
    h = chords[:, :24].sum(axis=0)
    return (h / max(h.sum(), 1e-9)).reshape(12, 2)


def sheet_histogram(m: "Model") -> np.ndarray:
    h = np.zeros(24)
    for c in m.chord:
        if 0 <= c < 24:
            h[c] += 1
    return (h / max(h.sum(), 1)).reshape(12, 2)


def estimate_key_shift(chords: np.ndarray, m: "Model") -> tuple[int, np.ndarray]:
    """Semitones to move the sheet into the band's key, from the audio alone:
    the shift whose chord-quality histogram best correlates with what BTC
    hears. Returns the shift and the correlation for every shift."""
    a = chord_histogram(chords)
    b = sheet_histogram(m)
    corr = np.array([np.corrcoef(a.ravel(), np.roll(b, k, axis=0).ravel())[0, 1] for k in range(12)])
    return int(np.argmax(corr)), corr


def frame_evidence(feats: dict, src: str, fps: float, duration: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(times, pitch-class salience T x 12, chord probabilities T x 25) at fps.
    Pitch is averaged over each frame's 1/fps window; chords are nearest."""
    t = np.arange(0, duration, 1.0 / fps)
    pt = feats[f"{src}_pitch_t"]
    pcs = ref.pc_salience(feats[f"{src}_pitch"])
    idx = np.clip(np.searchsorted(pt, t + 0.5 / fps), 0, len(pt))
    csum = np.vstack([np.zeros((1, 12), np.float32), np.cumsum(pcs, axis=0)])
    lo = np.clip(np.searchsorted(pt, t - 0.5 / fps), 0, len(pt))
    cnt = np.maximum(idx - lo, 1)[:, None]
    pcs_f = (csum[idx] - csum[lo]) / cnt
    ct = feats[f"{src}_chord_t"]
    ci = np.clip(np.searchsorted(ct, t), 0, len(ct) - 1)
    chords = feats[f"{src}_chord"].astype(np.float32)[ci]
    return t, pcs_f.astype(np.float32), chords


def log_obs(m: Model, pcs: np.ndarray, chords: np.ndarray, w_mel: float = 1.0, w_chord: float = 1.0,
            robust: float = 0.0) -> np.ndarray:
    """T x P log evidence.

    Each source is a log likelihood ratio against "this source says nothing".
    `robust` > 0 mixes each source with that uninformative case,
    log(robust + (1 - robust) * ratio), so a source that is confidently wrong
    (a harmony line on the vocal stem, a misheard chord) can lower a state by
    at most log(robust) per frame instead of vetoing it. The weights temper
    frames that are far from independent: ten a second, from features that
    change about once a second."""
    eps = 0.02
    mel_tab = np.log(eps + pcs) - np.log(eps + pcs.mean(axis=1, keepdims=True))  # T x 12
    ch_tab = np.log(1e-3 + chords) - np.log(1e-3 + 1 / 25)  # T x 25
    if robust > 0:
        mel_tab = np.log(robust + (1 - robust) * np.exp(mel_tab))
        ch_tab = np.log(robust + (1 - robust) * np.exp(ch_tab))
    has_m = m.midi >= 0
    mel = np.where(has_m[None, :], mel_tab[:, np.where(has_m, m.midi % 12, 0)], 0.0)
    has_c = m.chord >= 0
    ch = np.where(has_c[None, :], ch_tab[:, np.where(has_c, m.chord, 0)], 0.0)
    return (w_mel * mel + w_chord * ch).astype(np.float32)


class Transition:
    """The transition operator and its transpose, without a P x P matrix."""

    def __init__(self, m: Model):
        self.m = m
        v = m.bpm / 60.0 / m.fps / CELL  # cells per frame
        self.lo = np.floor(v).astype(int)
        self.f = (v - self.lo).astype(np.float32)
        self.u = np.zeros(m.P, np.float32)
        self.u[m.targets] = 1.0 / len(m.targets)

    def _rate_mix(self, x: np.ndarray) -> np.ndarray:
        p = self.m.p_rate
        y = (1 - 2 * p) * x
        y[1:] += p * x[:-1]
        y[:-1] += p * x[1:]
        y[0] += p * x[0]  # reflect at the ends so mass is kept
        y[-1] += p * x[-1]
        return y

    def forward(self, x: np.ndarray) -> np.ndarray:
        m = self.m
        x = self._rate_mix(x)
        y = np.zeros_like(x)
        lost = np.zeros(m.R, np.float32)
        for r in range(m.R):
            for k, w in ((self.lo[r], 1 - self.f[r]), (self.lo[r] + 1, self.f[r])):
                if k == 0:
                    y[r] += w * x[r]
                else:
                    y[r, k:] += w * x[r, :-k]
                    lost[r] += w * x[r, -k:].sum()
        jump = m.p_jump * y[:, m.end_cells]
        y[:, m.end_cells] -= jump
        pool = jump.sum(axis=1) + lost
        y += pool[:, None] * self.u[None, :]
        return y

    def backward(self, b: np.ndarray) -> np.ndarray:
        m = self.m
        ub = b @ self.u  # per rate: expected value at a jump target
        # jumps (transpose)
        bj = b.copy()
        bj[:, m.end_cells] = (1 - m.p_jump) * b[:, m.end_cells] + m.p_jump * ub[:, None]
        # advance (transpose): position c moves to c + k
        x = np.zeros_like(b)
        for r in range(m.R):
            for k, w in ((self.lo[r], 1 - self.f[r]), (self.lo[r] + 1, self.f[r])):
                if k == 0:
                    x[r] += w * bj[r]
                else:
                    x[r, :-k] += w * bj[r, k:]
                    x[r, -k:] += w * ub[r]
        return self._rate_mix(x)


def forward(m: Model, logobs: np.ndarray, keep: bool = False) -> tuple[np.ndarray, np.ndarray | None]:
    """Filtered position marginal per frame (T x P), and optionally the full
    filtered state for smoothing."""
    T = len(logobs)
    tr = Transition(m)
    a = np.full((m.R, m.P), 1.0 / (m.R * m.P), np.float32)
    marg = np.zeros((T, m.P), np.float32)
    full = np.zeros((T, m.R, m.P), np.float16) if keep else None
    m.loglik = np.zeros(T)  # log evidence per frame against "the music says nothing"
    m.rate_marg = np.zeros((T, m.R), np.float32)  # the filter's own tempo belief
    for t in range(T):
        a = tr.forward(a) if t else a
        mx = logobs[t].max()
        e = np.exp(logobs[t] - mx)
        a = a * e[None, :]
        z = a.sum()
        m.loglik[t] = mx + np.log(max(z, 1e-38))
        a /= z
        marg[t] = a.sum(axis=0)
        m.rate_marg[t] = a.sum(axis=1)
        if keep:
            full[t] = a
    return marg, full


def live_positions(m: Model, marg: np.ndarray, lag_s: float) -> np.ndarray:
    """What a live system would show at each wall-clock frame.

    The filter runs on audio time; the evidence for audio time tau is only
    complete `lag_s` later (the slowest feature in use). So at wall time t
    the newest usable belief is the filtered one at t - lag, projected
    forward by the lag at the filter's own tempo estimate."""
    T = len(marg)
    k = int(round(lag_s * m.fps))
    pos = marg.argmax(axis=1)
    bpm = m.bpm[m.rate_marg.argmax(axis=1)]
    out = np.zeros(T, int)
    for t in range(T):
        src = t - k
        if src < 0:
            out[t] = 0
            continue
        adv = lag_s * bpm[src] / 60.0 / CELL
        out[t] = min(m.P - 1, int(round(pos[src] + adv)))
    return out


def smooth(m: Model, logobs: np.ndarray) -> np.ndarray:
    """Forward-backward posterior position marginal per frame (T x P)."""
    T = len(logobs)
    _, full = forward(m, logobs, keep=True)
    tr = Transition(m)
    b = np.ones((m.R, m.P), np.float32)
    post = np.zeros((T, m.P), np.float32)
    for t in range(T - 1, -1, -1):
        g = full[t].astype(np.float32) * b
        post[t] = g.sum(axis=0) / max(g.sum(), 1e-30)
        if t:
            e = np.exp(logobs[t] - logobs[t].max())
            b = tr.backward(b * e[None, :])
            b /= b.max()
    return post
