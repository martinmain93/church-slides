# AI-assisted mode

Status: **built and working live, not yet tuned** (2026-10-16). Steps 1–6 of
the build order are done; tapes for tuning are recording overnight
(2026-10-16 23:30). The thresholds are still the starting points below.

Verified live (dev inject of `rec_20261006-225539_315`, Build My Life, screen
parked on Awesome God): a place suggestion for verse 1 ~23 s into the audio,
following the band forward through the song; the notice appears only with the
tile scrolled out of view; "Something else" lists three distinct places; Go
moves the screen and clears it; a next-tier pulse fired when the screen sat
on verse 1.

**Tuned 2026-10-17 against 19 tapes (2.07 h): the user chose Eager, the
defaults.** Correct-operator false alerts 31/h (place) and 55/h (next);
wrong-section caught 62/76 at a median of 3.5 s, stuck 52/73 at 5 s, missed
song 21/23 at 22 s. "Balanced" (`any_operator_move_waits`,
`screen_in_candidates_holds`, `next_min_confidence=0.7`, `place_dwell_s=5`)
measured 11/h and 6/h at the cost of slower and fewer catches. It is kept as
off-by-default knobs in `AssistConfig`, not the default. Most false place
alerts are band repeats that the forward-biased engine doesn't follow; missed-song
latency is the engine's own cross-song caution.

Not built yet: off-setlist "Add & go" (off-setlist recovery no longer imports
by itself outside Auto — see `server_library.observe_off_setlist`).

## Goal

A third AI mode between operator-led and AI-led. The operator drives the
slides. The AI listens, and speaks up only when the room has gone somewhere the
screen hasn't followed:

* the band went to a different song, in the setlist or not;
* the band went to a different section than the one on screen;
* the operator is late moving to the next slide.

It must never get in the operator's way. It must not be jittery while the
engine is unsure where the band is.

## Definitions

| Term | Meaning here |
|---|---|
| Engine | Everything that decides where the band is: the word tracker, the score follower, off-setlist recovery and whatever replaces them. |
| Engine move | One decision the engine would have put on screen in Auto mode. Every engine path ends in `Director.on_decision(decision)`. |
| Place | A slide, *by its words*. Two slides of a song with identical lyrics (chorus 1 and chorus 3) are the same place, because the room can't tell them apart. |
| Displayed place | What the audience screen shows now, as reported by the UI's slide-change notifications. |
| Suggestion | The layer's output: "the band is at place P, the screen isn't". It has a tier: **next** (the next slide is due) or **place** (somewhere else). |
| Tape | A recording of one golden replayed through the real engine in real time, with nobody at the controls: every engine move and every top-3 belief, stamped on the audio clock. |
| Yardstick | The offline eval: tapes plus simulated operators, scored for false alarms and catches. |

## The rule: a thin layer, the engine untouched

The engine changes often (forward filter, score follower, learned references,
MLX, CPU tier...). Assist must not need rewriting each time. So:

* **Assist reads only what every engine already produces:**
  * **Required:** the engine-move stream. It is the engine's core contract, and
    every engine in the repo, including the follower and off-setlist recovery,
    goes through `Director.on_decision`.
  * **Optional:** ranked candidates (`BeliefStateMessage.top_3`), the lost flag,
    and off-setlist library candidates. When an engine doesn't provide them,
    the matching feature ("Something else", the lost-suppression) is simply
    off.
* **No scoring or matching code changes.** The changes are in the wiring
  around the engine: the Director, the server and the protocol.

### The one wiring change that matters: don't feed operator clicks back

Today every operator click calls `Director.on_override`, which calls
`matcher.commit_external` and **re-anchors the engine on the operator's slide**.
In Assist that would make the AI agree with the operator's mistakes. If the
operator clicks verse 3 when the band is on verse 2, the engine snaps to
verse 3 and never objects.

So in Assist the engine runs **free**: operator clicks are not forwarded as
overrides. That is exactly how it runs in Auto mode with nobody touching the
controls, because Auto's own commits come back as `source="ai"` and are
ignored. Two benefits follow:

