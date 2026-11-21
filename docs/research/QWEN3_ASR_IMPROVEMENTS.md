# Making Qwen3-ASR a better live engine than Whisper

Spiked 2026-11-19, following [STREAMING_ASR_SPIKE](STREAMING_ASR_SPIKE.md),
which found that open-weight Qwen3-ASR (Apache 2.0) hears more sung words
than Whisper small.en but, run the way the app runs Whisper, was no faster
and made more errors. This spike tried changing how Qwen runs, the way the
project once changed Whisper, and measured each change on the same public
songs.

## The answer

> **Update 2026-11-21:** idea 4 is built (`asr_qwen_streaming`, off by
> default) and has been through the follower in real time. See
> [In the follower](#in-the-follower-2026-11-20-21): Qwen beats Whisper on
> slides; its word-time tail is fixed by reading times off the decoder's
> attention; and the next-line check now runs on Qwen's own audio with a
> phone model trained for it. Full gate with all of that (50f15ff): wrong-slide
> 0.249 vs Whisper's 0.272, hidden lag 0.156 vs 0.176, 179 vs 169 of 225 cues.

**Two of the five ideas are worth building, and they fit together.**

1. **Streaming Qwen that keeps its earlier work (idea 4)** beats the app's
   Whisper on every count we can measure without the follower: fewer wrong
   words (0.41 vs 0.45), and each word becomes final about **0.9 s sooner**
   (2.1 s vs 3.1 s after it's sung), first seen 0.6 s sooner. It keeps up in
   real time on a 16 GB M1 under normal desktop load.
2. **Qwen as a next-line checker (idea 2)** tells "the next line has started"
   from "still on this line" much better than Whisper does on the same test.
   At 2 false alarms per 100 mid-line checks it catches **49%** of line
   changes 0.7 s in, against Whisper's 25%, and 61% vs 34% a second in.

The rest are out or minor: the 1.7B model is too slow on this class of
machine and no more accurate in short windows; speculative decoding was
slower; 4-bit weights trade about 0.06 in word errors for speed (a choice for
slower machines); and the forced aligner can't say whether a line has
started (only where its words fall once it has).

**The catch is word times.** Qwen's separate aligner puts about 1 word in 5
more than a second from where it was sung (Whisper: 1 in 25). The follower
leans on word times, so this could eat the gain.

Neither result has been through the follower yet. The lesson from the
phoneme spike ([ASR_ENGINE](ASR_ENGINE.md)) still stands: earlier words only
matter if slides land earlier. The recommendation is to build idea 4 as a
real `ASRBackend` and run the real-time eval before anything else.

## Words used here

| Term | Meaning |
|---|---|
| Window / pass | The app runs speech recognition on the last 6 s of audio, about once a second. Each run is a pass. |
| WER | Word error rate: wrong, missing and extra words per word sung. Lower is better. Sung audio is hard; 0.45 is today's level. |
| Heard | Share of sung words that appear at all, in any interim result, near where they were sung. |
| First sight | Seconds from the end of a sung word until any result contains it. |
| Final | Seconds from the end of a sung word until it's committed (won't change). This is what the follower mostly acts on. |
| Incremental streaming | Instead of re-reading the whole window from scratch each step, keep the earlier audio's encoding and the text already written, and only work out the new tail. |
| Closed-set scoring | Instead of asking "what was sung?", ask "how likely is each of these 3 lines?" given the audio. It can't invent words. |
| Forced aligner | A separate Qwen model that, given audio and text, says when each word was sung. |
| Quantized (8-bit, 4-bit) | The model's numbers stored with fewer bits: smaller and usually faster, sometimes less accurate. "4e8" is a 4-bit model with an 8-bit audio encoder (the package's recommended mix). |
| Speculative decoding | A small model guesses the next few words and the big model checks them in one go. |
| Busy | Share of real time the speech model is working. Over 100% means it falls further behind every second. |
| False alarm | The check says "the next line started" while the singer is still mid-line. |

## How it was measured

- **Audio:** the parent spike's 6 JamendoLyrics songs (public, CC licensed,
  1,295 words with hand-made word timings). No church recordings; nothing
  left this Mac.
