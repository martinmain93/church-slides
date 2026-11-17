"""Switch timeline with and without the spontaneous hold, against the operator.

    uv run --project sidecar python research/spikes/music_only/diag_hold.py komh 170 300"""
import json
import sys

import numpy as np

import cv
import diag_anticipation as da
import follow as fl
import fusion
import joint
import run_joint as rj
import segref
from auto_presenter.eval.metrics import identical_slide_groups
from auto_presenter.eval.replay import load_recording

key, t0, t1 = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
da.setup()
rj.TEMPO = "beats"
events = json.loads(fusion.EVENTS.read_text())
song = cv.Song(key)
rec = load_recording(cv.S / key)
item = cv.stage1.song_item(song.s, cv.S / key)
ig = identical_slide_groups(rec.setlist_content)
ident = {sl: set(ig.get((item, sl), ())) for sl in range(len(song.slides))}
gof = {sl: min({sl} | ident[sl]) for sl in ident}
T = len(song.ev["mix"][0])
ops = [(c.t_seconds, c.slide_idx) for c in sorted(rec.ground_truth, key=lambda c: c.t_seconds) if t0 <= c.t_seconds <= t1]
print("operator:", "  ".join(f"{t:.1f}:{s}" for t, s in ops))
for mode in ("", "hold"):
    _, corr = fl.estimate_key_shift(song.ev["mix"][2][:600], song.scorer.m)
    ref = segref.SegmentReference(song.s, song.slides, song.spans, key_shift=int(np.argmax(corr)),
                                  sequences=[rj.roadmap_sequence(song)])
    srcs = rj.sources_for(song, ref, events[rec.id], da.ARM) + [rj.tempo_source(song, ref, T)]
    gate = None
    if mode:
        gate, tr = rj.spontaneous_gate(song, ref, ident, key)
    walls, pos, rate, marg = joint.follow(ref, srcs, T, lookahead_s=1.2, gate=gate)
    shown = rj.slide_readout(marg, list(ref.slide_of), ident)
    held = joint.follow.held
    out = []
    for k in range(1, len(walls)):
        if t0 <= walls[k] <= t1 and (gof.get(shown[k]) != gof.get(shown[k - 1]) or held[k] != held[k - 1]):
            out.append(f"{walls[k]:.1f}:{shown[k]}{'H' if held[k] else ''}")
    print(f"{mode or 'no hold':8s}", "  ".join(out))
