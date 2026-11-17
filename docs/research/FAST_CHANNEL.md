# A fast side channel: sherpa-onnx streaming zipformer

Spiked 2026-09-01. Goal from docs/research/LIVE_LATENCY.md: the word tracker identifies
slides well but Whisper delivers a word 2-3 s after it is sung, so the app
catches up instead of anticipating. A second engine earns its place by being
**fast**, not by being right.

## What works: the latency and the cost

| | Whisper base.en | sherpa 20M | sherpa large |
|---|---|---|---|
| realtime factor | ~0.31 | **0.020** | 0.040 |
| word available after it is sung | 2-3 s | **0.42 s** | 0.48 s |

That is the number the whole idea rested on, and it holds. A word arrives in
under half a second, for a fifteenth of Whisper's compute, from a model that
needs **no new runtime dependency** — sherpa-onnx is Apache-2.0 and runs on
onnxruntime, which the sidecar already ships.

## What does not work: it cannot hear singing

| | Whisper | sherpa 20M | sherpa large |
|---|---|---|---|
| word error rate | 0.22 | 0.85 | 0.59-0.96 |
| wrong-slide driving the tracker alone | **0.484** | 0.748 | 0.758 |

Recognisable fragments of the verse and chorus come through, with garbles
such as one chorus line heard as near-homophones for "it chases
me down, fights til I'm found" — so it is hearing the voice. But four words in
five are wrong, and the matcher's phonetic tolerance does not close that gap.

**A larger model does not help**: 342 MB against 142 MB, twice the compute, and
wrong-slide went the wrong way. This is not a capacity limit. It is the same
wall every speech-trained model has hit on this project — sung, full-mix worship
audio is out of distribution, which is why `vad_filter=False` after a speech VAD
discarded 60 of 349 sung words, and why the 2026-08-21 phoneme spike failed.

Whisper is the outlier for coping at all, presumably from the breadth of its
training audio.

## As a pure onset detector, also no

Discarding the words entirely and using only the moments it chooses to emit:

| | sung words located within 150 ms |
|---|---|
| random peaks | 31% |
| energy rise | 43% |
| sherpa word onsets | 17% |
| sherpa any-token onsets | 23% |

Partly unfair — it emits far fewer onsets than there are words, so it cannot
match the candidate count — but the underlying fact is the same: it misses most
sung words, so it cannot report when they happened.

## Where that leaves it

The architecture is validated and the model is not. Sub-second, near-free,
no new dependency — every structural property we wanted is real. What is missing
is a streaming model that handles singing over a band, and neither size of
zipformer does.

Two things follow:

1. **Judging this on the goldens alone is premature.** Every measurement here is
   full-mix band recordings. Live input may be vocals-forward, a different mic,
   a different room. The debug panel added alongside this exists so the fast
   channel can be watched live against the same audio Whisper is hearing, and
   judged on real input rather than on six recordings.
2. **The remaining candidate is a model trained for singing**, not a smaller or
   larger speech model. Singing voice detection in polyphonic music is its own
   MIR field precisely because speech models fail here — see
   docs/research/MUSIC_LITERATURE.md. Nothing suitable was found pre-exported to ONNX.

## Watching it live

`auto_presenter/asr/fast_channel.py` runs the streaming model alongside Whisper
and broadcasts what it hears. A **Fast channel** tab in the debug panel shows,
side by side:

- **Delay before we hear a word** — the fast channel's median, against Whisper's
  2-3 s. This is the number the feature exists for.
- **CPU used** — against Whisper's ~31%.
- **Both transcripts**, fast above and Whisper below, so the accuracy cost is
  visible at the same moment as the latency gain.

Off by default. Set `AUTO_PRESENTER_FAST_MODEL` to a sherpa-onnx streaming model
directory; without it the tab explains itself and nothing is loaded.

**It does not affect slide changes by default, but it can.** It is attached to
the frame fan-out beside the recording sink, and if it raises it disables
itself for the session. Diagnostic-only was true when this was written; a
`feeds_matcher` toggle was added afterwards and this paragraph was not
updated. With it on, `server_fast_channel.py` pushes fast words through the
same transcript handler Whisper's take, so they commit slides, move the belief
and are recorded to the golden exactly like slow ones.

The toggle is off by default because of the measurements above — the fast
words buy roughly 0.8 s of anticipation for about 0.09 of wrong-slide — but
"cannot" was wrong, and a doc that says a live path is unreachable is worse
than no doc.

