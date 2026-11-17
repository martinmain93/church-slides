# Is Whisper the right ASR engine?

Spike run 2026-08-21, prompted by the proposal to replace Whisper with a
streaming phoneme recogniser feeding a constrained phone-matching problem
(with a phone confusion matrix for robustness).

The reasoning behind the proposal is sound and worth restating, because the
measurements below do not refute it in principle — they refute it for *this
content on this hardware*. We currently go: audio → Whisper's acoustic model
computes phone posteriors internally → an autoregressive decoder with an
implicit language model collapses them to plausible English → we run Double
Metaphone (an *orthographic* heuristic that discards vowels) to approximate the
phones back. Two lossy steps to recover something the acoustic model already
had. That is a real inefficiency.

## What was measured

**Model:** `facebook/wav2vec2-lv-60-espeak-cv-ft`, 316M params, 392-symbol IPA
inventory, CTC. Run in an isolated venv (`scratchpad/phonespike`) so the
sidecar's shipping dependencies were untouched — note PyTorch is not viable in
the shipped PyInstaller binary anyway; the deployment path would be ONNX, and
`onnxruntime` is already a dependency.

**Test:** 30 s of I Surrender All, real full-mix worship audio (band + vocals),
against the same 30 s through Whisper.

### 1. Phone quality on sung, full-mix audio — poor

```
detected: ɑ t i tʃ i dʒ aː s ɑ aː s aː d t k i m aː f i i t d aː e l t a d t n
          tʃ aː ʃ t i t t i s t ɹ aː s e n s t eː l i i i i d aː s d
truth:    all to jesus i surrender / all to him i freely give
```

There is signal — `ɑ t i tʃ i dʒ aː s` tracks "all to jesus", `t ɹ aː s e n s`
tracks "surrender" — but vowels are wrong throughout and consonants drop. This
is the risk flagged before the spike: every acoustic model is trained on
speech, and sustained pitch-modulated sung vowels over instrumental backing are
out of distribution. It is the same failure class as the speech-trained VAD
that dropped 60 of 349 sung words (why `vad_filter=False`).

### 2. Whisper on identical audio — perfect

| | output | RTF |
|---|---|---|
| wav2vec2 phonemes | (above) | **0.40** |
| whisper base.en | "All to Jesus I surrender all to Him I freely give And I will ever love and trust Him in His presence daily And I surrender all" | **0.04** |
| whisper small.en | (also correct) | 0.13 |

Whisper is **ten times more accurate and ten times faster** here. The latency
argument for CTC rests on incremental emission rather than throughput, but at
RTF 0.40 a rolling window cannot be recomputed fast enough on CPU to realise
it. A much smaller CTC model (wav2vec2-base at 95M, or a streaming conformer)
might; this one cannot.

### 3. Whisper's WER on the goldens — 17-24%, with **zero deletions**

| recording | WER | sub | del | ins |
|---|---|---|---|---|
| Reckless Love | 0.244 | 48 | **0** | 29 |
| I Surrender All | 0.176 | 14 | **0** | 15 |

Zero deletions means every sung word is represented somewhere. The error is
substitutions (largely phonetically close, which our matching absorbs) and
insertions (duplicate finals + hallucination on instrumental passages).

### 4. The finding that settles it: a perfect transcript does not help

`synthesize_schedule` builds a transcript from ground truth — correct words,
exact timings. Over 8 seeds at production settings:

| recording | perfect transcript | real Whisper |
|---|---|---|
| Reckless Love | 0.283 | **0.276** |
| I Surrender All | 0.260 | **0.132** |

A *perfect* transcript performs no better, and on I Surrender All it is twice
as bad. ASR accuracy is not the binding constraint on wrong-slide rate; the
matcher and scheduler are. Replacing the ASR engine cannot fix an error that
does not live there.

(Caveat: the synthetic transcript spreads each slide's words evenly across its
interval, which is idealised but *unrealistic* timing — so this is not a strict
upper bound on ASR quality, it is a different input distribution. The matcher
is now tuned, via `snap_window=5`, for how real ASR timings actually behave.)

## Conclusion

**Keep Whisper.** Not because the phoneme argument is wrong in principle, but
because on this content it loses on every axis measured — accuracy, speed, and
robustness to musical backing — and because the headroom it would compete for
does not exist: a perfect transcript is not better than what we already get.

