# Musical feature literature, filtered through our constraints

Researched 2026-09-01, after the spike in docs/research/MUSIC_FINGERPRINTING.md measured
where a harmonic stream helps and where it breaks.

## The constraints that actually filter the field

Most of this literature optimises for large-scale retrieval accuracy. We have a
different problem, and four of our constraints eliminate most candidates before
accuracy is even discussed.

1. **Boundary misalignment is fatal.** Measured: 1 s of misalignment takes
   section-matching separation from +0.216 to +0.021; 2 s makes it negative. Any
   method needing correct segment boundaries to compute its feature is circular,
   because finding boundaries is the job.
2. **Key invariance must be free.** Searching 12 rotations cost us half the
   separation (+0.216 -> +0.115), because every wrong section also gets twelve
   chances to match.
3. **No PyTorch at runtime.** The shipped PyInstaller binary carries
   `onnxruntime` and numpy only (see docs/research/ASR_ENGINE.md). This rules out most
   recent neural work unless it exports cleanly to ONNX.
4. **Licensing.** This ships. A CC BY-NC-SA model is not usable.

Plus: streaming/causal, ~0.03 realtime factor of budget (we use 0.0015 today,
Whisper uses ~0.31), and the division of labour the measurements support —
**words decide which slide, harmony may adjust when**.

## Ruled out, with reasons

**Shazam-style landmark fingerprinting** (spectral peak pairs → hashes). Built to
identify *the same recording* through noise. It is explicitly sensitive to pitch
shift and is not designed for temporal deformation. We never see the same
recording twice — a band plays the song live, differently each week — so the
invariances it lacks are precisely the ones we need. Its strength (exact-match
robustness to noise and codecs) is irrelevant here.

**Neural boundary detection** (Grill & Schlüter and successors) is the
state-of-the-art on MIREX structure segmentation and would likely beat anything
below on recall. Blocked on constraints 3 and 4 unless a permissively-licensed
model exports to ONNX. Worth revisiting if this direction proves itself.

**Neural cover-song embeddings** (ByteCover, DisCover and similar). Same
blockers, and they target cross-recording retrieval at million-track scale,
which is a much harder problem than the one we have.

## Candidate 1 — 2D Fourier Transform Magnitude over beat-synchronous chroma

Bertin-Mahieux & Ellis, ISMIR 2012. **The strongest fit, because it solves our
top two constraints in a single step rather than trading them off.**

Take a patch of beat-aligned chroma, apply a 2D Fourier transform, keep the
**magnitude and discard the phase**. Discarding phase gives invariance to
rotation on the pitch axis (transposition, so key) *and* to offset on the time
axis (misalignment) simultaneously — the paper's stated goal is to match patches
without having to identify a universal time alignment. They reduce to a
50-dimensional vector with PCA and compare with plain Euclidean distance.

Why it maps onto our measurements:

- Time-shift invariance attacks the failure that dominates everything else. We
  do not need to find the boundary before computing the feature.
- Key invariance comes free from the same operation, rather than costing half
  the separation as a 12-rotation search does.
- Beat-synchronous framing normalises tempo more principledly than our fixed
  24-frame resampling, which only works when both boundaries are right.
- The output is a short fixed-length vector compared with a cheap metric, which
  suits a per-frame streaming comparison against a small candidate set.

Caveat worth stating: their headline number is 3.0% mean average precision on
18,000 covers among a million tracks. That sounds poor and is not our task —
retrieval among a million distractors versus discriminating a dozen sections of
one known song. The property we are borrowing is the invariance structure, not
the accuracy.

**Requires a beat tracker** (see candidate 3).

## Candidate 2 — CENS features

Müller, Kurth & Clausen. Chroma Energy Normalized Statistics: L1-normalise each
chroma vector, quantise with thresholds, then smooth and downsample over a large
window. Explicitly designed for audio matching, and the smoothing absorbs local
deviations in tempo, articulation, dynamics and ornamentation.

This is the cheapest meaningful upgrade available. Every number in our spike came
from raw FFT chroma with no quantisation, no smoothing, and no tuning
correction; CENS is a small amount of numpy on top of what we already compute,
with no model, no license question, and no new dependency. It should be tried
**first** simply because it isolates how much of our moderate separation is the
crudeness of the front end rather than a limit of the idea.

