"""Bass spike, step 4 (audio side): does the bass change on the LAST pass of a repeated section?

    uv run --project sidecar python research/spikes/music_only/bass_repeats.py

For a few long repeats, prints a bar-by-bar grid: one row per bar (from the
recording's Beat This downbeats), one cell per beat = the bass pitch class
read off the mix (and, beside it, BTC's chord root), with the golden slide.
Then, per bar position inside the repeating phrase, compares the final pass to
the earlier ones: a "last-pass change" is a beat whose bass on the final pass
differs from what EVERY earlier pass played there.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bass_lib as bl  # noqa: E402
import features as ft  # noqa: E402

N = bl.NAMES
#: name: (case, beats file, t0, t1, phrase length in bars, note)
WINDOWS = {
    "komh chorus repeats": ("komh", "komh", 160.0, 290.0, 4, "slide 7 166.7-222.3, slide 3, slide 8 251.3-278.1"),
    "since chorus x4": ("since", "since", 170.0, 236.0, 4, "slides 4/5 alternate 176.5-230.3"),
    "since ending vamp": ("since", "since", 320.0, 482.0, 4, "slide 6 from 327.5 to the end; sheet: F#/E pedal on repeats, E/G# F#/A# walk-up last time"),
    "medley nothing but the blood": ("medley", "medley", 540.0, 700.0, 4, "NBTB verses 547-621, slide 4 'nothing but the blood of jesus' from 621.1"),
}


def golden_slides(case):
    g = json.loads((bl.S / bl.CASES[case][0] / "golden.json").read_text())
    return [(c["t_seconds"], c.get("item_idx", 0), c["slide_idx"]) for c in g["ground_truth"]]


def slide_at(cues, t):
    cur = None
    for c in cues:
        if c[0] <= t:
            cur = c
    return f"{cur[1]}:{cur[2]}" if cur else "-"


def main():
    L = []
    for title, (case, bfile, t0, t1, plen, note) in WINDOWS.items():
        rdir = bl.S / bl.CASES[case][0]
        b = json.loads((bl.S / f"beats_{bfile}.json").read_text())
        beats = np.array(b["beats"])
        downs = np.array(b["downbeats"])
        dur = float(ft.load(bl.S / f"{bl.FEATS[case]}_feats.npz")["duration"])
        t = np.arange(0, dur, 1 / bl.FPS)
        x = bl.load_audio(rdir / "audio.wav")
        st = bl.load_audio(rdir / "stems/bass.wav")
        F = ft.load(bl.S / f"{bl.FEATS[case]}_feats.npz")
        mix = bl.stft_bass_chroma(x, t, causal=False, n_fft=8192, harmonic=False)
        stem = bl.stft_bass_chroma(st, t, causal=False, n_fft=8192, harmonic=False)
        btc = bl.btc_root_chroma(F, "mix", t)
        cues = golden_slides(case)
        L.append(f"## {title}  ({note})")
        L.append("bar start | slide | bass per beat (mix) | bass per beat (Demucs stem) | BTC root per beat")
        L.append("---|---|---|---|---")
        bars = [d for d in downs if t0 <= d < t1]
        grid = []
        for i, d0 in enumerate(bars):
            d1 = bars[i + 1] if i + 1 < len(bars) else d0 + np.median(np.diff(downs))
            bt = [x_ for x_ in beats if d0 - 1e-3 <= x_ < d1 - 1e-3]
            cells = {"mix": [], "stem": [], "btc": []}
            for j, b0 in enumerate(bt):
                b1 = bt[j + 1] if j + 1 < len(bt) else d1
                a_, z_ = int(b0 * bl.FPS), max(int(b0 * bl.FPS) + 1, int(b1 * bl.FPS))
                for k, X in (("mix", mix), ("stem", stem), ("btc", btc)):
                    v = X[a_:z_].sum(axis=0)
                    cells[k].append(int(np.argmax(v)))
            grid.append((d0, slide_at(cues, d0), cells))
            L.append(f"{d0:6.1f} | {slide_at(cues, d0)} | {' '.join(f'{N[c]:2s}' for c in cells['mix'])} | "
                     f"{' '.join(f'{N[c]:2s}' for c in cells['stem'])} | {' '.join(f'{N[c]:2s}' for c in cells['btc'])}")
        L.append("")
        print("\n".join(L[-len(bars) - 4:]), flush=True)
    (bl.OUT / "repeats.md").write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