## What would change this answer

- **Vocal-isolated audio.** The spike conflated "singing" with "band in the
  mix". Phase 9 (Demucs separation) would let those be separated, and a phone
  model on isolated vocals is a materially different test.
- **A smaller / streaming-optimised CTC**, exported to ONNX. The latency case
  needs a model that can be recomputed incrementally in real time; 316M params
  at RTF 0.40 cannot.
- **A singing-tuned acoustic model.** The out-of-distribution problem is the
  core one, and it is a data problem, not an architecture problem.
- **The matcher ceasing to be the bottleneck.** If wrong-slide were driven down
  to where ASR error dominated, this calculus would change.

## The cheaper idea that survives

Replacing Double Metaphone with real grapheme-to-phoneme plus a
confusion-weighted phone edit distance keeps Whisper but removes the
*orthographic* lossy step — Metaphone discards vowels entirely (`HMPL`,
`FRSKN`, `PLSNKS`). That tests whether finer phonetic granularity helps our
matching at all, for a fraction of the effort, and it is the part of the
original proposal least coupled to the acoustic model. Given finding 4, expect
a small effect — but it is cheap enough to be worth knowing.

On the confusion matrix specifically: if a posteriorgram is ever used, prefer
keeping the full probability vector over hard-decoding to a 1-best phone
string. Confusability is then represented implicitly (a /b/ that sounds like
/p/ carries mass on both) and no matrix is needed. A matrix earns its place
only after collapsing to discrete phones, and should be derived from
articulatory features (place, manner, voicing) rather than trained.


---

# Can we get phonemes out of Whisper, and drop what we don't need?

Asked 2026-08-21, after the spike above. Three separate answers.

## 1. There are no phonemes inside Whisper

Whisper is trained end-to-end from audio to BPE **text** tokens. There is no
phoneme layer, no phone inventory, and no phonetic supervision anywhere in it.
Nothing can be "extracted" because nothing labelled is there.

What *is* there is the encoder output, and it is reachable without PyTorch:
`ctranslate2.models.Whisper.encode()` returns

```
(1, 1500, 512) float32   for a 30 s window  ->  20 ms per frame
```

which is exactly the frame-synchronous representation the phoneme proposal
wanted — just unlabelled. Turning it into phones means training a small CTC
head on the frozen encoder against phone-labelled speech. That is a real and
fairly cheap project (the head is 512 -> ~40 outputs, ONNX-exportable, and
`onnxruntime` already ships), but it is training, not extraction.

## 2. Dropping the decoder is worth a lot — on compute and latency

Measured on 30 s of real audio, base.en, int8, CPU:

| | p50 |
|---|---|
| encode only | **339 ms** |
| encode + decode (full transcribe) | **1541 ms** |
| **decoder share** | **78%** |

So the part we least want — the autoregressive language model that turned
"reckless love" into "never ending breakfast of god" — is also 78% of the
cost. Removing it would additionally remove the two things that force our
latency floor: hallucination on non-speech, and output revision near the
buffer edge (the reason `stable_margin_seconds` is 1.5 s).

That is the strongest version of the original argument, and it survives the
earlier spike intact.

## 3. There is a middle path that needs no training: forced scoring

`ctranslate2.models.Whisper.align()` accepts **encoder output plus a given
token sequence** and returns `text_token_probs`. That is a forced-alignment
scorer: it answers "how well does *this* text explain *this* audio" without
generating anything. No free decoding means no hallucination, and it is
exactly the closed-vocabulary formulation — encode once, score every candidate
slide against the same encoding.

Prototyped on I Surrender All: 6 s windows every 8 s, all 13 non-empty slides
scored per window.

| aggregation | picks exact slide | picks the right **words** |
|---|---|---|
| mean log-prob | 36% | **57%** |
| best 6-token run | 36% | 54% |
| sqrt-length normalised | 4% | 25% |
| sum log-prob | 4% | 21% |

("Right words" is the fairer column: slides 2/5/8 are textually identical, as
are 3/6/9/12, so picking 2 when the truth is 5 displays exactly the correct
text.)

