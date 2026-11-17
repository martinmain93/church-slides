# What the field does, and which of it applies to us

Literature review, 2026-08-27. Our problem is **online audio-to-score
alignment**, and specifically **online audio-to-lyrics alignment**, which has
essentially one primary paper (Brazier & Widmer, ISMIR 2021).

## The hard constraint this document is filtered through

**Slide text is the only target we can assume.** A reference recording of the
song does not exist for 295 of our 311 songs, never exists the first time a
song is played, and cannot be required in the wild. Several of the strongest
results in the literature — including the one a first pass ranked highest —
depend on aligning to a reference *performance*, where "the duration of the
phoneme units is implicitly included in the reference" and tempo modelling
becomes unnecessary. **That is not available to us and is excluded here.**

Excluding it is not the same as excluding the mechanism it demonstrated. The
reason frame-synchronous alignment works is not the reference audio; it is that
the observation is *local* and evidence accumulates in the *state*. Both hold
against a text target.

## The finding that matters most

**No published real-time aligner scores a retrospective text span against a
retrospective audio window.** They are frame-synchronous: a per-frame
observation feeds a recursive cost matrix or posterior, so evidence accumulates
in the state rather than in the audio buffer. Brazier & Widmer emit a phoneme
posterior every 40 ms with only 280 ms of future context and feed Dixon's
online DTW. Discrimination comes from integrating hundreds of weakly-
informative frames, not from lengthening a window — which is why the field
never faces our identification-versus-reaction trade.

Consequences for us:

- Our measured 10.2 s reaction after a jump is a property of the formulation,
  not of the problem.
- The "half-window systematic lateness" is a predictable artefact of
  argmax-over-a-window scoring. It does not want fixing; it wants the
  formulation removed.
- **Our particle filter is the canonical design.** A filter over
  `(song, slide, position, rate)` is precisely Duan & Pardo (ICASSP 2011) and
  Montecchio & Cont (ICASSP 2011). That it still beats the newer acoustic
  tracker is what the field would predict.

## What applies, given a text-only target

| # | Idea | Source | Why it transfers |
|---|---|---|---|
| 1 | **Tempo as a state variable**: noise injected into tempo and *never* into position, and updated **only when the state crosses a word onset** | Duan & Pardo | Directly targets our measured EMA oscillation and the 0.94 vs 0.48 w/s spread. Our particles already carry a rate; this changes how it moves. |
| 2 | **Jumps inside the recursion**: explicit transitions from section ends to section starts, evaluated every frame rather than searched after an alert | JumpDTW (Fremerey et al. 2010); Nakamura et al. (TASLP 2016) | Nakamura reports **0.7 s** average recovery from repeats/skips. Ours is 10-16 s. Our songs already carry section tags, so the jump graph is derivable. |
| 3 | **Transform the likelihood before use** — raw likelihoods differ across positions by "tens of orders of magnitude" and swamp the process model | Duan & Pardo | Explains both our alert misbehaviour and why absolute-score thresholding is fragile. |
| 4 | **A consistency gate instead of an absolute-score gate**: accept a position only if the recent history fits a line of plausible slope | Brazier & Widmer (EUSIPCO 2021) | Their reliability factor lifts accuracy 85.7% → **98.9%** when set. Needs no absolute calibration, which is exactly what we could not get to separate at short windows. |
| 5 | **Fingerprint index for library-scale retrieval, then rerank** | Arzt et al. (ISMIR 2012) | 94.1% top-1 piece ID from ~2.78 s of audio at **11.8 ms/query** over 183 pieces. Fingerprints are derived from the *score*, not from reference audio, so this transfers. This is Phase 8's plan, validated. |
| 6 | **Two-level tracking**: a fine tracker over the setlist plus a coarse tracker over everything, the coarse one able to reset the fine one | Brazier & Widmer (EUSIPCO 2021); Arzt & Widmer (STAIRS 2010) | Part accuracy 28.3% → 84.0% on deliberately mutilated operas. Matches the two-tier design already sketched for library-scale search. |

## Confirmed as already right

- **Full mix, no source separation, for the tracking path.** Gupta et al.
  (ICASSP 2020) find polyphonic training beats separated vocals, because
  separation artefacts "may make the words unrecognizable".
- **Keeping Whisper.** Our WER (0.244 / 0.176) is *better* than open-source
  lyrics-transcription SOTA on Jam-ALT (~20.35). The aligner is the bottleneck,
  not the recogniser — which our own perfect-transcript experiment already
  showed.
- **Phonetic rather than orthographic matching units.**
- **Having a confidence gate at all**, even though our particular score is a
  poor one.

## Where the literature offers nothing

