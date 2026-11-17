# Baseline numbers

_First populated: 2026-08-19._

Scored on the **2 hand-verified golden recordings** — the only ones whose
ground truth a human has reviewed and approved in the Golden Recording
Editor. The other 14 bootstrapped recordings are machine-estimated and are
deliberately excluded: an unverified timeline would make these numbers look
however the bootstrapper happens to behave.

| Recording | Song | Duration | Cues | Repeat material |
|---|---|---:|---:|---:|
| `rec_20260818-213104_293` | Reckless Love | 333 s | 36 | 55% of the song |
| `rec_20260818-215943_266` | I Surrender All | 272 s | 14 | 19% of the song |

"Repeat material" is the share of playing time spent on a slide that had
already been shown once — the chorus coming round again. It turns out to be
the single most predictive number on this page.

## Verdict

Phase 6 misses its own targets under either measurement, and whether it beats
Phase 5 **depends entirely on whether the transcript is perfect**.

### With a synthetic (perfect) transcript — `run_baseline.py` / `sweep_pf.py`

| Metric | Phase 5 (naive) | Phase 6 (PF, best config) | Phase 6 target | Met? |
|---|---:|---:|---:|:--:|
| median \|anticipation error\| | 2986 ms | **912 ms** | < 500 ms | ✗ |
| mean wrong-slide rate | 0.596 | **0.330** | < 0.05 | ✗ |
| strict A/B improvement over Phase 5 | — | yes, on both | required | ✓ |

### With real ASR — `run_realtime_eval.py`, small.en, production window

Measured after the ASR duplicate-word defect below was fixed:

| Metric | Phase 5 (naive) | Phase 6 (PF) | Δ (PF − naive) | 95% CI of Δ |
|---|---:|---:|---:|---|
| median anticipation error | +2985 ms | **+1380 ms** | −1605 ms | [−2032, −1410] |
| mean wrong-slide rate | **0.608** | 0.930 | **+0.322** | [+0.296, +0.349] |
| total decisions | 253 | 26 | — | — |
| cues matched (of 50) | 34 | 1 | — | — |

For the record, the same run *before* the ASR fix: PF −45 ms / 0.903 / 4 cues
matched, naive +2910 ms / 0.645. **Cleaning up the transcript did not improve
the matcher — it made these numbers slightly worse.** The duplicated words had
been inflating the observed word rate, which pushed the particle filter ahead
of the song; with a clean stream it no longer races, but it now lags (+1380 ms)
and commits to even fewer correct slides. The ASR fix was still necessary — the
defect was real and live — but it does not rescue Phase 6, and the prediction
that it would was wrong.

**The particle filter is more punctual and significantly worse at being on the
right slide** — the wrong-slide regression is well clear of its confidence
interval, so it is real and not noise. The Phase 6 success criterion "strict
A/B improvement over Phase 5" is therefore **not met on real audio**, only on
a perfect transcript.

The decision counts say why: 26 PF decisions against 50 ground-truth cues
across the two songs. The filter marches forward, runs out of song, and stops
emitting entirely. The naive matcher flaps constantly (253 decisions) but
stays in the neighbourhood, which is enough to score better on
time-on-correct-slide.

## Where the remaining error actually is

Not spread evenly — concentrated almost entirely in repeated sections:

| Recording | Repeat material | PF wrong-slide rate | PF decisions | True cues |
|---|---:|---:|---:|---:|
| Reckless Love | 55% | 0.493 | 15 | 36 |
| I Surrender All | 19% | 0.167 | 13 | 14 |

On the song that barely repeats, the filter is close to right: 13 decisions
against 14 cues. On the song that repeats heavily it fires 15 times against
36 cues and is wrong half the time — it tracks the song once, straight
through, and never goes back for the second chorus or the fourth pass of the
bridge.

That is a structural limit, not a tuning problem. The tuning was swept:
`rate_noise_std` was extended past the original grid to 0.35 / 0.5 / 0.8 and
got monotonically worse (and saturated), confirming 0.2 as an interior
optimum rather than an edge artefact.

**This is exactly Phase 7's scope** — "chorus repeats, jumps to bridge/tag"
plus the confidence monitor and staged widening. The measurement says Phase 6
has reached its ceiling and the next phase is aimed at the right thing.

## A defect to fix before any of that: the ASR emits duplicated words

The production ASR path (`SlidingWindowWrapper`, 6 s window / 1 s step /
1.5 s stable margin) emits the same word repeatedly across window steps. Its
*final* stream reads "Before Before I spoke a a word … over over me me".

| Recording | Sliding-window ASR | Immediate repeats | Plain ASR, same audio | Inflation |
|---|---:|---:|---:|---:|
| Reckless Love | 447 words | 86 (19%) | 344 words | 1.30× |
| I Surrender All | 274 words | 82 (30%) | 180 words | 1.52× |

