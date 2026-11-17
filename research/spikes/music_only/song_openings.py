"""Spike: "listen for the other songs' opening lines" as a fast song-change proposal.

While a song is on screen, a slower path scores the *other* setlist songs'
entry spans (Whisper teacher-forced, `MLXSpanScorer`) on the trailing 6 s.
If another song's line is heard clearly — and better than anything of the
current song — that is fast evidence of a song change.

Reads the score cache from song_openings_cache.py (every span of every song,
every 2 s, on every recording) and replays proposal rules offline:

* current song = what the replay has switched to (starts at the golden first
  song); a proposal switches it, so false proposals cascade as they would live;
* a window ending at t is usable at t + ARRIVE (compute + queue);
* the slow path runs every CADENCE s (a multiple of the cache's 2 s step);
* multi-song recordings: delay from each golden song change to the first
  proposal of the new song, vs production's live switch (trace `enter` rows);
  false = a proposal of a song that is not the golden song (nor the next
  golden song within LEAD s: bands start before the operator's cue);
* single-song recordings: every proposal is false (decoys = every other song
  we have; their own setlist-mate, if any, too).

    research/spikes/music_only/py.sh research/spikes/music_only/song_openings.py [sweep|best|heard KEY T0 T1]
"""
from __future__ import annotations

import itertools
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import multi_replay as mr  # noqa: E402
from auto_presenter.eval.replay import load_recording  # noqa: E402

D = Path(__file__).resolve().parents[3] / "build" / "openings"
STEP = 2.0
ARRIVE = 0.5
LEAD = 15.0
MULTI = ["set", "medley"]
SINGLE = ["bml", "since", "wabn", "rl", "komh", "isa"]
TRACES = {"set": mr.CASES["set"][1], "medley": mr.CASES["medley"][1]}

_table = json.loads((D / "spans.json").read_text())
SONGS: dict[str, str] = _table["songs"]
_base = np.load(D / "baseline.npz")
TEXTS = list(_base["texts"])
TI = {t: i for i, t in enumerate(TEXTS)}
PRIOR = np.maximum(_base["silence"], _base["noise"]).astype(np.float32)


for _sid, _name in {"song_pp_a984506dd93b722e": "Build My Life",
                     "song_01m3z9rq3fpzdqm8gbqxvc3824": "Awesome God",
                     "song_pp_008c6ddf49fea80b": "What A Beautiful Name", "song_pp_7b0e4239689e710b": "Reckless Love",
                     "song_pp_86fc55d6249c2172": "King Of My Heart", "song_pp_7c62a4ff2ebb0782": "I Surrender All",
                     "song_pp_e51037987060ff08": "Since Your Love"}.items():
    SONGS[_sid] = _name


def span_cols(song: str, entry_words: int | None, min_words: int = 1) -> np.ndarray:
    """Columns (text indices) of a song's spans; `entry_words`: only spans
    ending within the first N words of a slide (its opening line or two);
    `min_words`: drop shorter spans (a slide's first word or two alone)."""
    cols = set()
    for r in _table["spans"]:
        if r["song"] != song or len(r["text"].split()) < min_words:
            continue
        if entry_words is None or any(j < entry_words for _si, j in r["at"]):
            cols.add(TI[r["text"]])
    return np.array(sorted(cols))


@dataclass
class Rec:
    key: str
    t: np.ndarray  # window end times
    S: np.ndarray  # T x texts, float32
    items: dict[int, str]  # item_idx -> song_id
    gt: list[tuple[float, str]]  # (time, song) golden song changes (first = start)
    prod: list[tuple[float, str]]  # production's live switches
    dur: float


def load(key: str) -> Rec:
    z = np.load(D / f"{key}.npz")
    assert list(z["texts"]) == TEXTS
    rec = load_recording(mr.CASES[key][0])
    items = {x.item_idx: x.song_id for x in rec.setlist_content}
    gt_ev = sorted(rec.ground_truth, key=lambda g: g.t_seconds)
    gt: list[tuple[float, str]] = []
    for g in gt_ev:
        if not gt or items[g.item_idx] != gt[-1][1]:
            gt.append((0.0 if not gt else g.t_seconds, items[g.item_idx]))
    prod = []
    if key in TRACES:
        for line in open(TRACES[key]):
            r = json.loads(line)
            if r["e"] == "enter":
                prod.append((r["t"], items[r["item"]]))
    return Rec(key, z["t"], z["scores"].astype(np.float32), items, gt, prod, float(z["t"][-1]))


