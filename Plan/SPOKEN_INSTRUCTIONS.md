# Spoken instructions — "let's go back to verse one"

Built 2026-09-03, roadmap item 3. "Let's go back to verse one." "Can we bring
up the chorus again?" The app hears these today and tries to match them
against lyrics, which is the worst possible interpretation — they are not
sung, they do not appear on any slide, and matching them anyway is exactly
the *off-book* case `decision.singing` already detects.

## Definitions

| Term | Meaning |
|---|---|
| **Tool call** | The one action a model can request: `go_to_slide(song_index, section_key)`. |
| **`no_action`** | A second, argument-free tool meaning "do nothing." Turned out to be load-bearing — see below. |
| **Wake threshold** | The talking-score level above which words get buffered into an utterance, instead of ignored. Looser than the singing detector's blanking bar, on purpose. |
| **Utterance** | Words buffered while the wake threshold is crossed, flushed to the model as one string when it drops back (or after 8 s). |
| **False fire** | The model calling `go_to_slide` on something that wasn't a request — prayer, announcements, banter. The failure that matters, same asymmetry as blanking. |

## Design

One tool, not several. An earlier draft had `go_to_slide` and `search_setlist`
as separate tools; collapsing them removes a tool-*selection* decision (the
harder thing for a small model to get right) and leaves only argument-filling
(the thing they are actually good at). The tool takes a song index and a
section key **already enumerated in the prompt** — never free text, never a
raw slide number the model can't see. It only ever has to copy a token we
handed it.

`search_library` (jumping to a song **not** in the current setlist) and
`show_scripture` are deliberately not tools yet. The former needs the
retrieval index the roadmap sequences after this; the latter has no data
model to search. Both would mean either shipping the whole library into every
prompt or building infrastructure this item was explicitly sequenced ahead of.

The wake trigger reuses `decision.singing`'s score rather than duplicating
the signal, but at its own threshold (0.5, vs. blanking's 0.80/0.40
hysteresis) — the roadmap's own reasoning: a false wake here costs one cheap
local inference that finds nothing and does nothing; a false blank is a black
screen mid-song. The two consumers of the same score should not share a bar.

Applying a decision uses the same code path as an operator clicking an arrow
key (`Director.on_override`) — explicit human intent, not the AI's continuous
fuzzy tracking. It re-anchors the tracker and, unless AI mode is off, flips
the director to `following_human` for the cooldown window. Broadcast as
`SlideChangeMessage(source="voice")` — a fourth value alongside `ai`,
`override`, `manual` — so the debug panel and any future UI can tell a
spoken command apart from the AI's own tracking.

## What was measured, and what wasn't

**Not measured: real utterances.** The roadmap's own gate on this feature —
"collect real utterances first" — has not been met. Everything below is
typed, not spoken through ASR noise, and the model was scored against a
20-utterance synthetic set (10 commands, 10 ordinary between-songs talk), not
a recorded service. Treat what follows as a first signal, not a validated
reliability number — the same caution `holdout_validation` (memory) applies
to any small hand-built test set.

**The finding that mattered most: `no_action` is not optional.** With only
`go_to_slide` on offer, `llama3.2:3b` called it on **10 of 10** sentences that
weren't requests at all — "amen, come on somebody," "we're going to take up
an offering now, ushers please come forward." Ollama's tool-calling has no
strong way to abstain when a single tool is offered; giving the model an
explicit, named way to choose "nothing" fixed it outright:

| | false fires (should call nothing) | notes |
|---|---|---|
| `go_to_slide` only | **10/10** | every ordinary sentence tested triggered it |
| `go_to_slide` + `no_action` | **0/10** | same 10 sentences |

**Model comparison**, both 3B, CPU, `temperature=0` (matching the fix already
shipped for Whisper — this is structured extraction, not open-ended
generation, so sampling only adds variance):

| model | ~s/call | commands correct | correctly silent |
|---|---|---|---|
| `llama3.2:3b` | 0.6–1.2 s | 7/10 | 10/10 |
| `qwen2.5:3b-instruct` | 20 s+ (timed out) | — | — |

`qwen2.5:3b-instruct` never finished a real judgement in the time budgeted —
every command-set call hit the 20 s timeout ceiling on this hardware.
Disqualifying on its own regardless of accuracy: a multi-second wait after
someone stops talking reads as broken, not thoughtful. `llama3.2:3b` shipped
as the default (`AUTO_PRESENTER_INSTRUCTIONS_MODEL` overrides it).

**The three misses**, read individually rather than as one number, because
they are not the same kind of failure:

- *"back to the top please"* → invented `section_key="top"`, which doesn't
  exist → resolved to nothing. Safe: the system fails closed on a
  hallucinated reference rather than moving to slide 0.
- *"one more time on that last part"* → guessed `bridge:1`, hallucinated a
  song index that didn't exist → resolved to nothing. Also safe, though for
  a different reason (this utterance is genuinely underspecified — a human
  operator would need more context too).
- *"let's do the second verse one more time"* → guessed `verse:1` instead of
  `verse:2`. **Not safe** — this resolves to a real, existing, wrong slide.
  The phrase is objectively unambiguous, so this is a genuine model error,
  not user ambiguity. The one finding here that should worry a future
  reviewer more than the raw 7/10.

## Round two, 2026-09-03: a real recording, and a real bug

