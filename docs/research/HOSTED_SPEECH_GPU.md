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
- **Server:** `speech-server/` (moved out of `research/spikes/qwen_gpu/`
  on 2026-11-27; the protocol is in its README).
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
(ONNX base.en scorer, `score_follower_cpu`) with `asr_remote` streaming to
Modal. Whisper small.en on the processor is the fallback. On mains power with
Low Power Mode off.

| Speech | Wrong-slide | Perceived | Anticipation | Engine CPU |
|---|---|---|---|---|
| Whisper small.en on the processor (baseline) | 0.295 | 0.182 | +190 ms | 114% of a core |
| **Qwen on Modal, with the next-line check on the server** | **0.243** | **0.144** | +966 ms | 82% |
| Qwen on Modal, no check (772c602) | 0.292 | 0.196 | +739 ms | 78% |
| DeepInfra Qwen 1.7B (per call) | 0.320 | — | — | — |
| DeepInfra Qwen 0.6B (per call) | 0.352 | 0.246 | +470 ms | 81% |
| *For reference: the Mac's own Qwen (2026-11-21)* | *0.249* | *0.174* | *+734 ms* | — |

Wrong-slide per recording (with check / no check / baseline):

| Recording | With check | No check | Baseline |
|---|---|---|---|
| 20260818-213104 | 0.228 | 0.312 | 0.325 |
| 20260818-213617 | 0.170 | 0.227 | 0.204 |
| 20260818-213825 | 0.436 | 0.485 | 0.513 |
| 20260818-214054 | 0.169 | 0.270 | 0.233 |
| 20260818-214759 | 0.273 | 0.264 | 0.297 |
| 20260818-215943 | 0.244 | 0.320 | 0.300 |
| 20260903-115044 | 0.147 | 0.191 | 0.193 |
| 20261002-185523 | 0.422 | 0.472 | 0.434 |
| 20261006-225539 | 0.098 | 0.090 | 0.154 |

- **The check is the win.** With it, the hosted path beats the processor
  baseline on all 9 recordings and matches the Mac's own Qwen (0.243 vs
  0.249). No audio was dropped, and the server failed no steps.
- **How it runs:** the server runs Qwen's audio tower and the Qwen phone
  model (`next_line_head_qwen.npz`) on its own audio, every 0.25 s of audio
  over the last 2.5 s, as the Mac does. It pushes the phone logits back
  (`"phones"`, about 5 KB/s). The app's check uses `next_line.qwen_tuning()`
  (`next_line.RemotePhones`). A check costs 17–21 ms on an A10.
- **While Whisper stands in,** no phones arrive and the check waits. That's
  the processor path's behaviour, which has no check.
- **Recording 213617 was rerun on its own.** In the full run, a leftover
  engine from an earlier stopped run was still holding the eval's port
  (8794), so that recording ran on old code. The no-check run's 213617 hit
  the same leftover. That engine had the same client code, so its number
  stands.

### The kernel fault: the wrong GPU's kernels

Twice, a container failed almost every step with "Failed to load compiled
gather_int32_uint32_1 kernel". `/health` reported "L4" because it printed
the first GPU asked for, but those containers had been given an **A10**
(compute 8.6). The kernels had been compiled at build on an L4 (8.9), and
MLX's cache is keyed by kernel name only. Fixes:
- **One kernel cache per architecture** (`_kernel_dirs`), compiled at build
  on an L4 and on an A10. L40S is also 8.9.
- **`/health` reports the real GPU** (`nvidia-smi`).
- **The container exits after 8 failed steps in a row**, so Modal replaces
  it. A server that answers without words keeps the app off its fallback.

### Lead time across a fallback switch (2026-11-27)

Recording 20261002-185523 (12 min) on the hosted path. The Modal app was
stopped at 204 s of audio and redeployed at 400 s
(`work/hosted_speech/outage.sh`).

| | Audio time | Measured delay (median, last 200 words) |
|---|---|---|
| On the server | 0–204 s | 2.0–2.2 s |
| Server stopped; Whisper takes over 18 s later | 204 s | 2.24 s |
| Whisper, settled | 267 s on | 2.7–2.97 s |
| Server back (22 s after the redeploy) | about 420 s | 2.7 s |
| On the server, settled | about 610 s on | 2.0 s |

- **The switch works both ways** with no error: words drop for about 18 s
  while Modal is still shutting the container (HTTP 500, then 404), and the
  app reconnects 22 s after the redeploy (backoff).
- **The lead follows in about 1 minute** going to Whisper, and **about
  3 minutes** coming back, since the median of 200 words moves only when
  half of them are new. The two engines differ by only about 0.6 s, so for
  those minutes slides are at most 0.6 s late, then up to 0.6 s early.
- **A cheap fix if it matters:** reset the delay window on a switch.

## Streams per GPU

One song (HILA), N copies streamed at once to one container:

| Streams | Final words p50 / p90 | WER |
|---|---|---|
| 1 (six-song run) | 2.14 s | 0.214 |
| 4 | 2.45–2.61 / 3.8–4.3 s | 0.19–0.23 |
| 8 | 7.4–7.8 / 10.3–11.3 s | 0.18–0.21 |

Steps run one at a time per container (`GPU_LOCK`), with no batching. So an
L4 carries **about 4 churches** before words come late. Accuracy doesn't
change. `speech-server/app.py` takes 4 per container, and Modal starts more
containers beyond that. Batching steps across streams is the obvious way to
raise this.

**With the next-line check on (2026-11-27, A10):** the same song, with each
stream asking for phones (`REMOTE_PHONES=1`):

| Streams | Final words p50 / p90 | WER | Phones late p50 / p90 | A check |
|---|---|---|---|---|
| 1 | 1.72 / 4.81 s | 0.220 | 0.09 / 0.15 s | 18 ms |
| 4 (one container) | 1.67–1.84 / 2.9–4.2 s | 0.19–0.23 | 0.11–0.12 / 0.18 s | 39 ms |
| 6 (Modal split them 4 + 2) | 1.71–1.92 / 2.9–4.7 s | 0.18–0.23 | 0.10–0.12 / 0.18 s | 23–39 ms |

The check doesn't lower the 4 streams per GPU: a check is 18–39 ms every
0.25 s, beside steps that take far longer. Beyond 4, Modal starts a second
container, as configured, so the cost estimate below stands.

## Cost, rough

- **Price:** an L4 on Modal is about $0.80/h, unpinned.
- **At 4 churches per L4:** about $0.20 per church-hour, so about $0.40–0.60
  per Sunday including warm-up and the 20-minute idle window. That's a
  couple of dollars a month per church.
- **A dedicated L4 per church:** about $1.60–2.40 per Sunday.

## Not yet done

- **Measuring cost with the check:** done (above), 4 per GPU holds.
- **Talk-only negatives:** done. 4 false moves in 265 min, fixed to 0
  (2d3d52d; see `docs/roadmap/HOSTED_SPEECH.md`).
- **The lead time across a switch:** done (above).
- **Batching steps** across streams on one GPU.
- **Waking in the app:** under 10 s, so the GPU is woken when the AI starts,
  with no warm-up message. Whisper covers the first seconds, and any wait
  for capacity (already built).
- **Tokens from our server** in place of the prototype's minting script.
- **The privacy page**, if this ships.
