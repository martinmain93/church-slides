# Live latency — why the interface feels laggy

Opened 2026-08-21 after the operator reported lag during live use. Everything
here is measured on an 8-core Apple M-series host with 16 GB.

## The budget

A word is sung, and some time later the slide moves. Where that time goes:

| stage | cost | notes |
|---|---|---|
| ASR stability margin | **1.5 s** | `stable_margin_seconds`; a word is not final until the buffer runs 1.5 s past it |
| step quantisation | **0–1.0 s** (avg 0.5) | `step_seconds`; passes only happen on the step boundary |
| ASR pass compute | **0.3–2.1 s** | model-dependent, see below |
| matcher | ~6 ms | negligible |
| WS + React render | single-digit ms | negligible |
| scheduler anticipation | **−0.3 s** | `lead_time_ms` |

So roughly **2.0–3.0 s** on a healthy pipeline, and unbounded if the pass
does not fit inside the step.

The UI is not the problem. It contributes milliseconds against seconds. The
`audio_level` stream runs at 10 Hz and `useAudioLevels` does two state updates
per message, but it drives a nine-bar indicator and nothing above it.

## Measured ASR pass time

One pass, 6 s window, int8 on CPU, p50 / p90:

| model | 4 s window | 6 s window | fits a 1 s step? |
|---|---|---|---|
| tiny.en | 292 / 317 ms | 325 / 344 ms | yes, comfortably |
| base.en | 547 / 636 ms | 578 / 794 ms | yes, ~80% of budget at p90 |
| small.en | 1766 / 1991 ms | 1816 / 2098 ms | **no — 2x over** |

Two things fall out:

- **Window size barely matters.** Whisper pads its input to 30 s internally,
  so a 4 s window costs almost the same as a 6 s one. Shrinking
  `window_seconds` to buy compute headroom does not work.
- **Model choice is everything.** It is the difference between 344 ms and
  2098 ms against a 1000 ms budget.

## Root cause: the model recommendation optimised for the wrong thing

`recommend_model` picked by **memory fit**, not throughput — its own reason
string read "large-v3 fits in unified memory". On this host it returned
`large-v3`, `device="auto"`, `compute_type="float16"`.

Measured against reality:

- CTranslate2 4.7.1 reports **no CUDA and no MPS**. Apple Silicon GPUs are not
  available to it, so every model runs on CPU.
- CPU supports `int8`, `int8_float32`, `float32` — **not `float16`**. The
  recommended compute type did not exist on the device it would run on.
- `small.en` is already 2x over budget; `medium` and `large-v3` are multiples
  beyond that.

A pass that overruns its step makes the capture queue back up and discard
audio, and the lag then grows without bound — the signature in the earlier
session log (lag 3.2 → 4.3 → 5.0 s, 102 dropped chunks).

**Fixed 2026-08-21.** `recommend_model` now selects the largest model whose
*measured* pass time fits the step budget, halving the budget on hosts with
fewer than 8 cores or under 8 GB. Apple Silicon is treated as CPU because that
is what it is here. On this host it now returns `base.en` / `cpu` / `int8`.
`test_no_cpu_recommendation_exceeds_the_pass_budget` asserts the property
directly rather than by example.

## Remaining strategies, ranked

Ease vs expected gain. Nothing below is done.

| # | change | gain | ease | notes |
|---|---|---|---|---|
| 1 | **Warn in-app when the selected model is over budget** | high | easy | The operator can still pick `small.en` by hand and get an unusable pipeline with no explanation. The Perf tab already has `asrMs`; it should say *why* and name the fix. |
| 2 | **`stable_margin_seconds` 1.5 → 1.0** | up to 0.5 s | easy to try, needs care | Directly on the critical path. Costs right-context, so words get revised more; needs a live recording to evaluate honestly (see caveat). |
| 3 | **`step_seconds` 1.0 → 0.5 with tiny.en** | ~0.25 s | easy | Only affordable under tiny.en (344 ms × 1.5 pace safety = 516 ms, marginal at a 500 ms step). Trades accuracy for latency. |
| 4 | **Act on unstable words for anticipation only** | up to 1.5 s | hard | Removes the stability margin from the *anticipation* path while keeping it for commits. The largest available win and the largest change. |
| 5 | **Re-test `lead_time_ms`** | up to 0.3 s | easy | Previously dead code; the projection rewrite made it reachable, and it has not been swept since. |
| 6 | **UI: coalesce `audio_level`, drop the double render** | milliseconds | easy | Not the cause. Worth doing only as hygiene. |

