"""Where does the remaining wrong-slide time come from?

    uv run --project sidecar python research/spikes/music_only/error_anatomy.py [names]

Replays the current follower over each live trace (`music_replay.decisions`,
song changes as the production engine made them live) and classifies every
0.1 s the metric counts as wrong (same exclusions as `wrong_slide_rate`:
blank cues and post-deviation grace are excused):

  wrong song     the screen shows another song (or nothing)
  blank vs words we show the blank while the band sings, or words over the operator's blank
  late           we still show the slide the band just left (the previous cue's)
  early          we already show the slide the band moves to next
  lost           right song, some other slide

Then episodes: maximal runs of wrong time, each with its dominant class and
length; the long ones (>= 5 s) are the destructive mis-slides.
"""
from __future__ import annotations

import collections
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import multi_replay as mr  # noqa: E402
import music_replay as mrp  # noqa: E402
from auto_presenter.eval.metrics import (  # noqa: E402
    DEFAULT_DEVIATION_GRACE_S,
    blank_cue_windows,
    derive_deviation_markers,
    identical_slide_groups,
)

STEP = 0.1


def anatomy(name):
    dec, rec = mrp.decisions(name, mrp.env_arm())
    gt = sorted(rec.ground_truth, key=lambda g: g.t_seconds)
    equiv = identical_slide_groups(rec.setlist_content)
    excused = list(blank_cue_windows(gt, rec.setlist_content))
    excused += [(m.t_seconds, m.t_seconds + DEFAULT_DEVIATION_GRACE_S) for m in derive_deviation_markers(gt)]
    same = lambda g, d: d is not None and (g == d or (d[0] == g[0] and d[1] in equiv.get(g, frozenset())))  # noqa: E731
    out = []  # (t, class)
    gi = di = 0
    shown = None
    for k in range(int((gt[-1].t_seconds - gt[0].t_seconds) / STEP)):
        t = gt[0].t_seconds + k * STEP
        while gi + 1 < len(gt) and gt[gi + 1].t_seconds <= t:
            gi += 1
        while di < len(dec) and dec[di].t_seconds <= t:
            shown = (dec[di].item_idx, dec[di].slide_idx)
            di += 1
        g = (gt[gi].item_idx, gt[gi].slide_idx)
        if same(g, shown) or any(a <= t < b for a, b in excused):
            out.append((t, None))
            continue
        prev = (gt[gi - 1].item_idx, gt[gi - 1].slide_idx) if gi else None
        nxt = (gt[gi + 1].item_idx, gt[gi + 1].slide_idx) if gi + 1 < len(gt) else None
        if shown is None or shown[0] != g[0]:
            c = "wrong song"
        elif (shown[1] == 0) != (g[1] == 0):
            c = "blank vs words"
        elif prev is not None and same(prev, shown):
            c = "late"
        elif nxt is not None and same(nxt, shown):
            c = "early"
        else:
            c = "lost"
        out.append((t, c))
    return out, gt


def episodes(seq):
    eps, cur = [], None
    for t, c in seq:
        if c is None:
            if cur:
                eps.append(cur)
                cur = None
            continue
        if cur is None:
            cur = [t, t, collections.Counter()]
        cur[1] = t + STEP
        cur[2][c] += STEP
    if cur:
        eps.append(cur)
    return [(a, b - a, cnt.most_common(1)[0][0]) for a, b, cnt in eps]


def main() -> int:
    names = sys.argv[1:] or list(mr.CASES)
    tot = collections.Counter()
    by_len = collections.Counter()
    longest = []
    total_t = 0.0
    for name in names:
        seq, gt = anatomy(name)
        total_t += len(seq) * STEP
        c = collections.Counter(cls for _t, cls in seq if cls)
        for k, v in c.items():
            tot[k] += v * STEP
        eps = episodes(seq)
        for a, dur, cls in eps:
            bucket = "<1 s" if dur < 1 else "1-3 s" if dur < 3 else "3-5 s" if dur < 5 else "5-15 s" if dur < 15 else ">=15 s"
            by_len[(bucket, cls)] += dur
            if dur >= 5:
                longest.append((dur, name, round(a, 1), cls))
        wrong = sum(c.values()) * STEP
        print(f"{name:7s} wrong {wrong / (len(seq) * STEP):.3f} | " + "  ".join(f"{k} {v * STEP:.0f}s" for k, v in c.most_common()), flush=True)
    wrong_all = sum(tot.values())
    print(f"\nall: {wrong_all:.0f} s wrong of {total_t:.0f} s ({wrong_all / total_t:.3f})")
    for k, v in tot.most_common():
        print(f"  {k:15s} {v:6.0f} s  {v / wrong_all:5.1%}")
    print("\nby episode length (seconds of wrong time):")
    for b in ("<1 s", "1-3 s", "3-5 s", "5-15 s", ">=15 s"):
        row = {cls: v for (bb, cls), v in by_len.items() if bb == b}
        print(f"  {b:7s} {sum(row.values()):6.0f} s  " + "  ".join(f"{k} {v:.0f}" for k, v in sorted(row.items(), key=lambda x: -x[1])))
    print("\nlongest episodes:")
    for dur, name, a, cls in sorted(longest, reverse=True)[:25]:
        print(f"  {dur:5.1f} s  {name:7s} at {a:6.1f}  {cls}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
