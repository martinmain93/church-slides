"""Does the musical premise hold? Measured on externally annotated corpora.

Spike, 2026-09-08. Companion to `spike_chord_downbeat.py`, which does the
audio side. Nothing here touches the sidecar.

The question this answers is deliberately *not* about our audio: it is whether
slide-sized musical sections land on downbeats at all, and whether chord changes
mark them. Using corpora with human beat, downbeat, chord and section
annotations separates the musical claim from whether our detector can see it —
which matters, because the detector turned out to be the weak link.

Two corpora, chosen because their annotations were produced by different people
for different purposes:

  Harmonix Set (Nieto et al., ISMIR 2019) — 912 pop tracks, beats, downbeats,
  functional segments. MIT-licensed annotations, no audio required.
      git clone https://github.com/urinieto/harmonixset

  Isophonics Beatles (Harte 2010; beats by Davies) — 180 tracks with chords,
  beats and segments annotated by separate efforts, so unlike Harmonix nothing
  can be an artifact of one tool snapping everything to one grid.
      curl -O 'http://isophonics.net/files/annotations/The Beatles Annotations.tar.gz'

    uv run --project sidecar python research/spikes/spike_metre_corpus.py \
        --beatles <dir> --harmonix <dir>
"""
from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict

import numpy as np

NON_SECTION = {"silence", "end", "applause", "noise"}


def _nearest(arr: np.ndarray, xs: np.ndarray) -> np.ndarray:
    if len(arr) < 2 or len(xs) == 0:
        return np.full(len(xs), 9.9)
    i = np.clip(np.searchsorted(arr, xs), 1, len(arr) - 1)
    return np.minimum(np.abs(arr[i] - xs), np.abs(arr[i - 1] - xs))


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

def _walk(root: str, sub: str, ext: str) -> dict[str, str]:
    out = {}
    for dp, _, fns in os.walk(os.path.join(root, sub)):
        for fn in fns:
            if fn.endswith(ext):
                out[fn.rsplit(".", 1)[0]] = os.path.join(dp, fn)
    return out


def load_beatles(root: str):
    bf = _walk(root, "beat", ".txt")
    sf = _walk(root, "seglab", ".lab")
    cf = _walk(root, "chordlab", ".lab")
    for k in sorted(set(bf) & set(sf) & set(cf)):
        beats, pos = [], []
        for line in open(bf[k]):
            p = line.split()
            if len(p) < 2:
                continue
            try:
                t, b = float(p[0]), int(float(p[1]))
            except ValueError:
                continue
            beats.append(t)
            pos.append(b)
        beats, pos = np.array(beats), np.array(pos)
        if len(beats) < 50:
            continue
        # Metre is the highest bar position that *recurs*, not the maximum,
        # which one stray annotation would set.
        counts = np.bincount(pos, minlength=8)
        metre = max([i for i in range(1, 8) if counts[i] > 0.2 * counts[1]] or [0])
        if metre not in (3, 4):
            continue
        db = beats[pos == 1]
        if len(db) < 8:
            continue
        bounds, labels = [], []
        for line in open(sf[k]):
            p = line.split()
            if len(p) < 3 or p[-1] in NON_SECTION:
                continue
            bounds.append(float(p[0]))
            labels.append(p[-1])
        ct, cl = [], []
        for line in open(cf[k]):
            p = line.split()
            if len(p) < 3:
                continue
            ct.append(float(p[0]))
            cl.append(p[2])
        ct = np.array(ct)
        chg = ct[1:][np.array([cl[i] != cl[i - 1] for i in range(1, len(cl))])] \
            if len(cl) > 1 else np.array([])
        yield k, beats, db, metre, np.array(bounds), labels, chg


def load_harmonix(root: str):
    bd = os.path.join(root, "dataset", "beats_and_downbeats")
    sg = os.path.join(root, "dataset", "segments")
    meta = {r["File"]: r for r in
            csv.DictReader(open(os.path.join(root, "dataset", "metadata.csv")))}
    for fn in sorted(os.listdir(bd)):
        nm = fn.rsplit(".", 1)[0]
        sp = os.path.join(sg, nm + ".txt")
        if not os.path.exists(sp) or meta.get(nm, {}).get("Time Signature") != "4|4":
            continue
        beats, pos = [], []
        for line in open(os.path.join(bd, fn)):
            p = line.split()
            if len(p) < 3:
                continue
            beats.append(float(p[0]))
            pos.append(int(p[1]))
        beats, pos = np.array(beats), np.array(pos)
        if len(beats) < 50:
            continue
        bl = [(float(p[0]), p[1]) for p in (ln.split() for ln in open(sp)) if len(p) >= 2]
        bl = [(t, lab) for t, lab in bl if lab not in NON_SECTION]
        if len(bl) < 4:
            continue
        yield (nm, beats, beats[pos == 1], 4,
               np.array([t for t, _ in bl]), [lab for _, lab in bl],
               np.array([]), meta.get(nm, {}).get("Genre", "?"))


# --------------------------------------------------------------------------
# measurements
# --------------------------------------------------------------------------

