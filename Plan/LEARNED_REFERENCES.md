# Learned references: how this church plays its songs

Status: **design spike, nothing built** (2026-10-07).

## Goal

The engine should get better at a song every time the church sings it. A lead
sheet says what the song *is*. Each service shows how *this* band plays it:

* the key they sing it in, and the tempo;
* the order of sections, and how many times each repeats ("bridge three times");
* how long the intro and the gaps are;
* where they vamp;
* the chords and melody they really use.

Each service's evidence should be weighted below the lead sheet's at first. As
services agree, it should overtake the sheet. When services disagree, it
should lose confidence.

## Definitions

| Term | Meaning here |
|---|---|
| Content | What the song is: the words, melody and chords of each section. Mostly the same week to week. |
| Arrangement | How this band plays it: section order, repeat counts, key, tempo, intro and gap lengths. Changes by church, by leader, by week. |
| Performance summary | A small record of one performance of one song, written once the song ends. |
| Hindsight pass | Re-running the follower's maths over a finished song's logged evidence, using later evidence too (forward-backward smoothing). It is far more accurate than the live, causal belief. |
| Pseudo-count | How many services the lead sheet "counts as" when it is mixed with what was observed. |
| Provenance | Where a session came from: live service, rehearsal, replay or eval, editor, or dev injection. |
| Shadow mode | Collect and store summaries without anything consuming them yet. |

## What exists that this builds on

* `song_references` (migration 007). This table already reserves a many-per-song
  `recording` kind for "auto-goldens".
* `follower/reference.py::order_prior(seg_slides, sequences, sung)`. It already
  takes a *list* of observed slide sequences. Today the only list passed is the
  sheet's roadmap, at `ROADMAP_WEIGHT` 0.85.
* `song_spans` already copies a printed verse's spans onto unprinted verses of
  the same section type. That is the user's "pre-populate verse 2 and 3 from
  verse 1" idea, done statically from the sheet.
* Known-key song-switch rule (`research/spikes/music_only/song_switch_rule.py`).
  On the set and the medley it gave 0 false switches. Song changes landed
  +30/+52/+1 s against production's +42/+109-or-never/+12. It was parked because
  the library has no per-song key. This is the product question "remember key
  per song after it's followed".
* Slide-change sources (`manual` / `override` / `ai` / `voice`) live in recording
  ground truth and in the follower trace (`LiveFollowers._log`). Recovery
  detection already exists.

## What was measured today (to ground the risks)

The recording store holds 126 recordings, 78 songs, and 57 songs with ≥2
"performances". This looks like a corpus, but it isn't one:

* **91% of recorded moves have under 1.5 s dwell (median 0.08 s).** These are
  editor, bootstrap, replay and scrubbing traffic, not a band.
* One song shows 22 distinct orders across 22 "performances".
* Monotone runs like 9, 8, 7, 6, 5 are an operator stepping back through
  slides, not a band singing backwards.
* Rapid corrections (an AI move followed within 4 s by a manual one): 4 of 107
  AI moves.

**Consequence:** a `manual` label is not ground truth. Learning needs a
provenance flag and label cleaning before anything is counted. No real
repeated-service data exists yet, so **collection is the critical path**: it
takes weeks of Sundays to accrue.

## Design

### 1. Split arrangement from content, and learn arrangement first

| Layer | Learned from | Used by | Value | Risk |
|---|---|---|---|---|
| Key (per section, to catch a final-chorus lift) | chord histogram over the hindsight-aligned song | known-key song-switch rule; follower key prior (skips the key search) | **high**, the measured song-change win | low |
| Tempo (bar tempo) | beat or bar tracker | rate-state prior; scheduler time-to-end | ~0 accuracy, small CPU saving | low |
| Section order and repeat counts | cleaned slide sequence | `order_prior` sequences; scheduler fork decisions | medium–high | medium (spontaneity) |
| Dwell, intro and gap lengths | aligned segment boundaries | scheduler anticipation, spontaneous hold | low–medium | low |
| Content templates (chords and melody per segment) | hindsight-aligned music evidence | a **recorded reference** for songs with no lead sheet | potentially largest | high (feedback loops, needs ≥2 clean services) |

### 2. Learn after the song from hindsight, never from the live decisions

There are two separate things:

* **Within-performance adaptation (real time, held in memory only).** This
  covers the key lock, the tempo state, a verse template copied from verse 1,
  and "they've done the bridge twice". Some of it exists already. It is
  dropped at song end.
* **Across-performance learning (persisted).** Each item writes **one
  performance summary at song end**. It is built from a hindsight pass over a
  compact evidence log plus the cleaned operator moves.

Why not persist live decisions: a causal belief is wrong exactly where the
learning matters most, at forks and repeats. Persisting it would teach the
engine its own mistakes. A hindsight pass sees the words that settled the fork
3 s later.

### 3. Label sources and trust