The user recorded and bootstrapped a genuinely hard multi-song session ("The
Blood" / "Nothing But The Blood", real cross-song jumps, garbled ASR — see
`Plan/ROADMAP.md`'s cross-setlist section) and approved a hand-corrected
golden. First real (if messy) utterances this feature had ever seen.

**Found a real bug testing against it, before the model was even involved.**
`build_context` derived each song's "title" from `item.slides[0].text` — the
leading blank slide, always empty. Every song's name in the prompt was `""`.
The synthetic benchmark above never caught this because it built
`InstructionItem` by hand with a title already filled in, bypassing the code
path that has the bug. Fixed by threading a real `title` field through
`SetlistItemContent` -> `IndexedItem` (frontend: `buildSetlistContent` now
sends `item.song.title`). Same class of gap as the "one sermon clip" lesson
from the singing detector the same day: a benchmark that never exercises the
real code path won't catch what's actually broken.

**Added the context the roadmap always intended and this hadn't shipped
yet:** `InstructionContext.current_section_key` — what the tracker currently
believes is being sung, threaded from the same `ForwardBelief` object
`decision.singing` already reads (`server._matcher_belief`, factored out of
`_observe_singing` so both features share one access path). Grounds relative
references ("that last part," "again") against something instead of only
ever knowing the song.

**A clean instance of the real thing works.** The recording contains a
genuine, unprompted "let's go to the bridge" mid-service. Isolated, with the
title fix in place, it resolves correctly: `go_to_slide(section_key="bridge:1")`
→ the right slide. This is the first real (not synthetic) command this
feature has ever correctly handled.

**The same utterance, in its actual surrounding noise, fails.** The real
Whisper output around it is the command `"let's go to the bridge"`
between two garbled lyric lines — the command is there, but
buried in ASR garbling before and after. The model returns nothing. This
reproduces cleanly and isn't a title or context bug; it's the model losing
the request inside irrelevant surrounding text — a known small-model
weakness, now measured rather than assumed.

**Replayed through the actual production pipeline — `ForwardTracker` for
evidence, `SingingDetector`, `UtteranceBuffer` — not a hand-picked window.**
This is the more important result. On this recording's real (heavily
degraded) evidence stream, the wake trigger constructed only 7 utterances
over 670 seconds, and none of them were the genuine spoken moments — not the
"go to the bridge" instant, not the closing praise monologue. Instead it
fired on short 2-6 word fragments *during garbled singing*, where Whisper's
hallucination (long "please please please..." and "-dud -dud..." loops
elsewhere in this same recording) drags evidence down enough to cross the
wake threshold without anyone having stopped singing at all.

**Reading this honestly:** the wake threshold (0.5) and the detector under
it were tuned on the six clean approved goldens plus synthetic speech —
never on audio this degraded. It is not that the mechanism is wrong; it's
that this recording's ASR quality is bad enough to make match-quality alone
an unreliable signal for "is this speech." The word-rate half of the
detector's score should be more robust to this (rate doesn't care whether
the words are right, only how fast they arrive) but evidently isn't carrying
enough weight to save it here. Worth a dedicated look before trusting the
wake trigger on real services — this recording is now the regression case
for it.

**The general system, measured on the same recording** — `wrong_slide_rate`
0.509 against the six clean goldens' 0.443, which is the expected direction
given this recording is deliberately much harder. The more informative read,
matching the user's own framing ("a human would have the wrong slide for a
few seconds and then quickly recover; the failure mode is not recovering"):
of this recording's 5 real cross-song jumps, 4 were followed within 0-2.1s
and the 5th within 31.7s — the cross-setlist tracking shipped 2026-09-01
holds up well on real, hard, ambiguous audio. Three ordinary within-song
repeats took roughly five minutes to resolve (stuck on a nearby-but-wrong
slide through a stretch of near-identical verse lines before catching up
much later), and two never resolved within the recording — one is the
wordless blank slide during a spoken interlude (documented, expected: no
lyric evidence exists for a blank slide), the other a late chorus repeat.
The within-song repeats, not the cross-song jumps, are where this
recording's real trouble is.

## Where this leaves it

Shipped: the tool-calling core, the `no_action` fix, real song titles, and
current-section context. Off by default, dedicated debug tab (`Cmd/Ctrl+D` →
Instructions) so the toggle is reachable without an active session. Needs a
local `ollama` server with a model pulled — no setup script yet (the
shipping-constraint pattern used for the fast channel and Whisper:
download-on-demand into the app data directory) is follow-up work, not done
here. `AUTO_PRESENTER_INSTRUCTIONS_MODEL` env var picks the model;
`research/spikes/fast_channel/setup_fast_channel.py` is the template to copy.

**What actually blocks trusting this now, in priority order:**

1. **The wake trigger doesn't reliably find real spoken moments on hard
   audio.** Measured directly, above. This is the binding constraint — a
   perfect model behind a wake trigger that fires on the wrong 7 seconds out
   of 670 accomplishes nothing.
2. **Noise robustness.** A model that gets the isolated command right but
   loses it in realistic surrounding words needs either a tighter utterance
   boundary or a more capable model — can't tell which without more real
   cases.
