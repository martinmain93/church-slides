"""Remaining errors in three buckets: human-like, lag, and neither.

    uv run --project sidecar python research/spikes/music_only/error_buckets.py [names]

Uses the latest live traces (recorded with the current engine: opening-line
song changes, evidence-proposed jumps) and replays each recording twice:
as live, and with zero lag (every word, scored span and chord known the moment
its audio happened). Every 0.1 s that `wrong_slide_rate` counts as wrong
(same exclusions: blank cues, post-deviation grace) is put in one bucket:

1 human     a person operating would do the same: the line being sung is on
            our screen anyway (a line on two slides; within 1 s of the band:
            operators vary that much), or nothing is being sung and we show
            the slide just before or after the operator's
2 lag       right in the zero-lag replay (it's the lag); for the wrong song,
            within SONG_LAG_S after the band changed song (the opening-line
            watch's own delay, which the replay can't remove)
3 neither   everything else, split by what the screen showed

What is being sung when comes from the offline transcript placed in the
lyrics (`eval.visible`), so where the words can't be placed (garbled singing)
a moment counts as "nothing placed": treated like an instrumental.
"""
from __future__ import annotations

import collections
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import multi_replay as mr  # noqa: E402
import music_replay as mrp  # noqa: E402
from auto_presenter.eval import visible as vis  # noqa: E402
from auto_presenter.eval.metrics import (  # noqa: E402
    DEFAULT_DEVIATION_GRACE_S,
    blank_cue_windows,
    derive_deviation_markers,
    identical_slide_groups,
)

HERE = Path(__file__).resolve().parent
STEP = 0.1
SONG_LAG_S = 10.0
JITTER_S = 1.0
LATEST = mr.SPK / "holdout"
TRACE = {
    "wabn": LATEST / "traces_single/wabn_on.jsonl", "rl": LATEST / "traces_single/rl_on.jsonl",
    "komh": LATEST / "traces_single/komh_on.jsonl", "isa": LATEST / "traces_single/isa_on.jsonl",
    "since": LATEST / "traces_single/rec_20260818-214054_974_on.jsonl",
    "bml": LATEST / "traces_single/rec_20261006-225539_315_on.jsonl",
    "medley": LATEST / "traces_multi4/rec_20260903-115044_425_on.jsonl",
    "set": LATEST / "traces_multi4/rec_20261002-185523_091_on.jsonl",
}


def transcript(name):
    f = mr.CASES[name][0] / "transcript.json"
    if not f.exists():
        f = HERE / "transcripts" / f"{name}.json"
    return [(w["start"], w["end"], w["word"]) for w in json.loads(Path(f).read_text())["words"]]


CLEAR_SCORE = float(__import__("os").environ.get("CLEAR_SCORE", "-2.5"))


def _owners(rec):
    from auto_presenter.follower.scorer import spans_for

    out = {}
    for x in rec.setlist_content:
        owner = spans_for(x.slides)
        out[x.item_idx] = (owner, sorted(set(owner.values())))
    return out


def _scored_rows(name):
    return [json.loads(line) for line in open(TRACE[name]) if '"e":"scored"' in line]


def shown_at(dec):
    def at(t):
        cur = None
        for d in dec:
            if d.t_seconds > t:
                break
            cur = (d.item_idx, d.slide_idx)
        return cur
    return at