**Caveat on 2, 3 and 5.** Replay cannot measure these honestly: `MockASRBackend`
emits pre-timed words and simulates no compute, so sweeping the wrapper
parameters in replay produced non-monotonic nonsense (margin 1.5 and 0.5
scoring identically, 1.0 worse than both). Judging them needs a live-captured
recording with real streaming ASR — the same gap that makes `snap_window`
provisional.

## Two eval/production divergences found and fixed

Both were silently measuring a configuration that does not ship:

- `eval/trace.py` hardcoded the ASR window at **4.0 / 0.5 / 0.5** while
  production runs the library defaults **6.0 / 1.0 / 1.5**. Now parameterised
  and defaulted to production. Aligning it costs real accuracy: Reckless Love
  0.421 → 0.475, I Surrender All 0.243 → 0.294 under the old `snap_window`.
- `eval/trace.py` also pinned `snap_window=1`, so it never picked up the move
  to 5. It now takes `ParticleFilterConfig`'s defaults for anything it does
  not deliberately override.

`scripts/perf_check.py` had the same class of bug earlier in the day. Any
harness that restates production config will drift from it; prefer reading the
defaults.

## Cutting evidence latency does not cut decision latency (2026-08-29)

Measured end to end on the production harness (`run_realtime_eval.py`), six
approved goldens. All rows are the forward filter behind the lead-time
scheduler; only the ASR configuration changes.

| model | window / step / margin | wrong-slide | anticipation | decisions |
|---|---|---|---|---|
| tiny.en beam 5 | 6 / 1 / 1.5 | 0.641 | +1443 ms | 99 |
| tiny.en beam 1 | 6 / 1 / 1.5 | 0.618 | +1393 ms | 99 |
| **base.en beam 1** | **6 / 1 / 1.5** | **0.603** | **+893 ms** | 106 |
| base.en beam 1 | 6 / 0.5 / **0.3** | 0.627 | +1140 ms | 84 |
| tiny.en beam 1 | **3** / 0.5 / 0.3 | 0.755 | +1397 ms | 75 |

**The two latency-oriented configurations are worse on both axes.** This was not
the prediction. `emit_lag ~= margin + 1.75 * pass_time` is arithmetically correct
— dropping the margin from 1.5 s to 0.3 s really does put words in front of the
matcher about a second sooner — but the words arrive before Whisper has settled
them, so the tracker needs more of them to reach the commit threshold. The
evidence gets earlier and the *decision* gets later.

Same for the window: 6 s -> 3 s costs 0.15 of wrong-slide and buys nothing,
because Whisper pads every input to 30 s internally and the encoder cost barely
moves (tiny.en: 306 ms at a 1.5 s window, 311 ms at 6 s).

So `stable_margin` and `window_seconds` are spent as latency levers. What
remains is genuine pass-time reduction.

### Where the tail actually comes from

`faster_whisper.transcribe()` defaults to `temperature=[0, .2, .4, .6, .8, 1.0]`
and retries a segment at the next temperature whenever it trips
`compression_ratio_threshold` or `log_prob_threshold`. Sung audio over
instrumental backing trips them routinely, so one "pass" is quietly up to six.
Forty windows, int8, M1:

| | p50 | p90 | max | words |
|---|---|---|---|---|
| tiny.en, default | 542 ms | 1521 ms | **4240 ms** | 213 |
| tiny.en, `temperature=0` | 473 ms | 553 ms | **648 ms** | 212 |
| base.en, default | 903 ms | 1219 ms | 1854 ms | 218 |
| base.en, `temperature=0` | 880 ms | 1080 ms | 1422 ms | 215 |

