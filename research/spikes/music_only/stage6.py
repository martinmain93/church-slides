"""Stage 6: which song is it? Every lead sheet against every recording.

    uv run --project sidecar python research/spikes/music_only/stage6.py [--arm "stems both"] [--window 20]

Each reference (lead sheet), in each of the 12 keys, is run as a causal
forward filter over the whole recording. The filter's per-frame log
evidence is a Bayes factor against "the music says nothing", so summing it
over a trailing window says how well that song, somewhere in its form,
explains the last W seconds. A recording is identified at time t when its
own song's best key beats every other song's best key.

Recordings with no lead sheet are the negatives: no reference should claim
them, and how strongly the best one does sets the bar a real claim must
clear.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

import cv
import features as ft
import follow as fl
import gt
import score as sc
import stage1
import stage4


def evidence(F: dict, s: sc.Score, slides, arm: str, fps: float, shift: int) -> np.ndarray:
    psrc, csrc, wm, wc = cv.ARMS[arm]
    dur = float(F["duration"])
    t, pcs, _ = fl.frame_evidence(F, psrc, fps, dur)
    _, _, ch = fl.frame_evidence(F, csrc, fps, dur)
    pcs = stage4.delayed(t, pcs, stage4.LATENCY[psrc]["pitch"])
    ch = stage4.delayed(t, ch, stage4.LATENCY[csrc]["chord"])
    m = fl.build(s, slides, fps=fps, key_shift=shift, p_jump=P_JUMP)
    lo = fl.log_obs(m, pcs, ch, wm * W_EV, wc * W_EV, ROBUST)
    fl.forward(m, lo)
    return m.loglik


# the setting leave-one-song-out chose for the melody arm on every song
W_EV = 0.4
ROBUST = 0.2
P_JUMP = 0.03


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", default="stems melody")
    ap.add_argument("--fps", type=float, default=10.0)
    ap.add_argument("--recordings", default="", help="extra rec dirs (negatives), comma separated")
    ap.add_argument("--cache", type=Path, default=cv.S / "stage6_cache.json")
    args = ap.parse_args()
    refs = {k: sc.load(cv.S / f"{v}.score.json") for k, v in cv.SONGS.items()}
    ref_slides = {k: stage1.load_slides(cv.S / k, refs[k]) for k in refs}
    recs = {k: cv.S / k for k in cv.SONGS}
    for extra in [x for x in args.recordings.split(",") if x]:
        recs[Path(extra).name] = Path(extra)
    cache = json.loads(args.cache.read_text()) if args.cache.exists() else {}
    for rk, rdir in recs.items():
        F = ft.load(cv.S / f"{rk}_feats.npz")
        for ref_k, s in refs.items():
            for shift in range(12):
                key = f"{args.arm}|{rk}|{ref_k}|{shift}"
                if key in cache:
                    continue
                t0 = time.time()
                cache[key] = evidence(F, s, ref_slides[ref_k], args.arm, args.fps, shift).round(3).tolist()
                print(f"{rk:8s} vs {ref_k:5s} key+{shift:2d}  {np.sum(cache[key]):9.1f}  [{time.time() - t0:.1f}s]", flush=True)
        args.cache.write_text(json.dumps(cache))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
