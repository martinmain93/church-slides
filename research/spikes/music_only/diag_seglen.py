"""Does the reference leave time out? Per slide change: how long the band
actually spent from this slide's first word to the next slide's first word
(audio seconds x song tempo = beats), against the length of the slide's
segment in the reference (last word + one bar, or to the next slide's first
word in the roadmap). If audio > reference, the follower can only explain the
extra time with a slower tempo — exactly the bias diag_anticipation shows."""
import numpy as np
import cv, gt, segref, run_joint as rj, follow as fl
import diag_anticipation as da

for key in ("wabn", "rl", "komh", "isa"):
    song = cv.Song(key)
    cues = gt.build(song.s, song.slides, cv.S / key, float(song.F["duration"]))
    fw = [x for x in da.first_word_times(song, cues) if x is not None]
    rate = gt.song_rate(cues)
    for ext in (False, True):
        ref = segref.SegmentReference(song.s, song.slides, song.spans, sequences=[rj.roadmap_sequence(song)], extend_to_next=ext)
        seglen = {sl: np.sum(np.asarray(ref.slide_of) == sl) * fl.CELL for sl in set(ref.slide_of)}
        ratios = []
        for (t0, tf, sl, _), nxt in zip(fw, fw[1:]):
            if sl in seglen and 0 < nxt[1] - tf < 40:
                ratios.append((nxt[1] - tf) * rate / seglen[sl])
        r = np.array(ratios)
        print(f"{key:5s} extend_to_next={ext!s:5s} sung beats / reference beats: median {np.median(r):.2f}, IQR {np.percentile(r,25):.2f}..{np.percentile(r,75):.2f}, >1.25 on {np.mean(r>1.25):.0%}")