**A hypothesis tested and refuted along the way.** Short slides won constantly
(`2:-1.76 5:-1.76 8:-1.76`), which looked like the same length bias that
afflicts the emission model, so mean-log-prob should have been favouring short
sequences. It is not: the aggregations that favour *long* sequences score 4%.
Mean log-prob was already correct, and the errors are genuine acoustic
ambiguity plus the absence of any temporal model.

**That verdict was wrong, and the harness was the reason — corrected below.**


## 4. Correction: the forced scorer is strong, and the first evaluation was broken

The 57% figure above, and the conclusion drawn from it, do not survive scrutiny.
Two separate errors.

**Error 1 — an unfair comparison.** 57% was cold-start classification of a
single isolated 6 s window with no prior whatsoever; the tracker's ~87% is
continuous tracking with history, a transition prior and position state.
Comparing them compares a component to a system.

**Error 2 — the scorer was being asked the wrong question.** Each 6 s window
was scored against a slide's *entire* text. A 24-word verse takes 15-20 s to
sing, so most of that text is simply not in the window. Spans of 10 words at
stride 5 were still too coarse to land on the right offset.

### The scorer is strongly acoustic

Scoring one fixed text, `"all to jesus i surrender"`, against different audio:

| window | mean logP | what is actually in that audio |
|---|---|---|
| **0 s** | **-1.048** | **"All to Jesus I surrender"** |
| 6 s | -6.013 | "To him I freely give" |
| 12 s | -6.475 | "And I will ever love and trust" |
| 60 s | -6.221 | "At his feet I bow" |
| 200 s | -6.297 | "my blessed Savior, I surrender" |
| silence | -7.059 | (zeros) |

A five-nat gap between matching and non-matching audio. An earlier test that
appeared to show "no discrimination between plausible English texts" was
comparing *wrong* text against *wrong* text — the label was mis-assigned,
because ground-truth cues mark slide boundaries and a mid-slide window
contains mid-slide words.

### With spans matched to the window, it also recovers position

6-word spans at stride 2, scored against a 6 s window:

```
t=0s   audio: "All to Jesus I surrender"
  -1.666  slide  1 @w0   all to jesus i surrender all
  -1.941  slide 13 @w0   all to jesus i surrender now     <- confusion group,
  -2.272  slide  7 @w0   all to jesus i surrender make       honestly near-tied
t=12s  audio: "And I will ever love and trust"
  -2.615  slide  1 @w12  will ever love and trust him     <- slide AND position
t=60s  audio: "At his feet I bow"
  -1.972  slide  4 @w6   at his feet i bow worldly        <- 2.7 nat margin
```

Standalone accuracy over 56 windows rises to **43% exact slide / 70% right
words** (from 36%/57%), and the near-ties at the top are the textually
identical slides, where either choice displays the correct words.

### Cost: cheaper than what we do today

| candidates | align | total | vs 1000 ms budget |
|---|---|---|---|
| 4 | 117 ms | **475 ms** | OK |
| 8 | 228 ms | **585 ms** | OK |
| 16 | 438 ms | **795 ms** | OK |
| 32 | 915 ms | 1273 ms | over |
| 57 (whole song) | 2127 ms | 2485 ms | over |

One encoder pass (357 ms) is shared across all candidates; align scales
linearly. **Full transcribe today costs 1541 ms**, so scoring 16 candidates is
roughly half the current cost — and it replaces the phonetic word-matching
work downstream as well.

### Why this is not refuted by "a perfect transcript does not help"

That earlier finding was about *word* evidence quality, and it stands. But the
measured bottleneck was never word accuracy — it was **timing**: real ASR word
timestamps drift several words, which is precisely why `snap_window` had to go
from 1 to 5. Forced alignment returns the position directly rather than
inferring it, so it attacks the failure we actually measured, which better
transcription would not.

### The architecture this implies

Not a replacement for the particle filter — a replacement for its *emission
model*. The filter already maintains exactly the state this needs (slide plus
fractional position) and already proposes a small plausible neighbourhood,
which is what keeps the candidate count inside budget. So:

1. Filter proposes candidates: current slide +/- a couple, plus section starts,
   each at its expected position -> ~8-16 text spans.
2. One encoder pass over the window; batched align over those spans.
3. Score becomes the emission likelihood, and the returned alignment supplies
   the position observation.

