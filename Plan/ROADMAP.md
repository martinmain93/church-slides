# Roadmap — September 2026

Where the app is, and what is queued. Supersedes the phase numbering in
`Plan/PHASE_*.md` for anything still open; those documents remain accurate about
what was built and why.

## Where it stands

The app follows a band by listening to the words and moving the slides. On six
approved golden recordings it shows the right slide **55.7% of the time**
(wrong-slide 0.443), committing about 0.6 s after the band reaches a new slide.

Recent gains, all measured on the same six recordings:

| change | wrong-slide |
|---|---|
| starting point | 0.603 |
| one decode attempt instead of six (`temperature=0`) | 0.499 |
| commit on pooled confidence across identical slides | 0.484 |
| match on pronunciation instead of spelling | **0.443** |

The honest read: it works, it is late rather than wrong most of the time, and
the remaining error is concentrated in songs whose verses open alike.

## The original phases, honestly scored

| phase | status |
|---|---|
| 0 — Foundation (Tauri, sidecar, storage) | done, **except the installer half** |
| 1 — Song library | done |
| 2 — Setlist, presentation, debug panel | done |
| 3 — Evaluation harness | done |
| 4 — ASR baseline + audio wizard | done |
| 5 — Naive matcher | done |
| 6 — Particle filter | done, then **replaced** |
| 7 — Deviation handling | **half** — within-song yes, across-setlist never built |
| 8 — Production: library recovery, LLM, telemetry, installer | **LLM built** (unmeasured on real speech), rest **not started** |
| 9 — Vocal separation (optional) | **measured, shelved on latency** — see Plan/PHASE_9.md |
| 10 — Musical priors (optional) | spiked, **rejected on evidence** |

Three of those need explaining, because "done" and "not done" both understate
what happened.

**Phase 6 was completed and then overturned.** The particle filter shipped and
worked. It was later replaced by a dense forward filter that measured better on
every golden, and the particle code now survives only as the legacy path some
integration tests still exercise. The *goal* of Phase 6 was met; its artifact
was not the final answer.

**Phase 7 was half-built, and the half that was built was turned off.** Its
components — confidence monitor, relocalization controller, lost-hold policy —
all exist and are all passed `None` in production, because each measured
*harmful* once structural jumps were evaluated inside the recursion instead of
bolted on after a detection delay. What replaced them is better. But the other
half of the phase, "across setlist", was never built at all, and that is item 4
below.

**Phase 0 is done except the part that lets anyone else run it.** PyInstaller
bundling, release-mode sidecar spawn, and cross-platform install verification
were deferred to Phase 8 and are still open: there is no `.spec`, no
`externalBin` entry, and the app only starts through `uv`. **The app cannot
currently be given to anyone.** That is not on the four-item list and is
arguably more urgent than three of them.

## The four things asked for

### 1. Silence and non-singing → blank slide — DONE 2026-09-02

Nothing in the app detects that nobody is singing. The tracker keeps whatever
slide it last committed, forever, through instrumental breaks, prayers, and the
gap between songs.

Two distinct problems behind one behaviour:

- **Silence** is easy and was already measured: energy drops to a lull in
  22-34% of frames in a full band mix, and a phrase-start detector is cheap
  (0.0011 realtime factor).
- **Non-singing speech** — a pastor talking — is harder, and is the same
  problem as item 3. The words arrive; they simply are not lyrics.

