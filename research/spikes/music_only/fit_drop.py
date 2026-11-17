"""Does the current song's music fit drop when the band leaves it?

    uv run --project sidecar python research/spikes/music_only/fit_drop.py

For every song stretch in the multi-song recordings (and each single-song
golden, for false drops), a music-only follower of that song (its key as the
follower knew it while playing it) runs over the stretch and the 60 s after.
Its predictive log-likelihood rate (trailing `WIN` s) is compared with its own
level inside the song: a *drop* is the rate falling `DROP` nats/s under the
song's running median for `HOLD` s. Reported: delay from each real song change
to the drop, and drops inside songs (false; bridges, breaks, key lifts).
"""
from __future__ import annotations

import collections
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diag_live_trace as d  # noqa: E402
import features as ft  # noqa: E402
import follow as fl  # noqa: E402
import multi_replay as mr  # noqa: E402
import song_id as si  # noqa: E402
from auto_presenter.eval.replay import load_recording  # noqa: E402
from auto_presenter.follower import score as fsc  # noqa: E402
from auto_presenter.follower import sheet  # noqa: E402
from auto_presenter.follower.reference import SegmentReference  # noqa: E402

FEATS = {"set": "set1002", "medley": "nbtb", "since": "neg_214054_974", "bml": "bml2",
         "wabn": "wabn", "rl": "rl", "komh": "komh", "isa": "isa"}


def stretches(rec, T):
    gt = sorted(rec.ground_truth, key=lambda g: g.t_seconds)
    out = []
    for i, g in enumerate(gt):
        end = gt[i + 1].t_seconds if i + 1 < len(gt) else T / si.FPS
        if out and out[-1][0] == g.item_idx:
            out[-1][2] = end
        else:
            out.append([g.item_idx, g.t_seconds, end])
    return out


def song_rate(name, rec, item, ch, pcs, win):
    pdf = mr.CASES[name][2][item]
    snap = [x for x in rec.setlist_content if x.item_idx == item][0]
    ref = SegmentReference(fsc.from_extract(mr.sheet(pdf)), snap.slides, list(snap.slide_sections))
    votes = collections.Counter(r["k"] for r in d.load_trace(mr.CASES[name][1]) if r["e"] == "key" and r["item"] == item)
    key = votes.most_common(1)[0][0] if votes else None
    if key is None:
        key, _ = sheet.estimate_key_shift(ch, ref.chord_sheet)
    ll = si.song_loglik(ref, ch, pcs, True, True, 1.0, 1.0, key=key)
    n = int(win * si.FPS)
    c = np.concatenate([[0.0], np.cumsum(ll)])
    idx = np.arange(len(ll))
    return (c[idx + 1] - c[np.maximum(0, idx + 1 - n)]) / win


def drops(rate, a, b, drop, hold, base_win=60.0):
    """Drop times after `a` (stretch start): rate under the in-song running median - drop, for hold s."""
    f0, f1 = int(a * si.FPS), min(len(rate), int(b * si.FPS))
    out, run = [], 0
    for f in range(f0 + int(30 * si.FPS), f1):  # 30 s to settle into the song
        lo = max(f0, f - int(base_win * si.FPS))
        base = np.median(rate[lo:f])
        run = run + 1 if rate[f] < base - drop else 0
        if run == int(hold * si.FPS):
            out.append(f / si.FPS)
            run = 0
    return out


def main() -> int:
    data = {}
    for name in FEATS:
        rdir = mr.CASES[name][0]
        rec = load_recording(Path(rdir))
        F = ft.load(mr.SPK / f"{FEATS[name]}_feats.npz")
        _t, pcs, ch = fl.frame_evidence(F, "mix", si.FPS, float(F["duration"]))
        sts = [s for s in stretches(rec, len(ch)) if s[0] in mr.CASES[name][2] and s[2] - s[1] > 40]
        rates = {it: song_rate(name, rec, it, ch, pcs, 10.0) for it in {s[0] for s in sts}}
        data[name] = (sts, rates, len(ch))
    for drop in (0.05, 0.1, 0.2):
        for hold in (3, 6, 10):
            hits, false, mins = [], 0, 0.0
            for _name, (sts, rates, T) in data.items():
                for k, (it, a, b) in enumerate(sts):
                    last = k == len(sts) - 1
                    end = T / si.FPS if last else min(T / si.FPS, b + 60)
                    ds = drops(rates[it], a, end, drop, hold)
                    inside = [t for t in ds if t < b - 5]
                    after = [t - b for t in ds if t >= b - 5]
                    false += len(inside)
                    mins += (b - a) / 60
                    if not last:
                        hits.append(round(after[0]) if after else None)
            print(f"drop {drop:.2f} hold {hold:2d}s: song ends detected {sum(h is not None for h in hits)}/{len(hits)} "
                  f"(after {[h for h in hits]}) | false drops inside songs {false} ({false / mins:.2f}/min)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
