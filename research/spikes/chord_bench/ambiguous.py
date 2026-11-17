"""The steelman: do chords help where LYRICS structurally cannot?

Words beat chords at localisation outright (see localise.py), but that is not
the case the proposal was aimed at. The app's known worst case is repeated
identical text -- a chorus sung four times offers no lyric evidence for which
repeat it is, and the tracker spends 27-41% of a repeat-heavy song lost.

So: restrict to chart positions whose LYRIC window is ambiguous song-wide, and
ask whether the CHORD window at the same position is any better. Both measured
on the chart alone, so this is the information available in principle, before
any question of detection quality.
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from spike_chord_charts import GOLDEN_MAP, find_sheet, parse_chart, chord_stream

import re


def norm(w):
    return re.sub(r"[^a-z']", "", w.lower())


print("Positions where a k-word window repeats elsewhere in the song, and what")
print("the chord window at that same position does. Candidates: 1 = unique.\n")
print(f"{'song':24s} {'k':>2s} {'lyric-ambig':>12s} {'chords also':>12s} "
       f"{'chords resolve':>15s}")
print("-" * 72)
tot_amb, tot_res, tot_n = 0, 0, 0
for name, needles in GOLDEN_MAP and [(n, nd) for _, n, nd in GOLDEN_MAP]:
    p = find_sheet(*needles)
    if not p:
        continue
    chart = parse_chart(p)
    if chart.key is None:
        continue
    st = chord_stream(chart)
    if len(st) < 12:
        continue
    # Word stream, and the chord in force at each word.
    wseq, cseq = [], []
    key = chart.key
    for ln in chart.lines:
        placed = ln.placed(key)
        bycol = {d["word_index"]: d["nashville"] for d in placed if d["nashville"]}
        cur = None
        for wi, w in enumerate([x for x in ln.lyric.split() if norm(x)]):
            cur = bycol.get(wi, cur)
            wseq.append(norm(w))
            cseq.append(cur)
    if len(wseq) < 20:
        continue

    for k in (4, 6):
        amb, resolved, n = 0, 0, 0
        for i in range(len(wseq) - k + 1):
            wl = tuple(wseq[i:i + k])
            wc = sum(1 for j in range(len(wseq) - k + 1)
                     if tuple(wseq[j:j + k]) == wl)
            n += 1
            if wc <= 1:
                continue
            amb += 1
            cl = tuple(cseq[i:i + k])
            # Among the positions the lyrics cannot separate, do the chords?
            same = [j for j in range(len(wseq) - k + 1)
                    if tuple(wseq[j:j + k]) == wl]
            cc = sum(1 for j in same if tuple(cseq[j:j + k]) == cl)
            if cc == 1:
                resolved += 1
        if k == 4:
            tot_amb += amb; tot_res += resolved; tot_n += n
        print(f"{name[:24]:24s} {k:2d} {f'{amb}/{n}':>12s} "
              f"{f'{amb - resolved}':>12s} {(resolved / amb if amb else 0):14.0%}")
print("-" * 72)
print(f"POOLED (k=4): {tot_amb}/{tot_n} positions are lyric-ambiguous; "
      f"chords resolve {tot_res / max(tot_amb, 1):.0%} of them")
