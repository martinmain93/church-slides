"""Does a better "time left" make slide changes land better?

For each arm (half-gap lead throughout), every forward slide change is
matched to the moment the band actually reached that slide (the next ground
truth cue for it): `shown - arrival` on the room's clock. Arms:
words (as is), music average, and SHEET — time left from the ground
truth's own sheet position, the ceiling for any tune-timing signal.
If the sheet arm does not tighten slide-change timing or wrong-slide, timing
precision is not what limits this scheduler.
"""
import json

import numpy as np

import cv
import exp_timing as ext
import fusion
from auto_presenter.eval.metrics import identical_slide_groups
from auto_presenter.eval.replay import load_recording

events = json.load(open(fusion.EVENTS))
held = fusion.held_settings("stems melody")
agg = {}
for key in cv.SONGS:
    rid = load_recording(cv.S / key).id
    song = cv.Song(key)
    rec = load_recording(cv.S / key)
    eq = identical_slide_groups(rec.setlist_content)
    same = lambda a, b: a == b or (a[0] == b[0] and b[1] in eq.get(a, frozenset()))
    seq = [(g.t_seconds, (g.item_idx, g.slide_idx)) for g in rec.ground_truth]
    arms = {"words": ("words", None), "music avg": ("average", fusion.Music(song, "stems melody", held[key])),
            "SHEET avg": ("average", ext.SheetOracle(song)), "SHEET only": ("music", ext.SheetOracle(song))}
    line = []
    for name, (mode, mus) in arms.items():
        dec = []
        wrong, ant, n = ext.run(key, events[rid], mode, mus, song, dec)
        errs = []
        for d in dec:
            tgt = (d.item_idx, d.slide_idx)
            # arrival: the GT cue for this slide nearest to the change, within 8 s either side
            cands = [t for t, sl in seq if same(sl, tgt) and abs(t - d.t_seconds) <= 8]
            if cands:
                errs.append(d.t_seconds - min(cands, key=lambda t: abs(t - d.t_seconds)))
        e = np.array(errs)
        agg.setdefault(name, []).extend(errs)
        agg.setdefault(name + "|wrong", []).append(wrong)
        line.append(f"{name}: {wrong:.3f}  |err| med {np.median(np.abs(e)):.1f}s")
    print(f"{key:5s} " + "   ".join(line))
print()
for name in ("words", "music avg", "SHEET avg", "SHEET only"):
    e = np.array(agg[name])
    print(f"{name:10s} mean wrong {np.mean(agg[name + '|wrong']):.3f};  slide changes vs arrival: median {np.median(e):+.2f}s,"
          f" |err| median {np.median(np.abs(e)):.2f}s, within 1 s {np.mean(np.abs(e) <= 1):.0%}, n={len(e)}")
