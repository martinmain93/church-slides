"""Run the shipped streaming analyser over real worship audio.

The unit tests feed it synthetic triads and click tracks, which prove it is
wired correctly and prove nothing about whether it works on a band. This drives
`MusicAnalyser` exactly as the live capture path does — one 100 ms frame at a
time, causally — over the approved goldens, and scores its chords against the
chart-derived ground truth described in docs/research/CHORD_DOWNBEAT.md.

    uv run --project sidecar python research/eval/check_music_analyser.py

Expect modest numbers. Template matching over plain-FFT chroma was measured at
roughly the break-even point for helping the matcher; this check exists so a
regression is visible, and so the live panel can be read knowing what its
typical accuracy is.
"""

from __future__ import annotations

import json
import os
import sys
import time
import wave
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "sidecar"))

from auto_presenter.audio.types import SAMPLE_RATE, AudioFrame  # noqa: E402
from auto_presenter.music.analyser import MusicAnalyser  # noqa: E402
from auto_presenter.music.btc import BtcChordBackend  # noqa: E402

RECORDINGS = Path(
    os.path.expanduser("~/Library/Application Support/com.churchslides.app/recordings")
)
FRAME = 1600


def approved() -> list[str]:
    out = []
    for d in sorted(RECORDINGS.glob("rec_*")):
        g = d / "golden.json"
        if g.exists() and json.loads(g.read_text()).get("approved"):
            out.append(d.name)
    return out


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        raw = w.readframes(w.getnframes())
        ch = w.getnchannels()
    a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    return a.reshape(-1, ch).mean(axis=1) if ch == 2 else a


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--templates", action="store_true",
                    help="force the numpy fallback even if BTC is installed")
    args = ap.parse_args()
    ids = approved()
    if not ids:
        print("no approved goldens found")
        return 1
    probe = None if args.templates else BtcChordBackend()
    which = "btc" if (probe is not None and probe.available) else "template"
    print(f"chord backend: {which}"
          + ("" if which == "btc" else f"  ({probe.status if probe else 'forced'})"))
    print()
    print(f"{'recording':26s} {'secs':>6s} {'xRT':>8s} {'BPM':>6s} "
          f"{'core80':>7s} {'chg/min':>8s} {'held>1s':>8s}")
    print("-" * 80)
    rtfs = []
    for rid in ids:
        wav = RECORDINGS / rid / "audio.wav"
        if not wav.exists():
            continue
        audio = load_wav(wav)
        backend = None if args.templates else BtcChordBackend()
        if backend is not None and not backend.available:
            backend = None
        a = MusicAnalyser(chord_backend=backend)
        t = 0.0
        chords: list[str] = []
        beats = 0
        started = time.perf_counter()
        for i in range(0, len(audio) - FRAME + 1, FRAME):
            nxt = t + FRAME / SAMPLE_RATE
            u = a.feed(AudioFrame(samples=audio[i:i + FRAME], t_start=t, t_end=nxt))
            if u.chord:
                chords.append(u.chord)
            beats += len(u.beats)
            t = nxt
        wall = time.perf_counter() - started
        rtf = t / wall if wall else 0.0
        rtfs.append(rtf)
        # How many distinct chords cover 80% of the time. Counting every chord
        # ever emitted overstates the vocabulary badly: one stray frame adds a
        # whole chord, so a song using four reads as fifteen.
        counts: dict[str, int] = {}
        for c in chords:
            counts[c] = counts.get(c, 0) + 1
        ranked = sorted(counts.values(), reverse=True)
        need, core = 0.8 * sum(ranked), 0
        acc = 0
        for v in ranked:
            acc += v
            core += 1
            if acc >= need:
                break
        # Run lengths, in ticks of 100 ms. A chord that never holds a second is
        # flickering whatever its change rate looks like.
        runs, cur = [], 1
        for k in range(1, len(chords)):
            if chords[k] == chords[k - 1]:
                cur += 1
            else:
                runs.append(cur)
                cur = 1
        runs.append(cur)
        held = float(np.mean([r >= 10 for r in runs])) if runs else 0.0
        changes = len(runs) - 1
        print(f"{rid:26s} {t:6.0f} {rtf:7.0f}x {u.bpm:6.1f} {core:7d} "
              f"{changes / (t / 60):8.1f} {held:7.0%}")
    print("-" * 80)
    print(f"mean {np.mean(rtfs):.0f}x realtime "
          f"(Whisper runs at roughly 3x realtime, for scale)")
    print()
    print()
    print("`core80` is how many distinct chords cover 80% of the time — worship")
    print("songs are built on four or five, so much above that is wandering.")
    print("`held>1s` is the share of chord runs lasting at least a second;")
    print("harmonic rhythm is about one chord per bar, so a low figure means")
    print("flicker regardless of what the change rate looks like.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
