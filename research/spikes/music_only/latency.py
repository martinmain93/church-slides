"""Stage 5's headline: how long after a slide starts does music alone know?

    uv run --project sidecar python research/spikes/music_only/latency.py [--arm "stems both"] [--setting R,W,PJ]

Causal filter bank (cv.Song.run), every song. For each sung slide in the
golden, time from the slide's first sung word (transcript, scoring only)
until the prediction is in the true slide's group and stays there for 1 s.
"Never" counts if it does not happen before the slide ends.

Set against Whisper's measured delivery lag on these recordings: median
~2.85 s, p90 up to 8.8 s (memory: measured_delivery_latency).
"""

from __future__ import annotations

import argparse
import json

import numpy as np

import cv
import gt


def lock_times(song: cv.Song, pred_ok: np.ndarray, hold: float = 1.0, stay: float | None = None) -> list[float | None]:
    out = []
    fps = song.fps
    rate, lead = float(song.tl["rate"]), float(song.tl["lead"])
    cues = gt.build(song.s, song.slides, cv.S / song.key, float(song.F["duration"]))
    need = int(hold * fps)
    for c in cues:
        if c.span is None or c.slide <= 0:
            continue
        icpt, slope = gt.fit(c, rate, lead)
        t_first = max(c.t0 - 1.0, (c.span.start - icpt) / slope)
        a, b = int(t_first * fps), int(c.t1 * fps)
        got = None
        lo, hi = max(0, a), min(b, len(pred_ok))
        if stay is None:
            run = 0
            for i in range(lo, hi):
                run = run + 1 if pred_ok[i] else 0
                if run >= need:
                    got = (i - need + 1 - a) / fps
                    break
        else:
            # the first moment from which the answer is right and stays right
            # for `stay` of what is left of the slide (at least 2 s of it)
            for i in range(lo, hi - int(2 * fps)):
                if pred_ok[i] and pred_ok[i:hi].mean() >= stay:
                    got = (i - a) / fps
                    break
        out.append(got)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", default="stems both")
    ap.add_argument("--setting", default="", help="robust,w,p_jump; default: each song's held-out choice from cv_causal.json")
    args = ap.parse_args()
    held = {}
    if not args.setting:
        res = json.loads((cv.S / "cv_causal.json").read_text())
        configs = [tuple(c) for c in res["configs"]]
        table = np.array(res["results"][args.arm])
        for si, key in enumerate(res["songs"]):
            others = [j for j in range(len(res["songs"])) if j != si]
            held[key] = configs[int(np.argmax(table[:, others, 0].mean(axis=1)))]
    allt = []
    print(f"=== {args.arm}, causal")
    for key in cv.SONGS:
        song = cv.Song(key)
        cfg = tuple(float(x) for x in args.setting.split(",")) if args.setting else held[key]
        song.run(args.arm, *cfg, causal=True)
        pred = song._last_pred
        ok = np.array([song.tl["slide"][i] in song.scorer.cover_eq[c] for i, c in enumerate(pred)])
        lt = lock_times(song, ok)
        got = np.array([x for x in lt if x is not None])
        allt += lt
        print(f"  {key:5s} {str(cfg):22s} slides {len(lt):3d}  locked {len(got) / max(1, len(lt)):4.0%}"
              f"  median {np.median(got) if len(got) else float('nan'):5.1f} s  p90 {np.percentile(got, 90) if len(got) else float('nan'):5.1f} s")
    got = np.array([x for x in allt if x is not None])
    print(f"  ALL   locked {len(got) / len(allt):.0%} of {len(allt)} slides; median {np.median(got):.1f} s, p90 {np.percentile(got, 90):.1f} s"
          f"   (Whisper: ~2.85 s median, p90 up to 8.8 s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