3. **Still true from round one:** record real utterances deliberately (a
   dozen services' worth of between-songs talk), rather than relying on
   whichever ones happen to land in a golden bootstrapped for other reasons.

## Three candidate fixes for the wake trigger, measured 2026-09-03

Ranked options were researched broadly first (vibrato, jitter/shimmer/HNR,
AudioSet classifiers, hosted APIs, rhythm-locking, Silero VAD, Whisper's own
confidence fields — full list not reproduced here). Three were picked to
measure: cheapest first, most novel last. None solved the problem; each
taught something concrete.

### Silero VAD, fused as a third feature — real gain on paper, made the actual failure case worse

We already carry Silero as a dependency (when this was written it had a
wrapper at `asr/vad.py`, unused because it drops sung words; that file has
since been deleted). Fused into the compound score as a
third feature (`rate`, `1-evidence`, `silero`), leave-one-recording-out:

| | shipped (2 features) | + silero (3 features) |
|---|---|---|
| false alarms, pooled over singing | 1.3% | **0.3%** |
| real short interjections caught | 0% (all three) | 2-22% |

A real, measured improvement — first time the three genuine user
interjections registered at all. **Then tested end-to-end on the actual hard
recording, replayed through the real production pipeline** (not a holdout
split): utterance count went from 7 (all useless, per the original finding)
to **zero**. Checked why: Silero's speech-probability is not just weak on
real talking, it is **indistinguishable from zero whether singing or
talking** whenever the recording is a loud full band — max 0.26 across the
entire 25-second span containing "let's go to the bridge," spoken while the
band was presumably still going. The LOO improvement was real but almost
entirely attributable to correctly handling the *clean, band-silent*
majority of the talking class (the sermon clips); on genuine full-mix
talking it contributes nothing and can make the fused score more
conservative than the two-feature version. Silero's actual operating domain
is "is there any voice over near-silence," and a live worship room is never
near-silence.

### Word-onset rhythm-locking — null result, likely reasons identified

The literature is unusually clean on speech having no syllable-level
periodicity while song entrains to a beat. Built it: reused the onset-flux +
autocorrelation tempo estimator from the earlier musical-fingerprinting
spike (`beat_phase.py`), added circular-statistics concentration of word
onsets modulo the estimated beat period in a trailing 6 s window. Tested
same-recording (singing window vs. the recording's own real talking span,
avoiding the room/mic confound that sank pitch stability on its first pass):

**AUC 0.473 — chance.** Three plausible reasons, not disambiguated by this
pass: (1) a single global tempo estimate per recording doesn't survive real
tempo drift or rubato phrasing — worship ballads hold notes and stretch time
far more than the rhythmically strict material most of the speech-vs-song
literature is built on; (2) the talking spans are only 12-15 s each, giving
very few 6 s windows to measure (n=14 total) — underpowered on its own
terms; (3) if the band quiets rather than stops during a spoken transition,
there may be no coherent beat grid for *either* class to lock to or fail to
lock to, which would produce exactly this null result without saying
anything about the underlying premise. Not disproven — undertested with a
plausibly-too-crude implementation. A per-window local tempo estimate
(rather than one global one) and more real talking data would be needed
before concluding the idea itself is wrong.

### Whisper's own free confidence fields — confounded by a hallucination collapse, not yet trustworthy

`no_speech_prob`, `avg_logprob`, `compression_ratio` cost nothing extra —
already computed on every segment. On clean data (six goldens + sermons +
the three real interjections): `compression_ratio` AUC 0.827, `avg_logprob`
AUC 0.766, both favoring the expected direction (talking = more confident,
less repetitive).

**Spot-checked against the hard recording before trusting it, and the
methodology broke first.** A single full-clip `model.transcribe()` call
(no windowing) on the 670 s recording collapsed from t=56s to t=662s — over
600 seconds, effectively the whole thing — into two alternating hallucinated
lines ("Old precious is the flow" / "That makes me quite small"), with
`compression_ratio` climbing to **7.40** as the decoder's self-conditioning
fed its own repetition back into context. That is far outside the clean
dataset's singing range (median 1.247) and inverts the measured direction —
here, high compression correlates with a *singing* passage, not a talking
one. This may be substantially an artifact of transcribing the whole clip in
one call rather than through the production `SlidingWindowWrapper` path
(bounded 6 s windows, different context-conditioning behavior) — not yet
separated from a genuine property of the audio. Unresolved: re-run this
measurement through the actual production transcription path before
concluding anything about these fields one way or the other.

### Where this leaves the wake trigger

All three measured ideas either failed to transfer to full-mix worship audio
(Silero, in the specific way that matters) or produced an inconclusive
result this pass didn't have time to disambiguate (rhythm, confidence
fields). The pattern repeating across today's entire investigation — pitch
stability, vocal separation's context requirement, and now this — is that
**methods built around isolating a clean vocal or clean-speech signal keep
failing on room-mic full-band audio**, while the two features that
transfer at all (evidence, word rate) are exactly the ones that never
needed isolation in the first place. The strongest untested candidate from
the original ranked list is still AudioSet-pretrained classifiers
(YAMNet/PANNs) — trained on real messy YouTube audio rather than clean
speech corpora, which is the first candidate in this entire search whose
*training data* already looks like ours.

## YAMNet, gone deep: a confound in our own ground truth, not just another negative result

