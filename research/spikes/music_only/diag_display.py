"""Where does displayed error come from: early, late, or elsewhere?

    uv run --project sidecar python research/spikes/music_only/diag_display.py

Per read-out, the slide the follower shows against the operator's slide at
that moment (identical-word slides count as the same). Wrong time is split:
  early  — showing the slide the operator goes to next, before they do
  late   — still showing the slide the operator just left
  other  — anything else (lost, a repeat's wrong copy, another section)
and switches are counted, with how many were undone within 3 s (flicker)."""
import json
import numpy as np
import cv, fusion, joint, segref, follow as fl, run_joint as rj, diag_anticipation as da
from auto_presenter.eval.metrics import identical_slide_groups
from auto_presenter.eval.replay import load_recording

da.setup()
events = json.loads(fusion.EVENTS.read_text())
CONFIGS = [("", 0.0), ("", 1.0), ("beats", 0.0), ("beats", 0.5), ("beats", 1.0)]
for key in ("wabn", "rl", "komh", "isa"):
    song = cv.Song(key); rec = load_recording(cv.S / key)
    item = cv.stage1.song_item(song.s, cv.S / key)
    ig = identical_slide_groups(rec.setlist_content)
    ident = {sl: set(ig.get((item, sl), ())) for sl in range(len(song.slides))}
    same = lambda a, b: a == b or (a is not None and b in ident.get(a, ()))
    cues = sorted((c.t_seconds, c.slide_idx if c.item_idx == item else -2) for c in rec.ground_truth)
    ct = np.array([c[0] for c in cues])
    _, corr = fl.estimate_key_shift(song.ev["mix"][2][:600], song.scorer.m)
    ref = segref.SegmentReference(song.s, song.slides, song.spans, key_shift=int(np.argmax(corr)),
                                  sequences=[rj.roadmap_sequence(song)])
    T = len(song.ev["mix"][0])
    for tempo, la in CONFIGS:
        rj.TEMPO = tempo
        srcs = rj.sources_for(song, ref, events[rec.id], da.ARM) + ([rj.tempo_source(song, ref, T)] if tempo else [])
        walls, pos, rate, marg = joint.follow(ref, srcs, T, lookahead_s=la)
        shown = rj.slide_readout(marg, list(ref.slide_of), ident)
        n = {"ok": 0, "early": 0, "late": 0, "other": 0}
        for w, s in zip(walls, shown):
            k = np.searchsorted(ct, w, side="right") - 1
            if k < 0 or cues[k][1] < 0:
                continue
            cur = cues[k][1]
            nxt = cues[k + 1][1] if k + 1 < len(cues) else None
            prv = cues[k - 1][1] if k > 0 else None
            if same(cur, s):
                n["ok"] += 1
            elif nxt is not None and same(nxt, s):
                n["early"] += 1
            elif prv is not None and same(prv, s):
                n["late"] += 1
            else:
                n["other"] += 1
        tot = sum(n.values())
        sw = [(walls[i], shown[i]) for i in range(1, len(shown)) if shown[i] != shown[i - 1]]
        undone = sum(1 for i in range(len(sw)) for j in range(i + 1, len(sw))
                     if sw[j][0] - sw[i][0] <= 3 and i > 0 and same(sw[j][1], sw[i - 1][1]) and j == i + 1)
        print(f"  {key:5s} tempo={tempo or 'none':5s} look {la:.1f} | wrong {1 - n['ok'] / tot:.3f} = early {n['early'] / tot:.3f}"
              f" + late {n['late'] / tot:.3f} + other {n['other'] / tot:.3f} | {len(sw)} switches, {undone} undone within 3 s", flush=True)
