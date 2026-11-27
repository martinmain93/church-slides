# Speech server

Live speech for computers without Apple silicon: the Mac's streaming
Qwen3-ASR engine (`sidecar/auto_presenter/asr/qwen_backend.py`) and the
Qwen next-line check, on an NVIDIA GPU rented from Modal, behind a
websocket. The app streams the service's audio in and gets words and
next-line phones back (`asr/remote_backend.py`), with Whisper on the
computer standing in whenever the server is away.

Design and plan: `docs/roadmap/HOSTED_SPEECH.md`. Measurements:
`docs/research/HOSTED_SPEECH_GPU.md` (0.243 wrong-slide against 0.295 for
Whisper on the processor).

| File | |
|---|---|
| `app.py` | The Modal app: image, GPUs, scaling |
| `server.py` | The websocket (FastAPI), runs anywhere |
| `tokens.py` | Speech tokens: minting and checking |
| `serve_local.py` | The server on a Mac's GPU, for checking the protocol |
| `requirements.txt` | The image's packages, pinned |
| `warm/` | Public-domain audio the build compiles the GPU kernels with |
| `tests/` | The websocket with a fake engine |

## Deploy

```sh
scripts/deploy_speech.sh staging
scripts/deploy_speech.sh production     # clean tree, pushed commit
```

The image copies `sidecar/auto_presenter` in at build, so a deploy is tied
to one commit; `/health` reports it as `build`. Each build compiles GPU
kernels on an L4 and an A10 (about 5 minutes when the engine changed).

Each Modal environment needs, once:

```sh
modal environment create <env>
modal secret create --env <env> church-slides-speech SPEECH_TOKEN_SECRET=<random> \
    USAGE_URL=https://<church slides server>/internal/speech/usage
```

The Church Slides server for that environment holds the same secret.

## Test

```sh
cd sidecar && uv run --no-sync --with fastapi --with httpx python -m pytest ../speech-server/tests -q
```

## Protocol (version 0)

`GET /health` → `{"ok": true, "load_s", "gpu", "build"}`.

`wss://<host>/stream?token=<token>`. A missing, forged or expired token is
refused before the websocket opens (HTTP 403).

Tokens are `<payload>.<signature>`, both base64url without padding. The
payload is JSON `{"exp": <unix seconds>, "org": "<organization id>"}`; the
signature is HMAC-SHA256 of the payload text with the shared secret
(`tokens.py`).

| Direction | Message |
|---|---|
| server → client | `{"type": "ready", "load_s", "gpu", "build"}` once connected |
| client → server | `{"type": "start", "t0": <audio seconds of the first sample>, "phones": <bool>}` |
| client → server | binary: 16 kHz mono int16 little-endian PCM, contiguous from `t0` |
| server → client | `{"type": "words", "final": bool, "segment_id": int, "ends_segment": bool, "pass_s": float, "words": [[text, t_start, t_end, confidence], …]}` |
| server → client | `{"type": "phones", "t_end": <audio s>, "shape": [frames, phones], "logits": <base64 float16>, "ms": <server ms>}` every 0.25 s of audio, when `phones` was asked for |
| client → server | `{"type": "end"}`: flush the last words and close |

Word times are on the audio clock the client set with `t0`.

A stream the Church Slides server says to stop (the plan ended, or today's
allowance is used up) is closed with code 4402, and that organization's new
streams are refused (HTTP 403) for 15 minutes. The app's own engine takes
over either way.

## Usage reports

Every 60 s and when a stream closes, the server posts that stream's totals
to `USAGE_URL` (the Church Slides server's `/internal/speech/usage`):

```json
{"stream": "<id>", "org": "...", "user": "...", "audio_seconds": 600.0,
 "wall_seconds": 610.2, "gpu_seconds": 118.4, "gpu": "NVIDIA L4, 8.9",
 "build": "...", "final": false}
```

signed in `X-Speech-Signature` (HMAC-SHA256 of the body, base64url, with
the shared secret). `gpu_seconds` is the time the stream's steps and checks
held the GPU. The answer is `{"continue": bool}`. When the server can't be
reached, the stream goes on and the next report carries the totals.

## What it keeps

Nothing. It logs connections (organization id, duration, seconds of
audio, GPU seconds), timings and failures, never words or audio, and
reports the same counts to the Church Slides server.

## Failures

- **Steps failing 8 in a row** (a GPU kernel that won't load): the process
  exits, so Modal replaces the container. A server that answers without
  words would keep the app off its fallback.
- **No GPU free:** Modal takes the next type in `GPUS`, then waits; the app
  uses Whisper meanwhile.
