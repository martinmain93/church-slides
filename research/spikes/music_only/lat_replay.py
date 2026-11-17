"""Replay the live traces with span-scorer evidence regenerated at other window / step settings.

    python research/spikes/music_only/wt_run.py lat_replay.py "ARM;ARM;..." [names]

ARM = `label:key=value,...`, keys (defaults in DEFAULT):
  src    trace | cache     trace = the live run's own scored rows; cache = scorer_cache.py rows
  win    window seconds (cache)                 step  seconds between steps (cache: 0.5, 1, 2, 4)
  span   words per span (cache)                 lat   seconds from window end to arrival
                                                       (number, or `meas` = the cached compute, GPU-contended)
  wmode  per-step weight of a scored step, times reliability:
           base    = online.SCORED_WEIGHT (1/3) whatever the step
           rate    = 1/3 * step/2     (same evidence per second as the live 6 s / 2 s)
           overlap = step / win       (1 / number of windows covering a moment)
  wx     extra multiplier on that weight         la    lookahead_s (0.8)
  shift  seconds added to each scored step's placement t (negative = earlier)
Everything else is music_replay.DEFAULT (song changes from the trace, live chords, words).
Reports wrong-slide (tuning / held-out), median anticipation error, and the
error anatomy (late / early / lost / blank / wrong-song seconds, long episodes).
"""
from __future__ import annotations

import collections
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diag_live_trace as d  # noqa: E402
import error_anatomy as ea  # noqa: E402
import multi_replay as mr  # noqa: E402
import music_replay as mrp  # noqa: E402
import scorer_cache as scc  # noqa: E402
from auto_presenter.eval.metrics import (  # noqa: E402
    DEFAULT_DEVIATION_GRACE_S,
    anticipation_error_ms,
    blank_cue_windows,
    derive_deviation_markers,
    identical_slide_groups,
    wrong_slide_rate,
)
from auto_presenter.eval.replay import load_recording  # noqa: E402
from auto_presenter.eval.types import Decision  # noqa: E402
from auto_presenter.follower import online  # noqa: E402
from auto_presenter.follower import score as fsc  # noqa: E402
from auto_presenter.follower.online import OnlineFollower  # noqa: E402
from auto_presenter.follower.reference import SegmentReference  # noqa: E402
from auto_presenter.follower.scorer import spans_for  # noqa: E402

DEFAULT = {"src": "trace", "win": 6.0, "step": 2.0, "span": 6, "lat": "0.3", "wmode": "base", "wx": 1.0,
           "la": 0.8, "shift": 0.0}
BASE_W = online.SCORED_WEIGHT


def parse(spec):
    label, _, body = spec.partition(":")
    arm = dict(DEFAULT)
    for kv in filter(None, body.split(",")):
        k, v = kv.split("=")
        arm[k] = v if k in ("src", "lat", "wmode") else int(v) if k == "span" else float(v)
    return label, arm


def scored_rows(name, arm, trace_rows):
    if arm["src"] == "trace":
        rows = [dict(r) for r in trace_rows if r["e"] == "scored"]
        step, win = 2.0, 6.0
        if arm["lat"] != "meas":
            for r in rows:
                r["arr"] = r["win_end"] + float(arm["lat"])
    else:
        rows = scc.load_rows(name, arm["win"], arm["step"], arm["span"])
        step, win = arm["step"], arm["win"]
        for r in rows:
            r["arr"] = r["win_end"] + (r["compute"] if arm["lat"] == "meas" else float(arm["lat"]))
    for r in rows:
        r["t"] = r["t"] + arm["shift"]
    w = {"base": BASE_W, "rate": BASE_W * step / 2.0, "overlap": step / win}[arm["wmode"]] * arm["wx"]
    return rows, w


def decisions(name, arm):
    rdir, trace, sheets = mr.CASES[name]
    trows = d.load_trace(trace)
    srows, w = scored_rows(name, arm, trows)
    rows = [r for r in trows if r["e"] != "scored"] + srows
    online.SCORED_WEIGHT = w
    mrp.OnlineFollower = lambda ref, identical: OnlineFollower(ref, identical=identical, lookahead_s=arm["la"])
    rec = load_recording(rdir)
    dur = max(r["t"] for r in rows) + 5
    ig = identical_slide_groups(rec.setlist_content)
    enters = sorted((r["t"], r["item"], r["slide"]) for r in rows if r["e"] == "enter")
    end = max(r["t"] for r in rows)
    spans_on = [(t, enters[i + 1][0] if i + 1 < len(enters) else end + 1, it) for i, (t, it, _s) in enumerate(enters)]
    marm = dict(mrp.DEFAULT)
    out = []
    for item, pdf in sheets.items():
        snap = [x for x in rec.setlist_content if x.item_idx == item][0]
        ident = {sl: set(ig.get((item, sl), ())) for sl in range(len(snap.slides))}
        owner = spans_for(snap.slides, arm["span"])
        spans = sorted(set(owner.values()))
        ref = SegmentReference(fsc.from_extract(mr.sheet(pdf)), snap.slides, list(snap.slide_sections))
        prev = None
        for t, slide, _ in mrp.replay_item(rows, ref, owner, spans, ident, item, marm, name, dur):
            if slide is None or slide == prev:
                continue
            prev = slide
            if any(a <= t < b and it == item for a, b, it in spans_on):
                out.append((t, item, int(slide)))
    out += [(t, it, s) for t, it, s in enters if t > 0]
    out.sort()
    dec, shown = [], None
    for t, it, s in out:
        if (it, s) != shown:
            shown = (it, s)
            dec.append(Decision(t_seconds=t, item_idx=it, slide_idx=s, source="ai"))
    return dec, rec


