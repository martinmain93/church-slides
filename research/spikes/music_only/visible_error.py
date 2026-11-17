"""Visible error: is the line being sung on our screen? (vs wrong_slide_rate)

    python research/spikes/music_only/visible_error.py [check|score|audit|anatomy] [names]

  check    placement coverage per recording, and the golden-cue-to-first-sung-word
           offset (catches clock offsets between transcript and golden)
  score    wrong_slide_rate vs visible error, follower replay + production ('off')
  audit    golden moments where the operator's slide does not carry the singing
  anatomy  error_anatomy's classes, split by whether the sung line was visible

Word timing is the recording's offline Whisper transcript (transcript.json,
small.en, VAD off — the one bootstrap_golden used); 'bml' has none, so
`transcripts/bml.json` was made the same way (bootstrap_golden.transcribe).
See `auto_presenter.eval.visible` for the metric.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import error_anatomy as ea  # noqa: E402
import multi_replay as mr  # noqa: E402
import music_replay as mrp  # noqa: E402
from auto_presenter.eval import visible as vis  # noqa: E402
from auto_presenter.eval.metrics import (  # noqa: E402
    DEFAULT_DEVIATION_GRACE_S,
    derive_deviation_markers,
    identical_slide_groups,
    wrong_slide_rate,
)
from auto_presenter.eval.replay import load_recording  # noqa: E402
from auto_presenter.eval.types import Decision  # noqa: E402

HERE = Path(__file__).resolve().parent
HOLDOUT = mr.SPK / "holdout/holdout.json"
TOL = 1.0  # seconds a screen may lead/lag the singing in the tolerant variant
BRIDGE = float(__import__("os").environ.get("VISIBLE_BRIDGE", "2.0"))  # score gaps inside a line up to this long


def vis_err(*a, **kw):
    return vis.visible_error(*a, bridge_s=BRIDGE, **kw)


def vis_samples(*a, **kw):
    return vis.sung_samples(*a, bridge_s=BRIDGE, **kw)


def transcript(name):
    rdir = mr.CASES[name][0]
    f = rdir / "transcript.json"
    if not f.exists():
        f = HERE / "transcripts" / f"{name}.json"
    return [(w["start"], w["end"], w["word"]) for w in json.loads(f.read_text())["words"]]


_cache = {}


def setup(name):
    if name not in _cache:
        rec = load_recording(mr.CASES[name][0])
        pl = vis.place_sung_words(transcript(name), rec.setlist_content, ngram=int(__import__("os").environ.get("VISIBLE_NGRAM", "3")))
        gt = sorted(rec.ground_truth, key=lambda g: g.t_seconds)
        grace = [(m.t_seconds, m.t_seconds + DEFAULT_DEVIATION_GRACE_S) for m in derive_deviation_markers(gt)]
        _cache[name] = (rec, gt, pl, grace)
    return _cache[name]


def follower(name):
    """music_replay.decisions(name, DEFAULT), cached on disk (replays take minutes)."""
    import os
    import pickle

    cache = Path(os.environ.get("VISIBLE_CACHE", "/tmp")) / f"visible_dec_{name}.pkl"
    if cache.exists():
        return pickle.loads(cache.read_bytes())
    dec, _ = mrp.decisions(name, dict(mrp.DEFAULT))
    cache.write_bytes(pickle.dumps(dec))
    return dec


def production(name):
    """The production engine's live decisions with the score follower off."""
    rid = mr.CASES[name][0].name
    for r in json.loads(HOLDOUT.read_text()):
        if r["name"] == rid and r["config"] == "off":
            return [Decision(t_seconds=t, item_idx=i, slide_idx=s, source="ai") for t, i, s in r["trace"]], r
    return None, None


def wsr(dec, rec):
    return wrong_slide_rate(dec, rec.ground_truth, None, forgive_unpredictable=True,
                            setlist_content=rec.setlist_content, forgive_identical_slides=True)


