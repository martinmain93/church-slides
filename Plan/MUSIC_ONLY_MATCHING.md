# Music-only matching against a lead sheet — spike plan

Status: **RUN 2026-10-03** — results at the end of this document. The plan
below is kept as written, including its pre-registered criteria.

Question: given a song's lead sheet (melody + chords + lyrics) and a recording,
can we tell **which song** is playing and **where in it** we are, using only
musical features — no Whisper, no transcript, no word matcher?

First case: `What A Beautiful Name-lead-D.pdf` (SongSelect lead sheet) against
the approved golden `rec_20260818-214759_057` (the user's own recording, 450 s,
47 cues, stems and Basic Pitch / Beat This / BTC already cached beside it).

## Why this is not a re-run of the fingerprinting spike

`Plan/MUSIC_FINGERPRINTING.md` closed one door firmly: audio-to-audio chroma
fingerprints (CENS + 2DFTM) cannot tell a song's own sections apart at a live
window — 53% forced choice against the next section at 4 s. Three things are
different now, and each attacks a stated cause of that failure:

1. **A symbolic reference.** The old reference was "the chroma we stored last
   time this section played", built from the same muddy mix it was compared
   against. A lead sheet says exactly which note and chord belong on each beat.
2. **Melody, not just harmony.** The old failure was that worship songs reuse
   the same four chords everywhere. The *tune* does not repeat that way: the
   WABN verse and chorus share D–G–Bm–A but have different melodic contours
   and registers.
3. **Separated sources.** The old work was full-mix only. We now have Demucs
   stems, Basic Pitch on the vocal stem, BTC on the harmonic stem and Beat This
   on the mix — exactly what score following needs.

The shape of the problem also changes, from *fingerprint matching* to **score
following**: aligning an audio stream to a known symbolic sequence, which has a
mature literature (online time warping, HMM score followers).

## What the investigation found

### The lead sheet

- Vector PDF, 3 pages, rendered by **Verovio** (the `Leipzig` music font). The
  text layer is clean: `pdftotext -bbox` gives every syllable, chord symbol,
  section label (VERSE, CHORUS 1 & 2, INSTRUMENTAL, BRIDGE 1 & 2, CHORUS 3,
  ENDING), measure numbers and positions. The band-screens importer
  (`src/lib/chords/pdfLayout.ts`) already reads this layout for chords.
- **Notes are not text.** Noteheads, stems, beams, rests and accidentals are
  drawn as vector paths (657 / 555 / 273 drawings per page; only 7 music-font
  glyphs survive as text). So melody extraction needs either
  - **vector OMR** — staff lines are 1-segment paths, noteheads are a repeated
    filled outline; pitch falls out of notehead y against the staff, key
    signature is known (2 sharps); rhythm from beams/flags/dots/rests, with the
    lyric syllable under each note as an anchor; or
  - **raster OMR** (e.g. oemer, MIT) on a 300 dpi render; or
  - **a hand transcription** checked by the user.
- Form is non-trivial: two lyric rows per system (verse 1/2, chorus 1/2), first
  and second endings, a repeated instrumental, a D.S., and a separate ending.
  Unrolling it needs the roadmap, not just the page order.
- Printed tempo ♩=68, 4/4, key D.

### The recording, from the cached analysis

- **Same key as the sheet.** BTC chords: A 146 s, G 121 s, D 84 s, Bm 81 s,
  F#m 16 s — the chart's chord set exactly. The opening sequence (D, G, Bm, A,
  Bm, A, D, G, Bm, A, D …) follows the verse/chorus progression bar for bar.
- **Tempo ~62.5 bpm, about 8% under the printed 68.** Beat This reports a median
  of 125 bpm with a 62.5–130 spread: it is flipping between the quarter and the
  eighth. Chord changes settle it — G→Bm at the half bar takes 1.9 s, a 3.84 s
  bar. Any beat-synchronous feature must resolve that octave first.