Went looking for the best of YAMNet's 521 AudioSet classes rather than
guessing Speech/Singing/Music — ranked every class by discrimination on the
"real talking" spans used all day (the three short user interjections).
Caught a documentation bug on the way (a mirrored model card said
`Singing=27`; the real AudioSet index is 24, 27 is `Chant` — fixed and
re-measured, no material change). The ranking surfaced almost nothing real:
only **Silence** and **Speech** carried any actual probability mass out of
521 classes, and Speech was backwards — higher during singing (0.194) than
during the labeled talking (0.002).

**Checked why, and it reframes today's entire investigation.** Measured
RMS level: the three labeled talking spans run **9-22 dB quieter** than
their own recording's singing — genuinely soft, back-from-the-mic asides
("Okay. Mm-hmm."). The actual target case — "let's go to the bridge" in the
hard recording — runs at **-0.3 dB relative to the whole recording**,
essentially full volume. These are not the same phenomenon. Every classifier
tested today that looked either promising or confusingly backwards
(Silero's LOO gain, YAMNet's Speech class here) was very plausibly reading
**level**, not content — and a quiet mumble happens to be the easy case for
a level-sensitive detector, while a clearly-spoken, on-mic instruction is
not, because it doesn't offer a level cue to exploit at all.

**Rebuilt the labeled set from four genuinely representative moments in the
hard recording** — the bridge announcement plus three more spoken stretches
at varying but broadly comparable levels (-0.3 to -9.8 dB relative,
nowhere near the -22 dB extreme) — and reran the full 521-class scan.
**Only 7 of 521 classes showed any real mass at all**, and none exceeded
AUC 0.63. `Speech` landed at **0.508 — exactly chance**. `Singing` was
weakly backwards again (0.350). The mild survivors (`Music` 0.632,
`Acoustic guitar` 0.577) are instrument classes, plausibly registering that
solo backing is cleaner to classify without a competing vocal melody — not
a route to "is someone talking."

**This is the most important finding of the whole investigation, not just
about YAMNet.** On a fair, level-matched, real, on-mic test, a 521-class
general-purpose audio classifier finds *nothing*. The earlier Silero result
(1.3%→0.3% false alarms in leave-one-out) should now be read with the same
suspicion — it was validated against the same quiet-mumble spans, so the
gain may be substantially the same level artifact rather than genuine
content discrimination, and may not hold at all against a real, clearly-said
instruction. Re-validating Silero against level-matched spoken moments
(the same four spans used here) is the obvious next check before trusting
that result any further.

**Where this leaves the search:** every acoustic and content-based signal
tried — pitch, vocal separation, Silero, rhythm, Whisper confidence, and now
a from-scratch scan of 521 general audio classes — fails or reverses once
tested against genuinely representative, same-level, real audio. This is no
longer "the first four ideas didn't pan out, try a fifth." It is evidence
that acoustic/content-based singing-vs-speech detection may not be tractable
at the fidelity a single full-mix room mic provides, at least not without a
labeled dataset and a purpose-trained model — the "different-shaped
project" conclusion reached earlier for vocal separation and for a
from-scratch SVD model now looks like the load-bearing one, not a
last-resort footnote.

## Silero re-validated against level-matched spans, and the trigger reworked, 2026-09-03

**Silero re-checked against the four same-level spoken moments** (not the
quiet mumbles): AUC **0.404** — worse than chance, both classes at
essentially zero. Confirms the suspicion directly: the earlier leave-one-out
gain (1.3% -> 0.3% false alarms) was the level artifact, not real
discrimination. Silero contributes nothing to this problem and isn't worth
carrying as a feature.

**Response: stop asking the acoustic gate to buy recall it was never
delivering, and make it cheaper instead.** Swept `UtteranceBuffer`'s wake
threshold against the hard recording and the clean goldens: the genuine
on-mic instruction ("let's go to the bridge") never crossed *any* threshold
from 0.5 to 0.9 — confirming, again, that the acoustic score cannot tell a
clearly spoken instruction from degraded singing. What the threshold *does*
control is noise: 5-9 spurious utterances per clean recording at 0.5, cut to
1-4 at 0.8. Raised the default to 0.8, matching blanking's bar — there was
no recall to trade away, so this is a clean win against wasted inference
calls and fragmented junk utterances. See `UtteranceBuffer`'s docstring.

**Tried strengthening the prompt to fix the one dangerous case measured so
far** ("let's do the second verse one more time" -> confidently wrong
verse:1, not a safe no-op) — twice, both attempts backfired badly. Adding
any hedging language ("only pick a section if it's clearly named...")
collapsed command recall from 7/10 to 3/10, then 1/10, on cases that were
never ambiguous ("pull up the bridge" started failing too) — and still
didn't fix the targeted case. On a 3B model, extra qualifying text produces
global hesitancy, not selective caution. Reverted to the original prompt.

**The fix turned out to be a different model, not a better prompt — and a
free one.** Re-tested `qwen2.5:3b-instruct`, which an earlier session had
rejected for timing out past 20s. That rejection was wrong: it was resource
contention from a long session running many parallel workloads, not a
property of the model. Re-tested clean:

| model | disk | RAM loaded | processor | latency/call | commands correct | correctly silent |
|---|---|---|---|---|---|---|
| llama3.2:3b | 2.0GB | ~2.1GB | 100% GPU (Metal) | 0.6-1.2s | 7/10 | 10/10 |
| qwen2.5:3b-instruct | 1.9GB | ~2.1GB | 100% GPU (Metal) | 0.7-1.5s | **8/10** | 10/10 |
| qwen2.5:7b-instruct | 4.7GB | ~4.5GB | 100% GPU (Metal) | 1.7-3.3s | **9/10** | 10/10 |