Does not solve alignment sensitivity on its own — it makes the features better,
not shift-invariant. Natural to combine with candidate 1.

## Candidate 3 — beat tracking as the timing primitive

Two options, and the choice is about licensing rather than accuracy.

**madmom** (CPJKU) implements its networks in numpy and Cython — no PyTorch,
which fits constraint 3 — and supports online causal operation. But its
**pre-trained models are CC BY-NC-SA**: source is BSD, models are not, and
commercial use requires contacting the author. That is a blocker for a shipped
app unless licensed.

**Ellis-style dynamic-programming beat tracking** — onset-strength envelope,
tempo by autocorrelation, beats by dynamic programming. No model, no license
encumbrance, straightforwardly numpy, and the classic baseline. Less accurate
than madmom, but worship music is steady-tempo with a strong beat, which is the
easy case.

Newer online work (BeatNet, BEAST — the latter reporting sub-50 ms latency)
is PyTorch and blocked on constraint 3.

**A caution before investing here.** The obvious hope is that slide changes land
on bar lines, letting the grid supply timing. Measured on our six goldens,
transition phase concentration was 0.03-0.23 within the beat and 0.15-0.48
within the bar, against roughly 0.21 for random — i.e. inconclusive. But the
test cannot settle it, because **our ground truth is operator key-presses**, not
musical events, and human reaction latency of a few hundred milliseconds smears
exactly this measurement. Two recordings do hint at bar alignment (0.48, 0.37).

Settling it needs boundaries annotated against the audio rather than captured
live — which the Golden Recording Editor could produce. **That is a
prerequisite, not a follow-up**, and it also qualifies the earlier claim that
novelty peaks time transitions to +0.04 s: that too was measured against
operator presses.

## Candidate 4 — proper Foote novelty for boundary detection

Our 24% boundary recall came from a hand-rolled novelty: cosine distance between
the mean chroma either side of each frame. The standard formulation is a
Gaussian-tapered **checkerboard kernel** slid along the diagonal of a
self-similarity matrix (Foote 1999/2003), and the literature consistently
reports that self-similarity *lag* matrices combined with spectral features beat
either alone.

Low risk, no new dependency, and it directly targets the weakest number in the
spike. Like CENS this is a front-end quality question rather than a new idea.

## Suggested order

1. **CENS + proper checkerboard novelty.** Cheap, no dependencies, no licensing.
   Tells us how much of the spike's mediocrity was the crude front end.
2. **Musically-annotated boundaries on two goldens.** Unblocks every timing
   claim, including ones already made. Needed before candidate 3 can be judged.
3. **2DFTM over chroma patches**, initially with fixed-length rather than
   beat-synchronous patches, to test the invariance claim without taking on a
   beat tracker. If shift-invariance holds up, it removes our worst failure.
4. **Beat tracking**, Ellis-style first, only if 2 shows the grid carries timing
   information worth having.

## Sources

- Bertin-Mahieux & Ellis, *Large-Scale Cover Song Recognition Using the 2D
  Fourier Transform Magnitude*, ISMIR 2012.
  https://ismir2012.ismir.net/event/papers/241_ISMIR_2012.pdf
- Müller, Kurth & Clausen, *Audio Matching via Chroma-Based Statistical
  Features*, ISMIR 2005.
  https://resources.mpi-inf.mpg.de/MIR/chromatoolbox/2005_MuellerKurthClausen_AudioMatching_ISMIR.pdf
- Müller & Ewert, *Chroma Toolbox*, ISMIR 2011.
  https://www.audiolabs-erlangen.de/content/resources/MIR/chromatoolbox/2011_MuellerEwert_ChromaToolbox_ISMIR.pdf
- Grill & Schlüter, *Music Boundary Detection Using Neural Networks*, ISMIR 2015.
  https://grrrr.org/data/pub/grill_schlueter-2015-ismir.pdf
- Hernandez-Olivan et al., *Music Boundary Detection using CNNs: comparative
  analysis of combined input features*. https://arxiv.org/abs/2008.07527
- madmom. https://github.com/CPJKU/madmom
- Heydari et al., *BeatNet*, ISMIR 2021. https://github.com/mjhydri/BeatNet
- Chang et al., *BEAST: Online Joint Beat and Downbeat Tracking*.
  https://arxiv.org/abs/2312.17156
