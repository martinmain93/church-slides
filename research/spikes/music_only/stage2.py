"""Stage 2: given the true alignment, does the audio agree with the sheet?

    uv run --no-project --with numpy python research/spikes/music_only/stage2.py \
        score.json REC_DIR feats.npz

Uses the transcript-derived ground truth (gt.py) as the alignment — for
scoring only — and asks, for the mix and for the stems:

1. Melody: on voiced frames, is the strongest pitch class the one the sheet
   says? And what is the offset when it is not (a key or tuning problem
   shows up as one dominant offset)?
2. Chords: does BTC's top chord match the sheet's (root + maj/min)?
3. The forced choice: over a window from a slide's first word, does the
   audio fit the true slide's music better than the next slide's? Only
   pairs whose music actually differs are scored — identical music is a
   coin toss by construction, and would only dilute the number.

Pass for stage 2 (pre-registered): melody beats the adjacent section in
>= 70% of 4 s windows. The fingerprinting spike reached 53%.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

import features as ft
import gt
import reference as ref
import score as sc
import stage1

EPS = 0.02


def melody_frame_score(pcs: np.ndarray, midi: np.ndarray) -> np.ndarray:
    """Per frame: log(salience at the expected pitch class / mean salience).
    Zero where the sheet has a rest."""
    out = np.zeros(len(midi), np.float32)
    has = midi >= 0
    pc = np.where(has, midi % 12, 0)
    e = pcs[np.arange(len(midi)), pc]
    out[has] = np.log(EPS + e[has]) - np.log(EPS + pcs[has].mean(axis=1))
    return out


def chord_frame_score(probs: np.ndarray, chord: np.ndarray) -> np.ndarray:
    out = np.zeros(len(chord), np.float32)
    has = chord >= 0
    p = probs[np.arange(len(chord)), np.where(has, chord, 0)]
    out[has] = np.log(1e-3 + p[has]) - np.log(1e-3 + 1 / 25)
    return out


def resample(times: np.ndarray, values: np.ndarray, at: np.ndarray) -> np.ndarray:
    idx = np.clip(np.searchsorted(times, at), 0, len(times) - 1)
    return values[idx]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("score", type=Path)
    ap.add_argument("recording", type=Path)
    ap.add_argument("feats", type=Path)
    ap.add_argument("--fps", type=float, default=20.0)
    args = ap.parse_args()
    rec = args.recording.expanduser()
    s = sc.load(args.score)
    slides = stage1.load_slides(rec, s)
    F = ft.load(args.feats)
    dur = float(F["duration"])
    cues = gt.build(s, slides, rec, dur)
    tl = gt.timeline(cues, fps=args.fps, duration=dur, s=s)
    R = ref.Reference(s)
    t, pos = tl["t"], tl["pos"]
    exp_midi, exp_chord = R.at(pos)

    spans = stage1.song_spans(s, rec, slides)
    cov = {si: set().union(*(sc.coverage(s, sp) for sp in sps)) if sps else set() for si, sps in spans.items()}

    sources = [k[:-8] for k in F if k.endswith("_pitch_t")]
    print(f"GT: {np.isfinite(pos).mean():.0%} of frames positioned; song tempo {60 * float(tl['rate']):.1f} bpm\n")
    for src in sources:
        pcs_all = ref.pc_salience(F[f"{src}_pitch"])
        pcs = resample(F[f"{src}_pitch_t"], pcs_all, t)
        probs = resample(F[f"{src}_chord_t"], F[f"{src}_chord"].astype(np.float32), t)
        voiced = pcs.max(axis=1) >= 0.3
        print(f"=== {src} (pitch from {'vocals' if src == 'stems' else 'mix'}, chords from {'other' if src == 'stems' else 'mix'})")
        m = (exp_midi >= 0) & voiced
        top = pcs.argmax(axis=1)
        off = (top[m] - exp_midi[m] % 12) % 12
        hist = np.bincount(off, minlength=12) / max(1, m.sum())
        print(f"  melody: voiced frames with a sheet note {m.sum()} ({m.sum() / max(1, (exp_midi >= 0).sum()):.0%} of sheet-note frames)")
        print(f"    top pitch class == sheet: {hist[0]:.1%}   within 1 semitone: {hist[0] + hist[1] + hist[11]:.1%}   chance 8.3%")
        print("    offset histogram (semitones, top - sheet): " + " ".join(f"{k}:{hist[k]:.0%}" for k in np.argsort(-hist)[:5]))
        # rests: is the voice quieter where the sheet rests?
        rest = (exp_midi < 0) & np.isfinite(pos)
        print(f"    voiced share: on sheet notes {voiced[(exp_midi >= 0)].mean():.0%}, on sheet rests {voiced[rest].mean():.0%}")
        c = exp_chord >= 0
        btc = probs.argmax(axis=1)
        print(f"  chords: top == sheet {np.mean(btc[c] == exp_chord[c]):.1%} of {c.sum()} frames (chance ~1/7 for this song's chord set)")

        mel = melody_frame_score(pcs, exp_midi)
        print("  forced choice, true slide vs the next musically different slide:")
        for W in (2, 4, 6, 8):
            res = {"melody": [], "chords": [], "both": []}
            ranks = []
            for i, cue in enumerate(cues):
                if cue.span is None:
                    continue
                f = gt.fit(cue, float(tl["rate"]), float(tl["lead"]))
                icpt, slope = f
                t_first = (cue.span.start - icpt) / slope
                w = (t >= t_first) & (t < t_first + W) & np.isfinite(pos)
                if w.sum() < W * args.fps * 0.5:
                    continue
                # the next cue in the performance whose music differs from this one
                alt = None
                for nxt in cues[i + 1 :]:
                    if nxt.span is not None and len(cov[nxt.slide] & cov[cue.slide]) < 0.5 * len(cov[cue.slide]):
                        alt = nxt
                        break
                if alt is None:
                    continue
                apos = alt.span.start + (pos[w] - cue.span.start)
                am, ac = R.at(apos)
                tm, tc = exp_midi[w], exp_chord[w]
                sm = (melody_frame_score(pcs[w], tm).mean(), melody_frame_score(pcs[w], am).mean())
                sch = (chord_frame_score(probs[w], tc).mean(), chord_frame_score(probs[w], ac).mean())
                res["melody"].append(sm[0] > sm[1])
                res["chords"].append(sch[0] > sch[1])
                res["both"].append(sm[0] + sch[0] > sm[1] + sch[1])
                # rank among every musically different slide
                beats = []
                for sj, sps in spans.items():
                    if not sps or sj == cue.slide or len(cov[sj] & cov[cue.slide]) >= 0.5 * len(cov[cue.slide]):
                        continue
                    for sp in sps:
                        opos = sp.start + (pos[w] - cue.span.start)
                        om, _ = R.at(opos)
                        beats.append(sm[0] > melody_frame_score(pcs[w], om).mean())
                ranks.append(np.mean(beats) if beats else np.nan)
            n = len(res["melody"])
            print(f"    {W} s: melody {np.mean(res['melody']):.0%}  chords {np.mean(res['chords']):.0%}  both {np.mean(res['both']):.0%}"
                  f"   (n={n});  melody beats {np.nanmean(ranks):.0%} of all other slides")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
