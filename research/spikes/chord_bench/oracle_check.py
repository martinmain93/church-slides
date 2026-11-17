"""Is my 'oracle' actually an oracle?

It underperformed BTC, which is impossible for a genuine upper bound, so it is
not one. The reference chords per slide (ref) and the anchor-derived chord
stream come from two different difflib alignments of the same chart, so they
can disagree about which chord covers which word.
"""
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fusion2 import build, GOLDEN_MAP

recs = [r for r in (build(rid, n, nd) for rid, n, nd in GOLDEN_MAP) if r]
print(f"{'recording':26s} {'words in cue':>13s} {'both defined':>13s} "
      f"{'oracle==ref[truth]':>19s}")
print("-" * 78)
tot = []
for r in recs:
    cues = r["cues"]
    ok, both, n = 0, 0, 0
    ci = 0
    for w, ts, btc, orc in r["words"]:
        while ci + 1 < len(cues) and cues[ci + 1]["t_seconds"] <= ts:
            ci += 1
        truth = cues[ci]["slide_idx"]
        if truth not in r["ref"]:
            continue
        n += 1
        want = r["ref"][truth].get(w)
        if want and orc:
            both += 1
            ok += (want == orc)
    tot.append(ok / max(both, 1))
    print(f"{r['rid']:26s} {n:13d} {both / max(n, 1):12.0%} "
          f"{ok / max(both, 1):18.0%}")
print("-" * 78)
print(f"mean agreement between the two chart-derived views: {np.mean(tot):.0%}")
print("\nA true oracle would read 100%. Anything less means the arm labelled")
print("'oracle' is a second noisy estimate, not a ceiling.")
