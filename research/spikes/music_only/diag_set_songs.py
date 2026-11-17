"""Within a known setlist, can music say which song is playing?

For the live set: each of its songs' lead sheets runs as a causal melody
filter over the whole recording in all 12 keys (stage6.evidence). Over a
trailing window, the sheet with the most evidence is music's answer to
"which song". Scored against the golden's song at every second, and per
song change: seconds until music names the new song and holds it 5 s,
against when the words-driven screen switched.
"""
import json

import numpy as np

import cv
import exp_timing as ext
import fusion
import score as sc
import stage1
import stage6
from auto_presenter.eval.replay import load_recording

key = "set1002"
events = json.load(open(fusion.EVENTS))
rec = load_recording(cv.S / key)
F = cv.ft.load(cv.S / f"{key}_feats.npz") if hasattr(cv, "ft") else __import__("features").load(cv.S / f"{key}_feats.npz")
items = {}
for name, sheet in cv.SETS[key].items():
    s = sc.load(cv.S / f"{sheet}.score.json")
    item = stage1.song_item(s, cv.S / key)
    slides = stage1.load_items(cv.S / key)[item]
    rows = [stage6.evidence(F, s, slides, "stems melody", 10.0, k) for k in range(12)]
    items[item] = np.stack(rows)  # 12 x T, per-frame log evidence
T = min(v.shape[1] for v in items.values())
gt = rec.ground_truth
truth = np.full(T, -1)
for i, g in enumerate(gt):
    t1 = gt[i + 1].t_seconds if i + 1 < len(gt) else T / 10
    truth[int(g.t_seconds * 10): int(t1 * 10)] = g.item_idx
dec = []
ext.run(key, events[rec.id], "words", None, None, dec)
for W in (10, 20, 30):
    n = W * 10
    ev = {}
    for it, rows in items.items():
        c = np.concatenate([np.zeros((12, 1)), np.cumsum(rows[:, :T], axis=1)], axis=1)
        idx = np.arange(T)
        win = c[:, idx + 1] - c[:, np.maximum(0, idx + 1 - n)]
        ev[it] = win.max(axis=0)
    its = sorted(ev)
    best = np.array(its)[np.argmax(np.stack([ev[i] for i in its]), axis=0)]
    sung = truth >= 0
    print(f"--- window {W} s: music names the right song {np.mean(best[sung] == truth[sung]):.0%} of sung time (chance ~{1 / len(its):.0%})")
    for i, g in enumerate(gt):
        if i and g.item_idx == gt[i - 1].item_idx:
            continue
        t0 = int(g.t_seconds * 10)
        run, lock = 0, None
        for k in range(t0, T):
            run = run + 1 if best[k] == g.item_idx else 0
            if run >= 50:
                lock = (k - 49 - t0) / 10
                break
        screen = next((d.t_seconds - g.t_seconds for d in dec if d.t_seconds >= g.t_seconds - 5 and d.item_idx == g.item_idx), None)
        print(f"     song {g.item_idx} starts {g.t_seconds:5.0f} s: music holds it after {('%.0f s' % lock) if lock is not None else 'never'};"
              f" screen switched after {('%.0f s' % screen) if screen is not None else 'never'}")
