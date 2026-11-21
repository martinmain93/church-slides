"""Baseline re-time: the app's MLX Whisper small.en per 6 s window, on mains power.

The parent spike's pass times were taken in Low Power Mode, so every speed
comparison in this spike uses this run instead. Same wrapper, same songs, no
real-time pacing (events are stamped window end + measured pass time).
Runs in the app's sidecar venv (it has mlx_whisper), not the Qwen venv:

    cd sidecar && PYTHONPATH=. ~/Development/church-slides/sidecar/.venv/bin/python \
        ../research/spikes/qwen_improve/whisper_time.py --out <run dir>
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "streaming_asr"))
from stream_eval import RESEARCH, load_clip, pct  # noqa: E402

from common import MAIN6  # noqa: E402
from speed_eval import run_window  # noqa: E402


class Timed:
    def __init__(self, inner, label: str) -> None:
        self.inner, self.label, self.s = inner, label, []

    def name(self) -> str:
        return self.label

    def transcribe(self, frames):
        a = time.monotonic()
        out = list(self.inner.transcribe(frames))
        self.s.append(time.monotonic() - a)
        return iter(out)


def main() -> None:
    import numpy as np
    from auto_presenter.asr.mlx_backend import MLXWhisperBackend

    ap = argparse.ArgumentParser()
    ap.add_argument("--size", default="small.en")
    ap.add_argument("--out", default=str(RESEARCH / "work" / "qwen_improve" / "runs" / "speed"))
    args = ap.parse_args()
    inner = MLXWhisperBackend(args.size)
    inner.warm()
    label = "mlx-" + args.size.replace(".", "")
    be = Timed(inner, label)
    root = Path(args.out)
    for name in MAIN6:
        clip = load_clip(name)
        samples = np.frombuffer(clip.pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        run_window(be, samples, root / name / f"{label}.jsonl")
        print(f"{name}: p50 {pct(be.s, .5):.3f}s", flush=True)
    sp = root / "speed.json"
    speed = json.loads(sp.read_text()) if sp.exists() else {}
    speed[label] = {"variant": label, "passes": len(be.s), "pass_p50": pct(be.s, .5), "pass_p90": pct(be.s, .9)}
    sp.write_text(json.dumps(speed, indent=1))
    print(json.dumps(speed[label]))


if __name__ == "__main__":
    main()
