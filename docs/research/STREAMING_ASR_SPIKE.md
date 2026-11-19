# Streaming speech services and Qwen3-ASR for live following

Spiked 2026-11-19. The question: could live following get words in a few
hundred milliseconds from a streaming speech service (Deepgram, AssemblyAI)
instead of chunked Whisper's 2–4 s? Qwen3-ASR (open weights) was added mid-spike
because Qwen reports it beating Whisper on singing.

## The answer

**Drop the streaming services as the live engine. They are fast, but mostly
deaf to singing.** On six public pop and rock songs, Deepgram Nova-3 and
Flux heard under a fifth of the sung words, and AssemblyAI's current model
(STT-3.7) heard almost none. Only AssemblyAI's older Universal-Streaming
model heard a useful share: about half, with the song's lyrics as keyterms.
That's still well short of Whisper, and it's the model AssemblyAI lists as
"replaced". On cleaner sung hymns every service did better (39–55% of
words). AssemblyAI Universal-Streaming even edged past local Whisper
small.en there, but not past Groq's Whisper or Qwen. A live engine has to
cope with band mixes, and they don't.

**Run a bigger trial of Qwen3-ASR 0.6B as a local engine.** Through the app's
own 6 s window it heard more sung words than Whisper small.en (77% against
71% on band mixes, 60% against 48% on hymns), put each word within 0.1 s of
when it was sung, and showed a word about half a second sooner (1.6 s against
2.1 s). On band mixes it makes a few more mistakes (error rate 0.51 against
0.47), on hymns fewer (0.46 against 0.53). Given whole songs it beats even
Whisper large-v3-turbo (0.30 against 0.36), so the window is what holds it
back. Giving it the lyrics as context breaks it.
Ways to make it faster and safer are listed under "Next" and have their own
spike (`docs/research/QWEN3_ASR_IMPROVEMENTS.md`, in progress).

## What was measured

Each clip was streamed in real time, 80 ms at a time, to every service at
once, and every message was logged with when it arrived. Whisper (local MLX
and Groq) and Qwen went through the app's own `SlidingWindowWrapper`, so
their delays are the ones the app sees: window end plus the pass time, plus
the 1.5 s stability margin for a final word.

- **Songs:** six JamendoLyrics tracks (three pop, three rock; Creative
  Commons), 1,295 sung words with hand-made word timings. Busy band mixes:
  harder than most worship. Local Whisper gets nearly half the words wrong
  here, against about a quarter on our worship recordings.
- **Hymns:** six LibriVox volunteer recordings (public domain), 1,488
  words. Only slide-level timing exists, so they count words, not delays.
- The church's own recordings were never used; only public audio went to
  any service.

## Results: songs (1,295 words)

| Engine | Error rate | Words heard | First sight of a word | Final word | Word timing error |
|---|---|---|---|---|---|
| Local MLX Whisper small.en (today) | 0.47 | 71% | 2.1 s | 3.6 s | 0.11 s |
| Groq Whisper large-v3-turbo | 0.42 | 72% | 2.5 s | 3.7 s | 0.11 s |
| **Qwen3-ASR 0.6B (local)** | 0.51 | **77%** | **1.6 s** | 3.4 s | 0.10 s |
| Qwen3-ASR 1.7B (local) | 0.47 | 73% | 3.6 s | 5.4 s | 0.08 s |
| Qwen3-ASR 0.6B given the lyrics | 0.82 | 79% | 2.7 s | 5.2 s (1 in 10 over 86 s) | 0.49 s |
| AssemblyAI Universal-Streaming + lyrics | 0.65 | 53% | 0.8 s | 1.0 s | 0.2 s after a fixed 0.9 s offset |
| AssemblyAI Universal-Streaming | 0.66 | 37% | 0.8 s | 0.9 s | same |
| AssemblyAI STT-3.7 Realtime (± lyrics) | 1.00 | 0.4% | — | — | — |
| Deepgram Nova-3 (± lyrics) | 0.89 | 18–19% | 0.6 s | 2.6–2.8 s | 0.06 s |
| Deepgram Flux (± lyrics) | 0.83–0.89 | 12–18% | 0.9–1.1 s | 9–12 s | 0.1 s |

Delays are medians, from the moment a word finished being sung. "Words heard"
counts a word if any message, interim or final, carried it within 1.5 s of
where it was sung.

By genre, AssemblyAI Universal-Streaming with lyrics came close to Whisper on
the three pop songs (59–71% against 67–77% matched) and fell away on rock
(0–65%). Nova-3 and Flux stayed under 35% on every song.

## Results: hymns (1,488 words)

| Engine | Error rate | Words matched |
|---|---|---|
| Groq Whisper large-v3-turbo | 0.45 | 58% |
| **Qwen3-ASR 0.6B (local)** | 0.46 | **60%** |
| AssemblyAI Universal-Streaming (± lyrics) | 0.50 | 53–55% |
| AssemblyAI STT-3.7 Realtime | 0.52 | 48% |
| Local MLX Whisper small.en (today) | 0.53 | 48% |
| Deepgram Nova-3 (± lyrics) | 0.59–0.62 | 39–42% |
| Deepgram Flux (± lyrics) | 0.63 | 39% |

