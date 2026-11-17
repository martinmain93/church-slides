# Audio-driven emission — first end-to-end result

Branch `asr-onnx-scoring`, 2026-08-21. Replaces the particle filter's
word-matching emission with direct acoustic scoring: for each update, group the
cloud into a few (slide, text-span) candidates and ask Whisper how well each
explains the audio window.

Runs entirely in the **shipping environment** — `onnxruntime` plus
faster-whisper's tokenizer and feature extractor. No `transformers`, no torch.

## Result: works, but currently 2.2x worse

| pipeline | wrong-slide |
|---|---|
| word-based, full Phase 7 | **0.132** |
| word-based, no Phase 7 | 0.132 |
| **audio emission** (scale 0.25) | **0.289** |

Phase 7 makes no difference on this recording (it has one deviation), so this
is an apples-to-apples comparison and the gap is real.

## What the trace shows

Tracking is **good for three quarters of the song**. Belief follows slides
1 -> 2 -> 3 -> 4 -> 5 -> 6 in order, marginal 0.9-1.0, position advancing
smoothly, commits landing 2-4 s after each cue.

Then it fails once, catastrophically:

```
  98.0     6    6  0.98  0.84     6
 100.0     6    5  0.63  0.35     6
 104.0     7    5  0.38  0.84     6
 106.0     7    1  1.00  0.06     1   <- commits slide 1, truth is 7
 120.0     7    1  1.00  0.27     1   <- still stuck
```

