"""Leave-one-song-out: choose the model's settings without fitting one song.

    uv run --project sidecar python research/spikes/music_only/cv.py [--causal]

For each feature arm, every setting in a small grid is run on all five
songs. For each song, the setting with the best mean on the *other four*
is chosen and that song's score under it is reported — so no number below
was tuned on the song it describes (memory: one sermon clip read 94% in-fit
and 13% held out).

Scored on slide-group accuracy over sung time (the pre-registered stage 4
criterion), with position-within-a-beat reported beside it.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
from pathlib import Path

import numpy as np

import evaluate as ev
import features as ft
import follow as fl
import gt
import score as sc
import stage1
import stage4

#: Cached evidence, stems and recording links for the spike (about 1.3 GB, not
#: committed): `build/spike_music_only`, or wherever SPIKE_MUSIC_DIR points.
S = Path(os.environ.get("SPIKE_MUSIC_DIR", Path(__file__).resolve().parents[3] / "build" / "spike_music_only"))
SONGS = {
    "wabn": "What A Beautiful Name-lead-D",
    "rl": "Reckless Love-lead-F#",
    "komh": "King Of My Heart-lead-Bb",
    "isa": "I Surrender All-lead-D",
    "nbtb": "Nothing But The Blood (Plainfield)-lead-G",
}
GRID = {
    "robust": [0.0, 0.2],
    "w": [1.0, 0.4],
    "p_jump": [0.03, 0.1],
}
KEY_WINDOW = 30.0  # seconds of evidence a key choice rests on (medleys change key)
ARMS = {
    # name: (pitch source, chord source, melody weight on, chord weight on)
    "stems both": ("stems", "stems", 1, 1),
    "stems melody": ("stems", "stems", 1, 0),
    "stems chords": ("stems", "stems", 0, 1),
    "vocals+mix chords": ("stems", "mix", 1, 1),
    "mix chords": ("mix", "mix", 0, 1),
    "mix melody": ("mix", "mix", 1, 0),
    # mix melody + mix chords, each observation counted once (stage6 multiplies
    # by W_EV = 0.4): melody 1/7.4 per frame, chords 1/39.2 — the weighting that
    # fixed "chords hurt" in the joint follower
    "mix both (obs)": ("mix", "mix", (1 / 7.4) / 0.4, (1 / 39.2) / 0.4),
}


#: Recordings holding several sheets' songs (a live set), by recording dir.
SETS = {
    "set1002": {
        "ag": "Awesome God-lead-Cm",
        "bml": "Build My Life-lead-G",
        "dox": "Doxology-lead-F#",
        "ocayf": "O Come All Ye Faithful (Adeste Fideles)-lead-G",
    },
}


class Song:
    def __init__(self, key: str, fps: float = 10.0, sheet: str | None = None, all_keys: bool = False):
        """`key` is the recording dir under S; `sheet` names the lead sheet when
        the recording is a set of several songs. `all_keys` searches every key
        instead of the chord histogram's top three — needed when one recording
        holds songs in different keys, where a whole-recording histogram is a
        blend of them."""
        self.key = key
        rec = S / key
        self.s = sc.load(S / f"{sheet or SONGS[key]}.score.json")
        self.slides = stage1.load_slides(rec, self.s)
        self.F = ft.load(S / f"{key}_feats.npz")
        dur = float(self.F["duration"])
        cues = gt.build(self.s, self.slides, rec, dur)
        self.tl = gt.timeline(cues, fps=fps, duration=dur, s=self.s)
        self.spans = stage1.song_spans(self.s, rec, self.slides)
        self.fps = fps
        self.ev = {}
        for src in ("stems", "mix"):
            t, pcs, ch = fl.frame_evidence(self.F, src, fps, dur)
            self.ev[src] = (t, pcs, ch)
        # key candidates from the audio: the 3 best chord-histogram shifts
        base = fl.build(self.s, self.slides, fps=fps)
        _, corr = fl.estimate_key_shift(self.ev["mix"][2], base)
        self.shifts = list(range(12)) if all_keys else [int(k) for k in np.argsort(-corr)[:3]]
        self.models = {(pj, k): fl.build(self.s, self.slides, fps=fps, p_jump=pj, key_shift=k)
                       for pj in GRID["p_jump"] for k in self.shifts}
        self.scorer = ev.Scorer(base, self.slides, self.spans)

    def run(self, arm: str, robust: float, w: float, p_jump: float, causal: bool) -> tuple[float, float]:
        """A bank of filters, one per candidate key; at each moment the key
        whose filter has the most evidence so far (causal) or in total
        (offline) supplies the answer."""
        psrc, csrc, wm, wc = ARMS[arm]
        t, pcs, _ = self.ev[psrc]
        _, _, ch = self.ev[csrc]
        # the slowest feature in use sets how far behind the room the filter is
        lag = max(stage4.LATENCY[psrc]["pitch"] if wm else 0, stage4.LATENCY[csrc]["chord"] if wc else 0)
        preds, cum = [], []
        for k in self.shifts:
            m = self.models[(p_jump, k)]
            lo = fl.log_obs(m, pcs, ch, wm * w, wc * w, robust)
            n = int(KEY_WINDOW * self.fps)
            if causal:
                marg, _ = fl.forward(m, lo)
                preds.append(fl.live_positions(m, marg, lag))
                c = np.concatenate([[0.0], np.cumsum(m.loglik)])
                i = np.arange(len(lo))
                k = int(round(lag * self.fps))  # key evidence is as late as the rest
                cum.append(c[np.maximum(0, i + 1 - k)] - c[np.maximum(0, i + 1 - k - n)])
            else:
                fl.forward(m, lo)
                c = np.concatenate([[0.0], np.cumsum(m.loglik)])
                i = np.arange(len(lo))
                cum.append(c[np.minimum(len(lo), i + n // 2)] - c[np.maximum(0, i - n // 2)])  # centred window
                preds.append(fl.smooth(m, lo).argmax(axis=1))
        best = np.argmax(np.stack(cum), axis=0)
        pred = np.stack(preds)[best, np.arange(len(best))]
        self.last_keys = [self.shifts[b] for b in best]
        self._last_pred = pred
        return self.score(pred)

    def score(self, pred: np.ndarray) -> tuple[float, float]:
        slide, pos = self.tl["slide"], self.tl["pos"]
        sung = (slide > 0) & np.isfinite(pos)
        err = self.scorer.errors(pred, pos)
        ok = np.array([slide[i] in self.scorer.cover_eq[c] for i, c in enumerate(pred)])
        return float(ok[slide > 0].mean()), float(np.mean(err[sung] <= 1.0))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--causal", action="store_true")
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    songs = {k: Song(k) for k in SONGS}
    configs = list(itertools.product(GRID["robust"], GRID["w"], GRID["p_jump"]))
    results = {}
    for arm in args.arms.split(","):
        table = np.zeros((len(configs), len(songs), 2))
        for ci, (r, w, pj) in enumerate(configs):
            for si, song in enumerate(songs.values()):
                table[ci, si] = song.run(arm, r, w, pj, args.causal)
        results[arm] = table.tolist()
        held = []
        for si, key in enumerate(songs):
            others = [j for j in range(len(songs)) if j != si]
            best = int(np.argmax(table[:, others, 0].mean(axis=1)))
            held.append((key, configs[best], table[best, si]))
        default = configs.index((0.0, 1.0, 0.03))
        print(f"\n=== {arm} ({'causal' if args.causal else 'offline'})")
        print(f"  {'song':6s} {'held-out setting (robust, w, p_jump)':40s} slide-group  pos<=1beat   | untuned (0,1,.03): slide  pos")
        for (key, cfg, (a, p)), si in zip(held, range(len(songs))):
            d = table[default, si]
            print(f"  {key:6s} {str(cfg):40s} {a:10.1%} {p:11.1%}   | {d[0]:14.1%} {d[1]:6.1%}")
        h = np.array([x[2] for x in held])
        print(f"  {'MEAN':6s} {'':40s} {h[:, 0].mean():10.1%} {h[:, 1].mean():11.1%}   | {table[default, :, 0].mean():14.1%} {table[default, :, 1].mean():6.1%}")
    if args.out:
        args.out.write_text(json.dumps({"configs": configs, "songs": list(songs), "results": results}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
