"""Does Qwen3-ASR stay accurate when its encoder hears shorter blocks?

The encoder attends within 8 s blocks (`n_window_infer` = 800 mel frames),
and the streaming cache can reuse a block's encoder output and decoder cache
only once the block is complete. With the app's 10 s window, that means
nearly the whole window is re-encoded and re-prefilled every step, which is
too slow on a processor (docs/research/QWEN3_ASR_CPU.md). Shorter blocks
complete sooner, so each step encodes and prefills only the newest audio,
but each block is heard without the audio around it, which the model wasn't
trained for.

This streams the six public songs (JamendoLyrics) through
`mlx_qwen3_asr.streaming` with the encoder's block set to `--block` seconds,
and reports the whole-song word error rate of the streamed text and the
per-step time. Run on Apple silicon, in the app's environment:

    cd sidecar && PYTHONPATH=. uv run --no-sync python \
        ../research/spikes/qwen_cpu/block_eval.py --block 8 2 1 --chunk 1 2
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "qwen_improve"))
from common import MAIN6, MODELS, SR, audio_of, load_clip, norm, song_lines  # noqa: E402


def wer(ref: list[str], hyp: list[str]) -> float:
    d = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, d[0] = d[0], i
        for j, h in enumerate(hyp, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r != h))
    return d[-1] / max(1, len(ref))


def stream(model, samples: np.ndarray, chunk: float, context: float) -> tuple[str, list[float]]:
    from mlx_qwen3_asr.streaming import feed_audio, finish_streaming, init_streaming

    st = init_streaming(model=MODELS["0.6b"], chunk_size_sec=chunk, max_context_sec=context,
                        language="English")
    step = int(chunk * SR)
    times = []
    for i in range(0, len(samples), step):
        a = time.monotonic()
        feed_audio(samples[i : i + step], st, model=model)
        times.append(time.monotonic() - a)
    finish_streaming(st, model=model)
    return st.text, times


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--block", type=float, nargs="+", default=[8.0, 2.0])
    ap.add_argument("--chunk", type=float, nargs="+", default=[1.0])
    ap.add_argument("--context", type=float, default=10.0)
    ap.add_argument("--clips", default=",".join(MAIN6))
    args = ap.parse_args()

    from mlx_qwen3_asr.load_models import load_model

    model, _ = load_model(MODELS["0.6b"])
    cfg = model.audio_tower.config
    clips = args.clips.split(",")
    refs = {c: [n for ln in song_lines(c) for n in ln.norms if n] for c in clips}
    audio = {c: audio_of(load_clip(c)) for c in clips}
    for block in args.block:
        cfg.n_window_infer = int(block * 100)
        for chunk in args.chunk:
            errs, n, times = 0.0, 0, []
            per = {}
            for c in clips:
                text, t = stream(model, audio[c], chunk, args.context)
                hyp = [norm(w) for w in text.split() if norm(w)]
                w = wer(refs[c], hyp)
                per[c] = round(w, 3)
                errs += w * len(refs[c])
                n += len(refs[c])
                times += t
            print(json.dumps({
                "block_s": block, "chunk_s": chunk, "context_s": args.context,
                "wer": round(errs / n, 3), "per_song": per,
                "step_p50_ms": round(float(np.median(times)) * 1000),
            }), flush=True)


if __name__ == "__main__":
    main()
