"""Does the chord feature earn more when the WORDS are worse?

The strongest objection to the small gain measured on cached transcripts:
`eval_harness_fidelity` established that cached offline transcripts understate
error by about 2x against live ASR. If harmony pays off mainly when lyrics are
degraded, then the cached measurement understates the fusion gain and the
conclusion flips.

So degrade the word stream deliberately -- drop a fraction of words and corrupt
another fraction into other vocabulary words -- and watch the ORACLE chord gain
as a function of word quality. Oracle, not BTC, because the question here is
about the ceiling, not the detector.
"""
import os
import sys

import numpy as np

SC = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SC)
import fusion4 as F  # noqa: E402

recs = [r for r in (F.build(rid, n, nd) for rid, n, nd in F.GOLDEN_MAP) if r]
vocab = sorted({w for r in recs for sl in r["slides"].values() for w in sl})


def degrade(rec, drop, corrupt, seed):
    rng = np.random.default_rng(seed)
    out = []
    for w, ts, btc, orc in rec["words"]:
        u = rng.random()
        if u < drop:
            continue
        if u < drop + corrupt:
            w = vocab[rng.integers(len(vocab))]
        out.append((w, ts, btc, orc))
    return dict(rec, words=out)


print("word stream degraded; chord arm is the ORACLE (ceiling), lambda 0.5\n")
print(f"{'drop':>5s} {'corrupt':>8s} | {'words only':>11s} {'+oracle':>8s} "
      f"{'delta':>7s} | {'ambig words':>12s} {'+oracle':>8s} {'delta':>7s}")
print("-" * 78)
for drop, corrupt in ((0.0, 0.0), (0.15, 0.10), (0.30, 0.20), (0.45, 0.25),
                      (0.60, 0.30)):
    ball, bamb, oall, oamb = [], [], [], []
    for seed in range(3):
        degraded = [degrade(r, drop, corrupt, seed) for r in recs]
        B = [x for r in degraded for x in F.evaluate(r, 0.0, "btc")]
        O = [x for r in degraded for x in F.evaluate(r, 0.5, "oracle")]
        ball.append(np.mean([w for w, _, _ in B]))
        bamb.append(np.mean([w for w, _, a in B if a] or [np.nan]))
        oall.append(np.mean([f for _, f, _ in O]))
        oamb.append(np.mean([f for _, f, a in O if a] or [np.nan]))
    ba, bm = np.mean(ball), np.nanmean(bamb)
    oa, om = np.mean(oall), np.nanmean(oamb)
    print(f"{drop:5.0%} {corrupt:8.0%} | {ba:10.1%} {oa:8.1%} {oa - ba:+7.1%} | "
          f"{bm:11.1%} {om:8.1%} {om - bm:+7.1%}")