- **Machine:** Apple M1, 16 GB, on mains power with Low Power Mode off
  (checked and logged at every step), with the usual desktop load (browser,
  this app, about 13 GB of swap in use). The parent spike's pass times were
  taken on battery in Low Power Mode, so all baselines were re-timed here.
- **Path:** windowed runs go through the app's own `SlidingWindowWrapper`
  (6 s window, 1 s step, 1.5 s stability margin, and its adaptive pacing: a
  slow pass delays the next one, so a slower model also hears less). Word
  times for Qwen come from Qwen3-ForcedAligner, and its time counts.
- **Scorer:** `research/spikes/streaming_asr/stream_eval.py score`, as in the
  parent spike. These are speech-recognition numbers, not slide numbers.

## Idea 1: faster weights

Seconds per 6 s window on the M1 (speech model + aligner), and accuracy
through the app's wrapper:

| Engine | Pass p50 (p90) | Memory peak | WER | Heard | First sight | Final p50 (p90) |
|---|---|---|---|---|---|---|
| Whisper small.en (app today) | 0.64 s (1.31) | n/a | **0.452** | 71% | 1.65 s | 3.06 s (4.36) |
| Qwen 0.6B | 0.91 s (1.17) | 5.2 GB | 0.500 | 76% | 1.75 s | 3.49 s (5.03) |
| Qwen 0.6B 8-bit | 0.71 s (0.88) | 4.7 GB | 0.527 | 77% | 1.30 s | 3.04 s (4.61) |
| Qwen 0.6B 4e8 | 0.67 s (0.81) | 4.3 GB | 0.526 | 78% | 1.15 s | 2.86 s (4.38) |
| Qwen 0.6B 4e8, 8-bit aligner | **0.59 s** (0.78) | 3.7 GB | 0.539 | 76% | **1.06 s** | **2.73 s** (4.39) |
| Qwen 1.7B | 2.16 s (3.78) | 7.8 GB | 0.566 | 67% | 4.29 s | 6.01 s (8.74) |
| Qwen 1.7B 8-bit | 1.48 s (1.92) | 6.2 GB | 0.595 | 78% | 2.50 s | 4.43 s (6.18) |
| Qwen 1.7B 4e8 | 1.31 s (1.67) | 5.2 GB | 0.564 | 80% | 2.24 s | 4.07 s (5.68) |
| Qwen 1.7B drafted by 0.6B | 4.10 s (12.8) | 9.3 GB | 0.727 | 38% | 7.87 s | 8.33 s (20.4) |

