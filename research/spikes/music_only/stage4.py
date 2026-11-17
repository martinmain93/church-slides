"""Stages 3-5: follow the recording through the lead sheet from music alone.

    uv run --project sidecar python research/spikes/music_only/stage4.py \
        score.json REC_DIR feats.npz [--causal]

Offline (forward-backward) by default; `--causal` runs the forward filter
only, with each feature delayed by the latency it would have live, and
projects the estimate forward by that latency.

Arms: melody + chords, melody only, chords only; mix and stems. Controls:
bars shuffled (same notes and chords, wrong order), melody a tritone off,
and a uniformly random position.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np

import evaluate as ev
import features as ft
import follow as fl
import gt
import score as sc
import stage1

# What a live system would wait for, per source (seconds). Stems: htdemucs
# releases audio ~2 s after it is heard (realtime_stems.py). Basic Pitch
# needs its 2 s window to finish (~1 s past the frame on average). BTC's
# offline frames are stamped at the centre of a CQT window that ends 0.74 s
# later.
LATENCY = {"mix": {"pitch": 1.0, "chord": 0.74}, "stems": {"pitch": 3.0, "chord": 2.74}}


def delayed(t: np.ndarray, x: np.ndarray, lag: float) -> np.ndarray:
    """What was known at each time t: the feature from t - lag (zeros before)."""
    out = np.zeros_like(x)
    k = int(round(lag * (1 / (t[1] - t[0]))))
    if k < len(x):
        out[k:] = x[: len(x) - k]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("score", type=Path)
    ap.add_argument("recording", type=Path)
    ap.add_argument("feats", type=Path)
    ap.add_argument("--causal", action="store_true")
    ap.add_argument("--fps", type=float, default=10.0)
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    rec = args.recording.expanduser()
    s = sc.load(args.score)
    slides = stage1.load_slides(rec, s)
    F = ft.load(args.feats)
    dur = float(F["duration"])
    cues = gt.build(s, slides, rec, dur)
    tl = gt.timeline(cues, fps=args.fps, duration=dur, s=s)
    spans = stage1.song_spans(s, rec, slides)

    base = fl.build(s, slides, fps=args.fps)
    scorer = ev.Scorer(base, slides, spans)
    print(f"{base.P} cells x {base.R} tempi; {len(base.targets)} jump targets; mode {'causal' if args.causal else 'offline'}\n")
    scorer.chance(tl)

    arms = []
    for src in [k[:-8] for k in F if k.endswith("_pitch_t")]:
        arms += [(src, "melody+chords", 1, 1, None, 0), (src, "melody only", 1, 0, None, 0), (src, "chords only", 0, 1, None, 0)]
    arms += [("stems", "CONTROL bars shuffled", 1, 1, 7, 0), ("stems", "CONTROL melody tritone off", 1, 0, None, 6)]
    for src, name, wm, wc, shuf, tr in arms:
        if args.only and args.only not in f"{src} {name}":
            continue
        m = fl.build(s, slides, fps=args.fps, shuffle_seed=shuf, transpose=tr) if (shuf is not None or tr) else base
        t, pcs, chords = fl.frame_evidence(F, src, args.fps, dur)
        if args.causal:
            pcs = delayed(t, pcs, LATENCY[src]["pitch"])
            chords = delayed(t, chords, LATENCY[src]["chord"])
        lo = fl.log_obs(m, pcs, chords, wm, wc)
        t0 = time.time()
        if args.causal:
            marg, _ = fl.forward(m, lo)
            pred = marg.argmax(axis=1)
            # the filter is `lag` behind the room: project forward at the song's pace
            lag = max(LATENCY[src]["pitch"] if wm else 0, LATENCY[src]["chord"] if wc else 0)
            pred = np.clip(pred + int(round(lag * float(tl["rate"]) / fl.CELL)), 0, m.P - 1)
        else:
            post = fl.smooth(m, lo)
            pred = post.argmax(axis=1)
        scorer.report(pred, tl, f"{src:5s} {name}  [{time.time() - t0:.0f}s]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
