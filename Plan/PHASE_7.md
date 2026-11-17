# Phase 7 — Deviation handling (within song + across setlist)

## Goal

Handle the most common real-world deviations: chorus repeats, jumps to bridge/tag/ending, songs played out of setlist order. Add a confidence trigger that detects when the tracker is lost, and a staged widening that re-localizes against progressively larger candidate sets — without invoking the full library yet.

## Prerequisites

- Phase 6 complete (particle filter, scheduler, baseline metrics committed)

## Scope

**In:**
- Confidence monitor (ESS + top-marginal + **model evidence**, with hysteresis — see step 1: the first two alone cannot detect this failure)
- Stage-1 widening: re-distribute particles across all slides of current song
- Stage-2 widening: include first slides of all setlist songs
- "Lost mode" UI signaling (don't advance while uncertain)
- Recording fixtures with deliberate deviations (annotated)
- A/B regression vs Phase 6 — must beat on deviation-heavy recordings; not regress on clean ones
- Debug panel: confidence + lost-mode visualization

**Out:**
- Library-wide recovery (Phase 8)
- LLM supervisor (Phase 8)
- Override re-anchor logic enrichment (Phase 8)
- Persistent telemetry beyond live shadow logs (Phase 8)

## Implementation steps

### 1. Confidence monitor

`sidecar/auto_presenter/tracker/confidence.py`:
```python
@dataclass
class ConfidenceState:
    ess: float
    top_marginal: float
    is_lost: bool
    lost_since_t: float | None
```
- Compute ESS and top-state marginal from `BeliefState`
- "Lost" trigger: `(ess < 0.3 * N) OR (top_marginal < 0.2)` sustained for > 1 second
- "Found" hysteresis: stay lost until `top_marginal > 0.5` for > 0.5 seconds (asymmetric thresholds prevent flapping)

> **Corrected 2026-08-19: that trigger cannot detect this failure.**
> Implemented as specified, it never fired once. While the filter sat 3–4
> slides behind the band through four bridge repeats, `top_marginal` was
> 0.985–1.000 and ESS never dropped below half the cloud. The filter is not
> uncertain — it is *confidently wrong*, and both specified signals measure
> posterior **concentration**, which says nothing about **correctness**.
>
> The signal that works is **model evidence**: the per-word geometric-mean
> likelihood of the observations under the current belief, available as the
> pre-normalization weight sum in `ParticleFilter.update` and exposed as
> `ParticleFilter.last_evidence`. Measured on the two hand-verified
> recordings — tracking correctly: median 0.755 / 0.775; tracking wrong:
> median 0.294 / 0.630.
>
> Shipped trigger: `(ess low) OR (marginal low) OR (evidence < 0.45)`,
> sustained. Recovery additionally requires `evidence > 0.55`, without which a
> confidently-wrong filter "recovers" instantly on its own high marginal,
> straight back into the state the widening was meant to escape.

### 2. Re-localization controller

`sidecar/auto_presenter/tracker/relocalize.py`:
```python
class RelocalizeController:
    def step(belief: BeliefState, confidence: ConfidenceState) -> BeliefState:
        """Maybe inject particles based on lost-mode stage."""
```
Stages:
- **Stage 0 — normal tracking** (not lost): no-op
- **Stage 1 — within current song**: when first lost, inject 30% of particles across all slides of current song (uniform over slides, position=0). Allow 2 seconds for filter to converge.
- **Stage 2 — across setlist**: if still lost after stage 1, inject 30% of particles at slide 0 of every setlist song. Allow 4 seconds.
- **Stage 3** is reserved for Phase 8 (full library).

`sidecar/auto_presenter/tracker/inject.py`:
- `inject_uniform_within_song(particles, song, fraction)`
- `inject_setlist_starts(particles, setlist, library, fraction)`
- Existing particles are kept (lower weight); injected particles get weight matching the injection fraction

### 3. Lost-mode hold

Implemented in `decision/engine.py` (not the scheduler): the engine owns the
confidence monitor, so it suppresses the commit before the scheduler is
consulted. The scheduler stays a pure belief→decision function.

- When `confidence.is_lost`: suppress new advance commits — hold the last committed slide
- Resume normal scheduling when `is_lost = False` AND `top_marginal > 0.5` for > 0.5 s
- Emit AI status `"uncertain"` to UI during lost mode

### 3b. Backward commits — the blocker the plan missed

`LeadTimeScheduler.on_belief` refused any commit whose target was behind the
displayed slide (`mode_slide < displayed → return None`). **Repeats were
therefore structurally uncommittable**: the tracker re-localized correctly to
the repeated section and every one of those decisions was discarded
downstream. Confidence + widening on their own produced *zero* measurable
change until this was fixed.

Diagnostic worth remembering: sweeping `inject_fraction` from 0.3 to 0.9
changed nothing at all, which is what proved the blocker was downstream rather
than weak injection.

Fix: allow a backward commit when `displayed - mode_slide >=
backward_min_slides` (2) **and** `marginal >= backward_marginal_threshold`
(0.6). The *size* of the step back is what separates a real jump from the
normal one-slide anticipation lag — anticipation never runs more than one
slide ahead, so two or more behind cannot be explained that way. The higher
marginal is required because yanking the screen backwards is more jarring to
a congregation than being a beat late.

This is the third layer of this system found to assume monotonic progression,
after the bootstrap aligner (`research/eval/bootstrap_golden.py`) and the particle
filter itself. Anything added here that walks slides should be checked for the
same assumption.

### 3c. The scheduler never anticipated — measured, then fixed structurally

Instrumenting which branch produced each commit: **41 of 41 commits came from
catch-up or backward; none from anticipation.** The lead-time mechanism Phase 6
was designed around was dead code in production.

Two compounding causes:

1. **Unreachable branch condition.** Anticipation required
   `mode_slide == displayed`. Evidence arrives ~750 ms stale, so by the time a
   slide is committed the cloud has already moved past it, putting the
   scheduler permanently back in catch-up. Fixed by projecting instead of
   branching: ask where the band will be after `lead_time` and commit that.
   Catch-up and anticipation now fall out of one rule.

2. **The mode never reaches the end of a slide.** Measured over 299 belief
   updates: `mode_position` peaks at **0.917** (p99 0.911) and minimum
   `seconds_remaining` is 0.65 s. The cloud's mode flips to the next slide as
   soon as half the weight crosses the boundary, so the last ~8% of every
   slide is never observed. Updates that would anticipate: **0 of 299 at
   lead=300 ms**, 4 at 800 ms, 46 at 1800 ms.

   So `lead_time_ms` is inert below roughly 1.5 s — which means the shipped
   default (300 ms) *and every value in Phase 6's sweep grid* (300/500/700)
   were in the dead zone. Any conclusion drawn from that sweep is about noise.

The structural fix removes the unreachable branch, but does not by itself make
the system anticipate: extrapolating from position and rate is too coarse when
the mode abandons the slide early. Detecting a transition needs *evidence* —
scoring incoming words against the **next** slide as well as the current one,
so a transition is recognised when the next slide's words start arriving
rather than inferred from a position estimate. That is the open work.

### 4. UI signaling

`sidecar/auto_presenter/protocol.py` (extend):
- `BeliefStateEvent { top_3, ess, is_lost, stage }` — emitted at 2 Hz for the debug panel
- Extend `AIStatusEvent` with new state `"uncertain"`

`src/components/presentation/AIStatusPill.tsx` (extend):
- Add yellow "uncertain" state
- Tooltip on hover: "AI lost track — will re-localize"

`src/components/debug/BeliefStateTab.tsx` (extend Phase 6):
- Show current confidence stage (0/1/2)
- Recent lost-mode events on a timeline strip

### 5. Deviation fixtures

In `data/recordings/`:
- `chorus_repeat/` — a song where the chorus is sung an extra time
- `song_reorder/` — setlist played out of order (song 3 before song 2)
- `jump_to_bridge/` — skip from verse 1 directly to bridge
- `tag_ending/` — song ends on a repeated tag

Each annotated with ground truth + `DeviationMarker`s (timestamps where the deviation begins).

If you can't get real recordings of these deviations, synthesize them: take a clean recording and a Phase 1 lyrics blob, then re-create the desired deviation with a soloist. Mark synthetic in `meta.json`.

### 6. Recovery time + false-lost metrics

`sidecar/auto_presenter/eval/metrics.py` (extend):
- `recovery_time_ms`: for each `DeviationMarker`, time from marker to next correctly-tracked slide
- `false_lost_rate`: how often `is_lost` fires on clean recordings (target: < 1 per song)

## Files to create

| Path | Purpose |
|---|---|
| `sidecar/auto_presenter/tracker/confidence.py` | ConfidenceState + monitor (evidence-based) |
| `sidecar/auto_presenter/tracker/particle_filter.py` (extend) | `last_evidence`, `particles`, `replace_particles` |
| `sidecar/auto_presenter/tracker/relocalize.py` | Staged widening controller |
| `sidecar/auto_presenter/tracker/inject.py` | Particle injection helpers |
| `sidecar/auto_presenter/decision/engine.py` (extend) | Confidence + relocalize wiring, lost-mode hold |
| `sidecar/auto_presenter/decision/scheduler.py` (extend) | Backward commits (step 3b) |
| `sidecar/auto_presenter/protocol.py` (extend) | BeliefStateEvent, uncertain state |
| `sidecar/auto_presenter/eval/metrics.py` (extend) | recovery_time_ms, false_lost_rate |
| `src/components/presentation/AIStatusPill.tsx` (extend) | Yellow uncertain state |
| `src/components/debug/BeliefStateTab.tsx` (extend) | Stage + lost-mode timeline |
| `data/recordings/chorus_repeat/`, `song_reorder/`, `jump_to_bridge/`, `tag_ending/` | Fixtures |
| `tests/sidecar/tracker/test_confidence.py` | Threshold + hysteresis |
| `tests/sidecar/tracker/test_relocalize.py` | Staged injection logic |
| `tests/sidecar/tracker/test_inject.py` | Particle distribution shapes |
| `sidecar/tests/integration/test_deviation_recovery.py` | Recovery, Phase 6 A/B, and lost-mode harm — all against the two human-approved goldens rather than synthesised fixtures, over 3 seeds |

## Success criteria

> **Revised 2026-08-19**, after measuring. The original single wrong-slide
> target of `< 3%` conflated two failures with different causes, different
> owners, and different fixes — and was arithmetically unreachable by this
> phase's work. See "Why the criteria were split" below. The original list is
> preserved at the end of this section.

### A. Identity — does the system know *which* slide (this phase's job)

- [ ] **Recovery time** after chorus repeat: median < 4 s
- [ ] **Recovery time** after song reorder: median < 6 s
- [ ] **Wrong-slide rate, timing-adjusted**: within **8 percentage points of
      the timing floor** on deviation-heavy recordings. The floor is measured
      per recording by scoring a synthetic decision stream with perfect slide
      identity and the tracker's own measured median \|timing offset\| — that
      is the score a flawless tracker would get on that recording, so the gap
      above it is the identity error and nothing else.
- [x] **Lost mode must not do harm**, on clean recordings — *criterion
      rewritten 2026-08-21 after measuring; the original counted episodes and
      is preserved below.* Every lost episode that no `DeviationMarker`
      explains must still leave the correct slide on screen.
      (`test_lost_mode_never_leaves_the_wrong_slide_up`, 3 seeds.)

      The count-based version failed on I Surrender All at t=163s and was
      wrong to. Every verse of that song opens "all to Jesus I surrender", so
      slides 1, 4, 7, 10 and 13 match the incoming words equally; the cloud
      splits and the top marginal falls to 0.22. That is the correct reading
      of genuinely ambiguous evidence, not a false alarm — there is a third
      category between "deviation" and "panic", and it is **lyric ambiguity**,
      which a hymn with five identical verse openings produces by design.
      Counting episodes cannot tell the three apart; asking whether the screen
      went wrong can.

      Two things measured along the way that a reader should not have to
      rediscover:

      - **Holding is not what protects the display.** Re-running that window
        with Phase 7 disabled commits the same slide 10 on all three seeds:
        the scheduler's `marginal_threshold` already refuses to act on a
        belief that weak. Lost mode was *redundant* there, not protective. It
        earns its keep on real deviations, which the recovery criterion
        measures.
      - **Lost mode is not rare, and it is not free.** Duty cycle is 6-10% of
        I Surrender All but **27-41% of Reckless Love**, of which 18-30% is
        unexplained by a deviation marker — because that song repeats its
        chorus block three times and its bridge four, so ambiguity is its
        normal state. `lost_budget_multiplier=4.0` therefore holds the cloud
        at 2000 particles for roughly a third of that song, which is the
        single largest term in the perf baseline's `peak_particles`. Worth
        revisiting once recovery time is inside target — not before, since
        the extra samples are what buys the recovery.
- [ ] **No regression** on clean recordings: within run-to-run variance of
      Phase 6, measured over >= 5 seeds — not a single-run comparison.

#### The fix: `snap_window` 1 -> 5 — *2026-08-21*

The emission bias turned out to be a **units** problem in the plainest sense:
the snap window was too small, and it had been tuned against a transcript that
could not reveal that.

`best_slot_likelihood` searches ±`snap_window` slots around the particle's
estimated position. That window absorbs positional error. Under the synthetic
transcript there is none — word timings come from the ground truth, so the slot
estimate is always right and ±1 suffices. Under real ASR the timings drift by
several words, ±1 misses the true slot entirely, and the filter takes its
evidence from whatever happens to sit nearby. That is the mechanism behind the
23-second failure above: one stray "all", scored against the wrong
neighbourhood, committed a 6-word refrain while the band was mid-verse.

Swept over 8 seeds, both goldens, real ASR:

| window | Reckless Love | I Surrender All |
|---|---|---|
| 1 (old) | 0.393 | 0.243 |
| 2 | 0.348 | 0.273 |
| 3 | 0.339 | 0.122 *(unstable: spread 0.122-0.335)* |
| 4 | 0.274 | 0.147 |
| **5** | **0.256** | **0.134** *(spread 0.129-0.137)* |
| 6 | 0.261 | 0.136 |

Note window=3 on the right-hand column: a great median over 5 seeds and a
mean of 0.186 over 8. Widening the seed count is what separated a lucky draw
from a real effect, and it is worth doing before believing any single row here.

**It is also free.** Interleaved on a settled machine, `matcher_ms_mean` is
5.74 at window 1 and 5.62 at window 5 — the extra slot scans are lru_cached
and negligible. (A first reading said 1.67x and failed the gate; that was
ambient machine load, the exact trap documented at the top of
`scripts/perf_check.py`. `--snap-window` was added there so this is one command
to re-check.)

**The cost is agility, and it is already paid for.** Recovery after a
deviation degrades monotonically with the window — 6.2s to 13.4s on Reckless
Love — because a wide window lets a stale hypothesis keep finding a match, so
the filter is slower to abandon it. But `wrong_slide_rate` *includes* those
recovery periods and improves anyway: steadier tracking between deviations
more than pays for slower recovery at them. The Phase 7 recovery criterion
moves further from its 4s target as a direct result, and that is the honest
trade this makes.

**Caveat.** The transcripts are *offline* whisper over the whole file. Live
streaming ASR (`SlidingWindowWrapper`) has different timing error, so this
should be re-swept against a live-captured recording when one exists.

Also tried and reverted along the way, both refuted by measurement:

- **A length-proportional window** (`window = fraction x n_words`), on the
  theory that a fixed ±1 means different things on a 6-word refrain than a
  24-word verse. It made I Surrender All *worse* at every fraction tested
  (0.243 -> 0.290-0.510). The two songs wanted opposite windows for reasons
  unrelated to slide length.
- **Averaging over the window instead of taking the max**, on the theory that
  a max is optimistic and favours slides with several matching slots. It
  crushed the one-ahead failure (24.4% -> 2.8%) and made wrong-slide worse
  nearly everywhere. Optimism was not the problem; positional error was.

#### Real-ASR failure analysis — *2026-08-21*

Replaying the cached `small.en` transcripts instead of the ground-truth proxy.
Real ASR is much harder, and the failures are *not* the ones the synthetic
harness suggested.

Where the wrong-screen time goes (share of scored time, seed 1):

| | Reckless synth | Reckless real | Surrender synth | Surrender real |
|---|---|---|---|---|
| correct | 75.5% | 69.6% | 89.2% | **58.0%** |
| one behind (late) | 2.2% | 9.2% | 4.9% | 2.1% |
| further behind | 1.1% | 1.5% | 0.3% | 12.2% |
| **one ahead (early)** | 9.2% | 9.2% | 1.0% | **23.3%** |
| elsewhere | 12.0% | 10.5% | 4.5% | 4.4% |

Two hypotheses tested and refuted before the real one:

- *Dropped words.* ASR coverage is **109%**, not short — more words than sung.
- *Instrumental hallucination.* Only 9-10 words fall outside the sung span,
  and removing them changes nothing. (Whisper does hallucinate — the outro
  reads "the overwhelming never ending **breakfast** of god" — it just is not
  what is costing accuracy here.)

**The actual mechanism**, read off the t=163-186s stretch of I Surrender All.
The band starts verse slide 10 ("**all** to jesus i surrender..."). The word
"all" arrives and spikes slide 11 — `i surrender all i surrender all`, **six
words** — to marginal 1.00, and it commits. Ground truth stays on slide 10 for
another 23 seconds. By t=174 the cloud is back on slide 10 at marginal 1.00
and evidence 0.80, and stays there for twelve seconds. The screen never moves.

Two causes stacked:

1. **Emission is position-local, so short repetitive slides are easy to hit.**
   `best_slot_likelihood` matches within `snap_window` of the particle's
   position. On the 6-word refrain, "all" sits at indices 2 and 5 — a particle
   almost anywhere on that slide is within a window of an "all". On the 24-word
   verse, "all" appears only at index 0. Under the synthetic transcript the
   timings are perfect so positions are right and the bias never bites; real
   ASR timing jitter exposes it. This is the root cause and it is unfixed.
2. **This song is 8/14 duplicate slides.** Slides 2, 5, 8, 11 are identical
   (`i surrender all i surrender all`) and 3, 6, 9, 12 are identical
   (`all to thee my blessed saviour i surrender all`). `find_confusion_groups`
   deliberately skips identical slides — right for the audience, wrong for the
   tracker, whose *subsequent* progression depends on which copy it picked.

#### Tried and reverted: correcting an off-by-one — *2026-08-21*

The obvious fix for cause 2 above: `backward_min_slides = 2` refuses a
one-slide backward correction, so the off-by-one at t=163 can never be undone.
Distance cannot distinguish an error from anticipation (being one ahead is the
*designed* state) but duration can — anticipation resolves in under a second.
So a one-slide backward move was allowed once the belief had contested the
display continuously for 3s.

It works exactly as designed and measures worse. It fires on 1 of 5 seeds, and
on that seed wrong-slide goes **0.331 -> 0.378**. The timelines say why:

- **Off:** the premature commit to slide 11 at 163.2 sits wrong for 22s and
  then becomes *accidentally correct* at 185.8 when the band genuinely reaches
  slide 11. Pure luck, scored as skill.
- **On:** correcting to slide 10 at 177.4 buys 8.4s of correct display, then
  re-anchors the filter mid-song — and with eight duplicate slides to choose
  from it wanders into 3 -> 4 -> 7 and ends up in the wrong part of the song
  entirely. Net 113.9s -> 123.7s of wrong screen.

Reverted. The lesson is not "never correct backwards", it is that **correcting
a symptom of a duplicate-slide ambiguity re-opens that ambiguity**. Cause 1 and
the duplicate-slide handling have to come first; a backward correction on top
of a filter that cannot tell four identical refrains apart just relocates the
error.

#### Shared-opening slides, as a first-class structure — *2026-08-21*

Raised by the user: verses that open with the same words and then diverge are
normal song construction, and the engine should know about them up front
rather than have thresholds tuned to dodge them. Quantified across the
311-song library before designing:

| | |
|---|---|
| songs with slides sharing their first 3 words | **142 of 311 (46%)** |
| slides sitting inside such a group | **530 of 2828 (19%)** |
| group size | median 2, max 8 |
| divergence point | median word 4, tail to word 50 |

Built in two halves, following the user's shape — know the groups up front,
follow in order by default, branch instantly on the distinguishing word:

- `index/confusion.py` — at index time, group each song's slides by their
  first three *phonetic* codes and compute **signatures**: codes occurring in
  exactly one member's text and lying past the divergence point. Hearing one
  is proof of identity, not evidence to accumulate.
- `decision/branch.py` — at run time, if the tracker sits on a member of a
  group and a signature for a *different* member arrives, commit to it
  immediately and re-anchor the belief. Outside that situation it returns
  `None` and the ordinary path is untouched.

**One bug worth remembering, because it inverted the feature.** Signatures
were first computed by scanning only each member's *continuation*. "All to
Jesus I surrender" opens five verses of one hymn, and verse 4 says "Jesus"
again later — so `JSS` looked like verse 4's private word, and every time the
band sang the shared opening line the resolver branched confidently to verse
4. Wrong-slide went 0.156 -> 0.394: the mechanism built to fix that song
became the thing that broke it. Ownership must be judged over each member's
*whole* text; a word in the shared prefix cannot distinguish anything.
Regression-tested in `test_a_word_from_the_shared_opening_is_never_a_signature`.

**Status: the runtime override is DEFAULT OFF, on evidence. The index-time
structure stays.**

The synthetic harness could not see the feature (its transcript is derived from
ground truth, so the filter is already correct when a signature lands —
converge measured 0.00s). So the real-ASR A/B was run, replaying the cached
`small.en` transcripts via `real_asr_schedule`. Real ASR is much harder, which
confirmed the harness had been hiding difficulty: wrong-slide 0.231 -> 0.421 on
Reckless Love, 0.156 -> 0.243 on I Surrender All.

It also showed the branch does not pay, and why — the failure mode it was
designed for does not occur:

| under real ASR | Reckless Love | I Surrender All |
|---|---|---|
| top hypothesis inside a confusion group | 32 | 108 |
| ...and on the **wrong** member | **0** | **3** |
| ...with a signature word present | 0 | 0 |
| signature words arriving at all | **0** | 19 |
| ...while the marginal was < 0.5 | 0 | 1 |
| ...pointing somewhere ground truth disagreed with | – | 5 of 19 |

The particle filter almost never sits *confidently on the wrong sibling*. Its
transition prior favours sequential progress and bands sing verses in order, so
shared openings surface as the cloud **spreading** — marginal falling to
0.22 — not as the top hypothesis flipping. The resolver keys on exactly the
event that does not happen. Meanwhile it would commit at `confidence=1.0`, and
5 of 19 signature arrivals disagreed with ground truth at the moment they would
have fired (largely the sliding window reporting words late). An override that
strong has to clear a higher bar than "cannot be shown to help".

Kept, tested, and runnable via `--branch` on both `diagnose_recovery.py` and
`perf_check.py`, so a future recording that *does* exercise it can settle the
question. Not wired into `server.py`.

**What the measurement suggests instead.** The confusion groups are real and
cheap, and the shape of the data points at a different consumer: the
*confidence monitor*. A low marginal across five verses that share an opening
is not the tracker failing, it is the song being genuinely ambiguous — which is
exactly the t=163s "false alarm" documented above. Knowing up front that a
slide sits in a confusion group would let the monitor stop treating expected
ambiguity as lostness, rather than the branch trying to resolve it after the
fact. That is speculation until measured, and is written here as a lead rather
than a plan.

#### What "search" actually is — *measured 2026-08-21*

Recovery = detect + search, and "search" turned out to be a misnomer. Split it
again, into **converge** (lost → the cloud's top hypothesis is the correct
slide) and **gate** (cloud correct → a commit is actually allowed):

| deviation | converge | gate |
|---|---|---|
| 145.8 | **0.00s** | 9.41s |
| 215.8 | **0.00s** | 1.48s |
| 233.6 | **0.00s** | 1.54s |
| 251.7 | **0.00s** | 2.48s |
| 267.7 | 8.08s | 2.22s |

**Converge is zero in four of five.** The particle filter already holds the
right answer at the instant lost mode is declared — the relocalization search
is not searching, because there is nothing left to find. All of the time is
the gate, and the gate is `ParticleFilterEngine.update` returning `None` for
every update while `is_lost`.

At the t=145.8 deviation that means slide 13 stays on screen from 147.8 to
157.2 — **9.4 seconds** — while the cloud correctly believes slide 5, then 6.
Not idle: actively wrong, and knowably so.

Which half of the exit conjunction blocks, over all lost updates, 3 seeds:

| blocking | share |
|---|---|
| both fail | 42.1% |
| `found_evidence` alone | 32.2% |
| both pass (awaiting the 0.5s dwell) | 17.6% |
| `found_marginal` alone | 8.2% |

So **evidence blocks 74% of the time, marginal 50%** — and in **54% of all
blocked updates the cloud's top slide was already correct.** The gate spends
most of its life holding a right answer off the screen.

The reason is structural, not a bad constant. `found_evidence` is an *absolute*
per-word match-quality threshold, but evidence is inherently depressed in
ambiguous passages — during a repeated chorus the same words match several
slides, so per-word likelihood stays mediocre *even when the tracker is exactly
right*. The exit condition is therefore hardest to satisfy in precisely the
situation that caused the loss. Raising or lowering 0.55 cannot fix that; the
threshold needs to be relative to what good evidence looks like *for this
passage*, or the commit-suppression rule needs to stop being unconditional.

One hypothesis tested and refuted along the way: that the 4x lost-widening
dilutes the marginal and so fights its own exit. It does lower the marginal,
but reducing the multiplier makes recovery *worse* (1.0x -> 11.39s, 2.0x ->
9.65s, 4.0x -> 6.52s) even though median marginal while lost rises to 0.66.
Fewer particles buy confidence, not correctness — the cloud exits sooner onto a
wrong slide and needs another episode. Leave the multiplier alone.

#### Releasing the hold: the fix that worked — *2026-08-21*

Given the above, the lever was the unconditional commit-suppression, not any
threshold. `decision/hold.py` lets a lost tracker move the screen when its
best guess has been the *same new slide* for `confirm_seconds`, substituting
persistence for the certainty a repeated chorus cannot supply.

Measured over 5 seeds on the repeat-heavy golden: recovery **6.52s -> 5.43s**,
wrong-slide **0.260 -> 0.214**, and the clean recording bit-for-bit unchanged.

Two things this cost to learn:

1. **Do not re-anchor the belief on a speculative commit.** The first version
   called `filter.commit_slide()` on the released hold, collapsing the cloud
   onto a guess and ending the search that was still running. It looked
   excellent on seed 1 (4.87s) and swung to 16.9s on seed 5, with wrong-slide
   up to 0.514. Committing while lost now moves the screen without touching
   the belief (`_commit(..., reanchor=False)`), and the seed spread collapses.
   A fix that only works on some RNG draws is not a fix.
2. **`min_marginal` is pinned at 0.45 by the clean recording, not the deviant
   one.** 0.35 scores better on Reckless Love (0.210) but degrades I Surrender
   All's worst seed 0.161 -> 0.291, because five verses opening "all to Jesus
   I surrender" keep a wrong verse on top just long enough to satisfy the
   confirm window. 0.45 sits just under `found_marginal` (0.5), which is the
   real design statement: bypass the *evidence* half of the exit test — the
   half that blocks 74% of the time — while still demanding a clear winner.

#### Tried and reverted: leaky doubt accumulation — *2026-08-21*

Recovery decomposes into **detect** (deviation → declared lost) and **search**
(declared lost → correct commit). Measured on the repeat-heavy golden over 3
seeds: detect 3.18s, search 2.48s, total 6.52s. Detect looked like the obvious
target, and there was a clean mechanism to blame.

`ConfidenceMonitor._doubt_since` was a **hard reset**: a single update above
`lost_evidence` discarded all accumulated doubt and restarted the 1.0s
debounce. The evidence signal is noisy per-update, so this fired constantly.
At the t=215.8 deviation the evidence ran 0.20, 0.32, 0.26 — doubt for a full
second, right at the threshold — then one update at 0.54 zeroed it; detection
that should have fired at 217.2 fired at 219.8. Three of five deviations lost
time exactly this way.

Replacing the latch with a leaky accumulator (doubt integrates over elapsed
time, drains at a fraction of real time when healthy) **worked as designed and
made the system worse.** Detection got much faster, and that was the problem:

| | wrong-slide | lost duty cycle |
|---|---|---|
| hard reset | **0.323** | 48% |
| decay 1.0 | 0.338 | 48% |
| decay 0.5 / 0.25 | 0.403 | 54% |

(Same-harness comparison. True baseline on the real test harness is 0.260 /
6.52s, restored exactly after the revert.)

**The lesson, which is the reusable part:** detection latency is not the
binding constraint, and the two halves of recovery are not independent.
Entering lost mode sooner means entering it *more*, and lost mode's search is
expensive and often wrong — so buying 2s of detection costs more than 2s of
search. Anything that makes the tracker quicker to doubt has to be paid for by
making the search better first.

**Where the next attempt should go.** Search is the half worth attacking, and
its distribution is bimodal rather than merely slow — per-deviation on seed 1:
9.41s, 1.48s, 1.54s, 2.48s, 10.30s. Three deviations are already inside the 4s
target; two are catastrophic, and both are the same case (returning to slide
5). A fix aimed at those two moves the median far more than any amount of
threshold tuning. Diagnose those two specifically before touching a config
value.

### B. Timing — does it flip *promptly* (scheduler + ASR latency, not this phase)

- [ ] **Median \|timing offset\| per transition** < 500 ms. Currently ~845 ms
      on Reckless Love, ~1.7 s on I Surrender All.
- [ ] Tracked here, owned elsewhere: this is bounded below by ASR evidence
      latency (`stable_margin_seconds` + `step_seconds`), so it cannot be
      fixed inside the tracker.

### C. UI

- [ ] AI status pill shows correct state during deviations (manual verification)
- [ ] Debug panel shows confidence stage + lost-mode events live

### Why the criteria were split

Wrong-slide rate charges every transition's timing error against the whole
song, so on a 36-cue, 333-second recording it is dominated by latency rather
than by correctness. Measured, with a **perfect** tracker — right slide, right
order, every cue, nothing wrong but lag:

| lag | wrong-slide (Reckless Love, 36 cues) | (I Surrender All, 14 cues) |
|---:|---:|---:|
| 0.3 s | 3.9% | 1.7% |
| 0.5 s | 6.5% | 2.8% |
| 1.0 s | 13.0% | 5.6% |
| 1.8 s | 23.4% | 10.1% |

So `< 3%` requires sub-230 ms transition timing on that recording. The ASR
pipeline alone imposes ~750 ms of staleness before the scheduler sees a word.
No quality of confidence monitoring or re-localization can reach the old
target, and chasing it would have meant tuning the tracker against a number
governed by something else entirely.

Splitting them also isolates a real result that the combined metric hid: at
the measured 845 ms offset the floor is ~11%, and Phase 7 scores 27%, so
roughly 16 points are genuine identity error and are worth attacking — while
the remaining 11 belong to Section B.

One approach that looked obvious and does **not** work, recorded so it is not
retried: compensating for the lag by increasing `lead_time_ms`. It moves the
*signed* median offset to zero (+0.690 s -> -0.220 s at lead 1800) while making
the *absolute* offset worse (0.833 s -> 1.120 s) and wrong-slide flat-to-worse.
The error is variance, not a constant lag; firing earlier converts late
transitions into early ones, and the metric charges for both.

### Original criteria (superseded)

- Recovery time after chorus repeat: median < 4 s
- Recovery time after song reorder: median < 6 s
- Wrong-slide rate including deviations < 3% on the deviation fixtures
- False-lost rate on clean recordings: < 1 spurious lost-mode entry per song
- No regression on clean recordings: Phase 7 metrics >= Phase 6 metrics
- AI status pill shows correct state during deviations (manual verification)
- Debug panel shows confidence stage + lost-mode events live

## Tests required before phase complete

**Unit:**
- `test_confidence.py`: synthetic ESS / marginal traces fire `is_lost` at expected times; hysteresis prevents flapping
- `test_relocalize.py`: stage transitions happen at correct times; particle distributions match expected shape after injection
- `test_inject.py`: `inject_uniform_within_song` produces uniform weights; `inject_setlist_starts` covers exactly setlist songs

**Integration:**
- `test_chorus_repeat.py`: replay chorus_repeat fixture → recovery time within target
- `test_song_reorder.py`: replay song_reorder fixture → recovery time within target
- `test_no_false_alarm.py`: replay every clean recording → false_lost_rate < 1/song

**Regression:**
- A/B vs Phase 6 on full eval set + deviation fixtures: must beat on deviations; must not regress on clean recordings (within bootstrap CI)

**Manual:**
- Live demo with deliberate chorus repeat: verify UI shows "uncertain" then re-anchors visibly within target

## Risks

- **Confidence threshold is recording-sensitive.** Mitigation: calibrate on the eval set; expose as live-tunable in debug panel; consider per-song thresholds based on slide count + repetition.
- **Stage transitions oscillate** when input is borderline-noisy. Mitigation: hysteresis in BOTH directions (lost↔not lost AND stage 1↔stage 2 require sustained signal); minimum dwell time in each stage.
- **Particle injection disrupts tracker** when not actually lost. Mitigation: only inject when confidence sustainedly low; keep a fraction of existing particles to preserve continuity.
- **Setlist-out-of-order is rare in your eval data.** Mitigation: synthesize fixtures; flag as synthetic in metric reporting; don't over-fit thresholds to synthetic patterns.

## Effort estimate

7–10 days.