- **Quantizing 0.6B** cuts the pass by a quarter and gets words to the
  follower 0.2–0.6 s sooner, but adds about 0.03 WER (extra words in the
  window's edges). Close to Whisper's speed, not past it.
- **The aligner is a third to two-fifths of every Qwen pass** (0.27–0.32 s).
  Whisper gets its word times almost free from its own decoder.
- **1.7B is out.** Three times Whisper's pass time at full precision and still
  twice it at 4-bit, and in 6 s windows it makes *more* errors than 0.6B (it
  was only better on whole songs: 0.29 vs 0.30 in the parent spike).
- **Speculative decoding lost.** Two models in memory (9.3 GB) on a 16 GB
  machine that's also running the app swapped; passes took 4 s and the
  wrapper's pacing skipped most of the audio. It might break even on a
  32 GB machine, but 1.7B isn't worth it anyway.

## Idea 2: closed-set next-line scoring

This mirrors the app's next-line check ([NEXT_LINE_CHECK](../done/NEXT_LINE_CHECK.md))
and its "honest" test: windows are not lined up with the singing, and
mid-line checks count false alarms. Per song, at every line change, the check
runs 0.3–2.0 s after the next line's first word; between changes, it runs
once a second mid-line. Each check scores the trailing 4 s:

- **3 candidates:** the line's last 4 words, then the first word (or two) of
  the true next line or of 2 other lines from the song.
- **Stay:** the best 4-word run anywhere in the current line, then the end of
  the transcript.

The check "fires" when the best candidate beats stay by a margin; the margin
is set so mid-line checks fire 2% (or 5%) of the time. A fire only counts if
it names the right line. Same 1,905 checks (1,284 at changes, 621 mid-line)
for both models; Whisper small.en scored with the same sums (`whisper_nextline.py`).

Line changes caught, naming the right line, at **2%** mid-line false alarms:

| Seconds into the new line | 0.3 | 0.5 | 0.7 | 1.0 | 1.5 | 2.0 |
|---|---|---|---|---|---|---|
| Whisper small.en | 6% | 16% | 25% | 34% | 39% | 41% |
| **Qwen 0.6B** | **19%** | **40%** | **49%** | **61%** | **65%** | **66%** |

At 5% false alarms: Whisper 23 / 34 / 40 / 50 / 59 / 59%, Qwen 31 / 50 / 58 /
70 / 74 / 72% (each model's best of the one- and two-word versions).

Picking the right one of 3 lines once a change has happened (one opening word):
Whisper 64 / 75 / 78 / 80 / 83 / 82%, Qwen 73 / 82 / 86 / 86 / 88 / 87%.

- **Cost:** 0.66 s per check for Qwen as written (about 10 short decoder
  runs, not batched) against 0.17 s for Whisper the same way, and 77 ms for
  the app's batched Whisper scorer. Batching the candidates would bring Qwen
  well under 0.3 s.
- **Not yet compared with the shipped check.** The app's next-line check
  uses a small trained phone head on Whisper's encoder, not Whisper's
  decoder. Its published numbers are on other recordings, so the fair
  comparison is the shipped check run on these 1,905 checks.

## Idea 3: the forced aligner as a position signal

Qwen3-ForcedAligner was given the current line plus the true next line and
the trailing 4 s at each of the same checks.

- **It can't tell whether the next line has started.** It must place every
  word it's given, so it puts the next line's first word in the window
  whether or not it was sung. At 2% false alarms it catches 1–4% of changes;
  weighting by its own confidence, 5–19%.
- **Once the line has started, it places the first word well:** within
  0.14–0.24 s of the hand timing (no bias) at every delay.
- Position in the line ("how many words in"): within one word 44% of the
  time; it leans a word ahead.
- 0.2 s per call.

So the aligner is useful for "where in the line" after something else has
said "which line", which is what it already does for transcripts.

## Idea 4: incremental streaming

The package's own streaming mode grows one window (here up to 10 s), keeps
the audio encoding of each finished 8 s block and the decoder's memory of
it, and re-writes only the tail of the text, with the earlier text forced in
(minus its last 5 tokens). Words were re-aligned after each step and made
final once two steps in a row agreed (the usual streaming rule). A step
can't start until the previous one finishes, so a slow step delays the next.

| Engine | Step p50 (p90) | Busy | WER | Heard | First sight | Final p50 (p90) |
|---|---|---|---|---|---|---|
| Whisper small.en, wrapper (app today) | 0.64 s per 1 s+ | ~64% | 0.452 | 71% | 1.65 s | 3.06 s (4.36) |
| Qwen 0.6B, wrapper | 0.91 s per 1 s+ | ~91% | 0.500 | 76% | 1.75 s | 3.49 s (5.03) |
| **Qwen 0.6B incremental, 1 s steps** | 0.69 s (0.99) | 70% | **0.406** | 71% | **1.09 s** | **2.14 s** (3.05) |
| ...4e8 + 8-bit aligner, 1 s | 0.42 s (0.60) | 42% | 0.464 | 68% | 0.76 s | 1.83 s (2.65) |
| ...re-align only new words, 1 s | 0.36 s (0.55) | 38% | 0.492\* | 70% | 0.80 s | 1.85 s (2.84) |
| ...4e8 + 8-bit aligner + new words only, 1 s | 0.36 s (0.54) | 37% | 0.539\* | 67% | 0.72 s | 1.80 s (2.62) |
| ...same, 0.5 s steps | 0.32 s (0.49) | 66% | 0.586\* | 65% | **0.49 s** | **1.04 s** (1.88) |
| Qwen 0.6B incremental, 0.5 s steps | 0.49 s (0.68) | 96% | 0.448 | 70% | 1.77 s | 2.56 s (**14.5**) |

\* Same words as the row it's derived from (identical text in the order
written); the score drops only because worse word times reorder some words
when the scorer sorts by time.

- **This is the result of the spike.** Keeping the earlier text gives Qwen
  back the context the 6 s window took away (parent spike: 0.30 on whole
  songs, 0.50 windowed): errors drop to 0.41, below Whisper's 0.45.
- **And it's faster**, because each step only writes the new words: final
  0.9 s sooner than Whisper, first seen 0.6 s sooner.
- **Steps of 0.5 s cost accuracy** (0.45 vs 0.41: more of the text is
  rewritten each time) and at full precision fall behind on this M1 (96%
  busy, so a backlog builds: final p90 14 s). 1 s steps are the setting.
- **4-bit buys speed for accuracy** (0.46, final 1.8 s, 42% busy): the
  choice for slower machines, not the default.
- **Re-aligning only the newest words** halves the aligner's time without
  changing the text, but as written (0.2 s of overlap) it misplaces more
  words; it needs a wider overlap before it's used.
- Machine load moves step times by up to ±40% between runs (the same 16-bit
  setting took 0.42 s and 0.28 s for transcription an hour apart), so treat
  step times as rough; the busy column is what matters.

### The catch: word times

Qwen's aligner puts **18–20% of the words it hears more than 1 s away** from
where they were sung, against **4% for Whisper**. The median error is the
same (about 0.1 s); the tail is not. The follower uses word times to place
the singer, so this is the first thing the real-time eval has to show is
harmless, or the backend has to fix (for example by aligning against a
window that ends at the newest audio, or dropping words the aligner squeezes
to the window edges).

## Idea 5: fine-tuning (LoRA), scoped only

Ideas 2 and 4 leave headroom (incremental 0.41 against 0.30 on whole songs),
but there's little public sung data with words to train on: the 20
JamendoLyrics songs are the test set; the 33 LibriVox hymn goldens have only
machine-aligned slide timing; the 57 Jamendo gospel tracks have no lyrics.
The phone-head work found 4x more data flat ([NEXT_LINE_CHECK](../done/NEXT_LINE_CHECK.md)).

The one version worth trying needs no lyrics: **self-distillation**. Use
Qwen's own whole-song transcripts of public audio (0.30 WER) as labels for
LoRA training of the incremental mode, so the live model learns to do what
the whole-song model does. Public audio only; church recordings only if
training stays on this Mac; DALI can't ship. Not started: it should wait
until idea 4 has been through the follower and we know words are still the
limit.

## In the follower (2026-11-20/21)

`QwenStreamingBackend` (`sidecar/auto_presenter/asr/qwen_backend.py`) runs
idea 4 inside the app, behind the `asr_qwen_streaming` tunable. The session
still loads Whisper small.en beside it, for the lyric-span scorer.

All numbers here come from the real-time eval
(`research/eval/run_follower_eval.py`, nosheet config), on an M1 on mains
power with Low Power Mode off and no other evals running. "Quick" means 3
short church goldens, about 12 min. Run-to-run noise there is about ±0.01–0.015
wrong-slide.

| Run | Wrong-slide | Hidden | Perceived |
|---|---|---|---|
| 9 goldens, Whisper (c9e78a1) | 0.277 | 0.212 | 0.178 |
| 9 goldens, Qwen (c9e78a1) | 0.256 | 0.168 | 0.194 |
| 9 goldens, Whisper (50f15ff) | 0.272 | 0.176 | 0.181 |
| **9 goldens, Qwen (50f15ff): attention times, Qwen check, no aligner** | **0.249** | **0.156** | **0.174** |
| Talk-only negatives, 16 min (c9e78a1) | 0 changes, both | | |
| Talk-only negatives, 16 min (50f15ff) | Whisper 0, Qwen 1 | | |

Hidden is over the 8 goldens that have a transcript. At 50f15ff Qwen also
hits more cues (179 vs 169 of 225), anticipates less (+734 vs +1,017 ms
median), keeps ASR lag p90 at 0.18 s (Whisper 0.29 s), and costs the same
engine CPU (39% of a core). Its wrong-slide is lower on 6 of 9 goldens.
Two of the other three are within 0.004. The third is the worst golden
(20261002, only 11% of its singing measured), at 0.438 vs 0.421.

The one talk-only change was the scripture read-along jumping on a meeting
clip (`IB4001…_828`, "reading room" said near the passage). Whisper's
read-along jumped on the same clip without moving a slide. Four replays of
that clip on Qwen, two with the next-line check and two without, moved
nothing. So it is a rare read-along fault, not the Qwen check. It's noted
as a follow-up.

**What the Whisper-era parts still do.** Ablations, quick set, Qwen on:

- With the next-line check off, wrong-slide is 0.299 and cues 31/47.
- With the span scorer off, wrong-slide is 0.457, and the engine overloads (4 unhealthy).
- So both still matter. Replacing Whisper outright needs a Qwen stand-in for the
  span scorer, and that doesn't exist yet.

**Ideas tried on the Qwen side** (quick set unless noted):

| Idea | Result | Verdict |
|---|---|---|
| Word confidence from the decoder's token probabilities (min per word) | Free to compute. Dropping flushed words under 0.5 (`QWEN_FLUSH_MIN_CONF`) cuts errors; WER 0.237 together with current-song lyrics as context. | Keep |
| Word times from decoder attention instead of the forced aligner (`QWEN_ATTN_TIMES`): peak of 8 chosen heads' attention over the audio tokens | Words more than 1 s off: 28% → 1.8% (aligner → attention). WER unchanged (0.357). Real time on the quick set: 0.269 alone, 0.259 with lyrics; level with the other Qwen variants (0.263–0.278). | Keep; it also lets the aligner go (memory) |
| Lines before/after the current one as context | WER 0.52–1.25 | Rejected |
| Qwen closed-set line scoring as the next-line check (idea 2) | Loses to the shipped phone check early (2% false alarms, +0.7 s: 49% vs 73%), and 661 vs 114 ms a check | Rejected |
| **Phone model on Qwen's audio tower** (below) | Quick set 0.241 vs 0.269 for the same run on the Whisper check | **Built** |

### The span scorer stays on Whisper

The follower's lyric-span scorer is teacher-forced Whisper. Every 2 s it
scores every 6-word span of the song against the last 6 s of audio. The
song-openings watch (off-setlist detection) and learned references use it
too. A Qwen version (`research/spikes/qwen_improve/span_scorer.py`) prefills
the audio prompt once and broadcasts its KV cache over each batch of spans.
It was run offline on the 9 church goldens, every 2 s, with the best span
counted right when its slide is the one sung:

| 1,578 windows | Right slide (at its placed time) | Right slide (last 3 s) | ms a call p50 (p90) |
|---|---|---|---|
| Whisper small.en (shipped) | 74.4% | 77.7% | 341 (504) |
| Qwen3-ASR 0.6B | 76.0% | 78.3% | 1,011 (1,643) |

The two are level per recording (55–90% Whisper, 61–89% Qwen), and Qwen
costs three times as much. Its decoder is about 3x Whisper's and every span
runs through all of it, so at one call every 2 s it would take about half
the GPU. They are right on different windows (either one: 83.7%), so
fusing both could pay, but not at that price.

Memory points the same way. Whisper small.en is 0.48 GB, and the forced
aligner, now unneeded with attention times, is 1.84 GB. **So Qwen sessions
keep Whisper as the scorer and drop the aligner.** Attention times and the
0.5 flush filter are now the Qwen backend's defaults, and the aligner is
loaded only with `AUTO_PRESENTER_QWEN_ATTN_TIMES=0`.

### The next-line check on Qwen's audio

The shipped check reads phones from Whisper's encoder. Qwen's audio tower
gives 13 frames a second (33 per 2.5 s window, 1024 wide) instead of 50. We
trained the same phone model (conv + 2-layer GRU-512, CTC over 41 phones) on
those frames, using the same public FMA windows as the shipped head. Training ran on
Kaggle (`research/spikes/qwen_improve/phone_head/kaggle_run.py`) and took
85 min on a T4. Our recordings never left this Mac. Held-out phone error: 37.7%.

On the spike's 1,905 checks, at 2 false alarms per 100 mid-line checks, line
changes caught by time after the first word:

| | +0.3 s | +0.5 s | +0.7 s | +1.0 s | +1.5 s | +2.0 s |
|---|---|---|---|---|---|---|
| Qwen phone model (FMA only) | **69%** | **81%** | **83%** | **78%** | 61% | 52% |
| Shipped Whisper check (FMA + our recordings) | 57% | 72% | 75% | 67% | 54% | 45% |

The garbage cost g (3.0 for Qwen, 0.6 for Whisper) was tuned on these same
checks for both models, so both rows flatter a little.

**Live** (`next_line.qwen_encoder`, `QWEN_MODEL_FILE`, `qwen_tuning()`; quick
set, attention times, flush conf 0.5). The decision's margins are sums over
frames, so they needed a new scale for Qwen:

| Margins (x Whisper's) | Wrong-slide | Hidden | Perceived | Moves (held) |
|---|---|---|---|---|
| Whisper check, same run | 0.269 | 0.251 | 0.173 | 27 (72) |
| 1.0x | 0.260 | 0.201 | 0.173 | 38 (107) |
| 1.3x | 0.252 | 0.196 | 0.165 | 31 (102) |
| 1.6x | 0.236 | 0.175 | 0.167 | 31 (62) |
| **2.0x (default)** | **0.241** | **0.180** | **0.147** | 23 (59) |
| 2.5x | 0.260 | 0.232 | 0.160 | 18 (46) |

The check costs about 6 ms a tick for the head, beside the audio tower on its
own MLX stream. As float16 the model is 10 MB; logits move at most 0.014 from
float32.

The margin scale was picked on the same 3 recordings it was measured on.
The full gate above (50f15ff) holds it to all 9 goldens and the talk-only
negatives: on the 6 goldens outside the sweep, Qwen is 0.258 vs Whisper's
0.267.

## Recommendation

1. **Build idea 4 as an `ASRBackend`** (`QwenStreamingBackend`, Apple
   silicon only, beside the MLX Whisper backend), emitting interim and final
   words directly instead of going through `SlidingWindowWrapper`. Package:
   `mlx-qwen3-asr` 0.4.4 and the Qwen weights (both Apache 2.0); run
   `scripts/third_party_notices.py`. Then run `research/eval/run_realtime_eval.py`
   on the goldens and the speech-only negatives, against Whisper, before
   making it a default. Start at 1 s steps, full precision, full re-alignment.
2. **Then try idea 2 inside the next-line check**, first against the shipped
   phone-head check on the same 1,905 checks, with batched candidates.
3. Drop 1.7B, speculative decoding, the aligner as a detector, and free lyric
   context.

Open:
- **Word-time outliers** (see idea 4): measure their effect in the real-time
  eval first; fix in the backend if they hurt.
- **Memory.** Qwen 0.6B with its aligner peaked at 5.2 GB in the windowed
  harness (3.7 GB fully quantized); the app's Whisper small.en is far
  smaller. On 8 GB machines that matters, and the machine check
  (`asr/machine_check.py`) would need a Qwen tier.
- **Hymns.** All of this is on 6 pop/rock songs with hand timings; the
  parent spike's 6 LibriVox hymns have only slide-level timing and weren't
  run here.

## Files

- Backend and check: `sidecar/auto_presenter/asr/qwen_backend.py`,
  `sidecar/auto_presenter/follower/next_line.py` (`qwen_encoder`, `Tuning`).
- Phone-model training and scoring: `research/spikes/qwen_improve/phone_head/`;
  attention timing: `research/spikes/qwen_improve/attn_timing.py`.
- Real-time runs: `../auto-presenter-research/work/qwen_backend/` (`q_*`,
  `rt_*`); phone features and heads: `../auto-presenter-research/work/qhead/`.

- Scripts: [research/spikes/qwen_improve/](../../research/spikes/qwen_improve/)
  (`speed_eval.py`, `nextline_eval.py`, `whisper_nextline.py`,
  `incremental_eval.py`, `whisper_time.py`, `common.py`).
- Run logs and summaries (outside the repo):
  `../auto-presenter-research/work/qwen_improve/runs/` (`speed/`,
  `nextline/`, `nextline_whisper/`).
