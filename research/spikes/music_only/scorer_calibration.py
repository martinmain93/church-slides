"""How much does a scorer step know? Calibrate the span scores into a posterior over slides.

    uv run --project sidecar python research/spikes/music_only/scorer_calibration.py

Every scored step in the 8 live traces, paired with the slide the band was on
(golden) at the step's placed time. The step's span scores (mean log-prob per
token) become a posterior over slides: p(span) ∝ exp(beta * tokens * score),
each span's probability shared among the slides that carry it, identical-text
slides pooled. We fit beta by the log-loss of the band's slide and report:
  * log-loss / accuracy at each beta (the live code effectively uses beta*tokens = 7)
  * calibration: max-posterior bins -> how often that slide was the band's
  * entropy bins -> how often the top slide was right
"""
from __future__ import annotations

import collections
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diag_live_trace as d  # noqa: E402
import multi_replay as mr  # noqa: E402
from auto_presenter.eval.metrics import identical_slide_groups  # noqa: E402
from auto_presenter.eval.replay import load_recording  # noqa: E402
from auto_presenter.follower.scorer import spans_for  # noqa: E402


def steps():
    """(name, step scores, span->slide groups, band slide group, n slides)."""
    out = []
    for name in mr.CASES:
        rdir, trace, sheets = mr.CASES[name]
        rec = load_recording(Path(rdir))
        gt = sorted(rec.ground_truth, key=lambda g: g.t_seconds)
        ig = identical_slide_groups(rec.setlist_content)
        rows = d.load_trace(trace)
        cache = {}
        for r in rows:
            if r["e"] != "scored" or r.get("item") not in sheets:
                continue
            it = r["item"]
            if it not in cache:
                snap = [x for x in rec.setlist_content if x.item_idx == it][0]
                owner = spans_for(snap.slides)
                spans = sorted(set(owner.values()))
                canon = {s: min(ig.get((it, s), {s})) for s in range(len(snap.slides))}
                groups = []
                for sp in spans:
                    gs = sorted({canon[k[0]] for k, v in owner.items() if v == sp and k[1] > 0})
                    groups.append(gs)
                ntok = np.array([max(1, len(sp.split())) * 1.3 for sp in spans])
                cache[it] = (groups, canon, ntok, len(snap.slides))
            groups, canon, ntok, nsl = cache[it]
            band = None
            for g in gt:
                if g.t_seconds <= r["t"]:
                    band = g
            if band is None or band.item_idx != it or band.slide_idx == 0:
                continue
            out.append((name, np.asarray(r["scores"]), groups, canon[band.slide_idx], nsl, ntok))
    return out


def posterior(scores, groups, nsl, beta, ntok=None, per_token=False):
    keep = [i for i, g in enumerate(groups) if g]  # drop one-word spans (no slide owner)
    s = scores[keep] * (ntok[keep] if per_token else 1.0)
    z = beta * (s - s.max())
    p = np.exp(z)
    p /= p.sum()
    post = np.zeros(nsl)
    for pi, i in zip(p, keep, strict=True):
        for sl in groups[i]:
            post[sl] += pi / len(groups[i])
    return post


def main() -> int:
    data = steps()
    print(f"{len(data)} scorer steps with the band on a lyric slide")
    for per_token in (False, True):
        for beta in (1, 2, 3, 5, 7, 10, 15):
            ll, acc = [], []
            for _n, sc, groups, band, nsl, ntok in data:
                p = posterior(sc, groups, nsl, beta, ntok, per_token)
                ll.append(-np.log(max(p[band], 1e-6)))
                acc.append(int(np.argmax(p) == band))
            print(f"  {'sum-logprob' if per_token else 'mean/token'} beta {beta:4.1f}: log-loss {np.mean(ll):.3f}  top-1 {np.mean(acc):.1%}")
    # calibration at the best beta of the mean/token family (pick by log-loss)
    best = min(((b, np.mean([-np.log(max(posterior(sc, g, n, b)[band], 1e-6)) for _x, sc, g, band, n, _t in data]))
                for b in (1, 2, 3, 4, 5, 7, 10)), key=lambda x: x[1])[0]
    print(f"\ncalibration at beta {best} (mean/token):")
    bins = collections.defaultdict(list)
    ebins = collections.defaultdict(list)
    for _n, sc, groups, band, nsl, _t in data:
        p = posterior(sc, groups, nsl, best)
        top = int(np.argmax(p))
        bins[min(9, int(p[top] * 10))].append(top == band)
        nz = p[p > 0]
        h = float(-(nz * np.log(nz)).sum() / np.log(max(2, nsl - 1)))
        ebins[min(9, int(h * 10))].append(top == band)
    print("  max posterior -> right   |   normalised entropy -> top right")
    for k in range(10):
        a, b = bins.get(k, []), ebins.get(k, [])
        print(f"  {k/10:.1f}-{(k+1)/10:.1f}: {np.mean(a) if a else float('nan'):5.2f} (n={len(a):4d})   |   "
              f"{k/10:.1f}-{(k+1)/10:.1f}: {np.mean(b) if b else float('nan'):5.2f} (n={len(b):4d})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
