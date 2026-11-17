"""Sheet-roadmap reference vs slide-segment reference, second by second.

    uv run --project sidecar python research/spikes/music_only/diag_refs.py rl [arm]

Same follower, same evidence, two references. For every scored second the
screen is wrong under one and right under the other, list the episode: when,
the true slide, what each showed. That says what the segment reference gets
wrong that the roadmap does not.
"""
import json
import sys

import numpy as np

import cv
import diag_fusion as dg
import fusion
import joint
import lead
import run_joint as rj
import segref
from auto_presenter.eval.metrics import identical_slide_groups
from auto_presenter.eval.replay import load_recording

key = sys.argv[1] if len(sys.argv) > 1 else "rl"
arm = sys.argv[2] if len(sys.argv) > 2 else "words"
events = json.load(open(fusion.EVENTS))
song = cv.Song(key)
rec = load_recording(cv.S / key)
item = cv.stage1.song_item(song.s, cv.S / key)
_, corr = cv.fl.estimate_key_shift(song.ev["mix"][2][:600], song.scorer.m) if hasattr(cv, "fl") else (None, None)
import follow as fl
_, corr = fl.estimate_key_shift(song.ev["mix"][2][:600], song.scorer.m)
k = int(np.argmax(corr))
T = len(song.ev["mix"][0])
ig = identical_slide_groups(rec.setlist_content)
ident = {sl: set(ig.get((item, sl), ())) for sl in range(len(song.slides))}
same = lambda a, b: a is not None and b is not None and (a == b or b in ident.get(a, ()))
runs = {}
for name, ref, smap in (
    ("roadmap", lead.LeadSheetReference(song.s, song.slides, key_shift=k), None),
    ("segment", segref.SegmentReference(song.s, song.slides, song.spans, key_shift=k, sequences=[rj.roadmap_sequence(song)]), "seg"),
):
    smap = list(ref.slide_of) if smap == "seg" else rj.slide_map(song, ref.P)
    srcs = rj.sources_for(song, ref, events[rec.id], rj.ARMS[arm])
    walls, pos, rate, marg = joint.follow(ref, srcs, T)
    shown = rj.slide_readout(marg, smap, ident)
    runs[name] = (walls, shown)
walls = runs["roadmap"][0]
print(f"{key} {arm}: GT cues " + " ".join(f"{g.t_seconds:.0f}:{g.slide_idx}" for g in rec.ground_truth))
cur = None
for i, t in enumerate(walls):
    g = dg.gt_at(rec.ground_truth, t)
    if g is None or g[1] == 0:
        continue
    a, b = runs["roadmap"][1][i], runs["segment"][1][i]
    ra, rb = same(g[1], a), same(g[1], b)
    tag = None if ra == rb else ("segment wrong" if ra else "roadmap wrong")
    if tag != (cur[0] if cur else None):
        if cur and cur[0]:
            print(f"   {cur[1]:6.1f}-{t:6.1f}s  {cur[0]:14s} truth {cur[2]:2d}  roadmap showed {cur[3]}  segment showed {cur[4]}")
        cur = (tag, t, g[1], a, b)