At t~104 the band starts verse 3 (slide 7, "**all to jesus i surrender** make
me saviour..."). Slide 1 opens with the same five words. With no strong prior
for slide 7, the cloud jumps to slide 1, `commit_slide` collapses it there, and
it never recovers. One mis-lock costs the remaining half of the song.

This is exactly the case `index/confusion.py` was built for, and the audio path
does not use it.

## Emission over-confidence, and why

Marginal sat at 1.00 almost everywhere: the emission was steamrolling the
transition prior. Part of that is a real double-count — **a 6 s window stepped
every 1 s scores each second of audio six times**, so the same evidence is
folded in six-fold.

Tuning the sharpening exponent confirms it:

| scale | wrong-slide | commits (14 cues) |
|---|---|---|
| 2.0 | 0.666 | 7 |
| 0.5 | 0.319 | 13 |
| **0.25** | **0.289** | 13 |
| 0.15 | 0.298 | 13 |
| 0.08 | 0.324 | 13 |

Scale is a blunt fix for a structural problem; the principled version is to
score only the audio that is new since the last update, or to stop overlapping
windows.

## Cost

700-1050 ms per update at 16 candidates, against a 1000 ms budget — at or
slightly over. Higher than the 510 ms measured for scoring alone because each
update also pays feature extraction and a full encoder pass.

## What to fix next, in order

1. **Wire the confusion groups into the audio path.** The one failure that
   dominates the score is a shared-opening jump, and the index already knows
   which slides are confusable and which words separate them.
2. **Stop double-counting evidence.** Score the new second of audio rather than
   re-scoring the whole overlapping window, so the sharpening constant stops
   standing in for a missing model.
3. **Give the audio path the Phase 7 machinery** (confidence monitor, hold,
   relocalize). Not the cause here, but it is missing, and a mis-lock that
   never recovers is precisely what lost-mode exists to break.

## Bearing on the library-scale goal

The eventual target is discovering candidates across the whole library, not
just the current song. The cost profile suits a two-tier design: scoring is
linear in candidates (~14 ms each) with a fixed ~370 ms encoder pass, so a
local tier of 8-16 candidates fits comfortably, while 2828 library slides never
could. That argues for cheap retrieval first — a coarse index over the free
Whisper transcript, where recall is what matters and its 17-24% WER is
tolerable — then exact acoustic scoring to re-rank the shortlist. The failure
above is a warning for that design too: retrieval must return *all* members of
a confusion group, or the re-ranker never gets the chance to choose correctly.

---

# Window length, and what the matching is actually doing

*2026-08-21, after the operator-model discussion.*

## What one update actually computes

1. **Predict** — advance every particle: `position += rate·dt/n_words`, plus
   noise; some transition to the next slide per the transition prior.
2. **Group** — per particle, `centre = int(position × n_words)`,
   `start = centre − SPAN/2`, bucketed to a multiple of `STRIDE`. Key is
   `(item, slide, start)`. Keep the top 16 groups by particle weight.
3. **Window** — the last `WIN` seconds of audio, log-mel, **padded to 30 s**,
   through the encoder → 1500×512.
4. **Score** — harvest cross-attention K/V once, then teacher-force each
   group's `words[start:start+SPAN]` through the decoder → mean
   log P(token | prefix, audio).
5. **Weight** — `lik = exp(scale × (score − best))`, multiplied into each
   particle's weight; renormalise; resample.

## Four flaws in that, in increasing order of importance

1. **The span is centred on the particle position while the audio is
   retrospective.** The window covers the seconds *before* `t`, so about half
   the span is words not yet sung. Off by half a span.
2. **Spans never cross slide boundaries.** A window straddling a transition —
   exactly when a decision is needed — has no candidate that can explain it,
   and with short slides a long window spans three.
3. **`SPAN` is fixed** regardless of window length or tempo. At the measured
   1.1–1.6 words/s, six seconds is 7–10 words, so a 6-word span was short.
4. **The likelihood is purely relative.** `exp(scale × (score − best))`
   guarantees the best candidate gets weight 1.0 — *there is no way to
   express "none of these match"*. That is precisely how the tracker locked
   onto slide 1: slide 7 was not among the winners, so the least-bad wrong
   answer won with full confidence and `commit_slide` froze it there.

Flaw 4 is the operator insight stated mathematically. A human hears words that
match nothing expected and goes on alert; this formulation cannot represent
that thought.

## Window length: 6 s was badly wrong

Sweeping window against span length, measuring **absolute** scores, not just
ranking (I Surrender All, one point every 6–10 s):

| window | span | exact | right-words | true | wrong | separation |
|---|---|---|---|---|---|---|
| 3 s | 6 | 35% | 39% | −5.62 | −5.45 | **−0.17** |
| 4 s | 10 | 43% | 65% | −5.26 | −5.72 | 0.46 |
| 6 s | 6 | 45% | 73% | −4.14 | −5.40 | 1.26 |
| 8 s | 6 | 41% | 77% | −3.40 | −5.33 | 1.93 |
| 10 s | 8 | 59% | 77% | −3.23 | −5.43 | 2.20 |
| 12 s | 8 | 45% | 73% | −3.05 | −5.37 | 2.31 |
| 16 s | 12 | 52% | **90%** | −2.64 | −4.68 | 2.05 |
| 16 s | 16 | 48% | **90%** | −2.69 | −4.80 | 2.10 |

- **Short windows are actively broken.** At 3 s the separation is *negative* —
  the wrong slide scores higher than the true one.
- **Right-words climbs 39% → 90%** as the window grows from 3 s to 16 s. The
  6 s window used for every measurement before this was leaving a great deal
  on the table.
- **The wrong-candidate score is flat at ≈−5.4 across every window**, while the
  true-candidate score climbs to −2.6. More context sharpens the correct
  answer without helping the wrong ones.

## The absolute score is the "relax or go on alert" signal

That flat wrong-candidate baseline is the important part. Matching sits around
−2.6 to −3.4; non-matching sits around −4.7 to −5.4, and *does not move* with
window length. So a fixed threshold in between separates "the audio is
explained by what I expect" from "it is not" — which is the judgement the
operator model is built on, and the signal the relative normalisation discards.

## The architecture this implies

Two states, mirroring the operator rather than re-classifying every second:

**Tracking.** We believe we are at `(slide, word position)`. Score the expected
continuation from that position, plus the next slide's opening for the upcoming
transition — three or four candidates, ~400 ms. While the absolute score stays
above threshold, reinforce and advance. **A marginal of 1.00 here is correct and
desirable**, not a bug: every matching word genuinely is more evidence for the
same hypothesis. The transition fires when position runs off the end of the
slide, not from re-identifying the slide.

**Alert.** The absolute score drops below threshold — words arrived that the
expectation cannot explain. Widen in the order the index already supports:
confusion-group siblings first (precomputed, and this is exactly the case that
broke the t≈104 run), then the rest of the song, then the library. Snap when a
candidate clears the threshold, return to tracking, and stay on elevated alert
for a while afterwards, because a band that deviated once is likelier to again.

The cost profile fits: tracking is 3–4 candidates, and only alert pays for a
wide search — which is also what makes library-scale search affordable, since
it is entered rarely rather than run continuously.

---

# The rewrite: a two-state tracker

*2026-08-27. Implements the architecture proposed above.*

The particle-filter emission is gone from this path. What replaces it is the
tracking/alert machine described at the end of the previous section, built as
real sidecar modules with tests rather than as a spike:

| module | what it holds |
|---|---|
| `acoustic/scorer.py` | ONNX teacher-forced scoring, cross-attention K/V shared per window (promoted from the spike) |
| `acoustic/window.py` | one window's encoder pass, reused across every candidate |
| `acoustic/path.py` | the song as a **word tape**, and a position along it |
| `acoustic/tracker.py` | the tracking/alert state machine |
| `decision/acoustic_engine.py` | tracker + scheduler → `MatcherDecision` |

`decision/scheduler.py` grew a `TrackedPosition` input so the *same* tuned
commit rule serves both trackers. That refactor is behaviour-preserving —
the 546-test suite passes unchanged — and it means the two paths are compared
on their tracking, not on two drifting copies of the commit logic.

The word tape is what dissolves three of the four flaws at once. Position is a
float index into the concatenated words of the slides believed sung, in the
order sung; a candidate is the *n* words **ending** at a position. Spans are
retrospective by construction (flaw 1), cross slide boundaries without noticing
(flaw 2), and scale with window length (flaw 3). Flaw 4 — no way to say "none of
these match" — is the absolute threshold that separates the two states.

## Result: better than the spike, still half as good as words

| pipeline | I Surrender All | repeat-heavy |
|---|---|---|
| word-based, full Phase 7 | **0.132** | 0.256 *(from the Phase 7 notes, not re-measured here)* |
| particle filter + audio emission | 0.289 | — |
| **two-state acoustic tracker** | **0.263** | **0.535** |

The spike's 0.289 was reproduced exactly before any of this was written, so the
comparison is sound. The rewrite is a real but modest improvement on it, and
still about **2x worse than the incumbent on both recordings**. It is not a
candidate to ship, and it is deliberately not wired into `pipeline/audio_session`
— doing so would ship a regression.

What did change qualitatively: the catastrophic failure mode is gone. The spike
mis-locked once and never recovered, losing half the song. This tracker never
loses the song; it is wrong in small, recoverable ways instead, and commits
14-of-14 and 29-of-36 cues rather than freezing.

## Four mechanism findings, three of them mistakes I made first

**1. Sizing the span from the live tempo estimate oscillates violently.** The
estimate feeds the span, a short span discriminates weakly between neighbouring
offsets, the tie-break then holds the position still, and a stalled position
measures as a lower tempo again. Measured: the rate hit its floor mid-verse,
tracking stalled eight seconds, then over-estimated at 2.66 w/s catching up and
committed three slides in seven seconds (0.601). The span must scale with
*window length* — which is what flaw 3 was about — but never with a quantity it
determines. Fixed by a constant `nominal_rate_wps`.

**2. Tempo cannot be learned from one update.** Position is correctable only in
whole words, so one second's observed advance is `rate + integer`, and a single
stray offset moves the estimate by a whole word per second. An EMA over those
ratchets: 2.25 w/s measured on a verse whose true rate is 0.94, which then burned
the six-word refrain in three seconds and committed eleven seconds early.
Measuring over an eight-second horizon makes the same one-word error worth
0.125 w/s.

**3. These are sung rates, and they are far slower than assumed.** The 1.1-1.6
words/s quoted from transcript timings measures words *within* a phrase and
ignores the rests. Against golden cue timings, I Surrender All runs 0.94 w/s in
the verses and **0.48 w/s in the refrain** — six words held over twelve seconds.
The first `min_rate_wps` of 0.6 was a floor sitting *above* the true tempo of the
slowest, most repetitive part of the song.

**4. A confusion-group candidate must reach past the divergence point.** The
wide search offered bare slide openings, and when five verses open "all to jesus
i surrender" that is not weak evidence, it is *no* evidence. At t≈167 the band
was on verse 5, the shared opening was offered for every verse, verse 1 won the
coin flip, and the engine put verse 1 on screen. `index/confusion.py` already
records `divergence_idx`, so the rule is now to refuse to ask a question the
audio cannot answer: below that length the candidate is not scored at all, and
the tracker stays on alert until the discriminating word arrives. This is the
failure the previous section named as the top priority, and it is fixed.

## The remaining gap, and where it comes from

The tracker is **systematically late, by roughly half a window**. Through the
whole of verse 1 it sat pinned at 0.94-0.98 of the slide with scores of -1.3 to
-2.0 — confident, self-consistent, and seven seconds behind a band already
singing the refrain.

The cause is structural rather than a bad constant. The score asks "how well
does this text explain this window", and a span sitting a little behind the band
explains a ten-second window slightly *better* than one reaching its leading
edge: the older words are fully sung and richly contextualised, the newest are
half-uttered at the boundary. Taking the argmax therefore recovers the
best-explained part of the window, not its front.

Two attempts to correct it, both measured, neither adopted:

| lever | I Surrender All | repeat-heavy |
|---|---|---|
| `span_coverage` 0.75 (default) | 0.263 | 0.535 |
| `span_coverage` 0.9 | 0.276 | — |
| `span_coverage` 1.0 | 0.513 | — |
| `edge_tolerance` 0.15 | 0.265 | — |
| `edge_tolerance` 0.3 | 0.250 | 0.553 |

Closing the coverage slack makes it clearly worse — scoring words the band has
not sung yet costs more than the alignment ambiguity it removes, which is the
asymmetry the span-coverage default rests on. `edge_tolerance` (take the
furthest-forward position within tolerance of the best, rather than the argmax)
helps one recording and hurts the other, so it ships implemented, documented and
defaulted to **off**.

## A note on measurement noise

The window sweep on one recording reads 0.431 / 0.270 / 0.263 / 0.442 / 0.273
at 6/8/10/12/16 s. That is not a response curve — 12 s scoring worse than both
its neighbours is one mis-snap landing differently. A single 4.5-minute
recording with 14 cues is a very noisy instrument, and unlike the particle
filter there is no seed to median over: **this tracker is deterministic**, so
the variance lives across recordings and there are only two approved goldens.
Every default here was chosen only where the preference held on both, which is
why `window_seconds` moved to 10 and `edge_tolerance` did not move at all.

## Cost

800-900 ms per update at the default 10 s window — 7 candidates while tracking,
up to 30 in alert — against a 1000 ms budget. Tracking alone is comfortable; the
alert state is what pushes it, and the engine currently spends more time in
alert than the design intends, which is the same lateness problem seen from the
cost side.

## What to fix next, in order

1. **The half-window lag.** It is the dominant remaining error and neither lever
   tried here fixes it. The promising direction is to stop asking one question:
   score the *newest* audio against a short span for position, and keep the long
   window only for the identification the alert state needs. The two states
   already want different things from the window; they should stop sharing one.
2. **Re-check the alert entry threshold.** `tracking_floor` at -4.0 was never
   swept, and the engine sits in alert more than it should — partly a real
   symptom of (1), partly possibly a threshold set too high.
3. **More goldens before more tuning.** Two recordings cannot separate a 0.013
   difference, and three of the numbers above are inside that band. Sixteen
   bootstrapped recordings exist; approving a few more would buy more than any
   further sweep on these two.

---

# Diagnosis: why the repeat-heavy recording scores 0.513

*2026-08-27, tracing Reckless Love through the acoustic tracker.*

Not a deviation-handling bug. **The window length is the reaction time**, and
the tracker is late everywhere.

## The numbers

Time from a ground-truth cue until the belief shows the right words:

| | cues | median | max |
|---|---|---|---|
| ordinary | 30 | **4.3 s** | 9.0 s |
| deviations | 4 | **10.2 s** | 10.4 s |

`window_seconds` is 10. The deviation lag *is* the window, and the ordinary lag
is about half of it. The belief shows the wrong words on 68% of updates.

## The mechanism

At the t=145.8 deviation the band jumps from slide 12 back to slide 5. For the
next four seconds the belief stays on slide 12 — **scoring −1.44**, which is an
excellent score, not a confused one. It is also correct: the span "have been so
so kind to me" genuinely is in the window, because a 10-second window still
contains the previous slide's audio for ten seconds after the band leaves it.

That is the whole problem. A retrospective span scored against a long
retrospective window explains the *old* audio beautifully and pins the position
in the past. The tracker cannot notice a jump until the old words fall out of
the window, and it cannot notice an ordinary transition until they are
outnumbered.

This is the same tension the window sweep found and did not resolve:
identification wants a long window (right-words 39% → 90% from 3 s to 16 s)
while reaction wants a short one. One number currently serves both jobs.

## How it compounds at a deviation

```
 146-149   truth 5, belief 12, score -1.44   <- old audio still explains it
 151       commits slide 13                  <- spurious, score still above floor
 154       alert
 155       snaps to slide 8, commits it      <- 8 is textually IDENTICAL to 5
 157       commits slide 9                   <- tape is wrong, so the next slide is
 161       alert again
 162       snaps to 6 — correct, 16 s late
```

The snap to slide 8 is worth separating out: **slides 5 and 8 are the same
text** (the chorus's first line), as are 2/10
and 4/12. Acoustically there is nothing to choose between them, so the audience
sees the right words — but the *tape* lands in the wrong place, and the
continuation is then wrong (after 8 comes 9; the band sang 6). It costs a
second alert/snap cycle to undo.

## Instrumental holds are handled, but by luck

Two ~28 s holds on slide 8 (84.6→112.8, 171.5→199.4) are instrumental. The
score collapses to about −5.5, the tracker goes on alert, and the position
dead-reckons forward through slides 9, 10 and 11 while nothing is sung. No
harm reaches the screen only because confidence is 0.00 and the scheduler
refuses to commit. The position is still wrong, and it is wrong in a way that
would matter if anything downstream trusted it.

## What follows

1. **Re-measure `window_seconds`.** The 10 s default was chosen with both of
   the bugs fixed in the previous commit still present — a dead anticipation
   path and a single tracking span length. The optimum has probably moved, and
   the same applies to `edge_tolerance`.
2. **Separate the two jobs.** Identification and change-detection want
   different window lengths, and one encoder pass cannot serve both unless the
   score can be attributed to *part* of the window. `align()` returns
   `alignments` — where each token landed in the audio — which would say
   whether the span sits at the leading edge or in the middle. The ONNX path
   discards that; CTranslate2's does not.
3. **Break ties between textually identical slides by continuation.** Nothing
   in the audio distinguishes slide 5 from slide 8, but what follows them
   differs. Choosing arbitrarily costs a full alert/snap cycle every time.

## Re-sweeping the window after the fixes

The diagnosis predicts a shorter window should react sooner, and it does. Both
goldens, after the duplicate-notification and single-span fixes:

| window | repeat-heavy | clean |
|---|---|---|
| 4 s | — | 0.417 |
| **5 s** | **0.284** | **0.229** |
| 6 s | — | 0.444 |
| 7 s | 0.516 | — |
| 10 s (current default) | 0.513 | 0.229 |

5 s is better on the repeat-heavy recording and identical on the clean one,
which puts the acoustic tracker level with the word-based pipeline on the
deviation-heavy song for the first time (0.284 against 0.272).

**Not adopted as the default yet, on purpose.** The neighbouring values are far
worse on both recordings — 4 s reads 0.417 and 6 s reads 0.444 on the clean
one, against 0.229 at 5 s — and that is not a response curve. With a
deterministic tracker there is no seed to average over, so all the variance
lives across recordings, and there are two. The mechanism and the measurement
agree, which is worth more than either alone; the jaggedness still says the
true optimum is not pinned down. More approved goldens would settle it.

---

# Dual windows: the state picks the window

*2026-08-27.* Identification and change-detection want different window
lengths, so the state machine — which already knows which question it is
asking — chooses. Only one window is scored per update, so this costs no extra
encoder pass.

  - **tracking** → `track_window_seconds` (5 s). Narrow question, recent audio.
  - **alert** → `window_seconds` (10 s). Wide question, more context.

The floors had to be split with it. The score *level* moves with window length
— a correct span reads -3.2 against 10 s but -5.1 against 4 s, while the
wrong-candidate level sits near -5.4 throughout — so re-using the long-window
floor on a short window reads every update as unexplained.

| variant | repeat-heavy | clean |
|---|---|---|
| single 10 s (previous default) | 0.513 | 0.229 |
| single 5 s | **0.284** | 0.229 |
| **dual 5 / 10** | 0.360 | **0.227** (14/14 cues committed) |
| dual, alert window grown from doubt onset | 0.311 | 0.256 |

Dual is better than the previous default on both recordings and is what ships.
Two things it is not:

**It does not beat a plain 5 s window on the repeat-heavy recording** (0.360 vs
0.284), and the reason is worth stating because it limits the whole design.

**The short window cannot carry the absolute state signal.** Traced on the
clean recording, 36% of updates sit in alert — on a song with one deviation —
and the tracking scores (median -3.89, p10 -5.60) overlap the wrong-candidate
level of -5.4. Sweeping the floor does not help in either direction: -5.5 gives
0.420 and -6.0 gives 0.568, over-committing 20 cues for 14, because a
permissive floor keeps the tracker accepting noisy offsets. The separation is
simply not there at 5 s (measured standalone: 0.24-0.46 at 4 s against 1.26 at
6 s).

That splits the score's two uses, which the design currently conflates:

  - **Position** — the relative ordering of nearby offsets. A short window does
    this well, and it is what makes reaction fast.
  - **State** — the absolute "is this explained at all" level. This needs the
    long window's separation.

Doing both properly means two encoder passes per update (~700 ms before
scoring), against the ~525 ms the single-window version costs. That is the next
real decision, and it is a cost question rather than a modelling one.

## Tried and reverted: splitting position and state across two passes

The natural next step from the finding above — short window for *position*
(relative ordering of nearby offsets, seen promptly), long window for *state*
(the absolute explained/unexplained level, which only separates with context) —
built and measured worse:

| variant | repeat-heavy | clean | ms/update |
|---|---|---|---|
| dual, state picks the window (shipped) | **0.360** | **0.227** | ~1100 |
| position/state split, two passes | 0.460 | 0.407 | ~2090 |

The clean recording also over-commits, 18 cues for 14. Reverted.

**The literature review run alongside this explains why, and it is not about
the split.** No published real-time aligner scores a retrospective text span
against a retrospective audio window at all. They are frame-synchronous: a
per-frame observation (Brazier & Widmer emit a 60-way phoneme posterior every
40 ms with only 280 ms of future context) feeding a recursive cost matrix or
posterior, so evidence accumulates in the *state*, not in the audio buffer.
Discrimination then comes from integrating hundreds of weakly-informative
frames rather than from lengthening the window, which is why they do not face
our identification-versus-reaction trade at all.

Two specific warnings from that review land directly on this experiment:

- Duan & Pardo note raw likelihoods differ across positions by "tens of orders
  of magnitude" and swamp the process model, so they transform the likelihood
  before use. Our absolute-score thresholding is exposed to exactly that, which
  is the mechanism behind both this failure and the 36% alert duty cycle.
- The half-window systematic lateness recorded earlier is a predictable
  artefact of argmax-over-a-window scoring. It has no analogue in
  frame-synchronous systems: it does not want fixing, it wants the formulation
  removed.

So the window work is at a dead end that further tuning cannot open. See
`Plan/ASR_ENGINE.md` and the review summary for where the effort should go.

---

# rec_20260818-213617_974: why the hardest golden fails

*2026-08-28.* Traced after two more goldens were approved and this one measured
0.50 wrong-slide against 0.13-0.44 for the others.

## The song is nearly all duplicates

"King of My Heart", 13 slides:

  - slides **7, 8, 9, 10 are byte-identical** ("you're never gonna let never
    gonna let me down")
  - slides **3 and 4** are identical; **11 and 12** are identical
  - slides **1, 2, 5, 6** share a **seven-word** prefix (the chorus's
    opening words)

Twelve of thirteen slides sit in a duplicate or confusable group.

## What actually happens

Tracking is fine for the first half — slides 1 through 6 in order, marginal
0.76-1.00. Then at the t=136.4 deviation (back to slide 3) it collapses:

```
 138.6     3     6     6  0.58  0.34        evidence falling
 139.6     3     7     7  1.00  0.68   <- commits slide 7, marginal 1.00
 144.7     3     5     7  0.57  0.16   <- lost, and never recovers
 158.8     4     5     5  1.00  0.30   <- commits slide 5, marginal 1.00
 160.8     4     3     5  0.34  1.20   <- lost for the remaining 128 s
```

From t=160 the display is frozen on slide 5 to the end of the recording. The
screen shows a slide the band has left for **64% of the recording**, and
"noticing" a deviation takes a median **19.3 s** against ~3 s elsewhere.

Both bad commits are **marginal 1.00 with low evidence** — a confidently wrong
cloud, which `tracker.confidence` was built to catch and which the *scheduler's*
gate cannot see, because that gate is pure concentration.

## The marginal is structurally capped

While the chorus is sung, the mass splits across the four identical slides:

```
 259.7     8   0.30   0.86    8:0.30 9:0.29 10:0.28
 261.6     8   0.31   0.89    9:0.31 10:0.30 8:0.29
```

Mean top-slide marginal **0.34**, mean pooled over the identical set **0.79**,
against a 0.40 commit threshold and a 0.50 found threshold. The tracker is
confident it is in the chorus and cannot say which copy — and which copy does
not matter, because they display the same words.

## Two fixes tried, both measured worse

**Pooling the marginal over display-equivalent slides.** Principled, and the
index now records the equivalence classes (`IndexedItem.identical_by_slide`,
kept — it is correct information and cheap). But using it for the commit and
lost/found thresholds measured worse on every recording (mean 0.327 -> 0.339)
and barely moved the freeze (64% -> 60%). The reason is visible in the trace:
the cloud is not cleanly split across the chorus copies, it is split between the
chorus *and slide 12*, which is genuinely wrong. Pooling let it act confidently
on a belief that was partly wrong.

**Gating commits on evidence** (`min_evidence`), so a concentrated-but-
unsupported cloud cannot commit. Worse: 0.327 -> 0.344 at 0.3, 0.364 at 0.4.
Blocking a commit does not fix the belief, it only freezes the screen longer —
the same lesson `LostHoldPolicy` produced, where 59% of blocked moments were
withholding a *correct* answer.

## What is actually wrong

Neither the duplicate slides nor the commit gate is the root cause. The root
cause is that **relocalization never finds the band again after t=144** on a
song where every slide resembles every other slide: 45 of 48 blocked moments
report "no single slide clearly ahead", and only 12% of them had the right
slide on top, against 59% on Reckless Love.

That is the thing to attack, and it is the case the literature's parallel-
hypothesis designs address directly (Plan/LITERATURE.md, items 2 and 6) — not
a threshold anywhere in the current decision path.

---

# Tracing the forward filter on the hardest golden

*2026-08-28.* Two findings, one of them the most consequential measurement in
this document, and neither yields a fix that pays.

## The first three quarters are fine

Slides 1-6 track correctly at marginal 0.5-0.97, and the pooled `display`
marginal visibly does its job across the identical 3/4 pair (0.95 against a
per-slide 0.59). Both deviations recover in 5-6 s — against the particle
filter's 19.3 s median "noticing" time on this recording.

At each deviation the filter first jumps to slide 7 and commits it before
correcting. That is the model working: mass flows off the end of slide 6 at
`advance` = 0.33, while a jump gets 0.08 spread over twelve landings, ~0.007
each. Sequential continuation should win until evidence says otherwise.

## The end is genuinely wrong

From t=278.8 the band sings slide 3 (a "**you are**..." chorus line) and the filter
sits on slide 10 (a "**you're**..." line from the bridge), reporting a pooled
confidence of 0.88-0.93 while its per-slide marginal is 0.30. Position sticks
at 0.02-0.14 — it re-enters the chorus at its start rather than advancing,
because `stay` = 0.55 lets a state absorb observations without ever being
tested against the *next* word.

## Why it survives: Double Metaphone collapses our most common words

```
metaphone('are')     = ('AR', '')
metaphone("you're")  = ('AR', '')
phonetic_match_score('are', "you're") = 0.90
```

Measured across the 2913 distinct words in the library, **50% share a primary
metaphone code with another word**, and the collisions are worst where the
words are most frequent:

```
A     you(1570), i(1225), a(400), we(363), oh(181)     <- "you" and "i" are one symbol
0     the(1974), thy, they, thee, though
ANT   and(1012), want, into, end, wind
AL    all(574), will(451), well, we'll
AR    your(984), are(458), our(289), you're(198)
M     my(863), me(502)
```

Metaphone was designed for surnames, which are long. Lyrics are dominated by
short function words, where one or two symbols do not identify a word — and
`phonetic_match_score` awarded a flat 0.9 for any primary-code match regardless
of how specific the code was.

## The obvious fix does not pay

Scaling the code-match score by code length (1 symbol 0.0, 2 symbols 0.45,
3+ symbols 0.9 as before) gives the intuitively right answers — "you" vs "i"
drops 0.9 to 0.45, "are" vs "you're" to 0.70 — and measures:

| | rec293 | rec213617 | rec214054 | rec266 | mean |
|---|---|---|---|---|---|
| particle filter, before | 0.251 | 0.505 | 0.414 | 0.139 | **0.327** |
| particle filter, after | 0.252 | 0.606 | 0.407 | 0.142 | 0.352 |
| forward filter, before | 0.169 | 0.544 | 0.333 | 0.131 | **0.294** |
| forward filter, after | 0.159 | 0.544 | 0.361 | 0.131 | 0.299 |

Worse for the particle filter, unchanged for the forward one. Reverted.

**Why, and it is not obvious:** function words appear in *every* slide, so
down-weighting them lowers the correct and incorrect candidates by about the
same amount. What decides a match is the ratio between candidates, and that is
already carried by the content words. The collision is real, it is large, and
it is very nearly self-cancelling.

That does not make the measurement useless — it rules out a whole family of
"improve the phonetic representation" ideas, including the G2P-plus-confusion-
matrix direction left open in Plan/ASR_ENGINE.md. If half the vocabulary is
non-discriminative and fixing that changes nothing, the representation is not
what is limiting us.