def alignment(songs, name: str, has_chords: bool) -> None:
    rng = np.random.default_rng(0)
    S = defaultdict(list)
    for row in songs:
        k, beats, db, metre, bounds, labels, chg = row[:7]
        bounds = bounds[(bounds > beats[0]) & (bounds < beats[-1])]
        if len(bounds) < 3 or len(db) < 8:
            continue
        ibi = float(np.median(np.diff(beats)))
        tol = min(0.10, ibi * 0.25)
        fake = rng.uniform(beats[0], beats[-1], max(200, len(bounds) * 40))
        S["n"].append(1)
        S["ibi"].append(ibi)
        S["b_beat"].append((_nearest(beats, bounds) < tol).mean())
        S["n_beat"].append((_nearest(beats, fake) < tol).mean())
        S["b_down"].append((_nearest(db, bounds) < tol).mean())
        S["n_down"].append((_nearest(db, fake) < tol).mean())
        onb = _nearest(beats, bounds) < tol
        if onb.sum():
            S["cond"].append((_nearest(db, bounds)[onb] < tol).mean())
            S["chance"].append(1.0 / metre)
        if has_chords and len(chg) > 4:
            chg = chg[(chg > beats[0]) & (chg < beats[-1])]
            mins = (beats[-1] - beats[0]) / 60.0
            S["b_chord"].append((_nearest(chg, bounds) < tol).mean())
            S["n_chord"].append((_nearest(chg, fake) < tol).mean())
            S["chord_down"].append((_nearest(db, chg) < tol).mean())
            S["chord_rate"].append(len(chg) / mins)
            S["bound_rate"].append(len(bounds) / mins)
            S["chord_prec"].append((_nearest(bounds, chg) < 1.0).mean())

    print(f"\n### {name} — {int(sum(S['n']))} songs, "
          f"median {60 / np.median(S['ibi']):.0f} BPM")
    print("tolerance is a quarter beat, capped at 100 ms\n")
    print(f"{'':38s} {'observed':>9s} {'null':>8s}")
    print(f"{'section boundary on a beat':38s} {np.mean(S['b_beat']):8.1%} "
          f"{np.mean(S['n_beat']):8.1%}")
    print(f"{'section boundary on a DOWNBEAT':38s} {np.mean(S['b_down']):8.1%} "
          f"{np.mean(S['n_down']):8.1%}")
    if S["b_chord"]:
        print(f"{'section boundary at a chord change':38s} {np.mean(S['b_chord']):8.1%} "
              f"{np.mean(S['n_chord']):8.1%}")
        print(f"{'chord change on a downbeat':38s} {np.mean(S['chord_down']):8.1%} "
              f"{np.mean(S['n_down']):8.1%}")
    print(f"\ngiven a boundary is on a beat, it is a downbeat: "
          f"{np.mean(S['cond']):.1%} (chance {np.mean(S['chance']):.1%})")
    if S["chord_rate"]:
        print(f"\nchord changes {np.mean(S['chord_rate']):.1f}/min against section "
              f"boundaries {np.mean(S['bound_rate']):.1f}/min")
        print(f"if every chord change alerted, share within 1 s of a boundary: "
              f"{np.mean(S['chord_prec']):.1%}")


def section_lengths(songs, name: str) -> None:
    per_song = {}
    for row in songs:
        k, beats, db, metre, bounds, labels, _chg = row[:7]
        if len(db) < 8 or len(bounds) < 4:
            continue
        bars = np.interp(bounds, db, np.arange(len(db)))
        byl = defaultdict(list)
        for i, lab in enumerate(labels[:-1]):
            L = bars[i + 1] - bars[i]
            if 0.5 < L < 64:
                byl[lab].append(L)
        per_song[k] = byl

    same, diff, within = [], [], []
    lens = []
    for byl in per_song.values():
        allv = [v for vs in byl.values() for v in vs]
        lens += allv
        for vs in byl.values():
            if len(vs) < 2:
                continue
            a = np.array(vs)
            same += list(np.abs(a[:, None] - a[None, :])[np.triu_indices(len(a), 1)])
            within.append(np.mean(np.abs(a - np.median(a)) < 0.6))
        if len(allv) > 2:
            a = np.array(allv)
            diff += list(np.abs(a[:, None] - a[None, :])[np.triu_indices(len(a), 1)])
    lens = np.array(lens)
    print(f"\n### {name} — section length in bars, {len(lens)} sections")
    print(f"median {np.median(lens):.1f} bars, "
          f"IQR {np.percentile(lens, 25):.1f}-{np.percentile(lens, 75):.1f}")
    for k in (1, 2, 4, 8):
        r = np.abs(lens / k - np.round(lens / k)) * k
        print(f"  within 0.2 bar of a multiple of {k:2d} bars: {np.mean(r < 0.2):6.1%}")
    print(f"when the SAME section recurs, |length difference|: "
          f"median {np.median(same):.2f} bars, identical {np.mean(np.array(same) < 0.2):.1%}")
    print(f"  ... between any two sections of the same song (null): "
          f"median {np.median(diff):.2f} bars, within 1 bar {np.mean(np.array(diff) < 1.0):.1%}")
    print(f"occurrences within 0.6 bar of that label's median: {np.mean(within):.1%}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--beatles", default="")
    ap.add_argument("--harmonix", default="")
    a = ap.parse_args()
    if a.beatles:
        songs = list(load_beatles(a.beatles))
        alignment(songs, "Isophonics Beatles", has_chords=True)
        section_lengths(songs, "Isophonics Beatles")
    if a.harmonix:
        songs = list(load_harmonix(a.harmonix))
        alignment(songs, "Harmonix Set", has_chords=False)
        section_lengths(songs, "Harmonix Set")
    if not (a.beatles or a.harmonix):
        ap.error("give --beatles and/or --harmonix")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
