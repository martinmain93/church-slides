"""When the words go quiet, does music already know the next slide?

    uv run --project sidecar python research/spikes/music_only/diag_drought_music.py

For every ground-truth slide change whose song has a lead sheet: the words
screen's lateness (half-gap lead, room's clock), and the music filter's —
the first moment from 3 s before the cue at which the live music belief
(causal: 3 s stem lag, projected to now) sits in music the new slide covers,
held for 1 s. Split by how long the words had been silent before the cue.

A slide that shares music with others (chorus 1 vs 3) counts as "known" when
music is in that shared music: music cannot name the pass, but the tracker's
words — or the slide order — can, and what is being asked here is whether the
music knows *that a new line has started*.
"""
from __future__ import annotations

import json

import numpy as np

import cv
import exp_timing as ext
import fusion
from auto_presenter.eval.metrics import identical_slide_groups
from auto_presenter.eval.replay import load_recording

SETTING = (0.2, 0.4, 0.03)  # what leave-one-song-out chose most; not tuned on the set


def songs_for(key):
    if key in cv.SETS:
        return [cv.Song(key, sheet=sh, all_keys=True) for sh in cv.SETS[key].values()]
    return [cv.Song(key)]


def main() -> int:
    events = json.loads(fusion.EVENTS.read_text())
    rows = []
    for key in list(cv.SONGS) + list(cv.SETS):
        rec = load_recording(cv.S / key)
        evs = events[rec.id]
        dec = []
        ext.run(key, evs, "words", None, None, dec)
        eq = identical_slide_groups(rec.setlist_content)
        same = lambda a, b: a is not None and b is not None and (a == b or (a[0] == b[0] and b[1] in eq.get(a, frozenset())))
        word_avail = sorted(e["available_at"] for e in evs if e["words"])
        musics = {}
        for song in songs_for(key):
            item = cv.stage1.song_item(song.s, cv.S / key)
            musics[item] = (song, fusion.Music(song, "stems melody", SETTING))
        gt = rec.ground_truth
        for i, g in enumerate(gt):
            if i == 0 or g.item_idx not in musics:
                continue
            if not rec.setlist_content[g.item_idx].slides[g.slide_idx]:
                continue
            tgt = (g.item_idx, g.slide_idx)
            t1 = gt[i + 1].t_seconds if i + 1 < len(gt) else g.t_seconds + 30
            # words screen
            shown = None
            for d in dec:
                if d.t_seconds <= g.t_seconds:
                    shown = (d.item_idx, d.slide_idx)
            w_late = 0.0 if same(tgt, shown) else next(
                (d.t_seconds - g.t_seconds for d in dec if g.t_seconds < d.t_seconds < t1 and same(tgt, (d.item_idx, d.slide_idx))), None)
            # music
            song, mu = musics[g.item_idx]
            m_late = None
            run = 0
            for t in np.arange(g.t_seconds - 3.0, t1, 0.1):
                p = mu.belief_at(t, t)
                ok = p is not None and g.slide_idx in song.scorer.cover_eq[int(np.argmax(p))]
                run = run + 1 if ok else 0
                if run >= 10:
                    m_late = t - 1.0 - g.t_seconds + 0.1
                    break
            # control: the same test for a slide of this song the band is NOT
            # moving to (different words), averaged over every such slide
            ctrl = []
            # not the slide being left either: music is still on it before the
            # cue, and counting that as "knowing a wrong slide" inflates the
            # control on any song that alternates two slides
            prev = (gt[i - 1].item_idx, gt[i - 1].slide_idx)
            sung = [k for k, sl in enumerate(rec.setlist_content[g.item_idx].slides)
                    if sl and not same(tgt, (g.item_idx, k)) and not same(prev, (g.item_idx, k))]
            beliefs = [(t, mu.belief_at(t, t)) for t in np.arange(g.t_seconds - 3.0, t1, 0.1)]
            for k in sung:
                run, hit = 0, None
                for t, p in beliefs:
                    ok = p is not None and k in song.scorer.cover_eq[int(np.argmax(p))]
                    run = run + 1 if ok else 0
                    if run >= 10:
                        hit = t - 1.0 - g.t_seconds + 0.1
                        break
                ctrl.append(hit is not None and hit <= 1)
            before = [a for a in word_avail if a <= g.t_seconds]
            drought = g.t_seconds - before[-1] if before else g.t_seconds
            rows.append((key, drought, w_late, m_late, float(np.mean(ctrl)) if ctrl else np.nan))
    print(f"{len(rows)} slide changes in songs with a lead sheet\n")
    print("words silent before cue    n   | WORDS screen: within 1 s / never  | MUSIC knows: within 1 s / median / never | music first")
    for lo, hi in [(0, 2), (2, 4), (4, 999), (0, 999)]:
        sel = [r for r in rows if lo <= r[1] < hi]
        if not sel:
            continue
        w1 = np.mean([r[2] is not None and r[2] <= 1 for r in sel])
        wn = np.mean([r[2] is None for r in sel])
        m1 = np.mean([r[3] is not None and r[3] <= 1 for r in sel])
        mm = [r[3] for r in sel if r[3] is not None]
        mn = np.mean([r[3] is None for r in sel])
        first = np.mean([(r[3] is not None) and (r[2] is None or r[3] < r[2] - 0.5) for r in sel])
        ctl = np.nanmean([r[4] for r in sel])
        label = "all" if (lo, hi) == (0, 999) else f"{lo}-{hi if hi < 999 else '...'} s"
        label += f" (ctrl {ctl:.0%})"
        print(f"  {label:22s} {len(sel):4d}  |        {w1:4.0%}  /  {wn:4.0%}             |      {m1:4.0%}  / {np.median(mm) if mm else float('nan'):+5.1f} s / {mn:4.0%}   | {first:4.0%}")
    print()
    for key in list(cv.SONGS) + list(cv.SETS):
        sel = [r for r in rows if r[0] == key]
        if sel:
            print(f"  {key:8s} n {len(sel):3d}: words within 1 s {np.mean([r[2] is not None and r[2] <= 1 for r in sel]):4.0%},"
                  f" music within 1 s {np.mean([r[3] is not None and r[3] <= 1 for r in sel]):4.0%},"
                  f" music first {np.mean([(r[3] is not None) and (r[2] is None or r[3] < r[2] - 0.5) for r in sel]):4.0%},"
                  f" CONTROL (wrong slide) within 1 s {np.nanmean([r[4] for r in sel]):4.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