Whisper, Groq and Qwen went through the same window as on the songs. Whole
hymns at once, Qwen 0.6B's error rate is 0.41. On hymns, lyric keyterms
barely mattered (±3 points), unlike the band mixes, where they lifted
AssemblyAI from 37% to 53%.

STT-3.7 hears hymns but not band mixes. Flux and STT-3.7 only finalise a
"turn" when the singing stops, so their final words came 9–26 s late; only
their interim results would be usable live.

## Whole song at once (accuracy ceiling)

How well each model does with the whole song in one go, which is roughly what
vendors publish. Not what the app sees live.

| Model | Error rate | Words matched |
|---|---|---|
| Qwen3-ASR 0.6B | 0.30 | 76% |
| Qwen3-ASR 1.7B | 0.29 | 79% |
| Whisper large-v3-turbo (MLX) | 0.36 | 67% |
| Whisper small.en (MLX) | 0.43 | 59% |

Given whole songs, Qwen beats both Whisper models clearly, matching Qwen's
own claims. But the windows treat the two very differently: Whisper small.en
loses little to them (0.43 whole song, 0.47 windowed), while Qwen 0.6B loses a
lot (0.30 against 0.51). Words get cut at the window edge, and the model sees
no earlier lines. That gap is Qwen's headroom: keeping its earlier text
between passes, which is how its own streaming recipe works, should win back
much of it. This is idea 4 in the follow-up spike.

## Why the lyrics hurt Qwen but help AssemblyAI

The streaming services take lyrics as *keyterms*: a nudge toward those words
when the sound is close. Qwen takes them as *context*, text the model reads
before it writes. Given the whole song's lyrics, Qwen writes lyrics that
nobody has sung yet, or the whole song at once (1.7B's whole-song error rate
with lyrics was 1.7: more wrong words than sung ones). For a follower, an
unsung line is the worst possible input: it moves the slide early. The safe
way to use lyrics with Qwen is to score a few candidate lines against the
audio rather than let it write freely, as the Whisper next-line check does.

## Speed and size on this Mac (M-series, MLX)

**Caveat: every run here was on battery with Low Power Mode on** (it's set to
switch on whenever the Mac is on battery). Low Power Mode has slowed engine
passes badly before (`docs/research/LIVE_LATENCY.md`; the 2026-10-14
low-battery lag). So the local pass times, and the local engines' first-sight
and final delays, are probably pessimistic. Whisper and Qwen were throttled
alike, so they still compare fairly with each other. Accuracy is unaffected,
and the cloud services' delays are mostly network. Re-time on mains power
before quoting absolute numbers.

| Model | Seconds per 6 s pass (p50 / p90) | Download |
|---|---|---|
| Whisper small.en | not timed separately here | 464 MB |
| Qwen3-ASR 0.6B + aligner | 0.85 / 1.14 | ~1.2 GB + 1.2 GB aligner (fp16) |
| Qwen3-ASR 1.7B + aligner | 2.0 / 3.1 | ~4.7 GB + aligner |

The 0.6B fits the app's pacing. The 1.7B is too slow: the wrapper backs off
to a pass every 3 s, which is where its extra 2 s of delay comes from.
Qwen runs on Apple silicon through `mlx-qwen3-asr` (Apache 2.0); a CPU build
for Intel Macs and Windows would need the pure-Rust `qwen-asr` crate or an
ONNX export, not checked here.

## Cost per service hour

| | Price | Notes |
|---|---|---|
| Deepgram Nova-3 streaming | $0.29/h, +$0.08 with keyterms | promo price; $200 free credit |
| Deepgram Flux | $0.39/h, + keyterms | |
| AssemblyAI STT-3.7 Realtime | $0.30/h, keyterms included | billed while the connection is open, idle included |
| AssemblyAI Universal-Streaming | $0.15/h | listed as replaced by STT-3.7 |
| Groq Whisper turbo | $0.04/h list, **~$0.13/h for us** | 10 s minimum per request, and we send a 6 s window every 3 s |
| Workers AI Whisper turbo | ~$0.03/h list, ~$0.06/h for us | our windows overlap, so each second is sent twice |
| Qwen3-ASR hosted (Alibaba `qwen3-asr-flash-realtime`) | not confirmed (~$0.02/h per a third-party post) | Singapore and Beijing; no word timestamps checked |
| Qwen3-ASR or Whisper local | $0 | |

At these prices, a 90-minute weekly service costs a church $20–40 a year on a
streaming service. Price isn't what rules them out.

## Regions, data and tokens

