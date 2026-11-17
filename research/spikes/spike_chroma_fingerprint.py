"""Harmonic section fingerprints: chroma -> CENS -> 2D Fourier magnitude.

Spike, 2026-09-01. Not wired into the sidecar. Reproduces the measurements in
docs/research/MUSIC_FINGERPRINTING.md; run it with `--verify` to re-check that each stage
does what its source claims before trusting any accuracy number from it.

    uv run --project sidecar python research/spikes/spike_chroma_fingerprint.py --verify
    uv run --project sidecar python research/spikes/spike_chroma_fingerprint.py --matrix

Three stages, each earning its place by measurement (AUC on same-section vs
different-section pairs across the six approved goldens):

    front end    descriptor    @0.0s  @0.5s  @1.0s  @2.0s   <- boundary error
    raw chroma   patch         0.749  0.625  0.527  0.427
    raw chroma   2DFTM         0.794  0.794  0.787  0.770
    CENS         patch         0.787  0.708  0.574  0.372
    CENS         2DFTM         0.835  0.818  0.800  0.797

CENS buys quality; 2DFTM buys immunity to boundary error, which was the failure
that made blind matching circular — you needed the boundary to compute the
feature that would have found the boundary. Together they are better aligned
*and* barely degrade at two seconds of misalignment, where a plain CENS patch
falls below chance.

Everything here is numpy. No model, no license encumbrance, no new dependency —
which matters because the shipped binary carries onnxruntime and numpy only.
"""
from __future__ import annotations

import argparse
import bisect
import os
import statistics
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "sidecar"))

from auto_presenter.eval.replay import Replayer, load_recording

SR = 16000
N_FFT = 2048          # 128 ms — long enough to resolve low notes
HOP = 512             # 32 ms  — ~31 frames/sec
NF = 24               # frames per section after resampling

def _bin_to_pitch_class(n_fft=N_FFT, sr=SR):
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    pc = np.full(len(freqs), -1, dtype=int)
    ok = (freqs >= 55.0) & (freqs <= 2000.0)     # A1 to ~B6
    midi = 69 + 12 * np.log2(np.maximum(freqs[ok], 1e-9) / 440.0)
    pc[ok] = np.round(midi).astype(int) % 12
    return pc


_PC = _bin_to_pitch_class()