1. Assist inherits Auto's behaviour and accuracy, measured by the existing
   harnesses, with no new engine state to reason about.
2. A tape of Auto mode on a golden **is** the input Assist sees. The yardstick
   needs no separate engine runs.

Two exceptions re-anchor the engine on purpose, both through the existing
`on_override` path:

* **Dismiss (×)**: the operator says "I'm right", so the engine re-anchors to
  the displayed slide.
* **Spoken instructions** keep their current authority (applied at once, as an
  operator click would be).

Accepted cost: a free-running engine never learns from a correct operator. If
it gets lost it stays lost until its own evidence recovers it. The lost
suppression below, and Dismiss, bound the damage. The yardstick will show
whether more is needed.

## The interpreter (`decision/assist.py`)

A pure, deterministic class: events in, suggestion and clear events out. It has
no I/O and no engine imports beyond the index (for slide words), so tests and
the yardstick drive it directly.

Inputs: `engine_move(t, item, slide, confidence)`,
`candidates(t, [(item, slide, score)], lost)`,
`displayed(t, item, slide, source)`, `dismiss(id)`, `tick(t)`.

### Rules

1. **Compare places, not slide numbers.** Same-lyric slides within a song are
   one place. This removes the biggest source of false alarms: repeated
   choruses, and the 46% of songs whose verses open alike.
2. **An operator move *ahead* of the engine never creates a suggestion.** An
   operator who goes early (later in the song, or on to another song) gets no
   nag until the engine's next move. Moving *back*, as when the screen was put
   on verse 1 while the band sang slide 4, counts at once, after the 3 s
   grace that any click gets. (First version blocked every operator move;
   live, that held a real mistake back until the engine happened to move.)
3. **Never suggest one step back.** The engine sitting on the slide before the
   displayed one means the operator is ahead. That's fine.
4. **Next tier.** The engine moved to the displayed place's successor. Once
   the operator is still not there after `next_late_s` (start: 1.0 s) past the
   engine move, **pulse that slide**. The engine move is already
   anticipatory, so "1 s after it" is the user's "1 s late". **Highlight
   only, never a notification.**
5. **Place tier.** The engine moved anywhere else. Once it has disagreed with
   the screen for `place_dwell_s` (start: 3 s), raise a suggestion: highlight
   it, and show the notification if the slide isn't in view. **The wait is
   for the disagreement, not each slide**: an engine stepping forward through
   a song the screen isn't following keeps the time already served. (First
   version restarted it on every move, so with 3–5 s slides it could never
   run out. Live, 2026-10-16, it said nothing for 23 s.)
   "Next" means the next *place*: identical slides in a row are stepped over.
   Waits are timed on one wall clock from when things arrive, checked four
   times a second (`server_assist`).
6. **Clear** when the displayed place equals the suggestion, or when the
   engine moves to the displayed place. If the engine moves somewhere new, the
   old suggestion clears and the new place starts its own dwell. The one
   exception: when the engine moves to the *next* slide after a standing
   place suggestion, the suggestion follows it (same id). The band is carrying
   on through the place it points at, and making it wait out a fresh dwell
   would leave it a slide behind.
7. **Dismiss** mutes that place for `mute_s` (start: 30 s) and re-anchors the
   engine on the displayed slide.
8. **Rate limit.** At least `place_gap_s` (start: 10 s) between two different
   place-tier suggestions.
9. **Lost suppression.** No new place-tier suggestion while the engine
   reports itself lost (when it reports that at all).
10. **Confidence floor** on the move's confidence, `min_confidence`. This is a
    tunable, not a guess; the yardstick sets it.

All the numbers above are starting points for the yardstick, not decisions.

### "Something else"

When the place-tier notification is open, a third button lists up to three
**alternative places** with a Go button each:

* drawn from the latest ranked candidates, deduplicated by place, excluding
  the displayed place and the suggestion itself;
