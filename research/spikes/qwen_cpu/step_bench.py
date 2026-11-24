"""How long one live pass of Qwen3-ASR 0.6B takes on a CPU with ONNX Runtime.

Runs anywhere with `onnxruntime` and `numpy` (no torch, no MLX), so it can be
timed on GitHub's Windows and Linux runners (.github/workflows/qwen-cpu-bench.yml).
Model: the int4 export at andrewleech/qwen3-asr-0.6b-onnx (Apache 2.0),
downloaded on first run. Audio: 6 s of a public-domain LibriVox hymn, as
precomputed mel spectrograms (`mel_6s.npy`, `mel_2s.npy`, made on a Mac with
`make_inputs.py`) — never church recordings.

A live pass, as the Apple-silicon backend does: encode audio, prefill the
prompt (audio tokens + text), decode N_TOKENS tokens one at a time.

- "full": encode the whole 6 s window every pass.
- "incremental": encode only the newest 2 s (the window's earlier audio
  features are kept from earlier passes), prefill the 6 s prompt, decode.

Load = pass time / step. Under 0.75 keeps up with margin (the old CPU path's
bar, sidecar/auto_presenter/asr/machine_check.py). Prints one JSON line per
configuration.

    python step_bench.py [model_dir]
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
REPO = "andrewleech/qwen3-asr-0.6b-onnx"
FILES = [
    "encoder.int4.onnx",
    "decoder_init.int4.onnx",
    "decoder_step.int4.onnx",
    "decoder_weights.int4.data",
    "embed_tokens.bin",
]
N_TOKENS = 8
PASSES = 10


def model_dir() -> Path:
    if len(sys.argv) > 1:
        return Path(sys.argv[1])
    from huggingface_hub import snapshot_download

    return Path(snapshot_download(REPO, allow_patterns=FILES))


def cpu_name() -> str:
    try:
        if sys.platform == "win32":
            import subprocess

            out = subprocess.run(
                ["powershell", "-NoProfile", "(Get-CimInstance Win32_Processor).Name"],
                capture_output=True, text=True, timeout=20,
            ).stdout
            return out.strip()
        if sys.platform == "linux":
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform.processor()


def main() -> None:
    import onnxruntime as ort

    m = model_dir()
    prompt = json.loads((HERE / "prompt.json").read_text())
    mel6 = np.load(HERE / "mel_6s.npy")
    mel2 = np.load(HERE / "mel_2s.npy")
    emb = np.fromfile(m / "embed_tokens.bin", dtype=np.float16).reshape(151936, 1024)
    for threads in (0, 4):
        so = ort.SessionOptions()
        if threads:
            so.intra_op_num_threads = threads
        t0 = time.perf_counter()
        enc = ort.InferenceSession(str(m / FILES[0]), so, providers=["CPUExecutionProvider"])
        init = ort.InferenceSession(str(m / FILES[1]), so, providers=["CPUExecutionProvider"])
        step = ort.InferenceSession(str(m / FILES[2]), so, providers=["CPUExecutionProvider"])
        load_s = time.perf_counter() - t0
        rows = []
        gen: list[int] = []
        for _ in range(PASSES):
            a = time.perf_counter()
            feats = enc.run(None, {"mel": mel6})[0]
            b = time.perf_counter()
            enc.run(None, {"mel": mel2})
            b2 = time.perf_counter()
            n_a = feats.shape[1]
            ids = prompt["before"] + [prompt["audio_pad"]] * n_a + prompt["after"]
            n = len(ids)
            logits, keys, vals = init.run(None, {
                "input_ids": np.array([ids], np.int64),
                "position_ids": np.arange(n, dtype=np.int64)[None],
                "audio_features": feats,
                "audio_offset": np.array([len(prompt["before"])], np.int64),
            })
            c = time.perf_counter()
            tok = int(np.argmax(logits[0, -1]))
            gen = [tok]
            pos = n
            for _ in range(N_TOKENS - 1):
                logits, keys, vals = step.run(None, {
                    "input_embeds": emb[tok].astype(np.float32)[None, None],
                    "position_ids": np.array([[pos]], np.int64),
                    "past_keys": keys,
                    "past_values": vals,
                })
                tok = int(np.argmax(logits[0, -1]))
                gen.append(tok)
                pos += 1
            d = time.perf_counter()
            rows.append((b - a, b2 - b, c - b2, d - c))
        r = np.median(np.array(rows[2:]) * 1000, axis=0)
        enc6, enc2, prefill, decode = (round(float(x)) for x in r)
        full = enc6 + prefill + decode
        incr = enc2 + prefill + decode
        print(json.dumps({
            "os": f"{platform.system()} {platform.release()}", "machine": platform.machine(),
            "cpu": cpu_name(), "cores": os.cpu_count(), "onnxruntime": ort.__version__,
            "threads": threads or "default", "load_s": round(load_s, 1),
            "encode_6s_ms": enc6, "encode_2s_ms": enc2, "prefill_ms": prefill,
            f"decode_{N_TOKENS - 1}_ms": decode,
            "full_pass_ms": full, "full_load_1s_step": round(full / 1000, 2),
            "incremental_pass_ms": incr, "incremental_load_2s_step": round(incr / 2000, 2),
            "tokens": gen, "expected_tokens": prompt.get("expected_tokens"),
        }), flush=True)


if __name__ == "__main__":
    main()