`qwen2.5:3b-instruct` is a strict upgrade at the same size and speed —
including correctly resolving the "second verse" case that no prompt change
could fix on llama3.2:3b. Switched the default
(`_spoken_instructions_model()` in `server.py`). `qwen2.5:7b-instruct` is
better still (9/10, and it's the only model to get "back to the top please"
exactly right) at roughly double the latency and more than double the RAM —
available via `AUTO_PRESENTER_INSTRUCTIONS_MODEL=qwen2.5:7b-instruct` for
hardware that can carry it, not the default.

**What this benchmark can't tell us.** Every number above, on every model,
ran 100% on Metal GPU on this Apple Silicon dev machine — meaning zero CPU
contention with Whisper, which is good, but also meaning none of this
speaks to a volunteer's Windows or Intel laptop, which is exactly the
hardware the shipping constraint is worried about. Forcing `num_gpu=0` via
ollama's per-request option showed *no* latency difference on this machine
(1.1-1.3s either way) — but that almost certainly reflects Apple Silicon's
CPU path being independently fast (Accelerate-optimized kernels, unified
memory bandwidth), not evidence that a weaker x86 CPU would keep pace. This
machine cannot produce that number; it needs testing on the kind of hardware
this app is actually meant to ship to.

## A generic backend, a model catalog, and download-from-the-UI, 2026-09-03

Built out model switching properly rather than a hardcoded `OllamaBackend`
call: local models downloadable and switchable from the debug panel, plus
two hosted providers for anyone who wants to try a bigger model than the
machine can run, or has no machine to run one on at all.

**One backend, not three.** Verified ollama's `/v1/chat/completions`
endpoint behaves identically to its native `/api/chat` (same request/reply
shape once refactored, same 8/10-command, 10/10-silent numbers on
`qwen2.5:3b-instruct`), and confirmed Groq and Gemini's OpenAI-compatibility
layers document the same `choices[0].message.tool_calls` shape. So there is
one implementation, `GenericLLMBackend(base_url, model, api_key=None)`, used
for local and hosted alike; `OllamaBackend` survives as a thin, named
convenience over it, not a separate code path. Hosted providers (Groq's
`llama-3.1-8b-instant`, Gemini's `gemini-3.5-flash`) are listed and wired to
the documented contract but **not measured end-to-end** — no key was
available to test against. `decision.instruction_models.CATALOG` is the
single place a new model gets added.

**Found a second real bug, the same class as the first.**
`SetSpokenInstructionsRequest` and `SpokenInstructionMessage` — from the
original build — were defined, imported, and dispatched on by `isinstance`
checks, but never added to `ClientMessage`/`ServerMessage`. The server
validates every incoming frame through
`TypeAdapter(ClientMessage).validate_python(...)` *before* any handler
runs, so a message missing from the union fails validation and never
reaches its handler — the toggle checkbox has never actually been
reachable from a real client, only from tests and scripts that construct
the Python object directly and skip validation entirely. Fixed both, and
added `test_protocol.py::test_every_message_is_reachable_through_a_union`,
which introspects every `kind`-bearing message class in the module and
fails if any aren't in a union — verified it actually catches this class of
bug by deliberately reintroducing it and watching the test fail. The four
new model-catalog messages went through the same union from the start,
written with this test already in place.

**Found a live bug in the new UI while verifying it in the browser.**
`InstructionModelPicker` fetched the catalog on mount with a bare `void
refreshInstructionModels()` — no `.catch()`. With no sidecar reachable
(browser dev mode was the case in hand, but any dropped connection would do
the same), the request rejects, nothing catches it, and the panel hangs on
"loading models…" forever while an unhandled promise rejection prints to
the console. Fixed by tracking a distinct `refreshError` state and wrapping
every direct action call (switch model, download, save key) the same way —
this class of bug doesn't show up in unit tests that mock `conn.request`
to always resolve, only in an actual disconnected browser, which is why it
was caught live rather than by the otherwise-thorough test suite added
alongside it.

**Storage.** API keys live in plaintext JSON in the app data directory
(`decision.instruction_keys`) — the same trust boundary as `library.db`,
not OS-keychain-hardened. An env var (`AUTO_PRESENTER_{PROVIDER}_API_KEY`)
always wins over a saved key, for anyone who'd rather not have it on disk
at all. Documented as a real limitation in the module docstring, not papered
over.

**Test coverage added:** catalog structure (uniqueness, required fields per
kind), key storage (round-trip, env-var precedence, corrupt-file handling),
`GenericLLMBackend` against a mocked OpenAI-compatible endpoint (request
shape, response parsing, the "null"-string quirk, network failure) — the
first real unit tests either backend has had, since `OllamaBackend` was
previously only ever measured against a live server — `ollama_manager`'s
pull-progress line parsing, server dispatch for all four new handlers, and
the frontend store's request/response and broadcast paths. 716 sidecar
tests, 177 frontend tests, both suites green.

**What's still open:** Groq remains unverified (no key). The non-Apple-Silicon
latency question from the model comparison above still applies to every local
model — nothing in this session's environment can measure it.

## Gemini Flash, verified end-to-end, 2026-09-03

The user added a real key via the app's own storage (the debug panel field,
not this conversation — `instruction_api_keys.json` already had it before
any testing started). First pass at the full synthetic benchmark came back
contaminated: several results returned in 0.2-0.3s, far faster than the
~1.5s genuine calls, and turned out to be `429 Too Many Requests` errors
silently swallowed as `None` by `GenericLLMBackend`'s deliberately-quiet
error handling — indistinguishable from a genuine `no_action` judgment in
the raw output. Caught by the same instinct that caught the hallucination
collapse and the level confound: an anomalously fast or clean result is a
reason to check the mechanism, not celebrate it.