That removes the whole audio -> LM-biased text -> Metaphone chain, removes
hallucination, and gives position from alignment instead of drifting word
timestamps.

**Next step:** wire it as an alternative emission source behind a flag and
measure end-to-end wrong-slide against the current 0.132 / 0.276, rather than
judging it by standalone window accuracy again.


---

# ONNX + shared cross-attention K/V + CoreML

Built 2026-08-21 on branch `asr-onnx-scoring`. Goal: find out why scoring the
whole song cost 2 s, and whether hardware acceleration was the answer.

## Where the time was going

CTranslate2's `align()` scales **perfectly linearly** — 4 candidates 122 ms,
57 candidates 1652 ms, i.e. ~29 ms each. That linearity is the tell: the only
way to score N candidates through that API is to hand it N copies of an
identical encoder output, so the cross-attention keys and values are projected
from scratch N times over. The genuinely per-candidate work (self-attention
over ~8 tokens) is trivial by comparison.

So this was not primarily a hardware problem. It was a redundant-computation
problem.

## The fix

Optimum's `--task automatic-speech-recognition-with-past` export produces
exactly the seam needed: `decoder_model.onnx` **emits**
`present.*.encoder.{key,value}`, and `decoder_with_past_model.onnx` **accepts**
them. And its `input_ids` dimension is dynamic, so a whole candidate sequence
can be scored in one teacher-forced pass with an empty self-attention past —
no autoregression.

So per window: encoder once, one decoder call to harvest the cross-attention
K/V, then every candidate reuses those tensors. Tiling already-projected K/V is
a memcpy; recomputing them is six layers of 1500x512x512 matmuls.

`research/dead_ends/acoustic/export_whisper_onnx.py` does the export and int8 quantisation;
`research/spikes/spike_onnx_scorer.py` is the scorer. **Not wired into the sidecar** —
PyTorch is export-only and must never reach the PyInstaller binary; runtime
needs onnxruntime alone.

## Results

Fixed cost per window: encoder 350 ms + **cross-K/V harvest 21 ms**. That 21 ms
replaces CTranslate2 doing the same work at ~29 ms *per candidate*.

| candidates | scoring | total | vs today's 1541 ms transcribe |
|---|---|---|---|
| 8 | 66 ms | **436 ms** | 3.5x |
| 16 | 139 ms | **510 ms** | 3.0x |
| 32 | 277 ms | 648 ms | 2.4x |
| 57 (whole song) | 448 ms | 819 ms | 1.9x |

Accuracy is unchanged at **70% right-words** (exact-slide 45% fp32 / 41% int8
against CTranslate2's 43%) — this is a pure cost win, not a trade.

int8 quantisation shrinks the models 4x (314 MB -> 79 MB for the decoder) and
helps the encoder (456 ms -> 350 ms) but barely touches scoring, which is
dominated by cross-attention *queries* against 1500 encoder frames.

## Two things that did not work

**CoreML is slower.** `CoreMLExecutionProvider` is available on this host, but
the encoder goes 701 ms -> 1271 ms (fp32) and 456 ms -> 978 ms (int8): dynamic
shapes partition badly and the transfers cost more than the compute saves. The
decoders will not run on it at all — scoring passes a zero-length
self-attention past, and the EP rejects any input whose runtime shape has zero
elements. Closed on this stack.

**Truncating encoder frames is still wrong.** Confirmed independently of
CTranslate2's DTW: keeping 300 of 1500 frames drops right-words 70% -> 50% and
saves almost nothing, because the encoder processes the padded 30 s regardless.
Whisper's cross-attention is calibrated over the full context; truncating
renormalises it rather than removing dead weight.

## What this means for the design

The remaining ~14 ms per candidate is cross-attention queries against 1500
encoder frames — irreducible without fewer frames (harmful) or fewer
candidates. Fewer candidates is exactly what the particle filter's prior
provides, and at 8-16 candidates the whole thing runs in 436-510 ms against
the 1541 ms we spend transcribing today.

That is the argument for the emission-model architecture sketched above: it is
not merely competitive on cost, it is roughly 3x cheaper than the current
pipeline while also returning position directly instead of inferring it from
drifting word timestamps.