def anatomy(dec, rec):
    """error_anatomy.anatomy on given decisions."""
    gt = sorted(rec.ground_truth, key=lambda g: g.t_seconds)
    equiv = identical_slide_groups(rec.setlist_content)
    excused = list(blank_cue_windows(gt, rec.setlist_content))
    excused += [(m.t_seconds, m.t_seconds + DEFAULT_DEVIATION_GRACE_S) for m in derive_deviation_markers(gt)]
    same = lambda g, x: x is not None and (g == x or (x[0] == g[0] and x[1] in equiv.get(g, frozenset())))  # noqa: E731
    out, gi, di, shown = [], 0, 0, None
    for k in range(int((gt[-1].t_seconds - gt[0].t_seconds) / ea.STEP)):
        t = gt[0].t_seconds + k * ea.STEP
        while gi + 1 < len(gt) and gt[gi + 1].t_seconds <= t:
            gi += 1
        while di < len(dec) and dec[di].t_seconds <= t:
            shown = (dec[di].item_idx, dec[di].slide_idx)
            di += 1
        g = (gt[gi].item_idx, gt[gi].slide_idx)
        if same(g, shown) or any(a <= t < b for a, b in excused):
            out.append((t, None))
            continue
        prev = (gt[gi - 1].item_idx, gt[gi - 1].slide_idx) if gi else None
        nxt = (gt[gi + 1].item_idx, gt[gi + 1].slide_idx) if gi + 1 < len(gt) else None
        if shown is None or shown[0] != g[0]:
            c = "song"
        elif (shown[1] == 0) != (g[1] == 0):
            c = "blank"
        elif prev is not None and same(prev, shown):
            c = "late"
        elif nxt is not None and same(nxt, shown):
            c = "early"
        else:
            c = "lost"
        out.append((t, c))
    return out


def run_one(job):
    name, arm = job
    dec, rec = decisions(name, arm)
    w = wrong_slide_rate(dec, rec.ground_truth, None, forgive_unpredictable=True,
                         setlist_content=rec.setlist_content, forgive_identical_slides=True)
    a = anticipation_error_ms(dec, rec.ground_truth)
    seq = anatomy(dec, rec)
    cls = collections.Counter(c for _t, c in seq if c)
    eps = ea.episodes(seq)
    return name, {"wrong": w, "antic": list(map(float, a)), "cls": {k: v * ea.STEP for k, v in cls.items()},
                  "t": len(seq) * ea.STEP, "eps": eps, "n": len(dec)}


def summarize(label, res, names):
    tune = [res[n]["wrong"] for n in names if n not in mr.HELD_OUT]
    held = [res[n]["wrong"] for n in names if n in mr.HELD_OUT]
    antic = np.concatenate([res[n]["antic"] for n in names]) if names else np.array([])
    cls = collections.Counter()
    for n in names:
        cls.update(res[n]["cls"])
    tot = sum(res[n]["t"] for n in names)
    long_ = sum(dur for n in names for _a, dur, _c in res[n]["eps"] if dur >= 5)
    short_late = sum(dur for n in names for _a, dur, c in res[n]["eps"] if dur < 3 and c == "late")
    f = lambda xs: f"{np.mean(xs):.3f}" if xs else "  -  "  # noqa: E731
    return (f"{label:22s} tune {f(tune)} held {f(held)} all {f(tune + held)} | antic med {np.median(antic):+5.0f} "
            f"p25 {np.percentile(antic, 25):+5.0f} p75 {np.percentile(antic, 75):+5.0f} ms | "
            + " ".join(f"{k} {cls[k] / tot:.3f}" for k in ("late", "early", "lost", "blank", "song"))
            + f" | <3s-late {short_late / tot:.3f} >=5s-eps {long_ / tot:.3f} | "
            + " ".join(f"{n} {res[n]['wrong']:.3f}" for n in names))


if __name__ == "__main__":
    arms = [parse(s) for s in sys.argv[1].split(";") if s]
    names = sys.argv[2].split(",") if len(sys.argv) > 2 else list(mr.CASES)
    procs = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--procs=")), 4))
    with Pool(procs) as pool:
        for label, arm in arms:
            res = dict(pool.map(run_one, [(n, arm) for n in names]))
            print(summarize(label, res, names), flush=True)
