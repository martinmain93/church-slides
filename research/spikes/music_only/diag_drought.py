"""How late is the screen after the words go quiet?

For every ground-truth slide change (excluding blank cues), on the room's
clock with the half-gap lead:
* lateness — from the cue until the screen first shows the right slide
  (capped at the next cue; "never" if it does not get there);
* drought  — how long before the cue the last transcript update carrying
  words had been delivered (`available_at`), i.e. how long the scheduler
  had been without new evidence when the change was due.
Binned by drought. If lateness grows with drought, the screen is waiting on
words that are not coming — the case where music, which keeps streaming,
is the only evidence there is.
"""
import json
import sys

import numpy as np

import cv
import exp_timing as ext
import fusion
from auto_presenter.eval.metrics import identical_slide_groups
from auto_presenter.eval.replay import load_recording

events = json.load(open(fusion.EVENTS))
keys = sys.argv[1:] or list(cv.SONGS) + ["set1002"]
rows = []
for key in keys:
    rec = load_recording(cv.S / key)
    evs = events[rec.id]
    dec = []
    ext.run(key, evs, "words", None, None, dec)
    eq = identical_slide_groups(rec.setlist_content)
    same = lambda a, b: a is not None and b is not None and (a == b or (a[0] == b[0] and b[1] in eq.get(a, frozenset())))
    gt = rec.ground_truth
    word_avail = sorted(e["available_at"] for e in evs if e["words"])
    for i, g in enumerate(gt):
        if i == 0:
            continue
        tgt = (g.item_idx, g.slide_idx)
        if not rec.setlist_content[g.item_idx].slides[g.slide_idx]:
            continue  # blank cue: not scored
        t1 = gt[i + 1].t_seconds if i + 1 < len(gt) else g.t_seconds + 30
        shown = None
        for d in dec:
            if d.t_seconds <= g.t_seconds:
                shown = (d.item_idx, d.slide_idx)
        if same(tgt, shown):
            late = 0.0
        else:
            late = None
            for d in dec:
                if g.t_seconds < d.t_seconds < t1 and same(tgt, (d.item_idx, d.slide_idx)):
                    late = d.t_seconds - g.t_seconds
                    break
        before = [a for a in word_avail if a <= g.t_seconds]
        drought = g.t_seconds - before[-1] if before else g.t_seconds
        rows.append((key, drought, late, t1 - g.t_seconds))
print(f"{len(rows)} slide changes")
print("words silent before the cue   n    on screen within 1 s   median lateness   never (before next cue)")
for lo, hi in [(0, 2), (2, 4), (4, 8), (8, 999)]:
    sel = [r for r in rows if lo <= r[1] < hi]
    if not sel:
        continue
    got = [r[2] for r in sel if r[2] is not None]
    print(f"  {lo:2d}-{hi if hi < 999 else '...':>3} s                    {len(sel):3d}        {np.mean([r[2] is not None and r[2] <= 1 for r in sel]):5.0%}"
          f"             {np.median(got) if got else float('nan'):5.1f} s          {np.mean([r[2] is None for r in sel]):5.0%}")
for key in keys:
    sel = [r for r in rows if r[0] == key]
    long = [r for r in sel if r[1] >= 4]
    print(f"  {key:8s} changes {len(sel):3d}; after >=4 s without words: {len(long):3d} ({len(long) / max(1, len(sel)):.0%}),"
          f" never reached {np.mean([r[2] is None for r in long]) if long else 0:.0%}")
