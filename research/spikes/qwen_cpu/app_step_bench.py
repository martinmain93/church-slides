"""Time one step of the processor backend (`asr.qwen_onnx_backend`) as it runs.

A step, every 2 s: encode the newest 2 s block, prefill its 26 audio tokens
plus the closing prompt and the forced text prefix onto the cached window
(`decoder_extend`), then decode new tokens one at a time with the same graph.
Runs with only onnxruntime and numpy, so it can be timed on GitHub's runners
(.github/workflows/qwen-cpu-bench.yml). Public-domain audio (mel_2s.npy).

    python app_step_bench.py MODEL_DIR DECODER_ONNX [threads ...]

MODEL_DIR holds the published encoder.int4.onnx and embed_tokens.bin.
Load = step time / 2 s. Prints one JSON line per thread setting.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
PASSES = 10
CACHED = 10 + 4 * 26  # prompt head + four cached 2 s blocks (an 8-10 s window)
PREFIX = 20  # forced text tokens (the window's words so far, minus 5)
NEW = 6  # tokens decoded a step (about 2 s of singing)


def main() -> None:
    import onnxruntime as ort

    sys.path.insert(0, str(HERE))
    from step_bench import cpu_name

    m, dec_path = Path(sys.argv[1]), sys.argv[2]
    prompt = json.loads((HERE / "prompt.json").read_text())
    mel2 = np.load(HERE / "mel_2s.npy")
    emb = np.fromfile(m / "embed_tokens.bin", dtype=np.float16).reshape(151936, 1024)
    tail_ids = prompt["after"]
    for threads in [int(t) for t in sys.argv[3:]] or [0, 4]:
        so = ort.SessionOptions()
        if threads:
            so.intra_op_num_threads = threads
        cpu = ["CPUExecutionProvider"]
        enc = ort.InferenceSession(str(m / "encoder.int4.onnx"), so, providers=cpu)
        dec = ort.InferenceSession(dec_path, so, providers=cpu)
        rng = np.random.default_rng(0)
        past = (rng.standard_normal((28, 1, 8, CACHED, 128)) * 0.1).astype(np.float32)
        rows = []
        for _ in range(PASSES):
            a = time.perf_counter()
            feats = enc.run(None, {"mel": mel2})[0][0]
            b = time.perf_counter()
            ids = tail_ids + [11] * PREFIX
            x = np.concatenate([feats, emb[ids].astype(np.float32)])[None]
            s = x.shape[1]
            out = dec.run(None, {
                "input_embeds": x,
                "position_ids": np.arange(CACHED, CACHED + s, dtype=np.int64)[None],
                "past_keys": past, "past_values": past,
            })
            c = time.perf_counter()
            keys, vals, pos = out[1], out[2], CACHED + s
            for _ in range(NEW):
                tok = int(np.argmax(out[0][0, -1]))
                out = dec.run(None, {
                    "input_embeds": emb[[tok]].astype(np.float32)[None],
                    "position_ids": np.array([[pos]], np.int64),
                    "past_keys": keys, "past_values": vals,
                })
                keys, vals, pos = out[1], out[2], pos + 1
            d = time.perf_counter()
            rows.append((b - a, c - b, d - c))
        enc_ms, pre_ms, dec_ms = (round(float(x)) for x in np.median(np.array(rows[2:]) * 1000, axis=0))
        step = enc_ms + pre_ms + dec_ms
        print(json.dumps({
            "os": f"{platform.system()} {platform.release()}", "machine": platform.machine(),
            "cpu": cpu_name(), "cores": os.cpu_count(), "onnxruntime": ort.__version__,
            "decoder": Path(dec_path).name, "threads": threads or "default",
            "encode_2s_ms": enc_ms, f"prefill_{s}_ms": pre_ms, f"decode_{NEW}_ms": dec_ms,
            "step_ms": step, "load_2s_step": round(step / 2000, 2),
        }), flush=True)


if __name__ == "__main__":
    main()
