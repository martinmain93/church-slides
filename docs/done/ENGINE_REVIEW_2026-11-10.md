# Engine review — 2026-11-10

Goal: find what could put the wrong slide on screen, freeze it, or put a
church's computer at risk, before the next release. Deep read of the live AI
engine; a lighter pass over the rest of what ships.

**Answer in one paragraph.** The tracking maths is in good shape; the
problems are at the seams. The engine keeps its own record of what the screen
shows and that record goes wrong in several ordinary situations (an operator
click, a setlist edit, a move the app declines), after which it stays a slide
behind or jumps song. Separately, the engine's local socket accepts commands
from anything on the computer, including a web page, and one of those commands
can delete a folder of the caller's choosing. Three findings are high, seven
medium. Five were reproduced; the rest are from reading the code and are
marked so.

Reviewed: working tree on branch `research/public-data` at `d44fe90`.

## Words used here

| Term | Meaning |
|---|---|
| Word tracker | The original engine: follows Whisper's words through the lyrics (`tracker/forward.py`) and decides slides (`decision/scheduler.py`). Drives slides on Intel/Windows, with Groq, for readings, and for song changes everywhere. |
| Score follower | The newer engine for slides inside a song (`follower/`). Default on Apple silicon. |
| Screen record | The engine's own note of which slide is showing. It is updated when the engine *decides*, not when the screen changes. |
| Cooldown | The 5 s after an operator moves the screen, during which the AI is meant to leave it alone. |
| Watchdog | Moves the screen when the tracker has disagreed with it for 16 s. |
| Engine socket | The local WebSocket the app uses to talk to the Python engine. |

## What was done, and what was not

- **Read in full:** the audio session and transcription loop, all three speech
  backends, the word tracker, scheduler, director, plan gate, follower engine,
  live follower, online follower, next-line check, span scorer, singing
  detector, the session/transcript/follower/voice/import paths of the server,
  the engine entry point, and the app shell's engine launcher.
- **Skimmed or searched:** recordings, the reference builder's evidence
  functions, band screens, peer sync, the assistant link, app config and
  entitlements, the release script, how the app applies AI decisions.
- **Run:** three small in-memory scripts against the engine's own classes
  (no files written, no microphone, no models). Their output is quoted below.
- **Not run:** the test suites, a build, the app. Another session was preparing
  0.2.0 in this checkout and did not answer when asked, so nothing that uses
  the GPU or touches shared state was started.
- **Not reviewed:** the reference builder beyond its evidence functions,
  in-service learning, scripture importers, chord tools, lead-sheet reading,
  the spoken-request prompt, the relay and stats Workers, the website, most of
  the React UI, database migrations, Windows-only paths.

## Findings

| # | Severity | Finding | Status |
|---|---|---|---|
| 1 | High | A move the screen never made is recorded as made | Reproduced |
| 2 | High | Editing the setlist mid-service makes the AI forget the screen and jump song | Reproduced |
| 3 | High (security) | Engine socket takes commands from any local page; one deletes any folder | Read |
| 4 | Medium | The cooldown is timed from the AI's last move, not the operator's click | Reproduced |
| 5 | Medium | A slide change waits behind side features, including a hosted model call | Read |
| 6 | Medium | Watchdog steps the screen back a slide 16 s into a break | Reproduced |
| 7 | Medium | One bad number from the scorer stops the follower and shows slide 0 | Reproduced with an injected value |
| 8 | Medium | AI "off" still runs the follower, chord analysis and next-line check | Read |
| 9 | Medium | Band leader PIN can be guessed in seconds | Read |
| 10 | Medium | On the processor path one failed speech pass ends the session | Read |
| 11–18 | Low | Smaller items, listed at the end | Mixed |

### 1. A move the screen never made is recorded as made — HIGH

**In the app:** after an operator click or a spoken request, the AI can sit one
slide behind for a whole slide, or think it is in a song the screen never went
to. Nothing notices, the watchdog included.