Re-ran with request pacing (4.5s between calls, one retry with backoff on a
429) and rate-limit failures surfaced distinctly rather than folded into
`None`:

| | result |
|---|---|
| commands correct | **9/10** — tied with `qwen2.5:7b-instruct` for the best measured, local or hosted |
| correctly silent | 5/5 completed calls (5 of 10 still hit 429s even at this pacing) |

**The free tier's real usable throughput is tighter than its documented 15
req/min.** Half the non-command calls were rate-limited at roughly 13
req/min of actual load. Anyone relying on this in practice needs a bigger
safety margin than the advertised number — space requests out more than
the docs suggest, or expect occasional failures under bursty use.

Catalog note updated to reflect the real numbers. Groq is now the only
remaining unverified entry.

## Groq, verified end-to-end, and a real bug on the way, 2026-09-03

First attempt: every single call, command or not, came back `HTTP 403`.
The body was `error code: 1010` — a **Cloudflare** error ("banned based on
your browser's signature"), not a Groq API error and not a bad key.
`urllib.request`'s default User-Agent (`Python-urllib/3.x`) reads as a bot
to Cloudflare's edge, which sits in front of Groq's API, and blocks the
request before it ever reaches Groq. Added a real `User-Agent` header to
`GenericLLMBackend` — fixes this for *any* hosted provider behind similar
bot protection, not just Groq. Confirmed with a regression test.

With that fixed, a second, unrelated problem: `404 model_not_found` on
`llama-3.1-8b-instant`, the catalog's original pick. Checked
`GET /v1/models` with the live key — it's been retired from Groq's lineup
since the catalog was written. **Lesson for this catalog going forward:**
hosted model availability moves out from under a hardcoded name; check the
live model list before trusting one, the same discipline already applied
to local ollama tags.

Replaced with `openai/gpt-oss-20b`, current and available. Full paced
benchmark:

| | result |
|---|---|
| commands correct | **9/10** |
| correctly silent | **10/10** — the only model with a complete, non-rate-limited sample on both |
| latency | **0.2-0.7s/call** — the fastest of everything measured, local or hosted |

The best result measured all session, on every axis at once. Groq's actual
selling point (custom inference hardware, not just "an API") shows up
directly here — completion times in the raw response were 50-80ms; the
rest is network round-trip. Free tier (30 req/min) never came close to
being hit at this pacing, unlike Gemini's tighter limit.

Both hosted providers are now verified. Local `qwen2.5:3b-instruct` remains
the shipped default (no network dependency), but for anyone with a
connection, Groq is the strongest option measured so far.

## Two live bugs, found using it for real, 2026-09-03

"Blank the screen when nobody is singing" wouldn't check. `SingingRow`'s
checkbox was bound to `state.blanking` — *is it blanking right now* — not
to whether the feature is *on*. Checking it while singing is on-book looks
identical to it silently unchecking itself, since `blanking` stays false
until something actually goes off-book. Same failure class
`spokenInstructions.ts` already avoided by design; `singingState.ts` just
didn't get it. Fixed with a real `enabled` field, optimistic across
broadcasts, same pattern.

The Fast Channel tab's Whisper-word display read backwards ("chorus the to
go" instead of "go to the chorus") — `.slice(-15).reverse()` reversed word
*order*, not just which end of the list was visible. Removed the reverse;
`TranscriptTab` already did this correctly and is what the fix now matches.

## Why "go to the chorus" never showed up in Utterances Heard, 2026-09-03

Reported live: clear, correctly-transcribed commands never appeared in the
Instructions tab's history at all — not even as a rejected "no action"
entry. That list is populated only when `UtteranceBuffer.observe()` flushes
something (`server._observe_spoken_instructions`), so silence there means
the wake gate never opened — the same threshold limitation measured
earlier this session, now confirmed live. But *why*, mechanistically, was
worth pinning down rather than re-asserting the existing finding.

Fed the actual `SingingDetector` + `UtteranceBuffer` classes (not a
description of them) a synthetic sequence: singing at the docstring's
measured medians (evidence 0.268, 1.25 wps), one 4-word command spoken fast
and off-book (evidence 0.03, ~2.25 wps after the rate window mixes in
adjacent singing), then singing resumes.

The command's own single-event `talking_score` was **0.968** — about as
confident as the score ever gets, and clear proof the per-event signal
*does* discriminate a real command from singing, contrary to how the
earlier "it doesn't discriminate at all" framing read. What kills it is
`UtteranceBuffer` reusing `SingingDetector.score` — a **3-event rolling
mean** built for blanking, where the goal is *not* flickering on a single
noisy frame. Averaged with the two singing events on either side, that
0.968 event only pulls the smoothed score to **0.508** — nowhere near the
0.8 wake threshold — and the utterance is over before enough consecutive
high-scoring events could accumulate to clear it.

This reframes the fix's shape. The earlier conclusion ("raise the
threshold, let the LLM be the precision filter") is still right about the
LLM's role, but the wake *gate* has a specific, addressable defect: it's
using a smoothed signal built for a different consumer with different
requirements (blanking wants stability over 6+ seconds; waking wants to
catch a single loud, fast, off-book utterance in 1-2). An unsmoothed
per-event `talking_score`, or a much shorter window (e.g. max-of-last-2
instead of mean-of-3), would likely recover real commands without
reopening the noise problem the smoothed threshold was raised to fix —
untested, and the next thing to measure before touching the wake gate
again.

## Groq Whisper as an ASR backend, 2026-09-03

Groq's hosted Whisper endpoint (`GroqWhisperBackend`, `asr/groq_backend.py`)
now satisfies the same `ASRBackend` protocol as local `WhisperBackend` —
`SlidingWindowWrapper` and everything above it compose it identically,
zero pipeline changes. Reuses the same "groq" API key already saved for
spoken instructions (`api_keys.py`, renamed from `instruction_keys.py` now
that a key is an account credential, not a feature one) — no extra setup
for anyone who already added a Groq key.

The instructions LLM and this ASR backend face a very different traffic
shape, though, so the same provider needed separate viability numbers.
`GenericLLMBackend.decide()` runs once per detected utterance — a network
round trip is nearly free. `SlidingWindowWrapper` calls `transcribe()`
roughly once per second, continuously, for the length of a service.

Measured against a real key on real worship audio (8 windows, 6s each,
`whisper-large-v3-turbo`):

| | result |
|---|---|
| round-trip latency | p50 **0.94s**, min 0.77s, max 2.01s |
| for comparison | local `tiny.en` ~0.34s p50, `base.en` ~0.79s p50, `small.en` ~2.1s p50 (all CPU, int8, M1) |
| daily request quota (measured from live response headers) | **2,000 requests/day** |

Latency lands in the same band as local `base.en`/`small.en` — `SlidingWindowWrapper`'s
adaptive pacing (`elapsed * 1.5`) would settle around 1.2-1.4s between
passes, a real but non-disqualifying cost, the same trade a locally-hosted
mid-size model already makes. Transcription quality on real sung lyrics
looked good, plausibly a large-v3-class model beating any locally-runnable
size at zero extra RAM/CPU.

The daily quota is the real constraint, and it's a *tighter* one than the
instructions use case ever faced. At roughly one request per ~1.2-1.5s of
continuous cadence, a single 45-60 minute live service costs on the order
of 1,800-3,000 requests — at or over the entire free-tier daily allowance,
in one service. This is not a "use it and see" number the way the
instructions rate limits were; it's a hard ceiling worth knowing before
relying on it as a service's primary transcription path.

Wired into the debug panel's Transcript tab as an opt-in, hosted option
alongside local Whisper sizes and the mock backend — matching
`constraint_shipping`'s "no network at service time" default (local stays
the shipped default; this is switchable per-session for anyone with a
connection who wants to try it, with the quota caveat shown inline in the
UI). Selecting it without a saved key surfaces an inline key field, same
pattern as `InstructionModelPicker`.

