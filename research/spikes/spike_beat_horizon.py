"""How far ahead can a beat grid be predicted from past audio alone?

Spike, 2026-09-08. Companion to `spike_chord_downbeat.py` (audio front end,
numpy only) and `spike_metre_corpus.py` (the musical premise, on annotated
corpora). Nothing here touches the sidecar.

This is the property the app would actually be buying. Every side channel tried
so far is reactive — evidence arrives after the event. A metrical grid is
different in kind: fit tempo and phase on audio up to time t, and the beats
after t are times in the *future*, which is what the scheduler needs.

Strictly causal: the grid scored at horizon h is fitted only on [t-W, t] and
evaluated on onset strength in (t, t+h]. The null is the same grid at a random
phase, so what is measured is *alignment*, not that beats land on loud audio.

Requires librosa (ISC), which the sidecar does not ship. Isolated venv:

    uv venv /tmp/spikevenv --python 3.11
    VIRTUAL_ENV=/tmp/spikevenv uv pip install librosa
    /tmp/spikevenv/bin/python research/spikes/spike_beat_horizon.py

The click-track control is not optional. Three other tests in this spike
produced confident numbers on worship audio and then reported the same result
on a rigid click track, which means they were measuring the method rather than
the music. Read the CONTROL row first: if it does not show strong prediction at
every horizon, the worship rows below it mean nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import librosa  # noqa: E402

from spike_chord_downbeat import (SR, _click_track, _drum_loop,  # noqa: E402
                                  approved_ids, load_rec)

HOP = 256
FPS = SR / HOP
W = 20.0
HORIZONS = [(0, 2), (2, 4), (4, 8), (8, 16), (16, 32)]


def horizon_profile(y: np.ndarray, label: str, rng: np.random.Generator) -> dict:
    env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP)
    dur = len(env) / FPS

    def strength(times: np.ndarray) -> float:
        idx = np.clip((times * FPS).astype(int), 0, len(env) - 1)
        return float(env[idx].mean()) if len(idx) else float("nan")

    ratios: dict = {h: [] for h in HORIZONS}
    bpms = []
    for t in np.arange(W + 5, dur - 35, 10.0):
        seg = y[int((t - W) * SR):int(t * SR)]
        _, frames = librosa.beat.beat_track(y=seg, sr=SR, hop_length=HOP, units="frames")
        bt = librosa.frames_to_time(frames, sr=SR, hop_length=HOP) + (t - W)
        if len(bt) < 8:
            continue
        period = float(np.median(np.diff(bt)))
        if not (0.3 < period < 1.4):
            continue
        bpms.append(60.0 / period)
        last = bt[-1]
        for (a, b) in HORIZONS:
            k0 = int(np.ceil((t + a - last) / period))
            k1 = int((t + b - last) / period)
            if k1 <= k0:
                continue
            pred = last + np.arange(k0, k1 + 1) * period
            pred = pred[(pred > 0) & (pred < dur)]
            if len(pred) < 2:
                continue
            obs = strength(pred)
            nul = float(np.mean([strength(pred + rng.uniform(0, period))
                                 for _ in range(12)]))
            if nul > 0:
                ratios[(a, b)].append(obs / nul)
    med = np.median(bpms) if bpms else float("nan")
    row = " ".join(f"{np.median(ratios[h]):8.3f}" if ratios[h] else "     n/a"
                   for h in HORIZONS)
    print(f"{label:30s} {med:6.1f} {row}")
    return ratios


def main() -> int:
    rng = np.random.default_rng(0)
    print("Causal beat-grid prediction. Ratio = onset strength at predicted beats")
    print("divided by strength at a randomly phased grid of the same tempo.")
    print("1.0 is chance; the grid carries no usable phase at that horizon.\n")
    print(f"{'':30s} {'BPM':>6s} " + " ".join(f"{a}-{b}s".rjust(8) for a, b in HORIZONS))
    print("-" * 88)

    loop, _ = _drum_loop(112.0, 300.0)
    horizon_profile(loop, "CONTROL: rigid drum loop", rng)
    loop2, _ = _drum_loop(112.0, 300.0, jitter=0.030, seed=7)
    horizon_profile(loop2, "CONTROL: 30 ms human jitter", rng)
    click, _ = _click_track(112.0, 300.0)
    horizon_profile(click, "(sparse click, unfair control)", rng)
    print("-" * 88)

    pooled: dict = {h: [] for h in HORIZONS}
    for rid in approved_ids():
        rec = load_rec(rid)
        r = horizon_profile(rec.audio.astype(np.float32), rid, rng)
        for h in HORIZONS:
            pooled[h] += r[h]
    print("-" * 88)
    print(f"{'POOLED median':30s} {'':6s} " +
          " ".join(f"{np.median(pooled[h]):8.3f}" for h in HORIZONS))
    print(f"{'share of windows above 1.0':30s} {'':6s} " +
          " ".join(f"{np.mean(np.array(pooled[h]) > 1.0):8.1%}" for h in HORIZONS))
    print(f"{'n windows':30s} {'':6s} " +
          " ".join(f"{len(pooled[h]):8d}" for h in HORIZONS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
