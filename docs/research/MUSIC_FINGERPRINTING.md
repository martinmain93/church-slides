# Musical fingerprinting as a parallel detection stream

Spiked 2026-09-01, against the two facets asked for: (1) with a mapped track,
can the music give good switch *timings*, and (2) without one, can we build the
map from moments where ASR is confident, trusting the music only once
established.

All numbers below are measured on the six approved goldens — real full-mix
worship audio, 35 minutes — with chroma computed in numpy, no new dependency.
Note `auto_presenter/acoustic/` is already taken by Whisper-encoder scoring,
which is lyric-based despite the name; this is unrelated.

## Why it is worth looking at now

The ASR path has a floor that today's work established and could not move:

- Evidence arrives late by construction: `stable_margin` + step + pass time.
- Anticipation from lyrics does not work. Believed position is *anti-correlated*
  with an imminent transition (median 0.31 before a move against 0.56
  mid-slide), and a tail-word detector runs at 41% recall for ~1.1 spurious
  advances per minute. See docs/research/LIVE_LATENCY.md.

So the interesting question is not "make ASR faster" but "is there a second
signal whose errors are uncorrelated". Harmony is a good candidate because it is
continuous — there is no waiting for a word to finish.

## What the measurements say

### Cost: negligible

| | realtime factor |
|---|---|
| chroma + novelty (this) | **0.0015** |
| Whisper base.en | ~0.31 |

3.2 s of CPU for 2093 s of audio. It can run alongside ASR without competing for
the cores that keep passes inside budget — which, after the sidecar-leak
episode, is a property worth insisting on.

### Timing: RETRACTED — this was chance (see the correction dated 2026-09-01)

Blind novelty peak-picking (cosine distance between mean chroma either side of
each frame, 2 s half-window), keeping as many peaks as there are real
transitions:

| | |
|---|---|
| transitions with a peak within 1.5 s | **24%** |
| timing error when matched | median **+0.04 s**, \|median\| 0.59 s |

**This was wrong, and the error was the absence of a null model.** Random peak
placement scores 20% under the same protocol and evenly-spaced peaks score 27%,
so 24% is chance. The unbiased +0.04 s was an artifact too: matching each
transition to the *nearest* peak within +/-1.5 s produces a roughly symmetric
error distribution by construction, whatever the peaks are. See the correction
at the end of this document. The identity results below are unaffected — those
were measured against same-section vs different-section pairs throughout, which
is the control the boundary work lacked.

### Identity: the progression carries the signal, the average does not

Comparing sections that recur within one performance against sections that do
not, using mean chroma over the section:

| | median cosine similarity |
|---|---|
| same section, sung again | 0.986 |
| different sections | 0.971 |

Useless — everything in a one-key song looks alike, and on one recording the gap
was *negative*. Worship songs reuse the same four chords throughout, so average
pitch content cannot separate a verse from a chorus.

Subtracting each segment's own mean, so what is compared is the *deviation over
time* — the progression rather than the key — and resampling to a fixed length
so different-length performances compare:

| | median similarity |
|---|---|
| same section, sung again | **+0.258** |
| different sections | +0.042 |

| threshold | matches a true repeat | matches a different section |
|---|---|---|
| 0.1 | 72% | 35% |
| **0.2** | **62%** | **17%** |
| 0.3 | 41% | 9% |
| 0.4 | 22% | 4% |

Real but moderate, from the crudest possible implementation: 12-bin chroma off a
plain FFT, no constant-Q, no tuning correction, no harmonic/percussive
separation, fixed-length resampling instead of DTW, and no key rotation. There
is a lot of headroom in the method before the idea itself is in question.

## Key and tempo invariance, measured

Requested explicitly: key-agnostic, tempo within roughly +/-30%.

| condition | gap | false positives at 62% recall |
|---|---|---|
| baseline (aligned) | +0.216 | 19% |
| **tempo +30%** (true stretch) | **+0.215** | 18% |
| **tempo -30%** (true stretch) | **+0.217** | 17% |
| key-agnostic (best of 12 rotations) | +0.115 | 28% |
| key-agnostic + tempo +/-30% | +0.114 | 29% |
| boundaries off by 1 s | +0.021 | 55% |
| boundaries off by 2 s | **-0.029** | 74% |