**Why:** the scheduler writes its screen record the moment it decides
(`decision/scheduler.py:862`, and `:732` for the watchdog). Whether the move
reaches the screen is decided later, in four places that never tell it "no":

- the director drops it during the cooldown (`decision/director.py:133-145`);
- the server drops it while holding for talk (`server.py:1781`, `:1476`);
- the follower path has the same shape: `follower/live.py:847` sets `shown`,
  `server_follower.py:471-476` then hands it to the director, which may drop
  it. A song heard by the opening watch re-anchors the engine first and asks
  the director second (`server_follower.py:713-724`);
- the app itself declines AI moves out of a scripture or text item and in
  continuous mode (`src/lib/presentation/usePresentationSession.ts:160-171`).

**Reproduced:** screen moved to a four-word slide with a timed override (the
spoken-request path), band sings it and then the whole next slide.

```
band sings slide 6 (4 words): engine decisions [(41.7, 0, 7)] | broadcast [] | state following_human
scheduler believes the screen shows (0, 7) - it shows (0, 6)
band sings all of slide 7: engine decisions [] | broadcast []
watchdog over the next 28 s: nothing (it compares against the same wrong record)
```

**Fix:** make the screen the source of truth. The app already reports every
slide change back (`server.py:1355`, `_state.last_slide`). Have
`Director.on_decision` return whether it broadcast; when it did not, call
`matcher.note_displayed(*_state.last_slide)` (and `followers.note_shown`).
For moves the app declines, reconcile against the echo: if the slide the
engine sent is not reported back within about a second, restore the record.

### 2. Editing the setlist mid-service makes the AI forget the screen — HIGH

**In the app:** add a song or fix a typo while the band is singing and the next
couple of words that resemble another song's first line move the screen to
that song.

**Why:** `engine.reindex` (`decision/engine.py:206-211`) resets the tracker to
"any song's opening" (`tracker/forward.py:725-730`, `:392-422`) and clears the
scheduler's screen record. With no record, the higher bar for leaving a song
is skipped (`scheduler.py:1071-1076`) and so is the plan gate
(`plan_gate.py:163`). `server.py:2092` does not restore either. This is the
2026-09-29 stray-word song jump again, by a different door. The docstring
says it re-anchors; it does not.

**Reproduced:** song A on screen at slide 4, then two words of song B's title.

```
before edit: screen (0, 4) belief (0, 3)
after edit : screen None belief (0, 1)
two stray words 'reckless love' -> [(1, 1, 1.0)]
same words with no edit      -> []
```

**Fix:** after `matcher.reindex`, call `note_displayed` with the slide on
screen (mapped to the new item index), and start the tracker on that slide
rather than on every song's opening.

### 3. The engine socket trusts any local caller — HIGH (security)

**In the app:** while Church Slides is open, a web page in the operator's
browser could find the engine's port and send it commands: start the
microphone and read the live transcript, overwrite the saved Groq key, or
delete a folder.

**Why:**

- `server.py:353-363` opens the socket on 127.0.0.1 with no token and no
  origin check. Browsers let pages open WebSockets to localhost. The
  assistant link next door already uses a per-launch token
  (`src-tauri/src/assistant_link.rs:64`).
- `delete_recording` joins the caller's `recording_id` onto the recordings
  folder and removes the result (`recording/sink.py:353-361`). `..` or an
  absolute path names any folder the user owns, the library included.
  Export and load have the same join (`server_recordings.py:112-119`,
  `:231`). `inspect_music` already checks properly (`:160-162`).

Not exploited here; read only. It needs the operator to have a hostile page
open while the app runs.

**Fix:** validate `recording_id` everywhere the way `inspect_music` does
(small, do first). Then pass a random token from the shell to the engine and
the UI at launch, require it on connect, and pass `origins=` to
`websockets.serve`.

### 4. The cooldown is timed from the wrong moment — MEDIUM