Essentially the same words for a 6.5x smaller tail. And the tail is worth far
more than the median here, because `SlidingWindowWrapper` sets
`_adaptive_step = elapsed * pace_safety` — **a single 4.2 s pass blocks the next
one for 6.4 s of audio.** That is the mechanism behind a screen that sits still,
and a candidate explanation for the 33% of ground-truth intervals where the
right slide is never displayed at all.

**Applied, and it is the largest single accuracy win measured this whole
session** — not merely a latency fix. End to end on the six goldens, base.en
greedy, production windowing:

| | wrong-slide | anticipation | decisions |
|---|---|---|---|
| default (6 temperatures) | 0.603 | +893 ms | 106 |
| **`temperature=0`** | **0.499** | +1006 ms | 113 |

0.104 of wrong-slide, from a one-line change that leaves the words alone. That
is the pacing feedback loop made visible: a fallback storm does not cost its own
duration, it costs `elapsed * 1.5` seconds of *no transcript at all*, and the
tracker cannot follow a band it cannot hear. King of My Heart — the recording
that had made four decisions against fourteen transitions — went to seven.

It is also a caution about attributing latency and accuracy separately. This was
found while chasing tail latency and scored as an accuracy fix.

### Encoder facts worth not re-deriving

Measured on M1, int8, 6 s window. Output is always `1500 x d_model` (20 ms per
frame) because of the 30 s padding — 384 dims for tiny.en, 512 base.en, 768
small.en. Mel extraction is 2 ms and never matters.

| | encode | decode | encoder share |
|---|---|---|---|
| tiny.en | 296 ms | ~15 ms | ~95% |
| base.en | 449 ms | 199 ms | 69% |
| small.en | 1584 ms | ~950 ms | 63% |

**The encoder output is reusable across decodes in CTranslate2 today** —
`generate()` and `align()` both accept what `encode()` returns. Measured:
`generate(mel)` 647 ms against `generate(encoder_output)` 199 ms, so a second
consumer of the same window costs 199 ms rather than 647 ms. A fast/slow split
sharing one encode is therefore affordable — **but only if both tiers use the
same model**, since two models means two encoder passes and the encoder is 69%
of the cost.

## The eval was not reproducible, and now the cheap path is (2026-08-30)

`SlidingWindowWrapper` derives `_adaptive_step` from **wall-clock** pass time, so
how loaded the machine is decides which windows get transcribed. Two runs of the
identical configuration returned wrong-slide 0.499 and 0.523, and a separate
replay produced 126 decisions where the eval reported 113.

That is a ~0.024 noise floor on the headline metric, which is larger than
several deltas measured this week. `temperature=0` (0.104) clears it
comfortably; `beam_size=1` measured end to end (0.023) does **not** — that change
stands on its independent WER and pass-time measurements, not on its eval delta.

### `--events-cache`

Transcription is ~100x the cost of everything else in a run: 35 minutes of audio
becomes ~2000 Whisper passes, because a 6 s window stepped by 1 s transcribes
every second of audio six times over and pays the full 30 s encoder toll each
time. Replaying both engines over the resulting events takes **7.3 seconds**.

So the cache stores transcripts keyed on exactly what changes them
(`model|window|step|margin`). A tracker, scheduler or metric change re-runs no
ASR at all:

| | wall time | reproducible |
|---|---|---|
| full run | ~12-15 min, 8 cores saturated | no (0.024 noise floor) |
| `--events-cache` hit | **8.9 s**, one core | **yes — byte-identical decisions** |

The determinism matters as much as the speed. Two cached runs produce identical
decision streams, so an A/B over the same cache isolates the change instead of
measuring it through ASR jitter. **Any experiment that cannot alter the
transcript should use the cache**, and results from full runs should be treated
as having a 0.024 noise floor.

## Commit on what is displayed, not on which index (2026-08-30)

Of the intervals where the correct slide is never displayed at all, replaying the
tracker and classifying each one:

| | share | meaning |
|---|---|---|
| believed | **75%** | the filter's top slide *was* right; nothing committed |
| outvoted | 12% | words heard, another slide still preferred |
| unheard | 7% | events landed, no word matched the slide |
| silence | 6% | no transcript events in the interval at all |

