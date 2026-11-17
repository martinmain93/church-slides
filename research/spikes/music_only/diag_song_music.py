"""What did music say about the song at each song switch the screen made?

For the live set, per-sheet causal melody evidence (all 12 keys, as in
diag_set_songs.py), summed over a trailing window and converted into a
per-second advantage of each song over the best other. At every moment
the words-driven screen switched song (cross-song confidence 0.70 and 0.40),
report music's preferred song and its margin, and whether the switch was
genuine (golden) — i.e. could music have vetoed the false ones and backed
the true ones?
"""
import json
import sys

import numpy as np

import cv
import diag_songs as dso
import fusion
import score as sc
import stage1
import stage6
import features
from auto_presenter.eval.replay import load_recording

KEY = "set1002"
W = 20
ARM = sys.argv[1] if len(sys.argv) > 1 else "stems melody"


def music_song_evidence():
    F = features.load(cv.S / f"{KEY}_feats.npz")
    items = {}
    for sheet in cv.SETS[KEY].values():
        s = sc.load(cv.S / f"{sheet}.score.json")
        item = stage1.song_item(s, cv.S / KEY)
        slides = stage1.load_items(cv.S / KEY)[item]
        items[item] = np.stack([stage6.evidence(F, s, slides, ARM, 10.0, k) for k in range(12)])
    T = min(v.shape[1] for v in items.values())
    n = W * 10
    ev = {}
    for it, rows in items.items():
        c = np.concatenate([np.zeros((12, 1)), np.cumsum(rows[:, :T], axis=1)], axis=1)
        idx = np.arange(T)
        ev[it] = (c[:, idx + 1] - c[:, np.maximum(0, idx + 1 - n)]).max(axis=0) / W
    return ev


def main():
    events = json.load(open(fusion.EVENTS))
    rec = load_recording(cv.S / KEY)
    ev = music_song_evidence()
    its = sorted(ev)
    np.save(cv.S / f"set1002_music_song_ev_{ARM.replace(' ', '_')}.npy", np.stack([ev[i] for i in its]))
    gt = rec.ground_truth

    def truth(t):
        cur = None
        for g in gt:
            if g.t_seconds <= t:
                cur = g.item_idx
        return cur

    for val in (0.70, 0.40):
        def hook(engine, e, val=val):
            engine.scheduler.config.cross_song_marginal_threshold = val
        _, dec, _ = dso.run(KEY, events[rec.id], hook=hook)
        print(f"--- cross-song confidence {val}")
        for i, d in enumerate(dec):
            if i and d.item_idx == dec[i - 1].item_idx:
                continue
            k = min(int(d.t_seconds * 10), len(ev[its[0]]) - 1)
            vals = {it: ev[it][k] for it in its}
            best = max(vals, key=vals.get)
            margin_new = vals[d.item_idx] - max(v for it, v in vals.items() if it != d.item_idx)
            print(f"   {d.t_seconds:6.1f} s screen -> song {d.item_idx}; band is on song {truth(d.t_seconds)} "
                  f"({'genuine' if truth(d.t_seconds) == d.item_idx else 'FALSE'}); music prefers song {best},"
                  f" new song's margin {margin_new:+.2f}/s")
    # music's margin for the true song through each song, as context
    print("\nmusic's margin for the true song (per s of window), by song: median / 10th pct")
    for i, g in enumerate(gt):
        if i and g.item_idx == gt[i - 1].item_idx:
            continue
        t1 = next((x.t_seconds for x in gt[i:] if x.item_idx != g.item_idx), len(ev[its[0]]) / 10)
        a, b = int((g.t_seconds + W) * 10), int(t1 * 10)
        if b <= a:
            continue
        m = np.array([ev[g.item_idx][k] - max(ev[o][k] for o in its if o != g.item_idx) for k in range(a, min(b, len(ev[its[0]])))])
        print(f"   song {g.item_idx} ({g.t_seconds:.0f}-{t1:.0f} s): {np.median(m):+.2f} / {np.percentile(m, 10):+.2f}, positive {np.mean(m > 0):.0%}")


if __name__ == "__main__":
    main()
