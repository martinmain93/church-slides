"""How strict can the opening-line song change get before it misses real changes?

The 2026-10-15 live set made four wrong mid-song jumps, each from one 4-word
line ("the son of god", "holy holy holy all") heard once at gain ~3 while the
current song still scored 1.1-2.0. The rule tuned in song_openings.py was the
loosest in its good region. This replays the production rule and stricter
ones over the same cache (build/openings, every span of every song every 2 s
on the 8 tuning recordings), adding the one knob that rule lacks: an upper
bound on how well the *current* song is still being heard.

    research/spikes/music_only/py.sh research/spikes/music_only/openings_strict.py
"""
from __future__ import annotations

import itertools
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import song_openings as so  # noqa: E402


@dataclass(frozen=True)
class Strict:
    pmi_min: float = 2.5
    margin: float = 1.0
    hits: int = 1
    of: int = 1
    min_words: int = 4
    #: the current song's best gain must be at most this (None: no bound)
    cur_max: float | None = None
    entry_words: int = 6
    cadence: float = 2.0


def replay(rec: so.Rec, rule: Strict):
    step = float(rec.t[1] - rec.t[0])
    every = max(1, int(round(rule.cadence / step)))
    cand = so.candidates(rec, so.Rule())
    cur = rec.gt[0][1]
    hist: dict[str, list[bool]] = {s: [] for s in cand}
    props = []
    for i in range(0, len(rec.t), every):
        row = rec.S[i]
        cc = so.cols(cur, None, 4)  # the current song: all its 4+ word spans, as in production
        cur_best = float((row[cc] - so.PRIOR[cc]).max())
        for s in cand:
            if s == cur:
                hist[s].append(False)
                continue
            c = so.cols(s, rule.entry_words, rule.min_words)
            c = c[~np.isnan(row[c])]
            gain = row[c] - so.PRIOR[c]
            ok = (gain >= rule.pmi_min) & (gain - cur_best >= rule.margin)
            if rule.cur_max is not None:
                ok &= cur_best <= rule.cur_max
            hist[s].append(bool(ok.any()))
            if sum(hist[s][-rule.of:]) >= rule.hits:
                props.append((float(rec.t[i]) + so.ARRIVE, s))
                cur = s
                for k in hist:
                    hist[k] = []
                break
    return props


def score(rule: Strict, recs):
    found = total = false_multi = false_single = 0
    delays = []
    single_s = 0.0
    for key, rec in recs.items():
        props = replay(rec, rule)
        d, false = so.judge(rec, props)
        if key in so.MULTI:
            false_multi += len(false)
            for c in d:
                total += 1
                if c[2] is not None:
                    found += 1
                    delays.append(c[2])
        else:
            false_single += len(false)
            single_s += rec.dur
    return found, total, false_multi, false_single / max(single_s / 3600, 1e-9), delays


def main() -> int:
    recs = {k: so.load(k) for k in so.MULTI + so.SINGLE if (so.D / f"{k}.npz").exists()}
    prod = Strict()
    rules = [prod] + [
        Strict(pmi_min=g, margin=m, hits=h, of=o, min_words=w, cur_max=cm)
        for g, m, (h, o), w, cm in itertools.product(
            [2.5, 3.0, 3.5], [1.0, 1.5, 2.0], [(1, 1), (2, 2), (2, 3), (3, 3)], [4, 5, 6],
            [None, 1.5, 1.0, 0.5],
        )
    ]
    rows = []
    for r in rules:
        f, t, fm, fs, d = score(r, recs)
        rows.append((r, f, t, fm, fs, max(d) if d else float("nan"), float(np.median(d)) if d else float("nan")))
    print(f"production: found {rows[0][1]}/{rows[0][2]}  false multi {rows[0][3]}  false/h single {rows[0][4]:.1f}  "
          f"delay median {rows[0][6]:+.1f} max {rows[0][5]:+.1f} s")
    keep = [x for x in rows[1:] if x[1] == x[2] and x[3] == 0 and x[4] == 0]
    print(f"\n{len(keep)} stricter rules still find {rows[0][2]}/{rows[0][2]} with 0 false; slowest-delay first-ten by strictness:")
    # strictness: more hits, higher gain, more words, tighter current bound
    def strict_key(x):
        r = x[0]
        return (-r.hits, -r.pmi_min, -r.min_words, r.cur_max if r.cur_max is not None else 9, -r.margin)
    for r, f, t, fm, fs, dmax, dmed in sorted(keep, key=strict_key)[:25]:
        print(f"  hits {r.hits}/{r.of}  gain>={r.pmi_min}  margin {r.margin}  words>={r.min_words}  "
              f"cur<={r.cur_max}   delay median {dmed:+.1f} max {dmax:+.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
