"""Calibrate the learning look-back's per-pass confidence against the goldens.

    uv run --project sidecar python research/eval/calibrate_lookback.py [--set live|cached|both]

A pass is one stretch the look-back places on one slide. It is *right* when
the golden showed that slide (identical-text copies count as one) for most of
the pass, from GOLDEN_LEAD_S before its first word to its last.

Two word sources, never pooled into one claim:

* ``live`` — the words a live session actually heard, from the 8 follower
  traces (`build/spike_music_only`, see `multi_replay.CASES`): what the
  look-back gets in a service.
* ``cached`` — whole-file Whisper (`transcript.json`) on the approved goldens:
  cleaner than live, so an optimistic check.

Fits a monotone map confidence -> P(right) (pool-adjacent-violators on
confidence bins) and scores it leave-one-recording-out: every recording's
passes are judged by a map fitted without it (Plan: held-out validation —
pooled in-fit numbers on this few recordings have reversed decisions before).
Prints the reliability table, the held-out calibration error, and the
confidence at which calibrated P(right) reaches each cutoff.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "sidecar"))
sys.path.insert(0, str(REPO / "research" / "spikes" / "music_only"))

from auto_presenter.asr.types import Word  # noqa: E402
from auto_presenter.learning.inspect import GOLDEN_LEAD_S, inspect_lookback, words_for  # noqa: E402
from auto_presenter.learning.session import identical_canon  # noqa: E402
from auto_presenter.recording.golden import effective_ground_truth, load_golden  # noqa: E402
from auto_presenter.recording.sink import load_recording_metadata  # noqa: E402

RECORDINGS = Path.home() / "Library/Application Support/com.churchslides.app/recordings"
#: Confidence bin edges for the reliability table and the fit.
EDGES = np.array([0.0, 0.4, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 0.98, 1.0001])


def majority_right(pass_, gold, item_idx, canon) -> bool | None:
    """Did the golden show the pass's slide for most of the pass?"""
    a, b = pass_["t_start"] - GOLDEN_LEAD_S, max(pass_["t_end"], pass_["t_start"] + 0.5)
    shown: dict[int, float] = {}
    cur = None
    for g in [*gold, None]:
        end = b if g is None else min(b, g.t_seconds)
        if cur is not None and cur.item_idx == item_idx and end > a:
            shown[canon(cur.slide_idx)] = shown.get(canon(cur.slide_idx), 0.0) + max(
                0.0, end - max(a, cur.t_seconds)
            )
        if g is None or g.t_seconds >= b:
            break
        cur = g
    if not shown:
        return None
    return max(shown, key=shown.__getitem__) == canon(pass_["slide"])


def passes_for(rec, golden, words, name: str) -> list[dict]:
    gold = sorted(golden, key=lambda g: g.t_seconds)
    out = []
    for item in inspect_lookback(rec, golden, words):
        snap = next(s for s in rec.setlist_content if s.item_idx == item["item_idx"])
        canon = identical_canon([list(s) for s in snap.slides])
        for p in item["passes"]:
            right = majority_right(p, gold, item["item_idx"], canon)
            if right is None:
                continue
            out.append(
                {"rec": name, "conf": p["confidence"], "n": p["n_words"], "right": bool(right)}
            )
    return out


def live_set() -> list[dict]:
    import diag_live_trace as d
    import multi_replay as mr
    from auto_presenter.eval.replay import load_recording

    out = []
    for name, (rdir, trace, _sheets) in mr.CASES.items():
        rec = load_recording(rdir)
        meta = load_recording_metadata(Path(rdir))
        golden = effective_ground_truth(Path(rdir), meta) if meta else rec.ground_truth
        rows = d.load_trace(trace)
        words = [
            Word(text=r["text"], t_start=r["t"], t_end=r["t"] + 0.3, confidence=1.0)
            for r in rows
            if r["e"] == "word"
        ]
        out += passes_for(rec, golden, words, f"live:{name}")
    return out


def cached_set() -> list[dict]:
    out = []
    for gpath in sorted(glob.glob(str(RECORDINGS / "rec_*/golden.json"))):
        d = Path(gpath).parent
        g = load_golden(d)
        if g is None or not g.approved or not (d / "transcript.json").exists():
            continue
        rec = load_recording_metadata(d)
        if rec is None or not rec.setlist_content:
            continue
        golden = effective_ground_truth(d, rec)
        words, _ = words_for(rec, d, golden, "cached")
        out += passes_for(rec, golden, words, f"cached:{d.name}")
    return out


