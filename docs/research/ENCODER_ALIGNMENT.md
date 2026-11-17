# Encoder-alignment emission: what the project actually is

Mapped 2026-08-29, after the production-harness correction in
`docs/research/LITERATURE.md` reopened the question.

## The idea in one paragraph

Today: audio → Whisper encoder computes acoustic posteriors → an autoregressive
decoder with an implicit language model collapses them into plausible English →
Double Metaphone (an *orthographic* heuristic that throws away vowels)
approximates the phones back → the tracker matches those against slide text.
Two lossy conversions to recover something the acoustic model already had, plus
a language model whose prior is fluent prose rather than the specific 8-26 words
on the next slide.

The proposal: keep the encoder, drop the decoder's freedom. Each window, ask the
decoder to score *the handful of text spans the tracker thinks we might be on*,
teacher-forced. The answer is a likelihood per candidate — which is exactly what
the tracker's emission model wants — with no hallucination, no LM prior pulling
toward prose, and no Metaphone round trip.

**This is not a replacement for the ASR engine, and not a replacement for the
tracker. It replaces the tracker's emission model.** The forward filter already
maintains (slide, word, dwell) and already knows a small plausible
neighbourhood; that prior is what keeps the candidate count inside budget.

## What is already settled

**The phoneme-CTC route is closed** (spiked 2026-08-21).
`wav2vec2-lv-60-espeak-cv-ft` on 30 s of real full-mix worship audio produced
vowels that were wrong throughout and dropped consonants, at RTF 0.40 against
Whisper base.en's 0.04 — **ten times less accurate and ten times slower**. Sung,
pitch-modulated vowels over instrumental backing are out of distribution for
speech-trained acoustic models. The same failure class as the speech-trained VAD
that dropped 60 of 349 sung words. Reopening this needs a *different, much
smaller* streaming CTC model, not a retry of that one.

**The encoder-scoring route runs.** `research/dead_ends/acoustic/export_whisper_onnx.py` exports
and int8-quantises; `research/spikes/spike_onnx_scorer.py` scores. The key structural
win is real and measured: Optimum's `-with-past` export lets the
cross-attention K/V be harvested **once per window** (21 ms) instead of
recomputed per candidate (CTranslate2 does the latter at ~29 ms each, which is
why scoring the whole song cost 2 s). Per-candidate cost falls to ~14 ms.

**Two dead ends, confirmed.** CoreML is slower on this stack (encoder 456 →
978 ms int8; the decoders will not run on it at all). Truncating encoder frames
is harmful (1500 → 300 frames drops right-words 70% → 50% and saves almost
nothing, because the encoder processes the padded 30 s regardless).

## What is not built

Four pieces, roughly in order of risk.

**1. Candidate proposal.** A function from the posterior to ~8-16 text spans.
Nothing like this exists. It is what keeps cost in budget, and it is also the
seam where the library-scale ambition eventually plugs in. Needs to cover: the
current slide at its expected position, its immediate neighbours, and the
section starts a band actually jumps to.

**2. The emission interface changes shape.** `ForwardFilter` is
*word-synchronous* — `_step(word)` per observed word, states carrying a word
index. Span scoring is *window-synchronous*: one score per candidate per
window. These do not compose without real work on the recursion. This is the
largest single piece of engineering in the project and it touches the component
we have the most measured confidence in.