def golden_at(rec: Rec, t: float) -> str:
    cur = rec.gt[0][1]
    for tt, s in rec.gt:
        if tt <= t:
            cur = s
    return cur


@dataclass(frozen=True)
class Rule:
    entry_words: int | None = 12  # candidate spans: first N words of each slide (None = all)
    pmi: bool = True  # subtract the span's score on silence/noise (LM prior)
    thresh: float = -1.2  # candidate's best (raw score) must be at least this
    pmi_min: float = 2.0  # ... and beat its own prior by this (when pmi)
    margin: float = 0.5  # ... and beat the current song's best span (all spans) by this
    hits: int = 2  # in this many of the last `of` slow-path windows
    of: int = 2
    cadence: float = 4.0
    pool: str = "setlist"  # candidates: the recording's setlist ("setlist") or every song ("all")
    min_words: int = 4  # candidate and current-song spans of at least this many words
    compare: str = "raw"  # margin over the current song's best: "raw" score or "gain" over prior
    phase: int = 0  # which of the cached 2 s windows the 4 s cadence starts on


_cols_cache: dict = {}


def cols(song, entry, min_words=1):
    k = (song, entry, min_words)
    if k not in _cols_cache:
        _cols_cache[k] = span_cols(song, entry, min_words)
    return _cols_cache[k]


def candidates(rec: Rec, rule: Rule) -> list[str]:
    own = list(dict.fromkeys(rec.items.values()))
    if rule.pool == "setlist" and len(own) > 1:
        return own
    # single-song recording: every song we have is a decoy (same song id excluded)
    return own + [s for s in SONGS if s not in own]


def replay(rec: Rec, rule: Rule, verbose: bool = False):
    """Proposals [(arrival time, song, evidence)] under `rule`."""
    step = float(rec.t[1] - rec.t[0])
    every = max(1, int(round(rule.cadence / step)))
    cand = candidates(rec, rule)
    cur = rec.gt[0][1]
    hist: dict[str, list[bool]] = {s: [] for s in cand}
    props = []
    for i in range(rule.phase % every, len(rec.t), every):
        row = rec.S[i]
        cc = cols(cur, None, rule.min_words)
        # the current song's best: raw score, or gain over the LM prior (`compare`)
        cur_best = row[cc].max() if rule.compare == "raw" else (row[cc] - PRIOR[cc]).max()
        for s in cand:
            if s == cur:
                hist[s].append(False)
                continue
            c = cols(s, rule.entry_words, rule.min_words)
            c = c[~np.isnan(row[c])]  # decoys' later spans aren't cached
            raw = row[c]
            gain = raw - PRIOR[c]
            ok_v = (raw >= rule.thresh) & ((raw if rule.compare == "raw" else gain) - cur_best >= rule.margin)
            if rule.pmi:
                ok_v &= gain >= rule.pmi_min
            j = int(np.argmax(np.where(ok_v, raw, -np.inf))) if ok_v.any() else int(np.argmax(raw))
            best = raw[j]
            hist[s].append(bool(ok_v.any()))
            if sum(hist[s][-rule.of:]) >= rule.hits:
                t_arr = float(rec.t[i]) + ARRIVE
                props.append((t_arr, s, TEXTS[c[j]], float(best), float(cur_best)))
                if verbose:
                    print(f"    {t_arr:6.1f}s  -> {SONGS[s][:22]:22s} heard '{TEXTS[c[j]]}' {best:.2f} (cur {cur_best:.2f}, prior {PRIOR[c][j]:.2f})")
                cur = s
                for k in hist:
                    hist[k] = []
                break
    return props


def judge(rec: Rec, props):
    """(per-change delays, false proposals): a proposal of song X is right if X is
    the golden song at that moment or becomes it within LEAD s."""
    false = []
    for t, s, *_ in props:
        if golden_at(rec, t) == s or golden_at(rec, t + LEAD) == s:
            continue
        false.append((t, s))
    delays = []
    for ci in range(1, len(rec.gt)):
        c, s = rec.gt[ci]
        nxt = rec.gt[ci + 1][0] if ci + 1 < len(rec.gt) else rec.dur + 10
        hit = [t for t, ss, *_ in props if ss == s and c - LEAD <= t < nxt]
        p = [t for t, ss in rec.prod if ss == s and c - LEAD <= t < nxt]
        delays.append((c, s, hit[0] - c if hit else None, p[0] - c if p else None, nxt - c))
    return delays, false


