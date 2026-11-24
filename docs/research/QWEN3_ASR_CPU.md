# Qwen3-ASR on a processor (Intel Macs, Windows)

**Question (2026-11-24):** can the Qwen3-ASR speech engine that 0.3.0 runs
on Apple silicon's GPU also run live on the processor of an Intel Mac or a
Windows PC, so live AI isn't Apple-silicon only?

**Answer so far: plausible on a decent 4-core machine, once prefill reuses
its cache.** The model runs and transcribes correctly through ONNX Runtime,
with no PyTorch. As first timed, one live pass cost 0.62–1.21 of a 2-second
step on a 4-core x86 server CPU, for speech alone, against the old processor
path's bar of 0.75 for *everything* (speech plus the lyric scorer). Prefilling
only the newest audio (below) cuts that to 0.28–0.56. The lyric scorer still
has to fit in what's left.

## How it was measured

- Runtime: ONNX Runtime 1.32 on the CPU. The int4 export
  `andrewleech/qwen3-asr-0.6b-onnx` (Apache 2.0): `encoder.int4.onnx`
  (FP32 weights), `decoder_init.int4.onnx` (prefill),
  `decoder_step.int4.onnx` (one token, with a key/value cache), and
  `embed_tokens.bin`. About 2 GB on disk.
- One live pass, as the Apple-silicon backend does it: encode audio, prefill
  the prompt (78 audio tokens for 6 s plus 18 text tokens), and decode 8
  tokens. "Incremental" encodes only the newest 2 s, with a pass every 2 s.
  "Full" encodes the whole 6 s window, with a pass every 1 s.
- Load is pass time divided by step time. Medians are over 8 passes.
- Audio: 6 s of a public-domain LibriVox hymn
  (`research/spikes/qwen_cpu/`). Every machine decoded the same 8 tokens,
  so the speeds compare like for like.
- Script: `research/spikes/qwen_cpu/step_bench.py`. The runners use
  `.github/workflows/qwen-cpu-bench.yml`, which fires only on pushes to
  `bench/qwen-cpu`.

## Results

| Machine | ONNX Runtime | Encode 2 s | Prefill | Decode 7 | Incremental load (2 s step) | Full load (1 s step) |
|---|---|---|---|---|---|---|
| M1, processor (arm64) | 1.32 | 414 ms | 417 ms | 162 ms | **0.50** | 1.20 |
| GitHub Linux, 4-core EPYC 7763 | 1.32 | 258 ms | 740 ms | 240 ms | **0.62** | 1.40 |
| GitHub Windows, same CPU, 4 threads | 1.32 | 260 ms | 1,174 ms | 395 ms | **0.91** | 2.05 |
| GitHub Windows, default threads | 1.32 | 426 ms | 1,661 ms | 332 ms | **1.21** | 2.46 |
| M1, processor | 1.19.2 (what the app ships) | — | 1,466 ms | 3,950 ms | 6.0 s a full pass | 6.01 |
| M1 as Intel code under Rosetta | 1.23 | — | 3,247 ms | 9,399 ms | 13.9 s a full pass | 13.9 |

- **Shipped runtime:** ONNX Runtime 1.19.2, the version the app ships, is far too slow for int4 (560 ms a token against 26 ms on 1.32). Shipping this means upgrading the engine's ONNX Runtime, which also runs the chord, search and scorer models.
- **Full precision:** FP32 is slower than int4 on the processor: 2.42 full load on the M1.
- **Rosetta:** it isn't a stand-in for a real Intel chip. It can't use the vector instructions ONNX Runtime's fast kernels need.
- **Windows runner:** it is markedly slower than Linux on the same CPU, mostly in prefill. Shared virtual machines are noisy, so treat 0.91–1.21 as a range.

## Cutting prefill (2026-11-24)

Prefill re-read the whole 6 s prompt (96 tokens) every pass, and it was the
largest cost on x86. The published `decoder_init` always starts from an
empty cache and `decoder_step` takes one token, so neither can continue from
a cached prefix. `research/spikes/qwen_cpu/export_extend.py` exports a third
graph, `decoder_extend`: it takes any number of new tokens plus the cache,
as the Apple-silicon backend's streaming does. Only the newest audio and the
closing text are prefilled, and the cache of the prompt head and older audio
comes from earlier passes. It was quantized to int4 the same way as the
published files (RTN, block 64), about 340 MB.

- **Correct:** at full precision the logits match a full prefill exactly
  (difference 0.0). At int4 every machine decoded the same 8 tokens.
- **Timed:** with `extend_bench.py`. The workflow builds the graph on a Linux
  runner from the public model, then times it on Windows and Linux.

Load = (encode 2 s + prefill + decode 7) / 2 s step:

| Machine | Threads | Full prefill (96 tokens) | Newest 2 s (35) | Newest 4 s (61) | Load, full | Load, newest 2 s | Load, newest 4 s |
|---|---|---|---|---|---|---|---|
| GitHub Windows, EPYC 7763 | default | 1,627 ms | 461 ms | 802 ms | 1.15 | **0.56** | 0.73 |
| GitHub Windows, EPYC 7763 | 4 | 1,031 ms | 318 ms | 533 ms | 0.92 | **0.56** | 0.67 |
| GitHub Linux, Xeon 6973P | default | 466 ms | 157 ms | 268 ms | 0.43 | **0.28** | 0.34 |
| M1 processor, battery + Low Power Mode | default | 439 ms | 147 ms | 238 ms | 0.54 | **0.39** | 0.44 |

- Prefill is about 3× cheaper. On Windows the whole pass drops from about 1.0
  to 0.56 of the step.
- The Linux runner landed on a newer Xeon this time, not the EPYC of the
  first run, so compare its rows only with each other. Its 4-thread row was
  noisy (encode 437 ms against 192 ms) and is left out.
- How much is "newest" depends on the streaming design. The Apple-silicon
  backend caches finished 8 s blocks, so its tail runs from 0 to 8 s. A CPU
  backend could keep the cache at every pass (tail 2 s) as long as the
  window has not slid. Once the window slides, the oldest audio drops out of
  the cache. The rotary positions of what's left still work, because they are
  relative.

## What it would take

1. **A CPU backend that streams with `decoder_extend`.** It needs to cache
   per pass, encode only new audio, and reset the cache when the window
   slides. It also needs a hosted copy of the int4 graph, since the published
   export has none.
2. **The follower's lyric scorer also needs the processor.** It is Whisper,
   run with ONNX on the CPU path. Speech plus scorer must fit under the bar
   together.
3. **The machine check would time Qwen on each computer** and offer live AI
   only where it keeps up, as it did for the old CPU path.
4. **GPU (DirectML on Windows) is unmeasured.** GitHub's runners have no
   GPU. A public ONNX Runtime report found the processor faster than the
   GPU for this model on an integrated-graphics laptop
   ([onnxruntime#27809](https://github.com/microsoft/onnxruntime/issues/27809)).
