"""A joint follower: words, melody, chords and beats against one reference.

One state — (cell on the reference timeline, tempo) — and every source of
evidence scored at the *audio time* it describes, not the time it arrives.

The reference is an interface, not a lead sheet, because the follower must
work the same way against two kinds of reference:

* `LeadSheetReference` — cells are eighth notes of the unrolled sheet; what
  each cell expects (syllable, note, chord, beat) comes from the notation.
* a recorded reference (`recorded.py`) — cells are tenths of a second of a
  past performance; what each cell expects is that performance's own pitch,
  chords and transcript, with the operator's slide changes as the slide map.

Both expose the same things: a cell count, the tempo range in cells per
second, the cells where a slide starts, which slide covers each cell, and an
emission function per source. The follower knows nothing else.

Latency. Each source reaches the follower late by a different amount
(Whisper words ~2.7 s, the vocal stem ~3 s, chords from the mix ~0.7 s,
the fast phoneme channel ~0.4 s). The follower runs on audio time; at each
read-out it re-filters the last `refilter_s` seconds from a checkpoint using
only the evidence that has arrived by then, so a late word corrects the past
instead of being dropped or misplaced, and early sources are used as soon as
they exist. The position shown is the filtered position at the read-out,
which by construction includes the projection through the tempo state.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


# ------------------------------------------------------------------ reference


class Reference:
    """What the follower needs to know about the thing it follows."""

    P: int  # cells
    rates: np.ndarray  # candidate tempi, cells per second
    jump_targets: np.ndarray  # cells a jump may land on
    end_cells: np.ndarray  # cells from which a jump may leave (ends of bars / phrases)

    def melody(self, pcs: np.ndarray) -> np.ndarray:  # T x 12 -> T x P
        raise NotImplementedError

    def chords(self, probs: np.ndarray) -> np.ndarray:  # T x 25 -> T x P
        raise NotImplementedError

    def word(self, text: str) -> np.ndarray | None:  # one heard word -> P
        raise NotImplementedError

    def beats(self, act: np.ndarray) -> np.ndarray:  # T beat activation -> T x P
        raise NotImplementedError


# ------------------------------------------------------------------ evidence


@dataclass
class Source:
    """One stream of evidence: dense frames, or sparse events (words)."""

    name: str
    latency: float  # seconds from the audio it describes to its arrival
    weight: float = 1.0
    dense: np.ndarray | None = None  # T x P log-likelihood ratios
    events: list[tuple[float, float, np.ndarray]] = field(default_factory=list)  # (audio t, arrival t, P-vector)
    tempo: np.ndarray | None = None  # T x R log-likelihoods over the tempo states (a beat tracker's pulse)
    drift: np.ndarray | None = None  # T: log change of the band's pulse at each frame (a beat tracker's tempo *drift*)


def robust(x: np.ndarray, rho: float) -> np.ndarray:
    """log(rho + (1-rho) e^x): a source that is confidently wrong can lower a
    state by at most log(rho) per frame."""
    if rho <= 0:
        return x
    return np.log(rho + (1 - rho) * np.exp(np.minimum(x, 30)))


# ------------------------------------------------------------------ filter


class SegmentTransition:
    """Advance within a segment; at its end, route to the start of the next
    segment by the reference's order prior (`ref.succ`, S x S, rows sum to
    1), mixed with a small uniform jump. Used when the reference is a
    sequence of slide segments rather than one continuous timeline."""

    def __init__(self, ref: Reference, fps: float, p_jump: float, p_rate: float):
        self.ref = ref
        v = ref.rates / fps
        self.lo = np.floor(v).astype(int)
        self.f = (v - self.lo).astype(np.float32)
        self.p_rate = p_rate
        self.seg = np.asarray(ref.seg_of)
        S = len(ref.jump_targets)
        succ = (1 - p_jump) * ref.succ + p_jump / S
        self.succ = (succ / succ.sum(axis=1, keepdims=True)).astype(np.float32)
        self.starts = ref.jump_targets
        self.ends = np.asarray(ref.end_cells)
        # segments where the band chooses what comes next (`clusters.py`)
        ce = getattr(ref, "cluster_end", None)
        self.cluster_end = np.ones(S, bool) if ce is None else np.asarray(ce, bool)

    def step(self, x: np.ndarray, hold: bool = False) -> np.ndarray:
        """`hold`: mass reaching the end of a segment that ends a cluster
        waits on its last cell instead of following the order prior — the
        follower does not guess past a fork. Inside a cluster it moves on."""
        p = self.p_rate
        y = (1 - 2 * p) * x
        y[1:] += p * x[:-1]
        y[:-1] += p * x[1:]
        y[0] += p * x[0]
        y[-1] += p * x[-1]
        x = y
        R, P = x.shape
        S = len(self.starts)
        y = np.zeros_like(x)
        over = np.zeros((R, S), np.float32)  # mass leaving each segment
        for k in range(0, int(self.lo.max()) + 2):
            w = np.where(self.lo == k, 1 - self.f, 0.0) + np.where(self.lo + 1 == k, self.f, 0.0)
            if not w.any():
                continue
            w = w.astype(np.float32)[:, None]
            if k == 0:
                y += w * x
                continue
            stay = np.zeros(P, bool)
            stay[: P - k] = self.seg[k:] == self.seg[: P - k]
            moved = w * x
            y[:, k:] += np.where(stay[: P - k][None, :], moved[:, : P - k], 0.0)
            leaving = np.where(~stay[None, :], moved, 0.0)
            over += np.stack([np.bincount(self.seg, weights=leaving[r], minlength=S) for r in range(R)])
        if hold:
            wait = over * self.cluster_end[None, :]
            y[:, self.ends] += wait
            over = over - wait
        y[:, self.starts] += over @ self.succ
        return y


class Transition:
    def __init__(self, ref: Reference, fps: float, p_jump: float, p_rate: float):
        self.ref = ref
        v = ref.rates / fps
        self.lo = np.floor(v).astype(int)
        self.f = (v - self.lo).astype(np.float32)
        self.p_jump = p_jump
        self.p_rate = p_rate
        self.u = np.zeros(ref.P, np.float32)
        self.u[ref.jump_targets] = 1.0 / max(1, len(ref.jump_targets))

    def step(self, x: np.ndarray) -> np.ndarray:
        p = self.p_rate
        y = (1 - 2 * p) * x
        y[1:] += p * x[:-1]
        y[:-1] += p * x[1:]
        y[0] += p * x[0]
        y[-1] += p * x[-1]
        x = y
        # advance: every tempo moves 0..K cells a frame; shift once per k
        y = np.zeros_like(x)
        lost = np.zeros(x.shape[0], np.float32)
        for k in range(0, int(self.lo.max()) + 2):
            w = np.where(self.lo == k, 1 - self.f, 0.0) + np.where(self.lo + 1 == k, self.f, 0.0)
            if not w.any():
                continue
            w = w.astype(np.float32)[:, None]
            if k == 0:
                y += w * x
            else:
                y[:, k:] += w * x[:, :-k]
                lost += (w * x[:, -k:]).sum(axis=1)
        jump = self.p_jump * y[:, self.ref.end_cells]
        y[:, self.ref.end_cells] -= jump
        y += (jump.sum(axis=1) + lost)[:, None] * self.u[None, :]
        return y


def follow(ref: Reference, sources: list[Source], T: int, fps: float = 10.0, readout_every: float = 0.5,
           refilter_s: float | None = None, p_jump: float = 0.03, p_rate: float = 0.02,
           lookahead_s: float = 0.0, heard_s: float = 3.0, gate=None):
    """Causal read-outs: at each wall time t (every `readout_every` s), the
    filtered position given all evidence that had *arrived* by t.

    `lookahead_s`: read out where the belief will be that much later instead
    of now — every (cell, tempo) state carried forward at its own tempo,
    through slide ends by the order prior, with no new evidence. The position
    a slide change is projected from is the follower's own: cells to the
    segment's end over the tempo, state by state.

    `heard_s`: also keep the belief about where the band was that long ago,
    filtered with the evidence that has arrived for it by now (words arrive
    ~2.7 s after they are sung, scored spans ~3.2 s) — what the follower has
    actually *heard*, as opposed to projected (`follow.marg_heard`).

    `gate(wall, heard marginal, tempo belief) -> bool`: called once per
    read-out with what has been heard; when it returns True (the band is off
    the arrangement), every frame after the heard point, and the lookahead,
    steps with `hold` — no crossing a cluster's end on a guess.

    Returns (wall times, position cell per read-out, tempo per read-out,
    filtered marginal at each read-out)."""
    tr = SegmentTransition(ref, fps, p_jump, p_rate) if getattr(ref, "succ", None) is not None else Transition(ref, fps, p_jump, p_rate)
    R = len(ref.rates)
    # every event must arrive inside the re-filter window, or it is dropped
    for s in sources:
        for t_a, t_arr, _v in s.events:
            if t_arr - t_a > s.latency + 1e-6:
                raise ValueError(f"{s.name}: an event arrives {t_arr - t_a:.1f} s after its audio, beyond its {s.latency:.1f} s window")
    max_lat = max([s.latency for s in sources] + [0.0])
    refilter_s = max_lat + 0.5 if refilter_s is None else refilter_s
    k_back = int(np.ceil(refilter_s * fps))

    def evidence(frame: int, wall: float) -> np.ndarray:
        e = np.zeros(ref.P, np.float32)
        for s in sources:
            if s.dense is not None:
                # a dense frame describing audio time frame/fps arrives `latency` later
                if frame / fps + s.latency <= wall + 1e-9 and frame < len(s.dense):
                    e += s.weight * s.dense[frame]
        return e

    # sparse events, bucketed by audio frame
    bucket: dict[int, list[tuple[float, np.ndarray, float]]] = {}
    for s in sources:
        for t_audio, t_arr, vec in s.events:
            bucket.setdefault(int(t_audio * fps), []).append((t_arr, vec, s.weight))

    def ev_at(frame: int, wall: float) -> np.ndarray:
        e = evidence(frame, wall)
        for t_arr, vec, w in bucket.get(frame, ()):
            if t_arr <= wall + 1e-9:
                e = e + w * vec
        return e

    tempo_srcs = [s for s in sources if s.tempo is not None]
    drift_srcs = [s for s in sources if s.drift is not None]
    rates = np.asarray(ref.rates, float)
    log_step = float(np.mean(np.diff(np.log(rates)))) if R > 1 else 1.0

    def drift(a: np.ndarray, frame: int, wall: float) -> np.ndarray:
        """Move the tempo belief by the band's measured pulse change: a 5 %
        faster pulse makes every tempo state 5 % faster. Only the *change* is
        taken from the beats; the tempo's level is still learned from where the
        words and music put the band, so a band that sings more beats than the
        sheet writes cannot be forced through the sheet too fast."""
        d = 0.0
        for s in drift_srcs:
            if frame / fps + s.latency <= wall + 1e-9 and frame < len(s.drift):
                d += s.drift[frame]
        if d == 0.0:
            return a
        k = d / log_step  # grid steps, fractional
        n, f = int(np.floor(k)), k - np.floor(k)
        out = np.zeros_like(a)
        for shift, w in ((n, 1 - f), (n + 1, f)):
            if w <= 0:
                continue
            # state r's mass moves to r + shift, piling up at the grid's ends
            np.add.at(out, np.clip(np.arange(R) + shift, 0, R - 1), w * a)
        return out

    def tempo_at(frame: int, wall: float) -> np.ndarray:
        e = np.zeros(R, np.float32)
        for s in tempo_srcs:
            if frame / fps + s.latency <= wall + 1e-9 and frame < len(s.tempo):
                e += s.weight * s.tempo[frame]
        return np.exp(e - e.max())[:, None]

    def update(a: np.ndarray, frame: int, wall: float) -> np.ndarray:
        e = ev_at(frame, wall)
        a = a * np.exp(e - e.max())[None, :]
        if tempo_srcs:
            a = a * tempo_at(frame, wall)
        return a / a.sum()

    # checkpoint: filtered state at frame c, built with everything that will
    # ever arrive for frames <= c (true once wall >= c/fps + max_lat)
    alpha = np.full((R, ref.P), 1.0 / (R * ref.P), np.float32)
    ck_frame = -1
    walls = np.arange(readout_every, T / fps, readout_every)
    out_pos = np.zeros(len(walls), int)
    out_rate = np.zeros(len(walls))
    out_marg = np.zeros((len(walls), ref.P), np.float32)
    rate_marg = np.zeros((len(walls), R), np.float32)
    ttg_cells = None
    if getattr(ref, "seg_of", None) is not None:
        seg = np.asarray(ref.seg_of)
        ttg_cells = (np.asarray(ref.end_cells)[seg] - np.arange(ref.P) + 1).astype(np.float32)
    ttg = np.full(len(walls), np.nan)
    marg_now = np.zeros((len(walls), ref.P), np.float32)
    marg_heard = np.zeros((len(walls), ref.P), np.float32)
    held = np.zeros(len(walls), bool)
    pos_now = np.zeros(len(walls), int)
    for wi, wall in enumerate(walls):
        now = min(T - 1, int(wall * fps))
        settle = now - k_back  # frames up to here have all their evidence by now
        while ck_frame < settle:
            ck_frame += 1
            if ck_frame > 0:
                alpha = drift(tr.step(alpha), ck_frame, wall)
            alpha = update(alpha, ck_frame, wall)
        a = alpha.copy()
        h_frame = now - int(round(heard_s * fps))
        hold = False

        def heard(m):
            nonlocal hold
            marg_heard[wi] = m
            if gate is not None:
                hold = bool(gate(wall, m, a.sum(axis=1)))
            held[wi] = hold

        if h_frame <= ck_frame:
            heard(alpha.sum(axis=0))
        for f in range(ck_frame + 1, now + 1):
            a = tr.step(a, hold=True) if hold else tr.step(a)
            a = drift(a, f, wall)
            a = update(a, f, wall)
            if f == h_frame:
                heard(a.sum(axis=0))
        rate_marg[wi] = a.sum(axis=1)
        marg_now[wi] = a.sum(axis=0)
        if ttg_cells is not None:
            # time to the segment's end under each state, and the belief's median of it
            t_go = ttg_cells[None, :] / np.asarray(ref.rates)[:, None]
            o = np.argsort(t_go, axis=None)
            cw = np.cumsum(a.ravel()[o])
            ttg[wi] = t_go.ravel()[o[np.searchsorted(cw, 0.5 * cw[-1])]]
            pos_now[wi] = int(np.argmax(a.sum(axis=0)))
        for _ in range(int(round(lookahead_s * fps))):
            a = tr.step(a, hold=True) if hold else tr.step(a)
        m = a.sum(axis=0)
        out_marg[wi] = m
        out_pos[wi] = int(np.argmax(m))
        out_rate[wi] = float(ref.rates[int(np.argmax(rate_marg[wi]))])
    follow.rate_marg = rate_marg  # tempo belief per read-out (diagnostics)
    follow.held = held  # read-outs where the gate held cluster ends
    follow.marg_heard = marg_heard  # the belief about `heard_s` ago, with its evidence in
    follow.marg_now = marg_now  # the belief at "now", before any lookahead
    follow.ttg = ttg  # belief's median seconds to the current segment's end, at "now"
    follow.pos_now = pos_now  # best cell at "now" (before any lookahead)
    return walls, out_pos, out_rate, out_marg
