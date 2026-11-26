# The Mac's speech engine on a rented GPU (Modal), 2026-11-26

Candidate A in `docs/roadmap/HOSTED_SPEECH.md`.

- **What runs:** the app's own streaming Qwen3-ASR engine
  (`asr/qwen_backend.py`: Qwen3-ASR 0.6B, encoder blocks and text prefix
  carried forward, Qwen3-ForcedAligner word times). It runs unchanged on MLX's
  CUDA backend, on an NVIDIA L4 rented from Modal, behind a websocket.
- **App side:** `asr/remote_backend.py` (`asr_remote` research tunable).
  - Audio goes out and words come back on their own threads, so the speech
    thread never waits on the network.
  - While the server is away or still waking, Whisper on the computer takes
    over after 3 s, and the app reconnects with backoff.
- **Server:** `research/spikes/qwen_gpu/` (`modal_app.py`, `speech_server.py`,
  `serve_local.py` to run it on a Mac). The protocol is documented in
  `modal_app.py`.
- **Auth:** tokens are signed with a shared secret (HMAC) and expire. Our
  server would mint them as it mints AI keys.

## Accuracy and delay (six public songs, streamed in real time)

`stream_eval.py`, the same six JamendoLyrics songs as the 2026-11-19 spike.
The client ran on the M1, and the server on Modal's L4 (`us-east` endpoint).

| Speech | WER | Heard | First words p50 / p90 | Final words p50 / p90 |
|---|---|---|---|---|
| **Qwen streaming on Modal L4** | **0.390** | 72% | **1.05 / 1.73 s** | **2.17 / 4.72 s** |
| Same server on the M1's own GPU (one song) | 0.408 | 73% | 0.96 s | 2.16 s |
| Mac's Qwen streaming (2026-11-19 spike) | 0.406 | — | 1.09 s | 2.14 s |
| DeepInfra Qwen 0.6B, 6 s windows | 0.437 | 77% | 1.99 s | 3.76 / 5.87 s |
| MLX Whisper small.en on the M1 | 0.466 | 71% | 2.14 s | 3.55 / 5.34 s |

**The rented GPU gives the same words, on the same time scale, as the Mac's
own engine.** The network costs nothing visible. It beats per-call hosting
by 1.6 s on final words.

## Start-up

**First try (2026-11-26 morning): about 2.5–3 min from zero.**

| Step | Time |
|---|---|
| Import MLX on the L4 | 1.7 s |
| Load both models (baked into the image) | 3.4 s |
| First step: MLX compiles its CUDA kernels | 31.7 s |
| Finding an L4 and pulling the image | most of the rest |

**Cut to 4.5–8.5 s (same day):**
- **The kernels are compiled at image build time.** `_compile_kernels`
  streams 40 s of a public-domain hymn through the engine on the same GPU
  type. MLX keeps its PTX in `MLX_PTX_CACHE_DIR`, and the driver keeps its
  binaries in `CUDA_CACHE_PATH`, both inside the image. In the container,
  start-up fell from 42.7 s to **1.8–4.3 s**.
- **GPU fallbacks:** `gpu=["L4", "A10", "L40S"]`. With L4 alone (and with
  `region="us"`), Modal sometimes queued the container for minutes waiting
  for a free L4.

| Cold start (container stopped first) | Total | In the container |
|---|---|---|
| 1 | 8.0 s | 3.6 s |
| 2 | 8.5 s | 4.3 s |
| 3 | 4.5 s | 1.8 s |
| 4 | 4.8 s | 2.0 s |
| 5 | 6.1 s | 2.4 s |
| 6 | 6.8 s | 2.4 s |

Kernels the build missed don't seem to matter. A song streamed straight
after a cold start scored WER 0.333, first words p50 0.66 s, final p50
1.92 s. That's no worse than warm (0.374 / 2.42 s on the same song).

**The risk left is capacity, not start-up.** When Modal has no GPU of these
types free, the wait is minutes. The app's fallback (Whisper on the
computer, after 3 s) covers that, and so would waking the GPU when the
setlist opens.

