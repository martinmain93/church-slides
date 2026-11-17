# Beat, downbeat and chord models: what to use, as of 2026-09

Literature review requested 2026-09-08, after the chord-chart spike found the
detector — not the idea — to be the blocker (`Plan/CHORD_DOWNBEAT.md`). The
question is narrow: **what should replace the hand-rolled numpy front end**,
given this app's constraints.

Numbers marked ✅ were extracted from the papers' own tables by hand. Numbers
marked ⚠️ come from abstracts or search summaries and are not verified against a
results table — one fetched summary quoted a row that turned out not to be the
row it claimed, so the distinction is load-bearing.

## What this app actually needs

| | requirement |
|---|---|
| runtime | onnxruntime only; no PyTorch at inference (`decision_asr_engine`) |
| budget | must sit beside Whisper (~0.31 RTF, or MLX at 1.69x realtime) |
| licence | hard filter; non-commercial model weights are a blocker |
| platform | Apple-silicon acceleration is allowed since 2026-09-05 (`constraint_shipping`) |
| latency | the whole point of a side channel is speed (`goal_side_channel`) |
| task | beats gate chord changes; chords localise position within a line |

---

## 1. Beat and downbeat tracking

### The finding that should shape the design: online downbeats are weak

| system | mode | beat F1 | downbeat F1 | notes |
|---|---|---|---|---|
| **Beat This!** (Foscarin, Schlüter & Widmer, ISMIR 2024) | offline | **89.1** ±0.3 ✅ | **78.3** ±0.4 ✅ | GTZAN; no DBN |
| Hung et al. (SpecTNT), prior SOTA | offline | 88.7 ✅ | 75.6 ✅ | closed source, unreproduced |
| **BEAST** (Chang & Su, 2024) | **online, 46 ms** | 80.0 ⚠️ | **46.8** ⚠️ | +5pp over prior online SOTA |
| BeatNet (Heydari et al., ISMIR 2021) | online | — | — | CRNN + particle filter; the previous online SOTA |

**Downbeat F1 falls from 78 to 47 when you require online operation.** That gap
is the single most important number here, and it argues against running a
downbeat tracker causally at all.

**The way around it, and it matches our own measurement.** Run an
offline-quality tracker on a *trailing* window and extrapolate one or two beats
forward. `spike_beat_horizon.py` measured exactly this regime independently:
forward extrapolation scores 1.42 against a phase-randomised null in the 0-2 s
band (69% of windows above chance) and is at chance beyond it. So a trailing
high-accuracy grid plus ~2 s of projection is the operating point the evidence
supports from both directions — and it is all beat-synchronous chord decoding
needs.

### Candidates

**Beat This!** — MIT ✅, pretrained checkpoints provided (**8.1 MB** for the
small variants, up to 78 MB) ✅, ~20 M parameters ✅, 22.05 kHz mono → 128-bin
mel (30 Hz-10 kHz) at 50 fps, 30 s context ✅. Needs PyTorch 2.0; **no ONNX
export exists** ✅, so exporting is on us — the project already has that pattern
in `research/dead_ends/acoustic/export_whisper_onnx.py`. Removes the DBN entirely, which matters
because the DBN's default 55 BPM floor forces double-tempo on 21% of SMC tracks
(SMC Blind Spot, arXiv 2605.12287, 2026) ⚠️ — and slow worship ballads sit right
at that floor.

**allin1** (Kim & Nam, arXiv 2307.16425) — MIT ⚠️. Jointly predicts beat,
downbeat, tempo **and functional segments**, trained on the Harmonix Set (the
same corpus used in `spike_metre_corpus.py`). Architecturally the closest thing
to what this app wants. **But it consumes Demucs-demixed stems**, so the source
separation dominates the cost, which conflicts with "cheap enough to run beside
Whisper". An Apple-silicon MLX port exists (`all-in-one-mlx`) ⚠️, which is
interesting given the project already ships an MLX Whisper path.

**madmom** — code BSD, **models CC BY-NC-SA**. Still a shipping blocker,
unchanged from the earlier check.

---

## 2. Chord recognition

### Chord models are small — much smaller than the beat models

| model | params | Root | MajMin | MIREX | Sevenths |
|---|---|---|---|---|---|
| **BTC** (Park et al., ISMIR 2019), original weights | 3.03 M | **82.28** ✅ | **79.31** ✅ | 78.71 ✅ | 67.00 ✅ |
| 2E1D student (distilled) | 2.2 M | 77.28 ✅ | — | — | — |

Evaluated on a 120-song set pooled from Isophonics, McGill Billboard, RWC Pop
and USPop (arXiv 2602.19778, 2026). **BTC is MIT** ✅ and 3 M parameters — for
scale, Whisper base.en is ~74 M. Chord recognition is the *cheap* half of this.

Against that, my template matcher has no published number but emitted **331
chord changes where the chart has 48**, so the gap is not marginal.

