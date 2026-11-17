"""Where do beat-tempo flickers happen?

    uv run --project sidecar python research/spikes/music_only/diag_flicker.py [keys...]

Each switch that is undone within 3 s: the slide it went to (the next one,
the previous one, or elsewhere), time since the operator's cue, and the
reference's expected length of the slide on screen (its segment at the
follower's tempo) against how long the band actually stayed on it."""
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

da.setup()
events = json.loads(fusion.EVENTS.read_text())
for key in sys.argv[1:] or ("komh", "isa"):
    song = cv.Song(key)
    rec = load_recording(cv.S / key)
    item = cv.stage1.song_item(song.s, cv.S / key)
    ig = identical_slide_groups(rec.setlist_content)
    ident = {sl: set(ig.get((item, sl), ())) for sl in range(len(song.slides))}
    cues = sorted((c.t_seconds, c.slide_idx if c.item_idx == item else -2) for c in rec.ground_truth)
    ct = np.array([c[0] for c in cues])
    _, corr = fl.estimate_key_shift(song.ev["mix"][2][:600], song.scorer.m)
    ref = segref.SegmentReference(song.s, song.slides, song.spans, key_shift=int(np.argmax(corr)),
                                  sequences=[rj.roadmap_sequence(song)])
    seg_beats = {sl: np.sum(np.asarray(ref.slide_of) == sl) * fl.CELL for sl in set(ref.slide_of)}
    T = len(song.ev["mix"][0])
    rj.TEMPO = "beats"
    srcs = rj.sources_for(song, ref, events[rec.id], da.ARM) + [rj.tempo_source(song, ref, T)]
    walls, pos, rate, marg = joint.follow(ref, srcs, T)
    shown = rj.slide_readout(marg, list(ref.slide_of), ident)
    rm = joint.follow.rate_marg
    qrate = rm @ (ref.rates * fl.CELL) / rm.sum(axis=1)
    sw = [(i, shown[i - 1], shown[i]) for i in range(1, len(shown)) if shown[i] != shown[i - 1]]
    print(f"== {key}: flickers (switch undone within 3 s)")
    kinds: dict[str, int] = {}
    for a, b in zip(sw, sw[1:]):
        if walls[b[0]] - walls[a[0]] > 3 or b[2] != a[1]:
            continue
        i = a[0]
        w = walls[i]
        k = np.searchsorted(ct, w, side="right") - 1
        cur = cues[k][1]
        nxt = cues[k + 1][1] if k + 1 < len(cues) else None
        stay = (ct[k + 1] if k + 1 < len(ct) else T / 10) - ct[k]
        if a[2] == nxt:
            kind = "to next"
        elif k > 0 and a[2] == cues[k - 1][1]:
            kind = "to prev"
        elif a[1] == cur:
            kind = "away from right"
        else:
            kind = "other"
        kinds[kind] = kinds.get(kind, 0) + 1
        exp_s = seg_beats.get(cur, np.nan) / qrate[i]
        print(f"   t={w:6.1f}  {a[1]}->{a[2]}->{b[2]} ({kind}); operator on slide {cur} for {w - ct[k]:4.1f} of {stay:4.1f} s;"
              f" reference expects {exp_s:4.1f} s at tempo {qrate[i]:.2f}")
    print("   ", kinds)
