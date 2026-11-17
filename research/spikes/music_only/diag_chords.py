"""Why does adding chords make the joint follower worse?

    uv run --project sidecar python research/spikes/music_only/diag_chords.py

Three tests, each aimed at one way extra evidence can hurt:

1. Timing. With the ground-truth alignment, how well do the detected chords
   (BTC on the mix) and pitch (vocal stem) agree with the sheet when the
   detector's frames are shifted by -3..+3 s? The best agreement should sit
   at 0. Anything else means evidence is stamped at the wrong audio time
   and pulls the position early or late.
2. Budget. Per second of singing, how much log-evidence does each source
   put on the true position against the best *wrong* slide's position, and
   how often does it point the wrong way? If one source contributes far
   more than its reliability earns, it drowns the others.
3. Oracle. Chords made perfect (the sheet's own chord at the true position,
   delivered as a confident BTC-style distribution with the same latency
   and frame rate). If perfect chords still hurt the follower, the fault is
   in how evidence is combined, not in the detector.
"""
from __future__ import annotations

import json

import numpy as np

import cv
import follow as fl
import fusion
import joint
import reference as rf
import run_joint as rj
import segref
from auto_presenter.eval.replay import load_recording


def agreement_by_offset(song, ref_full, src, kind):
    """Mean log-evidence for the TRUE sheet cell, detector shifted by d frames."""
    tl = song.tl
    pos = tl["pos"]
    ok = np.isfinite(pos) & (tl["slide"] > 0)
    cells = np.where(ok, np.clip((np.nan_to_num(pos) // fl.CELL).astype(int), 0, ref_full.P - 1), 0)
    _, pcs, ch = song.ev[src]
    E = ref_full.melody(pcs) if kind == "mel" else ref_full.chords(ch)
    out = {}
    for d in range(-30, 31, 5):  # frames at 10 fps
        idx = np.arange(len(pos)) + d
        m = ok & (idx >= 0) & (idx < len(E))
        vals = E[idx[m], cells[m]] - E[idx[m]].mean(axis=1)
        out[d / 10] = float(np.mean(vals))
    return out


def main() -> int:
    events = json.loads(fusion.EVENTS.read_text())
    keys = ["wabn", "rl", "komh", "isa"]
    print("1. TIMING: mean evidence for the true cell vs detector shift (s); peak should be at 0")
    for key in keys:
        song = cv.Song(key)
        _, corr = fl.estimate_key_shift(song.ev["mix"][2][:600], song.scorer.m)
        import lead
        ref_full = lead.LeadSheetReference(song.s, song.slides, key_shift=int(np.argmax(corr)))
        for src, kind in (("mix", "chd"), ("stems", "mel")):
            a = agreement_by_offset(song, ref_full, src, kind)
            best = max(a, key=a.get)
            print(f"   {key:5s} {kind} ({src}): peak at {best:+.1f} s;  " + " ".join(f"{d:+.1f}:{v:+.2f}" for d, v in a.items()))
    print("\n2. BUDGET: per second of singing, evidence for the true slide's position minus the best position in a WRONG slide")
    print("   (segment reference, weights as run; a well-configured source earns large margins only where it is reliable)")
    agg = {}
    for key in keys:
        song = cv.Song(key)
        rec = load_recording(cv.S / key)
        _, corr = fl.estimate_key_shift(song.ev["mix"][2][:600], song.scorer.m)
        ref = segref.SegmentReference(song.s, song.slides, song.spans, key_shift=int(np.argmax(corr)), sequences=[rj.roadmap_sequence(song)])
        slide_of = np.array(ref.slide_of)
        # true cell in the segment reference: the true slide's segment, at the GT offset into its span
        tl = song.tl
        seg_start = {sl: c for c, sl in zip(ref.jump_targets, ref.seg_slides)}
        span_start = {si: sps[0].start for si, sps in song.spans.items() if sps}
        def true_cell(i):
            sl = tl["slide"][i]
            p = tl["pos"][i]
            if sl <= 0 or not np.isfinite(p) or sl not in seg_start:
                return None
            # offset within the slide: GT position relative to the nearest span of that slide
            sps = song.spans.get(sl, [])
            off = min((p - sp.start for sp in sps if p >= sp.start - 1e-9), default=None)
            if off is None:
                return None
            c = seg_start[sl] + int(off // fl.CELL)
            return c if c < ref.P and slide_of[c] == sl else None
        srcs = {
            "words": rj.sources_for(song, ref, events[rec.id], ["words"])[0],
            "mel_stem": rj.sources_for(song, ref, events[rec.id], ["mel_stem"])[0],
            "chd_mix": rj.sources_for(song, ref, events[rec.id], ["chd_mix"])[0],
        }
        T = len(tl["t"])
        for name, src in srcs.items():
            margins = []
            wrong = 0
            n = 0
            if src.dense is not None:
                frames = [(i, src.weight * src.dense[i]) for i in range(min(T, len(src.dense)))]
            else:
                frames = [(int(t * 10), src.weight * v) for t, _a, v in src.events]
            for i, e in frames:
                if i >= T:
                    continue
                c = true_cell(i)
                if c is None:
                    continue
                sl = slide_of[c]
                other = e[slide_of != sl]
                if not len(other):
                    continue
                mg = e[c] - other.max()
                margins.append(mg)
                wrong += mg < 0
                n += 1
            sung_s = np.sum((tl["slide"] > 0) & np.isfinite(tl["pos"])) / 10
            a = agg.setdefault(name, [0.0, 0.0, 0, 0, 0.0])
            a[0] += float(np.sum(np.abs(margins))); a[1] += float(np.sum(margins)); a[2] += wrong; a[3] += n; a[4] += sung_s
    for name, (absm, netm, wrong, n, secs) in agg.items():
        print(f"   {name:9s} evidence moved per sung second {absm / secs:6.2f};  net toward the truth {netm / secs:+6.2f};"
              f"  points at a wrong slide on {wrong / max(1, n):4.0%} of its observations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
