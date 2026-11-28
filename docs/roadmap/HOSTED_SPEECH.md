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
- **Talk-only false moves:** done, and fixed (below).
- **Lead time across a switch:** the lead follows the median delay of the
  last 200 words, so after a switch it may lag about a minute behind.

### 2. Speech server out of research (done 2026-11-27)

- **`speech-server/`** at the repo root: `app.py` (Modal), `server.py`
  (websocket), `tokens.py`, `serve_local.py`, tests (tokens, words, phones,
  the watchdog). The protocol is in its README.
- **Pinned:** every image package (`requirements.txt`), the models by
  revision, the phone model by SHA-256 from the app's own release, and
  public-domain warm-up audio in the repo. Nothing comes from this Mac.
- **Environments:** `staging` (live, checked end to end: WER 0.239, final
  words p50 1.69 s, every phone tick) and `production` in the existing
  workspace, each with its own token secret as a Modal Secret.
- **`scripts/deploy_speech.sh staging|production`:** production needs a
  clean tree at a pushed commit; it checks `/health` reports the build.
- **Logs:** connections (organization id, duration, seconds of audio),
  timings and failures, never words or audio.
- **Still yours:** a spend limit in Modal's dashboard.

### 3. Server (built 2026-11-27, church-slides-server branch `speech-usage`)

- **Plans** (`pilot`: ai + speech; `free`: none), the plan an organization
  is on in D1, with room for billing (source, external reference, dates).
- **A usage ledger** per organization: one record per gateway call or
  speech stream (who, how much, provider and model or GPU, estimated cost;
  never content), daily sums, kept 400 days. `GET /v1/orgs/:id/usage` for
  owners and admins. Design: the server repo's `docs/PLANS_AND_USAGE.md`.
- **`/ai/key`** answers `features` and `speech: {url, token, expiresAt}`.
  The gateway tags calls by feature (`agent` reserved for the in-app
  assistant).
- **The speech server reports** each stream every 60 s and at close; the
  answer is a lease, so an organization off the plan or over its allowance
  is stopped (4402) and falls back to Whisper.

### 4. App (built 2026-11-27)

- `hosted_ai.rs` keeps `features` and `speech` beside the AI key; the
  engine reads the token fresh at each connect (`api_keys.hosted_speech`).
- `start_audio_session.online_speech`: the operator's answer, only where
  Whisper would listen. The engine streams to the speech server with
  Whisper standing in.
- The one-time "Listen online?" question (`OnlineSpeechPrompt`, held during
  a service), Settings → AI → Listen online, and "on this computer" on the
  AI pill while Whisper stands in (`pipeline_health.speech_online`).
- **Not covered yet:** computers too slow for Whisper on the processor
  (no fallback there, so they'd need the blank-and-retry path), and
  removing the DeepInfra code.

### False moves during talk (fixed 2026-11-27, 2d3d52d)

The talk-only bench on the hosted path made **4 false moves in 265 min**
(the Mac's path and the processor-only control: 0). All four were the score
follower's in-song advances, let through the plan gate. Two causes:

- **Common-word phrases released the follower's talk hold.** Its talk
  latch let go after 3 words in phrases that are also in the song, and
  "the same", "have a", "for all" are in most songs. Qwen hears talk
  more completely than Whisper does, so it met them more often. Now only a
  phrase with a content word releases the hold, or counts as the song's
  first line.
- **Meeting-room tone read as a band.** The 6 s harmony mean passes the
  bar on some talk (one meeting reads as a band 25–30% of the time), and a
  band makes the gate treat the room as not talking. The gate now needs
  the band held for 45 s (`BandPresence.sustained`). In the goldens, none
  of the 198 sung in-song moves needed the band for that exemption at
  under 45 s.

Measured in real time after the fix:

| | before | after |
|---|---|---|
| Hosted talk bench, the 3 recordings that moved (146 min) | 4 moves | **0** |
| Hosted sung gate (9 church goldens) | 0.243 | 0.240 |
| Mac MLX sung gate | 0.277 (2026-11-19) | 0.244 |

The other 5 talk recordings (120 min) made no moves before and the change
only holds more, so they weren't rerun. The Mac run was in Low Power Mode;
its "before" is on older code, so it shows no regression, not a gain.

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
