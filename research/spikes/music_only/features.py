"""Dense music features for one recording, cached beside the spike's output.

Nothing here reads a transcript. For each source (the full mix, and the
Demucs stems where they exist) it keeps:

* `pitch`: Basic Pitch's contour salience, frames x 264 bins (3 per
  semitone from MIDI 21), at ~86 fps. The full map, not the thinned trace
  the inspector draws, so a matcher can ask "how much energy is at the
  expected note" instead of trusting one argmax.
* `chords`: BTC's 25-way softmax (12 major, 12 minor, no-chord) at ~10.8 fps,
  run offline over overlapping 108-frame blocks with the centre half kept.
  Each frame is stamped at the centre of its CQT window — a trailing frame
  ends 0.74 s after that, which is the lag the live path has to add back.

Pitch reads the vocal stem and chords the `other` stem when stems exist, and
both read the mix too, so every comparison can be run both ways.

    uv run --project sidecar python research/spikes/music_only/features.py REC_DIR OUT.npz
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from auto_presenter.music import basic_pitch as bp
from auto_presenter.music.btc import BtcChordBackend, MAJMIN_LABELS
from auto_presenter.music.inspect import load_wav
from auto_presenter.music.realtime_stems import read_stem_wavs


def pitch_map(audio_16k: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    run = bp.PitchRun(audio_16k)
    run.advance_to(1e9)
    if run.backend != "basic_pitch":
        raise RuntimeError(f"basic pitch unavailable: {run.backend}")
    contour = bp._unwrap(run._contour_chunks, run._resampled_len)
    times = bp.frame_times(len(run._contour_chunks))
    n = min(len(contour), len(times))
    return times[:n].astype(np.float32), contour[:n].astype(np.float16)


def chord_map(audio_16k: np.ndarray, btc: BtcChordBackend) -> tuple[np.ndarray, np.ndarray]:
    feat = btc.features(audio_16k)
    n = btc.timestep
    hop = int(btc._meta["hop_length"])
    sr = float(btc._meta["sample_rate"])
    n_fft = btc._cqt.n_fft
    T = len(feat)
    logits = np.zeros((T, len(MAJMIN_LABELS)), np.float32)
    weight = np.zeros(T, np.float32)
    # overlapping blocks, weighted towards each block's centre
    w = np.hanning(n).astype(np.float32) + 1e-3
    starts = list(range(0, max(1, T - n + 1), n // 4))
    if starts[-1] != max(0, T - n):
        starts.append(max(0, T - n))
    for s0 in starts:
        block = feat[s0 : s0 + n]
        if len(block) < n:
            block = np.pad(block, ((0, n - len(block)), (0, 0)))
        out = btc._sess.run(["logits"], {"features": block[None].astype(np.float32)})[0][0]
        k = min(n, T - s0)
        logits[s0 : s0 + k] += out[:k] * w[:k, None]
        weight[s0 : s0 + k] += w[:k]
    logits /= np.maximum(weight, 1e-6)[:, None]
    e = np.exp(logits - logits.max(axis=1, keepdims=True))
    probs = e / e.sum(axis=1, keepdims=True)
    # a trailing frame k covers samples [k*hop, k*hop + n_fft); stamp its centre
    times = (np.arange(T) * hop + n_fft / 2) / sr
    return times.astype(np.float32), probs.astype(np.float16)


def compute(rec_dir: Path) -> dict[str, np.ndarray]:
    mix = load_wav(rec_dir / "audio.wav")
    stems = read_stem_wavs(rec_dir / "stems") if (rec_dir / "stems").exists() else None
    btc = BtcChordBackend()
    if btc.status != "ready":
        raise RuntimeError(f"BTC unavailable: {btc.status}")
    out: dict[str, np.ndarray] = {}
    sources = {"mix": (mix, mix)}
    if stems:
        sources["stems"] = (stems["vocals"], stems["other"])
    for name, (pitch_audio, chord_audio) in sources.items():
        print(f"  {name}: pitch", file=sys.stderr)
        out[f"{name}_pitch_t"], out[f"{name}_pitch"] = pitch_map(pitch_audio)
        print(f"  {name}: chords", file=sys.stderr)
        out[f"{name}_chord_t"], out[f"{name}_chord"] = chord_map(chord_audio, btc)
        out[f"{name}_vocal_rms"] = _rms(pitch_audio)
    out["duration"] = np.array(len(mix) / 16000.0)
    return out


def _rms(x: np.ndarray, hop: int = 1600) -> np.ndarray:
    """RMS per 100 ms, for telling sung from instrumental passages."""
    n = len(x) // hop
    return np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(axis=1)).astype(np.float32)


def load(path: Path) -> dict[str, np.ndarray]:
    return dict(np.load(path))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("recording", type=Path)
    ap.add_argument("out", type=Path)
    args = ap.parse_args()
    feats = compute(args.recording.expanduser())
    np.savez_compressed(args.out, **feats)
    for k, v in feats.items():
        print(f"{k:18s} {v.shape} {v.dtype}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