There is a signal already in the tracker for this and it is unused: the forward
filter's `evidence` term, the per-word match quality. Measured while building
lost-detection, it separates poorly *by itself* (near-chance for "are we on the
right slide") — but "no word matches anything on any slide" is a different and
much easier question than "which slide". Worth measuring before building
anything acoustic.

**It did not need audio work.** The evidence term already knows, and no new
signal was required.

Per event it is too noisy to act on — median 0.269 while singing against 0.124
for another song's lyrics. But non-singing lasts many seconds, and a rolling
mean separates them. Tuned for a 5% false-alarm rate on real singing:

| window | catches off-book | catches speech |
|---|---|---|
| 4 events | 45% | 34% |
| 8 events | 79% | 71% |
| 16 events | 94% | 94% |

Superseded 2026-09-02 by a compound score — see the end of this section.
The original sustained-evidence rule shipped at a 12-event window: on the six
goldens it blanked on **4.1%** of events
during real singing and catches **75%** of speech, noticing within about **five
seconds**. Silence is handled separately by the clock, because with nothing
arriving there is no evidence to score.

### Corrected against real talking, 2026-09-01

The numbers above came from synthetic speech pasted onto a golden. Four of the
user's own recordings turned out to contain genuine talking, and against those
the detector caught **none of it** — 0 of 8 events.

Not because the signal was missing. Evidence during the real talking ran
**0.055-0.142** against a singing median of **0.268**, which is as clean a
separation as the synthetic test showed. The problem was granularity: a real
interjection — *"Okay."*, *"All right."*, *"I'm going to get a little bit of"* —
is **two or three transcript events**, and a twelve-event window cannot see it.

The synthetic test hid this by feeding forty speech words in a row. Real
non-singing between songs is mostly short.

So there is now a second trigger: a couple of consecutive events *far* below
anything singing produces. Across all ten recordings (8 talking events, 1297
singing):

| rule | catches talking | false alarm |
|---|---|---|
| 2 events below 0.10 | 25% | **1.6%** |
| 2 events below 0.15 | 75% | 7.9% |
| 3 events below 0.15 | 50% | 3.2% |

Shipped at the conservative end. Eight events is far too few to tune on, and
anything more aggressive should wait for more recordings of someone talking.
On the four recordings it now catches 25% of the talking at 1.7% false alarm;
wrong-slide on the goldens is unchanged at 0.443.

**This is where a pitch-based detector would genuinely win** — or so it looked.
The limit appeared to be *event granularity* rather than signal: a frame-level
acoustic feature has no transcript events, so a two-second utterance is not a
special case for it. Measured on 2026-09-02, pitch does not survive a room mic;
see the next section and Plan/MUSIC_LITERATURE.md.

**The two consumers want opposite things.** Blanking the screen should be
reluctant — a false alarm is a black screen mid-song. Waking a language model to
check for "go back to verse one" should be eager — a false wake costs one cheap
inference that finds no command and does nothing. The same detector serves both,
and the spoken-instructions work should read it at a looser threshold than
blanking does.

**Acting on it is off by default.** The detector is reported in the debug panel
with a switch beside it. Being wrong means a blank screen mid-song, which is
worse than a stale slide, so that trade belongs to the room rather than to a
default. Wrong-slide is unchanged at 0.443 with the switch off.

### Rebuilt as one compound score, 2026-09-02

Three OR'd triggers — sustained low evidence, an acute streak, and a word-rate
rule added last — measured **14.6%** false alarms on the goldens. One blanked
screen in seven, mid-song. Independent triggers compound their false alarms;
that is the shape of the mistake, not the tuning.

Two things fixed it.

**Combine the signals instead of OR-ing them.** Word rate and match quality are
scored together by a balanced logistic fit into a single number, smoothed over
three events, with hysteresis (blank at 0.80, un-blank at 0.40 — without the gap
the state changed twice as often for no gain). As rankings of "this is talking",
word rate is worth AUC **0.933** on its own and match quality **0.844**.

**Get more than one speaker.** With a single sermon clip the held-out detection
rate read 94% in-fit and **13%** held out — the fit had learned one reader. Four
more public-domain LibriVox sermons were added, and every number below is now
measured with the recording being scored removed from the fit first:

| held out | caught | noticed after |
|---|---|---|
| Sermon on the Mount | 98% | 6 s |
| Calvin, two sermons | 98% | 6 s |
| Calvin on Ephesians | 98% | 7 s |
| Spurgeon, sermon 192 | 95% | 7 s |
| Booth, Purity of Heart | 50% | 9 s |
| **six goldens — false alarms** | **1.3%** | — |

Booth is the honest outlier: a 21 s clip read at 1.87 words/s, inside the range
people sing at, where only match quality can help.

**What was given up.** Brief interjections are no longer caught at this setting.
An aside is two or three events, over before the smoothing fills, and buying it
back cost eleven times the false-alarm rate. Leaving the slide up through a
ten-second *"okay, let's go back"* is the status quo and nobody notices; a blank
chorus is the failure people see. The spoken-instructions work wants the eager
threshold anyway, per the note above about the two consumers.

**Pitch was measured and rejected.** The section below and
Plan/MUSIC_LITERATURE.md said a frame-level pitch feature was where this would
genuinely win, because the limit looked like event granularity. It was tried and
it does not work on a room mic: AUC **0.313** absolute and **0.448** normalised,
against 0.811 within a single recording. The autocorrelation F0 follows the band
rather than the voice. Revisit only with a vocal-only feed.

Refit with `research/eval/fit_singing.py` if the recording set grows.

### Vocal separation (Phase 9) — measured and shelved, 2026-09-03

Checked whether the PyTorch blocker Phase 9 was written around still holds.
It doesn't — an MIT-licensed ONNX export of `htdemucs_ft`'s vocals model now
exists. Measured it on the two goldens with known full-mix WER: **24.7% ->
18.1%**, clearing Phase 9's original 5-point bar. Shelved anyway: the model
needs 7.8 s of context per chunk (architectural, not a speed problem), which
would roughly triple total emission latency — a straight regression on the
lag work earlier in this project. A causal alternative exists on paper
(Band-SCNet, 92 ms latency) but nothing in that class has released weights.
Full detail in Plan/PHASE_9.md and Plan/MUSIC_LITERATURE.md.

### 2. Musical features — NOT WIRED, AND SHOULD NOT BE

Confirmed: no chroma, CENS or fingerprint code is in the sidecar. It lives only
in `research/spikes/spike_chroma_fingerprint.py`.

That is the right outcome, and it is measured rather than assumed. The spike
went a long way — key-invariant, tempo-invariant fingerprints with AUC 0.835 on
whole sections — before the decisive test showed the number was answering the
wrong question. Asked what a live system must answer (*this audio is from
section X; is it X or the section we would otherwise switch to?*, over a window
short enough to act on) the answer was **53%, against 50% for a coin**.

Fusing it was measured too: for every moment it could rescue, it would threaten
**7.2 it would break**.

See `Plan/MUSIC_FINGERPRINTING.md`. The door is not closed — a representation
that discriminates *within* a song at short horizons would change it, and that
is what neural cover-song embeddings are built for — but nothing here should be
wired up on the current evidence.

### 3. A small LLM for spoken instructions — BUILT 2026-09-03, unmeasured on real speech

"Let's go back to verse 1." "Can we bring up the chorus again?" The app used
to hear these and try to match them against lyrics, which is the worst
possible interpretation.

Shipped: `decision.instructions` (one tool, `go_to_slide(song_index,
section_key)` — a second draft with `search_setlist` as a separate tool was
collapsed into this one, since tool *selection* is the harder thing for a
small model to get right, and a single tool leaves only argument-filling),
wired to the singing detector's **unsmoothed** per-event score at the same
0.80 wake threshold blanking uses, applying through the same path an
operator's arrow-key override uses. Both halves of that corrected an earlier
design: the looser 0.5 bar was measured to buy only noise, and the smoothed
score it originally read is a 3-event rolling mean, which caps a two-event
command at 0.667 — under any bar, however clearly it was spoken. Off by default. Full write-up,
including the model comparison and every measured failure:
`Plan/SPOKEN_INSTRUCTIONS.md`.

**The finding that mattered most:** an explicit `no_action` tool is not
optional. Offered only `go_to_slide`, the shipped model called it on 10 of
10 sentences that weren't requests at all. Adding a second, argument-free
"do nothing" tool fixed that outright — 0 of 10 on the same sentences.

**Local, `qwen2.5:3b-instruct` via ollama, `temperature=0`.** It was first
measured and rejected — every judgement timed out past 20 s — and that
reading turned out to be resource contention from a long session, not the
model. Re-tested uncontended it matches `llama3.2:3b` for size and speed,
scores 8/10 against 7/10, and resolves the one case prompt-engineering could
not fix on llama. `llama3.2:3b` stays in the catalog as an alternative. `search_library`
and `show_scripture` are still not tools; the former needs the retrieval
index item 5 depends on, the latter has no data model yet.

**What's still true from the original plan: "do first, collect real
utterances" was never done.** Every number above is on a 20-utterance
synthetic set, typed rather than spoken through ASR noise — a first signal,
not a validated reliability claim. Also still open: the download-on-demand
setup script (`research/spikes/fast_channel/setup_fast_channel.py` is the template) that would
let this ship rather than require a developer's local ollama.

### 4. Jumping between songs — SETLIST DONE 2026-09-01, library still open

Reported: singing whole verses from another song in the set never moves it. That
is correct and expected — **the tracker cannot leave the current song**.

Its entire state is `(slide, word, dwell)` *within* one song. Every lookup goes
through `current_item_idx`, which changes only when an operator overrides. There
is no cross-song hypothesis to win, so no amount of evidence can move it.

This is the largest functional gap on the list. It was Phase 7's stated scope
("within song **+ across setlist**") and only the within-song half was built.

Two levels, and they are different problems:

- **Across the setlist** — **done**. The state is now
  `(item, slide, word, dwell)` and the same recursion arbitrates, so another
  song is an ordinary hypothesis rather than a mode the operator selects.
  Measured on a six-song set built from all six goldens, seeding the tracker on
  the *wrong* song and feeding each recording:

  | | songs found | words needed |
  |---|---|---|
  | before | **0 of 6** | never |
  | after | **6 of 6** | 3-10 |

  Single-song sets are bit-identical (0.443, +630 ms, 161 decisions): with one
  song there is nowhere to go, so the term is inert. Live states grow 283 to
  754 on a six-song set, which is the honest cost.
- **Across the library** (311 songs, 2828 slides) needs a retrieval step first —
  the phonetic fingerprint index Phase 8 describes. Scoring everything every
  update will not fit the budget.

## Order

1. **Cross-setlist jumping.** The biggest gap, a bounded change, and it makes
   the app behave the way people expect on a real Sunday.
2. **Silence and non-singing detection**, starting with the cheap measurement of
   whether the evidence term already knows.
3. **Spoken instructions** — built 2026-09-03, still gated on the same real
   utterances this line originally called for; item 2 handed it the trigger
   as expected, since "this is speech, not singing" is what wakes the model.
4. **Installer and packaging.** Deferred to here deliberately, but it is the
   gate on anyone but us ever running this.
5. **Library-wide recovery**, which needs the retrieval index.

Musical features stay parked, with the evidence recorded so the case can be
reopened by a better representation rather than by optimism.

## Shipping is a constraint on 1-3, not just step 4

Packaging comes fourth in the order and first in every design decision before
it. A choice that works on this machine and cannot be installed on someone
else's has not solved the problem, and this project has already been bitten
three times.

**Revised 2026-09-05 — the hardware floor moved.** This section used to assume
"a volunteer's Windows/Intel laptop" as the target, and that assumption was
quietly ruling out the whole class of fixes the evidence now points at. The
measured picture is that the app identifies the right slide on **89% of cues
and shows it ~1.4 s late**: what is left is dominated by *latency*, not by
identification, and the largest single contributor is ASR pass time. On this
M1, `faster-whisper` runs CPU-only float32 (CTranslate2 has no Metal backend),
1076 ms per 6 s window — slow enough that the sliding window's own pacing rule,
not the configured step, sets the cadence.

So the target is now **a sufficiently fast laptop, or a network connection**.
In 2026 that covers a high share of the people who would run this. Hardware
acceleration that only exists on some platforms is therefore allowed to be a
first-class optional backend rather than something disqualified up front.

This is a relaxation, not an abandonment: memory and latency still get whittled
down wherever it is cheap to do so, and the points below still hold. What
changes is that "it only helps on Apple silicon" — or on any other subset of
machines — is no longer on its own a reason to reject something.

- **No PyTorch at runtime.** The shipped binary carries `onnxruntime` and numpy.
  This is why the phoneme spike and the neural boundary detectors were ruled
  out, and it should keep ruling things out.

  Worth noting how this interacts with the relaxation above: `mlx-whisper`
  *declares* `torch` but never imports it on the inference path — it is only
  reached by `torch_whisper.py`, a checkpoint-conversion utility. Uninstalling
  torch leaves it working at identical speed and takes 526 MB off the venv. A
  declared dependency is not automatically a runtime one; check before
  rejecting.
- **Licences are a hard filter, not a footnote.** madmom's beat tracker fits the
  runtime perfectly and its pre-trained models are CC BY-NC-SA, which makes it
  unusable in a shipped app. Check the *model* licence, not just the code's.
- **Wheels that only work here are not dependencies.** `sherpa-onnx` resolves to
  a macOS wheel missing its bundled `libonnxruntime.dylib`, so it is an optional
  extra installed by a setup script rather than something PyInstaller has to
  freeze. Anything with platform binaries needs testing as an *install*, not
  just as an import.

Applied to what is queued:

- **Spoken instructions should assume no network.** A worship service is exactly
  where the wifi is bad and the failure is public. Hosted APIs are a development
  convenience; the shipped path should be a local model, and that decision comes
  before any model comparison because it changes the shortlist.
- **Models are downloads, not bundles.** Whisper already works this way and the
  fast channel now does too. Anything new should follow it: small binary, fetch
  on demand into the app data directory, degrade clearly when absent.
- **Every new dependency is weight in the installer.** Worth asking of each one
  whether it earns its megabytes — but weigh that against what it buys, now
  that the hardware floor has moved. Megabytes are cheaper than a second of
  lag on the screen.