def check(names):
    for name in names:
        rec, gt, pl, _ = setup(name)
        lo, hi = gt[0].t_seconds, gt[-1].t_seconds
        sung = vis_samples(pl, lo, hi)
        # golden cue -> first placed word at home on that slide within 15 s
        offs = []
        for g in gt:
            key = (g.item_idx, g.slide_idx)
            ws = [w.start for w in pl.placed if key in w.homes and g.t_seconds - 5 <= w.start <= g.t_seconds + 15]
            if ws:
                offs.append(min(ws) - g.t_seconds)
        print(f"{name:7s} words {len(pl.words):4d} placed {pl.placed_share:5.1%}  sung-scored {len(sung) * 0.05:6.1f}s "
              f"of {hi - lo:6.1f}s ({len(sung) * 0.05 / (hi - lo):5.1%})  cue->first word median {np.median(offs):+.2f}s "
              f"IQR [{np.percentile(offs, 25):+.2f},{np.percentile(offs, 75):+.2f}] n={len(offs)}")


def score(names):
    """wrong_slide_rate vs visible error (deviation grace applied to both), strict and +-TOL s."""
    print(f"visible = hidden share of placed singing; strict / +-{TOL:.0f}s tolerance; 2 s deviation grace on all")
    print(f"{'':7s} {'follower':>22s}   {'production (off)':>22s}   {'golden':>13s}  coverage")
    print(f"{'':7s} {'wrong':>6s} {'strict':>7s} {'tol':>7s}   {'wrong':>6s} {'strict':>7s} {'tol':>7s}   {'strict':>6s} {'tol':>6s}")
    tot = collections.defaultdict(list)
    for name in names:
        rec, gt, pl, grace = setup(name)
        row = []
        for d in (follower(name), production(name)[0]):
            if d is None:
                row.append(None)
                continue
            row.append((wsr(d, rec), vis_err(d, gt, pl, excused=grace).rate,
                        vis_err(d, gt, pl, excused=grace, tolerance_s=TOL).rate))
        g0 = vis_err(gt, gt, pl, excused=grace)
        g1 = vis_err(gt, gt, pl, excused=grace, tolerance_s=TOL)
        f = lambda r: f"{'-':>6s} {'-':>7s} {'-':>7s}" if r is None else f"{r[0]:6.3f} {r[1]:7.3f} {r[2]:7.3f}"  # noqa: E731
        print(f"{name:7s} {f(row[0])}   {f(row[1])}   {g0.rate:6.3f} {g1.rate:6.3f}  {g0.coverage:5.1%}")
        for i, k in enumerate(("w", "s", "t")):
            tot["f" + k].append(row[0][i])
            if name != "set":
                tot["fx" + k].append(row[0][i])
            if row[1]:
                tot["p" + k].append(row[1][i]); tot["h" + k].append(row[0][i])
    m = lambda k: f"{np.mean(tot[k + 'w']):.3f} / {np.mean(tot[k + 's']):.3f} / {np.mean(tot[k + 't']):.3f}"  # noqa: E731
    print("means: wrong / visible strict / visible tol")
    print(f"  follower all 8      {m('f')}")
    print(f"  follower 7 (no set) {m('fx')}")
    print(f"  follower held-out 4 {m('h')}")
    print(f"  production held-out {m('p')}")


def sung_grid(name):
    """{sample index: homes} on the 0.05 s grid, over the golden's span."""
    rec, gt, pl, _ = setup(name)
    return {round(t / 0.05): h for t, h in vis_samples(pl, gt[0].t_seconds, gt[-1].t_seconds)}


def slide_text(rec, key, n=8):
    for x in rec.setlist_content:
        if x.item_idx == key[0]:
            return " ".join(x.slides[key[1]][:n]) if key[1] < len(x.slides) and x.slides[key[1]] else "(blank)"
    return "?"


