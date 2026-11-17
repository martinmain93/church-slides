# Chord charts as a position signal

Spiked 2026-09-08. Nothing wired into the sidecar.

    research/spikes/spike_chord_charts.py     --verify --goldens --position --audio
    research/spikes/spike_chord_downbeat.py   --verify --chords
    research/spikes/spike_metre_corpus.py     --beatles --harmonix
    research/spikes/spike_beat_horizon.py     (needs librosa, isolated venv)

## The design being tested

Three layers, each corroborating the one above:

    beats          corroborate  ->  chord transitions
    chord changes  corroborate  ->  section, and position within a line
    words                           decide which slide

Beats are **not** here to time slide switches. Chords are **not** a
boundary trigger. Both exist to say *where in the song we currently are*, at a
finer grain and sooner than a word can — which is the standing goal for any
second engine (`goal_side_channel`: speed, not identity).

An earlier version of this document measured chord changes as a switch trigger
and reported 11-13% precision. That was the wrong question, and the number is
retained below only because it turns out to be an argument *for* the design:
**chord changes run 10-20x the slide rate**, which is not noise but roughly
10-20 position observations per slide.

---

## 1. The chart importer works

`spike_chord_charts.py` reads the user's own PDFs from `~/Documents/Song
sheets` (847 sheets; all seven approved goldens are covered) via
`pdftotext -layout`, which preserves the character columns that make a
chord-over-lyric chart machine-readable at all.

It detects the key, converts to the **Nashville Number System**, and binds each
chord to the word it sits over:

    Verse 1 -> [('1', 'King', 0), ('4', 'mountain', 1), ('1', 'run', 0)]
               [('6m', 'fountain', 1), ('5', 'from,', 0), ('4', 'is', 1), ('1', 'song', 0)]

Verified under `--verify`: chord/lyric discrimination (including lines
containing "A" and "I", which are both words and chords), Nashville degrees in
two keys, slash bass, flat degrees, and chord-to-word binding on a
hand-checkable line.

**Key invariance is confirmed on real files, not just in principle.** The sheet
collection happens to contain the same songs typeset in two keys, so:

| song | keys | Nashville agreement |
|---|---|---|
| Alabaster | D vs Gb | **94%** of 66/62 chords |

Two independently typeset PDFs, a tritone apart, produce the same numbers.

### Two bugs worth not repeating

- **`Cma7` parsed as minor.** The negative lookahead excluded the literal "aj"
  but not a bare "a", so major-seventh chords became minor. Silent, and
  produces a plausible wrong Nashville number.
- **Chord columns are absolute, lyrics were stripped.** Matching a chord at
  column 14 against a left-stripped lyric shifted every chord right by the
  indent — the `1` over "King" was reported over "of", four words later. The
  parser now keeps the raw line.

### On exactness (noted 2026-09-08)

Placements are recorded **exactly** — column, word index, and character offset
within the word. But charts are typeset loosely and chords frequently sit a
character or two off the syllable they are sung on, so **the matching side must
be fuzzy**: treat a stored placement as a soft anchor with a tolerance of about
a word, not as a hard alignment point. The storage is exact so that the
tolerance is a decision made once, at match time, rather than baked into the
data.

---

## 2. How much position does a chord map actually carry?

Measured on the charts alone — no audio, no detector. If the chart cannot
distinguish one position from another, detection quality is irrelevant.

Candidate positions matching a window of k consecutive chords (1.0 = unique):

| scope | k=1 | k=2 | k=3 | k=4 | k=6 | k=8 |
|---|---|---|---|---|---|---|
| song-wide, median candidates | 14.0 | 10.0 | 5.0 | 3.0 | 2.0 | 1.0 |
| **within a section**, median | 4.0 | 2.0 | 2.0 | 2.0 | 1.0 | 1.0 |
| unique, song-wide | 2.3% | 11.8% | 20.6% | 29.2% | 44.6% | 55.6% |
| **unique within section** | 8.3% | 20.0% | 33.8% | 44.5% | 60.2% | 71.6% |

**Read the "within a section" rows** — that is the real operating condition,
because the words have already chosen the slide and the chords are corroborating
it. Three to four chords narrow position to a median of **2 candidates** and
are outright unique 34-45% of the time.

At the 50-100 chord changes per minute measured on our own audio, three chords
is roughly **2-4 seconds of singing**, and the information arrives continuously
rather than waiting for a word to be recognised.

Worst case is what you would expect: *Nothing But The Blood* has two distinct
chords in the whole hymn and stays at 8 candidates within a section. Repetitive
hymns are exactly where lyrics are also ambiguous, so this is a genuine gap
rather than a redundant one.

---

## 3. The detector is the blocker, and the controls say so

Two tests, both with the same control — match every recording against every
*other* song's chart, so "it fits" has to mean "it fits this song".

| test | correct chart identified | correct-pair vs wrong-pair |
|---|---|---|
| rotated chord **histogram** | 2/6 (chance 17%) | 0.842 vs 0.788, gap **+0.054** |
| rotated chord **sequence** | 1/6 | 0.167 vs 0.151, gap **+0.016** |

Without the control this looked strong: the histogram test recovers a clear key
on 6/6 recordings at 2.5-4.3 sd above the other eleven rotations. That margin
is real but it is mostly detecting *"a diatonic major song"*, which every chart
here is. This is the same trap the chroma fingerprinting work fell into and
recorded — the average is uninformative, the progression is where the signal
is — and the histogram version repeated it.

**The diagnostic that explains both failures:** on the first recording the
decoder emits **331 chord changes against 48 in the chart**. It is
over-segmenting by several times. Frame-synchronous template matching at 32 ms
flickers on passing notes and vocal harmonies, and no sequence comparison
survives that.

### Which is precisely the layer the design already specifies

Beat-synchronous decoding — averaging chroma between beat times and allowing a
chord change only at a beat — is the standard remedy for exactly this
over-segmentation, and it is the "beats corroborate chord transitions" layer of
the proposed design. **It is not yet implemented**, and it is the single next
thing to build. The supporting evidence for it is in the corpus work below:
**71.9% of chord changes fall on a downbeat**, so the beat grid is a strong
prior on where a change is allowed to be.

---

## 4. Supporting corpus evidence

Measured on human annotations rather than our audio, to separate the musical
claim from our detector. **Harmonix Set** (912 pop tracks, MIT annotations) and
**Isophonics Beatles** (chords, beats and segments annotated by *separate*
efforts, so nothing can be an artifact of one snapping tool — Harmonix alone
would be circular at 99.8% of boundaries on a beat).

| | Beatles | Harmonix | null |
|---|---|---|---|
| chord change on a downbeat | **71.9%** | — | 9.6% |
| section boundary on a downbeat | 94.4% | 80.2% | 9.5% |
| section length is a whole number of bars | 98.6% | 75.2% | — |
| a recurring section keeps an identical bar length | 82.3% | 58.3% | ~40% |

The first row is the one this design needs. The rest says the metrical grid is
strongly structured in this repertoire, which is useful context but not the
mechanism being built.

Rate, the number that reframed the whole spike:

| | chord changes | section changes |
|---|---|---|
| Beatles (annotated) | 29.8/min | 3.2/min |
| our goldens (decoded) | 49-106/min | 2.2-6.5/min |

---

## 5. Beat tracking, and why it is only a corroborator

Four separate attempts to establish a beat grid **on our audio** each produced
a confident number that collapsed against a control — a rigid click track, a
synthetic drum loop, or Beatles boundaries known to sit on downbeats. Details
in `spike_beat_horizon.py` and the retracted `--quantized` command.

The common cause is arithmetic: extrapolating a grid *h* seconds forward within
±ε needs the period known to about ε/h, and librosa read 75 BPM on a synthetic
112 BPM loop. Causal prediction survives roughly **2 seconds**.

For this design that is a much smaller problem than it first appeared: the beat
grid is only being asked to say *where a chord change is allowed to fall*, over
the next beat or two, not to predict a slide change ten seconds out. A grid
good to 2 seconds is adequate for beat-synchronous chord decoding.

Licences, checked: librosa **ISC** (beats only, no downbeats); madmom code BSD
but **models CC BY-NC-SA**, a shipping blocker; `beat_this` (Foscarin 2024)
**MIT**, needs torch and therefore an ONNX export, a pattern the project
already has in `research/dead_ends/acoustic/export_whisper_onnx.py`.

---

---

## 6. MEASURED 2026-09-08: the position signal does not pay off

With BTC benchmarked in (`Plan/MUSIC_MODELS_2026.md`), the design could finally
be tested end to end. Scripts in `research/spikes/chord_bench/`.

### Ground truth, at last

The chart binds each chord to a **word**; Whisper gives every word a **time**.
Composing them turns the chart into a timed chord score — the harmonic ground
truth this project never had. Repeats are handled inside each golden cue
interval, so the cues supply the performance order and a chorus sung four times
is aligned four times against one chart line. 427 anchors across six
recordings, covering 66-92% of each.

**BTC frame accuracy against it: 39.6%** (null 1.5%, the mean over the eleven
wrong rotations). The near-zero null means the key is recovered unambiguously.
But 39.6% is about half the 79.3% MajMin the literature reports on pop
benchmarks, and one recording (Nothing But The Blood) reads 9.8%. Some of that
gap is our ground truth, which holds each charted chord until the next anchor
and so is wrong across instrumental gaps.

### Head to head: words win, even paying Whisper's lag

Time from the start of a sung phrase group to the first correct unique
localisation, 35 groups. Words are charged the measured 2.85 s delivery lag;
chords are charged nothing.

| route | localises | median | p90 |
|---|---|---|---|
| chart chords (**oracle**, upper bound) | 86% | 3.48 s | 9.42 s |
| BTC chords (what we actually get) | **20%** | 4.58 s | 16.92 s |
| words + 2.85 s Whisper lag | **94%** | **2.85 s** | 7.12 s |

**Even with perfect chord detection, harmony localises position more slowly and
less often than lyrics — while lyrics pay a 2.85 s handicap and harmony pays
none.** The reason is information-theoretic and not fixable by a better
detector: a chord comes from an alphabet of about five symbols, a word from a
vocabulary of thousands. The words median of 2.85 s is exactly the lag, meaning
the *first* word usually disambiguates on its own.

This is the measurement that matters, because speed was the whole justification
for a side channel (`goal_side_channel`).

### The steelman: where lyrics structurally cannot help

The fair test is repeated identical text, the app's known worst case. Measured
on the charts alone, so this is information in principle, before detection.

**39% of chart positions (528/1348, 4-word window) are lyric-ambiguous. Chords
resolve 30% of them.** But the per-song split is the problem:

| song | lyric-ambiguous positions resolved by chords |
|---|---|
| Since Your Love | 100% (of only 12) |
| You Came (Lazarus) | 51% |
| Reckless Love | 48% |
| **King Of My Heart** | **14%** |
| **What A Beautiful Name** | **9%** |
| **Nothing But The Blood** | **4%** |

**The songs where chords help least are the repeat-heavy ones the idea was for.**
King Of My Heart — four identical choruses, the recording that has caused this
tracker more trouble than any other — sits at 14%. Harmonic repetition and
lyrical repetition are the same musical decision after all, at least for these
songs. `MUSIC_FINGERPRINTING.md` raised this worry and dismissed it on a
different measurement; on chord charts it holds.

### Verdict

As a **position signal**, this does not pay off: slower than words even at
oracle accuracy, and weakest exactly where words are weakest. The residual
value is 30% of lyric-ambiguous positions, concentrated in the songs that
needed help least.

What survives is worth keeping:

- **The chart importer** is useful independently of any of this — Nashville
  numbers with exact chord-over-word placement, key-invariant, verified on two
  independently typeset PDFs of the same song.
- **BTC is cheap and works**: 90-203x realtime, 3 M parameters, MIT, weights in
  the repo, and it recovers the key of every recording unambiguously.
- The chord-to-word-to-time construction gives the project **harmonic ground
  truth** it did not have.

---

## 7. The fusion ablation (2026-09-09)

Sections 5 and 6 asked the wrong question — how chords compare *against* words.
The design was always a compound feature: words **plus** chords, matched against
a "(word, chord) slide transcript". That is what this section measures.

Reference per slide: its words, each annotated with the chord in force over it,
from the chart's placement data. Observation: Whisper's words, each annotated
with BTC's decoded chord at that instant.

    score(slide) = sum over matched words of  idf(w) * (1 + LAMBDA * a(w))
    a(w) = +1 chords agree, -1 disagree, 0 either side undefined

Both arms share the identical word scorer; LAMBDA = 0 reproduces words-only.

### Result

Scored at 1 s steps, 2003 instants, six recordings. "Ambiguous" is the 415
instants (21%) where the top two slides are within 2% on word score alone —
the only moments a corroborating feature can act.

| arm | all instants | ambiguous instants |
|---|---|---|
| words only | 59.6% | 66.0% |
| **+ BTC chords** | −0.3% | **+1.0%** |
| **+ oracle chords** (ceiling) | **+1.9%** | **+3.4%** |
| + wrong chords (control) | −3.5% | −8.7% |

The control is strongly negative and BTC is positive on the ambiguous subset, so
BTC is carrying real signal — just very little of it.

### How good would the chord detector need to be?

Simulating a detector that reports the true chord with probability *p*:

| detector accuracy | all | ambiguous |
|---|---|---|
| 20% | −1.0% | −1.6% |
| **40%** | **+0.1%** | **−0.1%** — break-even |
| 60% | +0.9% | +1.7% |
| 80% | +1.5% | +3.4% |
| 100% | +1.9% | +3.4% |

**Break-even is about 40% and the ceiling is reached by 80%.** BTC sits at
break-even, which is why it delivers almost nothing.

### The gain grows as the words get worse

`eval_harness_fidelity` established that cached transcripts understate error
about 2x against live ASR, so the clean-transcript number understates the case.
Degrading the word stream deliberately (oracle chord arm):

| words dropped / corrupted | words only | gain, all | gain, ambiguous |
|---|---|---|---|
| 0% / 0% | 59.6% | +1.9% | +3.4% |
| 30% / 20% | 52.6% | +3.4% | +3.9% |
| 60% / 30% | 28.7% | +3.2% | **+6.3%** |

**The worse the lyrics, the more the chords are worth** — which is the right
direction, and means the live gain is likely nearer +3% overall than +2%.

### Three methodological failures on the way here, all caught by controls

1. **Chords as a separate sequence.** Treating chords as their own n-gram
   sequence beside the words gave +0.3%. The chart's value is the *placement*,
   so the chord belongs on the word, not next to it.
2. **A bonus-only term cannot discriminate.** With no penalty for disagreement,
   two slides that both match the words cannot be separated — precisely the case
   the feature exists for. Signed agreement was needed.
3. **The "oracle" was not an oracle.** It read +14.5% while leaking (it took the
   chord from the true slide's own chart, so agreement was guaranteed). Removing
   the leak dropped it to +0.6% — and then it scored *below* BTC, which no upper
   bound can do. The cause: the per-slide reference and the ground-truth chord
   stream came from two different difflib alignments of the same chart, agreeing
   only **53%** of the time. A single canonical alignment fixed both, and made
   BTC's earlier "39.6% accuracy" a figure against a half-wrong target.

### Verdict

The compound feature **does** help, the controls confirm the signal is
specifically harmonic, and the gain grows exactly where the app is weakest. But
the ceiling is small — around +2% clean, +3% under live-like word degradation,
+6% on genuinely ambiguous moments — and it needs a chord detector at 60-80%
accuracy on worship audio to reach it. BTC gives about 50% and lands at
break-even.

**Caveat that cuts against all of the above:** the word baseline here is a plain
IDF bag-of-words at 59.6%, not the project's real matcher. A stronger baseline
would leave less headroom, so these ceilings are probably optimistic.

## 8. Shipped as a diagnostic (2026-09-09)

Live chord and beat analysis is now in the production path — as a **diagnostic
that drives nothing**. The measurements above put the chord feature at
break-even for the matcher, which is not a case for wiring it into decisions,
but is a good case for being able to look at it on live input. The recordings
cannot say *why* it is at break-even; the panel can.

    music/features.py      chroma, onset flux, 24 triad templates   numpy only
    music/analyser.py      streaming, causal, per-frame updates
    server_music.py        toggles, broadcast, frame observer
    src/lib/musicAnalysis  rolling 10 s window (the store owns the history)
    MusicTab.tsx           the scope

**Debug panel → Music.** Two independent toggles, off by default. A 10 s
scrolling scope: RMS waveform, the chord in force drawn as a band over it, beat
ticks, and the three candidates with their confidence.

Deliberate choices worth knowing:

* **The numpy detector, not BTC.** BTC is PyTorch and the sidecar ships
  onnxruntime only. An ONNX export is the intended follow-up; `features.py` is
  the seam. What ships is the front end measured at break-even, so the panel
  shows the weaker of the two detectors — which is stated in the panel itself.
* **The sidecar sends deltas, the browser owns the window.** Each tick carries
  only the ~100 ms of levels since the last one. Sending the whole 10 s window
  ten times a second would put about a megabyte a second on the socket.
* **Beats are placed slightly in the past**, and the scope shows that honestly.
  A beat cannot be confirmed until the onset envelope has passed it; placing it
  the instant it falls due leaves only the left half of the search window in
  existence, which biased every beat early and compounded through the
  re-anchoring. Waiting a quarter period is what makes the placement correct.
* **`_frame_fanout` used to return early** when the fast-channel model was
  absent, which is the common case. Left alone, the analyser would have been
  silently starved of frames on every machine without that optional download.

### BTC swapped in (2026-09-09)

`scripts/setup_chord_model.py` installs the learned recogniser; the numpy
template matcher remains the always-available fallback and the panel says which
is running. Export and verification detail in Plan/MUSIC_MODELS_2026.md.

The over-segmentation this whole line of work was blocked on is largely gone:

| | template matching | **BTC** |
|---|---|---|
| chord changes per minute | ~47 | **~29** |
| chord runs holding at least a second | ~46% | **~70%** |
| chords covering 80% of the time | 4 | **3** |
| cost | 337x realtime | 14x realtime (~7% of a core) |

**~29 changes per minute is almost exactly the 29.8/min measured from
*annotated* Beatles chords** in section 4. Two independent routes — a human
annotator on pop records, and this model on worship audio — landing on the same
harmonic rhythm is the strongest evidence yet that the chord stream is now
reading something real rather than flickering.

That does not by itself move the fusion result in section 7, which was measured
with BTC in the first place. What it does is make the live panel worth looking
at, and remove the excuse that the detector was the problem.

### Measured on the approved goldens

`research/eval/check_music_analyser.py` drives the shipped analyser exactly as live
capture does, one 100 ms frame at a time:

| | |
|---|---|
| cost | **337x realtime**, about 1% of the ASR budget |
| chords covering 80% of the time | **3-4 per song** — the four-chord pattern |
| chord changes | 34-58 per minute |
| chord runs lasting a second or more | 35-54% |

The 80%-coverage figure is the one to watch. Counting every chord ever emitted
reads 11-19 per song and looks like wandering; almost all of that is a
single-frame tail, and the songs really are built on four.

### The scope stuttered, and it was not the analyser (2026-09-10)

Reported live: the waveform scrolled for about half a second, froze for about
three quarters, and cycled like that once a second — while the panel honestly
reported 8% of one core.

**Nothing downstream of capture sees a frame while an ASR pass is running.**
`SlidingWindowWrapper.transcribe` runs the backend pass *inline in the loop that
pulls frames*:

    for frame in frames:        # <- the capture generator
        ...
        events = self.backend.transcribe(window)   # ~0.75 s, blocking

So for the duration of a pass the generator is suspended, `_guarded_frames`
yields nothing, the frame observer is never called, and no `music_analysis`
message is sent. Then the pass ends, the capture queue drains at once, and ten
updates arrive together. `step_seconds` is 1.0, which is the observed period
exactly. The same applies to the VU meter and anything else hanging off the
per-frame path.

The audio and its timestamps are complete — only the *delivery* is bursty — so
this is a rendering problem. The scope now runs on `requestAnimationFrame` with
a display clock advanced by the wall clock and eased toward the newest audio,
held far enough behind live to cover the worst recent delivery gap (measured
per session, clamped to 0.35-2.5 s, and shown in the panel).

That also makes the display more coherent rather than less: the chord reading
was already up to 0.75 s stale during a stall, so putting the waveform on the
same delayed timeline aligns everything the scope draws.

**The deeper fix, not taken.** Removing the delay entirely means giving the
analyser its own tap on capture rather than a fan-out from the ASR generator —
a second queue filled by `LiveCapture._on_audio` and drained by its own worker
thread. That is a change to the PortAudio callback path, where a stall costs
dropped audio and a broken transcript, and it was not worth that risk for a
diagnostic panel.

## 9. Next

1. **Beat-synchronous chord decoding.** The identified blocker. Average chroma
   between beats; permit a chord change only on a beat. Re-run the cross-match
   control — that is the pass/fail.
2. **Promote the parser to a real importer.** It already does parse → key →
   Nashville → chord-over-word placement, and is the prototype of the app-side
   importer. Promoting it touches production code and library storage, so it
   was deliberately left in `scripts/` for this spike.
3. **Fuzzy matching**, per section 1: stored placements are exact, match
   tolerance is about a word.
4. Only then, position estimation against the golden recordings, scored on
   **latency** — how much sooner than a word the chords localise us.
