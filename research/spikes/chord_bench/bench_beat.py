"""Benchmark Beat This! on our own audio: speed, and the 16 kHz question.

Two things no paper reports and one risk specific to this project:

  1. Real inference speed on this machine, CPU vs Apple-silicon MPS. The
     literature quotes parameter counts, never realtime factors.
  2. Whether our 16 kHz capture costs beat accuracy. The model wants 22.05 kHz
     and builds mel bands to 10 kHz; our recordings hold nothing above 8 kHz.
     The originals are on disk at 44.1 kHz, same performances, and the
     durations match to under 10 ms, so this is a direct comparison rather
     than an argument.

The hi-res run is the reference and the 16 kHz run is the estimate, scored with
mir_eval's standard 70 ms F-measure. That is not "accuracy" in the absolute
sense — it measures how much the capture rate changes the answer.
"""
import os
import sys
import time

import mir_eval
import numpy as np
import soundfile as sf
import torch
from beat_this.inference import Audio2Beats

SC = os.path.dirname(os.path.abspath(__file__))
REC = os.path.expanduser("~/Library/Application Support/com.churchslides.app/recordings")
CKPT = sys.argv[1] if len(sys.argv) > 1 else "final0"


def load(path):
    x, sr = sf.read(path, dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x, sr


def fmeasure(ref, est, tol=0.07):
    if len(ref) < 2 or len(est) < 2:
        return float("nan")
    return mir_eval.beat.f_measure(np.asarray(ref), np.asarray(est), f_measure_threshold=tol)


devices = ["cpu"] + (["mps"] if torch.backends.mps.is_available() else [])
print(f"checkpoint={CKPT}  torch={torch.__version__}  devices={devices}\n")

speed = {d: [] for d in devices}
rows = []
for fn in sorted(os.listdir(f"{SC}/hires")):
    rid = fn[:-4]
    hi, sr_hi = load(f"{SC}/hires/{fn}")
    lo, sr_lo = load(f"{REC}/{rid}/audio.wav")
    dur = len(hi) / sr_hi

    out = {}
    for dev in devices:
        a2b = Audio2Beats(checkpoint_path=CKPT, device=dev)
        t0 = time.time()
        b_hi, db_hi = a2b(hi, sr_hi)
        el = time.time() - t0
        speed[dev].append(dur / el)
        if dev == devices[-1]:
            t0 = time.time()
            b_lo, db_lo = a2b(lo, sr_lo)
            out = dict(b_hi=b_hi, db_hi=db_hi, b_lo=b_lo, db_lo=db_lo)
    rows.append((rid, dur, out))
    print(f"{rid:26s} {dur:6.1f}s  hi: {len(out['b_hi']):4d} beats "
          f"{len(out['db_hi']):3d} downbeats | 16k: {len(out['b_lo']):4d} / "
          f"{len(out['db_lo']):3d}  | beat F {fmeasure(out['b_hi'], out['b_lo']):.3f} "
          f" downbeat F {fmeasure(out['db_hi'], out['db_lo']):.3f}")

print()
for dev in devices:
    print(f"speed {dev:4s}: {np.mean(speed[dev]):7.1f}x realtime  "
          f"(RTF {1 / np.mean(speed[dev]):.4f})")

bf = [fmeasure(r[2]["b_hi"], r[2]["b_lo"]) for r in rows]
df = [fmeasure(r[2]["db_hi"], r[2]["db_lo"]) for r in rows]
print(f"\n16 kHz vs 44.1 kHz agreement, mean beat F {np.nanmean(bf):.3f}, "
      f"downbeat F {np.nanmean(df):.3f}")
tempo_hi = [60 / np.median(np.diff(r[2]["b_hi"])) for r in rows]
tempo_lo = [60 / np.median(np.diff(r[2]["b_lo"])) for r in rows]
print("tempo (BPM)  hi-res:", " ".join(f"{t:5.1f}" for t in tempo_hi))
print("             16 kHz:", " ".join(f"{t:5.1f}" for t in tempo_lo))