def golden_issues(name, min_s=1.0, gap=2.5):
    """Episodes where the golden's slide does not carry what is being sung."""
    rec, gt, pl, _ = setup(name)
    g_at = vis.screen_at(gt)
    grid = sung_grid(name)
    v = vis_err(gt, gt, pl)
    out = []
    for a, b in vis.merge_windows(v.hidden_windows, gap):
        ks = [k for k in range(round(a / 0.05), round(b / 0.05)) if k in grid]
        hid = [k for k in ks if g_at(k * 0.05) not in grid[k]]
        if len(hid) * 0.05 < min_s:
            continue
        g = g_at(a + 0.01)
        sung = collections.Counter(s for k in hid for s in grid[k])
        top = [s for s, _ in sung.most_common(3)]
        words = " ".join(w.word for w in pl.placed if a <= w.start < b)[:70]
        gi = max(i for i, x in enumerate(gt) if x.t_seconds <= a + 0.01)
        prev = [(x.item_idx, x.slide_idx) for x in gt[max(0, gi - 1):gi]]
        nxt = [(x.item_idx, x.slide_idx, x.t_seconds) for x in gt[gi + 1:gi + 3]]
        near = {(x.item_idx, x.slide_idx) for x in gt if abs(x.t_seconds - a) < 30}
        top_set = set(sung)
        if g is not None and slide_text(rec, g) == "(blank)":
            cls = "blank while singing"
        elif nxt and (nxt[0][0], nxt[0][1]) in top_set and nxt[0][2] <= b + 1.0:
            cls = f"late {nxt[0][2] - a:.1f}s"
        elif prev and prev[0] in top_set:
            cls = "early"
        elif not (top_set & near):
            cls = "sung slide not shown"
        else:
            cls = "other"
        out.append(dict(a=a, b=b, hidden=len(hid) * 0.05, golden=g, sung=top, cls=cls, words=words))
    return out


def counted_wrong(name):
    """error_anatomy's per-0.1 s classification for the cached follower."""
    rec = setup(name)[0]
    mrp.decisions = lambda n, _arm: (follower(n), setup(n)[0])  # noqa: E731
    seq, _gt = ea.anatomy(name)
    return seq, rec


def audit(names):
    grand = collections.Counter()
    for name in names:
        rec, gt, pl, _ = setup(name)
        issues = golden_issues(name)
        seq, _ = counted_wrong(name)
        dec = follower(name)
        f_at = vis.screen_at(dec)
        grid = sung_grid(name)
        wrong_t = sum(ea.STEP for _t, c in seq if c)
        in_issue = sum(ea.STEP for t, c in seq if c and any(i["a"] <= t < i["b"] for i in issues))
        in_issue_vis = sum(ea.STEP for t, c in seq if c and any(i["a"] <= t < i["b"] for i in issues)
                           and round(t / 0.05) in grid and f_at(t) in grid[round(t / 0.05)])
        grand["wrong"] += wrong_t; grand["in"] += in_issue; grand["vis"] += in_issue_vis
        grand["issue_s"] += sum(i["hidden"] for i in issues)
        print(f"\n== {name}: {len(issues)} golden issues, {sum(i['hidden'] for i in issues):.1f} s of singing not on the "
              f"operator's slide | follower counted-wrong {wrong_t:.0f} s, {in_issue:.0f} s inside issues "
              f"({in_issue_vis:.0f} s of it with the sung line on OUR screen)")
        for i in issues:
            print(f"  {i['a']:6.1f}-{i['b']:6.1f} ({i['hidden']:4.1f}s) {i['cls']:22s} golden {i['golden']} "
                  f"[{slide_text(rec, i['golden'], 6)}] sung {i['sung']} :: {i['words']}")
    print(f"\nall: follower counted-wrong {grand['wrong']:.0f} s; {grand['in']:.0f} s ({grand['in'] / grand['wrong']:.1%}) "
          f"inside golden-issue windows; {grand['vis']:.0f} s of that with the sung line on our screen")


def voice_grid(name):
    """0.05 s samples inside a sung phrase: any transcript words (placed or not, held
    notes up to 4 s) joined across gaps < 1.5 s. Outside = instrumental / silence."""
    rec, gt, pl, _ = setup(name)
    phrases = vis.merge_windows([(a, max(min(b, a + 4.0), a + 0.2)) for a, b, _w in pl.words], 1.5)
    out = set()
    for a, b in phrases:
        out.update(range(round(max(a, gt[0].t_seconds) / 0.05), round(min(b, gt[-1].t_seconds) / 0.05)))
    return out


