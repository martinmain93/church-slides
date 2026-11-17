"""Does the shipped BTC path agree with the reference implementation?

Three things are swapped at once relative to upstream BTC, and each could
silently degrade the model rather than break it:

    librosa CQT   -> the numpy CQT in `music/cqt.py`
    PyTorch       -> ONNX
    centred frames, whole-file context -> trailing frames, trailing block

So this compares chord labels, frame by frame, against the reference pipeline,
and separately measures what the *online* constraint costs — which is the
number that matters, since a live analyser can never have right-hand context.

Needs the export venv (torch + librosa), not the sidecar:

    /tmp/bx/bin/python research/eval/check_btc_backend.py <wav> [...]
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "sidecar"))

from auto_presenter.music.btc import MAJMIN_LABELS, BtcChordBackend  # noqa: E402
from auto_presenter.music.cqt import log_cqt  # noqa: E402


def reference_labels(path: Path, backend: BtcChordBackend) -> np.ndarray:
    """Upstream's own pipeline: librosa's centred CQT, whole file, ONNX."""
    import librosa

    meta = backend._meta
    y, _ = librosa.load(str(path), sr=meta["sample_rate"], mono=True)
    cq = np.abs(
        librosa.cqt(
            y,
            sr=meta["sample_rate"],
            n_bins=meta["n_bins"],
            bins_per_octave=meta["bins_per_octave"],
            hop_length=meta["hop_length"],
        )
    ).T
    feat = (log_cqt(cq) - meta["mean"]) / meta["std"]
    n = backend.timestep
    out: list[int] = []
    for start in range(0, len(feat) - n + 1, n):
        block = feat[start:start + n][None].astype(np.float32)
        logits = backend._sess.run(["logits"], {"features": block})[0][0]
        out.extend(logits.argmax(-1).tolist())
    return np.array(out)


def ours_offline(path: Path, backend: BtcChordBackend) -> np.ndarray:
    """Same blocks, but our numpy CQT (still centred) — isolates the transform."""
    import soundfile as sf

    y, sr = sf.read(str(path), dtype="float32")
    if y.ndim > 1:
        y = y.mean(axis=1)
    from auto_presenter.music.cqt import resample_linear

    meta = backend._meta
    y = resample_linear(y, sr, meta["sample_rate"])
    feat = (log_cqt(backend._cqt(y)) - meta["mean"]) / meta["std"]
    n = backend.timestep
    out: list[int] = []
    for start in range(0, len(feat) - n + 1, n):
        block = feat[start:start + n][None].astype(np.float32)
        logits = backend._sess.run(["logits"], {"features": block})[0][0]
        out.extend(logits.argmax(-1).tolist())
    return np.array(out)


def ours_online(path: Path, backend: BtcChordBackend, step_s: float = 1.0):
    """What the live analyser actually does: trailing window, newest frame."""
    import soundfile as sf

    y, sr = sf.read(str(path), dtype="float32")
    if y.ndim > 1:
        y = y.mean(axis=1)
    if sr != 16000:
        from auto_presenter.music.cqt import resample_linear

        y = resample_linear(y, sr, 16000)
    win = int(backend.window_seconds * 16000) + 16000
    step = int(step_s * 16000)
    times, labels = [], []
    for end in range(win, len(y), step):
        probs = backend.predict(y[end - win:end])
        if probs is None:
            continue
        times.append(end / 16000)
        labels.append(int(np.argmax(probs)))
    return np.array(times), np.array(labels)


def main() -> int:
    backend = BtcChordBackend()
    if not backend.available:
        print(f"BTC model not available: {backend.status}")
        print("run scripts/setup_chord_model.py first")
        return 1
    hop_s = backend._meta["hop_length"] / backend._meta["sample_rate"]

    print(f"{'file':34s} {'frames':>7s} {'CQT swap':>9s} {'online':>8s} "
          f"{'online±1f':>10s}")
    print("-" * 74)
    swaps, ons, ons1 = [], [], []
    for arg in sys.argv[1:]:
        path = Path(arg)
        ref = reference_labels(path, backend)
        ours = ours_offline(path, backend)
        n = min(len(ref), len(ours))
        swap = float((ref[:n] == ours[:n]).mean())

        times, online = ours_online(path, backend)
        # A trailing frame ends where a centred frame is only half-built, so
        # the two are offset by half an FFT; compare at the matching instant.
        shift = backend._cqt.n_fft / (2 * backend._meta["sample_rate"])
        idx = np.clip(((times - shift) / hop_s).astype(int), 0, len(ref) - 1)
        exact = float((ref[idx] == online).mean())
        near = float(
            np.mean([
                ref[max(0, i - 1):i + 2].tolist().count(lbl) > 0
                for i, lbl in zip(idx, online, strict=False)
            ])
        )
        swaps.append(swap)
        ons.append(exact)
        ons1.append(near)
        print(f"{path.name[:34]:34s} {n:7d} {swap:8.1%} {exact:7.1%} {near:9.1%}")

    print("-" * 74)
    print(f"{'mean':34s} {'':7s} {np.mean(swaps):8.1%} {np.mean(ons):7.1%} "
          f"{np.mean(ons1):9.1%}")
    print()
    print("`CQT swap` isolates the numpy transform: same blocks, same model, so")
    print("anything below ~95% means the feature front end is off.")
    print("`online` is the live constraint — trailing window, no right context —")
    print("against the offline reference. `±1f` allows a one-frame (93 ms) shift.")
    print(f"labels: {len(MAJMIN_LABELS)} classes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
