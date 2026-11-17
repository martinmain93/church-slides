"""Does the follower know where it is *within the line*, and do beats help?

    uv run --project sidecar python research/spikes/music_only/diag_position.py [keys...]

run_joint's "within-beat" compares segment-reference cells with sheet
positions, which are different coordinates, so it has never measured the
segment follower. Here the truth is put in the follower's own coordinates:
the ground-truth slide and its sheet position become (slide group, beats
into that slide's line); the follower's best cell becomes the same.

At each read-out during singing, for the belief at "now" and the belief
about 3 s ago ("heard"):
  wrong line   the follower's best cell is in a different slide group
  |err|        beats between the follower's and the true place in the line,
               when the line is right (median, and share within 1 beat)
  signed       follower minus truth, beats (positive = ahead of the band)
"""
import json
import sys

import numpy as np

import cv
import diag_anticipation as da
import follow as fl
import fusion
import joint
import run_joint as rj
import score as sc
import segref
from auto_presenter.eval.metrics import identical_slide_groups
from auto_presenter.eval.replay import load_recording


def truth_offsets(song, walls):
    """(slide, beats into that slide's line) per read-out, or None."""
    out = []
    for w in walls:
        i = min(len(song.tl["slide"]) - 1, int(w * song.fps))
        sl, p = int(song.tl["slide"][i]), song.tl["pos"][i]
        if sl <= 0 or not np.isfinite(p):
            out.append(None)
            continue
        sps = [sp for sp in song.spans.get(sl, []) if sp.start - 1e-6 <= p < sc.span_end(song.s, sp) + 1e-6]
        out.append((sl, p - sps[0].start) if sps else None)
    return out


def main() -> int:
    da.setup()
    events = json.loads(fusion.EVENTS.read_text())
    for key in sys.argv[1:] or ("wabn", "rl", "komh", "isa"):
        song = cv.Song(key)
        rec = load_recording(cv.S / key)
        item = cv.stage1.song_item(song.s, cv.S / key)
        ig = identical_slide_groups(rec.setlist_content)
        gof = {sl: min({sl} | set(ig.get((item, sl), ()))) for sl in range(len(song.slides))}
        _, corr = fl.estimate_key_shift(song.ev["mix"][2][:600], song.scorer.m)
        ref = segref.SegmentReference(song.s, song.slides, song.spans, key_shift=int(np.argmax(corr)),
                                      sequences=[rj.roadmap_sequence(song)])
        seg_of, starts, slide_of = np.asarray(ref.seg_of), np.asarray(ref.jump_targets), np.asarray(ref.slide_of)
        T = len(song.ev["mix"][0])
        for tempo in ("", "beats"):
            rj.TEMPO = tempo
            srcs = rj.sources_for(song, ref, events[rec.id], da.ARM) + ([rj.tempo_source(song, ref, T)] if tempo else [])
            walls, pos, rate, marg = joint.follow(ref, srcs, T)
            tru_now = truth_offsets(song, walls)
            tru_heard = truth_offsets(song, walls - 3.0)
            line = []
            for view, margs, tru in (("now", joint.follow.marg_now, tru_now), ("heard", joint.follow.marg_heard, tru_heard)):
                wrong, errs = 0, []
                n = 0
                for m, t in zip(margs, tru):
                    if t is None:
                        continue
                    n += 1
                    c = int(np.argmax(m))
                    if gof[int(slide_of[c])] != gof[t[0]]:
                        wrong += 1
                        continue
                    errs.append((c - starts[seg_of[c]] + 0.5) * fl.CELL - t[1])
                e = np.array(errs)
                line.append(f"{view}: wrong line {wrong / max(1, n):4.0%}, |err| {np.median(np.abs(e)):.2f} beats,"
                            f" <=1 beat {np.mean(np.abs(e) <= 1):4.0%}, signed {np.median(e):+.2f}")
            print(f"{key:5s} {tempo or 'none':5s} | " + " | ".join(line), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
