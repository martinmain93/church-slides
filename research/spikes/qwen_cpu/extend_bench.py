"""Does prefilling only the new audio tail (decoder_extend) match a full prefill, and how much faster is it?

A live pass as step_bench.py times it prefills the whole 6 s prompt (96
tokens). Streaming as the Apple-silicon backend does it keeps the key/value
cache of the prompt head and older audio, and prefills only the newest audio
plus the text after it. This times that with `decoder_extend` (made by
export_extend.py): the cache of everything before the newest TAIL seconds
comes from an earlier pass, so only the tail and the closing text are
prefilled.

For each tail it checks the first-token logits and the decoded tokens
against a full prefill, and reports load = (encode 2 s + extend + decode) / 2 s.

    python extend_bench.py MODEL_DIR EXTEND_ONNX [threads ...]

MODEL_DIR holds the published int4 files (step_bench.py's FILES). Default
threads: 0 (ONNX Runtime's default) and 4.
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
N_TOKENS = 8
PASSES = 10
TOKENS_PER_S = 13  # 78 audio tokens for 6 s
TAILS_S = (2, 4)


def main() -> None:
    import onnxruntime as ort

    sys.path.insert(0, str(HERE))
    from step_bench import FILES, cpu_name

    m = Path(sys.argv[1])
    extend_path = sys.argv[2]
    threads_list = [int(t) for t in sys.argv[3:]] or [0, 4]
    prompt = json.loads((HERE / "prompt.json").read_text())
    mel6 = np.load(HERE / "mel_6s.npy")
    mel2 = np.load(HERE / "mel_2s.npy")
    emb = np.fromfile(m / "embed_tokens.bin", dtype=np.float16).reshape(151936, 1024)
    before, after, pad = prompt["before"], prompt["after"], prompt["audio_pad"]

    for threads in threads_list:
        so = ort.SessionOptions()
        if threads:
            so.intra_op_num_threads = threads
        cpu = ["CPUExecutionProvider"]
        enc = ort.InferenceSession(str(m / FILES[0]), so, providers=cpu)
        init = ort.InferenceSession(str(m / FILES[1]), so, providers=cpu)
        step = ort.InferenceSession(str(m / FILES[2]), so, providers=cpu)
        ext = ort.InferenceSession(extend_path, so, providers=cpu)

        feats = enc.run(None, {"mel": mel6})[0]
        n_a = feats.shape[1]

        def prefill(n_audio: int):
            # A partial prompt stops at its audio; the full one ends with the text after.
            ids = before + [pad] * n_audio + (after if n_audio == n_a else [])
            n = len(ids)
            return init.run(None, {
                "input_ids": np.array([ids], np.int64),
                "position_ids": np.arange(n, dtype=np.int64)[None],
                "audio_features": np.ascontiguousarray(feats[:, :n_audio]),
                "audio_offset": np.array([len(before)], np.int64),
            }), n

        def decode(logits, keys, vals, pos):
            tok = int(np.argmax(logits[0, -1]))
            gen = [tok]
            for _ in range(N_TOKENS - 1):
                logits, keys, vals = step.run(None, {
                    "input_embeds": emb[tok].astype(np.float32)[None, None],
                    "position_ids": np.array([[pos]], np.int64),
                    "past_keys": keys, "past_values": vals,
                })
                tok = int(np.argmax(logits[0, -1]))
                gen.append(tok)
                pos += 1
            return gen

        # Reference: full prefill.
        full_t = []
        for _ in range(PASSES):
            a = time.perf_counter()
            (ref_logits, rk, rv), n_full = prefill(n_a)
            full_t.append(time.perf_counter() - a)
        ref_tokens = decode(ref_logits, rk, rv, n_full)
        enc2_t, dec_t = [], []
        for _ in range(PASSES):
            a = time.perf_counter()
            enc.run(None, {"mel": mel2})
            enc2_t.append(time.perf_counter() - a)
            a = time.perf_counter()
            decode(ref_logits, rk, rv, n_full)
            dec_t.append(time.perf_counter() - a)
        med = lambda xs: round(float(np.median(np.array(xs[2:]) * 1000)))  # noqa: E731
        enc2, full, dec = med(enc2_t), med(full_t), med(dec_t)
        row = {
            "os": f"{platform.system()} {platform.release()}", "machine": platform.machine(),
            "cpu": cpu_name(), "cores": os.cpu_count(), "onnxruntime": ort.__version__,
            "threads": threads or "default", "encode_2s_ms": enc2, "decode_7_ms": dec,
            "full_prefill_ms": full, "full_prefill_tokens": n_full,
            "full_load_2s_step": round((enc2 + full + dec) / 2000, 2),
            "ref_tokens": ref_tokens, "expected_tokens": prompt.get("expected_tokens"),
        }

        for tail_s in TAILS_S:
            tail = tail_s * TOKENS_PER_S
            (_, pk, pv), p = prefill(n_a - tail)
            embeds = np.concatenate(
                [feats[:, n_a - tail :], emb[after].astype(np.float32)[None]], axis=1
            ).astype(np.float32)
            s = embeds.shape[1]
            feed = {
                "input_embeds": embeds,
                "position_ids": np.arange(p, p + s, dtype=np.int64)[None],
                "past_keys": pk, "past_values": pv,
            }
            times = []
            for _ in range(PASSES):
                a = time.perf_counter()
                logits, keys, vals = ext.run(None, feed)
                times.append(time.perf_counter() - a)
            tokens = decode(logits, keys, vals, p + s)
            diff = float(np.max(np.abs(logits[0, -1] - ref_logits[0, -1])))
            t = med(times)
            row[f"tail_{tail_s}s"] = {
                "extend_tokens": s, "extend_ms": t, "max_logit_diff": round(diff, 3),
                "same_tokens": tokens == ref_tokens,
                "load_2s_step": round((enc2 + t + dec) / 2000, 2),
            }
        print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