| Source | Weight | Cleaning |
|---|---|---|
| Golden-editor edits | 1.0+ (treated as truth) | none |
| Operator-led, stable | 1.0 | Collapse scrubbing (moves under ~1.5 s) to its final target. Drop a transient that was corrected within 4 s. Canonicalise identical-text slides, because an operator who stays on slide 3 for a repeated line is not saying "no repeat". Treat blank as "operator chose blank", not "band stopped". |
| Operator-led, contradicted by audio | 0.3, or excluded | If hindsight says the band was on the repeated chorus while the operator sat on the verse, trust the audio for *order* and the operator for *nothing*. |
| AI-led, hindsight margin high | 0.3 | Use only where the smoothed posterior beats the runner-up clearly. A detected recovery (AI showed X, the band was on Y) records the transition the band *actually* took, which is the valuable negative signal. |
| AI-led, low margin | 0 | |
| Rehearsal, replay, eval, `inject_audio`, editor playback | 0 | Provenance gate. |

There is a cap: AI-only evidence for a song can never outweigh the sheet until
at least one operator-led service agrees with it. Without that cap, a set the
AI ran wrong for months would become self-confirming.

### 4. Confidence: counts with a prior and decay

Every learned quantity is a set of weighted counts. The lead sheet enters as
a prior worth `α` services, with a proposal of `α = 3`. A service's weight
`w ≤ 1` keeps one service below the sheet, as asked.

* **Order:** Dirichlet counts per segment transition. The learned share is
  `n_eff / (n_eff + α)`. That is 25% after one clean service, 50% after three
  and 77% after ten. It is capped at 0.9 so the library-order floor stays and a
  novel arrangement can still be followed.
* **Repeat counts:** a categorical over "how many passes" per section cluster
  (bridge ×2: 3, ×3: 7). First-order transitions can't express "exactly three
  times"; they imply a geometric count. See phase 3.
* **Key:** categorical over 12 shifts. **Tempo:** a robust median and spread
  of bar-tempo.
* **Content:** per-cell soft chord counts, summed over each service's
  posterior. A contradiction dilutes the peak, so confidence drops. Once the
  learned counts outnumber `α` and disagree with the sheet, the learned version
  wins. "Correct the reference" falls out of the counting; it needs no
  separate rule.
* **Decay:** exponential by performance age, with a half-life of about 6
  months. A new leader or the new live single changes arrangements.
* **Aggregate on load, not on write.** Each performance stays its own row.
  Decay, leader conditioning and weights can then change without a migration.
  The operator can delete one bad service (a wedding medley), and held-out
  evaluation is possible.

### 5. Anchor everything to lyric text, not slide indices

Slides get edited, split and reordered, and setlist-scoped orders exist. A
summary stores each segment as a canonical text key (the normalised first
line plus a hash) and re-binds on load with the same fuzzy binding as
`bind_slides`. Rows that no longer bind are dropped silently and counted.

### 6. Where it lives in the codebase

New package `sidecar/auto_presenter/learning/`. It has no imports *into*
engines; it is an observer and a read-only prior.

```
learning/
  log.py        PerformanceLog — subscribes to the existing trace/slide-shown
                events; bounded in memory (~300 KB/song, chords quantised)
  labels.py     provenance gate, scrubbing/correction collapse, identical-
                slide canonicalisation
  hindsight.py  forward-backward over the follower's reference + the log
  summary.py    PerformanceSummary (versioned JSON, ~5-10 KB)
  profile.py    aggregate rows -> SongProfile (counts, decay, sheet prior)
  prior.py      read-only API: sequences(), key_prior(), tempo_prior(),
                dwell(), repeat_counts()
```

The engines touch it in three places only:

* `SegmentReference` gets an optional `extra_sequences` and weights. The
  default is today's behaviour.
* `LiveFollowers` gets an optional key and tempo prior.
* The song-switch rule gets a key per song.

Storage uses `song_references` kind `performance` (many per song). Rust needs
one line added to the allowed kinds. Kill switches: tunable `learned_collect`
(shadow mode, can default on) and `learned_use` (off).

A product surface comes later: "Your team usually plays V1 C V2 C B B B C"
with a one-click *set as default order*. It reuses the song default
slide-order feature, makes the learning inspectable and correctable, and adds
a "forget what you learned" button.

### 7. CPU

* **Live:** prior lookups at song entry and an append-only log. Effectively zero.
* **Hindsight:** about 3,000 steps × (≈600 cells × 16 rates) per 5-minute song
  is roughly 30 M multiply-adds of numpy, under 1 s. It runs on a background
  thread at song end, CPU only, and never on the GPU queue the scorer and
  Whisper share. A tighter alternative is to defer it to service end or idle.
* **The one real cost:** songs with no lead sheet run no music layers today.
  Learning content for them means running chords (BTC, RTF ~0.06) all the time.
  Phases 0–3 don't need this; phase 4 does.
* **Storage:** a few KB per song per week, for life.

## Phases