**Those numbers predate the 2026-09-04 scoring work and should be re-taken
before the trade is judged again.** `wrong_slide_rate` now pools slides
carrying identical words, gives an unpredictable jump a 2 s recovery window,
and excludes blank cues — three changes that each move the baseline
substantially. The fast channel's cost was measured against the older,
unforgiving scorer, and its *benefit* is anticipation, which that scorer never
charged for in the first place.

The point of shipping it as a panel rather than a conclusion is that every
number in this document comes from six full-mix band recordings. A live room,
a vocals-forward mix or a different mic may behave differently, and this is how
that gets seen rather than assumed.

---

# Correction: the words were usable, the feed was wrong (2026-09-01)

The conclusion above — that the fast channel's words are too poor — was reached
by feeding them to a tracker calibrated for a different word rate. Challenged on
the grounds that a near-homophone garble of a chorus line *ought* to
match the line itself under phonetic comparison, which
is the entire reason the matcher is phonetic.

That challenge was right.

## The scorer handles these words fine

`phonetic_match_score` against the correct slide:

| heard | best slide word | score |
|---|---|---|
| IT | it | 1.00 |
| SAYS | chases | 0.33 |
| ME | me | 1.00 |
| DOWN | down | 1.00 |
| FIGHT | fights | 0.60 |
| AND | found | 0.40 |
| FOUND | found | 1.00 |

Four of seven at 1.00. The words were never the problem.

## The problem was word density

The forward filter advances `advance + 2*skip` ~= **0.42 word-positions per
observation**, a rate tuned against Whisper. For one golden:

| | words delivered | positions the posterior travels | song length |
|---|---|---|---|
| Whisper | 461 | 194 | 145 words |
| fast channel | 218 | **92** | 145 words |

Ninety-two positions cannot traverse a 145-word song even once, let alone its
repeats. The posterior was left behind by arithmetic, not by evidence quality.
Re-tuning for the sparser stream: wrong-slide **0.748 -> 0.616**.

## Both streams together: the trade the side channel was for

Merging the two on *emission* time — rough words at ~0.4 s, accurate ones at
2-3 s — into one tracker:

| arm | wrong-slide | anticipation |
|---|---|---|
| Whisper alone (shipped) | **0.484** | +588 ms late |
| both, shipped model | 0.552 | +484 ms |
| both, advance 0.50 | 0.571 | **+176 ms** |
| both, advance 0.60 | 0.578 | **-278 ms (early)** |

**This is the first measured side-channel benefit.** The fast words move the
decision from 0.6 s late to 0.3 s *early* — the app anticipating rather than
catching up, which was the stated goal. It costs about 0.09 of wrong-slide.

Whether that trade is worth taking is a product judgement, but it is now a real
choice rather than a hypothetical, and the accuracy cost may be recoverable —
see below.

# What our phonetic matching actually does

## It is not phonemes, on either side

**sherpa does not emit phonemes.** Its vocabulary is orthographic BPE
word-pieces — `▁THE`, `ING`, `ED`, `S` — so the pipeline is still
audio -> letters -> Double Metaphone -> match, the same two-step loss the
original phoneme proposal was aimed at. A phoneme-output model would remove one
step; this does not.

## Robustness, measured

`phonetic_match_score` tiers: 1.0 identical, 0.9 same metaphone code, 0.7
secondary or cross match, and below that a Levenshtein fallback on **letters**,
capped at 0.6.

| perturbation | median score | fell out of the phonetic tiers |
|---|---|---|
| confusable consonant (p/b, t/d, s/z, f/v, k/g) | **0.90** | **0%** |
| dropped sound | 0.90 | 33% |
| **inserted sound** | **0.60** | **100%** |

**Confusable consonants are handled well, and for a good reason**: Double
Metaphone collapses voiced/unvoiced pairs by construction — d and t both code
to T, g and k to K, z and s to S, b and p to P. "praise"/"braise" and
"heart"/"heard" both score 0.90. So the confusion matrix the original proposal
asked for is, for the commonest consonant confusions, already there implicitly.

**Insertions are the weak spot.** Every inserted sound broke the code and fell
to letter-distance capped at 0.6. Dropped sounds mostly survive (vowels are
discarded anyway) but 33% break the code — "chases" -> "chses" gives KSS against
XSS and drops to 0.60.

That matters because insertion is the dominant ASR error here: Whisper's profile
on the goldens was **zero deletions**, with error entirely in substitutions and
insertions, and the fast channel fragments words badly.

## Where a real phoneme distance would help

Not in the confusable-consonant case, which Metaphone already covers, but in the
fall-through: once codes differ at all, the score collapses to *orthographic*
distance with a hard 0.6 ceiling, with no notion that XSS and KSS differ by one
sound. A graded distance over metaphone codes — rather than exact match or
nothing — is the targeted fix, and "SAYS" scoring 0.33 against "chases" is
exactly the case it would repair.

