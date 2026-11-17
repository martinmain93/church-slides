"""Validation, with nulls that can actually differ from the observation.

The previous version shifted the whole beat grid by a constant to build its
null. Rayleigh R is invariant to a constant phase shift -- |mean(e^{i(phi+c)})|
= |mean(e^{i phi})| -- so that null was mathematically identical to the
observation and every row read "at chance" by construction.

Correct nulls here:
  word onsets  -> keep the grid, replace the onsets with uniform random times
                  over the same span (destroys alignment, keeps n)
  slide cues   -> keep the cues, replace the downbeat grid with a random
                  isochronous grid of the same bar length (a constant shift is
                  fine for this statistic, but a random period is stricter)

Tolerance is swept for the cue test because the operator's presses scatter
-0.49..+0.87 s against the sung word, so a 200 ms window cannot see a real
effect even if it is there.
"""
import json
import os
import sys

import numpy as np
import soundfile as sf
from beat_this.inference import Audio2Beats

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from spike_chord_downbeat import beat_phase, rayleigh_harmonic  # noqa: E402

SC = os.path.dirname(os.path.abspath(__file__))
REC = os.path.expanduser("~/Library/Application Support/com.churchslides.app/recordings")
rng = np.random.default_rng(0)
a2b = Audio2Beats(checkpoint_path="final0", device="mps")
TOLS = [0.10, 0.20, 0.35, 0.50]

print(f"{'recording':26s} {'BPM':>6s} | {'word onsets: R (null95)':>34s}")
print("-" * 68)
rows = []
for fn in sorted(os.listdir(f"{SC}/hires")):
    rid = fn[:-4]
    x, sr = sf.read(f"{SC}/hires/{fn}", dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    beats, downs = a2b(x, sr)
    beats, downs = np.asarray(beats), np.asarray(downs)
    tj = json.load(open(f"{REC}/{rid}/transcript.json"))
    onsets = np.array([w["start"] for w in tj.get("words", [])], dtype=float)
    onsets = onsets[(onsets > beats[0]) & (onsets < beats[-1])]

    cell = []
    for k in (1, 2, 4):
        obs = rayleigh_harmonic(beat_phase(onsets, beats), k)[0]
        nul = [rayleigh_harmonic(
            beat_phase(np.sort(rng.uniform(beats[0], beats[-1], len(onsets))), beats), k)[0]
            for _ in range(60)]
        cell.append(f"{obs:.3f}({np.percentile(nul, 95):.3f})")
    print(f"{rid:26s} {60 / np.median(np.diff(beats)):6.1f} | k1 {cell[0]}  "
          f"k2 {cell[1]}  k4 {cell[2]}")
    rows.append((rid, beats, downs))

print()
gj_all = []
print(f"{'recording':26s} " + " ".join(f"+-{int(t * 1000)}ms".rjust(14) for t in TOLS))
print(f"{'':26s} " + " ".join(f"{'obs':>6s}{'null':>8s}" for _ in TOLS))
print("-" * 84)
tot = {t: ([], []) for t in TOLS}
for rid, beats, downs in rows:
    gj = json.load(open(f"{REC}/{rid}/golden.json"))
    cues = np.array([c["t_seconds"] for c in gj.get("ground_truth", [])], dtype=float)
    cues = cues[(cues > downs[0]) & (cues < downs[-1])]
    bar = float(np.median(np.diff(downs)))

    def near(ref, xs, tol):
        i = np.clip(np.searchsorted(ref, xs), 1, len(ref) - 1)
        d = np.minimum(np.abs(ref[i] - xs), np.abs(ref[i - 1] - xs))
        return float((d < tol).mean())

    cells = []
    for t in TOLS:
        obs = near(downs, cues, t)
        nul = float(np.mean([
            near(np.arange(downs[0] + rng.uniform(0, bar), downs[-1],
                           bar * rng.uniform(0.9, 1.1)), cues, t) for _ in range(40)]))
        tot[t][0].append(obs); tot[t][1].append(nul)
        cells.append(f"{obs:6.0%}{nul:8.0%}")
    print(f"{rid:26s} " + " ".join(cells))
print("-" * 84)
print(f"{'POOLED':26s} " +
      " ".join(f"{np.mean(tot[t][0]):6.0%}{np.mean(tot[t][1]):8.0%}" for t in TOLS))