**Tempo invariance is free**, and not by luck: resampling each segment to a fixed
24 frames means the same musical content is the same vector whether it took six
seconds or nine. +/-30% is comfortably inside that, and so would be considerably
more. (An earlier version of this test appeared to show tempo mattering; it was
clamping the stretch to a no-op in one direction and truncating the segment in
the other, so it measured neither tempo nor anything else.)

**Key invariance costs about half the separation.** Matching over all 12
transpositions keeps same-section similarity intact (+0.258 -> +0.262) while
lifting different-section similarity sharply (+0.042 -> +0.147), because every
wrong section also gets twelve chances. Worth paying for cross-performance
matching; not worth paying within a single service, where the key is fixed.

### Boundary error is the real sensitivity, and it reshapes the design

One second of misalignment takes the gap from +0.216 to +0.021. Two seconds
makes it negative — worse than chance. This follows from what the fingerprint
is: a time-ordered progression, sampled at ~31 frames per second, compressed to
24 frames. Shift the window and the progression no longer lines up.

**So a fingerprint cannot be used to find a boundary, only to confirm one.**
Computing it requires knowing where the segment starts and ends, which is the
thing a detector would be trying to discover. That circularity is the single
most important consequence of these measurements, and it separates the two
facets sharply:

- **Facet 2 has boundaries already.** The word tracker supplies them at exactly
  the moments it is confident, which is precisely when bootstrapping should
  record a fingerprint. The design works *with* the grain of this constraint.
- **Facet 1 does not.** A stored map tells you what the sections are, not where
  you currently are inside one, so blind matching is misaligned by construction.
  The way out is to search over alignments rather than assume one — try the
  fingerprint at a range of offsets and take the best. At 0.0015 realtime factor
  there is room: twenty candidate alignments is still ~0.03 RTF, an order of
  magnitude below Whisper. **Not yet measured**, and it is the first thing to
  measure for facet 1.

## The risk that decides this

**The songs that most need help are the ones where this works worst.** Of the
six goldens, five show a clear positive gap and one is *negative*: King of My
Heart — the song with four identical choruses, the one whose repeated structure
has caused the most trouble for the word-based tracker all along. Harmonic
repetition and lyrical repetition co-occur, because they are the same musical
decision. A second signal that fails on exactly the cases the first one fails on
is worth much less than its average numbers suggest.

### Measured 2026-09-01: they do not fail together

Two framings, both over the six goldens with the word tracker replayed on the
idle transcript cache.

*Proxy* — harmonic confusability (max similarity to a different section of the
same song) against the fraction of each interval the wrong text was displayed,
146 intervals: **Spearman -0.189**. Intervals the words handled well had
confusability +0.385; intervals the words failed had **+0.285**. Slightly
*better* harmonic separation exactly where the words struggled.

*Direct* — for the 118 sections that actually recur, `margin = similarity to own
repeat - best similarity to a different section`, so positive means the
fingerprint would identify it correctly:

| | Spearman vs word difficulty | fingerprint identifies correctly |
|---|---|---|
| all recurring sections | **+0.011** | 44% |
| where the words did well (n=59) | | 41% |
| where the words failed (n=36) | | **50%** |

**The correlation is essentially zero.** The worry — that harmonic and lyrical
repetition are the same musical decision, so a second signal would fail on
exactly the cases the first one fails on — is not supported. King of My Heart,
the recording that prompted the worry, sits mid-table on word difficulty (0.31),
so its negative fingerprint gap is not part of a general pattern.

**But the same measurement caps what fusion can be.** A fingerprint identifies
the right recurring section only 44% of the time, with a median margin slightly
*below* zero. Independent it may be; strong it is not. It cannot carry identity.

Taken with the timing result — unbiased to +0.04 s when a boundary is detected,
but only 24% detected — the division of labour the evidence actually supports
is narrow and specific:

> **Words decide *which* slide. Harmony is allowed to adjust *when*, and only
> when its own margin is high.**

