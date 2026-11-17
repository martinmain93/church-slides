"""List the slide changes that land 2 s+ after the band's first word, and why.

    uv run --project sidecar python research/spikes/music_only/lates.py ARM

For each: the recording, the cue, how late, the slide moved from -> to, and
whether the move followed the reference's order (`in order`) or the band went
somewhere the reference didn't expect (`off order`: a repeat, a skip).
"""
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))


def case(name, spec):
    import perceived_replay as P
    import sung_timing_check as stc
    import multi_replay as mr
    from auto_presenter.eval.metrics import identical_slide_groups
    dec, rec = P._decide(name, spec)
    tm = stc.timings(Path(mr.CASES[name][0]), rec)
    gt = sorted(rec.ground_truth, key=lambda g: g.t_seconds)
    eq = identical_slide_groups(rec.setlist_content)
    slides = {i.item_idx: i.slides for i in rec.setlist_content}
    same = lambda a, b: a[0] == b[0] and (a[1] == b[1] or a[1] in eq.get(b, ()))
    out = []
    for k in range(1, len(gt)):
        g, p = (gt[k].item_idx, gt[k].slide_idx), (gt[k - 1].item_idx, gt[k - 1].slide_idx)
        if not slides[g[0]][g[1]] or same(p, g):
            continue  # a blank, or a copy of the slide already up
        lo = tm[k - 1].first_word
        hi = tm[k + 1].first_word if k + 1 < len(gt) else tm[k].first_word + 15
        hit = next((d for d in dec if lo <= d.t_seconds < hi and same((d.item_idx, d.slide_idx), g)), None)
        late = (hit.t_seconds if hit else hi) - tm[k].first_word
        if late < 2:
            continue
        # written order: the next non-blank slide after p (or the same song's first)
        nxt = next((s for s in range(p[1] + 1, len(slides[p[0]])) if slides[p[0]][s]), None)
        kind = ("song change" if g[0] != p[0] else
                "in order" if nxt is not None and same((p[0], nxt), g) else "off order")
        out.append((name, round(gt[k].t_seconds, 1), round(late, 1), hit is None, p, g, kind))
    return out


if __name__ == "__main__":
    import collections
    import multi_replay as mr
    spec = sys.argv[1]
    with ProcessPoolExecutor(int(os.environ.get("JOBS", "8"))) as ex:
        rows = [r for rs in ex.map(case, list(mr.CASES), [spec] * len(mr.CASES)) for r in rs]
    c = collections.Counter(r[-1] for r in rows)
    s = collections.Counter()
    for r in rows:
        s[r[-1]] += min(r[2], 15)
    print(f"{len(rows)} changes 2 s+ late: " + ", ".join(f"{k} {v} ({s[k]:.0f} s)" for k, v in c.most_common()))
    for r in sorted(rows, key=lambda r: -r[2]):
        print(f"  {r[0]:7s} cue {r[1]:6.1f}  {'never shown' if r[3] else f'+{r[2]:4.1f} s':>11s}  {r[4]} -> {r[5]}  {r[6]}")
