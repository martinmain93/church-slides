# Hosted live speech for computers without Apple silicon

Status: **design, awaiting approval** (2026-11-25).

On Apple silicon, live AI hears the room with Qwen3-ASR on the computer's
GPU. That's our most accurate engine. Other computers (Windows, Intel Macs)
run faster-whisper on the processor, which is clearly behind: 0.295
wrong-slide on the real-time gate. Qwen on the processor didn't close the
gap (0.323, `docs/research/QWEN3_ASR_CPU.md`). So these computers get the
same model, run for them online.

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
  principle 5). The app asks for a speech key. A `402 not_on_plan` answer
  means "not for this organization", and the app quietly uses the
  processor path, with no upgrade prompt.
- **Pilot:** free for every organization. Signing up needs Google sign-in
  and the pilot passcode, as today.
- **Default on.** Once the operator has agreed, hosted is what live AI uses
  on these computers. Settings → AI can turn it off.

## The one-time screen

This screen is shown the first time live AI would start with hosted speech
available on this computer, and never again once answered.

> **Listen online?**
> On this computer, the AI follows songs best online. While the AI is on,
> the service's audio is sent in short pieces to DeepInfra to be turned
> into words. Nothing is kept, and we never receive the audio.
> [Use online] [Keep it on this computer]

- **"Keep it on this computer"** uses the processor path (or no live AI, if
  the computer can't keep up).
- **Either answer can be changed later** in Settings → AI.

## How it works

```
app ──sign-in token──▶ our server ──API key──▶ DeepInfra: mint scoped JWT
 │                         ◀── { base_url, model, token, expires_at }
 └─engine──Bearer JWT, 2 s steps──▶ DeepInfra /v1/openai/audio/transcriptions
```

1. **Key.** `POST /v1/orgs/:orgId/ai/speech-key` (role `run_live`). The
   server checks membership and plan, then calls DeepInfra's
   `POST /v1/scoped-jwt` with `models: ["Qwen/Qwen3-ASR-…"]`,
   `expires_delta: 120` and a small `spending_limit`. It returns
   `{ base_url, model, token, expires_at }`. The server's DeepInfra key
   never leaves the server.
2. **Refresh.** The app asks for a new key every 60 s, and holds the old
   one until the new one arrives. Leaving the organization, or the plan
   ending, stops speech within two minutes.
3. **Audio goes straight to DeepInfra,** not through our server. This takes
   one hop out of every 2 s step, which matters for a church far from a
   Cloudflare region. DeepInfra lists the Qwen3-ASR models as zero
   retention.
4. **The protocol stays vendor-neutral** (server PLAN.md, decision 4). The
   key answer is a generic OpenAI-compatible transcription endpoint plus a
   bearer token. The app has no DeepInfra-specific code, and another server
   could point it at its own endpoint. Discovery adds the capability
   `speech`.
5. **Engine:** a hosted backend behind the `ASRBackend` interface, like the
   deprecated Groq one. It sends the sliding window every step and asks for
   word timestamps (`timestamp_granularities=word`, needed by the
   follower). The key reaches the engine over the existing local socket and
   is held in memory only.

The existing AI gateway (`/ai/v1`, Groq / Workers AI behind signed `csa_`
keys) stays for chat. Speech moves off it.

## When it fails

The service never stops, and nothing shows an error to the room.

1. **A failed step is retried silently** for a few seconds (about 3 s).
2. **If the computer can run the processor path,** the AI switches to it
   silently, with a small status pill for the operator only. It keeps trying
   online in the background, and switches back when calls succeed again.
3. **Absolute failure** (nothing online, no processor path) **blanks the
   screen**, then retries online with backoff: 2 s, 4 s, 8 s and so on,
   capped at 60 s, with a little random spread. When a call succeeds, live AI
   carries on from the room's current position.

Reading of "silent fall-back" plus "blank on absolute failure": blanking
only happens when the processor path can't take over.

## Privacy and terms

Both pages are updated in the same change as the feature:

- **`site/privacy.html`:** the summary line "Audio … is never sent to us"
  stays true. A new section, *Live AI online*, covers:
  - what is sent: the service's audio, in short pieces, only while live AI
    is on and only after the operator agreed;
  - where it goes: DeepInfra, which turns it into words and keeps nothing,
    named as a service provider;
  - what we keep: the account, and that a speech key was issued, never the
    audio or the words;
  - how to turn it off: Settings → AI.
- **`site/terms.html`:** no change expected. To be checked.

## Before building: measure

The gate is the real-time eval, played through DeepInfra from this Mac:

- **The models:** Qwen3-ASR 0.6B vs 1.7B vs whisper-large-v3-turbo.
- **The bar:** wrong-slide against the processor path (0.295) and the MLX
  path. Also the round-trip latency at p50 and p90, since a slow p90 makes
  slides late.
- **What's needed:** a DeepInfra account and API key with the `inference`
  and `account_scoped_jwt` scopes. The church recordings stay on the Mac.
  This sends their audio to DeepInfra (zero retention), which needs your OK
  as a one-off for the eval.

## Prerequisites and order

1. **Measure** (above). Pick the model.
2. **Server:** the `speech-key` endpoint, the `speech` capability, and the
   DeepInfra key as a Worker secret. Tests for roles, plan and expiry.
3. **App:**
   - hosted backend and key refresh;
   - the one-time screen and the Settings → AI switch;
   - the fallback and backoff;
   - privacy page and CHANGELOG.
4. **Ship sign-in in released builds.** Account features are
   development-only today, and this needs them in the pilot build.