* off-setlist library songs, when off-setlist recovery offers one, are marked
  "not in setlist" and offer **Add & go**;
* each row shows the song, the section and the first line of words, with no
  percentages;
* **the list is frozen once opened**, and the whole notification freezes while
  the pointer is over it. Nothing reorders under a reaching hand;
* hidden when the engine offers no alternatives.

`top_3` may collapse to fewer than three distinct places once identical slides
pool. If so, widen the broadcast to top-6. That's a protocol change, not an
engine one.

## The UI

* **AI pill: Off · Assist · Auto.** The default in the operator view is an
  open question below. Single-screen and leader mode stay Auto.
* **Gentle pulsing yellow highlight** on the suggested slide's tile in the
  grid. One tile at most, fading in and out. The next tier pulses lighter and
  faster than the place tier. **Clicking the tile is the "Go".** It honours
  macOS Reduce motion.
* **The bottom notification appears only when the suggested tile isn't
  visible** (scrolled away, another song off screen, or not in the setlist).
  It reads: *"Sounds like they're singing **Holy Is The Lord**, Verse 2:
  'Holy, holy is the Lord…'"* with **[Go] [×] [Something else ▾]**.
  * One at a time; a new suggestion replaces the old.
  * Never takes keyboard focus. Arrows and B keep working.
  * Goes away on its own when the suggestion clears.
  * While "Something else" is open, pointing at a candidate lights its tile.
* Visibility is decided in the frontend (IntersectionObserver), not the
  sidecar.

## The yardstick (`research/eval/eval_assist.py`)

Built first, before any wiring. All tuning happens against it.

1. **Record tapes** (`research/eval/record_engine_tapes.py`): for each golden, play
   the audio into a real sidecar in real time with the production config, the
   way `run_follower_eval.py` does. Log every `slide_change` (source `ai`) and
   every `belief_state` on the audio clock, and save JSON. This is expensive
   (real time), so it's done once per engine version. **After an engine
   upgrade, re-record the tapes. The layer is untouched.**
2. **Replay offline** through the interpreter with simulated operators. This
   is deterministic and takes seconds:
   * **Good**: follows the golden, with a 0.5–1.5 s reaction after each cue.
   * **Missed song change**: stays on the old song for 30 s at a song change.
   * **Wrong section**: puts up a different section of the same song at one
     cue.
   * **Stuck**: stops advancing for 20 s.
3. **Score**, with identical slides pooled:
   * **Place tier, Good operator:** false alarms per hour. **Target < 1.**
   * **Place tier, planted mistakes:** share caught within 15 s, median time to
     the suggestion, and whether the suggestion is the right place.
   * **"Something else":** when the top suggestion is wrong, is the right place
     in the alternatives?
   * **Next tier:** for the Good operator, pulses while the operator is on time
     (false pulses per hour); for a slow operator, how late the operator is
     when the pulse appears.

Per `feedback_fast_eval_while_building`: iterate on the tapes; the real-time
gate is the tapes themselves (they come from the real-time pipeline). A live
service test comes last.

## Build order

1. This doc.
2. `decision/assist.py` and unit tests.
3. Tape recorder, plus tapes for the approved goldens.
4. Yardstick, a baseline and tuning.
5. Wiring: an `assist` mode in the Director (engine moves go to the
   interpreter instead of the screen; operator clicks are not forwarded),
   protocol messages `assist_suggestion` / `assist_clear`, and Dismiss
   re-anchors.
6. UI: pill, pulse, notification, "Something else".
7. A live service test.

## Open questions

1. **Default in the operator view**: should "AI on" mean Assist? (Recommended:
   yes. The operator view assumes someone is at the controls.)
2. **Wording of "Something else"**: alternatives are "Other matches" or "Not
   that — show options".
3. **Should a long, unraised disagreement ever re-raise without a fresh
   engine move?** For example, the operator clicked the wrong verse while the
   engine sat still. Rule 2 says no; the yardstick will show what it costs.
