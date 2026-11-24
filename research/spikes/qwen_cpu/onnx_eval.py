"""The processor backend (`asr.qwen_onnx_backend`) on the six public songs.

Streams each song through `QwenOnnxBackend.feed` a step at a time, as
block_eval.py streams the MLX package, and reports the whole-song word error
and the per-step time. Compare with block_eval.py's 2 s blocks, 2 s steps
row (0.381 on the M1's GPU at fp16). Needs ONNX Runtime 1.32 (the engine
pins 1.19 on macOS), and the model files in AUTO_PRESENTER_QWEN_ONNX_DIR:

    cd sidecar && AUTO_PRESENTER_QWEN_ONNX_DIR=... PYTHONPATH=. uv run --no-sync \
        --with onnxruntime==1.32.0 python ../research/spikes/qwen_cpu/onnx_eval.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "qwen_improve"))
from block_eval import wer
from common import MAIN6, SR, audio_of, load_clip, norm, song_lines


def main() -> None:
    import onnxruntime

    from auto_presenter.asr.qwen_onnx_backend import QwenOnnxBackend

    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", default=",".join(MAIN6))
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--seconds", type=float, default=0, help="only the first N s of each song")
    args = ap.parse_args()
    b = QwenOnnxBackend(threads=args.threads)
    b.load()
    errs, n, times, per = 0.0, 0, [], {}
    for c in args.clips.split(","):
        audio = audio_of(load_clip(c))
        if args.seconds:
            audio = audio[: int(args.seconds * SR)]
        st = b.new_state()
        step = int(b.step_seconds * SR)
        for i in range(0, len(audio), step):
            a = time.monotonic()
            b.feed(st, audio[i : i + step])
            times.append(time.monotonic() - a)
        text = f"{st.committed_text} {st.window_text}"
        ref = [x for ln in song_lines(c) for x in ln.norms if x]
        if args.seconds:
            ref = [x for ln in song_lines(c) if ln.end <= args.seconds for x in ln.norms if x]
        w = wer(ref, [norm(x) for x in text.split() if norm(x)])
        per[c] = round(w, 3)
        errs += w * len(ref)
        n += len(ref)
        print(c, per[c], f"step p50 {np.median(times) * 1000:.0f} ms", flush=True)
    print(json.dumps({
        "onnxruntime": onnxruntime.__version__, "threads": args.threads or "default",
        "wer": round(errs / n, 3), "per_song": per,
        "step_p50_ms": round(float(np.median(times)) * 1000),
        "step_p90_ms": round(float(np.percentile(times, 90)) * 1000),
        "load_2s_step": round(float(np.median(times)) / b.step_seconds, 2),
    }))


if __name__ == "__main__":
    main()
