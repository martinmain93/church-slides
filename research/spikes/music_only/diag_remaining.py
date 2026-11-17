"""Is the scheduler's "seconds left in this slide" biased, and how?

For every belief where the tracker has the right slide (ground truth at the
belief's own audio time), compare the projection's `seconds_remaining`
— (1 - position) * words / word-rate — with what actually remained: the next
ground-truth cue after the belief time, minus the belief time. Binned by the
tracker's position in the slide. A uniform-words model ignores the held last
note and the gap before the next line; if that is the error, it shows up as
a roughly constant positive residual that does not shrink near the end.
"""
import json

import numpy as np

import cv
import diag_fusion as dg
import diag_scheduler as ds
import fusion
from auto_presenter.eval.metrics import identical_slide_groups

events = json.load(open(fusion.EVENTS))
rows = []
for key in list(cv.SONGS):
    rec, dec, log = ds.run(cv.S / key, events[ds.load_recording(cv.S / key).id])
    gte = rec.ground_truth
    eq = identical_slide_groups(rec.setlist_content)
    same = lambda g, d: g is not None and d is not None and (g == d or (d[0] == g[0] and d[1] in eq.get(g, frozenset())))
    for e in log:
        if e["secs_left"] is None:
            continue
        g = dg.gt_at(gte, e["t_end"])
        if not same(g, (e["item"], e["mode"])):
            continue
        nxt = [x.t_seconds for x in gte if x.t_seconds > e["t_end"]]
        if not nxt:
            continue
        actual = nxt[0] - e["t_end"]
        if actual > 30:
            continue
        rows.append((key, e["position"], e["secs_left"], actual))
r = np.array([(p, s, a) for _, p, s, a in rows])
print(f"{len(r)} beliefs on the right slide")
print("position bin   n   predicted left   actual left   actual - predicted (median, IQR)")
for lo, hi in [(0, .25), (.25, .5), (.5, .75), (.75, .9), (.9, 1.01)]:
    m = (r[:, 0] >= lo) & (r[:, 0] < hi)
    if m.sum() < 5:
        continue
    d = r[m, 2] - r[m, 1]
    print(f"  {lo:.2f}-{min(hi, 1):.2f}  {m.sum():4d}   {np.median(r[m, 1]):8.1f} s      {np.median(r[m, 2]):8.1f} s     {np.median(d):+5.1f} s  ({np.percentile(d, 25):+.1f} .. {np.percentile(d, 75):+.1f})")
for key in cv.SONGS:
    k = np.array([(p, s, a) for kk, p, s, a in rows if kk == key and p >= 0.5])
    if len(k):
        print(f"  {key:5s} second half of slides: actual - predicted median {np.median(k[:, 2] - k[:, 1]):+.1f} s (n={len(k)})")