"Plain ASR" is faster-whisper over the same file in one pass — the same model,
without the sliding window — so the duplication comes from the windowing, not
from the acoustics.

This matters far beyond tidiness: **the particle filter advances position in
proportion to observed words**, so a 1.3–1.5× inflated word stream makes it
race ahead of the song. That is precisely the observed behaviour — on I
Surrender All the PF reaches slide 5 at t=32 s where the truth is t=75 s —
and once it has overrun the end it goes quiet. The wrong-slide regression
above is at least partly an ASR defect being blamed on the tracker.

Note the shape of this bug: the same class was fixed once already at a higher
level (the server used to feed *partial* transcript events into the matcher,
double-counting words — see `_make_transcript_handler`). This is that bug one
layer down, inside the window wrapper's own finals.

**Fixed 2026-08-19** in `asr/streaming.py`. Immediate-repeat rate on the two
verified recordings went 19.2% -> 1.7% (single-pass baseline 1.5%) and
29.9% -> 4.7% (baseline 0.6%). The remaining gap on the second recording is a
*different* defect, characterised below.

### Still open: the recogniser hallucinates over instrumental passages

Over the instrumental outro of I Surrender All, Whisper invents "you" — and
"thanks for watching!", its well-known YouTube artefact. Transcribing the file
in one pass yields exactly one such token; the sliding window passes over that
region ~30 times and hallucinates afresh on most of them, at non-monotonic
timestamps (253.0, 251.4, 252.3, 254.5 ...). They are genuinely distinct
tokens, so dedup correctly declines to merge them — widening the dedup gap to
swallow them was measured and eats real sung repeats instead ("so so good to
me").

This is a recogniser defect amplified ~30x by windowing, and it belongs
upstream: faster-whisper's `no_speech_threshold` /
`hallucination_silence_threshold`, or gating windows on vocal presence. Note
that the obvious lever — VAD — is the one already turned off because Silero
discards sung audio, so this needs care rather than a flag flip.

Worth noting: the bootstrapper hit this identical wall and got past it. Its
single-pass aligner also walked the slide list once and lost every repeat;
switching to a Viterbi decode over `(slide, dwell)` — where revisiting a
slide is just another transition — took mean F1 from 0.75 to 0.91 against
these same two recordings. The particle filter needs the same capability by a
different route.

## Winning Phase 6 config

Ships as the v1 default until Phase 7 replaces it:

- `n_particles = 500`
- `lead_time_ms = 300`
- `rate_noise_std = 0.2`

Larger particle counts made no material difference (1000 particles: 0.336 vs
0.330), so 500 is chosen for latency headroom.

## Caveats — read before quoting these numbers

- **n = 2.** Two recordings, one repeat-heavy and one not. Enough to expose a
  structural failure, nowhere near enough for a confidence interval. Approve
  more goldens in the editor and re-run.
- **Studio recordings, not a congregation.** These are album tracks: clean
  vocals, no room, no crowd. Real service audio will be harder. Treat every
  number here as an optimistic bound.
- **`run_baseline.py` and `sweep_pf.py` synthesise the transcript from the
  ground truth** rather than running ASR, so they measure the *matcher* in
  isolation with a perfect transcript. Real-ASR numbers live in
  `REALTIME_EVAL.md`, and will be worse.
- **The ground truth got denser.** These cue lists include repeats that the
  earlier machine-only annotations missed entirely, so the scores are
  stricter than any previously recorded. Lower numbers here do not mean the
  matcher regressed.
- `recovery_time_ms` is unpopulated: it needs hand-annotated
  `DeviationMarker`s, which no recording carries yet. It becomes meaningful
  during Phase 7.

## Reproducing

```bash
# Build a directory of only the human-approved recordings, then:
uv run --project sidecar python research/dead_ends/naive_matcher/run_baseline.py --recordings <verified-dir>
uv run --project sidecar python research/dead_ends/particle_filter/sweep_pf.py --recordings <verified-dir>
uv run --project sidecar python research/eval/run_realtime_eval.py --recordings <verified-dir> --model small.en
```

All three route through `eval.replay.load_recording`, which overlays
`golden.json` when present — so they score against the human-approved
timeline automatically.

## Reading the numbers

Metrics defined in `eval/metrics.py`:

| Metric | Direction | Notes |
|---|---|---|
| `anticipation_error_ms` (signed median) | closer to 0 is better | Negative = early; positive = late. |
| `wrong_slide_rate` (mean) | lower is better | Fraction of time the displayed slide ≠ ground truth. Defined as 0.0 for recordings with < 2 ground-truth events. |
| `recovery_time_ms` (median) | lower is better | Per `DeviationMarker`: ms from the deviation to the next correct commit. |
| `override_rate` (per song) | lower is better | Operator overrides per song. |