Only 13% is an ASR problem. Instrumenting every `return None` in
`LeadTimeScheduler.on_position` over the moments where the filter was right and
the screen was not: **39% were blocked by the commit threshold and nothing
else** — the single largest cause.

The reason was a wiring bug. `on_belief` gated on `top_hypothesis[2]`, the
*per-slide* marginal. A chorus written as four identical slides splits the mass
four ways, so that value sits near 0.25 against a 0.40 threshold while the
pooled mass over copies that all display the same words is 0.96.
`BeliefState.commit_confidence` — documented as "confidence in what to display,
pooled over identical slides", carrying a comment describing this exact King of
My Heart failure — existed and **was called from nowhere**.

Wired, and A/B'd over one cache so both arms see byte-identical transcripts:

| | by index | **by displayed text** | never-arrived | commits |
|---|---|---|---|---|
| pooled | 0.460 | **0.412** | 32% | 142 |
| per-slide | 0.463 | 0.444 | 36% | 126 |

The index metric hides it (0.003) because the extra commits move *between
identical slides* — a change of index that is not a change of screen. By text
it is 0.032, and five more intervals reach the right slide. That the by-index
number stays flat is the expected signature of this fix, not evidence against it.

## Threading: faster-whisper already parallelises, and badly past 4 (2026-08-30)

`WhisperModel(cpu_threads=0)` — the default, and nothing in the sidecar
overrides it — means CTranslate2 auto-detects. One pass is already spread across
cores. But that scaling is poor and **peaks at 4 threads, then goes backwards**
(base.en, int8, 6 s window, M1):

| cpu_threads | pass | speedup vs 1 | efficiency |
|---|---|---|---|
| 1 | 1943 ms | 1.00x | 100% |
| 2 | 1432 ms | 1.36x | 68% |
| **4** | **1024 ms** | **1.90x** | 47% |
| 6 | 1310 ms | 1.48x | 25% |
| 8 | 1766 ms | 1.10x | 14% |

Almost certainly the M1's 4 performance + 4 efficiency cores: work handed to an
efficiency core runs at a fraction of the speed and every barrier waits for it.
This explains the earlier sweep result where an explicit `cpu_threads=8` was
*worse than the default on both time and WER*.

**The default is already near-optimal** — 892 ms against 873 ms for an explicit
4 — so there is no free win here, only a way to lose: never set `cpu_threads=8`.

### The two-tier question

Since one model cannot use 8 cores efficiently, two models at 4 threads each use
the machine better than one model trying to use everything:

| base.en + tiny.en on the same window | wall time |
|---|---|
| sequential | 1649 ms |
| concurrent, 4 threads each | **1274 ms** (1.29x) |

So a fast/slow split is **not** fighting the wrong battle — there is real
headroom. But it is not free either: the concurrent pair costs 1274 ms against
892 ms for base.en alone, so a second tier adds ~380 ms of wall time to every
window. And two *different* models cannot share an encoder pass; only the same
model can (`generate(encoder_output)` 199 ms against `generate(mel)` 647 ms).
The cheap version of a two-tier design is therefore one model, one encode, two
decode strategies — not two models.

## Everything above was measured on a machine that was never idle (2026-08-30)

46 leaked sidecar processes had accumulated over 12 days — three of them holding
a loaded Whisper model and burning ~3.5 cores between them. Every full-ASR
number in this document was measured against them. With them gone, load average
went 15-49 to 1.5 and a base.en pass went **892 ms to 310 ms**.

That matters beyond tidiness, because pass time drives `_adaptive_step`: slower
passes mean fewer passes mean a thinner transcript. Rebuilt on an idle machine,
the same six recordings yield **1124 events / 2375 words** against 850 / 2208.

### What survived re-measurement, and what did not

Re-run idle. The temperature arms are two caches built back to back and replayed
through an identical matcher; the scheduler arms share one cache, so both arms
see byte-identical evidence.