**In the app:** the operator clicks a slide and the AI moves it again about a
second later. The 5 s of "following you" mostly does not exist.

**Why:** a click carries no time (`server.py:1368`), so the cooldown starts at
the AI's *last decision* (`director.py:164-171`) and ends 5 s after that
(`:202-219`). If the AI last moved more than 5 s ago, which is usual when
someone steps in, it has already expired. The pill also stays on "following"
until the next decision, because only a decision advances that clock.

**Reproduced:**

```
last AI decision at 7.4s, click at 40.4s, AI broadcasts after click: [(7.4, 2), (41.7, 7)]
```

**Fix:** stamp the override with the session's audio time (the health report
already carries it) and advance the director's clock from the 2 s health tick
as well as from decisions. Fix 1 first: a cooldown that works makes 1 happen
more often.

### 5. A slide change waits behind side features — MEDIUM

**In the app:** when a spoken request or an off-setlist import is handled on
the same transcript event as a slide change, the slide change is delayed by
the model call (about a second, longer on a slow network), and can land after
a newer one, putting an older slide back.

**Why:** the handler computes the decision, then awaits six observers in turn,
then delivers it (`server.py:1764-1783`). Two of them can take seconds:
spoken requests await the model inline (`server_instructions.py:159`, `:196`,
`:216`) and an import rebuilds the opening watch (`server_library.py:107`).
Each event has its own handler, so the next event's decision is not held
back. An AI import also puts a new song up and is then followed by the older
decision for the previous song. Read, not reproduced.

**Fix:** observe singing (the hold check needs it), deliver the decision, then
run the rest. Start the model call and the import as tasks.

### 6. The watchdog steps back during a break — MEDIUM

**In the app:** the AI advances to the next slide just before a line ends, the
band goes into an instrumental, and 16 s later the screen returns to the slide
just finished. When singing resumes the screen is a slide behind.

**Why:** after an early advance the tracker still sits on the finished slide,
which is one behind the screen. The watchdog reads that as a stall and
commits it (`scheduler.py:694-745`). It has no rule against undoing the
scheduler's own anticipation, and the plan gate allows it while a band is
playing. The sweep that chose 16 s could not see this: replays do not run
`tick`.

**Where it bites:** wherever the word tracker moves slides: Intel and Windows
without the follower, Groq, readings. On Apple silicon the follower engine
discards these (`follower_engine.py:71-76`).

**Reproduced:**

```
decisions while singing slides 1-2: [(0.4, 0, 1), (7.4, 0, 2), (20.4, 0, 3)]
screen per scheduler: (0, 3)  belief top: (0, 2)
silence -> watchdog decision (seconds after last word, item, slide): (16.0, 0, 2)
```

**Fix:** the watchdog should not move back to the slide the last commit came
from unless new words have arrived since that commit.

### 7. One bad number stops the follower — MEDIUM

**In the app:** the screen goes to the song's blank first slide mid-song and
the follower does nothing more until someone clicks.

**Why:** nothing checks scorer output for NaN. `reliability` passes it through
(`follower/reference.py:784-788`), it becomes the evidence weight
(`follower/live.py:494`), and the belief is multiplied by it
(`follower/online.py:829-830`). The read-out then picks slide 0
(`online.py:1005-1023`).

**Reproduced with an injected NaN:**

```
follower decisions over the next 20 s: [(3.1, 0, nan)]
belief finite after 20 s: False
```

Whether the MLX scorer can produce one in practice is not shown. The same
half-precision Whisper model has returned NaN log-probabilities on near-silent
audio in this project (the 2026-09-29 stray "!" trace), so it is plausible.

**Fix:** drop non-finite scores in `_push_scored`; in `_update`, keep the old
belief when the new one does not sum to a finite positive number.

### 8. "Off" is not off — MEDIUM

**In the app:** with the AI switched off, the engine still uses the GPU and a
good share of a core for the whole service.

