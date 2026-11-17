"""When was the scorer's best match actually sung?

    uv run --project sidecar python research/spikes/music_only/diag_score_timing.py [GEOM]

The follower stamps each scorer step at "window end - 3.0 s". Here, per step,
the best-scoring span's last word is located in the ground truth (slide group
and beats into the line, from the transcript-anchored GT timeline): the
moment inside the window when the band reached that word. That gives the
true lag (window end - sung time) per step, and the error of every placement
method against it:

  flat     window end - SCORE_SHIFT (3.0 s)
  attn     window start + the cross-attention argmax of the span's last token
           (cached `ends`, the method that scored 0.304)
  extra    any other placement passed in by a cache's `place` field
           (seconds from window start, per step: e.g. DTW)

Steps whose best span was not sung anywhere in the window (a wrong match)
are counted, not timed.
"""
import json
import sys

import numpy as np

import cv
import diag_position as dp
import follow as fl
import run_joint as rj
import segref
from auto_presenter.eval.metrics import identical_slide_groups
from auto_presenter.eval.replay import load_recording

GEOM = sys.argv[1] if len(sys.argv) > 1 else ""
SHIFT = 3.0


def main() -> int:
    agg = {}
    for key in ("wabn", "rl", "komh", "isa"):
        song = cv.Song(key)
        rec = load_recording(cv.S / key)
        item = cv.stage1.song_item(song.s, cv.S / key)
        ig = identical_slide_groups(rec.setlist_content)
        gof = {sl: min({sl} | set(ig.get((item, sl), ()))) for sl in range(len(song.slides))}
        ref = segref.SegmentReference(song.s, song.slides, song.spans, sequences=[rj.roadmap_sequence(song)])
        st, sg = np.asarray(ref.jump_targets), np.asarray(ref.seg_of)
        word_off = {sw: (c - st[sg[c]]) * fl.CELL for sw, c in ref.word_cell.items()}
        data = json.loads((cv.S / f"wscore_mlx{GEOM}_{key}.json").read_text())
        win = float(data.get("win", 6.0))
        # span string -> the (slide, word) ends it may stand for
        owners: dict[str, list[tuple[int, int]]] = {}
        for k, v in data["owner"].items():
            si, j = (int(x) for x in k.split(":"))
            owners.setdefault(v, []).append((si, j))
        grid = np.arange(0, float(song.F["duration"]), 0.1)
        truth = dp.truth_offsets(song, grid)
        rows = {"true": [], "flat": [], "attn": [], "place": []}
        missed = 0
        for k, (t, row) in enumerate(zip(data["steps"], data["scores"])):
            best = data["spans"][int(np.argmax(row))]
            cands = [(gof[si], word_off[(si, j)]) for si, j in owners.get(best, []) if (si, j) in word_off]
            i0, i1 = int((t - win) * 10), min(len(grid), int(t * 10))
            hit = None
            for i in range(max(0, i0), i1):
                tr = truth[i]
                if tr is None:
                    continue
                if any(gof[tr[0]] == g and 0 <= tr[1] - off <= 1.0 for g, off in cands):
                    hit = grid[i]
                    break
            if hit is None:
                missed += 1
                continue
            rows["true"].append(t - hit)
            rows["flat"].append((t - SHIFT) - hit)
            if data.get("ends"):
                e = data["ends"][k][int(np.argmax(row))]
                rows["attn"].append((t - win + e) - hit)
            if data.get("place"):
                rows["place"].append((t - win + data["place"][k]) - hit)
        n = len(rows["true"])
        q = lambda x: f"median {np.median(x):+.2f}, IQR {np.percentile(x, 25):+.2f}..{np.percentile(x, 75):+.2f}, |err|<=0.5 s {np.mean(np.abs(x) <= 0.5):4.0%}" if len(x) else "-"
        print(f"{key:5s} {n} timed, {missed} best spans not sung in the window | true lag (window end - sung): {q(np.array(rows['true']))}")
        for m in ("flat", "attn", "place"):
            if rows[m]:
                print(f"      {m:5s} placement error {q(np.array(rows[m]))}")
                agg.setdefault(m, []).extend(rows[m])
    print("all songs:")
    for m, v in agg.items():
        v = np.array(v)
        print(f"      {m:5s} |err| median {np.median(np.abs(v)):.2f} s, within 0.5 s {np.mean(np.abs(v) <= 0.5):4.0%}, within 1 s {np.mean(np.abs(v) <= 1):4.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