- Tralie, *Cover Song Identification with Timbral Shape Sequences*, ISMIR 2015.
  https://ismir2015.uma.es/articles/277_Paper.pdf

---

# Fast side-channel models: what is actually available (2026-09-01)

Researched against one criterion — can it tell us a sung line has started, in
well under a second, cheaply enough to sit beside Whisper.

## The model already asked about

`Wav2Vec2Phoneme` is the model *class*; the checkpoint everyone means by it is
`facebook/wav2vec2-lv-60-espeak-cv-ft`, which is exactly what the 2026-08-21
spike tested and rejected (316M parameters, 2.4 GB, RTF 0.40, poor phonemes on
sung full-mix audio). So that suggestion lands on the model we already have.

Two distinctions that spike did not draw, both of which matter now that the
purpose is timing rather than transcription:

**CTC is the wrong head for timing.** CTC alignments are *peaky* — the model
emits a spike somewhere inside a phone rather than at its boundary, and where
that spike lands is not trained to be anywhere in particular. For deciding
*when* a syllable began, that is close to the worst property a model could have.

**Frame classification is the right one.** Charsiu's `en_w2v2_fc_10ms` is a
`Wav2Vec2ForFrameClassification` model: it labels **every 10 ms frame**, because
it was built as a forced aligner rather than a recogniser. Same backbone family,
different head, and aimed at exactly the question we are asking. It is built on
wav2vec2-**base** (~95M), roughly a third the size of the espeak checkpoint.

## The strongest option on deployment grounds

**sherpa-onnx** (k2-fsa), Apache-2.0, **depends only on onnxruntime** — which is
precisely our runtime constraint, with no PyTorch anywhere and no export step.
Models ship pre-converted. The English streaming zipformer starts at **20M
parameters**, and comparable streaming models are quoted at ~160 ms latency on a
phone and real time on a Cortex-A7. It is designed for exactly the streaming,
low-latency, offline case we need, and it is the only candidate that requires no
new runtime dependency at all.

## The task that actually matches our problem

Worth naming, because it is neither ASR nor VAD: **singing voice detection** in
polyphonic music is its own MIR problem, with low-latency real-time methods
going back to LSTM work in 2015, and more recent transfer-learning approaches
that train on artificial mixtures of clean speech with instrumental music
precisely because singing over a band is out of distribution for speech models.

That literature exists because this is hard, which is the standing risk for
every option above: they are all speech-trained, and this project has already
been bitten once — `vad_filter=False` because a speech VAD discarded 60 of 349
sung words. No off-the-shelf ONNX singing-voice detector surfaced.

## Recommended order

1. **sherpa-onnx streaming zipformer (20M).** No new runtime dependency, Apache
   licensed, pre-exported. Measure one thing: do its token timestamps mark sung
   word onsets in a full mix, and how fast. Cheapest possible test of the whole
   idea.
2. **Charsiu `en_w2v2_fc_10ms`.** Better *suited* — frame-wise labels at 10 ms —
   but needs a torch environment for export and is ~95M. Worth it only if (1)
   shows the approach has legs and its timing is not precise enough.
3. **The espeak checkpoint we already have.** Largest, slowest, CTC-peaky.
   Not the one to try first, despite being the one already on disk.

## Sources

- sherpa-onnx (k2-fsa), Apache-2.0, onnxruntime-only.
  https://github.com/k2-fsa/sherpa-onnx — streaming models:
  https://k2-fsa.github.io/sherpa/onnx/pretrained_models/index.html
- Charsiu phonetic aligner. https://github.com/lingjzhu/charsiu ;
  Zhu et al., *Phone-to-audio alignment without text: a semi-supervised
  approach*. https://arxiv.org/pdf/2110.03876
- Wav2Vec2Phoneme docs.
  https://huggingface.co/docs/transformers/model_doc/wav2vec2_phoneme
- Lee et al., *Investigation of Singing Voice Separation for Singing Voice
  Detection in Polyphonic Music*. https://arxiv.org/abs/2004.04040
- *Transfer Learning for Improving Singing-voice Detection in Polyphonic
  Instrumental Music*. https://arxiv.org/pdf/2008.04658

---

# Singing vs speech: is there a purpose-built detector? (2026-09-01)