- **Regions.** Deepgram has EU, Australia and India endpoints (Flux
  included; requests never leave the region, and fail if it's down).
  AssemblyAI has an EU streaming host. Neither covers every region a church
  might ask for.
- **Data.** Deepgram uses audio for model improvement unless each request
  sets `mip_opt_out=true` (which may cost more). AssemblyAI keeps no streaming
  audio or transcripts once the account opts out of training, which only
  paid accounts can do, by email.
- **Short-lived tokens** work for both, so a slides computer could connect
  directly with a token our server mints:
  - Deepgram: `POST /v1/auth/grant` (30 s default, up to 1 h), sent as
    `Authorization: Bearer`. An open stream outlives the token. Minting needs a
    key with at least Member permission: the key used here wasn't, and got 403.
  - AssemblyAI: `POST https://token.assemblyai.com/v1/token` (1–600 s, not
    single-use), passed as `?token=` on the WebSocket URL.
  - Both let the client replace keyterms mid-stream, so a song change could
    swap in the next song's lyrics without reconnecting.
- **Groq's free tier** allows 20 requests a minute, and the app's 3 s step
  sits exactly on it. The first Groq run here had 92 refused windows; paced at
  3.5 s it had none. This is the same stall as the 2026-10-13 service.

## How it would fit (not built)

**A streaming backend.** `ASRBackend.transcribe(frames)` already takes an
iterator and returns one, so a streaming service fits it directly, without
`SlidingWindowWrapper`:

- `sidecar/auto_presenter/asr/assemblyai_backend.py` (or Deepgram): a thread
  owns one WebSocket for the session and sends each `AudioFrame` as 16-bit PCM
  as it arrives. Interim words become `TranscriptEvent(is_final=False)`, and
  words marked final become `is_final=True` with timestamps shifted onto the
  frame clock. AssemblyAI's fixed ~0.9 s timing offset gets subtracted here.
- Reconnect on drop, with a fresh token, and keep yielding nothing meanwhile.
  "Anything online fails quietly" holds as it does for Groq: a gap, not an
  error dialog.
- On a song change, send the new song's lyric lines as keyterms
  (`UpdateConfiguration` / `Configure`).
- The pipeline would need its first non-wrapped backend: today
  `audio_session.py` always wraps. A `streaming = True` attribute on the
  backend could skip the wrapper.

**Server tokens.** The church's server account holds the provider key. The
app asks the server for a token at Start (signed in as the organisation),
and the server checks the allowance, mints a 60 s token with a session cap,
and records the minutes. No audio passes through our server.

**A Qwen backend** fits exactly where `MLXWhisperBackend` does: a batch
`transcribe()` over the window, with the aligner for word times. The spike's
`QwenBackend` (in `qwen_eval.py`) is that, minus model management.

## Next

1. **Qwen3-ASR 0.6B: run the real-time follower eval**
   (`research/eval/run_realtime_eval.py`) on the church goldens, locally, before
   any decision. Word numbers aren't slide numbers. A perfect transcript alone
   didn't beat Whisper before (`ASR_ENGINE.md`), because the follower's timing
   is the binding constraint.
2. **Make Qwen faster and use the lyrics safely**, in the separate spike:
   4-bit weights and speculative decoding; scoring candidate next lines
   instead of free writing; the forced aligner as a position signal; keeping
   work between passes for true streaming; fine-tuning last.
3. **Streaming services: drop for now.** Revisit only if a provider ships a
   model that hears band-mix singing; the scripts here re-run in 25 minutes.

## Files

- `research/spikes/streaming_asr/stream_eval.py`: real-time streaming to
  Deepgram (Nova-3, Flux), AssemblyAI (STT-3.7, Universal-Streaming), Groq and
  MLX Whisper, plus the scorer. Keys are read from
  `~/.config/church-slides/research-keys.env` and never printed.
- `research/spikes/streaming_asr/qwen_eval.py`: Qwen3-ASR through the wrapper,
  plus whole-song accuracy and pass times (its own venv).
- `research/spikes/streaming_asr/whisper_offline.py`: whole-song Whisper.
- Logs: `~/Development/auto-presenter-research/work/streaming_asr/runs/`
  (`main6`, `groq35`, `hymns6`).

## Definitions

| Term | Meaning |
|---|---|
| Streaming service | Speech recognition that takes audio continuously over a connection and sends words back as it hears them |
| Chunked / windowed | What the app does with Whisper: transcribe the last 6 s again every second or so |
| Interim result | A provisional word the service may still change |
| Final word | A word the service won't change. Whisper's are final once 1.5 s old |
| Error rate (WER) | Wrong, missing and extra words, divided by the words sung. Over 1.0 means more wrong words than sung ones |
| Words heard / matched | Share of sung words the engine got right (matched: in its final transcript; heard: in any message) |
| Keyterms | Words or phrases the service is told to expect (here, the song's lyric lines) |
| Context | Text the model reads before it writes. Qwen's version of keyterms, and the reason it wrote unsung lyrics |
| Forced aligner | A model that, given the text, finds when each word was said. Qwen uses one for word times |
| Short-lived token | A key that works for a minute or so, minted by our server so the real key never leaves it |
| MLX | Apple's machine-learning library, which runs models on the Mac's GPU |