### Two 2026 papers aimed at exactly our failure mode

- **Event-based sequence modelling with oversegmentation minimisation**
  (arXiv 2604.24386, ICASSP 2026): reformulates chord recognition as
  segment-level autoregressive seq2seq, so **chord changes are only emitted at
  segment boundaries** rather than per frame ⚠️. This is a direct attack on
  over-segmentation, which is our diagnosed blocker.
- **Source separation as preprocessing** (APSIPA 2025): split into
  vocals/bass/drums/other, boost the non-vocal stems, then recognise ⚠️.
  Relevant because worship recordings are vocal-dominant, but it adds the same
  Demucs cost that makes allin1 expensive.
- **Consonance-based training** (arXiv 2509.01588) and **pseudo-labelling /
  distillation** (arXiv 2602.19778) both improve rare chord qualities —
  diminished went 0% → 45.6% ⚠️. Not our bottleneck: worship charts are
  overwhelmingly triads, and our own charts use 5 distinct Nashville degrees.

---

## 3. A risk nobody flagged: our audio is 16 kHz

Every recording in `~/Library/.../recordings` is **16 kHz mono** — chosen for
Whisper, which wants exactly that. But:

- Beat This! expects **22.05 kHz** and builds mel bands up to **10 kHz** ✅.
- 16 kHz audio contains nothing above 8 kHz at all.

Hi-hats and cymbals — the highest-rate and most reliable beat cue in a worship
mix — live largely above 8 kHz. So every beat-tracking number in this project,
including my own negative results, was measured on audio missing the band these
models most rely on. Resampling 16 kHz up to 22.05 kHz satisfies the input
shape but cannot restore the content.

**This is cheap to test and worth testing before concluding anything further
about beat tracking on worship audio**, and it is an argument for capturing new
recordings at a higher sample rate. It may also partly explain why librosa read
75 BPM on a synthetic 112 BPM drum loop and sat near its null on 3 of 7
recordings.

---

## 4. Recommendation

Build order, cheapest and highest-value first:

1. **Swap the chord front end for BTC.** MIT, 3 M parameters, MajMin 79.3
   against a template matcher that over-segments 7x. This is the blocker
   identified in `Plan/CHORD_DOWNBEAT.md` and the smallest model in the review.
   Re-run the cross-match control — it is already written and is the pass/fail.
2. **Check the 16 kHz question** before any further beat work. One resampling
   experiment, and a comparison against any recording captured at a higher rate.
3. **Add Beat This! (small checkpoint, 8 MB) on a trailing window**, projected
   forward ~2 s, for beat-synchronous chord decoding. ONNX export required.
4. **Only then** consider allin1, and only if the Demucs cost can be justified —
   it would replace steps 1 and 3 with one model that also does segmentation,
   but it is by far the most expensive option here.

Note that steps 1 and 3 are independent: BTC is worth doing on its own, and
beat-synchronous decoding is an improvement on top of it, not a prerequisite.

---

## 5. Benchmarked on our own audio (2026-09-08)

Everything below is measured on this machine (Apple silicon, MPS) against the
approved goldens. Scripts in `research/spikes/chord_bench/`.

### Speed: both models are cheap enough to ignore

| model | CPU | MPS | RTF (MPS) |
|---|---|---|---|
| Beat This! (final0, 20 M) | **60x** realtime | **96x** | 0.010 |
| BTC majmin (3 M) | — | **90-203x** | 0.005-0.011 |

For scale, Whisper runs at ~0.31 RTF. **Both models together cost under 10% of
the ASR budget.** The "is it fast enough" question is settled, and it was the
one thing no paper answered.

### The 16 kHz risk was overstated

Same performances, 16 kHz capture against 44.1 kHz originals, durations matching
to under 10 ms, scored with mir_eval's 70 ms F-measure:

| | mean agreement |
|---|---|
| beat | 0.906 |
| **downbeat** | **0.952** |

Downbeats — the thing this design needs — barely move. Two of six recordings
disagree on the metrical *level* (166.7 vs 115.4 BPM; 76.9 vs 150.0), but the
downbeat grid survives that. **I flagged 16 kHz as a likely cause of the earlier
beat-tracking failures; it is not.** Worth revisiting only if beat-level tempo
matters later.

### Is the grid right? Weak but consistent evidence

No beat annotations exist, so two independent checks:

- **Whisper word onsets** concentrate at beat phase above a proper null on
  **5 of 6** recordings (e.g. R 0.336 vs null-95 0.146).
- **Golden slide cues against downbeats**: at chance at every tolerance from
  100 to 500 ms. But the operator's presses scatter -0.49..+0.87 s, and at
  500 ms tolerance the base rate is already 43%, so this test has almost no
  power. It is not evidence against the grid.

A bug worth recording: the first version of this validation built its null by
shifting the whole beat grid by a constant. **Rayleigh R is invariant to a
constant phase shift**, so that null was mathematically identical to the
observation and every row read "at chance" by construction.