| change | claimed (contended) | actual (idle) | verdict |
|---|---|---|---|
| `temperature=0` | 0.104 by index | **0.074** by index | holds, smaller |
| pooled `commit_confidence` | 0.032 by text | **0.050** by text | holds, larger |
| one-slide backward after dwell | 0.025 by text | **0.008** by text | **reverted** |

`temperature=0` also shows its mechanism plainly once idle: 2375 words against
1790, i.e. the fallback costs a third of the evidence, not merely time.

Pooled confidence got *stronger* on a fuller transcript — 0.472 -> 0.422 by
displayed text, never-arrived 38% -> 32%. It is the largest single scheduler win
of the session.

The backward-dwell rule is **off again**. On clean evidence it buys 0.008 of
displayed-text accuracy and costs 4 -> 18 screen reversals: it had been
compensating for evidence gaps the leak caused, and with a fuller transcript the
tracker reaches the slide on its own, so the extra backward moves are mostly it
second-guessing a correct anticipation. Kept as an option, defaulting off,
because the finding is conditional rather than wrong.

### Thread scaling holds

The worry was that "4 threads beats 8" was an artifact of 3.5 cores being
stolen. It is not — the shape is identical idle, just three times faster:

| cpu_threads | idle | contended |
|---|---|---|
| 1 | 585 ms | 1943 ms |
| **4** | **310 ms** | 1024 ms |
| 8 | 441 ms | 1766 ms |

Genuinely the M1's 4 performance + 4 efficiency cores. Parallel two-model
throughput does weaken, 1.29x -> **1.15x**, so a two-tier design has less
headroom than it appeared. Encoder share is stable at 68%.

### What this implies for the live 3-5 s lag

The live log showing `asr=1881ms` and a `7028ms` spike was captured while three
zombie copies of the sidecar were eating half the machine. At the clean 310 ms
pass time the model gives `1.5 + 1.75 * 0.31 ~= 2.0 s`, against ~4.8 s before.
**A live session should be re-run before any further latency work** — much of
the reported lag may simply have been the leak.

### Method

Full-ASR runs carry a noise floor of at least 0.024 and, under varying load,
0.06 (three identical runs gave 0.499 / 0.523 / 0.557). Prefer `--events-cache`:
it is 100x faster, byte-identical across repeats, and isolates a matcher change
from ASR jitter. Where a change *does* alter the transcript, build both caches
back to back on an idle machine and replay them through the same matcher.

## Anticipation has never worked, and cannot as currently built (2026-08-31)

Live run of Reckless Love, vocals only, 165 s, 18 operator transitions
(`rec_20260831-205404_616`). Reported: "almost never anticipated... a few times
I paused hoping it would advance and it never did... maybe 1 or 2 out of 20."

That matches a note already sitting in `scheduler.py`: sweeping `lead_time_ms`
from 300 ms to 2400 ms "moved timing without ever improving accuracy". The
reason is worse than mistuning.

**1. The scheduler is only ever called from a transcript event.** `on_position`
ran exactly 110 times for 110 events and never otherwise. During a pause there
are no words, so no event, so no evaluation — the case the user tested
deliberately. 4 of the 18 transitions had no event at all inside the 1.5 s lead
window.

**2. For the other 14, the projection is fed a position that is not merely
late — it is anti-correlated with the thing it is meant to predict.**
`seconds_remaining = (1 - position) * n_words / rate`, and at the moment the
band actually moves the tracker believes it is **40%** through the outgoing
slide (median), projecting 5.7 s remaining against a 1.5 s lead. Splitting every
observation by whether a real transition follows within 2 s:

| | median believed position |
|---|---|
| about to transition | **0.31** |
| mid-slide | **0.56** |

Every threshold fires *more* often mid-slide than before a real move. The signal
runs backwards, because believed position effectively measures how long since
*the tracker* changed slides, which is the opposite of how close *the band* is
to changing. No lead-time value can fix a predictor with the wrong sign.

**Why the position is so far back.** The transition model advances
`advance + 2*skip = 0.33 + 0.088 ~= 0.42` words per observation, deliberately —
a low `stay` makes the posterior run ahead and costs wrong-slide rate. So the
posterior travels at ~40% of the sung word rate, which is exactly the 0.40
observed. It is a good identification model being asked a progress question.

