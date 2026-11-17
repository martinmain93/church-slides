"""Trace the scheduler's logic on the room's clock: why is the screen ahead?

    uv run --project sidecar python research/spikes/music_only/diag_scheduler.py [--songs wabn,rl,...]

Production engine, live MLX transcripts. `on_position` is wrapped so every
belief the scheduler sees is recorded with its projection, what was on
screen, and what it decided. Then:

1. Every commit is labelled: anticipation (projection advancing past the
   tracker's own slide), catch-up (to the tracker's slide), backward.
   For forward commits, the ground truth says when the band actually
   reached that slide: `arrival - shown` on the room's clock. Negative =
   shown early. "never" = the band did not sing that slide next.

2. Every screen-ahead second (tracker right, screen later in the same song)
   is traced to the commit that put the screen there.

3. Every belief the scheduler refused because the move was one slide
   backward is recorded with position in the slide, confidence and how long
   the screen had been up, split by what was really going on: the band
   still finishing the slide (lag) or back on it (repeat) — so a rule can
   be judged on whether those two are separable at all.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter

import numpy as np

import cv
import diag_fusion as dg
import fusion
from auto_presenter.asr.types import TranscriptEvent, Word
from auto_presenter.decision.production import build_engine
from auto_presenter.eval.metrics import DEFAULT_DEVIATION_GRACE_S, blank_cue_windows, derive_deviation_markers, identical_slide_groups, wrong_slide_rate
from auto_presenter.eval.replay import load_recording
from auto_presenter.eval.types import Decision
from auto_presenter.index.library import build_index
from auto_presenter.protocol import ParticleFilterTunables


def run(rec_dir, events, config_overrides: dict | None = None):
    rec = load_recording(rec_dir)
    indexed = build_index(fusion.content(rec))
    engine = build_engine(indexed, ParticleFilterTunables())
    sch = engine.scheduler
    for k, v in (config_overrides or {}).items():
        object.__setattr__(sch.config, k, v) if hasattr(sch.config, "__dataclass_fields__") else setattr(sch.config, k, v)
    log = []
    orig = sch.on_position
    cur = {}

    def traced(tracked):
        shown = (tracked.item_idx, sch._displayed_slide(tracked.item_idx))
        proj = sch.project(tracked)
        held = tracked.t_seconds - sch._last_commit_t if sch._last_commit_t is not None else None
        d = orig(tracked)
        log.append({
            "t_end": tracked.t_seconds, "avail": cur["avail"], "item": tracked.item_idx, "mode": tracked.slide_idx,
            "position": tracked.position, "conf": tracked.confidence, "shown": shown[1],
            "target": proj.target_slide if proj else tracked.slide_idx,
            "advancing": bool(proj and proj.advancing), "secs_left": proj.seconds_remaining if proj else None,
            "lead": proj.lead_seconds if proj else None, "held": held,
            "commit": (d.item_idx, d.slide_idx) if d else None,
        })
        return d

    sch.on_position = traced
    decisions = []
    for ev in events:
        cur["avail"] = ev["available_at"]
        te = TranscriptEvent(words=[Word(text=w["text"], t_start=w["t_start"], t_end=w["t_end"]) for w in ev["words"]],
                             is_final=True, segment_id=ev["segment_id"])
        d = engine.update(te)
        if d is not None:
            decisions.append(Decision(t_seconds=ev["available_at"], item_idx=d.item_idx, slide_idx=d.slide_idx, source="ai"))
    return rec, decisions, log


def analyse(key, rec, decisions, log):
    gte = rec.ground_truth
    equiv = identical_slide_groups(rec.setlist_content)
    same = lambda g, d: g is not None and d is not None and (g == d or (d[0] == g[0] and d[1] in equiv.get(g, frozenset())))
    seq = [(g.t_seconds, (g.item_idx, g.slide_idx)) for g in gte]

    # 1. commits
    commits = []
    for e in log:
        if e["commit"] is None:
            continue
        kind = "backward" if e["commit"][1] < e["shown"] else ("anticipation" if e["advancing"] and e["commit"][1] > e["mode"] else "catch-up")
        # when did the band actually get to that slide (next GT cue for it, or an identical one, after the belief time)?
        arrival = None
        gnow = dg.gt_at(gte, e["avail"])
        if same(gnow, e["commit"]):
            # already there when shown: when did it start?
            starts = [t for t, s in seq if t <= e["avail"] and same(s, e["commit"])]
            arrival = starts[-1] if starts else None
        else:
            nxt = [(t, s) for t, s in seq if t > e["avail"]]
            if nxt and same(nxt[0][1], e["commit"]):
                arrival = nxt[0][0]
        commits.append((kind, e, arrival))
    print(f"--- {key}: {len(commits)} commits")
    for kind in ("anticipation", "catch-up", "backward"):
        cs = [c for c in commits if c[0] == kind]
        if not cs:
            continue
        err = [c[2] - c[1]["avail"] for c in cs if c[2] is not None]
        never = sum(1 for c in cs if c[2] is None)
        early = [x for x in err if x > 0]
        print(f"   {kind:12s} {len(cs):3d}: shown vs band's arrival median {(-np.median(err) if err else float('nan')):+5.1f} s"
              f" (positive = shown late);  shown before the band got there: {len(early)} (median {np.median(early) if early else 0:.1f} s early);"
              f"  band never went there next: {never}")

    # 2. screen-ahead seconds -> the commit that caused them
    excused = list(blank_cue_windows(gte, rec.setlist_content)) + [(m.t_seconds, m.t_seconds + DEFAULT_DEVIATION_GRACE_S) for m in derive_deviation_markers(gte)]
    cause = Counter()
    li = -1
    last_commit = None
    for t in np.arange(gte[0].t_seconds, gte[-1].t_seconds, 0.1):
        while li + 1 < len(log) and log[li + 1]["avail"] <= t:
            li += 1
            if log[li]["commit"] is not None:
                last_commit = next(c for c in commits if c[1] is log[li])
        if li < 0 or any(a <= t < b for a, b in excused) or last_commit is None:
            continue
        e = log[li]
        g = dg.gt_at(gte, t)
        shown = last_commit[1]["commit"]
        belief = (e["item"], e["mode"])
        if same(g, shown) or not same(g, belief) or shown[0] != belief[0] or shown[1] <= belief[1]:
            continue
        cause[last_commit[0]] += 0.1
    print("   screen-ahead seconds by the commit that caused them: " + ", ".join(f"{k} {v:.0f} s" for k, v in cause.most_common()))

    # 3. refused one-slide-back beliefs: lag or repeat?
    refused = []
    for e in log:
        if e["commit"] is None and e["item"] == e["item"] and e["target"] == e["shown"] - 1 and e["conf"] >= 0.6:
            g_src = dg.gt_at(gte, e["t_end"])
            if not same(g_src, (e["item"], e["mode"])):
                continue  # tracker wrong: not this question
            # lag: the band had not yet reached the shown slide; repeat: it had sung it and come back
            k = max(i for i, (tt, _) in enumerate(seq) if tt <= e["t_end"])
            sung_before = any(same(s, (e["item"], e["shown"])) for _, s in seq[:k])
            last_left = None
            refused.append(("repeat" if sung_before else "lag", e))
    for kind in ("lag", "repeat"):
        rs = [r[1] for r in refused if r[0] == kind]
        if rs:
            pos = np.array([r["position"] for r in rs])
            held = np.array([r["held"] if r["held"] is not None else np.nan for r in rs])
            print(f"   refused one-back ({kind:6s}) {len(rs):3d} beliefs: position median {np.median(pos):.2f} (<=0.25 on {np.mean(pos <= 0.25):.0%}),"
                  f" screen up for median {np.nanmedian(held):.1f} s (p10 {np.nanpercentile(held, 10):.1f}, p90 {np.nanpercentile(held, 90):.1f})")
    return commits, refused


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--songs", default=",".join(list(cv.SONGS) + ["set1002"]))
    args = ap.parse_args()
    events = json.loads(fusion.EVENTS.read_text())
    lags = []
    for key in args.songs.split(","):
        rid = load_recording(cv.S / key).id
        if rid not in events:
            print(f"--- {key}: no live transcript yet")
            continue
        lags += [e["available_at"] - e["t_end"] for e in events[rid]]
        rec, dec, log = run(cv.S / key, events[rid])
        analyse(key, rec, dec, log)
        print(f"   lead used: median {np.median([e['lead'] for e in log if e['lead']]):.2f} s")
    print(f"\nmeasured delivery lag (available_at - t_end): median {np.median(lags):.2f} s, p10 {np.percentile(lags, 10):.2f}, p90 {np.percentile(lags, 90):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