## A command was heard, resolved, and applied — and the slide still didn't move, 2026-09-03

The wake-gate fix worked: sung words, then a clear "let's go back to the
first verse," registered in the Instructions tab. The slide stayed put
anyway. This turned out to be a completely different bug from the wake
gate, sitting one layer further downstream, and a bigger one — the feature
had never been able to move a slide from a real client, only from a test
that calls `engine.decide()` directly.

`server._observe_spoken_instructions` does everything right: resolves the
utterance, calls `director.on_override(...)` (re-aiming the matcher), and
broadcasts `SlideChangeMessage(source="voice")`. `protocol.py` even
documents `"voice"` as its own case, "worth telling apart in the UI." But
the only frontend consumer of that broadcast, `useAiDecisions`
(`src/lib/aiDecisions.ts`), had a hard filter: `if (msg.source !== "ai")
return;`. A `"voice"` broadcast landed on every connected client and was
thrown away by every one of them, silently — no error, because from the
frontend's perspective nothing happened, which is indistinguishable from
nothing being sent.

Fixing the filter wasn't enough on its own. Both consumers of
`useAiDecisions` (`PresentationView`, `OperatorView`) also gate applying
any decision on `initialAiMode === "active"` — deliberately, since shadow
mode's whole point is "the AI decides but doesn't touch the display." But
a spoken instruction isn't the AI's continuous fuzzy tracking; it's the
same server-side comment that says so: "the same authority as an operator
override." An arrow key moves the slide no matter what AI mode is set to.
A spoken command needs the same exemption, or it silently does nothing in
shadow mode (the mode most operators would reasonably run to trust the
feature before flipping to active) — which is exactly the mode the user
was testing in.

Fixed in both places: `AiDecision` now carries its `source` through
(`"ai" | "voice"`), and both consumers gate the AI-mode check on
`source === "ai"` specifically, applying `"voice"` unconditionally. Pinned
with `src/__tests__/aiDecisions.test.tsx`, since nothing previously tested
`useAiDecisions`'s filtering at all.