### Chord front ends, under one control

Cross-match: every recording against every song's chart, best over 12 rotations.

| front end | correct chart | correct-pair | wrong-pair | gap | chord changes |
|---|---|---|---|---|---|
| template matching (mine) | 1/6 | 0.167 | 0.151 | +0.016 | 516 |
| **BTC frame-level** | 3/6 | 0.417 | 0.285 | **+0.132** | 166 |
| BTC beat-synchronous | 4/6 | 0.434 | 0.301 | +0.133 | 150 |

**BTC does essentially all of the work**: the separation gap improves 8x and
chord changes drop 3x. Swapping the front end is the win.

**Beat-synchronous decoding did not deliver what I predicted.** I called it "the
single next thing to build" and expected it to be the fix. It moves the gap by
+0.001 — noise — and 3/6 to 4/6 on six recordings is also within noise. It
remains architecturally reasonable and may help on the *actual* task (position
within a known section, which this metric does not measure), but on the evidence
here it is a marginal add-on, not the fix.

A caveat on the "chord changes" column: the chart counts (30-74) are the
*unique* progression, while a performance repeats sections several times, so the
true target is higher than the chart number. BTC's 150-166 is plausibly close to
correct rather than still 3x over.

---

## 6. BTC exported and shipped (2026-09-09)

The review recommended BTC first. It is now the chord recogniser, behind an
optional model install.

    scripts/export_btc_onnx.py     PyTorch -> ONNX, with a numerical check
    scripts/setup_chord_model.py   fetch, export in a throwaway venv, install
    music/cqt.py                   librosa's CQT, rebuilt in numpy
    music/btc.py                   ONNX session, streaming feature extraction
    research/eval/check_btc_backend.py   agreement with the reference pipeline

### The export

12.9 MB, opset 17, emitting **logits** rather than argmax indices so the panel
can show the runners-up. Verified in the export script itself against PyTorch:
**max logit difference 2.9e-06, argmax agreement 100%**. An export that
silently differs is worse than one that fails, so the script exits non-zero
rather than reporting a warning.

### The CQT is the actual work

BTC consumes `librosa.cqt(sr=22050, n_bins=144, bins_per_octave=24,
hop=2048)`, and the sidecar ships numpy and onnxruntime and deliberately not
librosa. Rebuilt by Brown & Puckette's method — one complex kernel per bin,
spectra taken once, evaluated as a sparse frequency-domain product.

Two scale corrections were needed, and both are derivable rather than fitted:

| | |
|---|---|
| `1/n_fft` | the convolution theorem's factor — a frequency-domain inner product is N times its value |
| `sqrt(len_k)` | librosa's `scale=True`, compensating longer low-frequency filters |

Uncorrected, the per-bin log ratio against librosa spanned 2.06; corrected it
spans 0.07. That mattered: BTC normalises with statistics baked into its
checkpoint, so a constant offset in the features is not harmless — it moves the
whole input distribution.

| | |
|---|---|
| correlation with librosa | **0.9989** |
| per-frame shape correlation | 0.9973 |

### End to end, against the reference pipeline

`check_btc_backend.py` swaps three things at once — librosa→numpy, PyTorch→ONNX,
offline→online — and measures each:

| | |
|---|---|
| CQT swap (same blocks, same model) | **99.8%** of chord labels identical |
| online (trailing window, no right context) | **86.6%** (89.0% within one frame) |

**The feature front end is faithful; the ~13% loss is the online constraint**,
which is inherent — BTC is bidirectional and the newest frame is the one with
no right-hand context. That is the same effect the literature measures as the
offline/online gap.

### Cost

| | |
|---|---|
| feature extraction (incremental) | 4.3% of one core |
| inference at 4 Hz | 3.4% of one core |
| **total** | **~7.7%**, against Whisper's ~31% |

Inference runs at 4 Hz rather than the panel's 10 Hz because chords change
about once a second; at 10 Hz it cost 8.5% for nothing.

### A bug the tests caught, worth recording

Resampling each 100 ms chunk independently is the obvious streaming
implementation and is wrong: each chunk's output positions stop short of its
final input sample, so every chunk drops a fraction of a sample and inserts a
phase discontinuity. Measured at ~2 samples per chunk, it moved the features by
2.3 normalised units and changed the predicted chord from C to "no chord" —
while the offline path stayed correct. A stateful `StreamResampler` that
carries the fractional phase fixes it. Nothing but an explicit
streaming-equals-batch test would have found this.

## 7. What I did not verify

- BEAST's numbers come from its abstract; I did not extract its results table,
  and I did not check whether an implementation with weights is available.
- allin1's licence and the MLX port come from search summaries, not from the
  repository.
- No inference-speed benchmark exists in any of these papers for CPU or Apple
  silicon. Every runtime claim above is a parameter count, not a measurement.
- None of these models has been run on our audio. That is the next spike, not a
  conclusion of this one.