## Real-time gate (9 church recordings, 2026-11-26)

`run_follower_eval.py`, nosheet, real time. The processor path's follower
(ONNX base.en scorer, `score_follower_cpu`) with `asr_remote` streaming to the
L4. Whisper small.en on the processor is the fallback. On mains power with
Low Power Mode off, at 772c602.

| Speech | Wrong-slide | Perceived | Anticipation | Engine CPU |
|---|---|---|---|---|
| Whisper small.en on the processor (baseline) | 0.295 | 0.182 | +190 ms | 114% of a core |
| **Qwen streaming on Modal L4** | **0.292** | 0.196 | +739 ms | 78% |
| DeepInfra Qwen 1.7B (per call) | 0.320 | — | — | — |
| DeepInfra Qwen 0.6B (per call) | 0.352 | 0.246 | +470 ms | 81% |

Per recording, Modal against the baseline: 0.312/0.325, 0.227/0.204,
0.485/0.513, 0.270/0.233, 0.264/0.297, 0.320/0.300, 0.191/0.193, 0.472/0.434,
0.090/0.154. No audio was dropped, and the server failed no steps.

- **A tie on accuracy, not a win, because the next-line check is missing.**
  The server already runs the Mac's Qwen tuning (2026-11-21,
  `QWEN3_ASR_IMPROVEMENTS.md`): attention word times and the 0.5 flush
  filter are `QwenStreamingBackend` defaults. What the processor path lacks
  is the next-line check, which needs Qwen's audio tower on a GPU. On the
  Mac, turning it off took Qwen from about 0.24–0.27 to 0.299 (quick set).
  The baseline here has no check either.
- **What it does buy:** the best hosted result so far (DeepInfra was 0.320
  at best). It switches slides about 0.5 s earlier. It also uses about a
  third less of the computer's processor, which matters on the slow
  machines it's meant for.
- **One fault, not reproduced.** The first attempt ran on a container left
  over from the cold-start tests. Almost every step there failed with
  "Failed to load compiled gather_int32_uint32_1 kernel" (a kernel the build
  hadn't compiled), and the run was stopped. A fresh container, a
  `probe_gather` run and the full gate all ran clean. If it comes back, the
  server should restart its container when steps keep failing.

## Streams per GPU

One song (HILA), N copies streamed at once to one container:

| Streams | Final words p50 / p90 | WER |
|---|---|---|
| 1 (six-song run) | 2.14 s | 0.214 |
| 4 | 2.45–2.61 / 3.8–4.3 s | 0.19–0.23 |
| 8 | 7.4–7.8 / 10.3–11.3 s | 0.18–0.21 |

Steps run one at a time per container (`GPU_LOCK`), with no batching. So an
L4 carries **about 4 churches** before words come late. Accuracy doesn't
change. `modal_app.py` now takes 4 per container, and Modal starts more
containers beyond that. Batching steps across streams is the obvious way to
raise this.

## Cost, rough

- **Price:** an L4 on Modal is about $0.80/h, unpinned.
- **At 4 churches per L4:** about $0.20 per church-hour, so about $0.40–0.60
  per Sunday including warm-up and the 20-minute idle window. That's a
  couple of dollars a month per church.
- **A dedicated L4 per church:** about $1.60–2.40 per Sunday.

## Not yet done

- **The Qwen next-line check on the server:** its phone model reads Qwen's
  audio tower, which only the server has. Send its phones back and use
  `next_line.qwen_tuning()` while the server answers; with Whisper as the
  fallback, run without a check, as the processor path does today.
- **A health check on the server:** restart the container when steps keep
  failing (the kernel fault above).
- **Batching steps** across streams on one GPU.
- **Waking in the app:** under 10 s, so the GPU is woken when the AI starts,
  with no warm-up message. Whisper covers the first seconds, and any wait
  for capacity (already built).
- **Tokens from our server** in place of the prototype's minting script.
- **The privacy page**, if this ships.