**Why:** off pauses transcription only (`server_state.py:288-314`). The frame
observer keeps running the chord analyser and the follower's read-outs
(`server_follower.py:328-366`), and feeds the next-line check, which runs
Whisper's encoder four times a second (`follower/live.py:528-530`,
`follower/next_line.py:533-562`). Its only pause is the overload level. Read,
not measured.

**Fix:** skip the observer's work when `transcription_needed()` is false and
set `listener.paused`.

### 9. The band leader PIN can be guessed — MEDIUM

**In the app:** anyone on the same Wi-Fi can take leader control of the slides
and read the library.

**Why:** four digits, kept for good (`src-tauri/src/band.rs:79-82`), and a
client may send `hello` with a new guess as often as it likes on one
connection (`band.rs:363`). 10,000 tries takes seconds.

**Fix:** close the connection after a few wrong PINs and slow repeat attempts
per address.

### 10. One failed pass ends the session on the processor path — MEDIUM

**In the app:** on Intel and Windows, a single error inside speech recognition
stops live AI for the service, with an error where there should be a gap.

**Why:** the MLX and Groq backends catch a failed pass and return no words
(`asr/mlx_backend.py:245-250`). The faster-whisper backend does not
(`asr/whisper_backend.py:160-186`, `:188-244`), nor does the wrapper
(`asr/streaming.py:381-386`), so it reaches `audio_session.py:383-384` and
fails the session. Read.

**Fix:** catch in `_collect_words`, once for every backend. End the session
only after several failures in a row.

### Low

11. **Word times shift after dropped audio.** The rolling buffer is joined as
    if continuous (`asr/streaming.py:155-160`, `mlx_backend.py:222-223`), but
    drops leave gaps (`audio/live_capture.py:275-278`,
    `pipeline/audio_session.py:400-406`). Words after a gap are stamped early
    by its length until the gap leaves the window.
12. **A withheld song change starts its count again.** `_cross_song_ready`
    clears its streak when it passes (`scheduler.py:1117-1119`); if the plan
    gate then says no (`:853`), three more observations are needed. Read.
13. **Re-selecting the slide already showing counts as a repeat**, which turns
    off early advance from that slide for the rest of the song
    (`tracker/progress.py:87-89` via `scheduler.py:1159`). The follower engine
    does this on every decision it drops (`follower_engine.py:75`). Seen in a
    script run.
14. **Errors in the transcript handler before its guarded block are lost.**
    Nothing reads the future (`pipeline/audio_session.py:525-529`).
15. **Changing tunables live drops the reading margin**
    (`server.py:2044-2047`). No effect at today's default of 0.
16. **Stopping runs one more full speech pass** whose result is thrown away
    (`asr/streaming.py:216-217`).
17. **App shell hardening.** No content security policy, the Tauri API is on
    `window`, and app commands are not permission-gated
    (`src-tauri/tauri.conf.json`, `capabilities/default.json`).
    `AUTO_PRESENTER_SIDECAR` can point a release build at any program
    (`sidecar.rs:262-266`). The updater key has no password.
18. **Next-line model licence.** Its notice says it was trained on Free Music
    Archive tracks, some under non-commercial licences, and leans on the app
    being free (`scripts/notices/supplements/next-line-model.txt`). Worth a
    decision before any paid tier. Not legal advice.

## Order to fix

1. Recording id check (3, first half): a few lines.
2. Screen record (1), then setlist edit (2), then cooldown (4). They share
   one idea: the engine learns the screen from the screen.
3. Follower NaN guard (7) and the processor-path catch (10): small and local.
4. Socket token (3, second half), watchdog (6), decision ordering (5).
5. Off means off (8), PIN attempts (9).

The three reproduction scripts make ready regression tests for 1, 2, 4, 6
and 7.

## Looked sound

Adaptive pacing of speech passes; the MLX buffer cap and release on stop;
the session start lock and the generation check for late errors; the parent
watchdog; dropped-audio accounting in capture; the hash check on the scorer
model download; the dev-only module boundary; the engine crash-loop backoff.
