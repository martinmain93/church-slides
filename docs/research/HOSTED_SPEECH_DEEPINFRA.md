# Online speech on DeepInfra: first real-time gate (2026-11-25)

Design: `docs/roadmap/HOSTED_SPEECH.md`. Backend: `asr/hosted_backend.py`
(`asr_hosted` research tunable).

## Set-up

- **Gate:** the release gate, 9 church goldens, no lead sheets, played in
  real time on the M1.
- **Follower:** the processor path's follower, with the ONNX base.en scorer
  (`score_follower_cpu`).
- **Speech:** sent to DeepInfra's OpenAI-compatible
  `/v1/openai/audio/transcriptions` with word timestamps. It goes through
  the sliding window built for Whisper: a 6 s window, a pass at most once
  a second.
- **Fallback:** Whisper small.en on the processor. It never fired.
- **Token:** a DeepInfra scoped JWT, one model, $3 cap. Mains power, Low
  Power Mode off.
- **Script:**
  `auto-presenter-research/work/hosted_speech/rt/run.sh`.

| Speech | Wrong-slide | Perceived | Anticipation | Dropped audio | Unhealthy reports | Engine CPU |
|---|---|---|---|---|---|---|
| Whisper small.en on the processor (today) | **0.295** | **0.182** | +190 ms | 0 | 0 | 114% |
| DeepInfra Qwen3-ASR 0.6B | 0.352 | 0.246 | +470 ms | 91 | 29 | 81% |
| DeepInfra Qwen3-ASR 1.7B | 0.320 | 0.218 | +470 ms | 94 | 36 | 111% |

Per recording, 1.7B beat Whisper on 3 of 9 recordings.

## What went wrong

- **Slow answers back up the audio.** A pass takes 0.9–1.5 s typically and
  around 2 s at p90. Some answers take several seconds. The call blocks the
  speech thread, the queue fills, and audio is dropped (`audio_session`
  drops the oldest audio when the queue is full). That shows as the
  "unhealthy" reports. On-device Whisper never falls behind.
- **This isn't the Mac's Qwen engine.** On Apple silicon, Qwen streams with a
  10 s context, carries its own text forward between steps, and has the
  next-line check. Here every 6 s window is transcribed cold, and its words
  are merged by Whisper's rules.
- **DeepInfra's speed varies.** In a side bench on public clips, run while
  the gate ran:
  - 0.6B answers went from 0.9 s to 6–12 s for a stretch;
  - 1.7B often timed out at 30 s.

  So a slow answer has to be dropped, not waited on.

## Things learned about DeepInfra

- **A scoped JWT with a spending limit must name exactly one model.** With
  several, minting fails with a 422.
- **Qwen3-ASR returns per-word `start` and `end` through the OpenAI-compatible
  endpoint.** The first word sometimes has zero length.

## Next

1. **Never queue behind the network.** Always send the newest window, with
   at most one or two calls in flight, so slow answers are skipped rather
   than backing audio up.
2. **A 10 s window,** and carry the committed text forward as the `prompt`,
   closer to the Mac engine.
3. **Then re-run the gate,** 1.7B first.

## Streaming bench on public songs (2026-11-26)

`research/spikes/streaming_asr/stream_eval.py` streams six JamendoLyrics
songs (1,295 hand-timed words) in real time through the app's
`SlidingWindowWrapper`, with `DI_STREAMS` variants. All four variants ran at
once. Baselines are from the 2026-11-19 spike, `runs/main6`, timed in Low
Power Mode, so their delays are pessimistic.

| Stream | WER | Heard | First p50 | Final p50 / p90 |
|---|---|---|---|---|
| **DeepInfra Qwen 0.6B, 6 s window** | **0.437** | 77% | 1.99 s | 3.76 / 5.87 s |
| DeepInfra Qwen 0.6B, 10 s window | 0.548 | 76% | 1.99 s | 4.05 / 7.24 s |
| DeepInfra Qwen 0.6B, 10 s window + carried prompt | 0.532 | 78% | 2.00 s | 4.14 / 7.01 s |
| DeepInfra Qwen 1.7B, 10 s window | 0.879 | 28% (timeouts) | 1.69 s | 4.02 / 9.21 s |
| MLX small.en on the M1 (baseline) | 0.466 | 71% | 2.14 s | 3.55 / 5.34 s |
| MLX Qwen 0.6B, 6 s window (baseline) | 0.506 | 77% | 1.58 s | 3.39 / 5.09 s |
| Groq Whisper turbo, paced 3 s (baseline) | 0.524 | 62% | 3.03 s | 3.94 / 5.67 s |

- **DeepInfra 0.6B on 6 s windows hears the most of any streamed setup we've
  measured,** but its words are about 0.2–0.5 s later than on-device.
- **A longer window hurts through the sliding window,** and the carried
  prompt only partly makes up for it.
- **1.7B on DeepInfra is unusable for live work:** it queues and times out.
- **So on the gate the words were good.** The loss came from late or dropped
  audio and from merging by Whisper's rules, not from what was heard.
