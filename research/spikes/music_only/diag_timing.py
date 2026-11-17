"""Could the lead sheet tell the scheduler how long is left in a slide?

    uv run --project sidecar python research/spikes/music_only/diag_timing.py

At every belief where the tracker has the right slide of the sheet's song,
three estimates of "seconds until this slide ends", against what actually
remained (next ground-truth cue minus the belief time):

* words  — the scheduler's own: (1 - position) * words / word-rate;
* music  — the live music filter (causal, 3 s stem lag, projected): its tune
           position, placed inside one of this slide's sheet spans (any copy
           of identical music), beats to the span's end at its own tempo;
* sheet  — the same with the ground truth's sheet position and the song's
           measured tempo: the best a tune-position timing signal could do.

Ground-truth cues sit at the moment a slide should appear, not when its first
word is sung, so a constant offset is expected; what matters is the spread
(IQR), and whether music is precise where words are not.
"""
from __future__ import annotations

import json

import numpy as np

import cv
import diag_fusion as dg
import diag_scheduler as ds
import follow as fl
import fusion
import score as sc
from auto_presenter.eval.metrics import identical_slide_groups


def remaining_from_position(song: cv.Song, slide: int, cell: int, bpm: float) -> float | None:
    """Seconds to the end of `slide`'s span that holds (a copy of) `cell`."""
    best = None
    for sp in song.spans.get(slide, []):
        a, b = sp.start, sc.span_end(song.s, sp)
        for c in song.scorer.equiv[cell]:
            p = (c + 0.5) * fl.CELL
            if a - fl.CELL <= p < b:
                rem = (b - p) * 60.0 / bpm
                best = rem if best is None else min(best, rem)
    return best


def main() -> int:
    events = json.loads(fusion.EVENTS.read_text())
    held = fusion.held_settings("stems melody")
    rows = []
    for key in cv.SONGS:
        song = cv.Song(key)
        music = fusion.Music(song, "stems melody", held[key])
        rec, dec, log = ds.run(cv.S / key, events[ds.load_recording(cv.S / key).id])
        item = cv.stage1.song_item(song.s, cv.S / key)
        gte = rec.ground_truth
        eq = identical_slide_groups(rec.setlist_content)
        same = lambda g, d: g is not None and d is not None and (g == d or (d[0] == g[0] and d[1] in eq.get(g, frozenset())))
        song_bpm = 60.0 * float(song.tl["rate"])
        for e in log:
            if e["secs_left"] is None or e["item"] != item:
                continue
            g = dg.gt_at(gte, e["t_end"])
            if not same(g, (e["item"], e["mode"])):
                continue
            nxt = [x.t_seconds for x in gte if x.t_seconds > e["t_end"]]
            if not nxt or nxt[0] - e["t_end"] > 30:
                continue
            actual = nxt[0] - e["t_end"]
            # live music
            p = music.belief_at(e["t_end"], e["avail"])
            m_rem = None
            if p is not None:
                i = min(int(min(e["t_end"], e["avail"] - music.lag) * song.fps), len(music.bpm) - 1)
                m_rem = remaining_from_position(song, e["mode"], int(np.argmax(p)), music.bpm[i])
            # perfect sheet position
            gi = min(int(e["t_end"] * song.fps), len(song.tl["pos"]) - 1)
            gp = song.tl["pos"][gi]
            s_rem = remaining_from_position(song, e["mode"], min(song.scorer.m.P - 1, int(gp // fl.CELL)), song_bpm) if np.isfinite(gp) else None
            rows.append((key, e["position"], actual, e["secs_left"], m_rem, s_rem))
    print(f"{len(rows)} beliefs on the right slide of the sheet's song\n")
    print("estimator   available   error = actual - estimate: median   IQR          |err|<=1s   (second half of slide only)")
    for name, k in (("words", 3), ("music", 4), ("sheet", 5)):
        for half in (False, True):
            sel = [r for r in rows if r[k] is not None and (not half or r[1] >= 0.5)]
            if not sel:
                continue
            d = np.array([r[2] - r[k] for r in sel])
            avail = len(sel) / len([r for r in rows if (not half or r[1] >= 0.5)])
            print(f"  {name:6s} {'2nd half' if half else 'all     '} {avail:5.0%}      {np.median(d):+5.1f} s   ({np.percentile(d, 25):+.1f} .. {np.percentile(d, 75):+.1f})"
                  f"   {np.mean(np.abs(d - np.median(d)) <= 1):5.0%}")
    print("\nper song, second half of the slide (error = actual - estimate; negative = estimate too long):")
    for key in cv.SONGS:
        sel = [r for r in rows if r[0] == key and r[1] >= 0.5]
        out = []
        for name, k in (("words", 3), ("music", 4), ("sheet", 5)):
            d = np.array([r[2] - r[k] for r in sel if r[k] is not None])
            if len(d):
                out.append(f"{name} {np.median(d):+5.1f} s IQR {np.subtract(*np.percentile(d, [75, 25])):.1f} (n={len(d)})")
        print(f"  {key:5s} " + "   ".join(out))
    # where both exist: is music closer than words, beat for beat?
    both = [r for r in rows if r[4] is not None and r[1] >= 0.5]
    if both:
        dw = np.array([r[2] - r[3] for r in both])
        dm = np.array([r[2] - r[4] for r in both])
        dw, dm = dw - np.median(dw), dm - np.median(dm)
        print(f"\nsecond half, both available (n={len(both)}): music closer than words on {np.mean(np.abs(dm) < np.abs(dw)):.0%};"
              f" spread (IQR) words {np.subtract(*np.percentile(dw, [75, 25])):.1f} s, music {np.subtract(*np.percentile(dm, [75, 25])):.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
