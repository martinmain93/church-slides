"""When perfect tune position flips a correct belief to a wrong one, why?

For every event where oracle fusion (lam 1) turns the tracker's right slide
into a wrong one, report: where the tracker's mass sat inside the true slide
(its expected word index), which word the tune position points at, and the
total boost given to the true slide's mass versus the winning wrong slide's.
Then, over *all* events on the true slide, the word-index offset between the
tracker and the tune: if that is routinely a few words, per-word fusion
punishes the right slide for the tracker's ordinary lag.
"""
import json
from collections import defaultdict

import numpy as np

import cv
import diag_fusion as dg
import fusion
from auto_presenter.asr.types import TranscriptEvent, Word
from auto_presenter.decision.production import build_engine
from auto_presenter.index.library import build_index
from auto_presenter.protocol import ParticleFilterTunables

LAM, EPS = 1.0, 0.2
events = json.load(open(fusion.EVENTS))
all_off = []
for key in cv.SONGS:
    song = cv.Song(key)
    rec = dg.load_recording(cv.S / key)
    oracle = dg.Oracle(song)
    indexed = build_index(fusion.content(rec))
    engine = build_engine(indexed, ParticleFilterTunables())
    ff = engine.filter.filter
    item = cv.stage1.song_item(song.s, cv.S / key)
    it = indexed.item(item)
    words = {si: it.slide(si).words for si in range(len(it.slides)) if it.slide(si).words}
    cells = fusion.word_cells(song, words)
    orig = ff.update
    cur = {}
    flips = []

    def fused(event):
        b0 = orig(event)
        ev = cur["ev"]
        p = oracle.belief_at(ev["t_end"], ev["available_at"])
        g = dg.gt_at(rec.ground_truth, ev["t_end"])
        if p is None or g is None or g[0] != item:
            return b0
        r = {sw: max(p[c] for c in cs) for sw, cs in cells.items()}
        mean = np.mean(list(r.values())) or 1.0
        alpha = ff._alpha
        # where the tracker's mass sits inside the true slide, before fusion
        mass_w = defaultdict(float)
        for (i, si, wi, _d), v in alpha.items():
            if i == item and si == g[1]:
                mass_w[wi] += v
        tune_w = [wi for (si, wi), v in r.items() if si == g[1] and v == max(r.get((g[1], w), 0) for w in range(len(words.get(g[1], ())))) and v > 0]
        if mass_w and tune_w:
            tot = sum(mass_w.values())
            exp_w = sum(w * v for w, v in mass_w.items()) / tot
            all_off.append(exp_w - np.mean(tune_w))
        boost = defaultdict(float)
        pre_mass = defaultdict(float)
        before = sum(v for st, v in alpha.items() if st[0] == item)
        for st in list(alpha):
            if st[0] == item and (st[1], st[2]) in r:
                f = (EPS + r[(st[1], st[2])] / mean) ** LAM
                pre_mass[st[1]] += alpha[st]
                alpha[st] *= f
                boost[st[1]] += alpha[st]
        after = sum(v for st, v in alpha.items() if st[0] == item)
        for st in alpha:
            if st[0] == item and after > 0:
                alpha[st] *= before / after
        ff._belief_cache = None
        b1 = ff._snapshot()
        same = lambda a, b: a == b[1]
        if b0.slide_idx == g[1] and b1.slide_idx != g[1] and b0.item_idx == item:
            w = b1.slide_idx
            flips.append((ev["t_end"], g[1], w, pre_mass[g[1]], pre_mass[w],
                          boost[g[1]] / max(pre_mass[g[1]], 1e-12), boost[w] / max(pre_mass[w], 1e-12),
                          (sum(k * v for k, v in mass_w.items()) / max(1e-12, sum(mass_w.values()))), tune_w[:3]))
        return ff._snapshot()

    ff.update = fused
    for ev in events[rec.id]:
        cur["ev"] = ev
        engine.update(TranscriptEvent(words=[Word(text=w["text"], t_start=w["t_start"], t_end=w["t_end"]) for w in ev["words"]],
                                      is_final=True, segment_id=ev["segment_id"]))
    print(f"--- {key}: {len(flips)} right->wrong flips under perfect tune position")
    for f in flips[:8]:
        t, gs, ws, mg, mw, bg, bw, ew, tw = f
        print(f"   t {t:6.1f}  true slide {gs:2d} mass {mg:.3f} boost x{bg:5.2f} (tracker at word {ew:4.1f}, tune says {tw})"
              f"  ->  slide {ws:2d} mass {mw:.4f} boost x{bw:6.2f}")
off = np.array(all_off)
print(f"\nTracker's expected word index minus the tune's word, true slide, all events: median {np.median(off):+.1f},"
      f" |offset| median {np.median(np.abs(off)):.1f} words, >=2 words on {np.mean(np.abs(off) >= 2):.0%}")