def buckets(name):
    rdir, _old, sheets = mr.CASES[name]
    mr.CASES[name] = (rdir, TRACE[name], sheets)  # the latest live run
    arm = mrp.env_arm()  # $ARM, e.g. "col=0.1"; the shipped follower when unset
    decide = mrp.decisions
    if os.environ.get("DECIDE"):
        # another replay with the same `decisions(name, arm)` (e.g.
        # DECIDE=tune_share_replay: a reference learned during the service)
        import importlib

        decide = importlib.import_module(os.environ["DECIDE"]).decisions
    elif os.environ.get("REF"):
        # another reference (`nosheet_replay` modes, e.g. REF=libspec: the
        # library slides alone, what a song without a lead sheet runs on)
        import nosheet_replay as ns

        ns.MODE = os.environ["REF"]
        decide = ns.decisions
    live, rec = decide(name, arm)
    zero, _ = decide(name, {**arm, "zl": 1})
    gt = sorted(rec.ground_truth, key=lambda g: g.t_seconds)
    equiv = identical_slide_groups(rec.setlist_content)
    excused = list(blank_cue_windows(gt, rec.setlist_content))
    excused += [(m.t_seconds, m.t_seconds + DEFAULT_DEVIATION_GRACE_S) for m in derive_deviation_markers(gt)]
    pl = vis.place_sung_words(transcript(name), rec.setlist_content)
    sung = {round(t / STEP): set(homes) for t, homes in vis.sung_samples(
        pl, gt[0].t_seconds, gt[-1].t_seconds, step=STEP, tolerance_s=JITTER_S, bridge_s=2.0)}
    # Also what the live scorer heard clearly: where the transcript is garbled
    # it is the better witness. A clear step's best span (CLEAR_SCORE or better,
    # MIN_WORDS+ words) was sung over the 2 s before its placed time, on every
    # slide carrying it.
    from auto_presenter.follower import score as fsc

    slide_words = {x.item_idx: [[fsc.norm(w) for w in sl if fsc.norm(w)] for sl in x.slides]
                   for x in rec.setlist_content}
    for item, (owner, spans) in _owners(rec).items():
        for row in _scored_rows(name):
            if row.get("item") != item:
                continue
            sc = row["scores"]
            b = max(range(len(sc)), key=sc.__getitem__)
            text = spans[b]
            if sc[b] < CLEAR_SCORE or len(text.split()) < 4:
                continue
            # every slide whose words contain the heard words, anywhere
            homes = {(item, si) for si, words in enumerate(slide_words[item])
                     if f" {text} " in f" {' '.join(words)} "}
            for k in range(round((row["t"] - 2.0 - JITTER_S) / STEP), round((row["t"] + JITTER_S) / STEP) + 1):
                sung.setdefault(k, set()).update(homes)
    sung_of = {x.item_idx: [k for k, sl in enumerate(x.slides) if sl] for x in rec.setlist_content}

    def written(g, step):
        """The sung slide before (-1) or after (+1) band slide `g` in the song's written order."""
        order = sung_of.get(g[0], [])
        if g[1] not in order:
            return None
        k = order.index(g[1]) + step
        return order[k] if 0 <= k < len(order) else None

    def anticipated(d, band_prev, screen_prev) -> bool:
        for ref in (band_prev, screen_prev):
            if ref is None or ref[0] != d[0]:
                continue
            for step in (-1, 0, +1):
                w = written(ref, step) if step else ref[1]
                if w is not None and same((ref[0], w), d):
                    return True
        return False

    same = lambda g, d: d is not None and (g == d or (d[0] == g[0] and d[1] in equiv.get(g, frozenset())))  # noqa: E731
    at_live, at_zero = shown_at(live), shown_at(zero)

    def before_live(t):
        """What the screen showed before its current slide."""
        cur = prev_ = None
        for x in live:
            if x.t_seconds > t:
                break
            if (x.item_idx, x.slide_idx) != cur:
                prev_, cur = cur, (x.item_idx, x.slide_idx)
        return prev_
    song_changes = [g.t_seconds for i, g in enumerate(gt) if i and g.item_idx != gt[i - 1].item_idx]
    out = collections.Counter()
    marks = []  # (t, label) of every wrong 0.1 s, for episode listing
    gi = 0
    t = gt[0].t_seconds
    while t < gt[-1].t_seconds:
        while gi + 1 < len(gt) and gt[gi + 1].t_seconds <= t:
            gi += 1
        g = (gt[gi].item_idx, gt[gi].slide_idx)
        d = at_live(t)
        if same(g, d) or any(a <= t < b for a, b in excused):
            t += STEP
            continue
        homes = sung.get(round(t / STEP))
        prev = (gt[gi - 1].item_idx, gt[gi - 1].slide_idx) if gi else None
        nxt = (gt[gi + 1].item_idx, gt[gi + 1].slide_idx) if gi + 1 < len(gt) else None
        near_change = any(abs(t - x.t_seconds) <= JITTER_S for x in gt)
        if d is None or d[0] != g[0]:
            if any(0 <= t - c <= SONG_LAG_S for c in song_changes):
                label = "2 lag: song change"
            elif same(g, at_zero(t)):
                label = "2 lag"
            else:
                label = "3 wrong song"
        elif homes is not None and d in homes:
            label = "1 human: line on screen anyway"
        elif same(g, at_zero(t)):
            label = "2 lag"
        elif homes is None and ((prev is not None and same(prev, d)) or (nxt is not None and same(nxt, d))):
            label = "1 human: instrumental, next/previous slide"
        elif near_change:
            label = "1 human: within 1 s of the band"
        elif (d[1] == 0) != (g[1] == 0):
            label = "3 blank vs words"
        elif prev is not None and same(prev, d):
            label = "3 late (beyond the lag)"
        elif nxt is not None and same(nxt, d):
            label = "3 early"
        else:
            label = "3 lost (elsewhere in the song)"
        out[label] += STEP
        # perceived: what the congregation sees, not just "wrong slide"
        if d is not None and d[0] == g[0] and d[1] == 0:
            kind = "blank"
        elif d is not None and d[0] == g[0] and homes is not None and d in homes:
            kind = "line on screen anyway"
        elif (prev is not None and same(prev, d)) or (d is not None and d[0] == g[0] and d[1] == written(g, -1)):
            kind = "stale (previous slide)"
        elif (nxt is not None and same(nxt, d)) or (d is not None and d[0] == g[0] and d[1] == written(g, +1)):
            # the next slide as written counts as early even when the band
            # did something else next (repeated the line, an instrumental)
            kind = "early (next slide)"
        elif d is not None and anticipated(d, prev, before_live(t)):
            # one written step from where the band just was, or from what the
            # screen showed before: following the song as written while the
            # band went elsewhere — a normal operator's mistake
            kind = "followed the written order"
        else:
            kind = "jump (anywhere else)"
        out["_p:" + kind] += STEP
        if kind.startswith("jump"):
            JUMPS.append((name, round(t, 1), d, g))
        marks.append((t, label, d, g))
        t += STEP
    from auto_presenter.eval.metrics import anticipation_error_ms
    out["_ant"] = anticipation_error_ms(live, rec.ground_truth)
    out["_flips"] = sum(1 for i in range(2, len(live)) if (live[i].item_idx, live[i].slide_idx)
                        == (live[i - 2].item_idx, live[i - 2].slide_idx) and live[i].t_seconds - live[i - 2].t_seconds <= 5.0)
    return out, gt[-1].t_seconds - gt[0].t_seconds, marks