**3. Position is not actually available yet.** The spike returns mean
`log P(token | prefix, audio)` — a likelihood, not an alignment. Getting a
position observation means extracting cross-attention weights and running DTW
over them (what CTranslate2's `align()` does internally). That is additional
code *and* additional per-candidate cost that is not in any measurement quoted
above. **"Position comes directly from alignment" is currently a claim about
work nobody has done.**

**4. Runtime packaging.** PyTorch is export-only and must never reach the
PyInstaller binary; runtime is onnxruntime alone (already a dependency). But
model artifacts currently arrive through `ModelManager` from the HF cache, and
ONNX exports do not. Either ship them, or convert on device, or host them —
each with its own size, licensing and first-run-latency consequences.

## The business case just got worse, and this is the honest part

The "3.0-3.5x cheaper" figure compares 436-510 ms of scoring against
"1541 ms full transcribe today". Today changed. Sweeping the ASR knobs on real
golden audio (2026-08-29, base.en, 6 s window, int8, M1):

| config | p50 pass | emit lag p50 | WER |
|---|---|---|---|
| beam 5 (what shipped) | 737 ms | 2.06 s | 0.357 |
| **beam 1** | **649 ms** | 2.16 s | **0.220** |
| beam 1 + 8 threads | 1131 ms | 2.79 s | 0.439 |
| tiny.en beam 1 | 428 ms | 2.10 s | 0.354 |
| small.en beam 1 | 2499 ms | 3.48 s | 0.207 |
| beam 1, margin 0.5 | 807 ms | **1.47 s** | 0.300 |
| beam 1, margin 0.0 | 847 ms | 0.88 s | 0.478 |
| beam 1, window 4 | 699 ms | 2.22 s | 0.492 |
| beam 1, window 3, margin 0.5 | 650 ms | 1.04 s | 0.423 |
| beam 1, step 0.5 | 690 ms | 2.21 s | 0.316 |

Three things the full sweep settles. **The 6 s window stays** — every attempt to
shrink it costs 0.10-0.27 of WER, because Whisper's accuracy depends on context
it no longer has. **Never set `cpu_threads=8`** on this M1: oversubscribing 4
performance and 4 efficiency cores made passes 74% slower *and* less accurate.
And **`stable_margin` is a genuine dial rather than a free win** — 1.5 s -> 0.5 s
buys 0.69 s of lag for 0.08 of WER, which is a trade worth making deliberately,
unlike beam size which costs nothing.

**Greedy decoding is both faster and substantially more accurate than beam 5.**
Beam search optimises for fluent English, which is the wrong prior for sung
lyrics — it is a free win available today, and it moves the baseline the
alignment project has to beat.

Two further cautions on that comparison. The 1541 ms baseline in the ONNX
write-up does not reconcile with 737 ms measured here for the nominally same
configuration, so the multiple is unreliable in both directions until re-run
head to head. And the alignment path's fixed cost is encoder 350 ms + harvest
21 ms *before any candidate is scored*, against 649 ms for a full greedy
transcribe. At 8-16 candidates the two are close to parity, not 3x apart.

**So the case for this project is no longer cost.** It rests on three
qualitative claims, none of which has been measured end to end:

- The likelihood discriminates *between similar spans* better than Metaphone
  word-matching does.
- No hallucination on instrumental passages.
- Position from alignment beats position inferred from drifting word
  timestamps (`snap_window` had to go 1 → 5 because of that drift).

## The risk that would kill it

**Forced scoring always returns a score.** Hand it five verses that share
"all to Jesus I surrender" and it will happily score all five, and the question
is whether the likelihood *separates* them. If shared-opening spans score within
noise of each other, the approach has replaced a matcher that knows it is
uncertain with one that is confidently arbitrary — strictly worse. `index/confusion.py`
already measures how common this is: **142 of 311 songs (46%) contain slides
sharing their first three words**, and 530 of 2828 slides sit in such a group.

Secondary: standalone window accuracy is the metric that has misled this project
before. The spike's "45% exact-slide" is precisely that kind of number, and the
last time a standalone comparison drove a decision it was wrong because it
ignored temporal continuity.

## The gate: one cheap experiment before any of the above

All four workstreams are expensive and all are wasted if the likelihood does
not discriminate. That is testable **today**, with the code that already
exists and no integration at all:

> Take the approved goldens. For each window, take the ground-truth slide plus
> its confusion-group siblings plus a few neighbours. Score them with
> `spike_onnx_scorer`. Ask: how often is the true span top-1, and what is the
> margin between it and the best confusable sibling?

Compare that margin against what the current Metaphone emission produces on the
same windows. Three outcomes:

- **Clean separation, better than Metaphone** → the project is justified, and
  the sequencing below is the plan.
- **Separation only on non-confusable candidates** → the interesting half of
  the problem is untouched; not worth the rebuild.
- **No separation** → closed, for the same reason the phoneme route closed.

This is perhaps a day of work and it gates roughly three weeks.

## Sequencing, if the gate passes

1. Candidate proposal from the posterior — standalone, testable, cheap.
2. Wire scoring as an *alternative emission source behind a flag*, keeping the
   word-synchronous recursion and using the span score as an additional term
   rather than a replacement. Measure on `run_realtime_eval.py` — the
   production harness, not the cached one.
3. Only if that shows a margin: rework the recursion to window-synchronous.
4. Cross-attention DTW for position, measured against `snap_window=5` drift.
5. Packaging, last — it is pure engineering with no unknowns, and doing it
   earlier means shipping weight for a feature that might not land.

## Do this first regardless

`beam_size=1` is a measured win on both axes and is a one-line change
independent of everything above. It should not wait for this project.

---

# Post-script: what the beam-1 eval actually showed (2026-08-29)

`beam_size=1` shipped. End-to-end on the production harness, six goldens:

| | wrong-slide | anticipation |
|---|---|---|
| tiny.en beam 5 (before) | 0.641 | +1443 ms |
| tiny.en beam 1 | 0.618 | +1393 ms |
| **base.en beam 1** | **0.603** | **+893 ms** |

It is an improvement and it was free, but **the size is the finding**: a 38%
relative cut in WER (0.357 -> 0.220) bought a 3.6% relative cut in wrong-slide.
That is evidence *against* the reframing in `docs/research/LITERATURE.md` that word
quality is the binding constraint. Three follow-ups pinned down why.

**It is not lateness.** Shifting every decision earlier by a fixed amount and
re-scoring recovers almost nothing (0.603 at 0 s, best case 0.596 at 1 s, worse
beyond). Correct-but-late decisions would have been recovered by the shift.
These decisions are wrong, not late.

**It is not the startup convention.** Time before the first decision, which the
metric counts as wrong, is 0.6% of the scored span.

**Identical slides are real but small.** Worship songs repeat a chorus as
several textually identical slides — King of My Heart has slides 3==4 and
7==8==9==10 — so showing slide 10 while the truth says 7 puts exactly the right
words on screen and is scored as an error anyway. `IndexedItem.identical_by_slide`
and `display_marginal` already model this on the tracker side; the *metric*
never got the same treatment. Scoring by displayed text instead of slide index:
0.603 -> **0.556**. Worth fixing, not the explanation.

## The actual finding: the tracker under-commits by 4x

| recording | truth transitions | forward decisions | naive decisions |
|---|---|---|---|
| 213104_293 | 36 | 25 | 76 |
| 213617_974 | 14 | **4** | 30 |
| 213825_312 | 23 | 10 | 42 |
| 214054_974 | 18 | 21 | 84 |
| 214759_057 | 47 | 38 | 140 |
| 215943_266 | 14 | 8 | 72 |
| **total** | **152** | **106** | **444** |

**0.70 decisions per real transition.** On King of My Heart it made four
decisions against fourteen transitions across 254 seconds — the screen simply
sat still. That is not a matcher choosing the wrong slide; it is a matcher that
will not commit, and it explains the live report far better than
misidentification does: the app is "generally on the right slide" because it
moves rarely and the song comes back around to it.

This reframes the priority list again. Ranked by evidence:

1. **Why the scheduler will not commit.** Suspects, in order: the
   `lead_time_ms` 500 -> 1500 change (measured +0.005 on the *cached* harness,
   never re-measured live), `marginal_threshold=0.4` against a pooled marginal,
   and the `order_is_predictive` path. All cheap to A/B on the live harness.
2. **Score by displayed text, not slide index.** A measurement fix worth 0.047
   that currently mis-ranks every experiment touching repeated sections.
3. Latency work — real, arithmetic, and bounded (see above).
4. The encoder-alignment project, which was pitched at an emission model that
   is not obviously the thing failing.

**The gate in this document still stands, but it is no longer the top of the
queue.** Item 1 is a day at most and may recover more than any of them.
