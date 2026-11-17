"""Refit the singing-vs-talking score in `decision/singing.py`.

  uv run --project sidecar python research/eval/fit_singing.py \\
      --features features.json

The detector scores each transcript event on two signals — the word rate over a
trailing window, and the filter's `evidence` term — combined by a balanced
logistic fit. This script produces the constants at the top of
`auto_presenter/decision/singing.py` and, more importantly, reports how well
they hold up on recordings left out of the fit.

`features.json` is a list of per-event objects:

    {"rid": "<recording id>", "t": 12.5, "rate": 1.25,
     "ev": 0.27, "talk": false, "warm": true}

where `rid` groups events by recording (the unit that gets held out), `rate` is
words/second over the trailing window, `ev` is the filter's evidence, `talk` is
the ground-truth label, and `warm` is false for the first few seconds before the
rate window has filled.

Fit on everything, but *judge* on the held-out columns: with a single speech
recording in the set, the in-fit detection rate read 94% while the same speaker
held out read 13%. Five speakers is the current minimum for a believable number.

The speech side of the current fit is five LibriVox sermon readings, which are
public domain. They are not in the repo — 120 s of 16 kHz mono each — but they
are reproducible, taken from 45 s in to skip the spoken LibriVox boilerplate:

    sermons_ephesians_2108_librivox     Calvin on Ephesians
    spurgeons_sermons_librivox          Spurgeon, sermon 192
    sermon_on_the_mount_librivox        Sermon on the Mount
    twogodlysermons_2107_librivox       Calvin, two sermons
    purityofheart_2007_librivox         Booth, Purity of Heart

    curl -L -o x.mp3 https://archive.org/download/<identifier>/<file>.mp3
    ffmpeg -ss 45 -t 120 -i x.mp3 -ac 1 -ar 16000 -c:a pcm_s16le x.wav

The singing side is the six approved goldens plus four of the user's own
recordings that contain real spoken interjections.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

#: Matches the shipped detector; change both together.
SMOOTH, ENTER, EXIT = 3, 0.80, 0.40


def fit(rows: list[dict]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Balanced logistic regression on (word rate, 1 - evidence).

    Balanced because talking is a small minority of any real service, and we
    care about both errors rather than overall accuracy.
    """
    x = np.array([[r["rate"], 1.0 - r["ev"]] for r in rows], dtype=np.float64)
    y = np.array([float(r["talk"]) for r in rows])
    mean, sd = x.mean(axis=0), x.std(axis=0)
    z = np.hstack([np.ones((len(x), 1)), (x - mean) / sd])
    weights = np.zeros(3)
    class_weight = np.where(
        y == 1, len(y) / (2 * y.sum()), len(y) / (2 * (len(y) - y.sum()))
    )
    for _ in range(4000):
        p = 1.0 / (1.0 + np.exp(-z @ weights))
        weights -= 0.5 * (z.T @ (class_weight * (p - y))) / len(y)
    return weights, mean, sd


def score(row: dict, weights: np.ndarray, mean: np.ndarray, sd: np.ndarray) -> float:
    x = (np.array([row["rate"], 1.0 - row["ev"]]) - mean) / sd
    return float(1.0 / (1.0 + np.exp(-(np.concatenate([[1.0], x]) @ weights))))


def replay(rows, weights, mean, sd) -> tuple[int, int, float | None]:
    """Run the detector's own smoothing and hysteresis over one recording."""
    history: list[float] = []
    flagged = off_book = 0
    first: float | None = None
    state = False
    for row in rows:
        history.append(score(row, weights, mean, sd))
        del history[:-SMOOTH]
        if len(history) == SMOOTH:
            mean_score = sum(history) / len(history)
            state = mean_score >= (EXIT if state else ENTER)
        flagged += 1
        off_book += state
        if state and first is None:
            first = row["t"]
    return off_book, flagged, first


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, required=True)
    args = parser.parse_args()

    rows = [r for r in json.loads(args.features.read_text()) if r.get("warm", True)]
    by_recording = defaultdict(list)
    for row in rows:
        by_recording[row["rid"]].append(row)

    weights, mean, sd = fit(rows)
    print("Paste into auto_presenter/decision/singing.py:\n")
    print(f"_BIAS = {weights[0]:.4f}")
    print(f"_W_RATE = {weights[1]:.4f}")
    print(f"_W_LOW_EVIDENCE = {weights[2]:.4f}")
    print(f"_MEAN_RATE, _SD_RATE = {mean[0]:.4f}, {sd[0]:.4f}")
    print(f"_MEAN_LOW_EVIDENCE, _SD_LOW_EVIDENCE = {mean[1]:.4f}, {sd[1]:.4f}")

    print("\nHeld out one recording at a time:\n")
    print(f"  {'recording':38s} {'label':8s} {'flagged':>9s}  noticed")
    false_alarms = singing_events = 0
    for rid in sorted(by_recording):
        held_out = [r for r in rows if r["rid"] != rid]
        hit, total, first = replay(by_recording[rid], *fit(held_out))
        talking = any(r["talk"] for r in by_recording[rid])
        when = f"{first:.0f}s" if first is not None else "never"
        print(
            f"  {rid[:38]:38s} {'talking' if talking else 'singing':8s} "
            f"{hit:4d}/{total:<4d} {when:>8s}"
        )
        if not talking:
            false_alarms += hit
            singing_events += total
    if singing_events:
        print(
            f"\n  false alarms over all singing: {false_alarms}/{singing_events} "
            f"= {false_alarms / singing_events:.1%}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