Asked after shipping a detector that reuses the tracker's own match-quality
signal. Short answer: yes, this is a named problem with recent work, and the
literature both **confirms our signal is not the obvious one** and **names a
cheaper acoustic feature we have not tried**.

## The problem has a name and a recent paper

Grundhuber et al., *Robust Speech Activity Detection in the Presence of Singing
Voice* (Fraunhofer IIS / International Audio Laboratories Erlangen, Dec 2025).
It opens on our exact complaint: speech activity detectors "often misclassify
singing as speech". They train a GRU model on 80-band log-mel input against
mixed speech/singing and report **AUC 0.919 while rejecting singing**, plus a
low-complexity variant that downsamples along time to cut inference cost.

Directly on point, and it also confirms this is genuinely hard rather than
something we were fumbling: they note the whole area "remains underexplored"
despite mattering for exactly this kind of application.

Blocked for us on the usual two: it is a neural model with no obvious released
weights, and it would need ONNX export to reach our runtime.

## The cheap feature we have not tried: pitch stability

The more useful finding is *why* singing and speech differ, which the same paper
summarises from earlier work: **higher fundamental frequency, lower temporal
rate, and increased pitch stability**. Sarasola et al. classified speech against
singing using **pitch-derived parameters alone** — pitch stability and musical
note duration — and reported it works on short segments and across languages and
singing styles. Another line reports 94.2% accuracy from fundamental frequency
plus autocorrelation peak amplitude.

That is a hand-computable, numpy-only feature with no model and no licence
question, which is exactly the shape that survives our shipping constraints.

## Whether it is worth doing

The bar is not the literature's 94%. The bar is what we already have, which is
**free**: the detector reuses evidence the tracker computes anyway, and on the
goldens runs at 4.1% false alarms with 75% of speech caught, noticing in about
five seconds.

Pitch stability would attack the one weakness that matters — **latency**. Ours
needs a dozen transcript events because it works on words; a pitch feature works
on frames and could answer in well under a second. It is also independent of
whether the ASR produced anything, so it covers the silence case and the
speech case with one mechanism.

The reason not to rush: **every published number here is on clean or monophonic
audio**, and this project has now been beaten twice by hand-crafted acoustic
features on full-mix room recordings — energy-based onset detection scored 43%
against 31% for random, and harmonic fingerprints reached chance. Estimating a
stable pitch through a full band is a much harder problem than the papers are
solving, and the honest expectation is that the clean-audio numbers will not
transfer.

**Recommendation:** worth an afternoon, measured against 4.1%/75% rather than
against 94%, and only after there is real audio of someone *talking* over a
service — which we still do not have. The current detector is not the
bottleneck, and the same recordings would serve the spoken-instructions work.

## Sources

- Grundhuber, Halimeh, Strauß & Habets, *Robust Speech Activity Detection in the
  Presence of Singing Voice*. https://arxiv.org/abs/2512.09713
- Sarasola et al., *Application of Pitch Derived Parameters to Speech and
  Monophonic Singing Classification*. https://www.mdpi.com/2076-3417/9/15/3140
- *Discrimination of speech and monophonic singing in continuous audio streams
  applying multi-layer support vector machines*.
  https://www.researchgate.net/publication/4124910
- *A hierarchical approach for speech-instrumental-song classification*.
  https://pmc.ncbi.nlm.nih.gov/articles/PMC4322669/

## Measured, and rejected (2026-09-02)

The afternoon was spent. Pitch stability does not survive our microphone, and
the prediction directly above turned out to be the right one.

The feature: autocorrelation F0 per 64 ms frame (70–500 Hz), converted to
semitones so it does not depend on how high the voice is, then the standard
deviation over a trailing 750 ms. Small means a held note, large means a glide.
Scored as a ranking of "this is talking" over 1738 transcript events:

    within one recording — same voice, same room     AUC 0.811
    pooled across recordings, absolute threshold     AUC 0.313
    pooled, normalised against each recording        AUC 0.448

Read those in order, because the first one is a trap. Inside a single recording
the literature's effect is plainly there and in the right direction — singing
3.03 semitones of spread against 7.27 for talking. It does not survive pooling.
An absolute threshold lands at 0.313, meaning it is not merely useless but
*anti*-correlated: on the six goldens, spread during pure singing ran a median
7.60 and a p95 of **5.69 semitones above that recording's own baseline**, wider
than actual speech.