Anything more ambitious than that is not yet supported by measurement.

## The two facets, answered as far as the evidence goes

### 1. With a mapped track, can we get good switch timings?

Partly, and the failure is recall rather than precision. Blind peak-picking
finds 24% of boundaries but times them to ±0.6 s unbiased. With a *mapped* track
the problem changes shape: it is no longer peak-picking but alignment against a
known sequence, where the expected next boundary is known and the detector only
has to confirm it. That should do considerably better than 24%, and it is the
obvious next experiment — **not yet run**, so the honest claim is only that the
timing precision is there when the boundary is detected at all.

### 2. Without one, can we bootstrap from ASR-confident moments?

The measurements support this more directly than facet 1, because the separation
above was measured **within a single performance** — same key, same tempo, same
band, same room. That is exactly the setting bootstrapping operates in, and it
sidesteps the hardest part of the problem (cross-performance invariance) for the
first version.

The shape that follows from the numbers:

1. Run chroma continuously. It is free.
2. When the word tracker is confident (`display_marginal` high) and stable on a
   slide, record the chroma segment for that slide.
3. On a later section, compare against stored segments. At threshold 0.2 that
   is 62% recall for 17% false positives — **not good enough to drive a
   commit alone**, but potentially good enough to *confirm* a commit the word
   tracker is already leaning toward, or to supply the timing for one.
4. Only trust it to act alone once a song has accumulated several confident
   observations of the same section that agree with each other.

Point 3 is the one to design around. Given a 17% false-positive rate, the music
stream should adjust *when* to fire a decision the words already justify, rather
than *what* to fire. That also plays to the measured strengths exactly: use
harmony for timing, words for identity.

## What has not been tested

- Alignment against a known map (facet 1 properly).
- Whether fingerprint failures correlate with word-tracker failures — the risk
  above, and the thing most likely to kill this.
- Alignment search for facet 1 — matching at a range of offsets rather than
  assuming the boundary, now that misalignment is known to be fatal.
- Cross-performance matching. Tempo is already handled; key costs half the
  separation. Deliberately out of scope for a first version, since within-service
  bootstrapping needs neither.
- Vocals-only audio. Every number here is full-mix. A vocals-only run like
  `rec_20260831-205404_616` has far less harmonic content and may behave quite
  differently.


---

# Candidates 1 and 2, measured (2026-09-01)

Reproducible via `research/spikes/spike_chroma_fingerprint.py --verify --matrix`.
Each stage was traced against its source before any accuracy number was read
off it, because a feature that silently degenerates still produces
plausible-looking similarity scores.

## Verification first

**CENS** (Müller, Kurth & Clausen 2005): L1 rows sum to 1.000; quantisation
produces exactly the levels [0,1,2,3,4]; smoothing cuts frame-to-frame change to
**0.11x**; and 4x louder audio moves the output by **0.000000**, since L1
normalisation removes loudness by construction.

One check failed on the first attempt and the fault was the check: it compared
downsampled CENS (frames 10 apart) against raw chroma (frames 1 apart) and
concluded smoothing had *increased* variation. Measured at matched rates it is
0.11x, as expected.

**2DFTM** (Bertin-Mahieux & Ellis 2012): transposition by 1, 3 and 7 semitones
all score **1.000000** — exact, because twelve semitones is a genuine circle.
Circular time shifts also score 1.000000. Real time shifts, which pull in new
content and so are only approximately covered, hold at **0.9929** (1 s) and
**0.9873** (2 s) where an untransformed patch falls to +0.298 and -0.033.

## The measurements

AUC over same-section against different-section pairs, six approved goldens.
Misalignment is applied to **one side only** — shifting both preserves their
relative alignment and measures nothing, which an earlier version of this
experiment did, producing a spurious immunity to boundary error.

| front end | descriptor | @0.0s | @0.5s | @1.0s | @2.0s |
|---|---|---|---|---|---|
| raw chroma | patch | 0.749 | 0.625 | 0.527 | 0.427 |
| raw chroma | 2DFTM | 0.794 | 0.794 | 0.787 | **0.770** |
| CENS | patch | 0.787 | 0.708 | 0.574 | 0.372 |
| **CENS** | **2DFTM** | **0.835** | 0.818 | 0.800 | **0.797** |

