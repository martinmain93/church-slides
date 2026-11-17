"""Two slides that share a tune: what moves the belief between them?

    uv run --project sidecar python research/spikes/music_only/diag_copies.py isa 2 5 22 36

Per read-out in the window, with and without beat tempo: each slide's share of
the belief, the tempo belief's mean and spread, and what evidence arrived
since the last read-out (free words with their text; scored steps with
which of the two slides they favour)."""
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
from auto_presenter.eval.replay import load_recording

key, A, B, t0, t1 = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5])
da.setup()
events = json.loads(fusion.EVENTS.read_text())
song = cv.Song(key)
rec = load_recording(cv.S / key)
_, corr = fl.estimate_key_shift(song.ev["mix"][2][:600], song.scorer.m)
ref = segref.SegmentReference(song.s, song.slides, song.spans, key_shift=int(np.argmax(corr)),
                              sequences=[rj.roadmap_sequence(song)])
sl = np.asarray(ref.slide_of)
T = len(song.ev["mix"][0])
print("slide", A, ":", " ".join(song.slides[A]))
print("slide", B, ":", " ".join(song.slides[B]))
for tempo in ("", "beats"):
    rj.TEMPO = tempo
    srcs = rj.sources_for(song, ref, events[rec.id], da.ARM) + ([rj.tempo_source(song, ref, T)] if tempo else [])
    walls, pos, rate, marg = joint.follow(ref, srcs, T)
    rm = joint.follow.rate_marg
    q = ref.rates * fl.CELL
    words = [s for s in srcs if s.name == "words"][0].events
    scored = [s for s in srcs if s.name == "scored"][0].events
    print(f"== tempo {tempo or 'none'}")
    prev = None
    for k, w in enumerate(walls):
        if not (t0 <= w <= t1):
            prev = w
            continue
        m = marg[k] / marg[k].sum()
        pa, pb = m[sl == A].sum(), m[sl == B].sum()
        r = rm[k] / rm[k].sum()
        mean = r @ q
        sd = np.sqrt(r @ (q - mean) ** 2)
        got = []
        for ta, tarr, v in words:
            if prev < tarr <= w:
                got.append(f"word@{ta:.1f}")
        for ta, tarr, v in scored:
            if prev < tarr <= w:
                got.append(f"scored@{ta:.1f} A-B {v[sl == A].max() - v[sl == B].max():+.2f}")
        top = sorted(((m[sl == x].sum(), x) for x in set(sl)), reverse=True)[:3]
        op = [c.slide_idx for c in rec.ground_truth if c.t_seconds <= w][-1]
        print(f"  t={w:5.1f} operator {op:2d} | top " + " ".join(f"{x}:{p:.2f}" for p, x in top) + f" | tempo {mean:.2f}±{sd:.2f} pos-in-seg {pos[k] - ref.jump_targets[ref.seg_of[pos[k]]]:3d}/{np.sum(ref.seg_of == ref.seg_of[pos[k]])}  " + " ".join(got))
        prev = w
