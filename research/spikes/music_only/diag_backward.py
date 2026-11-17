"""The screen ahead of a tracker that is right: what are those seconds?

For every scored second where the tracker's available belief is the true
slide and the screen shows a later slide of the same song, classify by the
ground truth's own sequence:

* overshoot  — the screen shows the slide the band sings NEXT (an early or
               anticipatory commit the band has not reached yet);
* repeat     — the screen shows a slide the band sang BEFORE the current one
               and has gone back from (a repeat the screen did not follow);
* other      — neither.

Episodes (consecutive seconds) are reported with their lengths, and for
repeats the scheduler's backward rules are checked: slide gap (the rule
blocks a 1-slide backward move unless the band is early in the slide) and
belief confidence against `backward_marginal_threshold` (0.6).
"""
import json

import numpy as np

import cv
import diag_fusion as dg
import fusion
from auto_presenter.eval.metrics import DEFAULT_DEVIATION_GRACE_S, blank_cue_windows, derive_deviation_markers, identical_slide_groups

events = json.load(open(fusion.EVENTS))
held = fusion.held_settings("stems melody")
tot = {}
for key in cv.SONGS:
    song = cv.Song(key)
    rec, item, dec, tr = dg.run_traced(key, 0.0, 0.2, fusion.Music(song, "stems melody", held[key]), events[dg.load_recording(cv.S / key).id])
    gte = rec.ground_truth
    equiv = identical_slide_groups(rec.setlist_content)
    same = lambda g, d: g is not None and d is not None and (g == d or (d[0] == g[0] and d[1] in equiv.get(g, frozenset())))
    excused = list(blank_cue_windows(gte, rec.setlist_content)) + [(m.t_seconds, m.t_seconds + DEFAULT_DEVIATION_GRACE_S) for m in derive_deviation_markers(gte)]
    seq = [(g.t_seconds, (g.item_idx, g.slide_idx)) for g in gte]
    ts = np.arange(gte[0].t_seconds, gte[-1].t_seconds, 0.1)
    di, ti, shown = 0, -1, None
    eps = []  # (kind, start, end, gap, belief marginal)
    for t in ts:
        if any(a <= t < b for a, b in excused):
            continue
        while di < len(dec) and dec[di].t_seconds <= t:
            shown = (dec[di].item_idx, dec[di].slide_idx); di += 1
        while ti + 1 < len(tr) and tr[ti + 1]["avail"] <= t:
            ti += 1
        g = dg.gt_at(gte, t)
        if ti < 0 or same(g, shown) or shown is None:
            continue
        b = tr[ti]
        if not same(g, b["post"]) or shown[0] != b["post"][0] or shown[1] <= b["post"][1]:
            continue
        k = max(i for i, (tt, _) in enumerate(seq) if tt <= t)
        nxt = seq[k + 1][1] if k + 1 < len(seq) else None
        prev = {s for _, s in seq[:k]}
        kind = "overshoot" if same(nxt, shown) else ("repeat" if shown in prev else "other")
        gap = shown[1] - b["post"][1]
        if eps and eps[-1][0] == kind and abs(eps[-1][2] - t) < 0.15:
            eps[-1][2] = t + 0.1
            eps[-1][4].append(b["display_marginal"])
        else:
            eps.append([kind, t, t + 0.1, gap, [b["display_marginal"]]])
    print(f"--- {key}")
    for kind in ("overshoot", "repeat", "other"):
        e = [x for x in eps if x[0] == kind]
        secs = sum(x[2] - x[1] for x in e)
        tot[kind] = tot.get(kind, 0) + secs
        if e:
            lens = [x[2] - x[1] for x in e]
            gaps = [x[3] for x in e]
            conf = [np.median(x[4]) for x in e]
            print(f"   {kind:9s} {secs:6.1f} s in {len(e):2d} episodes (median {np.median(lens):.1f} s, max {max(lens):.1f} s);"
                  f" slide gap 1: {sum(1 for g in gaps if g == 1)}, 2+: {sum(1 for g in gaps if g >= 2)};"
                  f" belief confidence median {np.median(conf):.2f}, >=0.6 in {np.mean(np.array(conf) >= 0.6):.0%}")
    for x in sorted(eps, key=lambda x: x[1] - x[2])[:4]:
        print(f"      e.g. {x[0]:9s} {x[1]:6.1f}-{x[2]:6.1f}s  gap {x[3]}  conf {np.median(x[4]):.2f}")
T = sum(tot.values())
print("\nALL: " + ", ".join(f"{k} {v:.0f} s ({v / T:.0%})" for k, v in tot.items()))
