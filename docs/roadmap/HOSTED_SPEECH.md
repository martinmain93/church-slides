# Hosted live speech for computers without Apple silicon

Status: **prototype measured, moving to production** (2026-11-27).

On Apple silicon, live AI hears the room with Qwen3-ASR on the computer's
GPU. Other computers (Windows, Intel Macs) run Whisper small.en on the
processor, which is clearly behind (0.295 wrong-slide on the real-time gate).
So these computers get the Mac's own engine, run for them on a rented GPU.

The prototype is measured in `docs/research/HOSTED_SPEECH_GPU.md`:

| Speech | Wrong-slide | Perceived |
|---|---|---|
| Whisper small.en on the processor (today) | 0.295 | 0.182 |
| **Qwen on our speech server, check on the server** | **0.243** | **0.144** |
| The Mac's own Qwen | 0.249 | 0.174 |

That's better on all 9 church recordings. DeepInfra (per call, 0.352) is
dropped.

## User story

> As the operator at a church whose plan includes hosted AI, running Church
> Slides on a Windows laptop, I start the service and the AI follows the
> songs as well as it would on a Mac. The first time, the app tells me in
> one screen that the service's audio will be sent online to be turned into
> words, and I agree once. If the internet drops, the slides keep going as
> best they can, and the AI picks up again by itself when it's back.

## Who gets it

- **Computers without Apple silicon**, signed in to an organization whose
  plan includes hosted speech. Apple silicon always stays on the computer.
- **The server decides the plan,** never the app (CONTRIBUTING.md,
  principle 5). No speech in the key answer means "not for this
  organization", and the app quietly uses the processor path, with no
  upgrade prompt.
- **Pilot:** free for every organization, with the daily allowance the AI
  gateway already has (6 hours of audio).
- **Default on.** Once the operator has agreed, hosted is what live AI uses
  on these computers. Settings → AI can turn it off.

## The one-time screen

Shown the first time live AI would start with hosted speech available on
this computer, and never again once answered.

> **Listen online?**
> On this computer, the AI follows songs best online. While the AI is on,
> the service's audio is sent to our speech server to be turned into words.
> Nothing is kept.
> [Use online] [Keep it on this computer]

Either answer can be changed later in Settings → AI.

## How it works

```
app ──sign-in──▶ api.churchslides.app  POST /v1/orgs/:id/ai/key
 │                 ◀── { key, base_url, …, speech: { url, token, expires_at } }
 └─engine──wss, 0.1 s audio frames──▶ speech server (Modal GPU)
          ◀── words (Qwen3-ASR streaming) + next-line phones every 0.25 s
```

1. **One key answer.** The existing AI key (`hosted_ai.rs`, renewed every
   5 minutes, 15-minute life, role `run_live`) gains a `speech` field: the
   speech server's URL and a token for it. No second key flow.
2. **The token** is HMAC-signed with a secret the Worker and the speech
   server share, and carries the organization id and an expiry. The speech
   server checks it on connect and needs no database.
3. **Audio goes straight to the speech server,** not through the Worker.
4. **The app's engine** uses `asr/remote_backend.py` with Whisper on the
   processor as the fallback, and the Qwen next-line check fed by the
   server's phones (`next_line.RemotePhones`, `qwen_tuning()`).
5. **The protocol is public** (`docs/server-protocol/`, a new "Speech"
   section): another server could run its own speech server.

## When it fails

The service never stops, and nothing shows an error to the room. Built and
tested in the prototype:

1. **No answer for 3 s:** Whisper on the processor takes over silently. The
   app reconnects with backoff and switches back.
2. **No processor path** (a computer too slow for it): the screen blanks,
   and the app retries with backoff, as for any absolute failure.
3. **A broken server** (steps failing) exits after 8 failures in a row, so
   Modal replaces it, rather than answering without words.

## Production plan

Decided 2026-11-27: the speech server lives in **this public repo**, and
runs in the **existing Modal workspace** with separate staging and
production environments.

### 1. Measure what's left (in progress)

- **GPU load with the check on:** done. 4 streams per GPU still hold
  (final words p50 1.7–1.8 s, phones 0.11 s late, a check 39 ms with 4 at
  once); beyond 4, Modal starts another container.
- **Talk-only false moves** (speech-negatives bench, public recordings).
- **Lead time across a switch:** the lead follows the median delay of the
  last 200 words, so after a switch it may lag about a minute behind.

### 2. Speech server out of research

- `speech-server/` at the repo root, from `research/spikes/qwen_gpu/`:
  `app.py` (Modal), `server.py` (websocket), `serve_local.py`, tests for
  the token check, the protocol and the watchdog.
- **Pinned image:** exact versions of MLX (CUDA), the models by revision,
  the phone head by hash. The engine code is copied from `sidecar/` at
  build, so a deploy is tied to one app commit.
- **Environments:** `modal deploy --env staging|production`. The token
  secret is a Modal Secret per environment, never a file.
- **Deploy from the Mac** (`scripts/deploy_speech.sh staging|production`),
  like the release pipeline, with no CI minutes.
- **Logs** say connections, GPU, step failures and timings, never words or
  audio.
- **Spend:** a workspace spend limit in Modal's dashboard (yours to set),
  and `max_containers` in the app.

### 3. Server (church-slides-server)

- `SPEECH_URL` and `SPEECH_TOKEN_SECRET` per environment; `/ai/key` adds
  `speech` when the plan includes it.
- Discovery capability `speech`.
- Allowance: speech minutes counted per key renewal against the daily
  audio allowance.
- Tests: roles, plan, expiry, a token the speech server accepts.

### 4. App

- `hosted_ai.rs` passes `speech` through to `hosted_ai.json`; the engine
  picks `asr_remote` from it on computers without Apple silicon, in place of
  the research tunable and environment variables.
- The connection opens when live AI starts (this also wakes the GPU, under
  10 s). Whisper covers the first seconds.
- The one-time screen, the Settings → AI switch, a small status pill for
  the operator while Whisper stands in.
- **Check on Windows**, the computers this is for: a real service played
  through the app, then a dropped network.
- Remove the DeepInfra code (`asr/hosted_backend.py`, `asr_hosted`).

### 5. Privacy, terms and release

- **`site/privacy.html` changes meaning:** it says today that live audio
  never leaves the computer. Hosted speech sends it to a server we run (on
  Modal, in the United States), which turns it into words and keeps
  neither. Name Modal as a service provider. `site/terms.html`: check.
- CHANGELOG line; `docs/FEATURES.md`; move this file to `docs/done/`.
- Release with the server change first, so older apps simply see no
  `speech`.

### Later

- **Batching steps** across streams, to raise 4 streams per GPU.
- **Regions** beyond us-east, if churches far away show late words.
