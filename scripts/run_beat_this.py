"""Beat and downbeat times from Beat This! (Foscarin, Schlüter & Widmer).

Run with the beat-this venv, not the sidecar's. The sidecar stays
onnxruntime and numpy; this process is the one place torch is allowed,
and only for the inspector.

    <beat-this-venv>/bin/python scripts/run_beat_this.py path/to/audio.wav

Prints JSON `{"beats": [...], "downbeats": [...]}` to stdout. A failure
prints a message on stderr and exits non-zero.
"""

from __future__ import annotations

import json
import os
import sys
import wave
from pathlib import Path

import numpy as np


def load_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    if sw == 2:
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sw == 4:
        samples = np.frombuffer(raw, dtype=np.float32).copy()
    else:
        raise SystemExit(f"unsupported sample width {sw}")
    if ch > 1:
        samples = samples.reshape(-1, ch).mean(axis=1)
    return np.ascontiguousarray(samples, dtype=np.float32), sr


def _emit(payload: dict) -> None:
    print(json.dumps(payload), flush=True)


def _device() -> str:
    """BEAT_THIS_DEVICE (the sidecar asks for "cpu" during a service), else MPS
    when present."""
    import torch

    if os.environ.get("BEAT_THIS_DEVICE"):
        return os.environ["BEAT_THIS_DEVICE"]
    return "mps" if torch.backends.mps.is_available() else "cpu"


def _stream(audio: np.ndarray, sr: int) -> None:
    """Beat This's own 30 s spectrogram chunks, published as each one finishes.

    The library already discards 6 frames at every chunk edge and keeps the
    earlier chunk where they overlap. Peak picking looks ±70 ms around a
    frame, so a partial result stops short of that edge. The last line is the
    full piece, peak-picked the same way `Audio2Beats` does.
    """
    import torch
    from beat_this.inference import (
        Audio2Beats,
        aggregate_prediction,
        split_piece,
    )

    device = _device()
    tracker = Audio2Beats(checkpoint_path="final0", device=device)
    spect = tracker.signal2spect(audio, sr)
    chunk_size = 1500
    border_size = 6
    # Frames held back so the ±70 ms max-pool, and downbeat-to-beat snapping,
    # only see logits that will not change.
    hold = 25
    fps = 50.0
    chunks, starts = split_piece(
        spect, chunk_size, border_size=border_size, avoid_short_end=True
    )
    pred_chunks: list[dict] = []
    with torch.inference_mode():
        for i, chunk in enumerate(chunks):
            pred = tracker.model(chunk.unsqueeze(0))
            pred_chunks.append({"beat": pred["beat"][0], "downbeat": pred["downbeat"][0]})
            beat_logits, down_logits = aggregate_prediction(
                pred_chunks,
                list(starts[: i + 1]),
                int(spect.shape[0]),
                chunk_size,
                border_size,
                "keep_first",
                spect.device,
            )
            last = i + 1 == len(chunks)
            if last:
                written = int(beat_logits.shape[0])
                safe = written / fps
            else:
                written = int(starts[i + 1]) + border_size
                written = max(0, min(written, int(beat_logits.shape[0])))
                safe = max(0.0, (written - hold) / fps)
            beats, downbeats = tracker.frames2beats(
                beat_logits[:written], down_logits[:written]
            )
            beat_times = [
                round(float(t), 3) for t in np.asarray(beats).reshape(-1) if float(t) < safe or last
            ]
            down_times = [
                round(float(t), 3)
                for t in np.asarray(downbeats).reshape(-1)
                if float(t) < safe or last
            ]
            _emit(
                {
                    "partial": not last,
                    "through": round(safe if not last else float(len(audio)) / sr, 3),
                    "beats": beat_times,
                    "downbeats": down_times,
                }
            )


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--stream"]
    if len(args) != 1:
        raise SystemExit("usage: run_beat_this.py [--stream] audio.wav")
    from beat_this.inference import Audio2Beats

    audio, sr = load_wav(Path(args[0]))
    if "--stream" in sys.argv:
        _stream(audio, sr)
        return
    device = _device()
    model = Audio2Beats(checkpoint_path="final0", device=device)
    beats, downbeats = model(audio, sr)
    _emit(
        {
            "beats": [round(float(t), 3) for t in np.asarray(beats).reshape(-1)],
            "downbeats": [round(float(t), 3) for t in np.asarray(downbeats).reshape(-1)],
        }
    )


if __name__ == "__main__":
    main()