**Deciding when to *act*.** Henkel et al.'s automatic page-turner uses "a
simple heuristic" — turn when the position is halfway into the last system —
plus monotonicity filters. No cost model, no asymmetry between being early and
late, no hysteresis analysis. Our scheduler, lead time, hold policy and
backward-commit rules have no counterpart in the literature, and we will not
find one. That part is ours to solve.

Also unquantified anywhere found: how much accuracy is lost going from offline
to online alignment.

## Sources

Brazier & Widmer [ISMIR 2021](https://arxiv.org/abs/2107.14496) ·
[EUSIPCO 2021](https://eurasip.org/Proceedings/Eusipco/Eusipco2021/pdfs/0000366.pdf) ·
Fremerey et al. [ISMIR 2010](https://ir.webis.de/anthology/2010.ismir_conference-2010.41/) ·
Nakamura et al. [TASLP 2016](https://arxiv.org/abs/1512.07748) ·
Duan & Pardo [ICASSP 2011](https://interactiveaudiolab.github.io/assets/papers/DuanPardo_OnlinePolyphonicAudioScoreAlignment_ICASSP2011.pdf) ·
Arzt et al. [ISMIR 2012](https://www.cp.jku.at/research/papers/Arzt_etal_ISMIR_2012.pdf) ·
Arzt & Widmer [STAIRS 2010](https://ebooks.iospress.nl/publication/6255) ·
Montecchio & Cont [ICASSP 2011](https://ieeexplore.ieee.org/document/5946373/) ·
Dixon [ISMIR 2005](https://www.cp.jku.at/research/papers/dixon_ismir_2005.pdf) ·
Dannenberg [ICMC 1984](https://quod.lib.umich.edu/i/icmc/bbp2372.1984.025/1/--on-line-algorithm-for-real-time-accompaniment?page=root;size=150;view=text) ·
Raphael [NeurIPS 2001](http://papers.neurips.cc/paper/2035-a-bayesian-network-for-real-time-musical-accompaniment.pdf) ·
Gupta et al. [ICASSP 2020](https://arxiv.org/abs/1909.10200) ·
Henkel et al. [ISMIR 2021 LBD](https://arxiv.org/abs/2111.06643) ·
Morsi & Serra [ISMIR 2022](https://archives.ismir.net/ismir2022/paper/000032.pdf)

---

## Tried and reverted: parallel hypotheses as particle scouts

*2026-08-28.* Item 2 from the table above — keep hypotheses at plausible jump
targets continuously accumulating evidence, so a jump is a switch between
already-tracked hypotheses rather than a cold search after detection.

Implemented as **scouts**: after each resample, guarantee `n` particles parked
near the start of every landing, re-seeded from the weakest particles so the
leading hypothesis is not disturbed, at a fraction of mean weight so they must
earn their place from the emission.

Measured on all four goldens, 8 seeds, real ASR (mean wrong-slide):

| landings | 1/landing | 2 | 4 | 2 @ w0.1 | 2 @ w0.6 | off |
|---|---|---|---|---|---|---|
| every section start | 0.335 | 0.334 | 0.363 | 0.333 | 0.349 | **0.327** |
| only already-sung starts | 0.347 | 0.338 | 0.341 | 0.339 | 0.340 | **0.327** |

Every variant is worse. On the recording that motivated it — King of My Heart,
which never recovers after t=144 — it moved 0.505 to 0.500-0.550, i.e. nowhere.

**Why it does not transfer, which is the part worth keeping.** In the systems
this idea comes from, hypotheses are nearly free: JumpDTW evaluates jump edges
inside a DP recursion it is running anyway, and Arzt & Widmer's trackers are
cost-matrix based. **In a particle filter every hypothesis costs samples from a
fixed budget.** With 500 particles, scouting even a handful of landings takes
real mass away from the hypothesis that is right, and our emission is word-level
and sparse (~1 word/s), so a scout needs several seconds of evidence to climb —
by which time `jump_hazard_per_second` has usually already moved a particle
there stochastically for free.

That last point matters: we already ship a cheaper stochastic version of this
idea, and it *did* pay (recovery 13.6s -> 5.5s). Adding deterministic scouts on
top over-diversifies a cloud that is already diversifying.

So the literature's recommendation stands, but it is a recommendation about
*DP and HMM formulations*, and taking it up properly means changing the
inference, not adding hypotheses to a sampler. That is the same conclusion the
frame-synchronous finding reached from the other direction.

---

## Adopted (as an experiment): the dense forward filter

*2026-08-28.* `tracker/forward.py`. The frame-synchronous finding said evidence
should accumulate in the *state*, not in an audio window, and that hypotheses
should be free. The half of that which transfers to a text target is **dense
recursive accumulation**, and it is affordable for a reason easy to miss: a
song is 12-17 slides of 10-24 words, so the whole state space is 150-400
`(slide, word)` positions. A forward recursion over that costs a few thousand
multiplies per observed word — less than the 500-2000 particles it replaces.

Measured on all four goldens, real ASR, against the particle filter's median
over 8 seeds:

| | rec293 | rec213617 | rec214054 | rec266 | mean |
|---|---|---|---|---|---|
| particle filter | 0.251 | **0.505** | 0.414 | 0.139 | 0.327 |
| forward, geometric dwell | 0.169 | 0.544 | 0.333 | **0.131** | 0.294 |
| **forward, explicit duration** | **0.152** | 0.530 | **0.328** | **0.131** | **0.285** |

Better on three of four, ~10% better on the mean, and it runs the entire
four-recording evaluation in **one second, deterministically**, against minutes
of eight-seed medians.

Three long-standing problems dissolve rather than being solved:

- **Tempo needs no state.** Dwell is the self-transition probability, so a
  verse at 0.94 words/s and a refrain at 0.48 are one model. No rate, no EMA to
  oscillate, no span sized from an estimate that the span determines — three
  separately-measured failures that were all one missing dwell distribution.
  The sweep also says something counter-intuitive: `stay` wants to be **0.55**,
  far above the "advance is the common case" intuition, because the observation
  stream is not one word per sung word (Whisper inserts, the window re-reports).
- **Jumps are transitions**, evaluated every step for nothing, which is what
  `RelocalizeController` approximates expensively after a detection delay.
- **No seed.** Every particle-filter number here is a median over 5-8 seeds;
  this one is just the number.

**Not shipped, and two honest caveats.** The tuning swept ~20 configurations
against all four recordings — the entire dataset — so some of the 0.294 is fit
to it. And rec213617 remains the worst recording under either tracker (0.544
against 0.505), so the failure diagnosed in Plan/AUDIO_EMISSION.md is not
addressed by this either; it is not a relocalization problem.

Next: drive it from `ParticleFilterEngine`'s seat behind a flag, so the
scheduler, hold and branch paths are exercised identically and the comparison
is of trackers rather than of two pipelines.


## Explicit duration on the dwell (item 1, in the form that transferred)

*2026-08-28.* The tempo half of item 1 was already handled by the self-loop —
dwell *is* the tempo model, so nothing estimates a rate. What was missing is
that a geometric self-loop has **no memory**: a word already sung for five
observations is exactly as likely to continue as one just entered. That is what
let a wrong state absorb evidence indefinitely without ever being tested
against the next word (Plan/AUDIO_EMISSION.md, the chorus held through a verse
because "are" scores 0.90 against "you're").

Cont's Antescofo and Duan & Pardo both carry explicit duration for this reason.
The equivalent here is to augment the state with dwell — `(slide, word, dwell)`
— and decay the stay probability as dwell grows, which is affordable only
because the base state space is 150-400 positions.

Swept `stay_decay` from 1.0 (geometric) to 0.0:

    1.0    0.85   0.7    0.55   0.4    0.2    0.0
    0.294  0.291  0.290  0.289  0.287  0.286  0.285

Monotonic, improving every recording, and 0.0 is also the *cheapest* — fewer
states stay live, so the four-recording evaluation drops from 3 s to 1 s.
`max_dwell` measures identically at 2, 3, 4 and 6 once the decay is strong,
because nothing survives the first repeat.

At 0.0 a word may be held for at most two observations and must then advance:
a hard bound rather than a decay. That the sweep runs all the way there says
the geometric self-loop was not really modelling dwell, it was letting wrong
states persist.

**Honest size of the win: 0.294 -> 0.285, about 3%.** The mechanism is right
and the direction is unambiguous across every recording, but this is not what
closes the remaining gap. rec213617 is still 0.530 against the particle
filter's 0.505.

## In the engine seat: the standalone comparison was flattering it

*2026-08-29.* `ForwardTracker` presents the filter through the interface
`ParticleFilterEngine` uses, so both trackers now run the same scheduler, hold
and branch. That mattered: standalone, the forward filter drove a bare
scheduler while the particle filter ran the full pipeline.

Two things had to be fixed before the comparison meant anything.

**The engine collapsed the posterior on its own commits.** `_commit` calls
`filter.commit_slide` to re-anchor, which is right for a cloud that would
otherwise drift through the slide the display just left, and destructive for a
dense posterior: it discards every hypothesis the recursion has built and
restarts at word 0. Trackers now declare `reanchor_on_commit`, and this alone
moved the forward filter from 0.341 to 0.296. `AcousticEngine` had already
reached the same conclusion from the other direction.

**Phase 7 has to be off.** Its thresholds are calibrated against a particle
cloud's marginal, and its lost-mode commit suppression fights a tracker whose
jump transitions already handle recovery:

| forward filter | mean |
|---|---|
| with confidence monitor + hold | 0.409 |
| with confidence monitor, hold off | **0.835** — commits suppressed, no release, screen freezes |
| no Phase 7 | **0.296** |

## The result, like for like

| | rec293 | rec213617 | rec214054 | rec266 | mean | recovery |
|---|---|---|---|---|---|---|
| particle + Phase 7 | 0.251 | **0.505** | 0.414 | 0.139 | 0.327 | 6.2 / 31.4 / 12.0 |
| **forward, no Phase 7** | **0.192** | 0.533 | **0.328** | **0.131** | **0.296** | **3.0 / 10.8 / 5.8** |

Better on three of four and ~9% on the mean, with recovery roughly halved on
every recording where it is measurable — including 31.4 s to 10.8 s on the
hardest one, which no amount of work on the particle path had moved.

It also replaces three components rather than joining them: the confidence
monitor, the relocalize controller and the hold policy all exist to do what
dense jump transitions do inside the recursion.

**Still not shipped**, and the same two caveats stand: the transition
parameters were swept against all four recordings, so a fifth golden is the
real test; and rec213617 remains the one recording where the particle filter
wins, so the failure diagnosed in Plan/AUDIO_EMISSION.md is untouched by any of
this.

### The held-out test (2026-08-29)

Two further goldens were approved, neither used in any sweep. They are the
test the caveat above asked for.

| | tuned-on (4) | **held out (2)** | all six |
|---|---|---|---|
| particle + Phase 7 | 0.327 | 0.487 | 0.380 |
| **forward, no Phase 7** | **0.296** | **0.445** | **0.346** |
| margin | 9.5% | **8.6%** | 8.9% |

The margin on unseen recordings is within a point of the margin on the tuned
ones. Had the advantage been fitted it would have collapsed; it did not, and
the forward filter is better on both held-out recordings individually
(0.508 against 0.585, 0.381 against 0.389).

Recovery holds up too — 17 s to 11 s on the first held-out recording, and
roughly level on the second.

**But the absolute numbers are worse than four recordings suggested, for both
trackers.** 0.380 and 0.346 across six are the honest current state, against
0.327 and 0.296 across four. The new recordings are harder — one of them is the
worst yet under either tracker — and the particle filter's own tunables were
fitted on the original four as well. Six recordings should be the benchmark
from here.

### The harness was easier than the app (2026-08-29)

Everything above — every sweep, every A/B, every held-out check — replays a
**cached offline faster-whisper pass** through `MockASRBackend`
(`eval.trace.real_asr_schedule`). The timing is realistic; the words are not.
That transcript was produced over the whole file, so every word had unlimited
right context. Live, `SlidingWindowWrapper` gives Whisper a 6 s window and
1.5 s of right context, and the words come back materially worse.

Running the same six goldens through the real
`SlidingWindowWrapper(WhisperBackend)` that the app runs:

| wrong-slide rate | cached offline words | live windowed ASR |
|---|---|---|
| particle | 0.327 | 0.844 |
| **forward** | **0.296** | **0.641** |
| naive (Phase 5) | — | 0.675 |

Two things follow, and they point in opposite directions.

**The choice of tracker was right, and by more than we knew.** Forward beats
particle on all six recordings live — 0.417/0.711/0.754/0.725/0.588/0.651
against 0.662/0.971/0.803/0.908/0.799/0.923 — a mean margin of 0.203, against
0.031 on the cached transcript. The easier harness was *understating* the
difference, because clean words let a weaker tracker keep up.

**The absolute numbers are roughly double, and they are the real ones.** 0.641
means the correct slide is on screen about a third of the time. That matches
the live report ("perpetually 3-5 seconds behind", frequent wrong slides) in a
way 0.296 never did. It also puts the forward filter only 0.034 ahead of the
*naive Phase 5 matcher* on this harness, with a confidence interval spanning
zero — on six recordings we cannot yet claim the tracker beats the baseline
under real ASR.

The implication for method: **tuning on the cached harness is tuning against
words the matcher will not get.** Parameters fitted there are fitted to a
cleaner emission distribution than production has. `run_realtime_eval.py` is
now wired to `decision.production.build_engine`, so it measures the shipped
stack; it should be the gate for anything claiming an accuracy improvement,
with the cached harness kept for fast iteration only.

The gap itself is the next thing to attack, and it is an **ASR** gap rather
than a matcher gap: the same tracker loses 0.35 of accuracy purely from the
words degrading. That reframes the ONNX/decoder work from a latency project
into an accuracy project.