| # | What | Behaviour change | Gate before the next phase |
|---|---|---|---|
| 0 | Provenance flag on sessions; PerformanceLog + labels + hindsight + summaries in **shadow mode** | none | Summaries look right on the 8 live traces; **start collecting on real services now** |
| 1 | Learned **key** (and tempo) per song → known-key song-switch rule + follower key prior | song changes earlier | Chronological hold-out: key from services 1..k predicts k+1 ≥ 70%; switch rule still 0 false |
| 2 | Learned **order** into `order_prior` (Dirichlet + sheet pseudo-count); suggested default order UX | follower order prior | **Arrangement predictability**: share of service k+1's transitions that the learned top-1 predicts beyond what the sheet predicts. If low, stop here. |
| 3 | **Arrangement templates**: a posterior over the top-K observed roadmaps (prefix-matched) gives repeat counts and forward look-ahead; the scheduler uses it at forks (spontaneous-hold off when confident, on-rails anticipation) | scheduler | wrong-time on repeat/fork cues, held out |
| 4 | **Recorded references** for sheetless songs (content templates from hindsight-aligned chords + word timings) → follower engages without a sheet | follower coverage | ≥2 clean services of one song; real-time gate vs production |
| 5 | Within-performance section templating (verse 1 → verses 2 and 3) in memory | small | measured gain, or drop it |
| — | Later: cross-church shared arrangement priors (opt-in, aggregate only) | — | privacy design first |

Evaluation rules:

* Strictly chronological hold-out: learn from earlier services, score the
  next. Never in-fit (see the held-out validation memory).
* Use the real-time gate for any accuracy claim.
* Until repeated services exist, test the *mechanism* on synthetic
  arrangements: splice golden audio at slide boundaries into, for example, a
  bridge ×3 version. This proves the plumbing, not the effect size.

## Effectiveness estimates (guesses with their basis)

| Piece | Estimate | Basis | Confidence |
|---|---|---|---|
| Learned key → song changes | song switches ~10–60 s earlier with no new false switches, *if* the key repeats ~75%+ of weeks | measured rule (+30/+52/+1 vs +42/+109/+12 s) used same-recording keys (optimistic) | medium |
| Learned order | wrong-slide −0.01 to −0.04 on follower songs; most of the 5–20 s repeat lateness gone *when the arrangement is habitual* | off-order cues are now most of the follower's lateness (median +5.7 s); `ROADMAP_WEIGHT` 0.5→0.85 alone was worth 0.013 | medium-low: depends on how habitual the band is (unknown) |
| Repeat templates + scheduler | −0.01 to −0.03 | on-rails anticipation was only 0.005–0.01; repeats-not-followed was 25% of production wrong time | low |
| Sheetless recorded references | 0.33 → ~0.22–0.26 on songs with ≥2 clean services | the WABN self-repeat recorded reference reached 0.186 (n=1, same recording, optimistic) | low; largest upside |
| Verse templating | < 0.01 | words already identify verses; music only helps in word droughts | medium |
| Tempo prior | ~0 accuracy | oracle tempo ceiling was ~0.01 | high |

## What could go wrong (self-critique)

1. **Self-training loop.** AI-led learning confirms its own errors.
   *Mitigation:* hindsight only, a margin gate, low weight, and the cap
   requiring operator agreement before AI-only evidence can outweigh the sheet.
2. **Operator labels are not truth.** Operators click late or early, skip
   slides, stay on identical slides, leave the screen blank, and scrub.
   *Mitigation:* the cleaning rules, identical-slide canonicalisation, and
   audio timing in place of click timing.
