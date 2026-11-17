"""Does the band spend the sheet's beats on each line?

    uv run --project sidecar python research/spikes/music_only/diag_pulse_vs_sheet.py

Per slide change: the time from this slide's first sung word to the next
slide's first sung word, counted in the band's own beats (Beat This beats
between the two moments, at the octave nearest the transcript's tempo), against
the beats the reference gives that slide's segment. Also, within a line:
beats between the slide's first and last matched words against the sheet's
beats between the same two words. If the band adds beats at line ends (held
last notes, an extra bar), the first ratio exceeds 1 while the second stays 1."""
import json
import numpy as np
import cv, gt, segref, run_joint as rj, follow as fl
import diag_anticipation as da

for key in ("wabn", "rl", "komh", "isa"):
    song = cv.Song(key)
    cues = gt.build(song.s, song.slides, cv.S / key, float(song.F["duration"]))
    g = gt.song_rate(cues)
    b = np.array(json.loads((cv.S / f"beats_{key}.json").read_text())["beats"])
    tb = 1 / np.median(np.diff(b))
    oct_ = 2 ** np.round(np.log2(tb / g))  # tracker beats per quarter note
    beats_between = lambda t0, t1: np.sum((b >= t0) & (b < t1)) / oct_
    ref = segref.SegmentReference(song.s, song.slides, song.spans, sequences=[rj.roadmap_sequence(song)])
    seglen = {sl: np.sum(np.asarray(ref.slide_of) == sl) * fl.CELL for sl in set(ref.slide_of)}
    fw = [x for x in da.first_word_times(song, cues) if x is not None]
    whole, inner = [], []
    for (t0, tf, sl, _), nxt in zip(fw, fw[1:]):
        if sl in seglen and 0 < nxt[1] - tf < 40:
            whole.append(beats_between(tf, nxt[1]) / seglen[sl])
    for c in cues:
        if c.span is not None and len(c.anchors) >= 4:
            (ta, pa), (tz, pz) = c.anchors[0], c.anchors[-1]
            if pz - pa >= 4:
                inner.append(beats_between(ta, tz) / (pz - pa))
    q = lambda x: f"median {np.median(x):.2f} (IQR {np.percentile(x,25):.2f}..{np.percentile(x,75):.2f})"
    print(f"{key:5s} pulse {tb * 60 / oct_:.0f} q-bpm vs transcript {g * 60:.0f} | slide-to-slide band beats / reference beats {q(whole)}"
          f" | within a line, band beats / sheet beats {q(inner)}")
