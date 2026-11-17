"""Read stage6's cached evidence and say how well each song is identified.

    uv run --project sidecar python research/spikes/music_only/stage6_report.py [--arm "stems both"]

For each recording with a lead sheet: over the time its song is being sung
(from the golden), the share of instants at which its own sheet (best key)
has the highest trailing-window evidence of all five, and how many seconds
from the start of the song until it first does so and keeps it for 5 s.

For every recording, the best evidence any sheet reaches, per second of
window: songs that are on a lead sheet should sit clearly above the ones
that are not, or a music-only system cannot say "this is not one of my
songs".
"""

from __future__ import annotations

import argparse
import json

import numpy as np

import cv
import gt
import score as sc
import stage1


def windowed(x: np.ndarray, n: int) -> np.ndarray:
    c = np.concatenate([[0.0], np.cumsum(x)])
    idx = np.arange(len(x))
    return c[idx + 1] - c[np.maximum(0, idx + 1 - n)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", default="stems melody")
    ap.add_argument("--fps", type=float, default=10.0)
    args = ap.parse_args()
    cache = json.loads((cv.S / "stage6_cache.json").read_text())
    recs = sorted({k.split("|")[1] for k in cache if k.startswith(args.arm + "|")})
    refs = list(cv.SONGS)
    for W in (10, 20, 30):
        n = int(W * args.fps)
        print(f"\n=== {args.arm}, trailing window {W} s")
        print(f"  {'recording':28s} {'own sheet first':>16s} {'time to lock':>13s}   best key   per-window evidence/s: own  best-other")
        neg_best = []
        pos_best = []
        for rk in recs:
            ev = {}
            for ref_k in refs:
                rows = [np.array(cache[f"{args.arm}|{rk}|{ref_k}|{k}"]) for k in range(12) if f"{args.arm}|{rk}|{ref_k}|{k}" in cache]
                if len(rows) < 12:
                    continue
                stack = np.stack([windowed(r, n) for r in rows])  # 12 x T
                ev[ref_k] = (stack.max(axis=0), stack.argmax(axis=0), np.stack(rows).sum(axis=1).argmax())
            if len(ev) < len(refs):
                continue
            T = len(next(iter(ev.values()))[0])
            best_all = np.max([ev[r][0] for r in refs], axis=0)
            if rk in cv.SONGS:
                s = sc.load(cv.S / f"{cv.SONGS[rk]}.score.json")
                cues = gt.load_cues(cv.S / rk, stage1.song_item(s, cv.S / rk))
                tl = np.full(T, -1)
                for i, (t0, sl) in enumerate(cues):
                    t1 = cues[i + 1][0] if i + 1 < len(cues) else T / args.fps
                    tl[int(t0 * args.fps) : int(t1 * args.fps)] = sl
                own = ev[rk][0]
                other = np.max([ev[r][0] for r in refs if r != rk], axis=0)
                sung = tl > 0
                first = own > other
                # time to lock: first instant after the song starts where own leads for 5 s straight
                start = int(np.argmax(tl >= 0))
                run = 0
                lock = None
                for i in range(start, T):
                    run = run + 1 if first[i] else 0
                    if run >= 5 * args.fps:
                        lock = (i - run + 1 - start) / args.fps
                        break
                print(f"  {rk:28s} {first[sung].mean():16.0%} {('%.1f s' % lock) if lock is not None else 'never':>13s}   +{ev[rk][2]:<2d}"
                      f"       {np.median(own[sung]) / W:6.2f}  {np.median(other[sung]) / W:6.2f}")
                pos_best.append(np.median(best_all[sung]) / W)
                # a medley's other song: does any sheet claim it?
                if (tl == gt.OTHER_SONG).any():
                    o = tl == gt.OTHER_SONG
                    print(f"  {'  (its other medley song)':28s} best sheet evidence/s {np.median(best_all[o]) / W:6.2f}")
                    neg_best.append(np.median(best_all[o]) / W)
            else:
                b = np.median(best_all[int(20 * args.fps):]) / W
                which = refs[int(np.bincount(np.argmax([ev[r][0] for r in refs], axis=0)).argmax())]
                print(f"  {rk:28s} {'(no sheet)':>16s} {'':>13s}            best sheet evidence/s {b:6.2f} (mostly {which})")
                neg_best.append(b)
        if pos_best and neg_best:
            print(f"  songs with a sheet: median best evidence/s {np.median(pos_best):.2f} (min {np.min(pos_best):.2f});"
                  f"  without: {np.median(neg_best):.2f} (max {np.max(neg_best):.2f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
