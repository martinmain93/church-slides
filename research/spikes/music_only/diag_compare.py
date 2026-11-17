"""Where does one configuration lose to another, and what did each believe?

    uv run --project sidecar python research/spikes/music_only/diag_compare.py rl "" "--tempo bars" [--lookahead 1.2]

Runs the follower twice on one song (A = first config, B = second), then
lists every stretch where B shows the wrong slide while A shows the right one
(and the reverse), with the operator's slide, what each showed, each one's
tempo belief, and where each thought the band was (slide, beats into the
line) at "now". Identical-word slides count as the same slide.
"""
import json
import shlex
import sys

import numpy as np

import cv
import diag_anticipation as da
import diag_position as dp
import follow as fl
import fusion
import joint
import run_joint as rj
import segref
from auto_presenter.eval.metrics import identical_slide_groups
from auto_presenter.eval.replay import load_recording


def configure(argv: str):
    """Apply run_joint flags (the subset that matters here) to its globals."""
    rj.TEMPO, rj.SPONTANEOUS, rj.P_RATE = "", "", 0.02
    rj.SCORER, rj.SCORE_SHIFT = "mlx_flat", 3.0
    look = 1.2
    toks = shlex.split(argv)
    for i, t in enumerate(toks):
        if t == "--tempo":
            rj.TEMPO = toks[i + 1]
        elif t == "--spontaneous":
            rj.SPONTANEOUS = toks[i + 1]
        elif t == "--p-rate":
            rj.P_RATE = float(toks[i + 1])
        elif t == "--lookahead":
            look = float(toks[i + 1])
        elif t == "--scorer":
            rj.SCORER = toks[i + 1]
        elif t == "--score-shift":
            rj.SCORE_SHIFT = float(toks[i + 1])
    return look


def run(song, rec, ref, events, ident, key, argv):
    look = configure(argv)
    T = len(song.ev["mix"][0])
    srcs = rj.sources_for(song, ref, events[rec.id], da.ARM)
    if rj.TEMPO:
        srcs.append(rj.tempo_source(song, ref, T))
    gate = None
    if rj.SPONTANEOUS:
        gate, _ = rj.spontaneous_gate(song, ref, ident, key)
    walls, pos, rate, marg = joint.follow(ref, srcs, T, lookahead_s=look, gate=gate, p_rate=rj.P_RATE)
    f = joint.follow
    shown = rj.slide_readout(marg, list(ref.slide_of), ident)
    rm = f.rate_marg / f.rate_marg.sum(axis=1, keepdims=True)
    tempo = rm @ (np.asarray(ref.rates) * fl.CELL)
    now_cell = f.marg_now.argmax(axis=1)
    return walls, shown, tempo, now_cell


def main() -> int:
    key, a_argv, b_argv = sys.argv[1], sys.argv[2], sys.argv[3]
    da.setup()
    events = json.loads(fusion.EVENTS.read_text())
    song = cv.Song(key)
    rec = load_recording(cv.S / key)
    item = cv.stage1.song_item(song.s, cv.S / key)
    ig = identical_slide_groups(rec.setlist_content)
    ident = {sl: set(ig.get((item, sl), ())) for sl in range(len(song.slides))}
    gof = {sl: min({sl} | ident[sl]) for sl in ident}
    _, corr = fl.estimate_key_shift(song.ev["mix"][2][:600], song.scorer.m)
    mk = lambda: segref.SegmentReference(song.s, song.slides, song.spans, key_shift=int(np.argmax(corr)),
                                         sequences=[rj.roadmap_sequence(song)], extend_to_next="--extend" in sys.argv)
    ra, rb = mk(), mk()
    A = run(song, rec, ra, events, ident, key, a_argv)
    B = run(song, rec, rb, events, ident, key, b_argv)
    walls = A[0]
    cues = sorted((c.t_seconds, c.slide_idx) for c in rec.ground_truth if c.item_idx == item)
    ct = np.array([c[0] for c in cues])
    op = [cues[np.searchsorted(ct, w, side="right") - 1][1] if w >= ct[0] else None for w in walls]
    truth = dp.truth_offsets(song, walls)
    so, st, sg = np.asarray(ra.slide_of), np.asarray(ra.jump_targets), np.asarray(ra.seg_of)

    def where(cell):
        return f"{so[cell]}+{(cell - st[sg[cell]]) * fl.CELL:.0f}b"

    ok = lambda s, o: o is not None and s is not None and gof.get(s) == gof.get(o)
    for label, win, lose in (("B wrong, A right", A, B), ("A wrong, B right", B, A)):
        print(f"== {label}   (A = '{a_argv}', B = '{b_argv}')")
        total, k = 0.0, 0
        while k < len(walls):
            if op[k] is not None and op[k] > 0 and ok(win[1][k], op[k]) and not ok(lose[1][k], op[k]):
                j = k
                while j + 1 < len(walls) and op[j + 1] == op[k] and ok(win[1][j + 1], op[j + 1]) and not ok(lose[1][j + 1], op[j + 1]):
                    j += 1
                dur = walls[j] - walls[k] + 0.5
                total += dur
                t = truth[k]
                print(f"  {walls[k]:6.1f}-{walls[j]:6.1f} ({dur:4.1f}s) operator {op[k]:2d} (truth {f'{t[0]}+{t[1]:.0f}b' if t else '?'})"
                      f" | right one shows {win[1][k]}, now at {where(win[3][k])}, tempo {win[2][k]:.2f}"
                      f" | wrong one shows {lose[1][k]}, now at {where(lose[3][k])}, tempo {lose[2][k]:.2f}")
                k = j + 1
            else:
                k += 1
        print(f"  total {total:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