def chromagram(audio: np.ndarray, hop: int = HOP) -> np.ndarray:
    """(frames, 12), each frame L2-normalised."""
    win = np.hanning(N_FFT).astype(np.float32)
    n = 1 + max(0, (len(audio) - N_FFT) // hop)
    out = np.zeros((n, 12), dtype=np.float32)
    for i in range(n):
        seg = audio[i * hop: i * hop + N_FFT]
        if len(seg) < N_FFT:
            break
        mag = np.abs(np.fft.rfft(seg * win))
        for pc in range(12):
            out[i, pc] = mag[_PC == pc].sum()
    norms = np.linalg.norm(out, axis=1, keepdims=True)
    return out / np.maximum(norms, 1e-9)


def frames_for(chroma_arr: np.ndarray, t0: float, t1: float, hop: int = HOP):
    a = int(t0 * SR / hop)
    b = int(t1 * SR / hop)
    return chroma_arr[max(0, a): max(a + 1, min(b, len(chroma_arr)))]

THRESHOLDS = (0.05, 0.1, 0.2, 0.4)
WEIGHTS = (1.0, 2.0, 3.0, 4.0)


def quantise(chroma_l1: np.ndarray) -> np.ndarray:
    """Each bin takes the weight of the highest threshold it exceeds."""
    out = np.zeros_like(chroma_l1)
    for t, w in zip(THRESHOLDS, WEIGHTS):
        out = np.where(chroma_l1 > t, w, out)
    return out


def cens(chroma: np.ndarray, smooth: int = 41, downsample: int = 10) -> np.ndarray:
    """chroma: (frames, 12) raw magnitudes. Returns (frames', 12) CENS."""
    l1 = chroma / np.maximum(np.abs(chroma).sum(axis=1, keepdims=True), 1e-9)
    q = quantise(l1)
    win = np.hanning(smooth + 2)[1:-1]
    win = win / win.sum()
    sm = np.empty_like(q)
    for k in range(12):
        sm[:, k] = np.convolve(q[:, k], win, mode="same")
    ds = sm[::downsample]
    n = np.linalg.norm(ds, axis=1, keepdims=True)
    return ds / np.maximum(n, 1e-9)


def tdftm(patch: np.ndarray, keep: int = 8) -> np.ndarray | None:
    """2D Fourier magnitude of a (frames, 12) patch — Bertin-Mahieux & Ellis 2012.

    The 2D FFT encodes the patch's periodic structure; discarding the phase
    discards *where* those periodicities sit. A translation multiplies the
    transform by a phase factor and leaves the magnitude alone, so the magnitude
    is invariant to shifts in both axes at once: pitch rotation (transposition)
    and time offset (misalignment).

    The pitch axis wraps genuinely — twelve semitones is a circle — so key
    invariance is *exact*, verified at 1.000000 for every rotation. The time
    axis does not wrap: a real shift pulls in new content at one end and drops
    it at the other, so invariance there is approximate. Measured on a 10 s
    patch, a real 2 s shift still scores 0.987 against 0.9873 for a circular
    one, while the same shift on an un-transformed patch scores -0.013.

    `keep` retains only the lowest time-periodicity rows, standing in for the
    paper's PCA: fast structure is mostly noise at chroma resolution.
    """
    if patch.shape[0] < 4:
        return None
    mag = np.abs(np.fft.fft2(patch))[:keep, :]
    v = mag.reshape(-1)
    return v / max(float(np.linalg.norm(v)), 1e-9)


# --------------------------------------------------------------- measurement

def _resample(seg: np.ndarray, n: int = NF) -> np.ndarray | None:
    """Fixed frame count, which is what makes the fingerprint tempo-invariant:
    the same content is the same vector whether it took 6 s or 9 s."""
    if len(seg) < 4:
        return None
    idx = np.linspace(0, len(seg) - 1, n)
    lo = np.floor(idx).astype(int)
    hi = np.minimum(lo + 1, len(seg) - 1)
    f = (idx - lo)[:, None]
    return seg[lo] * (1 - f) + seg[hi] * f


def fingerprint_patch(seg):
    r = _resample(seg)
    if r is None:
        return None
    r = r - r.mean(axis=0, keepdims=True)
    return r / max(float(np.linalg.norm(r)), 1e-9)


def fingerprint_2dftm(seg):
    r = _resample(seg)
    return None if r is None else tdftm(r)


def auc(same: list[float], diff: list[float]) -> float:
    """P(a true repeat outscores a random different pair).

    Scale-free on purpose. 2DFTM descriptors are non-negative magnitudes, so
    their cosines bunch near 1.0 and a raw same-minus-different gap makes them
    look far worse than they are — 0.022 against CENS's 0.409, while actually
    discriminating better. Ranks avoid that trap entirely.
    """
    d = sorted(diff)
    total = 0.0
    for x in same:
        total += (bisect.bisect_left(d, x) + bisect.bisect_right(d, x)) / 2.0
    return total / (len(same) * len(d))


RECORDINGS = Path(os.path.expanduser(
    "~/Library/Application Support/com.churchslides.app/recordings"))


def _approved(root: Path) -> list[str]:
    import json
    out = []
    for d in sorted(root.iterdir()):
        g = d / "golden.json"
        if g.exists():
            try:
                if json.loads(g.read_text()).get("approved"):
                    out.append(d.name)
            except (OSError, ValueError):
                pass
    return out


def _load(rid: str, root: Path):
    rec = load_recording(root / rid)
    audio = np.concatenate(
        [f.samples for f in Replayer(rec, realtime=False).audio_frames()]
    ).astype(np.float32)
    ch = chromagram(audio)
    return rec, {"raw": (ch, SR / HOP), "cens": (cens(ch), SR / HOP / 10)}


def measure(root: Path, front: str, fp_fn, shift: float) -> float:
    """AUC over same-section vs different-section pairs.

    `shift` misaligns only ONE side of each comparison. Shifting both preserves
    their relative alignment and measures nothing — an earlier version of this
    experiment did that and reported a spurious immunity to boundary error.
    """
    same, diff = [], []
    for rid in _approved(root):
        rec, feats = _load(rid, root)
        arr, fps = feats[front]
        by: dict[int, list] = {}
        gt = sorted(rec.ground_truth, key=lambda g: g.t_seconds)
        for i, g in enumerate(gt):
            t1 = gt[i + 1].t_seconds if i + 1 < len(gt) else rec.duration_seconds
            if t1 - g.t_seconds < 3.0:
                continue

            def grab(off: float):
                a = int((g.t_seconds + off) * fps)
                b = int((t1 + off) * fps)
                return arr[max(0, a): max(a + 1, min(b, len(arr)))]

            aligned, moved = fp_fn(grab(0.0)), fp_fn(grab(shift))
            if aligned is not None and moved is not None:
                by.setdefault(g.slide_idx, []).append((aligned, moved))
        keys = list(by)
        for vs in by.values():
            for i in range(len(vs)):
                for j in range(i + 1, len(vs)):
                    same.append(float((vs[i][0] * vs[j][1]).sum()))
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                diff.append(float((by[keys[i]][0][0] * by[keys[j]][0][1]).sum()))
    if not same or not diff:
        return float("nan")
    return auc(same, diff)


def cmd_matrix(root: Path) -> None:
    shifts = (0.0, 0.5, 1.0, 2.0)
    header = "".join(f"{f'AUC @{s}s':>10s}" for s in shifts)
    print(f"{'front end':12s} {'descriptor':12s} {header}")
    for front in ("raw", "cens"):
        for name, fn in (("patch", fingerprint_patch), ("2DFTM", fingerprint_2dftm)):
            row = "".join(f"{measure(root, front, fn, s):10.3f}" for s in shifts)
            print(f"{front:12s} {name:12s} {row}", flush=True)


def cmd_verify(root: Path) -> None:
    """Check each stage against what its source claims, before any accuracy
    number is trusted. A feature that silently degenerated would still produce
    plausible-looking similarity scores."""
    rid = _approved(root)[0]
    rec, feats = _load(rid, root)
    ch, _ = feats["raw"]
    cn, fps = feats["cens"]

    l1 = ch / np.maximum(np.abs(ch).sum(axis=1, keepdims=True), 1e-9)
    nz = l1.sum(axis=1) > 0.5
    print(f"CENS, on {rid}")
    print(f"  L1 rows sum to {l1.sum(axis=1)[nz].min():.3f}-{l1.sum(axis=1).max():.3f} "
          f"over non-silent frames (expect 1.000)")
    print(f"  quantisation levels {sorted(set(np.unique(quantise(l1)).tolist()))} "
          f"(expect a subset of [0, 1, 2, 3, 4])")
    unit = l1 / np.maximum(np.linalg.norm(l1, axis=1, keepdims=True), 1e-9)
    smoothed = cens(ch, downsample=1)
    print(f"  smoothing cuts frame-to-frame change to "
          f"{np.abs(np.diff(smoothed, axis=0)).mean() / np.abs(np.diff(unit, axis=0)).mean():.2f}x "
          f"(expect well below 1)")
    loud = cens(chromagram(np.concatenate(
        [f.samples for f in Replayer(rec, realtime=False).audio_frames()]
    ).astype(np.float32) * 4.0))
    n = min(len(cn), len(loud))
    print(f"  4x louder audio moves CENS by {np.abs(cn[:n] - loud[:n]).mean():.6f} "
          f"(expect 0 — L1 removes loudness)")

    patch = cn[int(30 * fps):int(40 * fps)]
    base = tdftm(patch)
    print(f"\n2DFTM, on a 10 s patch {patch.shape}")
    for k in (1, 3, 7):
        print(f"  transpose +{k} semitones: cosine "
              f"{float(base @ tdftm(np.roll(patch, k, axis=1))):.6f} (expect 1.000000)")
    for k in (5, 15):
        print(f"  circular time shift {k} frames: cosine "
              f"{float(base @ tdftm(np.roll(patch, k, axis=0))):.6f} (expect 1.000000)")
    for secs in (1.0, 2.0):
        off = int(secs * fps)
        moved = tdftm(cn[int(30 * fps) + off:int(40 * fps) + off])
        plain_a = fingerprint_patch(patch)
        plain_b = fingerprint_patch(cn[int(30 * fps) + off:int(40 * fps) + off])
        # Elementwise, not `@`: `fingerprint_patch` returns a 2-D (frames, 12)
        # array, so `@` would be a matrix product rather than a dot.
        print(f"  real time shift {secs:.1f}s: 2DFTM {float((base * moved).sum()):.4f}  "
              f"vs untransformed patch {float((plain_a * plain_b).sum()):+.4f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--recordings", type=Path, default=RECORDINGS)
    ap.add_argument("--verify", action="store_true",
                    help="check each stage behaves as its source claims")
    ap.add_argument("--matrix", action="store_true",
                    help="AUC for each front end x descriptor x misalignment")
    args = ap.parse_args()
    if not args.verify and not args.matrix:
        ap.error("pass --verify and/or --matrix")
    if args.verify:
        cmd_verify(args.recordings)
    if args.matrix:
        if args.verify:
            print()
        cmd_matrix(args.recordings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