3. **Contaminated sessions.** Rehearsals, replays, `inject_audio` and editor
   playback get counted (today's store is 91% this).
   *Mitigation:* the provenance gate, enforced in code rather than by
   convention.
4. **Spontaneity punished.** "Always bridge ×3" becomes a prior that drags the
   screen back to the bridge in the week they do it twice. A strong wrong prior
   costs a few seconds, which is exactly where it fires.
   *Mitigation:* cap at 0.9, keep the library floor, and let words override
   (bridge → chorus differ lexically, so recovery takes ~3 s). Measure the
   cost on held-out "deviation weeks" specifically.
5. **Drift.** A new leader, key changes for a different voice, or the new live
   version.
   *Mitigation:* decay, a key *distribution* rather than a single key, and
   per-leader conditioning if the setlist knows the leader.
6. **Library edits orphan learned data.**
   *Mitigation:* anchor to text and re-bind; count the unbound rows.
7. **The first-order Markov model can't express repeat counts.** Feeding
   observed sequences into `order_prior` (phase 2) learns "bridge often
   repeats", not "three times". Phase 3 exists for this. Don't oversell phase 2.
8. **Hindsight shares a model with the live follower.** Its errors are
   correlated with the live ones, so it can't fix a reference that is wrong in
   the same way.
   *Mitigation:* operator-led services are the independent check; sheet
   disagreement gets flagged, not silently absorbed.
9. **Coverage gap.** The follower is MLX-only and lead-sheet-only today, so
   phases 2–3 only help that subset. The production particle filter has its own
   transition model; exposing `prior.py` to it as well is phase 2b.
10. **Evaluation drought.** No real repeated services exist. Everything except
    phase 1's mechanism waits on weeks of data. That is why phase 0 should ship
    first and quietly.
11. **Medleys and inserted tags** (Doxology between songs) look like cross-song
    transitions. Phase 0 logs them; nothing learns them yet.

## Alternatives considered

* **Keep raw audio and rebuild references offline (full auto-golden).** This
  has the highest fidelity. It costs ~130 MB per hour, raises a privacy
  question, and is heavy to re-run. Use it only for opt-in recordings, as a
  bonus input to the same summaries.
* **Online parameter learning inside the follower (online Baum–Welch).** It is
  elegant but couples learning to engine internals, invites feedback loops, and
  is hard to debug or undo. Fine for ephemeral within-song adaptation; rejected
  for persistence.
* **Per-performance summaries aggregated on load (recommended).** Separable,
  cheap, inspectable, deletable, and evaluable chronologically.
* **Arrangements from the internet** (chord sites, YouTube live versions). This
  raises licensing questions, and the result isn't *this* church.
* **A trained neural arrangement model.** Not enough data, by orders of
  magnitude.

## Decisions (2026-10-07, round 2)

* **Leader:** not known, so there is no per-leader conditioning.
* **Visibility:** the learning is invisible except in a dedicated dev debug
  panel. Learned order counts toward slide predictions from day one. Above a
  confidence threshold it quietly writes the song editor's slide-order field,
  but never over a hand edit, and that needs a provenance marker. The
  slide-order duplication bug must be fixed first, because learned orders
  repeat section tags.
* **AI-led sets** count from day one, at low weight and gated on a clear
  look-back margin.
* **First pass covers arrangement and word timing only.** The look-back runs
  as a delayed background process: a fixed-lag smoother about 10–15 s behind
  live, finalised at song end.
* **The look-back has its own neutral model** (library order plus generic
  jumps), with no learned prior and no engine decisions in it. Without that
  separation, what it learns feeds back into itself within one service.
* **No locks.** The look-back writes rows; an aggregator builds an immutable
  `LearnedPrior` snapshot; the engines swap it in at song entry. Mid-song
  pickup is a later experiment.
* **Lead-sheet rows are never mutated.** Learned data lives in `performance`
  rows, aggregated on load.
* **Consumption is not free.** Only the follower reads references, only
  lead sheets, and only once at session start. The production forward filter,
  which runs most songs, has generic `stay`/`jump` weights and no per-song
  order. It needs a per-song transition hook.

## Round 3: the follower without a lead sheet (measured 2026-10-07)

`research/spikes/music_only/nosheet_replay.py` replays the 8 live traces
(`REF=sheet|library|oracle|cross`). The follower is given a synthetic
reference built only from the library slides: one word per beat and a
one-bar gap between lines. Song changes come from the trace, as in
`music_replay.py`. Each cell is the wrong-slide rate (lower is better).

| Reference | tuning (4) | held-out (4) |
|---|---|---|
| production engine (from earlier runs, approx.) | ~0.38 | ~0.455 |
| library words, library order, no music | 0.287 | 0.358 |
| library words, **operator's own order** (in-fit ceiling) | 0.235 | 0.298 |
| real lead sheet, no chords | 0.208 | 0.284 |
| real lead sheet + live chords (today's follower) | 0.171 | 0.245 |

How to read this:

* **The follower doesn't need a lead sheet to be useful.** Library words alone
  beat production on average, so the sheet requirement can go. The exception is
  the 4-song live set: 0.584 against production's ~0.524, because the singing
  is hard to hear. That needs a real-time run before this ships.
* The value splits roughly into three equal parts:
  * **order ~0.05:** the oracle, so a ceiling;
  * **word rhythm and timing ~0.03:** the oracle-order versus sheet-no-chords
    gap;
  * **chords ~0.04.**

  The first two are exactly what the first-pass learning targets.
* **Cross-performance, n = 1.** Build My Life was played in two different
  orders. One sang the chorus twice before the bridge; the other went straight
  to it. Learning the order from the *other* service gave ~0
  (0.146 → 0.145, 0.584 → 0.574). Order learning pays only when the band is
  habitual, and it needs several services to average over.

### Design: one neutral reference spec, three sources

`SegmentReference` is currently built from a `Score`. The plan is to put a
neutral `ReferenceSpec` in between:

* per slide: words with onsets in beats, and optional melody and chord cells;
* the order sequences, with their weights;
* an optional intro length.

A builder per source produces it, plus a blend:

1. **lead sheet → spec:** today's code, moved.
2. **learned performances → spec:** the aggregated order, plus median word
   onsets converted to beats at that performance's tempo.
3. **library-only → spec:** the fallback for every song.
4. **blend:** the sheet supplies rhythm and music; learned rows supply order
   and timing, weighted by `n_eff/(n_eff+α)`.

The follower engages for every song (MLX), no longer only songs with ≥80%
sheet coverage. That engagement sits behind its own tunable until a real-time
run confirms it, the live set especially.

### Scale: 200 performances

200 synthetic summaries come to 270 KB. Loading them from SQLite and
aggregating them (decay-weighted transitions, key histogram, median onsets)
takes **8.8 ms per song** in plain Python and numpy. A 6-song setlist is under
60 ms at session start, so no cached aggregate is needed. If it is ever needed,
store a materialised aggregate row keyed by (row count, newest row, algorithm
version).

## Round 4: built (2026-10-08, branch `learned-references`)

All numbers here are **trace replays** (`nosheet_replay.py`), not the
real-time gate. The real-time run waits until the feature is complete.

### What exists

| Piece | Where | Notes |
|---|---|---|
| Neutral reference spec | `follower/spec.py`, `reference.sheet_spec` | The sheet path reproduces the old numbers exactly (0.168 / 0.252). |
| Library-only follower | `spec.library_spec`, tunable `score_follower_without_sheet` (off) | 0.290 / 0.352 against production ~0.38 / ~0.455. One real-time spot check: Build My Life 0.153 (replay said 0.150). |
| Look-back | `learning/lookback.py` | Forward-backward over (slide, word) on heard words, neutral library-order model, 15–430 ms per song. |
| Labels | `learning/labels.py` | Scrub and correction cleaning, who led, agreement, weights. |
| Observer + store | `learning/session.py`, `store.py`, `server_learning.py` | Watches the session, aligns every 15 s in the background, writes rows to `learning.db` beside the library. Injected audio is kept with weight 0. Tunable `learned_collect` (on). |
| Aggregation | `learning/profile.py` | Rows re-bound by lyric text, decay half-life 182 days, order and word timing. |
| Learned reference | `profile.learned_spec`, `order_prior(learned=)`, tunable `learned_use` (on) | Applies to every follower song: lead sheet order, library order and timing. |
| Debug panel | `LearningTab.tsx`, `get_learning_state` / `delete_performance` | Verified against an injected Build My Life session. |

### The look-back against the operator (8 traces, words only)

Confident passes (≥ 0.8), with a line repeated in a row collapsed: 1.00
precise on 6 clean songs. The hard ones read 0.86 (set Build My Life) and
0.78–0.88 (medley). Line starts land within ±0.1–0.4 s of the operator's
click.

### Learned references (in-fit = learned from the same recording, a ceiling)

| Follower reference | tuning | held-out |
|---|---|---|
| library | 0.290 | 0.352 |
| library + 1 service, unblended timing | 0.222 | 0.344 |
| library + 1 service, timing α = 3 (shipped) | 0.266 | 0.335 |
| operator-order oracle (earlier) | 0.235 | 0.298 |
| sheet | 0.168 | 0.252 |
| sheet + 1 service (order only) | 0.160 | 0.254 |

**Cross-service is the only honest test available**: Build My Life learned
from its other service. Each cell is Build My Life / the set.

* Library base: 0.150 / 0.556 → unblended timing 0.188 / 0.530; α = 3 gives
  0.154 / 0.546.
* Sheet base: 0.177 / 0.461 → 0.166 / 0.465.

Findings:

* **Line timing is learned in words, not seconds.** Each service's times are
  divided by its own median seconds per word. Build My Life's line 6 took
  15.4 s in one service and 21.1 s in the other, and learned lines in seconds
  next to library lines in beats confused the tempo.
* **One service's order barely moves the prior.** That is by design: one
  service has a 25% say. The order oracle shows the ceiling at about 0.05.
  Reaching it needs habitual bands and several services, which these
  recordings cannot show.
* **Timing does most of the in-fit gain.** It is also the riskiest across
  services, hence α = 3: a lead sheet or the library counts as three services,
  for timing as for order.

## Round 5: what a look-back pass is worth (2026-10-08)

`research/eval/calibrate_lookback.py` labels every look-back pass right or wrong
against the golden. A pass is right if the golden showed its slide for most
of the pass. It then fits confidence → P(right), held out by recording.

* **Live words (8 traces): 300 passes, 76% right unfiltered.** Raw confidence
  is overconfident from 0.4 to 0.95 and accurate at the top (≥ 0.98 → 98%
  right).
* **No fitted map beat the raw posterior held out.** Brier on live words: raw
  0.137, Platt 0.159, Platt + word count 0.166, 10 bins 0.172. Whole-file
  Whisper on 8 approved goldens: all within 0.104–0.110.
  * The reason is that recordings differ: the live set is 51% right, the rest
    87–94%. A map fitted to 7 recordings mis-predicts the 8th, and the
    posterior already carries the difference.
  * So raw confidence is used as P(right).
* **Line changes (what learning counts):** P(both lines right) ≈ conf(A) ×
  conf(B). It is a little optimistic in the middle (0.92 → 72% right) and
  accurate at the ends.

| Rule (live words) | changes kept | right (by weight) |
|---|---|---|
| old: both passes ≥ 0.8, equal weight | 62% | 82.9% |
| **new: product ≥ 0.7, weight = product** | 64% | **84.1%** |

On whole-file Whisper the two rules read 91.0% and 91.2%. Passes below 0.7
count for nothing and break the chain rather than bridging it. Line timing
uses medians weighted by pass confidence.

Replays (`nosheet_replay.py`):

* In-fit: 0.266 / 0.335 → 0.270 / 0.328.
* **Cross-service Build My Life / set: 0.154 / 0.546 → 0.142 / 0.546.** Library
  alone is 0.150 / 0.556, so it now beats it on both recordings.
* Sheet + learned: flat.

As expected, the look-back sits between the live matcher and the goldens. Its
kept line changes are right about 84% of the time on live words (about 91% on
clean transcripts), and on hard-to-hear singing it is mostly unsure rather
than wrong.

## Round 6: real-time gate (2026-10-08, after merging main)

`run_follower_eval.py`, real server and real time, on the 4 held-out
recordings. The machine was loaded (load average ~6) throughout. Each cell is
the wrong-slide rate.

| Recording | production | follower, no sheet | follower, lead sheet |
|---|---|---|---|
| Build My Life | 0.197 | **0.150** | 0.177 |
| Since Your Love | 0.431 | **0.278** | 0.051 |
| Blood medley | 0.466 | **0.291** | 0.156 |
| live set (4 songs) | 0.399 | **0.252** | 0.211 |
| **mean** | 0.373 | **0.243** | 0.148 |

* **No sheet beats production on 4 of 4, the live set included.** Replay had
  feared the set: 0.556 against ~0.524. Both engines are better since main's
  merged fixes, and the follower more so.
* **No sheet runs later.** Median lateness was +1.0 to +2.6 s against
  production's -0.07 to +3.6 s.
* **Learned from the other Build My Life service (cross-service, honest):**
  * Build My Life: 0.150 → 0.138, and lateness +1.36 → +0.27 s.
  * Live set: 0.252 → 0.366. **That is not the learning.** Split by song, the
    set's Build My Life (the only song with learned data) is unchanged at
    148 s vs 150 s wrong. The extra error is in Awesome God (+18 s) and
    Doxology (+37 s), which are identical between the two runs. Doxology alone
    read 10 s wrong in one run and 47 s in the other.
  * So real-time run-to-run noise on the set is about ±0.05–0.1. Treat single
    set runs accordingly.

**Recommendation:** turn on `score_follower_without_sheet` by default (MLX
sessions only). The learned layer is neutral to positive where it applies.

## Round 7: learning in the service, from the service (2026-10-09/10)

A song with no lead sheet learns its own music *while it is sung*: a
look-back a few seconds behind live writes what it is sure of into the live
follower's reference, and lends it to the slides likely sung to the same tune,
so verse 3 has a rhythm and chords before anyone sings it. Built as
`follower/learn.py` (`ServiceLearner`) and `LiveFollowers.apply_learned`,
behind the hidden tunable `in_service_learning` (default **off**).

### How a slide is learned (the user's constraints: only when sure, never clobber)

* **Which pass:** `learning.lookback.align` (forward-backward over the words);
  a pass is used once it ended 2 s ago, with confidence >= 0.7, its first or
  second word heard and >= 60% of its words heard.
* **Where each word fell:** the slide's lyrics forced onto that audio with the
  live scorer's own DTW (`MLXSpanScorer.word_starts`), the previous line's last
  2 words in front to absorb the lead-in. Alignments run on the ASR thread in
  ticks the span scorer leaves idle, so the GPU stays one queue.
* **In musical time:** Beat This beats, cleaned (missed beats put back,
  doubles dropped). A song whose *raw* grid has >= 8% of gaps over twice the
  median is not learned at all (Build My Life, two of the live set's songs).
* **Chords:** the session analyser's, per half-beat cell, kept at >= 0.35.
* **Settling:** for 30 s a learned slide follows the look-back's current view of
  the same pass, and is un-learned if that pass stops qualifying; then it
  freezes, so a later stray word cannot replace it.
* **Sharing:** lent to slides of the same metre (exact syllable count, word
  counts within 1.25x, chained) until they are heard themselves. Library
  section tags are not musical sections (152/314 songs tag every slide "verse
  N"), so tags are not used.
* **Rebuild:** the reference is rebuilt with the learned segments; the belief is
  carried by each slide's share and the re-filter window's raw evidence pushed
  again.

### Replayed (8 live traces, songs without lead sheets, wrong / perceived)

| arm | wrong | perceived |
|---|---:|---:|
| library slides only | 0.219 | 0.254 |
| learned, naive (own slides only) | 0.196 | 0.244 |
| **learned + metre groups (what ships)** | **0.189** | **0.229** |
| learned + the sheets' own tune groups (grouping ceiling) | 0.177 | 0.213 |
| the sheet's music on library order (ideal learning) | 0.156 | 0.179 |
| lead sheet | 0.130 | 0.156 |

Perceived = wrong time, with "random" mishearing jumps weighted 5x
(`error_buckets.py`).

Shipped as defaults on the way (they help every follower):
`reference.CHORDLESS_AT_MEAN` (a cell with no chord scores the mean chord
evidence, so a partly learned song doesn't pull toward its learned slides) and
`online.HOLD_HEARD_LINE_S = 8` (the collapse rule can't leave a slide that
still carries the line just heard).

### Hard limits found

* **Medley tag vamps** (Nothing But The Blood): the learned line length is one
  pass's; a vamp repeats the tag an unknown number of times. Learned is worse
  than library there.
* **Grouping from text alone:** metre over-groups modern songs (unlike lines of
  like length) and misses hymn verses off by one syllable. The sheets' melody
  groups are worth another ~0.012.
* **Identical copies** (King Of My Heart 7-12) never get a confident pass, so
  never learn. Pooling confidence over copies admitted wrong passes (worse).
* Tried and left off: repeat spans, pooling identical copies, chordless
  quantiles, beat-rate units, order learned in-service (helps only a move's
  2nd occurrence), blank-when-lost, chords-within-slide, per-slide jumps.

### Real time (2026-10-10, `run_follower_eval.py --configs nosheet,learning`)

Real server, real time, no lead sheets, MLX small.en; load average 4-5 (the
machine's own apps; the quiet gate was loosened from 4 to 5 because Spotlight
indexing never let it pass). Library-only and learning run back to back per
recording. Final code unless marked.

| recording | library only | + learning | |
|---|---:|---:|---|
| I Surrender All | 0.294 | **0.232** | hymn: verses share a tune |
| Since Your Love | 0.261 | **0.123** (0.134, 0.141 on earlier builds) | |
| WABN | 0.249 | 0.234 | tempo flips: not learned (was 0.295 when learned) |
| live set (4 songs) | 0.232 | 0.262 | 2 of 4 songs learned; per song within noise |
| Blood medley | 0.220 | 0.269 | the tag vamp (Nothing But The Blood +30 s) |
| **mean** | 0.251 | 0.224 | |

Found and fixed by the real-time runs, none visible in replay:

* **Alignments never ran.** They waited for a tick the span scorer left idle,
  but the scorer is due every ASR step, so each request timed out after 30 s.
  They now run, at most one per step, after each scoring pass.
* **The gate saw a different grid.** Replay gates on the whole song's beat
  grid; live has only the audio so far. WABN's grid passed on prefixes
  (missed-beat share 0.03-0.08) but switched from 62 to 120 bpm. A song is now
  learned only once two grids agree on its tempo (within 15%), and dropped,
  back to its library slides, if the tempo later moves. Over growing prefixes
  of the 7 single-song recordings, only WABN and the medley flip, and those are
  the two learning made worse. Replay on one consistent grid confirms WABN is
  worse learned (0.198 -> 0.243).
* **Mixed beat units.** Each learned slide's placement is kept in seconds and
  re-measured on every new grid.
* **Beat This took the GPU.** It ran on MPS beside MLX Whisper, and the set
  dropped 40 chunks with one 8.5 s ASR pass. During a service it now runs on
  the CPU, at `nice 10` and on 2 threads. Runs are spaced to max(30 s, a third
  of the song so far), since each re-tracks the whole song (13 s of CPU at 4
  min, 24 s at 8). Afterwards the set had 0 dropped and 0 unhealthy, with ASR
  pass p90 909 ms against 942 library-only.

Cost per song: about 30-85 forced alignments at 150-230 ms median (p90 < 420)
on the ASR thread, ASR lag p90 rising 0.1-0.2 -> 0.17-0.34 s; 8-20 rebuilds at
3-16 ms; Beat This 5-19 s of wall time per run on the CPU, about 6 runs per
song.

**Recommendation.** A clear win on hymns and verse-repeating songs, and
neutral to negative on contemporary songs and medleys. It needs Beat This,
which is dev-only (a separate torch venv, not in the frozen app; the weights
licence is unchecked). So `in_service_learning` stays **off by default**.
Before shipping it, three things: Beat This's licence; a shippable beat
tracker; and a guard for tag vamps (the remaining loss).

### Why learned slides made things worse: root cause (2026-10-10)

Replayed on the 8 live traces with the chords the live session has (mix BTC;
the earlier 0.189 headline used Demucs-stem chords and does not reproduce on
today's code). `research/spikes/music_only/learned_diff.py` lists every stretch
where one arm is right and the other wrong.

**The approach is sound; the learned material was bad.** Holding the
learning schedule fixed and swapping only what is written into the reference
(`SEGMENTS=`):

| written into the reference | wrong | perceived |
|---|---:|---:|
| the library's own slides (the rebuild alone) | 0.219 | 0.254 |
| what the look-back learned | 0.220 | 0.295 |
| the lead sheet's true material | 0.185 | 0.232 |

Learned chords laid on the library's timing are worse still (0.229 / 0.319):
chord evidence on the wrong cells actively misleads.

**Two defects in the material:**

1. **The look-back can't say "this isn't the song".** It places *every* heard
   word on some lyric word (a pass has as many onsets as words in its window),
   and its confidence compares only this song's slides with each other. So
   talk before a song passes as the song: 60 s of a children's talk before
   Awesome God ("I'm not a man I'm a man") alternated as slides 1 and 2 at
   confidence 0.70-0.82. That junk was learned and then frozen, and every real
   chorus afterwards was followed against it (most of the set's 105 s of
   regressions). The same flaw as the stray-"!" song jump. Fix: a pass's words
   must be the lyric word they are placed on, or a neighbour, at least 20% of
   the time. The talk scores 0%; sung passes score 26%+ (5th percentile).
2. **Collapsed forced alignments.** On short or garbled windows the DTW put
   most words on one instant; the result was learned as a rhythm (lines 0.1-0.5
   of their true length, more than half the words on one cell). Fix: reject a
   learned line with more than 30% of its words on the previous word's cell
   (sound passes: median 0.07).

Refuted on the way: capping a line's length after its last word (0.238 /
0.316). The long lines are real; the band does take that break, and the
sheet just doesn't print it.

| | wrong | perceived |
|---|---:|---:|
| library only | 0.219 | 0.254 |
| learned, as built | 0.220 | 0.295 |
| + collapse check | 0.196 | 0.249 |
| **+ collapse + word-match checks** | **0.181** | **0.231** |
| true sheet material (ceiling for this schedule) | 0.185 | 0.232 |

Per recording, learned vs library (seconds wrong only in one arm):
Reckless Love +26 s net better, King Of My Heart +24, I Surrender All +17,
Since Your Love +32, the set +19 (was -60), WABN and Build My Life unchanged
(not learned), the medley -10.

**What remains: the medley.** Nothing But The Blood's verses 1, 2 and 4 share
a tune and a last line; the band vamps that line on verse 4 for 30-37 s
against a learned 14 s. With learned timing the follower believes verse 4 is
over and moves to another verse ending the same way; library-only, with only
words, stays put. The true-material arm does the same (perceived 0.268), so
this is not material quality but vamps beyond the learned length.

Ported to the live learner (`learn.MIN_LEXICAL`, `learn.MAX_COLLAPSED`). Not
yet re-run in real time.

**Word-match threshold swept** (collapse check on; wrong / perceived / 5x
jump seconds): library 0.219/0.254/24.1; 0.2 0.181/0.231/34.9; 0.3
0.179/0.230/34.9; 0.4 0.187/0.237/34.9; 0.5 0.201/0.250/34.4; 0.6
0.202/0.252/34.4; 0.7 0.209/0.267/40.1; 0.8 0.192/0.225/23.1. From 0.5 on,
Reckless Love loses learned slides and gets worse than library-only (0.357
and 7.7 s of jumps at 0.8). 0.8 only looks good overall because the medley
stops learning. Shipped at 0.3.

**The 5x jumps at 0.3 (34.9 s against library's 24.1):** 15.5 s same-tune
verses confused, including the verse-4 vamp (medley, new with learning);
3.8 s wrong song after the medley returns to its first song (new); 3.1 s
lines two slides share (medley's part new); 11.1 s different lines misheard on
King Of My Heart and Build My Life (the library follower's, reduced from 18 s
by learning on King Of My Heart); 1.0 s scoring artifact (no screen at
t = 0). Tool: research/spikes/music_only/jump_report.py.

### Counted loops (2026-10-10)

`reference.LOOPS`: a section the band repeats is unrolled into counted
passes, and after pass k the band leaves with a count-shaped chance (5% on
odd passes, 30% on 2 and 6, 60% on 4, 85% on 8, all on 12). Marked from the
song's structure: runs of identical slides, and tags that close a slide.
Look-back marks (a slide heard sung twice in a row) measured worse and are
not used.

Replay, 8 traces (wrong / perceived / 5x jumps): library 0.219/0.254/24.1 s ->
0.204/0.216/8.2 s; learned 0.179/0.230/34.9 s -> 0.170/0.209/26.5 s.

Real time, library follower, loops off -> on: King Of My Heart 0.275 ->
0.131 (45 -> 18 slide changes), WABN 0.228 -> 0.212, Build My Life 0.154 ->
0.158, Blood medley 0.229 -> 0.203; mean 0.222 -> 0.176. No dropped chunks;
ASR lag unchanged.

**Shipped on by default (0c3f1ac), with the hidden `counted_loops` tunable
as kill switch.** Real time with lead sheets, loops off -> on: King Of My
Heart 0.097 -> 0.080, WABN 0.158 -> 0.148, Blood medley 0.174 -> 0.151, live
set 0.196 -> 0.186; mean 0.156 -> 0.141. The set's loops-on run dropped 1
chunk, from two single slow Whisper passes (4.3 and 4.9 s, CPU 33-41%).
Loops add no GPU work (the scorer's spans are unchanged), so that was
background GPU contention, not loops. Follower readout cost: median 3.7
-> 4.7 ms, max about 9 ms, every 0.5 s of audio, active song only.
