"""One mis-slide episode, up close: what the band did, what we showed, what the evidence said.

    uv run --project sidecar python research/spikes/music_only/explain_episode.py NAME T [DUR]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diag_live_trace as d  # noqa: E402
import error_buckets as eb  # noqa: E402
import multi_replay as mr  # noqa: E402
import music_replay as mrp  # noqa: E402
from auto_presenter.follower.reference import SegmentReference, roadmap_sequence  # noqa: E402
from auto_presenter.follower import score as fsc  # noqa: E402
from auto_presenter.follower.scorer import spans_for  # noqa: E402


def main() -> int:
    name, t0 = sys.argv[1], float(sys.argv[2])
    dur = float(sys.argv[3]) if len(sys.argv) > 3 else 20.0
    lo, hi = t0 - 15, t0 + dur + 8
    rdir, _old, sheets = mr.CASES[name]
    mr.CASES[name] = (rdir, eb.TRACE[name], sheets)  # the latest live run, as error_buckets
    dec, rec = mrp.decisions(name, mrp.env_arm())
    rdir, trace, sheets = mr.CASES[name]
    rows = d.load_trace(trace)
    gt = sorted(rec.ground_truth, key=lambda g: g.t_seconds)
    item = gt[0].item_idx
    for g in gt:
        if g.t_seconds <= t0:
            item = g.item_idx
    snap = [x for x in rec.setlist_content if x.item_idx == item][0]
    words = lambda s: " ".join(snap.slides[s])[:60] if s < len(snap.slides) else "?"  # noqa: E731
    if item in sheets:
        ref = SegmentReference(fsc.from_extract(mr.sheet(sheets[item])), snap.slides, list(snap.slide_sections))
        print(f"sheet order (roadmap): {roadmap_sequence(ref.spans)}   unbound slides: "
              f"{[s for s in range(1, len(snap.slides)) if not ref.spans.get(s) and snap.slides[s]]}")
    print(f"--- band (golden), item {item}")
    for g in gt:
        if lo <= g.t_seconds <= hi:
            print(f"  {g.t_seconds:6.1f}  band -> {g.item_idx}/{g.slide_idx}: {words(g.slide_idx) if g.item_idx == item else ''}")
    print("--- we showed")
    for x in dec:
        if lo <= x.t_seconds <= hi:
            print(f"  {x.t_seconds:6.1f}  screen -> {x.item_idx}/{x.slide_idx}")
    owner = spans_for(snap.slides)
    spans = sorted(set(owner.values()))
    print("--- evidence (arrival time): words heard; scorer's best spans")
    for r in rows:
        if r.get("item") not in (None, item) or not (lo <= r.get("arr", r.get("t", 0)) <= hi):
            continue
        if r["e"] == "word":
            print(f"  {r['arr']:6.1f}  word {r['text']!r} (sung {r['t']:.1f})")
        elif r["e"] == "scored":
            sc = np.asarray(r["scores"])
            top = np.argsort(sc)[::-1][:2]
            own = lambda i: sorted({k[0] for k, v in owner.items() if v == spans[i]})  # noqa: E731
            print(f"  {r['arr']:6.1f}  scorer: {spans[top[0]]!r} slides {own(top[0])} {sc[top[0]]:.2f} | "
                  f"{spans[top[1]]!r} {own(top[1])} {sc[top[1]]:.2f}")
        elif r["e"] in ("enter", "key"):
            print(f"  {r.get('t', 0):6.1f}  {r['e']} {r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
