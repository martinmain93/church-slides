"""How often does perfect tune position give the TRUE slide no support?

At every transcript event inside the sheet's song with a ground-truth sheet
position, check whether that position (±1 beat, canonical) falls on any of
the true slide's word cells as `fusion.word_cells` maps them. When it does
not, say why: the position is past the slide's last mapped word (a held
note / gap), or inside the slide's span but between mapped cells (words
collapsed onto one position by the interpolation).
"""
import json

import numpy as np

import cv
import diag_fusion as dg
import follow as fl
import fusion
import score as sc
from auto_presenter.index.library import build_index

events = json.load(open(fusion.EVENTS))
for key in cv.SONGS:
    song = cv.Song(key)
    rec = dg.load_recording(cv.S / key)
    oracle = dg.Oracle(song)
    indexed = build_index(fusion.content(rec))
    item = cv.stage1.song_item(song.s, cv.S / key)
    it = indexed.item(item)
    words = {si: it.slide(si).words for si in range(len(it.slides)) if it.slide(si).words}
    cells = fusion.word_cells(song, words)
    n = gap_after = gap_inside = ok = collapsed = 0
    for si in words:
        cs = [cells.get((si, j), [None])[0] for j in range(len(words[si]))]
        if cs and None not in cs:
            collapsed += len(cs) - len(set(cs))
    for ev in events[rec.id]:
        g = dg.gt_at(rec.ground_truth, ev["t_end"])
        p = oracle.belief_at(ev["t_end"], ev["available_at"])
        if p is None or g is None or g[0] != item or g[1] not in words:
            continue
        n += 1
        mine = [c for j in range(len(words[g[1]])) for c in cells.get((g[1], j), [])]
        if max(p[c] for c in mine) > 0:
            ok += 1
            continue
        top = int(np.argmax(p))
        # compare canonically: nearest copy of the true position to each mapped cell
        eq = song.scorer.equiv[top]
        if all(min(abs(e - c) for e in eq) > 0 for c in mine) and min(eq, key=lambda e: min(abs(e - c) for c in mine)) > max(mine):
            gap_after += 1
        else:
            gap_inside += 1
    total_words = sum(len(w) for w in words.values())
    print(f"{key:5s} events {n:3d}: true slide supported {ok / max(1, n):4.0%}; unsupported because position is past its last mapped word "
          f"{gap_after / max(1, n):4.0%}, between its mapped words {gap_inside / max(1, n):4.0%};  words sharing a cell with an earlier word: {collapsed}/{total_words}")