def pava(conf: np.ndarray, right: np.ndarray, weight: np.ndarray | None = None):
    """Monotone (non-decreasing) P(right) per bin of EDGES; empty bins
    interpolated. Returns the per-bin values."""
    k = len(EDGES) - 1
    idx = np.clip(np.digitize(conf, EDGES) - 1, 0, k - 1)
    w = np.ones_like(conf) if weight is None else weight
    num = np.bincount(idx, weights=right * w, minlength=k)
    den = np.bincount(idx, weights=w, minlength=k)
    # Beta(1,1) prior so a tiny bin cannot read 0 or 1
    vals = list((num + 1) / (den + 2))
    wts = list(den + 2)
    blocks = [[v, wt, [i]] for i, (v, wt) in enumerate(zip(vals, wts, strict=True))]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] > blocks[i + 1][0]:
            v = (blocks[i][0] * blocks[i][1] + blocks[i + 1][0] * blocks[i + 1][1]) / (
                blocks[i][1] + blocks[i + 1][1]
            )
            blocks[i] = [v, blocks[i][1] + blocks[i + 1][1], blocks[i][2] + blocks[i + 1][2]]
            del blocks[i + 1]
            i = max(0, i - 1)
        else:
            i += 1
    out = np.zeros(k)
    for v, _w, ids in blocks:
        out[ids] = v
    return out


def apply(table: np.ndarray, conf: np.ndarray) -> np.ndarray:
    idx = np.clip(np.digitize(conf, EDGES) - 1, 0, len(table) - 1)
    return table[idx]


def report(name: str, rows: list[dict]) -> np.ndarray | None:
    if not rows:
        print(f"{name}: no passes")
        return None
    conf = np.array([r["conf"] for r in rows])
    right = np.array([r["right"] for r in rows], float)
    recs = sorted({r["rec"] for r in rows})
    print(f"\n=== {name}: {len(rows)} passes from {len(recs)} recordings, "
          f"{right.mean():.1%} right overall")
    # held-out: each recording calibrated by a fit without it
    held = np.zeros(len(rows))
    for rname in recs:
        mask = np.array([r["rec"] == rname for r in rows])
        table = pava(conf[~mask], right[~mask])
        held[mask] = apply(table, conf[mask])
    full = pava(conf, right)
    print(f"{'confidence':>12} {'passes':>7} {'right':>7} {'fit':>6} {'held-out pred':>14}")
    for i in range(len(EDGES) - 1):
        m = (conf >= EDGES[i]) & (conf < EDGES[i + 1])
        if not m.any():
            continue
        print(f"{EDGES[i]:5.2f}-{min(1, EDGES[i + 1]):4.2f} {m.sum():7d} {right[m].mean():7.1%} "
              f"{full[i]:6.2f} {held[m].mean():14.2f}")
    brier_raw = np.mean((conf - right) ** 2)
    brier_cal = np.mean((held - right) ** 2)
    print(f"Brier (lower is better): raw confidence {brier_raw:.3f}, calibrated held-out {brier_cal:.3f}")
    for cut in (0.6, 0.7, 0.8, 0.9):
        kept = held >= cut
        if kept.any():
            print(f"  cutoff P>={cut:.1f} (held-out): keeps {kept.mean():.0%} of passes, "
                  f"{right[kept].mean():.1%} of them right")
    print("  by recording (held-out, P>=0.7):")
    for rname in recs:
        mask = np.array([r["rec"] == rname for r in rows])
        kept = mask & (held >= 0.7)
        print(f"    {rname:40s} passes {mask.sum():4d}  right {right[mask].mean():6.1%}  "
              f"kept {kept.sum():4d} -> right {right[kept].mean() if kept.any() else float('nan'):6.1%}")
    return full


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="both", choices=["live", "cached", "both"])
    ap.add_argument("--json", type=Path, help="write the live fit (edges, table) here")
    args = ap.parse_args()
    live = live_set() if args.set in ("live", "both") else []
    cached = cached_set() if args.set in ("cached", "both") else []
    fit = report("LIVE words (what a service hears)", live)
    report("CACHED whole-file Whisper on approved goldens (optimistic)", cached)
    if args.json and fit is not None:
        args.json.write_text(json.dumps({"edges": EDGES.tolist(), "table": fit.round(3).tolist()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
