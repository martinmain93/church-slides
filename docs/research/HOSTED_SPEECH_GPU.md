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

| Step | Time |
|---|---|
| Import MLX on the L4 | 1.7 s |
| Load both models (baked into the image) | 3.4 s |
| First step: MLX compiles its CUDA kernels | 31.7 s |
| In the container in all (`load_s`) | 42.7 s |
| From zero, with no GPU warm, as a church would see it | about 2.5–3 min |

Most of the cold start is Modal finding an L4 and pulling the image. The
image is NVIDIA's CUDA *devel* image, which MLX needs for the headers it
compiles kernels with. Pinning `region="us"` left the container waiting for
an L4 on US Thanksgiving, and unpinned it scheduled at once.

**Ways to cut start-up, untried:**
- keep MLX's compiled kernels on a Modal volume;
- a slimmer image with only the CUDA headers;
- Modal's GPU memory snapshots.

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

- **The real-time gate** (9 church recordings vs 0.295). It sends church
  audio to Modal, which needs the user's OK first.
- **Batching steps** across streams on one GPU.
- **The next-line check on the server.**
- **Faster cold starts** (above).
- **The waking flow in the app:**
  - wake the GPU when a service opens;
  - say "starting the AI, about a minute";
  - Whisper covers the wait, already built.
- **Tokens from our server** in place of the prototype's minting script.
- **The privacy page**, if this ships.
