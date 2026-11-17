"""Recorded reference, smoke test on a song's own repeats.

The reference is WABN's first bridge and chorus-3 passes (167-284 s), with
its operator cues (the golden) as the slide map and its offline transcript
as the reference words. The test follows the same song from 320 s to the end
(second bridge, chorus 3, ending) against it. Compared, on the same stretch,
with the lead-sheet segment follower and with production (half-gap lead).

Same band, same day: the easiest cross-performance case there is. A real
test needs the same song from two different services.
"""
import json

import numpy as np

import cv
import exp_timing as ext
import fusion
import joint
import recorded
import run_joint as rj
import segref
import follow as fl
from auto_presenter.eval.metrics import anticipation_error_ms, wrong_slide_rate
from auto_presenter.eval.replay import load_recording
from auto_presenter.eval.types import Decision
from auto_presenter.eval.types import GroundTruthEvent

key, REF0, REF1, TEST0 = "wabn", 167.0, 284.0, 320.0
events = json.load(open(fusion.EVENTS))
song = cv.Song(key)
rec = load_recording(cv.S / key)
fps = 10.0
_, pcs_s, _ = song.ev["stems"]
_, _, ch_m = song.ev["mix"]
gt = rec.ground_truth
cues = []
for i, g in enumerate(gt):
    t1 = gt[i + 1].t_seconds if i + 1 < len(gt) else REF1
    if REF0 <= g.t_seconds < REF1 and g.slide_idx > 0:
        cues.append((g.t_seconds, min(t1, REF1), g.slide_idx))
ref_words = [(w["start"], w["word"]) for w in json.load(open(cv.S / key / "transcript.json"))["words"]]
ref = recorded.RecordedReference(cues, pcs_s, ch_m, ref_words)
print(f"recorded reference: {len(cues)} cue segments, {ref.P} cells ({ref.P / 10:.0f} s), slides {sorted(set(c[2] for c in cues))}")

# the test stretch: features and words from TEST0 on, times shifted to start at 0
o = int(TEST0 * fps)
T = len(pcs_s) - o
live_words = [(w["t_start"] - TEST0, e["available_at"] - TEST0, w["text"]) for e in events[rec.id] for w in e["words"] if w["t_start"] >= TEST0]
gt_test = [GroundTruthEvent(t_seconds=g.t_seconds - TEST0, item_idx=g.item_idx, slide_idx=g.slide_idx) for g in gt if g.t_seconds >= TEST0 - 1e-9]
gt_test = [GroundTruthEvent(t_seconds=0.0, item_idx=0, slide_idx=next(g.slide_idx for g in reversed(gt) if g.t_seconds <= TEST0))] + gt_test


def score(walls, slides):
    dec, shown = [], None
    for t, sl in zip(walls, slides):
        if sl is not None and sl != shown:
            shown = sl
            dec.append(Decision(t_seconds=float(t), item_idx=0, slide_idx=int(sl), source="ai"))
    w = wrong_slide_rate(dec, gt_test, None, forgive_unpredictable=True, setlist_content=rec.setlist_content, forgive_identical_slides=True)
    a = anticipation_error_ms(dec, gt_test)
    return w, (float(np.median(a)) if a else float("nan")), len(dec)


from auto_presenter.eval.metrics import identical_slide_groups
ig = identical_slide_groups(rec.setlist_content)
ident = {sl: set(ig.get((0, sl), ())) for sl in range(len(song.slides))}
for arm in ("words", "words+mel_stem", "words+mel_stem+chd_mix"):
    srcs = []
    if "words" in arm:
        evs = [(t, a, v) for t, a, x in live_words if (v := ref.word(x)) is not None]
        srcs.append(joint.Source("words", 3.5, 1.0, events=evs))
    if "mel_stem" in arm:
        srcs.append(joint.Source("mel", 3.0, 0.4, dense=ref.melody(pcs_s[o:])))
    if "chd_mix" in arm:
        srcs.append(joint.Source("chd", 0.74, 0.4, dense=ref.chords(ch_m[o:])))
    walls, pos, rate, marg = joint.follow(ref, srcs, T)
    r = score(walls, rj.slide_readout(marg, ref.slide_of, ident))
    print(f"  recorded ref  {arm:24s} wrong-slide {r[0]:.3f}  anticip {r[1]:+6.0f} ms  {r[2]} changes")

# lead-sheet segment follower on the same stretch
_, corr = fl.estimate_key_shift(song.ev["mix"][2][:600], song.scorer.m)
sref = segref.SegmentReference(song.s, song.slides, song.spans, key_shift=int(np.argmax(corr)), sequences=[rj.roadmap_sequence(song)])
for arm in ("words", "words+mel_stem"):
    srcs = []
    evs = [(t, a, v) for t, a, x in live_words if (v := sref.word(x)) is not None]
    srcs.append(joint.Source("words", 3.5, 1.0, events=evs))
    if "mel" in arm:
        srcs.append(joint.Source("mel", 3.0, 0.4, dense=sref.melody(pcs_s[o:])))
    walls, pos, rate, marg = joint.follow(sref, srcs, T)
    r = score(walls, rj.slide_readout(marg, sref.slide_of, ident))
    print(f"  lead sheet    {arm:24s} wrong-slide {r[0]:.3f}  anticip {r[1]:+6.0f} ms  {r[2]} changes")

# production on the same stretch
dec = []
ext.run(key, events[rec.id], "words", None, None, dec)
dec_t = [Decision(t_seconds=d.t_seconds - TEST0, item_idx=d.item_idx, slide_idx=d.slide_idx, source="ai") for d in dec if d.t_seconds >= TEST0 - 5]
w = wrong_slide_rate(dec_t, gt_test, None, forgive_unpredictable=True, setlist_content=rec.setlist_content, forgive_identical_slides=True)
print(f"  production (half-gap lead)             wrong-slide {w:.3f}")