- **The voice sits an octave below the written melody** (vocal-stem f0 median
  MIDI 54 = F#3 in verse 1, against F#4 on the page). Pitch has to be compared
  octave-folded, or with an octave offset estimated per performance.
- **Verse and chorus already look different in raw f0.** Verse 1 pitch classes
  spread over F#, E, A, G, D, B (10th–90th percentile MIDI 50–58); chorus 1 is
  dominated by F# (141 of 223 confident frames) in a narrow 52–55 band. That is
  the cheapest possible probe and it already separates the two sections that
  chords cannot.
- The performed order is **not** the lead sheet's roadmap: intro → V1 → C1 → V2
  → C2 (+ repeated last line) → instrumental → bridge → C3 → C3 → last-line
  tags → instrumental → bridge → C3. The matcher must allow jumps at phrase
  ends, as the slide tracker does.

### The ceiling music cannot pass

**Much of this song is musically identical under different words.** Chorus 1,
2 and 3 are the same melody and chords with *beautiful / wonderful / powerful*
swapped (slides 3 ≡ 10 ≡ 23, 4 ≡ 11 ≡ 24, …). Verse 2 is verse 1's tune, and
its slides break at different points (7 + 8 = slide 1's span). Bridge 1 and 2
share a tune too. No musical feature can tell those apart, so:

- Scoring must be on **musical positions**, not slide ids. Each slide maps to a
  span of the unrolled score; slides with the same span form one equivalence
  class, and a "correct" answer is any member of the true class.
- The interesting result is then what words *and* music do together: music
  carries position and phrase, words carry which pass. That is the fusion
  question for later, not this spike.

This is the same trap as the identical-slide scoring inflation recorded in
`eval_open_questions`, only far larger here.

## Plan

Each stage has its own check and a control, and later stages do not start until
the earlier one passes. Everything below is offline, in `scripts/` plus a
scratchpad, and touches no production code.

### Stage 0 — extract the reference from the PDF

Output: one `score.json` per lead sheet — notes `(measure, beat, duration, midi,
syllable, lyric_row)`, chord symbols with beat positions, section labels,
repeat/volta/D.S. markers.

1. Text layer (`pdftotext -bbox`): syllables, chords, section boxes, measure
   numbers, each bound to a staff system by y and to a beat by x.
2. Melody by **vector OMR** over PyMuPDF `get_drawings()` (spike-only
   dependency; AGPL, so it never ships): find staff lines, classify noteheads,
   read pitch from staff position plus key signature and accidentals, read
   duration from beams, flags, dots and rests, and check that every bar sums to
   four beats.
3. **Gold to check it against:** a hand transcription of the 47 bars, written
   by me from the rendered pages and spot-checked by you. Report pitch and
   onset accuracy of the automatic extraction against it.

Pass: ≥ 95% of notes right on pitch and onset, every bar sums to 4/4. If vector
OMR stalls, fall back to the hand transcription and keep going — it is the
matching that is the research question, not the OMR.

### Stage 1 — unroll the form and bind slides to it

1. Unroll the roadmap into the reference performance order: repeats, the two
   lyric rows, endings 1/2/3, the D.S., the ending.
2. Bind each library slide to a span of unrolled score beats by **word-stream
   alignment** of slide words to syllables (the band-screens alignment, so line
   breaks and repeats don't matter).
3. Collapse spans to **musical phrases** — the unit the matcher tracks — and
   derive the slide equivalence classes from them.

Check: every one of the 27 slides lands on a span; the classes come out as
predicted above (choruses collapse 3-way, verses 2-way with different breaks).

### Stage 2 — does the audio agree with the score at all?

Before any matching, measure the features against an **oracle alignment**.
The golden's cues plus the stored Whisper word times are used here as *ground
truth only*, never as matcher input: each sung word maps to its syllable, so we
know which note the singer should be on at every voiced instant.

Measure, per section:

- **Melody:** fraction of confident vocal-stem f0 frames within ±1 semitone of
  the expected note, octave-folded; and the same against a *wrong-section*
  note (the forced-choice control).
- **Chords:** BTC on the `other` stem vs the expected chord, as root + maj/min
  (the chart's extensions and slashes reduced, which the chord audit found
  necessary), read with BTC's ~0.74 s lag.
- **Tempo/meter:** recover ♩≈62 from Beat This plus chord changes; downbeats
  against bar lines.

Known noise in that ground truth: Whisper word starts run 0.25–0.75 s early,
and cues carry operator scatter. Score in windows, not instants.

Pass: melody matches the right section clearly more often than the adjacent
one. If melody is near chance here, nothing downstream can work and we stop
and look at the pitch tracker instead.

### Stage 3 — offline alignment with the order given (ceiling)

DTW of the audio feature sequence against the reference unrolled in the
**performed order** (from the golden). Answers: given the right sequence, how
precisely do music features place us within it? Report position error in beats
and seconds, per feature arm (melody / chords / both).

### Stage 4 — offline, order unknown

HMM over `(phrase, beat in phrase)`, tempo as a slowly varying latent,
transitions free at phrase ends (any phrase may follow any other), Viterbi
over the whole recording. This is the real question in offline form.

Report, against the musical-class ground truth:

- phrase-class accuracy over time (and slide-class accuracy derived from it);
- position error within the phrase when the class is right;
- **controls:** a reference with phrases shuffled, the same with melody
  pitch-shifted by a tritone, and chords-only (to show melody earns its place).

### Stage 5 — causal

Forward filter only, no look-ahead, as a live system would run. Add what
offline hid: the vocal stem is ~2 s behind real time (`realtime_stems.py`),
BTC reads ~0.74 s late, Basic Pitch windows at 2 s. Report:

- **time to lock** after each phrase starts — the number that matters, set
  against Whisper's measured ~2.85 s median / 8.8 s p90 delivery lag;
- accuracy and position error as in stage 4;
- behaviour through the instrumental (slide 0), where the vocal stem is silent
  and only chords and beats speak.

### Stage 6 — which song (cross-song control)

Only one lead sheet exists, so:

- WABN reference against all 17 golden recordings: does WABN audio score
  clearly above every other recording, and how many seconds does it take?
- WABN audio against the **chord charts** of the other goldens (chords-only
  arm, since those have no melody) — all 7 approved goldens have charts.
- Ask for 2–3 more SongSelect lead sheets for other goldens to make the
  melody arm a real n > 1 (see questions).

### Ablations, across stages 3–5

melody / chords / both · stems vs mix · key known vs searched over 12
transpositions · octave folded vs estimated offset · beat-synchronous vs
frame-level.

## Pre-registered success criteria

Written down now so the result cannot move the bar:

| | criterion |
|---|---|
| stage 2 | melody picks the true section over the adjacent one ≥ 70% at a 4 s window (the fingerprinting spike got 53%) |
| stage 4 | phrase-class accuracy ≥ 80% of sung time, and beats both the shuffled and chords-only controls by ≥ 15 points |
| stage 5 | median time to lock ≤ 3 s from phrase start, i.e. no worse than Whisper delivers words |
| stage 6 | WABN audio ranks the WABN reference first against every other recording, by a margin outside the spread of the others |

Stage 5 is the one that would make this useful in the app: a music-only signal
that knows "chorus, bar 2" sooner than the words arrive serves the
side-channel goal (speed, not identity).

## Risks

- **n = 1.** One song, one recording, the user's own and comparatively clean.
  Anything we learn about thresholds is fit to it. Held-out tuning is
  impossible with one song, so tune nothing on stage 4–5 numbers until a
  second lead sheet exists; report raw.
- **Room audio.** This recording is a home recording, not a live room mic.
  Demucs on a real room capture is unmeasured.
- **Arrangement drift.** Bands don't play the lead sheet: added intros, tags,
  ad-libbed melody, harmony vocals on the vocal stem. The HMM's free
  transitions cover structure; melodic ad-libs are noise we will measure in
  stage 2.
- **Licensing, for later only.** Basic Pitch is Apache-2.0, BTC MIT, Beat This
  MIT; PyMuPDF (AGPL) and Demucs-MLX are spike-only. SongSelect lead sheets
  come under the church's CCLI SongSelect licence, so any shipping version
  reads the user's own downloads and never redistributes them.

## Questions for discussion

1. Are you happy for the **hand transcription to be the gold** (I transcribe
   from the PDF, you spot-check a few bars), with vector OMR measured against
   it — or should automatic extraction be a hard requirement of this spike?
2. Can you download **2–3 more SongSelect lead sheets** for songs we have
   goldens of (I Surrender All, Nothing But The Blood, Reckless Love, King Of My
   Heart)? That turns stage 6 from a control into a real test.
3. Is **musical-class scoring** the right target (counting chorus 1/2/3 as one
   answer)? I think it has to be, but it means this spike alone never picks a
   slide.
4. Stage 5 uses stems, which add ~2 s of latency. Do we go straight to the mix
   for the causal stage if stage 4 shows stems are not essential, or measure
   both?

---

# Results (2026-10-03)

Code: `research/spikes/spike_leadsheet_omr.py` (stage 0) and `research/spikes/music_only/`
(`score.py` unroll + slide binding, `gt.py` ground truth, `features.py`,
`reference.py`, `follow.py` the matcher, `evaluate.py`, `cv.py`
leave-one-song-out, `stage2.py`, `stage4.py`, `stage6.py`, `latency.py`).
All spike-only; nothing touches the app.

Scope grew in one useful way: the user supplied lead sheets for four more
goldens (Reckless Love, King Of My Heart, I Surrender All, Nothing But The
Blood), so every stage ran on five songs, and model settings were chosen
leave-one-song-out rather than on WABN.

## Stage 0 — extraction: done, all five sheets

Vector OMR straight off the PDF drawings (Verovio/Leipzig: noteheads, stems,
beams counted by *level*, flags, dots, rests incl. 16th, ties, duplets, key
signatures, cue-size second voice, repeats, voltas, segno/D.S./coda, To
Coda), text layer for lyrics, hyphens, chords (music-font ♯/♭ joined in),
section labels. Every bar of every sheet adds up to its metre (47 + 109 + 63
+ 16 + 16 bars, 0 flagged); no syllable on a tied note; no note with two
syllables. WABN checked bar-by-bar against an overlay of the extracted notes
on the page — 0 pitch or rhythm disagreements, which is the gold check the
user accepted; overlay samples with note names sent for spot-checking.

Traps worth not repeating: Verovio splits a secondary beam into segments that
meet at a stem (count levels, not boxes); tie curves must be filtered to
their own staff; chord ♯/♭ are music-font glyphs in separate spans with
oversized boxes; the text between two systems belongs to the system above
if it is lyrics and below if it is chords or a styled mark — no fixed
boundary works (chord rows sit 9-46 pt above their staff).

## Stage 1 — unroll and bind: done

WABN unrolls to exactly the sheet's roadmap (91 bars). Slides bind on letters
with spaces removed at >= 0.93 similarity — loose character matching let
"beautiful" match "wonderful", so a chorus bound to all three passes. Hymn
verses the sheet does not print take the music of the same library section
type. Bars that differ only by a tie are the same music. WABN: 47 written
bars are 33 distinct; which slides share music is as predicted (each chorus
line 3-way, verse 1 = verse 2, the two bridge halves).

## Stage 2 — does the audio agree with the sheet: PASS (where the key matches)

With the transcript-derived alignment (scoring only), WABN:

| | vocal stem | mix |
|---|---|---|
| strongest pitch class = sheet note | 55% (chance 8%) | 26% |
| BTC chord = sheet chord | 84% | 84% |
| forced choice vs next different slide, 4 s, melody | **74%** (bar: 70%) | 56% |
| same, chords | 93% | 95% |

Reckless Love: melody 89% at 4 s. Three of the five performances are **not
in the sheet's key** (offsets 11, 8, 9 semitones), which reads as 0% chord
agreement until the key is found — so the key became part of the model.
Key from the chord histogram (audio only) is right for 4/5 songs within
30 s; the medley is the exception because its first song is in another key.
The second WABN bridge shows the other arrangement risk: the singer takes a
different line (offset 5-8 semitones), and melody-only falls apart there.

## Stages 3-4 — offline following: position yes, the 80% bar only with hymns

The matcher (`follow.py`): HMM over (eighth-note cell of the unrolled sheet,
tempo 40-130 bpm), free jumps at bar lines to any slide or section start, a
bank of filters over the 3 keys the chord histogram suggests (the key whose
filter explains the last 30 s best wins). Evidence: Basic Pitch salience at
the sheet's note (octave-folded log ratio) and/or BTC's probability of the
sheet's chord. Settings chosen **leave-one-song-out** (`cv.py`).

Held-out, offline (forward-backward), mean of five songs:

| arm | right slide group | within 1 beat |
|---|---|---|
| **melody only, vocal stem** | **81.5%** | **69.3%** |
| vocal melody + mix chords | 71.3% | 65.2% |
| melody + chords, stems | 67.7% | 60.1% |
| chords only, stems | 57.9% | 45.3% |

**Read the slide column against chance**, which is 11% / 13% / 25% / 34% /
63% (WABN, RL, KOMH, ISA, NBTB) — a hymn reuses one tune for everything.
Melody on the three modern songs: 71.5 / 70.5 / 81.6% — far above chance,
but two of three miss the pre-registered 80%. Position-within-a-beat is the
cleaner number (chance 1-8%). Bars-shuffled control: 3.7 / 1.3 / 0.7%
within a beat on the modern songs. The tritone control is *not* clean: the
key search can partly undo it (tritone + a +1 key candidate = a fifth, which
keeps a diatonic tune mostly in scale) — ISA keeps 80%.

**Melody carries it; chords hurt on average.** Chords are excellent on WABN
(84% frame agreement) and poor elsewhere (RL "other" stem 63%; NBTB 8%
within a beat), and at equal weight a confidently wrong chord stream drags
the fusion down. Tempered evidence (w = 0.4) was chosen held-out on nearly
every song, which is the frames-are-not-independent correction.

Verdict against the pre-registered bar: **narrowly not met** on the songs
where it is meaningful; met on the five-song mean only because of the hymns.

## Stage 5 — live: about half the slides, fast

Causal filter, read one latency behind the room (vocal stem: 3 s) and
projected forward at the filter's own tempo. Held-out means: melody 40.4%
within a beat, slide group 64.6%. On WABN, 68% offline becomes 45% with
zero latency and 36% at 3 s — **most of the loss is no look-ahead, not the
stem latency.**

Lock = from the first moment the answer is right *and stays right for 80%
of the rest of the slide*. A looser "right for 1 s" definition failed its
control (shuffled bars still "locked" 49% of slides, median 0.8 s), so it is
not reported as a result.

| | slides that lock and stay | median | p90 |
|---|---|---|---|
| modern songs, real | **55%** of 95 | **0.6 s** | 4.7 s |
| modern songs, bars shuffled | 14% | 2.6 s | 20.4 s |
| hymns, real | 92% of 26 | 0.3 s | 6.8 s |
| hymns, bars shuffled | 35% | 11.8 s | 19.6 s |

Whisper delivers words at ~2.85 s median, p90 up to 8.8 s. So for about half
of modern-song slides, music alone knows the place in the tune within a
second of the first sung word — the side-channel speed the project wants —
and for the other half it does not settle. Pre-registered (median lock <= 3 s):
met for the slides that lock; 45% never do.

## Stage 6 — which song: three of five, and no clean "not mine"

Melody arm, every sheet in all 12 keys, causal, 30 s trailing window:

| recording | own sheet ranked first (sung time) | first held for 5 s |
|---|---|---|
| I Surrender All | 98% | 5.4 s |
| Nothing But The Blood | 86% | 17.8 s |
| What A Beautiful Name | 82% | 14.0 s |
| King Of My Heart | 47% | 11.5 s |
| Reckless Love | 45% | 47.9 s |

The medley's other song is not claimed (best evidence 0.63/s, the
negatives' median). But songs with a sheet (median best evidence 1.14/s,
min 0.90) and the 12 goldens without one (median 0.63, **max 1.18** — Death
Where Is Your Sting reads as WABN) overlap, so "this is none of my songs"
cannot be thresholded from melody evidence alone. Pre-registered (WABN first
against every other recording, clear margin): WABN ranks first 82% of sung
time, but the margin is not outside the negatives' spread.

## What this means

1. **A lead sheet's melody is a real music-only position signal** — the
   thing chroma fingerprints could not do (forced choice 74-89% at 4 s
   against 53%), and it is fast when it works (median 0.6 s live).
2. It is a *companion* to words, not a replacement: it cannot tell chorus 1
   from chorus 3, and live it settles on only about half of modern-song
   slides. The natural use is the side channel the project already wants —
   timing and "which line of the tune" — with words choosing the pass.
3. Song identity from music alone is not ready; words already do it well.

## Next experiments, in order

1. **Fusion with the word tracker** — the real question now: does adding
   "position in the tune" (with its confidence) to the existing matcher move
   wrong-slide or anticipation on the goldens? Music supplies line and
   beat; words supply the pass.
2. **Why half the live slides never settle.** Candidates: the jump prior
   (uniform over all targets) and sections whose tune repeats inside the
   section; a fixed-lag smoother (1-2 s) might recover much of the offline
   gap at a small latency cost.
3. **Chord weight learned per song from its own agreement** (BTC is
   excellent on some recordings and poor on others) instead of one weight.
4. **Room audio.** Every recording here is a studio or home recording.
5. Lead sheets for more songs; the CCLI SongSelect licence permits the
   church's own downloads, and stage 0 reads them without hand work.

---

# Fusion into the word tracker (2026-10-03)

Next experiment 1 above. `research/spikes/music_only/fusion.py`, transcripts from
`transcribe_live.py`.

**Setup.** The production engine (`decision.production.build_engine`, default
tunables) on live-windowed MLX `small.en` transcripts of the five goldens,
each event carrying its measured `available_at`. After every transcript event,
before the scheduler sees the belief, the forward filter's states are
reweighted by the causal music filter's belief about that moment:

    alpha(song, slide, word) *= (eps + r(slide, word)) ** lam

- r = the music filter's probability (canonical: every copy of identical music
  gets it, so the words still choose the pass), smoothed ±1 beat, relative to
  its mean over the song's words.
- The song's total share is preserved: music says where in the tune, never
  which song (stage 6).
- Causal: the music belief comes from audio up to `available_at - 3 s` (stem
  latency), projected to the event's `t_end` at the music filter's own tempo.
- lam chosen leave-one-song-out.
- Scored with the harness's own `wrong_slide_rate` (forgiving mode) on the
  **availability clock** — the sung clock reads every decision ~2.6 s early,
  because the scheduler's lead deliberately covers delivery lag.

**Result: no gain.** Five variants, declared before running, all reported:

| variant | held-out mean wrong-slide | better / worse |
|---|---:|---|
| production (no music) | **0.389** | — |
| melody, per word | 0.388 | 3 / 2 |
| melody, per word, gate 0.5 | 0.396 | 0 / 1 |
| melody, per slide | 0.395 | 2 / 2 |
| melody, per slide, gate 0.5 | 0.386 | 2 / 1 |
| mix chords (0.74 s lag), per word | 0.398 | 1 / 2 |

All within ±0.01 of the baseline, inside the noise this project has learned
to expect (differences under ~0.02 do not survive). The first, ungated version
was 0.391 before the song-share fix.

**Under the mean, two consistent patterns:**
- **King Of My Heart** (repeated short-tag choruses, the words'
  worst song here at 0.464) improves in several variants: 0.363 (per word,
  lam 1), 0.357 (per slide, lam 2), 0.387 (mix chords, lam 0.25). Leave-one-out
  never picks those strengths, because they hurt the others.
- **Nothing But The Blood** gets worse almost everywhere (0.476 -> 0.51-0.55).
  The hymn is a third of a medley recording; for the other two thirds the
  music filter's belief is meaningless, yet it still reshapes where the
  tracker thinks it is inside the hymn, and re-entry suffers. Holding the
  song's share fixed recovered only a little of that.

**Reading.** Tune position, injected as a likelihood on the word tracker's
states, does not improve slide accuracy on these five songs. Where words are
already right (WABN 0.28, ISA 0.24) there is little to fix, and an independent
signal right only about half the time live adds about as much error as it
removes. The King Of My Heart result points at where it could matter (repeated
sections the words cannot place) but is one song.

**What would be worth trying instead:**
1. **Apply music only to the song the words already believe in**, gated on the
   tracker's own song share — the medley damage is avoidable by construction.
2. **Use music for timing, not position**: the scheduler's "this line is
   ending" projection (`seconds_remaining`) from the tune's bar position, which
   the stage-5 lock times suggest arrives well before the words.
3. **More songs like King Of My Heart** before believing or dismissing the
   repeated-chorus effect.

## Why fusion cannot move the number (diagnosis, 2026-10-03)

Tools: `diag_fusion.py` (where the wrong time is + oracle bound),
`diag_flips.py` (why perfect position flips a right belief), `diag_coverage.py`
(does the true slide get any tune support), `diag_backward.py` (what the
screen-ahead seconds are). All on the room's clock, production engine, the
same live MLX transcripts, the scorer's own excusals and identical-slide rule.

**1. Where the production app's wrong time is** (five goldens, baseline 0.389):

| bucket | share | meaning |
|---|---:|---|
| scheduler | **52%** | the tracker's latest available belief is the right slide; the screen is not showing it |
| stale | 13% | the belief was right for the audio it had; the band moved on since (delivery lag) |
| identity | 35% | the belief is wrong even for the audio it had heard |

**2. The scheduler bucket is almost entirely the screen sitting *ahead* of a
right tracker** (91%; below-threshold confidence 5%, other song 4%). Of those
seconds:

- **44% overshoot**: the screen moved to the next slide before the band got
  there. Episodes of 1-3 s, tracker 85-94% confident in the current slide.
- **25% repeats not followed**: the band went back to an earlier slide, the
  tracker followed (confident >= 0.6 in most), the screen stayed for 8-22 s.
  Almost all one slide back — the case the one-slide backward rule blocks.
- **31% other**, mostly an overshoot of a different shape: the band repeats a
  line (King Of My Heart sang the bridge line, slides 7-10 with identical words,
  for 55 s) while the screen anticipates the slide *after it in slide order*,
  which the band does not sing for minutes. Tracker right and confident the
  whole time.

So **about half of all wrong time is the scheduler's own moves against a
tracker that is right** — anticipating into printed order, and not following
repeats. No better "where in the song" signal touches it.

**3. The identity bucket is mostly out of music's reach too.** Of identity
seconds, only 22% are between slides with *different* music at that moment;
11% are between slides with the same music (no tune signal can separate them)
and 58% have no comparable sheet position (instrumentals, before the first
word, the medley's other song). So even a perfect tune signal addresses about
0.35 x 0.22 = **8% of wrong time**. Live music was right on 30% of identity
seconds and wrong on 34%, so the real signal nets roughly nothing — which is
what the five variants measured.

**4. The oracle bound confirms it, and exposed two mapping bugs.** Perfect tune
position (ground truth, no lag) through the same fusion: best 0.376 (lam 0.5),
*worse* at higher strength (I Surrender All 0.244 -> 0.467 at lam 1). Every
damaging flip had the true slide receiving **no** tune support. Cause, measured
(`diag_coverage.py`): the true slide's mapped words miss the true position on
~20% of events in I Surrender All and Reckless Love (97-99% elsewhere):
- unmatched words at the end of an unprinted hymn verse collapse onto the last
  matched word, leaving the rest of the line unmapped;
- words are mapped at their onset ±1 beat, so held notes and gaps fall between
  words.
These are real bugs in `fusion.word_cells`, but they explain the oracle
*hurting*, not the low ceiling — the ceiling is points 2 and 3.

**5. Not the cause:** the tracker's word index agrees with the tune's to a
median 0.7 words (2+ words off on 11% of events), and running the app's stall
watchdog (`engine.tick` on the audio clock, which the eval harness never
calls) changes wrong-slide by <= 0.014 on every song.

**What this says to do next (not tried):** the biggest single lever in this
data is the scheduler, not the tracker:
- **repeats**: a confident one-slide-back belief held for many seconds is
  being refused; tune position (start of the line vs its end) is exactly the
  evidence that tells a real repeat from lag, so this is where music could earn
  its place — as a gate on the backward rule, not as tracker evidence;
- **anticipating into printed order**: when the band is repeating a line (or a
  section), the next slide in library order is not where they are going;
- **overshoot** of 1-3 s: a lead-time calibration question on the room's clock.

## The scheduler, traced (2026-10-03)

Tools: `diag_scheduler.py` (wraps `on_position`: every belief, projection,
screen and decision), `diag_remaining.py` (is time-left biased?),
`diag_timing.py` (words vs music vs sheet time-left), `exp_scheduler.py`,
`exp_timing.py`. Production engine, live MLX transcripts, room's clock.

**Ruled out first:** the forward tracker does *not* re-anchor on the
scheduler's commits (`ForwardTracker.reanchor_on_commit = False`), so an
anticipation does not overwrite the tracker's belief. That also removes the
reason the duration-based backward escape was reverted on 2026-08-21 (it
re-anchored the particle filter).

**What puts the screen ahead of a right tracker:**

1. **Anticipation commits** cause 278 of ~320 screen-ahead seconds. The lead
   used is a median 4.0-4.7 s while measured delivery lag is 2.73 s: the lead
   is `lag + the whole gap to the next update`, the rule for *never being
   late*. Wrong-slide scores early and late alike; the error-minimising lead
   is `lag + gap / 2`. About half the anticipations showed 1-2.7 s early.
2. **The one-slide re-entry escape never fires on live ASR.** By the time the
   tracker notices a re-entry its position is 0.4-0.6 into the slide; 0 of 21
   refused confident one-back beliefs were <= 0.25. It was set on cached
   transcripts, where nothing is 2.7 s late.
3. **Printed-order anticipation through repeats**: King Of My Heart 7 of 13,
   Nothing But The Blood 8 of 20 anticipations went to a slide the band did not
   sing next, and (2) means none is ever taken back.
4. After fixing (1), **72% of the remaining screen-ahead time is the
   projection itself still saying "next slide"** (tracker 64-85% through the
   slide, 1.7-2.4 s left by its estimate). The time-left estimate is
   **unbiased** (median error ~0 in every position bin) but **noisy** (IQR
   about ±1.5 s): the screen moves whenever it reads short.

**Corrections, derived not swept:**

| | mean wrong (5) | timing | better / worse | new golden (held out) |
|---|---:|---|---|---:|
| production | 0.389 | -373 ms | — | 0.589 |
| **lead = lag + gap/2** | **0.353** | +143 ms | **5 / 0** | 0.580 |
| retract after lead + gap | 0.383 | -373 ms | 1 / 1 | — |
| both | 0.345 | +143 ms | 5 / 0 | — |

The half-gap lead is the one clear result: every song better, -0.036 mean, a
one-line change of formula. Retraction mostly does not get the chance —
the projection keeps re-endorsing the anticipated slide (point 4) — and
helps only King Of My Heart.

**Music for *when*, measured:** time left in the slide, second half of the
slide, against what actually remained:

| estimator | IQR | within ±1 s |
|---|---:|---:|
| words (scheduler now) | 2.4 s | 46% |
| live music (3 s lag, projected) | 1.5 s | 60% |
| perfect sheet position | 1.1 s | 72% |

But fed into the ending test (equal-weight average with words, fixed in
advance) it nets **0.350 vs 0.353** — 3 better, 1 worse; music alone is worse
(0.391). King Of My Heart goes 0.426 -> 0.464, with anticipation turning 2 s
late. **Open, not yet diagnosed**: the music estimate runs ~0.5 s long on
average, and on the song where live music is confident only 24% of the time
a confidently-placed but wrong copy of a repeated line would push "ending"
later. Measure that before changing anything.

**The new golden (`rec_20261002-185523_091`, four-song live set, words hard
to hear):** production 0.589 with the screen +2.4 s late; half-gap lead
0.580. No lead sheets yet for its songs (Awesome God, Worthy, Doxology,
O Come All Ye Faithful), so no music arm.

## Why music did not help timing, and the wordless stretches (2026-10-05)

**Recorded, not applied:** lead = lag + gap/2 (0.389 -> 0.353 on 5/5,
`exp_scheduler.py`). Kept as the base for everything below; production is
unchanged at the user's request.

New lead sheets (Awesome God, Build My Life, Doxology, O Come All Ye
Faithful) for the new live-set golden needed three more notation features:
time-signature digits read per bar (the Doxology alternates 4/4, 2/4, 3/4 —
42 flagged bars -> 1), whole notes, and "(2." verse labels. All nine sheets
now extract with 0-5 flagged bars, checked by overlay.

### Why music time-left did not help (diag_timing*.py, diag_timing_effect.py)

1. **It is a real but modest lever.** With the half-gap lead, time-left from
   *perfect* sheet position (ceiling) takes wrong-slide 0.353 -> 0.329 and
   on-time slide changes (within 1 s of the band) 23% -> 41%. Live music:
   0.350, 27%.
2. **Per song, music timing is better on four songs and much worse on one.**
   Second-half-of-slide IQR, words vs music: WABN 1.6 / 1.1 s, Reckless Love
   1.8 / 1.2, I Surrender All 4.2 / 2.8, NBTB 5.1 / 2.4, but **King Of My
   Heart 2.9 / 5.5** — the song where live music is confident only 24% of the
   time. The pooled IQR (2.4 vs 1.5) hid it.
3. **The scheduler can only act on a transcript update.** Music moves about
   11 commits per song, each by exactly one update (~1-2 s), earlier about as
   often as later, so on WABN they cancel (0.270 both). A better estimate can
   only choose a neighbouring update; it cannot place a change between them.
   Ruled out on the way: music being absent near slide ends (it has an
   estimate 77-81% of the time there; "projected past the slide's end" 4%).

### Wordless stretches (diag_drought.py, diag_drought_music.py)

- Lateness grows with how long the words have been silent: within 1 s on 47%
  of changes after < 2 s of silence, 25% after >= 4 s, and 40% are never
  reached. **On the new set 38% of slide changes follow >= 4 s without words**
  (0-15% on the other goldens).
- **Two different kinds of silence.** On the five goldens a drought before a
  change is mostly an *instrumental* (voice present 19-27% vs 55-72% while
  words flow): a vocal-melody matcher is as blind there as Whisper, and only
  band features (chords, the sheet's instrumental bars) can speak. On the new
  set it is *singing Whisper cannot make out* (voice present 44% in droughts,
  43% otherwise): the melody is there to use.
- **Music does know a new line has started**, sooner than the words screen:
  within 1 s of the cue on 54% of changes vs 44% (control, a wrong slide that
  is not the one being left: 17%); after >= 4 s without words 50% vs 25%
  (control 19%); new set 38% vs 22% (control 15%). An earlier control that
  included the slide being left read 59% on the new set and wrongly
  suggested no signal.

### Acting on the music clock (exp_music_clock.py) — and why it did not pay

A minimal rule: between transcript updates, if live music has sat 1 s in the
next slide's music with >= 50% of its mass, show it. First version fired
mid-verse on the hymn (every verse is one tune: 18 of 20 moves wrong);
corrected to "in the next slide's music *and out of the shown slide's*".
Result 0.353 -> 0.360. Audited: the WABN moves land 0.5-3 s *early* (a line's
music starts at its pickup notes in the previous bar, plus one beat of
tolerance and the 3 s projection), and on the new set the rule almost never
gets a chance:

| new set, at its 32 slide changes | |
|---|---:|
| screen on another song, or nothing shown | 31% |
| band not going to the next slide in order (Awesome God loops 2 -> 1) | 22% |
| screen already right | 22% |
| music not confident | 12% |
| music confident and right — the rule could act | 3% |

### The new set's real problem is the song, not the line

Of its 266 wrong seconds: 35% wrong song on screen with the tracker also on
the wrong song; **28% wrong song on screen while the tracker is on the right
one** (the cross-song guard — >= 4 words, 0.70, 3 observations — rarely gets
enough clear words); 37% right song, wrong slide. The screen opens on the
Doxology, reaches Awesome God 53 s late, and never shows the Doxology or
O Come All Ye Faithful when they are sung.

Within the known setlist, live melody evidence names the right song 65-71% of
sung time (20-30 s window; chance 25%), holds Awesome God within seconds of
its start (screen: 53 s), the Doxology after ~21 s and O Come All Ye Faithful
after ~9 s (screen: never), and is slower than words only on Build My Life
(~20 s vs 6 s). One set, four song changes — suggestive, not established.

### Where this leaves music

- *Which line*: words already do it; music adds little (fusion: no gain).
- *When*: worth ~0.02 at the ceiling, but only usable if the scheduler can act
  between transcript updates, and acting needs the line-start defined at the
  first sung *word*, not the pickup notes.
- *Which song, when the words are hard to hear*: the clearest opening found
  so far — exactly the new golden's failure — but measured on one set.
- *Instrumentals*: need a band-feature arm (chords / instrumental bars), not
  the vocal melody.

## Song changes, traced (2026-10-05)

Tools: `diag_songs.py` (per-update trace of the cross-song gates, with the
singing detector run and pushed onto the scheduler exactly as
`server._observe_singing` does — the plain eval harness leaves the talking
score at 0, so that gate never acted in any earlier number), `diag_set_songs.py`,
`diag_song_music.py`. Recordings with song changes: the new live set
(4 songs, family service, heavy chatter) and the NBTB medley.

**The gates, in order:** conditional slide confidence >= 0.70; talking score
< 0.5; new song's share of the belief >= 0.6; 3 consecutive updates; >= 4
words. Nothing on screen yet: anything may be shown.

**What holds the screen on the wrong song:**

1. **The slide-confidence gate decides a *song* question with *slide*
   evidence.** Entering a song, the tracker is certain of the song (share
   0.92-0.99) but split between lines whose words are shared (Build My Life
   across several lines; NBTB verses that all contain the refrain; the
   Doxology's two "praise..." lines). The gate keeps the old song — certainly
   wrong — rather than show the likeliest line of the new one. Medley return
   at 547 s: tracker immediately, screen 55 s later (18 refusals); Build My
   Life at 281 s: screen 42 s later.
2. **Hard-to-hear singing reads as talking.** The talking score is inferred
   from word-match quality; during Awesome God its median is 0.77 (gate 0.5).
3. **The opening is a guess from chatter**: with nothing shown, the screen
   committed to the Doxology at 5 s from a children's story ("an eagle!").

**Decoupling the song decision** (cross-song confidence 0.70 -> the ordinary
0.40, song share / streak / words / talking kept): medley 0.481 -> 0.448
(return shown at 558 s instead of 602 s; the early jump that motivated 0.70
does not recur now that the song-share gate exists); single-song goldens
unchanged; live set 0.552 -> 0.583 — the Doxology now appears (late), but a
**false switch to Build My Life at 248 s**, 33 s before the band. The 0.70
gate was doing two jobs: blocking ambiguous-line entries (wrong) and,
incidentally, some false song changes (right).

**Where music fits:** within-set melody evidence at each screen switch —

| switch | genuine? | music's margin for the new song |
|---|---|---|
| 5.5 s -> Doxology | false (chatter) | -0.01/s |
| 143.8 s -> Awesome God | yes | +0.17/s |
| 247.7 s -> Build My Life | **false** | **-0.18/s** (music firmly on Awesome God) |
| 323.0 s -> Build My Life | yes | -0.04/s |
| 638.4 s -> Doxology | yes | +0.13/s |

Music would have vetoed the false switch and backed two of three genuine
ones; it is strong on Awesome God and O Come All Ye Faithful (right 93% /
100% of their sung time) and at chance on Build My Life and the Doxology
(54%). Six switches, two false: a direction, not a result.

**What this points at:** separate the song decision from the slide decision,
and give it song evidence that does not depend on clear words — music's
within-set song evidence as a veto/confirmation, and a singing detector that
listens for pitched voice (the vocal stem) instead of inferring singing from
word-match quality. Both need more multi-song goldens to be measured on.

**Asked mid-investigation — are words, melody and chords aligned to the lead
sheet together?** No. Melody and chords are, inside the music filter (state =
beat position in the sheet). Words go through the word tracker, whose state is
(slide, word index) with no sheet timing; the fusion experiments joined the
two afterwards. A joint score follower — one beat-position state, each Whisper
word scored against the syllable at its own timestamp, pitch and chord at
theirs, with evidence placed at audio time despite 0.7-3 s of differing
latency — would give tempo, beats-to-next-line, the pass (from words) and the
place in the line (from melody) in one belief, and could act between word
updates. Not built.

---

# Spike: one follower for words, melody, chords and beats (2026-10-05)

Code: `joint.py` (the follower), `lead.py` (sheet roadmap reference),
`segref.py` (slide-segment reference), `recorded.py` (reference from a recorded
service), `run_joint.py`, `run_recorded.py`, `fast_words.py`,
`bench_latency.py`, `diag_refs.py`.

## Design, with the no-lead-sheet future built in

The follower sees an interface, not a lead sheet: cells, tempo range, slide
segments with an order prior, and one scoring function per source (melody,
chords, words; beats reserved). Two references implement it today:

* **lead sheet** — one segment per *library slide*: its own words, placed on
  the rhythm of its line from the sheet, with the sheet's notes and chords;
  the sheet's roadmap and library order as the order prior;
* **recorded service** — one segment per operator cue of a past performance,
  0.1 s cells, expecting that performance's own vocal pitch, chord
  probabilities and transcript words; the operator's slide sequence as the
  order prior; tempo as a ratio (0.75-1.33x). This is the "record a set,
  let the operator drive, store our own golden" path.

Every piece of evidence is placed at its *audio* time and arrives when it
really would (Whisper words at their measured `available_at`, the vocal stem
3 s late, mix chords 0.74 s). At each read-out (2 Hz) the follower re-filters
the last few seconds from a checkpoint with whatever has arrived, so a late
word corrects the past instead of being dropped. The screen shows the slide
with the most belief.

## Results (four single-song goldens, live MLX transcripts, room's clock)

| | WABN | Reckless Love | King Of My Heart | I Surrender All | mean |
|---|---:|---:|---:|---:|---:|
| production (half-gap lead) | 0.270 | 0.414 | 0.426 | 0.209 | 0.330 |
| **joint, words + vocal melody** (segments) | 0.248 | 0.289 | 0.354 | 0.183 | **0.268** |
| joint, words only (segments) | 0.193 | 0.335 | 0.396 | 0.248 | 0.293 |
| joint, words only (sheet roadmap) | 0.185 | **0.136** | **0.158** | 0.566 | 0.261 |
| joint, words + vocal melody + mix chords | 0.323 | 0.420 | 0.269 | 0.385 | 0.349 |
| joint, no stems (mix melody + mix chords) | 0.382 | 0.437 | 0.259 | 0.427 | 0.376 |

* The joint follower with words and the vocal melody beats production on all
  four songs.
* **Chords hurt** the screen on three of four songs (and double the slide
  changes); King Of My Heart is the exception.
* **The reference structure is the open question.** The sheet-roadmap
  reference with words alone is the best single result on three songs, but
  fails a hymn whose sheet prints one verse (verses 2-5 had verse 1's words).
  The segment reference fixes the hymn but loses some of what the roadmap
  carried. Measured causes, from `diag_refs.py`: (a) bands repeat or vamp a
  line (Reckless Love holds one slide 28 s against 8 s of written music); a
  segment that cannot repeat is forced onward; adding a repeat transition
  helps King Of My Heart and costs WABN / I Surrender All (mean 0.279);
  (b) identical-word slides as separate segments predict the wrong "next".
  Merging them was worse (0.366). Five structural variants all land words +
  melody at 0.27-0.29; the differences are song-specific trade-offs.
* **Two bugs found and fixed on the way**: the key was estimated against an
  already-shifted model (King Of My Heart collapsed to 0.94); and an
  argmax-cell read-out flips between passes of a repeated tune (per-slide
  belief is the read-out).

## The three questions

**Separate stems first?** For the *melody*, yes: Basic Pitch on the mix
agrees with the sheet on 26% of voiced frames against 55% on the vocal stem,
and every no-stems arm is worse (0.376-0.418 vs 0.268). Chords are as good
from the mix (and 2 s sooner). So: Demucs for the vocal melody only;
chords and words from the mix, in parallel with it. The stem's delay is not
the lever it looks like: as if it arrived after 1.5 s instead of 3 s, the
mean moves 0.268 -> 0.265; even a zero-delay stem is worth only ~0.03.

**A faster word model, now that there is other evidence?** The streaming
Zipformer (20M, sherpa-onnx, already in the app as the "fast channel") uses
0.034 of real time against Whisper small.en's 0.645 on this Mac.

| words source | words alone | words + vocal melody |
|---|---:|---:|
| Whisper small.en | 0.293 | **0.268** |
| Zipformer on the mix | 0.379 | 0.320 |
| Zipformer on the vocal stem | 0.409 | 0.322 |

With the melody, the fast model reaches production's level (0.320 vs 0.330)
at ~1/20 of the compute — but it is not *faster* end to end, because the
melody it leans on is 3 s late anyway, and Whisper + melody is clearly best.
The vocal stem helps the fast model find words (more of them) but not the
score. Not yet tried: Whisper's own forced alignment (scoring the expected
words against audio, no free decoding), flagged last month as promising.

**Parallel processing?** Measured per stage on this Mac (share of real time /
what it waits for): Demucs 0.10 / 7.8 s window, ~3 s behind; Basic Pitch 0.03
/ 2 s window; BTC 0.06 / ~0.74 s; Whisper 0.65 / ~2.7 s; Zipformer 0.03 /
~0.4 s. Total ~0.87 — it fits, with Whisper and Demucs sharing the GPU. The
stages are independent except pitch-on-the-vocal-stem, and the follower
already consumes each stream as it arrives, so latencies do not add: the
screen is bounded by the slowest *useful* source (~3 s), not the sum.

## Recorded reference, smoke test

Reference: WABN's first bridge and chorus-3 passes (167-284 s, 15 operator
cues). Test: the same song from 320 s (second bridge, chorus 3, ending).

| on that stretch | wrong-slide | timing |
|---|---:|---:|
| recorded reference, words + vocal melody | 0.186 | -83 ms |
| lead-sheet segments, words + vocal melody | 0.182 | +660 ms |
| lead-sheet segments, words only | 0.043 | +10 ms |
| production (half-gap lead) | 0.147 | |

The machinery works and the recorded reference is on a par with the lead
sheet, with the best timing (its cells carry the operator's own cue timing),
despite lacking two slides sung in the test. Same band, same day, one song:
the easiest case. The real test is the same song from two services.

## Not done / next

* Song switching inside the joint follower (all four songs here are single
  songs; the live set and the medley need it — see the song-change trace).
* An anticipation mechanism: the joint screen runs 0.5-1.4 s late; the
  follower's tempo state gives time-to-next-line for free.
* Beats as evidence (reserved in the interface; Beat This is offline today).
* The reference-structure trade-off above.
* Two services of the same song, for the recorded path.

## Bug: adding chords made the joint follower worse — found and fixed (2026-10-05)

Tool: `diag_chords.py`. Three tests:

1. **Timing** — detected chords and vocal pitch agree with the sheet best at
   0 offset (+0.5 s on one hymn): evidence is stamped at the right audio time.
2. **Budget** — chords moved 2.5 units of log-evidence per sung second against
   0.7 for words, while being the least able to tell slides apart.
3. **The bug: evidence was counted per frame, not per observation.** A
   detector's output stays the same for many frames — a chord for ~39 frames
   (time-weighted mean, 2.6-3.9 s), a sung note for ~6, a word is one event.
   With one flat per-frame weight (0.4, chosen when music was the only
   source), every chord was counted ~39 times against a word's once, and a
   sub-second difference at a chord change became overwhelming evidence that
   overrode the words. Fix: per-frame weight = 1 / the detector's measured
   run length (chords 1/39, vocal melody 1/6.3, mix melody 1/7.4) — each
   observation counted once. No tuning.

| mean wrong-slide, 4 songs | flat (bug) | per observation |
|---|---:|---:|
| words | 0.293 | 0.293 |
| + vocal melody | 0.268 | 0.257 |
| + mix chords | 0.370 | **0.237** |
| + vocal melody + mix chords | 0.349 | **0.229** |
| no stems (mix melody + mix chords) | 0.376 | **0.228** |
| production | 0.330 | 0.330 |

Per song, words + vocal melody + mix chords: WABN 0.194, Reckless Love
0.279, King Of My Heart 0.269, I Surrender All 0.175 — all four beat
production (0.270 / 0.414 / 0.426 / 0.209). Adding a source no longer hurts
the mean, and **no stem separation at all is now as good as the best**: the
mix melody is weak, but it is weighted for what it is worth. That changes the
stems answer above — Demucs is not needed for this follower.

"Perfect" chords (the sheet's chord at the ground-truth position) score
0.237-0.253 — no better than detected ones, because that ground-truth
position comes from the transcript and is itself only good to ~0.5 s. A
limit of the check, not a remaining bug.

## Two more bugs, found by the same principle (2026-10-05)

**Late evidence silently dropped.** The follower re-filters a window of the
recent past as late evidence arrives; evidence arriving after its window had
closed was bucketed before the checkpoint and ignored, with no error. The
Whisper word source had a 3.5 s window, but measured delays from a word's
start to its arrival reach 5.2 s — so some free-transcribed words had been
dropped in every joint result above. Fixed: each source's window is its own
longest measured delay, and `joint.follow` now refuses any event that would
arrive outside its window. Words alone: 0.293 -> 0.266.

## Whisper as a scorer of the expected words (`wscore.py`)

Encode the trailing 6 s once, then score each candidate 6-word span of the
slides by the decoder's probability of exactly those tokens (CTranslate2
`align`, the forced-alignment path faster-whisper uses for word timestamps).
No free decoding, so nothing to hallucinate and no stability margin.

* **Quality:** at every probed moment the top span is the right lyric,
  including the right pass ("beautiful / wonderful / powerful"): about -0.4
  to -1.3 mean log-probability per token against a median of -6.
* **Placement bug, found by measurement:** the decoder starts at the window's
  start, so the best span is the one sung at the *beginning* of the window —
  its last word ends a median 3.0 s before the window's end. Crediting it to
  the window's end (my first wiring) made every arm with scoring 2-3 s late
  and worse than free words on three songs. Evidence is now placed at the
  span's sung time (a 3.0 s correction here; per-span alignment times are
  being cached to replace it).
* **Cost:** CPU (CTranslate2 int8): all ~160 spans of a song 4.3 s per step
  (offline cache only); the live shape scores only the follower's ~16
  candidates. MLX can do the same teacher-forced scoring on the GPU and is
  the path to measure for live latency.

| mean wrong-slide, 4 songs (all fixes in) | |
|---|---:|
| production (half-gap lead) | 0.330 |
| joint, free-transcribed words | 0.266 |
| joint, scored words | 0.247 |
| joint, free + scored words | 0.215 |
| joint, free words + no-stems music | 0.217 |
| **joint, free + scored words + no-stems music** | **0.192** |

Per song (everything): WABN 0.192, Reckless Love 0.271, King Of My Heart
0.123, I Surrender All 0.184 — against production 0.270 / 0.414 / 0.426 /
0.209. I Surrender All is the one song where scoring adds nothing yet.

Both jobs the user asked for are served by the two word sources together:
*scored* words rank the current song's candidates (and settle the pass), and
*free* transcription stays as the open-vocabulary stream that can catch a
cross-song or cross-library switch, since it is not restricted to the
candidates. Song switching itself is not yet in the joint follower.

## Live latency with MLX scoring (2026-10-05)

`wscore_mlx.py` scores spans on the GPU with mlx-whisper small.en: the window
is encoded once, all candidates go through the decoder in one teacher-forced
batch. The encoder is cut to the 6 s actually used (whisper.cpp's "audio
context": the positional embedding sliced to fit) instead of padding to 30 s.

**Cost per live step (window encode + 16 candidates), this Mac:**

| | |
|---|---|
| full 30 s encoder | 2.3 s |
| **truncated to 6 s** | **0.16 s alone; 0.37 s median (p90 0.55) beside live Whisper** |

**Contention (`bench_concurrent.py`, real-time playback, one GPU):** the app's
own live Whisper pass goes 0.78 -> 1.09 s and its word delivery lag 2.71 ->
3.06 s median (p90 3.09 -> 3.88) while the scorer runs. That cost is charged
to the free-words source in every number below.

**Quality:** MLX scores agree with the CPU scorer's ranking (rank correlation
0.87-0.89; same top span 40-47%, mostly between near-duplicate spans). In the
follower, with the same placement, they are as good (scored alone 0.252 vs
0.247) — the truncated encoder costs nothing measurable.

**Placement:** cross-attention argmax as each span's sung time is too noisy
(best span's end 1.0-3.7 s before the window's end, IQR) and made MLX scoring
look worse (0.304) than it is. The measured 3 s offset works; Whisper's own
DTW word timing (as faster-whisper does it) is the better per-span option and
was not built.

**The structural limit:** teacher-forced scoring starts the decoder at the
window's start, so a score describes text sung ~3 s before the window's end.
Scored evidence about a moment therefore arrives ~3.4 s after it — no sooner
than free transcription — even though computing it takes 0.4 s. Its value is
*what* it says (the right pass, the right line), not *when*.

**Result at measured live latency (4 songs):**

| | mean wrong-slide |
|---|---:|
| production (half-gap lead) | 0.330 |
| joint, free words (with contention) | 0.281 |
| joint, free + MLX-scored words | 0.217 |
| **joint, free + MLX-scored words + no-stems music** | **0.203** |

Next, if pursued: score shorter windows or right-aligned spans to get the
evidence nearer "now"; Whisper's DTW timing for per-span placement; song
switching in the joint follower (with free transcription as the
cross-song / cross-library stream).

## Scorer-only critical path (2026-10-05)

Question: make the scorer the only thing on the latency-critical path, and move
free transcription to a slow, low-priority path used only to catch cross-song
jumps.

**Scheduling (bench_priority.py, one GPU worker, scorer first every second,
then a transcription pass if one is due):**

| transcription every | scorer latency | transcription done | free-word lag |
|---|---:|---:|---:|
| never | 0.17 s | — | — |
| 1 s | 0.17 s (p90 0.18) | 0.79 s | ~2.8 s |
| 2 s | 0.17 s | 0.79 s | ~3.3 s |
| 3 s | 0.17 s | 0.77 s | ~3.8 s |

Running both on two threads gave a scorer latency of 0.37 s (p90 0.55) and
added 0.35 s to word delivery. One queue with the scorer first gets the scorer
back to its standalone 0.17 s. Transcription still runs every second at its
standalone speed, because the scorer plus one pass takes 0.79 s, which is under
the 1 s budget. So **priority ordering beats concurrency, and it costs nothing
here**.

**Accuracy of the joint follower when free words are pushed off the critical
path:**

| setup | mean wrong-slide |
|---|---:|
| scorer and free words concurrent (scorer 0.4 s, words +0.35 s) | 0.203 |
| scorer first (scorer 0.2 s, words +0) | **0.200** |
| free words held back +2.35 s / +5.35 s / +10.35 s | 0.218 / 0.228 / 0.229 |
| no free words at all (scored + music) | 0.222-0.229 |

**Lateness hardly moves.** Anticipation stayed at +0.5 to +1.0 s late per song
across all of these. Scorer compute (0.17 s) is not what limits delivery. The
limit is when the text becomes recognisable: a span scores well only once most
of it has been sung. The best span ends 2.0-3.4 s before the window's end at
6 s windows, and 0.9-1.2 s at 3 s windows.

Changing the shape doesn't fix this:

| shape | scored | scored + music |
|---|---:|---:|
| 3 s window, 4-word spans | 0.289 | 0.270 |
| 6 s window, 6-word spans | 0.250 | 0.229 |
| 6 s window, 10-word spans | 0.265 | 0.230 |

The 3 s windows got much less accurate and were no earlier in the follower.

**Conclusion:** the split is right and free: one worker, scorer first, slow
transcription only for cross-song and library detection. It will not lower
delivery lag on its own, because no evidence source knows "now" sooner than
about 2-3 s after it is sung. The rest of the lag has to come from prediction:
anticipation from the follower's position and tempo, which production does
with its lead time and the joint follower does not do yet.

## Anticipation in the joint follower (2026-10-05)

Base setup: the scorer-first GPU queue (kept), words + scored + mel_mix +
chd_mix, observation weighting. Wrong-slide is the harness metric (forgiving
mode); anticipation is the median of the follower's switch time minus the
operator's cue.

**Built:**
* `joint.follow(lookahead_s=…)` carries every (cell, tempo) state forward at
  its own tempo, through slide ends by the order prior, with no new evidence.
  This is "beats to the next slide over the pulse", worked out state by state.
* A `tempo` source. Beat This beats give the median inter-beat interval over
  the trailing 8 beats, as a likelihood on the tempo state. It supports x0.5,
  x1 and x2 because the tracker's beat level is ambiguous. Arrival is 0.5 s
  late.
* Diagnostics:
  * `diag_anticipation.py`: operator lead, follower switch time, tempo.
  * `diag_seglen.py`: sung slide length against reference length.
  * `diag_rate.py`: the follower's tempo belief.
  * `diag_ttg.py`: predicted against true time to the next slide.
  * `diag_display.py`: shown error split into early, late and other, plus
    flicker.
  * `diag_flicker.py` and `diag_copies.py`.

**Why it was late:** the operator changes slide 0.1-0.5 s before the first
sung word (median). The follower switched 0.4-0.8 s *after* it. The follower
had no lookahead, so the best it could do was switch at the first word.

**Lookahead alone fixes the lateness at no cost:**

| lookahead | wrong-slide | anticipation per song (ms) |
|---|---:|---|
| 0 | 0.200 | +1020 / +573 / +890 / +470 |
| 0.5 s | 0.192 | +512 / +222 / +390 / +900 |
| **1.0 s** | **0.198** | **+252 / -154 / -110 / -84** |
| 1.5 s | 0.209 | -109 / -615 / +234 / +470 |
| 2.0 s | 0.239 | (early everywhere) |

**The follower's own tempo is badly off, and beats fix it:**

| | follower tempo belief | beat tracker |
|---|---|---|
| bias | slow on all 4 songs (King of My Heart 0.74 vs a true 1.14 quarter notes/s) | — |
| entropy (0 = sure, 1 = uniform) | 0.69-0.84 | — |
| readings within 5% of true tempo | — | 42-95% |

The slow bias does *not* come from short reference segments: sung length over
reference length has a median of 0.94-1.06. With beat tempo, the belief's
predicted time to the next slide improves:

| | without beats | with beats |
|---|---|---|
| bias | +0.3 to +1.3 s | ~0 |
| within 0.5 s, last 3 s of a slide | 3-44% | 42-59% |

**But beats do not improve the screen.** At matched lateness:

| setup | wrong-slide |
|---|---:|
| beats, lookahead 1.0 | 0.222 |
| no beats, lookahead 1.0 | 0.198 |
| beats + repeat prior (p_repeat 0.1, 0.25) | 0.200-0.215 |
| beats + commit gate (0.6, 0.8) | 0.197-0.225 |

The frontier stays at about 0.195-0.20 when on time.

**Why beats don't help:**

* **Forks.** King of My Heart vamps its chorus for 55 s, about 8 times against
  the sheet's 4 copies, and holds the bridge line 29 s against 14 s. With the
  true tempo the follower reaches the end of a copy every 7 s. The prior then
  sends it on to the bridge until the words (~3 s late) pull it back, which
  shows up as flicker (King of My Heart: 4 -> 16-28 switches undone within 3 s).
  With the wrong slow tempo it never reached the fork, so the vamp was hidden
  by luck. A repeat prior and confidence gating move error between early and
  late but cannot see the fork. Only evidence can resolve it, or knowing this
  band's arrangement, which is what a recorded reference provides.
* **I Surrender All flicker is not an error.** Slides 2 and 5 have identical
  words, and the display's pooled argmax breaks a floating-point tie between
  them. The harness forgives it. A live display should break ties toward the
  slide already shown.
* **Most remaining error is not timing.** In the strict breakdown, "other"
  (wrong section or copy) is most of the wrong time on 3 of 4 songs. Timing
  fixes can't reach it.

**Conclusion:** ship-candidate anticipation is a 1.0 s lookahead without beats
(0.198, roughly on time). Beat tempo is correct and makes the follower's
timing belief far better. It will pay off once forks are resolved: with a
recorded reference of the band's real arrangement (the auto-golden future), or
with fork evidence that arrives faster than words.

## Spontaneous mode + section clusters (2026-10-05)

The user's design:
* Anticipate (beats, 1.2 s lookahead) while the band follows the arrangement.
* When the band deviates (adds a repeat, goes back to a chorus, holds a line),
  flag the moment "spontaneous" and stop anticipating until an expected
  transition is seen.
* Even while spontaneous, still anticipate the next slide *inside* a section
  (a cluster). Wait only at a section's end.

**Cluster data (`clusters.py`):**
* **Library section tags:** unusable here. Most songs tag every slide as its
  own "verse N" (What a Beautiful Name has verse:1 to verse:24).
* **Lead sheets:** they work. A slide ends a cluster when, between its last
  word and the next line's first word, the sheet has a section label or a jump
  (repeat, ending, D.S., coda). Two details matter:
  * A pickup pushes the label or jump to the bar after the next line's first
    word.
  * A slide ends a cluster if *any* of its passes does, because a vamp block's
    first pass runs straight on.
  * Checked against the operators: **all 7 times an operator left the written
    order (roadmap or library order) were at sheet-cluster ends**. 18 of 50
    slide groups end a cluster.
* **Future:** the operator histories of recorded services. Slides always
  taken in the same order form a cluster; a slide whose successor varies ends
  one.

**Built:**
* `spontaneous.py` `SpontaneityTracker`. It judges only the *heard* belief,
  i.e. where the band was 3 s ago with those words in
  (`joint.follow(heard_s=3)`). Events:
  * an unexpected group change, counted only when the heard belief gives it
    at least 0.85 and it holds 1.5 s;
  * a pass beyond the written repeat count;
  * an overstay longer than 1.5x the expected time plus 2 s.

  Any expected move clears the flag. A written pass of the same line does not
  clear it: in a vamp, that pass *is* the fork in doubt.
* `joint.follow(gate=)`. The tracker runs inside the follower. While the flag
  is on, the unheard stretch and the lookahead step with
  `SegmentTransition.step(hold=True)`: mass reaching a cluster end waits
  there; inside a cluster it moves on.

**Results (lookahead 1.2):**

| | wabn | rl | komh | isa | mean |
|---|---:|---:|---:|---:|---:|
| beats | 0.160 | 0.332 | 0.218 | 0.218 | 0.232 |
| beats + hold (first tracker) | 0.149 | 0.354 | 0.161 | 0.233 | 0.224 |
| **beats + hold (confident, clear on any expected)** | 0.158 | 0.339 | **0.145** | 0.230 | **0.218** |
| no beats | 0.160 | 0.293 | 0.121 | 0.217 | **0.198** |
| no beats + hold (tuned) | 0.157 | 0.299 | 0.121 | 0.235 | 0.203 |

* **The hold fixes the vamp it was built for.** King of My Heart's 55 s vamp
  goes from about 12 flickers to 1, and the song improves from 0.218 to 0.145
  with beats.
* **The first tracker mostly reacted to the follower's own confusion:** 23
  triggers on wabn, only 6 after a real deviation. A lost follower and a
  spontaneous band look the same from inside. Confidence plus debounce cuts
  that to 4 triggers, 3 real, still catching 2 of 4 deviations. komh's
  overstay triggers are real vamps the operator metric can't count, because
  the operator just stays on the slide.
* **Overall, no-beats lookahead is still best (0.198).** Beats still cost
  Reckless Love (0.293 -> 0.339) and King of My Heart (0.121 -> 0.145, even
  with the hold). Reckless Love has not been diagnosed: its tracker tempo is
  within 5% only 59% of the time, so octave or tempo error is the first
  suspect.

## Why beats don't reach the screen (2026-10-05)

**Lag accounting (question from the user):**
* **Free words:** each word carries its measured arrival time from a live run
  (median ~2.7 s, up to ~5 s), so variation is accounted for.
* **Scored evidence:** arrival is measured and steady (window end + 0.2 s,
  p90 0.18 s). Its *placement* is a flat assumption: "sung 3.0 s before the
  window end". The real offset is 2.0-3.4 s per song with ±1 s per step, and
  this is not accounted for.
* **Anticipation** has no lag constant: it projects from wherever the evidence
  ends. The 1.2 s lookahead and the 3 s "heard" point are fixed.

**Tools:**
* `diag_position.py`: the follower's place in the line against the truth, in
  its own coordinates. run_joint's "within-beat" compares segment cells with
  sheet positions, so it was meaningless for the segment follower.
* `diag_pulse_vs_sheet.py`: the band's own beats against the sheet's beats.
* `diag_hold.py`, plus `--order oracle` and `--spontaneous always` in
  run_joint.

**Findings:**

1. **Beats do improve tracking** on 3 of 4 songs:

   | | within 1 beat at "now" (no beats -> beats) | wrong line |
   |---|---|---|
   | What a Beautiful Name | 88% -> 90% | 20% -> 16% |
   | King of My Heart | 57% -> 74% | 25% -> 21% |
   | I Surrender All | 47% -> 65% | 13% -> 11% |

   They also remove a -0.65-beat lag at "now". At lookahead 0 this reaches the
   screen: wabn 0.195 -> 0.171, isa 0.203 -> 0.184.

2. **Forks.** The order prior is a 50/50 blend of roadmap and library order,
   so every section end where they disagree is a coin flip. Beats reach forks
   on time, so the wrong guesses stay up longer. A slow, wrong tempo hid this.
   Never crossing a section end in the lookahead (`--spontaneous always`)
   takes beats from 0.232 to 0.206. Without beats it barely matters (0.198 ->
   0.201).

3. **Repeat counts.** Even an oracle order prior (the operator's exact slide
   order) gives no beats 0.180 against beats 0.254; King of My Heart with beats
   is 0.351. The order says 7 -> 8 -> 9 -> 10 -> 1. The band vamps about 8
   passes, but the operator shows them on one slide, so the slide order never
   records how many times a line is sung. With the true tempo, the follower
   uses up the copies and leaves while the band is still vamping.

4. **Reckless Love doesn't follow its sheet.** In the band's own beats it
   spends 1.11x the sheet's beats within a line and 1.26x slide to slide.
   Trailing tempo moves more than 10% on 44% of readings (King of My Heart 3%,
   I Surrender All 6%). Pinning to the true pulse drives the follower through
   the sheet too fast. Loosening the tempo evidence 10x changes nothing
   (0.314-0.320 against 0.287 without beats).

5. **What a Beautiful Name's tracker** switches between quarter and eighth
   notes 30% of the time. The x0.5/x1/x2 tempo source absorbs it.

**Diagnosis:** beats make the follower trust the reference's timeline, which
is right when the band follows it. The lead sheet carries the notated beats
but not this band's arrangement: where forks go, how many times it vamps,
extra bars, rubato. Where the band departs from it, a confident tempo is wrong
sooner, while the sluggish no-beat tempo was accidentally robust. All three
gaps are what a recorded reference (a past service by this band) supplies by
construction. That is the test that would show beats paying off, and it
needs two services of the same song.

## Why adding beats made things worse: configuration faults (2026-10-06)

The user's principle: correct information, configured properly, cannot hurt.
It held. Beats hurt because of four faults, each found with a tool.

1. **Subdivision switches read as tempo jumps.** Reckless Love's sheet is in
   4/4. Beat This splits a third of its bars into 6 beats (the triplet feel),
   so the trailing-beat tempo jumped 1.5x, which the x0.5/x1/x2 handling can't
   absorb. This produced the "44% drift" and "1.11-1.26x more beats" I
   reported earlier. Both were wrong: they were the tracker, not the band.
   Bar length is solid on every song: trailing-3-bar tempo is off by more
   than 10% only 2-9% of the time, and tracker bars match sheet bars within
   4%. New mode `--tempo bars` builds pseudo-beats from downbeats
   (`diag_pulse_vs_sheet.py` plus a stability check).

2. **The lookahead was tuned on a slow tempo.** `diag_compare.py` runs two
   configs side by side and lists the stretches where one loses. Reckless
   Love's losses were the follower at *exactly* the true place in the line
   (for example 4+10 beats, truth 4+10 beats) switching 0.5-1.5 s before the
   operator. The no-beats tempo was 15-25% slow, which cancelled part of the
   1.2 s lookahead. With the true tempo the right lookahead is about 0.5-0.8 s.

3. **Segments ended at the wrong place.** A slide's segment ended at its last
   word plus at most one bar. The operator switches at the next line's first
   word. With the true tempo, the follower reached the segment end during the
   instrumental gap. `extend_to_next` (segments run to the next line's first
   word) fixes it: Reckless Love with bars at 1.0 s goes from 0.352 to 0.305.

4. **Repeat counts (structural).** King of My Heart's losses with beats are all
   in the vamp, where the true tempo uses up the 4 written copies.
   Without beats the follower was "right" by being stalled, with its tempo
   35% slow. This is a missing state in the reference, not bad information.
   Spontaneous hold covers it partly.

Also built: `--tempo drift` and `bardrift` (beats move the tempo belief by the
pulse's *change*, while the level is still learned from evidence). It is
neutral (0.198-0.199), because the evidence alone cannot pin the tempo
level: the belief entropy stays at 0.5-0.86.

**After the fixes, beats are neutral:**

| (`--extend` on all rows) | wrong-slide |
|---|---:|
| no beats, lookahead 0.5 / 0.8 / 1.0 | 0.196 / ~0.199 / 0.200 |
| bars, lookahead 0.3 / 0.5 / 0.8 / 1.0 | 0.200 / 0.200 / 0.207 / 0.217 |
| no beats + tuned hold, lookahead 0.5 / 0.8 | 0.202 / 0.199 |
| bars + tuned hold, lookahead 0.5 / 0.8 | 0.199 / 0.203 |

Best single songs: What a Beautiful Name 0.145-0.147 (beats + extend + 0.8 s)
against 0.160 before.

**Why beats aren't positive on these goldens:**
* **The tempo ceiling is small.** An oracle constant tempo gave only ~0.01
  (0.191 vs 0.200).
* **The error is which line, not timing.** "Wrong line at now" is 11-21%,
  and correct tempo improves position *within* the line (diag_position).
* **None of the 4 goldens drifts.** Measured correctly by bar length, tempo is
  off by more than 10% only 2-9% of the time. Real-time drift tracking, the
  case it exists for, has nothing to win here.

Proposed proof: time-warp a golden (gradual +-10% tempo) consistently across
audio features, words and ground truth, and compare beats against no beats.

## Scorer timing: replacing the flat 3.0 s guess (2026-10-06)

**Measured** (`diag_score_timing.py`): for each scorer step, the moment the
best-scoring span's last word was actually sung (ground-truth slide and
position) against where the follower placed it.
* The true lag (window end minus sung time) has a median of 1.8-2.7 s per
  song, with the middle half spanning about 2 s. A flat 3.0 s cannot fit it.
* The best span was not sung in the window on 35-58% of steps (instrumental
  stretches, or a wrong match).
* **Caveat:** the ground-truth positions are anchored on Whisper transcript
  word times, so the truth and the alignment may share some bias.

**Fix:** Whisper's own word-timing method (mlx_whisper.timing.find_alignment):
alignment-head cross-attention, softmax over frames, normalised per head,
median filter 7, averaged, then DTW over tokens x frames. The best span is
placed at the start of its last word (`MLXScorer.align`,
`wscore_align_cache.py`, `--scorer mlx_dtw`).

| placement | median abs error | within 0.5 s | within 1 s |
|---|---:|---:|---:|
| flat 3.0 s | 1.20 s | 26% | 44% |
| attention argmax (old "aligned") | 1.10 s | 22% | 46% |
| **DTW** | **0.28 s** | **76%** | **87%** |

Per song, within 0.5 s: wabn 81%, rl 83%, komh 74%, isa 59%. Median bias is
under 0.12 s.

**Follower** (words + scored + mel_mix + chd_mix, segments to the next line,
`--extend`):

| | wabn | rl | komh | isa | mean |
|---|---:|---:|---:|---:|---:|
| flat 3.0, lookahead 0.5 | 0.155 | 0.303 | 0.119 | 0.208 | 0.196 |
| DTW, lookahead 0.3 | 0.152 | 0.275 | 0.137 | 0.184 | 0.187 |
| DTW, lookahead 0.5 | 0.134 | 0.275 | 0.141 | 0.176 | 0.182 |
| **DTW, lookahead 0.8** | **0.132** | **0.274** | 0.145 | **0.175** | **0.181** |
| DTW + tuned hold / + bars | | | | | 0.189-0.193 |

Anticipation at 0.8 s is +0.18 to +0.73 s (still slightly late).

* **Accurate timing alone gives the follower the right tempo.** On King of My
  Heart it reads 1.02-1.17 against a true 1.14, where the flat guess gave
  0.74-0.86. The slow tempo bias chased in the beats work was partly caused
  by mis-timed scorer evidence.
* With the right tempo, King of My Heart hits the vamp problem again (the
  repeat count is not in the reference), so it is the one song that gets
  worse (0.119 -> 0.141). Spontaneous hold does not recover it here.

**Live cost:**
* A 16-candidate score step is 153 ms.
* A separate align pass on the best span is +66 ms, because it re-encodes.
* DTW plus the median filter alone is 1.9 ms on the CPU. Live, the
  batch pass already computes every candidate's cross-attention, so taking
  the best one's from it costs about 2 ms and no extra GPU work. Not yet
  built that way.

---

# Integration into the live engine (2026-10-06)

Plan: `~/.claude/plans/stateless-beaming-knuth.md` (approved). Code:
`sidecar/auto_presenter/follower/` (score, sheet, reference, online, scorer,
live), `decision/follower_engine.py`, `server_follower.py`, the
`SlidingWindowWrapper.side_pass` hook, `MusicUpdate.chord_probs`.

## Scope decided by measurement before building

**The song rule is out of v1.** Music confirming or vetoing cross-song
switches was measured on mix-only evidence, since the spike's song identity
used the Demucs vocal stem, which is spike-only. It failed its pre-registered
gate on the live set:

| at the live set's switches | vocal stem | mix melody | mix melody + chords (obs) |
|---|---:|---:|---:|
| false switch, margin (veto wanted) | -0.18 | -0.00 | -0.26 |
| genuine switch 323 s | -0.04 | **-0.41 (veto)** | -0.04 |
| genuine switch 638 s | +0.13 | -0.00 | **-0.21 (veto)** |
| true song leads, by song | 93/54/54/100% | 32/1/23/96% | 61/50/9/100% |

**Live melody is deferred.** It is worth 0.009 within songs, inside the noise.

**The scorer scores every span.** Restricting to the follower's candidates
cost 0.03-0.06 (0.181 -> 0.210-0.238). Scoring all spans is made affordable by
computing the audio's cross-attention K/V once per window instead of per span:
1128 -> 372 ms for 160 spans, with identical scores. Every 4 s instead of 2 s:
0.192 (the overload step).

**Ablation in the final configuration** (`exp_*`, 4 goldens):

| evidence | wrong-slide |
|---|---:|
| all | 0.181 |
| no music | 0.227 |
| chords only | 0.190 |
| melody only | 0.211 |
| no free words | 0.191 |
| no scorer | 0.220 |

## Port parity

| check | result |
|---|---|
| score unrolling | identical to the spike |
| cell tables | identical to the spike |
| reference (cells, words, segments, order prior) | identical on all 4 songs |
| vectorised transition | identical; 0.04 vs 0.09 ms per step |
| live scorer vs cached spike scores | same best span 20/20, same DTW placement, 380 ms for 160 spans |
| online follower, spike-like start (`exp_online.py`) | reproduces 0.190 / 0.181 to the third decimal |

The **live** arm anchors at an intro cell and estimates the key causally:
0.181 with chords, 0.172 with melody.

The intro was then changed to leak 30% to any line start, so a band starting
on a chorus is followable. On these goldens, where every band starts at line
1, that costs 0.006 (0.187).

## Engagement rules

* Songs with a `lead_sheet` reference whose sheet places at least 80% of the
  sung slides.
* The MLX backend.
* The hidden `score_follower` tunable on (the kill switch).
* The production engine still decides which song.
* Within a followed song the follower decides the slide. Base decisions are
  dropped and the base engine is told the real screen.

## The real-time gate (`research/eval/run_follower_eval.py`, 2026-10-06)

The real server, a fresh sidecar per run, golden audio injected in real time,
MLX small.en, follower `on` against the kill switch `off` (production). Runs
were sequential. Scored on the room clock with the harness metrics.

| recording | on | off | timing on / off |
|---|---:|---:|---|
| What A Beautiful Name | **0.156** | 0.308 | +535 / -827 ms |
| Reckless Love | **0.274** | 0.461 | +192 / -1054 ms |
| King Of My Heart | **0.157** | 0.486 | +1539 / -466 ms |
| I Surrender All | **0.221** | 0.272 | +240 / -30 ms |
| **single songs, mean** | **0.202** | 0.382 | |
| live set (4 songs, all with sheets) | 0.500 | 0.554 | |
| NBTB medley (1 of 2 songs with a sheet) | 0.562 | 0.563 | |

**Single songs:** better on 4 of 4, with no song worse. The gate criterion
passes. The follower runs 0.2-1.5 s *late* where production runs early; the
replay ran +0.2-0.9 s. Live adds latency: scored arrival about 0.4 s against
the 0.2 s assumed, and Whisper's pacing absorbs the scorer's time.

**Song changes** (production still decides them):

| change | on | off | band |
|---|---:|---:|---:|
| medley return | **544 s** | 572 s | 547 s |
| live set, Build My Life | **325 s (later)** | 308 s | 281 s |
| live set, false switch | none | 696 s (FALSE) | |

The streak gate does not depend on the follower's screen sync (checked). Both
configurations dropped audio on the live set: 58 chunks on, 78 off. The
machine was loaded (WindowServer 43%, Cursor 22%, Ableton Live 15%), so the
word stream differs from run to run. One switch 17 s later is not conclusive,
and the gate must be rerun on a quiet machine.

**A real flaw found and fixed.** On the medley the follower left the blank 32 s
before the band sang. A word matching no lyric (a welcome) counted as evidence
*against* the intro, because the intro has no words. The intro is now neutral
to words. Cost on mid-song starts (`exp_midstart.py`, 54 starts on 4 goldens):
lock median 7.9 -> 8.0 s, p75 9.9 -> 10.9 s, 53/54 within 60 s both ways. The
offline replay is unchanged (0.188).

**Overload stepping worked as built.** The scorer stepped to every 4 s and back
on Reckless Love and the medley, and to off on the live set, under the same
load that made production drop audio.

**Still open before automatic rollout:**
* held-out goldens: lead sheets for songs whose settings were never chosen
  (user to supply);
* a rerun on an unloaded machine;
* the follower's lateness (+0.2-1.5 s live).

## Lateness, traced live (2026-10-06)

Live evidence traces (`AUTO_PRESENTER_FOLLOWER_TRACE`; `run_follower_eval.py --trace-dir`) replayed by
`research/spikes/music_only/diag_live_trace.py` reproduce the live read-outs 99.7%.

* **Chords are not the cause.** The live chord latency is ~1.0 s, not the 0.74 s assumed, but per-cue lateness is the
  same with live chords, offline chords or no chords.
* **Order prior credited shared lines to the lowest copy.** `roadmap_sequence` turned King Of My Heart's 3, 4, 5 into
  3, 3, 5, so half the belief left the chorus a line early. Fixed: credit the copy that continues library order.
* **One-word spans.** A line's first word alone ("you're") out-scores every real span on a window with no clear lyric,
  and DTW then places it at the window's start: 14-20% of scorer steps pulled the belief backwards. Fixed: if the best
  span is one word the step is no evidence, and one-word spans never count. 0.186 -> 0.167 offline.
* **No null level.** Scores were only compared with each other, so before anyone sang the least-bad line (-4.2 to -5
  mean log-prob/token) pulled the follower off the blank. The intro cell now scores a fixed -3.0 (sung lines: -0.6 to
  -1.5; flat from -2.5 to -3.5). 0.167 -> 0.162 offline; mid-song lock-on unchanged (52/54, median 8.5 s).
* Tried and dropped: a score floor (worse, 0.171) and a switch hysteresis margin (0.159 at 0.1, noise, and later).

Real-time gate, follower on, same loaded machine: **0.202 -> 0.176** (wabn 0.148, rl 0.234, komh 0.099, isa 0.224);
production 0.382.

Still late: **shared openings** (King Of My Heart's chorus opening starts four slides and the
line is only identified at its eighth word; "You are good" / "You're never gonna" flip 3 <-> 7) and **repeats beyond
the sheet's roadmap** (the band sang the 5, 6 chorus pair twice).

## Expected order, flicker, spoken cues (2026-10-06, follow-up)

* **Roadmap over library order.** The order prior was half library order, half the sheet's roadmap. Where they
  disagree (King Of My Heart after slide 6: library 7, roadmap 3) the follower guessed by coin toss. `ROADMAP_WEIGHT`
  0.5 -> 0.85: offline 0.162 -> 0.149, live traces 0.176 -> 0.165, flat above 0.85. Only Reckless Love and King Of My
  Heart move (the other two sheets agree with library order) — held-out sheets needed.
* **Flicker was a missing confidence bar, not ad-libbing.** The 136-148 s "ad-lib" was the chorus tag
  misheard ("you're going guy… you're my guide"); with belief split by the order prior and "you're" matching both
  lines, the follower showed slide 7 on 40% belief, then flipped five times. `MIN_SWITCH_SHARE` 0.5 (a slide needs
  half the projected belief to replace the shown one), 4 live traces: flicker (A -> B -> A within 5 s) 16 -> 3,
  wrong-slide 0.165 -> 0.176, median lateness +0.18 s. 0.6 removes all flicker for 0.181.
* **Shared openings are mostly not the problem now.** With the roadmap prior, only one cue in the 4 goldens goes to a
  line sharing its opening with another *plausible* next line (shown on time). The lateness lives in **off-order cues**
  (the band leaves the expected order: unwritten repeats, a chorus brought back): 9 cues, shown a median 5.7 s late.
* **Spoken cues — built (dev-only) but they don't handle repeats.** 18 goldens hold 6 real navigation cues; after 5 the
  operator jumped back to the start of the section just sung (or to the bridge) 0.3-8 s later. The shipped path
  (GPT-OSS 20B via Groq) gets 1/6 (only "let's go to the bridge"); "sing it/that again" -> no action or stays put.
  Gemini Flash: 1/6, 5-8 s per call. Cause: "again" means *the section just sung*, and the model only sees section
  labels (bootstrapped goldens label nearly every slide `verse:N`); the follower is what knows the sheet's sections.

## Held-out validation (2026-10-07)

Four recordings never used for tuning, with newly downloaded lead sheets: Since Your Love; a new Build My Life
golden; the Nothing But The Blood medley with The Blood's sheet added; the live set (Build My Life, Awesome God,
Doxology, O Come All Ye Faithful).

The first real-time run failed the gate: Build My Life 0.431 against production's 0.260, and the live set 0.733 against
0.524 with 60 screen changes against 16. Since Your Love (0.063 against 0.434) and the medley (0.511 against 0.603)
passed. Traced causes, all general rather than tuned:

* **Lyric versions differ.** The library's "trust in you oh lord" against the sheet's "trust in You alone" (0.92 alike,
  under the 0.93 bar) left a slide with no place in the sheet. A slide with no match gets a second pass at 0.85, for
  lines of at least 20 letters.
* **Overlap rule.** A slide's span starting at the same word as another slide's longer span was kept, putting 3, 2, 4 in
  the order where the band sings 3, 4.
* **The intro's null level, mid-song.** When Whisper can't make out the words (Awesome God: 94% of scorer steps below
  the null level) the blank took the belief back. The null applies only until a line of the song is heard.
* **Moving with nothing sung.** Through an 85 s intro and for four minutes after a song ended, the follower kept moving
  through it. Slides now change only while a line of the song was heard in the last 15 s.
* A follower that failed to start (here a trace file; equally a bad sheet) took the session's whole matcher with it.
  Isolated.

Replayed over all 8 live traces (`build/spike_music_only/multi_replay.py`): held-out 0.367 -> 0.330 (production
0.455), tuning songs 0.171 -> 0.172, the live set's flicker 10 -> 0. Open: live chords barely change decisions (Build My
Life replays identically with chords off), unlike the offline ablation.

## When the words can't be heard (2026-10-07, later)

**The fast loop.** `research/spikes/music_only/music_replay.py` replays every live trace (the 4 tuning songs and the 4
once-held-out recordings, multi-song ones included: song changes come from the trace's `enter` rows) with the
follower's knobs and its music evidence swapped: the run's own chords, offline mix or stem chords, offline melody, no
words, no scored spans, weights. All 8 recordings in about two minutes per arm, against 25-90 for real-time runs, and it
reproduces their numbers (live set 0.551 replayed vs 0.551 for the same trace). Real-time runs are now only the final
confirmation. Offline features: `features.py REC OUT.npz` into `build/spike_music_only/`.

**Music alone beat the words on the live set.** Whisper makes out almost nothing in its Awesome God (best scorer span
median -5.7 against a null of -3.0). Replayed: music only (no words, no scorer) 0.519 and chords only 0.509 against
0.533-0.551 with the words, and production's 0.524. Elsewhere the words are essential (Since Your Love 0.063 with them,
0.278 without). Chords matter live (no chords: tuning 0.172 -> 0.208); offline chords match the live analyser's;
stronger music weights hurt; mix melody helps the tuning songs a little (0.169) but not the others.

**So the fusion was wrong, not the music.**
* Scorer steps now count by how clearly they heard a line: weight x clip((best + 4.5) / 2, 0, 1), so noise counts for
  nothing and a clear line fully.
* The lyric hold had frozen the screen exactly where the music should lead. Now, with no line heard for 8 s, a slide
  needs 80% of the belief (not 50%) to replace the shown one.

Replayed over the 8 traces: the live set 0.551 -> 0.481 (production 0.524) with no flicker, the medley 0.488 -> 0.454,
Build My Life 0.217 -> 0.201, held-out mean 0.330 -> 0.300 (production 0.455), tuning songs 0.172 -> 0.173. These
knobs were chosen on all 8 recordings: the next goldens are the held-out test.

## Music for song changes (2026-10-07)

`research/spikes/music_only/song_id.py` runs a music-only follower per setlist song over a whole recording (offline mix
chords/melody, live latency) and compares each song's predictive log-likelihood rate; `song_switch_rule.py` turns that
into proposals ("the band is playing X": X leads every other song by a margin for a hold time) and counts false
proposals and how soon each genuine song change is proposed. Two multi-song recordings, 7 genuine changes: thin.

* **With each song free to choose its key (every 30 s, from the trailing 60 s of chords) music can't be trusted.**
  Right song 73-79% of sung time on the live set (chance 25%) but 40-49% on the medley (chance 50%: The Blood and
  Nothing But The Blood are alike), and no margin/hold gives zero false switches while beating production's timing.
  Adding the transcript's words to each song's model doesn't fix it.
* **With each song's key known, it can.** Chords + mix melody, 40 s window, margin 0.05 nats/s for 10 s: no false
  switch on the live set and none on the medley once the band is playing (one proposal of the first song, 26 s before
  the operator's first cue). Live set: Build My Life +30 s (production +42), Doxology +52 (production +109 or never),
  O Come All Ye Faithful +1 (production about +12). Medley changes +24 to +82 s, where words already do this.
* Why: worship songs share progressions, so a key chosen per song to fit the last minute fits several songs. A fixed
  key makes chords absolute.
* The keys used were the follower's own estimates from the same recording (a stand-in for a key the app would already
  know), so this is optimistic. The library has no key for these songs (no chord charts; setlist items carry none),
  and SongSelect sheets were in the played key for 1 of 4 set songs.

So the product question is how the app knows a song's key before it's played: remember the key the follower settled
on each time a song is played (bands mostly keep a song's key), a key on the setlist item, or the chart's key.

## Key and tempo changes as a song-change hint (2026-10-07)

The user's framing: a different key (or tempo) says the band probably changed song; the same key says nothing (sets
are often kept in one key; a band plays a song in different keys on different days).

* Measured: both multi-song goldens keep every song in one key (the set in G, the medley in E), so key changed at 0 of
  7 song changes; tempo changed between some songs (Awesome God 110 -> Build My Life 66 bpm) and not others.
* **Current-song fit drop** (`fit_drop.py`) as a song-end signal: sees all 7 song ends ~10-15 s after, but drops
  inside songs 0.4-5 times a minute (breaks, bridges, builds). Not used.
* **Built:** `music.change_watch.KeyTempoWatch` on the session analyser (one per AI session, now shared with the
  score follower): 12-key diatonic templates over 30 s of BTC chords, the beat tracker with octaves folded, a new
  value must hold 15 s. For 45 s after a change the song-change gate's confidence and song-share bars drop by 0.1;
  the observation, word and talking gates are untouched. Kill switch: `music_change_watch` tunable.
* Run as live over the 8 goldens (`change_watch_eval.py`): 0.21 changes a minute away from song changes (key flips
  between a key and its fourth, beat-tracker re-locks). Real time on the set and medley it changed nothing visible:
  the set's false Build My Life jump at ~93 s recurred as in an earlier run without the watch.
* Waiting on goldens with key changes between songs to set its weight.

## Five ideas, explored in parallel (2026-10-07/08)

Error anatomy first (`error_anatomy.py`, `episode_causes.py`): over the 8 recordings 25% of time was wrong; 31% of that
was the wrong song (30-50 s episodes), 53% within-song episodes of 3 s or more, only 14% short lateness.

1. **Other songs' opening lines** (agent spike, then `follower/openings.py`, beeb56f). Every 4 s the scorer also scores
   the other setlist songs' entry spans; a span's *gain* is its score minus its score on silence/noise (Whisper's LM
   prior). Propose a song at gain >= 2.5 and 1.0 above the current song's best gain. Offline: 7/7 song changes within
   10 s (production 3/7, 9-41 s late), 0 false proposals in 56 min. **Live, real time: medley 0.494 -> 0.379, live set
   0.477 -> 0.333** (production 0.603 / 0.524); all 4 medley changes within 7 s; the recurring false jump gone.
2. **The band waits** (agent spike, merged off by default). Wait states at line ends plus "is anyone singing" evidence
   from the Demucs vocal stem: tuning 0.173 -> 0.150, held-out flat. Whisper-based silence fails (garbled singing
   reads as silence). Needs a live vocal-activity source (demucs / demucs-mlx code is MIT; check the htdemucs weights'
   terms) before it can ship.
3. **Unmistakability from entropy** (`scorer_calibration.py`; `reference.step_entropy`). Mean log-prob x 2 is the
   calibrated temperature (log-loss 0.92 vs 1.64 at the legacy x7); steps with normalised posterior entropy < 0.1 are
   right 96% of the time. An entropy-set robustness floor alone changed nothing: recovery was slow because jumps could
   only happen at a line's end. A uniform jump from anywhere helped held-out (0.300 -> 0.277) but leaked (tuning
   0.182). **Jumps proposed by the evidence, 5% x (1 - entropy): tuning 0.171, held-out 0.266** (4c41494, default).