## Time

`phonetic_match_score` takes two strings and no timestamps. It has no time
dimension to be sensitive to, so time-stretch robustness is not a property of
the matcher at all — the tracker's dwell states carry tempo, which is why they
were tuned to a word rate in the first place, and why feeding a differently-paced
stream broke it.

---

# Phoneme matching, built and measured (2026-09-01)

Asked for two things: robustness to inserted and dropped sounds, and whether a
pronunciation dictionary is being used. The second answer was **no** — Double
Metaphone reads spelling and applies English spelling rules; it never looks a
word up. `research/spikes/spike_phonemic_match.py` changes both.

## Coverage makes it viable

| | in CMUdict |
|---|---|
| slide lyrics | **99%** of 919 |
| Whisper output | **100%** of 2372 |
| fast-channel output | **96%** of 929 |

Misses are British spellings (*saviour*), compounds (*ninety-nine*),
contractions (*ev'rything*) and ASR nonsense (*enig*, *samulay*).

## What it does

Phonemes from CMUdict — so *knight* and *night* are identical, and *through*
and *threw* are identical, which no spelling rule guarantees. Then a weighted
edit distance over the phoneme sequences, which handles insertions and deletions
by construction rather than falling off a cliff.

Substitution cost is graded by how the sounds are made:

| | cost |
|---|---|
| same sound | 0.00 |
| voicing only (P/B, T/D, S/Z) | 0.20 |
| vowel for another vowel | 0.30 |
| same manner *or* place (T/K, M/N, S/SH) | 0.60 |
| unrelated, or vowel for consonant | 1.00 |

Dropping or adding a **vowel** costs 0.4, a consonant 0.8 — sung vowels are the
least reliable thing an ASR reports, which the 2026-08-21 phoneme spike found
directly ("vowels wrong throughout").

## One thing that had to be walked back

A crude grapheme fallback for out-of-vocabulary words made things *worse*:
"graace" scored **0.25** against "grace" where Metaphone scored 0.90, because
guessing a pronunciation from letters produces nonsense. For a word the
dictionary does not know, spelling is all we have, and Double Metaphone is built
for precisely that. The scorer now uses the dictionary where it applies and
falls back to the shipped scorer where it does not — so it can never do worse.

## Results

| stream | scorer | wrong-slide | anticipation |
|---|---|---|---|
| Whisper only | metaphone (shipped) | 0.484 | +588 ms late |
| Whisper only | **phonemic** | **0.472** | +615 ms |
| both streams | metaphone | 0.571 | +176 ms |
| both streams | **phonemic** | **0.529** | **-220 ms early** |

Small gain on Whisper alone, larger on the combined stream — the expected shape,
since the fast channel's errors are near-miss real words that a graded distance
recovers and an exact code match discards.

**The combination now on the table**: both streams with phoneme matching gives
0.529 wrong-slide and fires **0.2 s early**, against the shipped 0.484 and
0.6 s late. Roughly 0.045 of accuracy for 0.8 s of timing.

## Not yet wired in

`cmudict` is a new dependency, and `phonetic_match_score` is the hottest
function in the system — every update scores every candidate word. The spike is
cached the same way, but this needs a perf check before promotion.

---

# Shipped: a toggle, not a default (2026-09-01)

The fast channel can now feed the matcher, from a checkbox in the **Fast
channel** debug tab. Off unless switched on.

| | wrong-slide | when the slide lands |
|---|---|---|
| Whisper alone (default) | **0.443** | 0.6 s after the band |
| + fast channel | 0.529 | **0.2 s before** |

It is a switch rather than a default because the right answer depends on the
room, and neither column is obviously better: a congregation reading ahead
notices a late slide, and notices a wrong one more.

**The toggle also re-tunes the transition model.** The forward filter's advance
rate is calibrated to a word *density*, and a second stream roughly doubles it.
Left alone, the posterior runs ahead of the band; `advance` moves 0.33 -> 0.50
while the channel is feeding, and back when it stops. That coupling is the same
one that made the fast channel look useless when it was first measured — the
words were fine and the feed rate was wrong.

Fast words go through the *same* handler Whisper's take, so they commit,
broadcast belief and record to goldens identically. Nothing downstream needs to
know which stream a word came from.


## Setup (2026-09-01)

    uv run --project sidecar python research/spikes/fast_channel/setup_fast_channel.py

Installs the runtime and downloads the model into the app data directory,
beside the library and the recordings, where the sidecar finds it with no
configuration. `AUTO_PRESENTER_FAST_MODEL` still overrides it for experiments.

**Two things this fixed.** The first attempt reported "not configured" even with
the environment variable set, because `sherpa_onnx` was never in the sidecar's
environment — every measurement had been run with an ephemeral `uv run --with`,
so the dependency existed only inside throwaway environments.

Adding it as a project dependency then failed differently: resolving through the
lock selects a macOS wheel whose `lib/` omits the bundled `libonnxruntime.dylib`,
and the import dies on `dlopen(@rpath/libonnxruntime.dylib)`. A direct
`uv pip install` picks the complete platform wheel. So it is an **optional
extra** installed by the setup script rather than a base dependency — which also
keeps it out of the shipped binary, appropriate for a feature that is off by
default.

**And the panel now says which thing is missing** — runtime, model, or a load
failure — with the command to fix it. "Not configured" sends people hunting
through settings that do not exist.

## What each path is actually fed (2026-09-01)

Capture hands both paths the same 100 ms frames. What they do with them differs
completely.

| | slow (Whisper) | fast (sherpa) |
|---|---|---|
| audio per call | **6 s** — the whole rolling window | **100 ms** — one frame |
| calls | every 1 s of audio, or slower when a pass runs long | every frame, 10/s |
| each second of audio is read | **~6 times** | **once** |
| emits | 0.54 events/s, 1.14 words/s | 0.44 words/s |
| words per event | median 2 | 1 |
| total over six goldens | 2375 words | 929 (39% as many) |

The 6x re-reading is the cost of Whisper being a batch model: it has no memory
between calls, so the only way to get a stable transcript is to re-transcribe
the recent past and diff. The streaming model keeps its own state, reads each
frame once, and never revisits — which is where both the low latency and the
low cost come from, and also why it cannot go back and correct itself.

That 39% is the number behind the density mismatch documented above: the
tracker's advance rate is calibrated to Whisper's word rate, and the fast
channel delivers well under half of it.

## Tuning the fast channel (2026-09-01)

Three axes swept on three goldens, against Whisper's transcript as reference.

**Chunk size changes nothing about what it hears.** Word count was *identical*
at 402 and word error identical at 0.87 across 50, 100, 200, 300 and 500 ms —
the model buffers to its own internal chunk size regardless. What it does change
is when a word surfaces:

| chunk | delay before a word appears | realtime factor |
|---|---|---|
| **50 ms** | **0.38 s** | 0.0202 |
| 100 ms | 0.42 s | 0.0191 |
| 300 ms | 0.50 s | 0.0185 |
| 500 ms | 0.60 s | 0.0185 |

So smaller is free latency, and 50 ms is now what ships — capture delivers
100 ms frames and they are split in half before feeding.

**Two knobs measured and rejected.** Beam search costs 35% more compute for
*worse* accuracy (0.88 against 0.87). Four threads cost 70% more for no change
at all — the same oversubscription that hurt Whisper on this machine.

### Overlapping windows: the honest version, and why it fails

Re-feeding overlapping audio to one stream would corrupt it — a streaming model
carries state, so it would hear each word several times. The equivalent that
*is* sound is several independent streams whose chunk boundaries fall in
different places. They do catch different words:

| | words | WER | wrong-slide with Whisper |
|---|---|---|---|
| 1 stream | 401 | 0.87 | 0.582 |
| 2 streams, 50 ms apart | 653 | 0.88 | 0.632 |
| 3 streams | 799 | 0.89 | 0.628 |

The extra words are real — 251 of them survive de-duplication — but they are
about as often noise as recovery, and end to end they make the tracker *worse*
while not improving timing. The fast channel's problem is not that it reports
too few words; it is that the ones it reports are wrong four times in five, and
running it more ways produces more of the same.

## The panel looked stalled, and why (2026-09-01)

Reported as the debug panel not refreshing at 100 ms. The two paths are not on a
shared loop, and neither runs at the panel's apparent rate:

| | runs at | broadcast at |
|---|---|---|
| Whisper | one pass per ~1 s of audio | when a pass yields new stable words, ~0.5/s |
| fast channel | **every 100 ms capture frame** | *was* only when a word was decoded, ~0.44/s |

So the fast channel really was ticking ten times a second, and the panel was
told about it twice a second at best — the latency and CPU figures froze between
words, which reads as a stalled panel while the channel is working perfectly.

Fixed by decoupling the two: words still go out the instant they arrive, and the
figures now refresh every 250 ms regardless. Four a second is smooth to the eye;
pushing all ten frames would be noise on the socket for no visible gain.

Worth noting the panel cannot show 100 ms *word* updates from either path, and
that is not a defect — Whisper produces about one word a second and the fast
channel about half that. What updates at 100 ms is the machinery, not the
speech.
