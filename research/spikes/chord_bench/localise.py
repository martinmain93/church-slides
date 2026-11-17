"""Head-to-head: how fast does each route localise position inside a known section?

The app already knows which slide is showing. The open question is how quickly
it can know WHERE IN THE LINE the band is, and whether harmony beats words at it.

Position is measured in chart-chord index, so both routes are scored in the same
units on the same sections.

  words   a word localises when the words heard so far are unique to one
          position; but the app only has a word once Whisper delivers it, so
          each word's usable time is its onset + the measured delivery lag
          (2.85 s median, 8.78 s p90 -- see measured_delivery_latency).
  chords  BTC's decoded chord stream, causal, matched against the section's
          chord sequence. Also run with ORACLE chords, to separate what the
          chart affords from what the detector delivers.

Reported as seconds from section start to the first correct unique localisation.
"""
import pickle
import sys
import os
import difflib
import json
import re

import numpy as np

SC = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from spike_chord_charts import (GOLDEN_MAP, MAJOR_DEGREE, FLAT_DEGREE, PITCH,  # noqa: E402
                                find_sheet, parse_chart)

REC = os.path.expanduser("~/Library/Application Support/com.churchslides.app/recordings")
per_rec = pickle.load(open(f"{SC}/per_rec.pkl", "rb"))
WHISPER_LAG = 2.85


def deg_name(deg, minor):
    n = MAJOR_DEGREE.get(deg) or FLAT_DEGREE.get(deg)
    return None if n is None else n + ("m" if minor else "")


def btc_to_deg(lab, rot):
    if lab in ("N", "X"):
        return None
    r = PITCH.get(lab.split(":")[0].upper())
    return None if r is None else deg_name((r - rot) % 12, lab.endswith(":min"))


def norm(w):
    return re.sub(r"[^a-z']", "", w.lower())


def time_to_unique(obs_seq, obs_times, truth_seq, true_pos):
    """First time the last-k observations are unique to the correct position."""
    for k in range(1, min(9, len(obs_seq)) + 1):
        win = tuple(obs_seq[:k])
        cands = [j for j in range(len(truth_seq) - k + 1)
                 if tuple(truth_seq[j:j + k]) == win]
        if len(cands) == 1 and cands[0] == true_pos:
            return obs_times[k - 1]
    return None


rows = []
for rid, (anchors, labels, times, rot, an, at) in per_rec.items():
    tj = json.load(open(f"{REC}/{rid}/transcript.json"))
    words = [(norm(w["word"]), w["start"]) for w in tj["words"] if norm(w["word"])]
    # Sections = runs of anchors between gaps > 6 s (a new sung phrase group).
    gaps = np.flatnonzero(np.diff(at) > 6.0) + 1
    groups = np.split(np.arange(len(at)), gaps)
    for g in groups:
        if len(g) < 4:
            continue
        seq = [an[i] for i in g]
        t = [at[i] for i in g]
        start = t[0]
        # ORACLE: the chart's own chords, arriving at their anchor times.
        o = time_to_unique(seq, t, seq, 0)
        # BTC: modal decoded chord in each anchor interval, causally.
        got = []
        for i in range(len(g)):
            a0 = t[i]
            a1 = t[i + 1] if i + 1 < len(t) else t[i] + 3.0
            m = (times >= a0) & (times < a1)
            seg = [btc_to_deg(labels[j], rot) for j in np.flatnonzero(m)]
            seg = [s for s in seg if s]
            got.append(max(set(seg), key=seg.count) if seg else None)
        b = time_to_unique(got, t, seq, 0)
        # WORDS: each word usable only after Whisper's delivery lag.
        wt = [wtm for _, wtm in words if t[0] <= wtm <= t[-1] + 2.0]
        ws = [w for w, wtm in words if t[0] <= wtm <= t[-1] + 2.0]
        wgt = [x + WHISPER_LAG for x in wt]
        w = time_to_unique(ws, wgt, ws, 0) if len(ws) > 2 else None
        rows.append(dict(rid=rid, start=start,
                         oracle=None if o is None else o - start,
                         btc=None if b is None else b - start,
                         words=None if w is None else w - start,
                         n=len(g)))

print(f"{len(rows)} sung phrase groups across {len(per_rec)} recordings\n")


def summarise(key, label):
    v = [r[key] for r in rows if r[key] is not None]
    got = len(v) / len(rows)
    if not v:
        print(f"{label:34s} never localises")
        return
    print(f"{label:34s} localises {got:5.0%} of groups  "
          f"median {np.median(v):5.2f}s  p90 {np.percentile(v, 90):5.2f}s")


summarise("oracle", "chart chords (oracle, upper bound)")
summarise("btc", "BTC chords (what we actually get)")
summarise("words", "words + 2.85 s Whisper lag")

both = [(r["btc"], r["words"]) for r in rows
        if r["btc"] is not None and r["words"] is not None]
if both:
    b = np.array([x for x, _ in both]); w = np.array([y for _, y in both])
    print(f"\nhead to head on the {len(both)} groups both localise:")
    print(f"  BTC   median {np.median(b):5.2f}s")
    print(f"  words median {np.median(w):5.2f}s")
    print(f"  chords faster on {np.mean(b < w):.0%} of groups, "
          f"median saving {np.median(w - b):+.2f}s")