Both changes earn their place and they compose. CENS buys quality (+0.038
aligned) and about half a second of alignment tolerance. 2DFTM buys quality too
(+0.045) *and* removes the boundary sensitivity almost entirely.

**This overturns the conclusion recorded above.** The earlier finding — that a
fingerprint can confirm a boundary but never find one, because computing it
requires knowing where the section starts — was a property of the *descriptor*,
not of the idea. With 2DFTM, a two-second boundary error costs 0.038 of AUC
rather than dropping below chance. Facet 1 is no longer circular.

A note on reading these numbers: the raw same-minus-different gap makes 2DFTM
look terrible (+0.022 against CENS's +0.409) because its descriptors are
non-negative magnitudes whose cosines bunch near 1.0. AUC is rank-based and
avoids that trap. The gap is the wrong statistic for comparing descriptors with
different similarity ranges, and it would have led to discarding the better one.

## Still open

- Candidate 4, proper Foote checkerboard novelty, against the weak 24% boundary
  recall. Not yet run.
- Musically-annotated boundaries, still a prerequisite for any timing claim,
  since ground truth is operator key-presses.
- Vocals-only audio: every number here is full-mix.


---

# Candidate 4, and a retraction (2026-09-01)

## Foote novelty adds nothing, and neither did what preceded it

Proper Foote novelty — a Gaussian-tapered checkerboard kernel correlated along
the diagonal of a self-similarity matrix — implemented and verified. The kernel
sums to zero, its diagonal quadrants are positive and off-diagonal negative, the
taper puts corner weight at 0.42x the centre, and on a synthetic sequence with a
chord change planted at frame 100 the novelty peaks at **exactly frame 100**. The
implementation is right.

On real audio it makes no difference:

| method | recall |
|---|---|
| two-window on raw chroma (the original 24%) | 22% |
| two-window on CENS | 24% |
| Foote checkerboard, 2-8 s window, raw or CENS | 22-26% |

**Then the control that should have been run first:**

| detector | recall |
|---|---|
| uniformly random peaks | **20%** |
| evenly spaced peaks | **27%** |
| Foote novelty on CENS | 24% |

Given as many peaks as there are real transitions and a 1.5 s tolerance, random
placement already covers a fifth of the timeline. The audio-based detector sits
*between* the two null models and **below** the one that uses no audio at all,
only the assumption that sections are roughly regular in length.

## What this retracts

The earlier claim that harmony "knows *when* to sub-second accuracy and
unbiased, but only sometimes", and the complementarity argument built on it, are
withdrawn. Both the 24% recall and the +0.04 s median error were artifacts:

- 24% is chance under this protocol.
- The unbiased error is guaranteed by the scoring, not by the detector. Matching
  each transition to the *nearest* peak within a symmetric window produces a
  symmetric error distribution however the peaks were chosen.

The correct summary is that **we have no evidence harmony can time a slide
change**, and the "words decide which, harmony decides when" division of labour
has lost its second half.

## What survives

The identity work. Every fingerprint number was measured as same-section against
different-section pairs, which is a built-in control, and the AUC comparison
(CENS + 2DFTM at 0.835 aligned, 0.797 at two seconds of misalignment) stands.
That is the part with evidence behind it.

The independence result also stands: fingerprint quality is uncorrelated with
word-tracker difficulty (Spearman +0.011), so a harmonic stream would fail on
different cases than the word tracker does.

## What that leaves

A harmonic stream is supported as a **section identity** signal — "does this
audio look like the chorus we stored" — and not as a boundary detector. Which
happens to be the half the word tracker is already good at, so the case for
building it is considerably weaker than it looked two commits ago.

Before going further, the cheapest thing that would change the picture is
**musically-annotated boundaries**. Every boundary number here is scored against
operator key-presses, and if those carry several hundred milliseconds of
variable reaction lag, a 1.5 s tolerance is wide enough that the null models
score as well as anything. It remains possible that harmony marks boundaries
precisely and our ground truth cannot see it — but that is a hypothesis, and
right now the measured position is that it does not.

---

# Facet 1, tested against audio-anchored boundaries (2026-09-01)

The previous section left an excuse open: every boundary number was scored
against operator key-presses, so a real signal might have been hidden by human
reaction lag. That excuse is now closed.

## Better ground truth, from machinery the project already trusts

`bootstrap_golden.decode_slides` — a Viterbi over `(slide, dwell)` states whose
emissions are the matcher's own phonetic scorer, validated at F1 0.91 against two
hand-annotated recordings — was reused to attribute every transcript word to a
slide. A boundary is then the **onset of the first sung word** of each slide:
audio-anchored, with no human in the loop.

**The operator turns out to be unbiased but noisy.** Comparing presses to
first-sung-word onsets over 129 matched pairs:

| | |
|---|---|
| median lag | **+0.01 s** |
| quartiles | −0.49 s .. +0.87 s |
| 10th-90th percentile | −1.13 s .. +2.65 s |
| within ±0.5 s of the sung word | 41% |
| within ±1.0 s | 65% |

So there was no systematic lag to correct — only scatter. The aligner has its own
flaw in the other direction: **43% of its boundaries have no corresponding
press**, i.e. it over-segments, mostly by flipping between textually identical
slides.

Neither source is clean alone, so ground truth here is their **intersection**:
129 boundaries where a human and the audio agree that a transition happened, with
the *timing* taken from the word onset. That is the annotation this facet needed.

## Blind detection: still essentially chance

| tolerance | random peaks | evenly spaced | Foote on CENS |
|---|---|---|---|
| ±1.5 s | 19% | 23% | 24% |
| ±1.0 s | 12% | 15% | **19%** |
| ±0.5 s | 9% | 6% | 8% |

There is a modest real edge at ±1.0 s — 19% against 12% for random — that was
invisible against the noisier ground truth. It disappears at ±0.5 s, the
precision that would actually matter for a slide change.

## Guided detection: high recall, unusable timing

Blind peak-picking is not what facet 1 proposed, though. A mapped track means
knowing which two sections we are between, so the real question is: *we are on A
and expect B — when did the audio stop being A?* That uses the identity signal,
which works, rather than novelty, which does not.

Each slide's fingerprint was stored from its **first** corroborated occurrence
and tested only on later ones, so no descriptor was scored on the audio it was
built from. A 4 s trailing window sweeps the transition and the crossover is the
first moment B outscores A.

| | |
|---|---|
| transitions where a crossover was found | **96%** (53 of 55) |
| median timing error | **−2.14 s** (fires early) |
| absolute median error | **3.36 s** |
| within ±1 s | 15% |
| within ±2 s | 28% |
| a random instant in the same span, for contrast | 4.91 s |

It beats chance and it almost always fires, but a 3.4 s median error is not a
switch time. The early bias is diagnostic: 2DFTM descriptors bunch near cosine
0.96-0.98, so which of two candidates scores higher flips on small differences,
and the crossover is largely noise-driven.

## Facet 1: not supported

Two independent routes — blind novelty and guided identity crossover — both fail
to produce usable switch timing, now measured against boundaries that are
audio-anchored and corroborated. The harmonic signal carries **section identity**
(AUC 0.835) and does not carry **section timing**.

## What this is good for anyway

The aligner-derived boundaries are more precise than the operator's own presses
(41% of presses are more than half a second from the sung word). That is directly
useful independent of fingerprinting: the goldens' ground truth could be
re-anchored to word onsets, which would sharpen every timing metric in the
project, including `anticipation_error_ms`.

And it is the answer to the "generate the map on the fly from ASR" question:
the machinery exists and works offline at F1 0.91. What has not been tested is
whether it works **causally** — `decode_slides` is a Viterbi over the whole
transcript and uses future words to decide past states, which a live system
cannot do.

---

# Why it misses, traced (2026-09-01)

Asked to find the bug in the switching logic or the eval logic. There is
neither. The signal is not present at the granularity a live system needs, and
the earlier AUC of 0.835 measured an easier question than the one being asked.

## The trace

Printing the A-vs-B similarity trajectory across a real transition
(`rec_20260818-214759_057`, span A 9.5 s, into B):

    t      rel. boundary   sim A    sim B   pick
    140.2      -30.7      0.9593   0.9593    B
    152.2      -18.7      0.9707   0.9703    A
    159.7      -11.2      0.9629   0.9720    B
    170.2       -0.7      0.9720   0.9672    A
    173.2       +2.3      0.9692   0.9748    B

Every value sits between 0.93 and 0.99, the margin between them is 0.005-0.02,
and the winner **alternates throughout the span with no structure at the
boundary**. Matching the window length to the stored section (9.5 s instead of
4 s) changes nothing. So this is not a windowing artifact and not a timing
artifact — the descriptor simply is not separating these two sections.

That also explains the impossible-looking result in the previous section: a
*trailing* window fired 2.14 s **early**, which no causal detector should manage.
It was not detecting anything early; it was flipping at random, and the first
flip tended to land before the boundary because that is where most of the
scanned span lies.

## Where 0.835 went

The AUC pooled every same-section against different-section pair, which includes
a verse against a bridge from elsewhere in the song. The live question is
narrower. Asked as a two-way forced choice — *does audio from section X score
higher against X than against the section we would otherwise switch to?*, chance
being 50%:

| task | accuracy |
|---|---|
| whole span vs a **random** other section | 73% |
| whole span vs the **next** section | 62% |
| **4 s window vs the next section** | **53%** |

Two effects compound, and both were hidden by pooling. Adjacent sections are
harder than arbitrary ones — same song, same key, largely the same chords. And a
short window carries far less than a whole section.

## The bind, in one table

| window | accuracy against the next section |
|---|---|
| 2 s | 44% |
| 4 s | 53% |
| 6 s | 62% |
| 8 s | 62% |
| 12 s | 75% (n=12) |

Discrimination rises with window length, and **a window long enough to
discriminate is longer than the decision it exists to inform**. Six seconds is
the shortest that clears chance meaningfully, but a 6 s window spanning a
boundary contains both sections, so it cannot report "we are on B" until roughly
six seconds after B began — against a word-based tracker already committing in
about one.

## Fusion, measured rather than argued

The proposal was to combine fingerprint and phoneme evidence. Scored at 1 s
intervals across all six goldens, with a 6 s window and each section's
fingerprint stored from its first corroborated occurrence:

| when the word tracker was **wrong** | |
|---|---|
| fingerprint points at the right slide | 97 (26%) |
| fingerprint also wrong | 274 (74%) |

| when the word tracker was **right** | |
|---|---|
| fingerprint agrees | 359 (34%) |
| fingerprint disagrees | 698 (66%) |

**For every moment the fingerprint could rescue, it would threaten 7.2 it would
break.** A fused term would be strongly net-negative, and no weighting fixes
that: the signal is close to uninformative about which of a song's own sections
is playing, so weighting it down toward zero is the best available setting.

## Conclusion

The honest reading is that this was my error rather than a defect in the idea's
execution. Measuring identity as pooled same-vs-different over whole sections
was the wrong benchmark, and 0.835 on it did not license the claim that
fingerprints could drive slide decisions. The forced-choice number against the
adjacent section, at the window length a live system can afford, is the one that
matters, and it is 53%.

What would change this is a representation that discriminates *within* a song at
short horizons — the neural cover-song embeddings ruled out earlier on runtime
and licensing grounds are the part of the literature aimed at exactly that. Plain
chroma, CENS and 2DFTM are not, and no amount of tuning around them will be.

---

# Re-framed: the side channel is for speed, not identity (2026-09-01)

Corrected goal: the word tracker already identifies slides well. What limits the
app is that Whisper delivers a word 2-3 s after it is sung, so the app catches up
instead of anticipating. A second engine is worth having only if it says
something useful **in under a second**. Identity is not its job.

That reframing invalidates how the last several experiments were judged — they
asked whether music could pick the right slide, which duplicates what words
already do and is the harder problem.

## Asking the fast question instead

*Has the band just started singing again after a gap?* Energy, not harmony.
Causal, and effectively free (0.0011 realtime factor against Whisper's ~0.31).

**Full band: 10% of boundaries.** Worship music flows — instruments and voices
run continuously through most transitions, so there is no gap to find. Only 1.8
phrase starts per minute are detectable against 3.7 slide changes per minute.

**Vocals only: 60% of boundaries within one second**, median error +0.37 s, on
the live Reckless Love run. A close mic on the worship leader is a very
different signal, because the silences between sung lines are real.

| | full band (6 goldens) | vocals only (1 run) |
|---|---|---|
| boundaries with a phrase start within 1 s | 10% | **60%** |
| phrase starts per minute | 1.8 | 6.2 |
| slide changes per minute | 3.7 | 3.6 |

Caveat: ten boundaries in one recording. This is a direction, not a result.

## Why this shape is more promising than fingerprinting was

The detector over-fires — 17 phrase starts against 10 real boundaries — so it
cannot drive switching alone. But it does not need to. The word tracker knows
which slide is showing and what follows; what it lacks is a precise moment. Used
as a **trigger rather than a detector** — act on a phrase start only when the
tracker already expects a transition — most spurious fires are filtered by a
condition the tracker already evaluates.

That is the opposite arrangement to everything tried above, and it fits both
measured facts: the fast signal is imprecise about *what* and reasonably precise
about *when*, and the word tracker is the reverse.

## An implementation bug worth recording

The first version of this detector returned **zero** starts on every recording.
The cause was requiring the frame immediately after a lull to exceed the rise
threshold — energy ramps over a syllable rather than stepping, so that frame is
always still low, and the counter reset before the rise arrived. Zero detections
across 35 minutes is not a finding, and was not treated as one.

## Room mic: hand-built features cannot do it (2026-09-01)

A room mic is the common case, so the vocals-only result above does not settle
anything on its own. Tested whether any cheap frame-level feature can mark
sung-word onsets in a full mix, scored against Whisper's own word timestamps —
a fair target, since the question is only *when* a word began, not which word.
Each method was given exactly as many peaks as there are words, so recall is
comparable.

| method | onsets found within 150 ms |
|---|---|
| random peaks | 31% |
| energy rise | 43% |
| spectral-envelope change (MFCC delta) | 40% |

The random baseline is high because words are dense — roughly one a second — so
peaks land near one by luck. Against that, both features are close to
uninformative, and the voice-selective one is no better than raw level.

This is the clearest argument yet **for** a learned model. The room-mic problem
is not loudness and not spectral movement; it is separating a voice from a band
that never stops, and hand-built features do not encode what a voice is. A CTC
phoneme model does, and its blank-versus-non-blank probability is a voice
detector learned from speech rather than a threshold.

### What a CTC spike would cost

Purposed for *timing* rather than matching, the bar is much lower than the 2026-08-21
spike faced: it does not need correct phonemes, only to know when a syllable
starts. That spike rejected `wav2vec2-lv-60-espeak` on phoneme *quality*, which
is no longer the criterion.

Standing in the way:

- **No PyTorch anywhere.** The sidecar has onnxruntime only, deliberately, and
  the isolated spike venv is gone. Trying a model means a torch install for
  export, then ONNX for runtime.
- **Size.** The cached espeak model is 2.4 GB at 316M parameters and measured
  RTF 0.40 — usable in real time but expensive beside Whisper's 0.31, which
  defeats the point of a *cheap* side channel. A small model (wav2vec2-base at
  95M, or a streaming conformer) is the interesting one, and nothing suitable is
  cached.
- **Sung, full-mix audio is out of distribution** for every speech-trained model
  — the same reason the project runs `vad_filter=False`, after a speech VAD
  dropped 60 of 349 sung words.

The last point is the risk worth weighing: the argument for CTC is that a
learned speech model knows what a voice is, and the standing evidence is that
speech-trained models do poorly on sung worship audio. Blank-vs-non-blank may
survive where phoneme identity did not, since it is a far coarser question — but
that is the hypothesis, and it is the first thing a spike should measure, before
any ONNX or packaging work.