def episodes3(marks):
    """Runs of bucket-3 time (gaps under 0.25 s joined): (start, length, label, screen, band)."""
    eps, cur = [], None
    for t, label, d, g in marks:
        if not label.startswith("3"):
            continue
        if cur and t - cur[1] <= 0.25:
            cur[1] = t + STEP
            cur[2][label] += STEP
        else:
            if cur:
                eps.append(cur)
            cur = [t, t + STEP, collections.Counter({label: STEP}), d, g]
    if cur:
        eps.append(cur)
    return [(a, b - a, c.most_common(1)[0][0], d, g) for a, b, c, d, g in eps]


#: Perceived cost per second of each kind of wrong screen: a jump to the
#: wrong place is five times worse than staying on a slide too long.
PERCEIVED = {"jump (anywhere else)": 5.0}
#: (recording, time, screen, band) of every random-jump 0.1 s, for listing.
JUMPS: list = []


def main() -> int:
    names = sys.argv[1:] or list(TRACE)
    total, span = collections.Counter(), 0.0
    longest, ant, flips = [], [], 0
    ptotal = collections.Counter()
    for name in names:
        b, dur, marks = buckets(name)
        ant += b.pop("_ant")
        flips += b.pop("_flips")
        kinds = {k[3:]: b.pop(k) for k in list(b) if k.startswith("_p:")}
        for k, v in kinds.items():
            ptotal[k] += v
        longest.extend((ln, name, round(a, 1), lab, d, g) for a, ln, lab, d, g in episodes3(marks) if ln >= 3)
        total.update(b)
        span += dur
        b1 = sum(v for k, v in b.items() if k.startswith("1"))
        b2 = sum(v for k, v in b.items() if k.startswith("2"))
        b3 = sum(v for k, v in b.items() if k.startswith("3"))
        print(f"{name:7s} {dur:5.0f} s | wrong {sum(b.values()) / dur:.3f} = human {b1 / dur:.3f} + lag {b2 / dur:.3f}"
              f" + neither {b3 / dur:.3f} | perceived {sum(PERCEIVED.get(k, 1.0) * v for k, v in kinds.items()) / dur:.3f}",
              flush=True)
    wrong = sum(total.values())
    import numpy as np
    a = np.asarray(ant, float)
    print(f"\nall {span:.0f} s: wrong {wrong / span:.3f}   slide changes vs band: median {np.median(a):+.0f} ms,"
          f" p25 {np.percentile(a, 25):+.0f}, p75 {np.percentile(a, 75):+.0f}; flicker {flips}")
    for bucket in ("1", "2", "3"):
        part = {k: v for k, v in total.items() if k.startswith(bucket)}
        print(f"  bucket {bucket}: {sum(part.values()):5.0f} s  {sum(part.values()) / span:.3f} of the time"
              f"  ({sum(part.values()) / wrong:.0%} of errors)")
        for k, v in sorted(part.items(), key=lambda x: -x[1]):
            print(f"      {k:44s} {v:5.0f} s  {v / span:.3f}")
    perceived = sum(PERCEIVED.get(k, 1.0) * v for k, v in ptotal.items())
    print(f"  perceived (a wrong jump x{PERCEIVED['jump (anywhere else)']:g}): {perceived / span:.3f}")
    for k, v in sorted(ptotal.items(), key=lambda x: -x[1]):
        print(f"      {k:44s} {v:5.0f} s  {v / span:.3f}  x{PERCEIVED.get(k, 1.0):g}")
    print("\nbucket-3 episodes of 3 s or more (screen vs band):")
    for ln, name, a, lab, d, g in sorted(longest, reverse=True)[:20]:
        print(f"  {ln:5.1f} s  {name:7s} at {a:6.1f}  {lab[2:]:28s} screen {d}  band {g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