The cause is the one the section above named. Our goldens are a room mic over a
full band, and the F0 track follows whichever instrument is loudest rather than
the voice — periodicity confidence sat at 0.56 during singing, against 0.82 in
the near-solo recordings where the feature worked. The published results assume
an isolated vocal.

Normalising each recording against its own running baseline was the obvious
repair, since the within-recording signal is real. It does not work either
(0.448, chance): the baseline is built from the same instrument-dominated
frames, so it is noise measured against noise.

**Where this leaves it.** Pitch is not usable on a room mic and is not in the
shipped detector. It is worth revisiting only if we ever get a vocal-only feed —
a desk send, or a channel off the mixer — where the confidence figures suggest
it would work well and would answer in under a second, which words never can.

What replaced it is duller and does work: the same two word-level signals we
already had, **combined into one score instead of OR'd as separate triggers**.
Word rate alone ranks at AUC 0.933 and match quality at 0.844. The OR-of-rules
version cost 14.6% false alarms on the goldens because independent triggers
compound their errors; the compound score costs **1.3%** at 95–98% of sustained
speech caught. See `decision/singing.py`.

The other thing that changed: **five speakers, not one**. With a single sermon
clip the held-out detection rate read 94% in-fit and 13% held out — the model
had learned one reader. Four more public-domain LibriVox sermons fixed it.

## Vocal separation, measured and shelved (2026-09-03)

Phase 9 (`docs/research/VOCAL_SEPARATION.md`) named Demucs as the model and PyTorch as the
blocker. Both parts of that have changed enough to re-check.

**The PyTorch blocker is gone.** `StemSplitio/htdemucs-ft-vocals-onnx` on
Hugging Face is an MIT-licensed ONNX export of the vocals sub-model from
`htdemucs_ft` — the exact model Phase 9 asked for — verified numerically
equivalent to the original (<1e-3 max abs diff), no PyTorch at inference.

**The accuracy case is real, not speculative.** Ran it on the two goldens
`decision_asr_engine` already had full-mix WER numbers for, using the same
whole-file base.en pass, WER against the actual sung order (not just the
deduped lyric sheet):

| song | full-mix WER | separated WER |
|---|---|---|
| Reckless Love | 28.9% | 16.2% |
| I Surrender All | 20.6% | 20.0% |
| mean | 24.7% | **18.1%** |

A 6.6-point drop, in line with Phase 9's original ≥5-point bar.

**Shelved anyway: it needs 7.8 s of audio before it can produce anything.**
`htdemucs_ft` is not causal — the ONNX export chunks in fixed 7.8 s segments
with overlap-add, because the architecture needs symmetric context, not just a
slow model. Bolting that onto either channel roughly triples the total
emission latency the whole live-use effort this session has been chasing down.
Measured RTF was fine (0.62-0.63 on this machine) — RTF was never the
problem, the *context requirement* was, and no amount of speed fixes that.

**Checked whether a causal, low-latency alternative exists.** It does, on
paper, and it clears the bar comfortably:

| model | latency | SDR (vocals) | released weights? |
|---|---|---|---|
| Band-SCNet (Interspeech 2025) | **92 ms** | 7.79 dB | **no** — no official repo at all |
| RT-STT / "Towards Practical Real-Time Low-Latency..." (Nov 2025) | causal, sub-second by design | not directly comparable | **no** |
| Hybrid Spectrogram-TasNet (ByteDance/L-Acoustics, 2024) | causal | — | **no** |
| DTTNet (the offline model RT-STT modifies) | not low-latency | 10.12 dB | yes, via Mega/Baidu links, weight licence unstated | 

Real-time source separation is an active research area and the causal models
are good — Band-SCNet's numbers would likely still show a meaningful WER win
even below full offline quality. But nothing in that column ships a model file
we can point `onnxruntime` at. Getting there means training one ourselves,
which needs a labelled separation dataset and training infrastructure this
project doesn't have — a different-shaped project, not an integration.

**Where this leaves it:** shelved, not rejected on the merits. The one thing
that would reopen it is one of the three no-weight rows above publishing a
checkpoint — worth a search again in a few months, not worth building around
today. The measured instrumental-hallucination failure mode Whisper has is
already handled for free, at zero added latency, by the singing detector
shipped the same day this was measured — see the section above.