def anatomy(names, tol=0.0):
    """Each counted class split: sung+visible / sung+HIDDEN / voice not placed / no voice."""
    tot = collections.Counter()
    long_eps = collections.Counter()
    for name in names:
        seq, rec = counted_wrong(name)
        gt, pl, grace = setup(name)[1:]
        f_at = vis.screen_at(follower(name))
        grid = {round(t / 0.05): h for t, h in vis_samples(pl, gt[0].t_seconds, gt[-1].t_seconds, tolerance_s=tol)}
        voice = voice_grid(name)
        lab_of = {}
        for t, cls in seq:
            k = round(t / 0.05)
            if k in grid:
                lab = "visible" if f_at(t) in grid[k] else "HIDDEN"
            else:
                lab = "unplaced" if k in voice else "no voice"
            lab_of[round(t / ea.STEP)] = lab
            tot[(cls or "counted right", lab)] += ea.STEP
        for a, dur, cls in ea.episodes(seq):
            if dur >= 3:
                for t in np.arange(a, a + dur - 1e-6, ea.STEP):
                    long_eps[(cls == "wrong song", lab_of.get(round(t / ea.STEP), "no voice"))] += ea.STEP
    print(f"{len(names)} recordings{' (tolerance +-%.0fs)' % tol if tol else ''} (seconds):")
    print(f"  {'class':16s} {'counted':>8s} {'no voice':>9s} {'unplaced':>9s} {'visible':>8s} {'HIDDEN':>7s} {'hidden/sung':>12s}")
    for cls in ("late", "early", "lost", "wrong song", "blank vs words", "counted right"):
        n, u, v, h = (tot[(cls, x)] for x in ("no voice", "unplaced", "visible", "HIDDEN"))
        print(f"  {cls:16s} {n + u + v + h:8.0f} {n:9.0f} {u:9.0f} {v:8.0f} {h:7.0f} {h / (v + h) if v + h else 0:12.1%}")
    for ws in (False, True):
        row = {lab: v for (w, lab), v in long_eps.items() if w == ws}
        tot_ = sum(row.values())
        print(f"  episodes >= 3 s, {'wrong song' if ws else 'within song'}: {tot_:.0f} s = "
              + "  ".join(f"{k} {row.get(k, 0):.0f}" for k in ("no voice", "unplaced", "visible", "HIDDEN")))


def hidden_episodes(names, tol=0.0, min_s=1.0):
    """The follower's visible errors: where the sung line was not on our screen."""
    for name in names:
        rec, gt, pl, grace = setup(name)
        dec = follower(name)
        f_at, g_at = vis.screen_at(dec), vis.screen_at(gt)
        seq, _ = counted_wrong(name)
        counted = {round(t / ea.STEP): c for t, c in seq}
        v = vis_err(dec, gt, pl, excused=grace, tolerance_s=tol)
        print(f"\n== {name}: hidden {v.hidden:.0f} s of {v.scored:.0f} s sung")
        for a, b in vis.merge_windows(v.hidden_windows, 2.5):
            hid = sum(min(y, b) - max(x, a) for x, y in v.hidden_windows if x < b and y > a)
            if hid < min_s:
                continue
            cls = collections.Counter(counted.get(round(t / ea.STEP)) or "counted-right"
                                      for t in np.arange(a, b, ea.STEP))
            words = " ".join(w.word for w in pl.placed if a - 0.5 <= w.start < b)[:60]
            homes = collections.Counter(h for w in pl.placed if a - 0.5 <= w.start < b for h in w.homes)
            print(f"  {a:6.1f}-{b:6.1f} hidden {hid:4.1f}s  ours {f_at(a + 0.01)} golden {g_at(a + 0.01)} "
                  f"sung {[h for h, _ in homes.most_common(3)]}  {dict(cls)}  :: {words}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "score"
    if len(sys.argv) > 3:
        TOL = float(sys.argv[3])
    names = sys.argv[2].split(",") if len(sys.argv) > 2 else list(mr.CASES)
    {"check": check, "score": score, "audit": audit, "anatomy": lambda n: anatomy(n, TOL if len(sys.argv) > 3 else 0.0), "episodes": hidden_episodes}[mode](names)
