"""Live scoring can't afford every span of a song: does scoring only the
follower's own candidates lose accuracy?

    uv run --project sidecar python research/spikes/music_only/exp_candidates.py

The cached MLX scores cover every distinct span at every step. Live, each
step scores only the spans of slides the follower currently believes in
(mass >= EPS, the top K by mass) plus each one's likeliest successors in the
order prior. Here that choice is made from the follower's belief at the
step's window end (from the previous pass: run, mask, run again until the
mask stops changing), and spans outside it are left out of the step. Cells
without a scored span get a neutral value (the scored mean, 0 after
centring) rather than the minimum, so not being asked about is not evidence
against a slide.

Placement: the cached DTW time is for the best span of *all*; when that span
is not a candidate, the step is placed by the median lag (2.3 s) instead.

Final configuration: words + scored (DTW) + mel_mix + chd_mix, observation
weighting, segments to the next line, lookahead 0.8 s.
"""
from __future__ import annotations

import json
import sys

import numpy as np

import cv
import fusion
import joint
import run_joint as rj
import segref
import follow as fl
from auto_presenter.eval.metrics import identical_slide_groups
from auto_presenter.eval.replay import load_recording

ARM = ["words", "scored", "mel_mix", "chd_mix"]
MEDIAN_LAG = 2.3


def configure():
    rj.WEIGHTING = "observation"
    rj.SCORER = "mlx_dtw"
    rj.SCORE_LATENCY = 0.2
    rj.EXTEND = True
    rj.LOOKAHEAD = 0.8


def masked_scored(song, ref, cand_spans: list[set[str]] | None, data, tokens: float = 7.0):
    """The scored source with step k limited to `cand_spans[k]` (None: all)."""
    win = float(data.get("win", 6.0))
    idx = {sp: i for i, sp in enumerate(data["spans"])}
    owner = {tuple(int(x) for x in k.split(":")): v for k, v in data["owner"].items()}
    seg = np.asarray(ref.seg_of)
    order = sorted((c, sw) for sw, c in ref.word_cell.items())
    evs, n_scored = [], []
    for k, (t, row) in enumerate(zip(data["steps"], data["scores"])):
        row = np.asarray(row)
        allowed = None if cand_spans is None else cand_spans[k]
        v = np.full(ref.P, np.nan)
        for c, sw in order:
            if sw in owner and (allowed is None or owner[sw] in allowed):
                v[c] = row[idx[owner[sw]]]
        n_scored.append(len(allowed) if allowed is not None else len(data["spans"]))
        if np.all(np.isnan(v)):
            continue
        last = np.nan
        for c in range(ref.P):
            if c > 0 and seg[c] != seg[c - 1]:
                last = np.nan
            if not np.isnan(v[c]):
                last = v[c]
            v[c] = last
        v = np.where(np.isnan(v), np.nanmean(v), v)  # neutral, not the minimum
        e = tokens * (v - v.mean())
        best_all = data["spans"][int(np.argmax(row))]
        if allowed is None or best_all in allowed:
            t_ev = t - win + data["place"][k]
        else:
            t_ev = t - MEDIAN_LAG
        evs.append((t_ev, t + rj.SCORE_LATENCY, joint.robust(e, 0.2).astype(np.float32)))
    span = max((arr - t_a for t_a, arr, _ in evs), default=1.0) + 0.5
    return joint.Source("scored", span, 2.0 / win, events=evs), n_scored


def candidates_from(walls, marg_now, ref, data, steps, eps: float, top_k: int, n_succ: int) -> list[set[str]]:
    """Spans to score at each step, from the follower's belief at that step's window end."""
    slide_of = np.asarray(ref.slide_of)
    seg_slides = list(ref.seg_slides)
    succ = np.asarray(ref.succ)
    by_slide: dict[int, set[str]] = {}
    for key, span in data["owner"].items():
        si = int(key.split(":")[0])
        by_slide.setdefault(si, set()).add(span)
    out = []
    for t in steps:
        i = max(0, np.searchsorted(walls, t, side="right") - 1)
        m = marg_now[i]
        mass = np.bincount(np.asarray(ref.seg_of), weights=m, minlength=len(seg_slides))
        top = [s for s in np.argsort(-mass)[:top_k] if mass[s] >= eps] or [int(np.argmax(mass))]
        segs = set(top)
        for s in top:
            segs.update(int(x) for x in np.argsort(-succ[s])[:n_succ])
        spans: set[str] = set()
        for s in segs:
            spans |= by_slide.get(seg_slides[s], set())
        out.append(spans)
    return out


def main() -> int:
    configure()
    events = json.loads(fusion.EVENTS.read_text())
    variants = [(0.02, 3, 1), (0.02, 4, 2), (0.05, 2, 1)]
    results: dict[str, list[float]] = {}
    spans_used: dict[str, list[int]] = {}
    for key in sys.argv[1:] or ("wabn", "rl", "komh", "isa"):
        song = cv.Song(key)
        rec = load_recording(cv.S / key)
        item = cv.stage1.song_item(song.s, cv.S / key)
        ig = identical_slide_groups(rec.setlist_content)
        ident = {sl: set(ig.get((item, sl), ())) for sl in range(len(song.slides))}
        _, corr = fl.estimate_key_shift(song.ev["mix"][2][:600], song.scorer.m)
        ref = segref.SegmentReference(song.s, song.slides, song.spans, key_shift=int(np.argmax(corr)),
                                      sequences=[rj.roadmap_sequence(song)], extend_to_next=True)
        smap = list(ref.slide_of)
        T = len(song.ev["mix"][0])
        data = json.loads((cv.S / f"wscore_mlx_{song.key}.json").read_text())
        others = [s for s in rj.sources_for(song, ref, events[rec.id], ARM) if s.name != "scored"]

        def run(cands):
            scored, n = masked_scored(song, ref, cands, data)
            walls, pos, rate, marg = joint.follow(ref, others + [scored], T, lookahead_s=0.8)
            shown = rj.slide_readout(marg, smap, ident)
            r = rj.evaluate(song, rec, item, walls, pos, smap, shown)
            return r[1], walls, joint.follow.marg_now.copy(), n

        base, walls, marg_now, n_all = run(None)
        results.setdefault("all spans", []).append(base)
        spans_used.setdefault("all spans", []).extend(n_all)
        line = [f"all spans {base:.3f} ({np.mean(n_all):.0f}/step)"]
        for eps, k, ns in variants:
            name = f"eps {eps} top {k} succ {ns}"
            w, mn = walls, marg_now
            for _ in range(3):  # iterate until the candidate choice is consistent with its own belief
                cands = candidates_from(w, mn, ref, data, data["steps"], eps, k, ns)
                r, w, mn, n = run(cands)
            results.setdefault(name, []).append(r)
            spans_used.setdefault(name, []).extend(n)
            line.append(f"{name}: {r:.3f} ({np.mean(n):.0f}/step, p90 {np.percentile(n, 90):.0f})")
        print(f"{key:5s} " + " | ".join(line), flush=True)
    print("mean:")
    for name, rs in results.items():
        print(f"  {name:24s} wrong-slide {np.mean(rs):.3f}   spans per step: mean {np.mean(spans_used[name]):.0f}, "
              f"p90 {np.percentile(spans_used[name], 90):.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