4. **Less delay** (agent spike). Compute time isn't the lever (0 compute: 0.236 -> 0.230); evidence age is (the best
   span's last word is a median 2.2 s before the window end). A 3 s / 1 s window: tuning 0.172 -> 0.151, lateness
   +269 -> +30 ms, but held-out 0.299 -> 0.324 (short windows can't tell look-alike lines apart) and double GPU. Next:
   a long window for identity plus a short one for timing.
5. **Visible error** (agent spike, `eval/visible.py`). Scored only where sung words can be placed (coverage ~50%; the
   live set 11%). Mean 0.202 -> 0.161; ranks change (rl/isa errors are mostly instrumental). Golden mistakes < 3% of
   counted error. New visible failure: the blank stays up 4-5 s while a song's first line is sung.

## Bucket-3 rules (2026-10-08)

Three named bucket-3 cases, traced on the latest live traces (`explain_episode.py`, per-copy belief):

| case | cause | rule | kept? |
|---|---|---|---|
| I Surrender All @103.5, 6.1 s late | the next verse led on 34% (copy memory right), the shown refrain held 1%; switching needs 50% | `COLLAPSE_SHARE` 0.2: a shown slide under 20% gives way to a leader with 2x its share (not while the lyric hold is on) | yes |
| What A Beautiful Name @128.6/@294.8 | at slide 14's end the order moved to 15 (the bridge) before hearing it; the band repeated the line / jumped back. Collapse lets it recover to the sung copy instead of waiting on 50% | collapse (above) | yes |
| same | — | `SLIDE_REPEAT`: a share of each slide's end restarts it | **no**: delays every ordinary line change (0.156 -> 0.171 at 0.2, 0.213 at 0.4); removed |
| song start, blank 3.4 s into the first line, even at zero lag | the lyric hold (nothing heard yet -> 80% bar) applied to the blank -> first line move | `FIRST_LINE_UNHELD` | yes |

8 latest live traces, replayed (`error_buckets.py`):

| | wrong | human | lag | neither |
|---|---:|---:|---:|---:|
| before | 0.156 | 0.073 | 0.056 | 0.026 (72 s) |
| collapse 0.1 / 0.2 / 0.3 | 0.155 / 0.154 / 0.154 | | | 68 s |
| first line only | 0.155 | | | 69 s |
| **both (shipped)** | **0.150** | 0.073 | 0.055 | **0.022 (60 s)** |

No recording worse beyond 0.004 (the set, lag). Left of bucket 3 at 3 s+: the set's Doxology repeat (2 -> 1, junk words),
the medley's garbled "o precious is the flow" (evidence drought) and its tag copy (1 vs 4), I Surrender All 3.6 s
(the scorer's timing). Same recordings tuned on; 3 knobs, flat across 0.1-0.3.

## The lag (bucket 2), measured (2026-10-08)

Ceiling: zero lag takes wrong-slide 0.150 -> ~0.095. Evidence arrives (sung -> follower): free words 3.9 s median
(p90 5.7), scored spans 2.6 s (p90 5.9), chords 0.74 s. The scorer itself never queues: every 2.00 s, 0.33-0.61 s
from window end to arrival (max 0.81). The tails are Whisper on a busy machine (9.6 s passes, 84 dropped chunks
in the set run while another session's jobs ran).

What bucket 2 is (153 s): 44 s late at ordinary next-slide changes (~1.1 s each); 36 s "elsewhere" after an
in-order move, 20 s of it one Reckless Love instrumental (the band holds slide 8, the tempo carries the follower to
9: a vocal-activity problem, not lag; zero lag only held by luck, its hold engaging ~2.6 s earlier); 22 s late
after the band went *back*; 10 s blank into a song's first line.

| tried | wrong | note |
|---|---:|---|
| today (lookahead 0.8 s) | 0.150 | median switch +564 ms vs the operator |
| lookahead 1.5 / 2.5 s | 0.160 / 0.203 | on time, but early is wrong too; bucket 2 unchanged |
| on rails (look further only when >=80-90% of "now" is on the shown slide) | 0.151-0.157 | flicker 9 -> 45-58 |
| on rails, never stepping back | 0.151-0.176 | the late quartile (+1.3 s) is the follower *unsure*; rails never fire there |
| hold clock on sung time, not arrival | 0.149-0.152 | moves error between buckets |
| scored evidence 1 s / 2 s sooner (ceiling of a faster scorer) | 0.141 / 0.137 | |
| + second scorer, 3 s window every 1 s, 6-word spans | 0.160-0.205 | 6 words don't fit 3 s; medley 0.24 |
| ... restricted to held slides + next | 0.168-0.185 | |
| ... 4-word spans | 0.160 | medley locks on "hallelujah hallelujah i know" (scores -3.3 on silence; median line -6.9) |
| ... 4-word, minus each span's silence/noise prior (x0.5) | 0.147 | best of ~12 arms; +35% GPU (0.26-0.40 s per step); not shipped |
| prior correction on the main scorer | 0.162-0.184 | its thresholds are tuned on raw scores |
| **chord weight x2** | **0.136** | x1.5 0.140, x3 0.139, x4 0.141; gain on the 4 recordings that never set the weight |

Evidence quality by window (1 s steps, best span on the band's slide / first right step after a band move):
6 s 77% (89% clear) / 2.7 s; 4 s 64% / 2.2 s; 3 s 6-word 55% / 2.0 s; 3 s 4-word 59% (82% clear) / 2.1 s.
Whisper needs ~2 s of a new line whatever the window, so a shorter window buys ~0.6 s at a large accuracy cost.
The follower already switches at a median +0.56 s by prediction; evidence speed only matters where it is unsure.

Real time, chord weight x2 (2026-10-08). Medley, clean run (0 dropped): 0.156 (previous live run at x1: 0.161).
Live set: the first run is void (the Mac slept on 1% battery mid-run: 207 dropped chunks, scorer shed twice);
the rerun had Spotlight/mediaanalysisd catching up after the wake (load to 28, 56 dropped): 0.246 (previous live
0.267, also loaded). Same-trace replays of the new runs, x1 vs x2: medley 0.145 -> 0.136, set 0.228 -> 0.201.

Why every live set run dropped audio (2026-10-08). Not the machine: a quiet run (load <= 5) still dropped 128
chunks and scored 0.316. Whisper looped on sung audio ("no, no, ..." x79; "please, please, ..."): a looping 6 s
window decoded to the 224-token default in 4.3-6.0 s (normal 0.7 s), overflowed the ASR queue (5 s) and tripped
the overload guard, which switched the scorer off: no scored step for 47 s, across the Doxology. Fix
(`asr/mlx_backend.MAX_TOKENS_PER_S` 10, i.e. 60 tokens per 6 s window): looping windows 2.3-2.5 s, every other
window decoded identically. Live set, real time, quiet machine: **0.228, 0 dropped, 1 unhealthy report**, scorer
never paused over 2.1 s, all 3 song changes (previous live runs 0.246-0.316, 56-207 dropped).