The lesson for next time: every measurement and fix up to this point
exercised the pipeline up to "did the engine resolve the right slide" —
synthetic benchmarks call `engine.decide()` directly, and the wake-gate
investigation stopped at "did `UtteranceBuffer` flush." Nothing in this
session had actually watched a spoken command move a slide in the running
app until the user did, live, just now. The synthetic/unit-level rigor
this session leaned on hard is real and was worth it, but it has a
boundary: it proves the decision is right, not that the decision reaches
the screen.

## Code review: three real bugs in the competing-decisions surface, 2026-09-03

Asked to review this session's work and specifically probe the ways
different sources (keys/clicks, the continuous AI matcher, the fast
channel, and voice) can compete to move a slide. Found and fixed three,
none of them hypothetical — each was demonstrated with a test that fails
without the fix and passes with it.

**1. Out-of-order voice instructions.** `_observe_spoken_instructions`
dispatches `engine.decide()` via `asyncio.to_thread` — a ~1s+ call whose
latency varies (0.2-2s+, measured for Groq/Gemini this session). Two
utterances spoken close together can have overlapping in-flight calls that
*finish* in a different order than they *started*. Without a guard, a
slow, stale first result can land after a faster, more recent correction
and silently win — e.g. "go to the chorus" ... "no, the bridge" ending on
the chorus because that call happened to take longer. Fixed with a
monotonic `_state.voice_dispatch_seq`, bumped at dispatch time; a call
whose sequence number has moved on by the time it returns is dropped
regardless of what it decided. This also protects the fast-channel path
transparently — when the debug-only "feeds matcher" toggle mixes
fast-channel words into the same handler as Whisper's, both flow through
this same guarded function. Test:
`tests/test_server_spoken_instructions.py::test_a_slow_stale_utterance_does_not_clobber_a_newer_one`.

**2. A live AI-mode switch was silently ignored.** `AISettings` lets the
operator flip AI mode while a session is already running ("start in
shadow, flip to active once you trust it" — its own description) and
fires `set_ai_mode` immediately, and the sidecar's `Director.mode` really
does update right away. But `PresentationView` and `OperatorView` each
read `loadAiPrefs().mode` once at mount into `initialAiMode` and gated
every "ai"-sourced decision on that permanent snapshot — so a live switch
to active never took effect until the view remounted (leaving a service,
re-entering it). This is the workflow the settings screen's own copy
recommends, silently not working. Fixed with `useLiveAiMode()`
(`src/lib/aiDecisions.ts`), which prefers the sidecar's live `ai_status`
broadcast over the mount-time snapshot. While in there, pulled the
AI-mode gating rule itself out of both views' independently-drifted inline
`if` statements into one tested function, `shouldApplyDecision` — that
drift (one copy correctly exempting "voice", conceptually, before the
copies existed at all) is exactly the shape of bug the previous session
entry describes. Tests: `src/__tests__/aiDecisions.test.tsx`'s
`shouldApplyDecision` and `useLiveAiMode` describe blocks.

**3. (Adjacent, found auditing test coverage, not this feature but hit
during the same review) `ollama_manager.pull()` reported success after a
failure.** An in-stream `{"error": ...}` line from ollama's pull API
correctly produced a "failed" progress event, but `pull()`'s final
`emit(..., "completed", ...)` ran unconditionally after the loop
regardless of what happened inside it — so a failed download was reported
to the UI as having succeeded. Only `_iter_pull_lines` (the parsing
helper) had a test; `pull()` itself, where the bug actually lived, had
none. Fixed by tracking whether a failure was seen and raising instead of
falling through. Tests: `tests/decision/test_ollama_manager.py::TestPull`.

**Sources that can move the live slide, confirmed by tracing the code
rather than recalling it:** operator keys/clicks (local-first, mirrored to
the sidecar for matcher re-aim only), the continuous AI/matcher tracking
fed by Whisper's transcript (`source="ai"`, gated on live AI mode being
`active`), the fast channel (diagnostic-only unless the debug "feeds
matcher" toggle is on, in which case it shares the exact same pipeline —
and therefore the same ordering guard — as Whisper), and spoken
instructions (`source="voice"`, applies unconditionally). The AI mode
toggle itself is a fifth, cross-cutting factor rather than a source in its
own right — bug 2 above. Checked and ruled out: two simultaneously open
windows (`PresentationView` + the audience projection) racing each other —
the audience window is a passive Tauri-IPC mirror of whichever single view
is live, not an independent decision source, so this isn't a real
avenue.

**Near-miss during the review, worth recording:** verifying fix #1 by
temporarily reverting it, I ran `git checkout -- auto_presenter/server.py`
to restore the original — forgetting that file carried this entire
session's *other* uncommitted changes too (protocol/handlers/groq
backend), not just my one-line test edit. It discarded all of it. Recovered
by reconstructing the file from the full diff captured earlier in the same
review (this transcript had it verbatim) and reapplying it with `git
apply`, then redoing the fix on top — verified via the full test suite
returning to the expected count (732) afterward. No data was actually
lost, but the mechanism was luck (having just printed the diff) rather
than safety margin. Lesson: never run a bare `git checkout --`/`restore` on
a file with mixed old-and-new uncommitted changes to undo a small
experiment — stash, or copy the file aside, or revert the specific hunk
instead.