**Two alternatives measured, both rejected.**

*Elapsed time in slide* (`(now - entered) * rate / n_words`) is worse — median
0.20 — because the tracker often only recognises a slide about 2 s before the
band leaves it.

*Leading edge of the posterior* rather than its mean: the 99th percentile of the
word-index distribution still reads only 0.50 at a real transition, so this is
not an artifact of summarising by the mean. The belief genuinely never reaches
the end of a slide.

*Tail matching* — asking whether the words just heard are the last words of the
believed slide — also fails to separate (38% vs 47%). And that failure is the
most informative of the three: **the tail of a slide is still inside
`stable_margin` when the band moves on.** The words that would signal "this
slide is ending" are by construction not final yet.

### What this leaves

Anticipation cannot be recovered by tuning. The candidates are:

1. **Use the partial (`is_final=False`) events.** They already exist and are
   already broadcast; the matcher ignores them. They carry exactly the
   ~1.5 s of leading evidence the stable margin withholds, which is precisely
   the window anticipation needs. Cheapest real option.
2. **Drive the scheduler on a timer**, not only on transcript events, so a pause
   can still produce a decision. Necessary for the 4/18 silent transitions but
   insufficient alone — a timer tick would re-evaluate the same backwards
   signal.
3. **Learn slide durations** and predict from structure rather than evidence.

Until one lands, the honest position is that the app *catches up* rather than
anticipates, and `lead_time_ms` is close to inert.

### Tried: partials, and a tail-word detector (2026-09-01) — neither ships

**Partials buy no lead time.** The hypothesis was that `is_final=False` events
carry the ~1.5 s the stable margin withholds. Measured on the live run, for
words delivered as both: the partial arrives a median **0.04 s** earlier, and is
first only 53% of the time. The wrapper emits both kinds from the same pass, so
a partial is mostly the same information with a lower confidence label, not
earlier information. Feeding them to the matcher would add noise, not runway.

**A correction to the previous section.** It concluded the tail of a slide is
still inside `stable_margin` when the band moves, so ending-evidence can never
arrive in time. That was wrong, and it was an artifact of how the test scored:
it took the *fraction* of recent words matching the tail, which dilutes one
decisive word among everything else in the buffer. Asked directly — when does
the last word of the outgoing slide first reach the matcher — the answer is a
median **3.47 s before** the band moves on, in 13 of 15 transitions. The
evidence is there.

**But a detector built on it is still not good enough.** Firing when a word
arrives that occurs only in the tail of the current slide (the same "proof, not
evidence" shape as `decision/branch.py`), against ground-truth slide identity so
the tracker's own lag is not counted against it:

| gate on time already spent in the slide | transitions anticipated | spurious fires |
|---|---|---|
| none | 7/17 | 5 (1.8/min) |
| 2 s | 7/17 | 3 (1.1/min) |
| 3 s | 4/17 | 2 (0.7/min) |
| 5 s | 4/17 | 1 (0.4/min) |

Adding partials raises recall to 9/17 and spurious fires to 8.0/min — the wrong
direction. The gate trades recall for precision almost linearly and there is no
knee. 41% of transitions anticipated for four or five wrong slide changes in a
single song is not a trade worth making: a congregation forgives being a beat
late far more readily than being sent to the wrong verse.

**A timer tick was not built.** It is necessary for the 4/18 transitions with no
event in the lead window, but on its own it only re-evaluates the same
anti-correlated position signal more often. It is worth building behind whatever
finally produces a usable ending signal, not before.

### Where that leaves anticipation

The evidence exists ~3.5 s ahead. What is missing is a decision rule that can
use it without firing on repeated lyrics — and worship lyrics repeat by design
(46% of songs share opening lines across slides; choruses recur verbatim). The
per-song structure the tracker already learns (`progress.order_is_predictive`)
and the duration regularity visible here (slide 6 sung twice at 7.1 s and 7.0 s)
look like the more promising direction than another lexical rule.

Meanwhile the app catches up rather than anticipates, and `lead_time_ms` remains
close to inert. That is the honest state.