def evaluate(rule: Rule, recs: dict[str, Rec], verbose=False):
    out = {"delays": [], "false_multi": 0, "false_single": 0, "single_s": 0.0, "multi_s": 0.0}
    for key, rec in recs.items():
        if verbose:
            print(f"  [{key}]")
        props = replay(rec, rule, verbose)
        delays, false = judge(rec, props)
        if key in MULTI:
            out["delays"] += [(key, *d) for d in delays]
            out["false_multi"] += len(false)
            out["multi_s"] += rec.dur
        else:
            out["false_single"] += len(false)
            out["single_s"] += rec.dur
    return out


def summary(rule, o):
    ds = [d[3] for d in o["delays"]]
    found = [d for d in ds if d is not None]
    # a miss counts as the time until the song ended (it was wrong all along)
    cost = sum(d if d is not None else dd[5] for d, dd in zip(ds, o["delays"]))
    fph = o["false_single"] / max(o["single_s"] / 3600, 1e-9)
    return (len(found), len(ds), cost, fph, o["false_multi"])


def sweep(recs):
    res = []
    # raw-score comparison (the first idea) and gain-over-prior comparison (PMI)
    raw = itertools.product([12], ["raw"], [-2.0, -1.5, -1.2, -1.0], [1.0, 2.0, 3.0], [0.0, 0.5, 1.0],
                            [(1, 1), (2, 2), (2, 3)], [4.0, 6.0])
    gain = itertools.product([12, None], ["gain"], [-6.0, -4.0, -3.0, -2.0], [2.0, 2.5, 3.0, 3.5, 4.0],
                             [0.5, 1.0, 1.5, 2.0, 2.5], [(1, 1), (2, 2), (2, 3)], [4.0, 6.0])
    for ew, cmp_, th, pm, mg, (h, of), cad in itertools.chain(raw, gain):
        rule = Rule(entry_words=ew, compare=cmp_, thresh=th, pmi_min=pm, margin=mg, hits=h, of=of, cadence=cad)
        res.append((rule, summary(rule, evaluate(rule, recs))))
    return res


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "sweep"
    if mode == "cost":  # spans per slow-path pass: other songs' candidates + the current song's own
        for key in MULTI:
            rec = load(key)
            own = list(dict.fromkeys(rec.items.values()))
            for ew in (12, None):
                for cur in own:
                    n_c = sum(len(cols(s, ew, 4)) for s in own if s != cur)
                    print(f"{key:7s} entry_words={ew}  on {SONGS[cur][:22]:22s}: {n_c:4d} candidate spans"
                          f" + {len(cols(cur, None, 4)):3d} current-song spans")
        return 0
    if mode == "trace":  # each setlist song's best span per window: raw score / gain over prior
        key, t0, t1 = sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
        rec = load(key)
        own = list(dict.fromkeys(rec.items.values()))
        print("        " + "".join(f"{SONGS[s][:24]:>36s}" for s in own))
        for i in np.where((rec.t >= t0) & (rec.t <= t1))[0]:
            row = rec.S[i]
            cells = []
            for s in own:
                c = cols(s, None, 4)
                j = int(np.argmax(row[c]))
                cells.append(f"{row[c][j]:5.2f}/{row[c][j] - PRIOR[c][j]:+4.1f} {TEXTS[c[j]][:22]:22s}")
            print(f"{rec.t[i]:5.0f} {SONGS[golden_at(rec, rec.t[i])][:4]} " + " ".join(cells))
        return 0
    if mode == "heard":
        key, t0, t1 = sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
        rec = load(key)
        own = list(dict.fromkeys(rec.items.values()))
        for i in np.where((rec.t >= t0) & (rec.t <= t1))[0]:
            row = rec.S[i]
            g = golden_at(rec, rec.t[i])
            parts = []
            for s in own + [s for s in SONGS if s not in own]:
                c = cols(s, None, 4)
                c = c[~np.isnan(row[c])]
                if not len(c):
                    continue
                j = int(np.argmax(row[c]))
                parts.append((row[c][j], SONGS[s][:10], TEXTS[c[j]], row[c][j] - PRIOR[c][j]))
            parts.sort(reverse=True)
            print(f"{rec.t[i]:6.0f} gold={SONGS[g][:10]:10s} " + " | ".join(f"{p[1]} {p[0]:.2f}(+{p[3]:.1f}) '{p[2][:28]}'" for p in parts[:3]))
        return 0
    recs = {k: load(k) for k in MULTI + SINGLE if (D / f"{k}.npz").exists()}
    print("recordings:", list(recs), " golden changes:", {k: [(round(t), SONGS[s][:12]) for t, s in r.gt] for k, r in recs.items() if k in MULTI})
    if mode == "loro":  # leave one recording out: pick the rule on the rest, judge it on the one
        rules = [r for r, _ in sweep({k: recs[k] for k in ["set"]})]  # just the grid
        per = {}
        for r in rules:
            for k, rec in recs.items():
                per[(r, k)] = judge(rec, replay(rec, r))

        def loss(r, keys):
            # a miss costs its song's length, a false proposal 120 s of wrong song
            tot = 0.0
            for k in keys:
                delays, false = per[(r, k)]
                tot += sum(max(d[2], 0) if d[2] is not None else d[4] for d in delays) + 120 * len(false)
            return tot

        for k in recs:
            rest = [x for x in recs if x != k]
            best = min(rules, key=lambda r: loss(r, rest))
            delays, false = per[(best, k)]
            f = lambda x: "never" if x is None else f"{x:+.1f}"
            print(f"held out {k:7s}: false {len(false)}  delays " + ", ".join(f(d[2]) for d in delays)
                  + f"   <- {best.compare} pmi_min={best.pmi_min} margin={best.margin} hits={best.hits}/{best.of} cad={best.cadence} ew={best.entry_words} th={best.thresh}")
        return 0
    if mode == "sweep":
        res = sweep(recs)
        # Pareto: fewer false/h, then lower total delay cost
        res.sort(key=lambda x: (x[1][3], x[1][2]))
        front, best_cost = [], float("inf")
        for rule, s in res:
            if s[2] < best_cost:
                front.append((rule, s))
                best_cost = s[2]
        for rule, s in front:
            print(f"found {s[0]}/{s[1]}  delay-cost {s[2]:6.0f}s  false/h single {s[3]:5.1f}  false multi {s[4]}  {rule}")
        # robustness: how wide is the good region, per knob
        good = [(r, s) for r, s in res if s[0] == s[1] and s[3] == 0 and s[4] <= 1 and s[2] <= 60]
        print(f"\n{len(good)} of {len(res)} rules find 7/7 within 60 s total, 0 false/h single, <=1 false multi")
        for knob in ("entry_words", "compare", "thresh", "pmi_min", "margin", "hits", "cadence"):
            vals = sorted({str(getattr(r, knob)) for r, _ in res})
            print(f"  {knob:12s} " + "  ".join(f"{v}:{sum(str(getattr(r, knob)) == v for r, _ in good)}" for v in vals))
        for c in ("raw", "gain"):
            sub = [(r, s) for r, s in res if r.compare == c]
            b = min(sub, key=lambda x: (x[1][3] + 10 * x[1][4]) * 30 + x[1][2] + 300 * (x[1][1] - x[1][0]))
            print(f"  best {c}: found {b[1][0]}/{b[1][1]} cost {b[1][2]:.0f}s false/h {b[1][3]:.1f} multi {b[1][4]}  {b[0]}")
    else:
        rule = eval(f"Rule({sys.argv[2] if len(sys.argv) > 2 else ''})")
        o = evaluate(rule, recs, verbose=True)
        for key, c, s, d, p, n in o["delays"]:
            f = lambda x: "never" if x is None else f"{x:+.1f}s"
            print(f"  {key:7s} change at {c:6.1f}s -> {SONGS[s][:24]:24s} openings {f(d):>7s}   production {f(p):>7s}   (song lasts {n:.0f}s)")
        print(summary(rule, o))
    return 0


if __name__ == "__main__":
    sys.exit(main())
