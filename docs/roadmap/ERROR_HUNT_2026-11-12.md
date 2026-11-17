# Error hunt: moves a person would never make — 2026-11-12

**Question:** leaving aside reasonable guesses that turned out wrong, where does
the shipped engine do something an operator never would, and what rule,
limitation or mishearing causes it?

**Answer:** it is not under 1%. About one AI move in seven is of a kind a
person wouldn't make. Most last 2 to 5 seconds before the engine corrects
itself, which is why the time-based scores hide them. They fall into four
families with clear causes, plus one freeze from mishearing. Two families have
cheap rule-level fixes; two need a faster "was this line heard" signal than the
transcript.

## What was examined

The two real-time runs of the shipped engine from the 0.2.0 gate (`rt_echo_a`,
`rt_echo_b`): nine approved church recordings, 50 minutes per run, 751 AI
moves in all. For each move I had the engine's own trace (which part moved the
screen and how sure it was), its log, the words it heard live, and an offline
transcript of what was sung. Nothing was re-run. The faults repeat almost
move for move between the two runs, so they are behaviour, not noise.

A move counted as **fine** when the slide was the operator's within 4 s before
to 6 s after, or its words were sung within that window. The rest were sorted:

| Kind | Moves (two runs) | Verdict |
|---|---:|---|
| Fine | 548 | |
| Advanced at the end of a slide, band repeated or went elsewhere | 48 | Reasonable guess |
| Stepped back one | 4 | Usually a correction |
| **Moved with nobody singing the lyrics** | 32–46 | Fault A |
| **Left a slide before its last words** | 40 | Fault C |
| **Jumped to a slide nobody sang, mid-song** | 35 | Fault D |
| **There and back inside 6 s, different words** | 36 (overlaps the above) | Fault B |

The 32–46 range: 32 are certain (the operator had blanked, or the transcript
shows talk); 14 more rest on the offline transcript hearing nothing, and in one
recording it missed real singing.

## Terms

| Term | Meaning |
|---|---|
| Follower | The part that tracks the song by its music and words and picks the slide every half second. |
| Share | How much of the follower's belief sits on the slide it picked (0–1). |
| Next-line check | A fast listener that hears a line's opening sounds and flips the screen to it. |
| Talk latch | The rule that holds the screen while people are talking. |
| Lyric hold | The rule that makes moves harder once no line has been heard for 8 s. |

## Fault A: the screen moves while nobody is singing

**In the room.** After "Build My Life" the band stops and two people chat. The
screen steps on to the bridge's first line, then its second,
then jumps to the chorus on the words "this is the first
prototype". In another service the pastor prays "thank you Jesus for your blood
poured out for us" and the screen flips between two slides six times in 14
seconds. In a third, an instrumental starts and the screen jumps four slides
ahead.

**Causes.**

1. **The next-line check ignores the talk latch.** Its flip is applied after
   the latch has been consulted and has no talk test of its own
   (`follower/live.py:398-417`). 17 of the 46 came this way.
2. **The talk test only sees chatter.** It needs 8 different content words in
   8 s with almost none in a lyric phrase (`follower/talk.py`). Slow talk,
   pauses and instrumentals never reach 8. A prayer that uses the song's words
   ("thank you", "the blood") counts as lyrics.
3. **Music alone can still move the screen.** Under the lyric hold a move
   needs a 0.8 share, not words (`follower/online.py:62`, `:913-931`). Six
   moves cleared 0.8 with nobody singing; the rest came inside the 8 s before
   the hold starts, where 0.5 is enough.
4. **Mishearing.** On instrumentals the live transcript invents words ("I die
   gonna"), which feed the same belief.

The talk-only benchmark reads zero false moves because every clip starts on the
blank slide before a song. These all happen after singing has started.

**Proposals.**

- Put the next-line check behind the talk latch and the lyric hold. Small
  change.
- One unconfirmed move at most: after a move made without any of the new
  slide's words heard, make no further move until words of the shown slide or a
  new one arrive. An operator may step on once at a line's end; nobody walks
  three slides through silence.
- Add "talk after a song" clips to the talk benchmark, cut from these
  recordings (`rec_20261002-185523_091` 455–535 s, `rec_20260903-115044_425`
  500–540 s).

## Fault B: there and back

**In the room.** The band sings the third chorus. The screen shows
the first chorus, twenty slides back, for two seconds, then
returns. The prayer case above is the worst: six flips.

**Cause.** Two parts can each move the screen and neither remembers what the
other just did. The next-line check waits 2 s after any change and refuses only
a step back by one (`live.py:398-407`). The follower refuses to go back to the
slide it left only inside 4 s and only below a 0.8 share
(`online.py:878-885`). A similar-sounding opening elsewhere in the song passes
both.

**Proposal.** A there-and-back lockout shared by every mover: after leaving
slide A for B, a return to A inside 6 s needs words of A heard, and a third
change inside 6 s is refused. 36 visible flips in the two runs; 10 more were
between slides with identical words and can't be seen.

## Fault C: leaving a slide before its last words

**In the room.** The chorus slide reads "Hallelujah, hallelujah, I know it was
the blood... x2". After the first time through, the screen moves to the next
verse, comes back a second and a half later, then does it again. Elsewhere the
band holds a slow last line for 8 seconds and the screen has already left; it
sits on the wrong slide for 13 seconds.

**Causes.**

1. **Repeat markers in the lyrics.** The engine reads "x2" as a word and
   expects the slide once. 28 of the 367 songs in the library carry a marker
   like this.
2. **No rule ties leaving a slide to its last line.** The follower moves when
   its belief, projected 2 s ahead, favours the next slide. Its tempo estimate
   often runs fast (1.6–1.8 in these cases), so on a slow last line its clock
   says the slide is over before the line has started.

**Proposals.**

- Understand repeat markers: strip them from the words, tell the follower the
  slide is sung that many times, and flag them at import. Self-contained.
- Don't leave before the last line has started. I tested this using the live
  transcript: it stops 70% of the early exits but also 33% of good advances,
  because words arrive about 3 s late. It needs the follower's own faster
  "line heard" signal, which the traces don't map to slides, so it is untested.

## Fault D: jumping to a slide nobody sang

**In the room.** In "Build My Life" the band sings the end of verse 1 and the screen
skips verse 2 and
shows the chorus just as the band starts singing
"Jesus...". This happened in both recordings of the song, in both runs. In
another song the screen flashes the chorus for two and a half seconds in the
middle of a bridge.

**Causes.**

1. **Near-twin slides.** Slide 4 of that song is slide 2 plus one more "we
   live for you". The band sang the longer version at slide 2's place. The
   follower placed itself on slide 4 and stepped on from there. Identical
   slides are already handled; a slide that is the start of a longer one is
   not. 32 library songs have such a pair.
2. **Anticipating a jump.** At a slide's end the follower projects ahead, and
   its belief sometimes prefers a jump to the chorus over the next slide in
   order, before a word of either is heard. An operator anticipates in order
   and jumps only on hearing it.
3. **Sound-alike openings.** The next-line check can flip to any slide in the
   song. "You came", "you said" and "you sang" open three slides of one song
   and it hops between them.

**Proposals.**

- Anticipate in order, jump on evidence: the follower may move ahead of the
  words only to the next slide; any other destination needs a word of its
  opening heard. Tested on the recorded moves: it stops 24 of 25 bad follower
  jumps. The cost is that about half of the good jumps would wait for their
  first word. How long that wait is needs a real-time run.
- Treat a slide and its longer twin as one place, and keep the copy reached in
  order.
- Give the next-line check a short list (the next slide, the current one,
  section starts) or a margin over the in-order candidate for anything further.

## A freeze from mishearing

In one phone-recorded service the room sang a song that wasn't on the setlist and the
live transcript produced nonsense ("now it's full, I am, I am"). The engine
held one slide for 51 seconds while the operator alternated two. Holding is
right when nothing is understood, but a person would have followed. Faults A
and this one need the same missing thing: knowing that people are singing,
apart from recognising the words. No cheap fix; worth its own spike.

## What didn't work

- **A minimum share.** Bad moves are no less confident than good ones: moves at
  0.6–0.8 were wrong more often than moves at 0.5.
- **Simple word gates.** "No move unless lyric words arrived in the last 8 s"
  stops 37% of the not-singing moves and 12% of good ones. The engine's good
  moves also run ahead of the words.

## Limits of this hunt

- Nine recordings, which are also the tuning set; six are single songs. Other
  songs will have other twins and markers.
- No operator in the loop, Apple-silicon path only.
- The rule tests are first-order: stopping one move changes what follows.
- The "not singing" count leans on an offline transcript that misses some
  singing.

## Suggested order

1. Next-line check behind the talk latch, and the there-and-back lockout (A, B).
2. Repeat markers (C).
3. Anticipate in order, jump on evidence, measured in a real-time run (D).
4. Near-twin slides (D).
5. Run `scan.py` over the held-out public and lyric-video sets.

Tools: `~/Development/auto-presenter-research/work/error_hunt/` (`scan.py`
finds the cases, `look.py` shows one window on a timeline, `attribute.py` and
`classify.py` sort the moves, `whatif.py` tests the rules).

## Prototype follow-up (same day)

Before building the two rules first in the suggested order, I tested them on
the recorded moves. Both failed:

- **Next-line check behind the talk latch:** the latch was on for 1 of the 17
  bad flips. The latch itself is the weak part, not the bypass.
- **There-and-back lockout:** in 58 of the visible there-and-back cases the
  trip away was the mistake and the return was the correction; in 24 it was
  the other way round. Blocking returns would lengthen more errors than it
  prevents.

Word-based gates of every kind also failed: for a quarter of good moves, no
lyric phrase had arrived in the live transcript in the previous 10 s.

One signal did separate good from bad: the follower's own line scores. For
steps on to the next slide, the left slide's last words scored under -4.0 in
81% of early exits and 12% of good steps.

**Built (off by default, `AUTO_PRESENTER_LAST_LINE=1`):** a step on to the next
slide waits until the shown slide's last words have been heard, or the next
slide's opening has, for at most 6 s of wanting to leave
(`follower/online.py` `_wait_for_last_line`, fed from `live.py`
`_push_scored`; tests in `tests/follower/test_last_line.py`).

**Measured by offline replay** (`work/error_hunt/replay.py`: the recorded
evidence fed back through the follower; with the rule off it reproduces 95% of
the recorded read-outs). Not a real-time run: the laptop was on battery in Low
Power Mode.

| | Run A off → on | Run B off → on |
|---|---|---|
| Early exits | 21 → 8 | 20 → 7 |
| Sung line hidden | 0.213 → 0.174 | 0.216 → 0.185 |
| Wrong-slide | 0.280 → 0.275 | 0.277 → 0.278 |
| Cues reached within 5 s | 169 → 168 | 169 → 169 |

One recording got worse against the operator (`rec_20261002-185523_091`,
wrong-slide 0.354 → 0.441) while its hidden-line rate improved (0.419 →
0.244). It is the phone recording the engine can barely hear: the rule held a
step that the old behaviour made early on tempo alone and happened to get
right for 28 s. Skipping the rule when no line has been heard lately did not
fix it and gave back gains elsewhere, so it is not in the prototype. Open.

### Real-time gate run (same day, mains power, Low Power Mode off)

Same nine recordings and shipping setup as the 0.2.0 gate, rule on, one run
(`work/error_hunt/rt_lastline`). The two shipped-engine runs are the baseline
and show the run-to-run spread.

| | Shipped, run A | Shipped, run B | Rule on |
|---|---:|---:|---:|
| Early exits | 20 | 20 | 7 |
| Sung line hidden | 0.229 | 0.224 | 0.186 |
| Wrong-slide | 0.284 | 0.280 | 0.278 |
| Perceived | 0.190 | 0.189 | 0.180 |
| Cues reached within 5 s | 169 | 169 | 169 of 225 |
| AI moves | 357 | 364 | 317 |

The replay's prediction held. Early exits fell by two thirds and the sung line
is hidden about a sixth less often, well outside the spread between the two
baseline runs. Wrong-slide and cues are unchanged. No audio was dropped.

Per recording:

- `rec_20261002-185523_091`, the one the replay flagged: wrong-slide 0.359
  against 0.354, so the feared loss did not appear in real time, and its
  hidden-line rate halved (0.42–0.44 → 0.225).
- `rec_20260818-213825_312` got worse: wrong-slide 0.520 against 0.463 / 0.447,
  hidden 0.465 against 0.399 / 0.364. The replay showed no change for it, so
  this may be run-to-run variation on a hard recording, but it is one run and
  not explained.

Still needed before turning it on by default: a second run to see whether the
213825 loss repeats, and a look at that recording if it does.

## Shipped, and four more candidates (same day)

The last-line rule is on by default as of 950136b (it stands aside when the
line scorer isn't running, e.g. on the slower-computer path).

Four further candidates were tried by offline replay against the rule-on
baseline. All were experiments in the working tree, off by default; only the
near-twin rule was kept (see the end of this report). The replay re-decides only the follower's moves; next-line flips
are replayed as recorded, so it cannot test fixes to those.

| Candidate | Switch | Result on the two runs | Verdict |
|---|---|---|---|
| Near-twin slides are one place; keep the copy reached in order | `AUTO_PRESENTER_TWINS=1` | Removes the Build My Life jump in 3 of 4 replays (wrong-slide 0.171 → 0.142 on that recording); no change on recordings without twins | Worth building properly |
| A jump waits for the destination's opening to be heard (max 8 s) | `AUTO_PRESENTER_JUMP_EVIDENCE=1` | Bad jumps 20 → 14 and 20 → 13, but 3 cues lost per run and hidden-line 0.174 → 0.184, 0.185 → 0.189 | Not as built |
| After a move to a slide nothing was heard of, no further move until something is | `AUTO_PRESENTER_ONE_UNCONFIRMED=1` | No-singing moves 26 → 21 and 23 → 20, 2 cues lost per run, scores flat | Weak; not as built |

The two rejected rules share a cause: the line scorer's "heard" signal is
good at confirming a line's end (the last-line rule) but too patchy to demand
before every jump. Most remaining no-singing moves and jumps come through the
next-line check, which needs a real-time run to test.

The repeat-marker experiment was dropped and its code removed: markers should
be taken out of the slides ahead of time, not handled by the engine.

## What is left with the rule on (same day)

Sorting the moves of the rule-on real-time run the same way (332 AI moves,
50 min), against roughly what one shipped-engine run had:

| Kind | Shipped (per run) | Rule on |
|---|---:|---:|
| Left a slide before its last words | 20 | 7 |
| Jumped to a slide nobody sang | 17–18 | 14 |
| Moved with nobody singing | 22–24 | 21 |
| There and back inside 6 s (visible) | about 18 | 11 |

**Next-line check short list: refuted before building.** Of its recorded
moves to somewhere other than the next slide, 107 were fine and 20 were not.
Its error share (18%) is lower than the follower's (26%). Limiting where it
may go would remove far more good moves than bad.

**Why `rec_20260818-213825_312` read worse.** From 117 s to 177 s the band
repeats "You are a miracle-working God" eleven times and the operator stays on
that slide. The shipped engine stepped on to the next slide (never sung) and
came back nine times in that minute. The rule-on run stepped on fewer times
but once stayed there 23 s. Neither is what a person would do: after the first
bounce an operator stays put.

**Bounce rule (experiment, `AUTO_PRESENTER_BOUNCE=1`):** after a step on that
was undone, the same step needs the next slide's opening heard (or 10–15 s of
wanting it), for the next 30–60 s. Replay: helps that recording (wrong-slide
0.435 → 0.409, hidden 0.356 → 0.319) and removes a quarter of the
end-of-slide guesses (20 → 15), but costs a little on two other recordings;
the means do not move. Mixed; not as built.

## Real-time runs, evening (mains power, Low Power Mode off)

**Second run of the shipped engine (rule on), all nine recordings**
(`work/error_hunt/rt_lastline_b`). The first result repeats:

| | Before the rule, A / B | Rule on, run 1 / run 2 |
|---|---:|---:|
| Early exits | 20 / 20 | 7 / 8 |
| Sung line hidden | 0.229 / 0.224 | 0.186 / 0.181 |
| Wrong-slide | 0.284 / 0.280 | 0.278 / 0.272 |
| Perceived | 0.190 / 0.189 | 0.180 / 0.183 |
| Cues reached within 5 s | 169 / 169 | 169 / 170 of 225 |

`rec_20260818-213825_312` read 0.470 this time (0.520 in run 1; 0.463 and
0.447 before the rule), so the earlier loss was run-to-run variation on the
repeated-line minute, not the rule.

**Near-twin rule on the two Build My Life recordings**
(`work/error_hunt/rt_twins`, `AUTO_PRESENTER_TWINS=1`):

- `rec_20261006-225539_315`: the jump to the chorus at
  25 s is gone; the screen stays on verse 2.
  Wrong-slide 0.158 against 0.183 in both rule-on runs.
- `rec_20261002-185523_091` (phone recording): no gain. The screen still
  reaches "Holy" early, but by stepping through two slides in two seconds, not
  by a jump, so the twin rule doesn't apply. Wrong-slide 0.363 against 0.345
  and 0.359: inside the spread.

The twin rule does what it was built for and nothing else. It is now on by
default (`AUTO_PRESENTER_TWINS=0` turns it off; tests in
`tests/follower/test_twins.py`). The three rejected experiments (jump needs
evidence, one unconfirmed move, bounce) were removed from the code.

## Second diagnostic run (2026-11-13, both fixes on)

Real time, mains power, commit 729cb3f. Two sets: the nine church recordings
(`work/error_hunt/rt_diag_church`), and 20 public songs the engine was never
tuned on (JamendoLyrics, pop and rock, one line per slide, 72 min;
`rt_diag_jamendo`). New tools in `work/error_hunt/`: `evidence.py` (what the
line scorer heard), `belief.py` (the follower's belief per slide), `cause.py`,
`overrule.py`, `recover.py`, `attract.py`, `frozen.py`, `lib_tags.py`.

| | church (50 min) | public (72 min) |
|---|---|---|
| wrong-slide / perceived | 0.283 / 0.192 | 0.390 / 0.258 |
| AI moves, not justified | 341, 72 (21%) | 590, 113 (19%) |
| jumps to an unsung slide | 17 | 51 |
| moved with nobody singing | 21 | 10 |
| early exits | 9 | 5 |
| there-and-back inside 6 s | 21 | 28 |
| line scorer's best slide is the sung one | 84% | 72% |

Church against the last rule-on run: wrong 0.283 (0.272, 0.278 before), line
hidden 0.178 (0.180), cues 170 of 225 (170), early exits 9 (8). Flat, apart
from the phone recording (0.345 -> 0.462, family 5 below). The twin fix held
(`225539`: 0.163). Public run: one song (Fire Inside) had 5 dropped audio
blocks and 2 unhealthy ticks.

Most of the wrong time comes from wrong moves that stay up: moves wrong for
more than 5 s are 39 of 72 on the church set and carry 602 of its 686 wrong
seconds (public: 807 of 987). A person who steps wrongly is back in 2 s.

### 1. After the last slide the model goes to the first (a bug)

`spec.library_spec` leaves out "tag" slides (words inside two or more longer
slides). `reference.order_prior` then looks for the song's last slide to give
it the "repeat or go back to any earlier slide" row (`LAST_REPEAT`); when that
last slide is a tag it is not in the model, the row is never written, and the
last modelled slide falls through to the old rule: next is the first slide,
probability 1.0.

Vision (public): slide 17 is sung five times in a row. The line scorer reads
it at -0.8 against -3.7 for every other slide for 30 s, and the follower
still goes to slide 1 four times (153, 162, 172, 179 s), share 0.52-0.57.
The next-line check puts it back each time, after waiting out its 2 s settle
rule. `succ.py` prints the table: "slide 17 -> slide 1: 1.0".

Library: 13 of 361 songs end on a tag slide (How Great Thou Art, Goodness Of
God, The Lion And The Lamb, Glorious Day, Yes And Amen, O Come All Ye
Faithful, ...), counted on written order (`lib_tags.py`). Fix: take the last
slide that is in the model. Small.

### 2. Repetitive lines score well on anything

When the line scorer is wrong it is wrong towards one slide per song, and
that slide is made of repeated words: "no no no no no no no no" (best slide
on 43 passes while other lines were sung), "whoa oh oh oh oh" (29), "I've
waited" x5 (28), "lalalalala" (15); on the church set "nothing but the blood
of Jesus" (32), "hallelujah hallelujah" (18), Build My Life's chorus line
(18), "I surrender all, I surrender all" (17). Wrong Concept's "no no"
slide read -2.3 to -2.5 on five passes in a row whatever was sung.

Likely mechanism, not yet tested: the scorer is Whisper's decoder scoring a
given text, and after "no no" the decoder predicts "no" from the text alone,
so the score no longer depends on the audio.

The follower then moves to that slide or the one after it: 28 of the public
set's 113 bad moves are "scorer heard slide X, follower went to X+1" (Wrong
Concept went to its title line 8 times this way). Candidate: score a line
against its own no-audio baseline, or discount repeated words in a span.

### 3. The follower undoes a correct next-line flip

Follower jumps within 4 s of a next-line flip: 17 wrong against 3 that fixed
a wrong flip (public); 7 against 7 (church). On songs it knows the two halves
are equally right, so no blanket rule; on unfamiliar songs the follower is
the one to doubt. The flip moves only part of the belief, the rest stays on
the wrong slide and wins again a second later, and the settle rule then holds
the check off for 2 s (40 held flips in Vision alone).

### 4. Steps on, the band doesn't, and it stays

40 bad follower moves (16 church, 24 public) were made while the line scorer
still had the shown slide as its best. Nearly all are a step to the next
slide when the band repeated the slide or went elsewhere. `213617` at 136 s:
the band goes back to the chorus, the follower steps on to the bridge at
share 0.50 on timing alone, and the bridge stays up 11 s while its share
climbs to 0.96 without one scorer pass hearing it. Share is relative between
slides, so it rises when nothing is heard at all.

"No second move until the first is confirmed" does not separate these:
follower moves off a never-heard slide are 28 fine against 6 bad (church), 51
against 14 (public). Same answer as the rejected experiment.

### 5. Quiet singing counts as no singing

Phone recording, two-slide songs: the follower believed slide 2 at 0.6-0.88
for 79 s over four stretches (longest 30 s) and the screen stayed on slide 1,
because no line scored well enough to count as heard and the lyric hold was
on. Last run the step landed 1.5 s before the hold began, this run it didn't:
a coin toss worth 37 s, and the whole of this run's worse score there.

### Finding 1 fixed (2026-11-13, uncommitted)

`order_prior` now takes the song's order from slides that have a segment, so
the last modelled slide gets the `LAST_REPEAT` row (Vision's slide 17: 0.5
repeat, the rest spread over earlier slides). Test added to
`test_after_the_last_slide_the_band_repeats_it_or_goes_back_not_to_the_top`;
suite 1699 passed. Replay of the recorded evidence: Vision's four jumps to
slide 1 are gone (39 moves -> 31); the two church recordings with such a song
are unchanged (62 -> 62 and 23 -> 22 moves). Not yet run in real time.

### Finding 2 tested (2026-11-13): the cause is real, the fix is not there

Every recorded scorer pass on both sets (3,135) was scored again offline with
the per-token log-probabilities kept (`rep_rescore.py`, `rep_analyse.py`;
re-scores match the live ones, r 0.996-0.999), plus each song's spans on
silence and on noise.

| mean log-prob per token | first-time words | repeated words |
|---|---|---|
| the line is being sung | -4.82 | -2.37 |
| another line is being sung | -6.29 | -3.17 |
| noise | -6.66 | -3.35 |
| silence | -6.37 | -3.21 |

A word that has already appeared in the span scores about 3 higher whatever
the audio, silence included. So a span that is mostly repeats reads -4.7
when it is not being sung, against -6.3 for a span with none: confirmed.

Correcting for it does not make the scorer right more often (best slide is
the sung one; church / public):

- as shipped: 83.5% / 72.2%
- repeated words left out of the mean: 83.4% / 70.2%
- minus the span's score on silence: 79.0% / 67.3% (on noise: 79.2% / 66.7%)
- minus the span's usual score in this song: 83.2% / 67.4%
- minus half of either: 84-86% / 71-72%

Each correction helps the songs with a "no no no" slide (Wrong Concept 54 ->
65-70%, Whistler 58 -> 72%) and hurts as many others (Crowd Pleaser 92 ->
62%, Is It Right 89 -> 68%): taking the text's own likelihood out promotes
lines of rare words instead. Not worth building. The scorer's errors on
unfamiliar songs are mostly something else, not yet found.

## Where the remaining error comes from (2026-11-13 dig)

Tools: `rep_errors*.py`, `rep_gate.py`, `hidden_split.py`, `elsewhere.py`,
`rep_replay.py` (variants by `AP_VAR`), `start_gate.py`, `talk_scan.py`.
Numbers are replay on the two diagnostic runs; base: church wrong 0.277,
hidden 0.173, cues 170/225; public 0.386, 0.222, 330/414.

**The line scorer is not the problem when someone is singing.** With singing
at the start of its 6 s window it names the sung line 94% of the time
(public). Its wrong answers are windows with no singing in them (456 on the
public set; 77% still name some line at -4 or better). The trust weight
already tells those apart (best score alone: AUC 0.963 public, 0.869 church;
adding lead, entropy or Whisper's no-speech score gets 0.978). Nine variants
of the trust weight in replay all land within 0.01 of base. Not the lever.

**Where the hidden sung words are** (share of hidden words, public / church):

| while these words were sung, the screen showed | public | church |
|---|---|---|
| a slide elsewhere in the song | 35% | 29% |
| the previous slide, first second of the line | 25% | 15% |
| the blank, before the song's first slide came up | 16% | 13% |
| the next slide (left early) | 9% | 13% |
| the previous slide, 1-3 s late | 9% | n/a |

"Elsewhere" is many short stretches (about 3 words each), put there by a
follower jump (32%), a follower step (25%), a next-line jump (23%) or step
(12%). The scorer had the sung line on top for about half of them, but the
answer arrived after the words. Two rules built on that ("stay where the
words are", "go where the words are") are flat or slightly worse.

**The evidence is old when it arrives.** A line the scorer hears is 2.8 s
(church) / 3.3 s (public) old by then, p90 5.5 s: passes run every 2 s over
a 6 s window and take 0.4-0.5 s. Replay with the same results arriving
sooner:

| scorer results arrive | church hidden | public hidden |
|---|---|---|
| as now | 0.173 | 0.222 |
| 1 s sooner | 0.161 | 0.218 |
| 2 s sooner | 0.152 | 0.209 |
| 3 s sooner | 0.150 | 0.205 |

Wrong-slide barely moves. A pass every 1 s would be worth about the first
row (not tried: it doubles the scorer's load).

**A song's first words come up late: the one fix found.** The first lyric
slide appears a median 5.3 s after the first word on the public songs (all
20 over 3 s) and 4.5 s on the church set; the operator is there half a
second before. On Vision the scorer had slide 1 at -1.3 (others -3.7) 0.9 s
after the first word and the belief was 1.00 five seconds before the screen
moved. The blank waits for `talk.lyrics_heard`, which only the transcript
feeds, and the next-line check does not act off a blank.

Rule: one scorer pass with a line at -2.0 or better, 1.0 ahead of every
other line, while the band plays, counts as lyrics heard.

| | first words up after | wrong | hidden | cues |
|---|---|---|---|---|
| public, now | 5.3 s | 0.386 | 0.222 | 330/414 |
| public, rule on | 2.6 s | 0.377 | 0.211 | 338/414 |
| church, now | 4.5 s | 0.277 | 0.173 | 170/225 |
| church, rule on | 3.5 s | 0.276 | 0.172 | 170/225 |

Safety: 0 false releases in the church set's 4.5 minutes of gold blank
(-2.5 gives 13). On 265 min of talk-only audio, seven songs' lines scored
every 6 s: 51 windows pass for some song, 27 for the worst single song
(about 6 an hour), 18 at -1.5; two windows in a row almost never (2). That
count is before the band-playing test, the talk latch and the belief, all of
which still apply, and the transcript rule it stands beside ("any two words
of the song") is itself loose. Not proven safe.

Built: `live.START_ON_CLEAR_LINE` (`clear_line`, `TalkGate.note_lyrics`,
`tests/follower/test_start_on_line.py`), **off by default**,
`AUTO_PRESENTER_START_ON_LINE=1`. Before turning it on: one real-time run on
both sets and the full talk benchmark (4.4 h).

### Start rule in real time (2026-11-13/14, `rt_start_church`, `rt_start_jamendo`, `speechneg/start_on_line`)

One run each, mains, rule on, at acefd12 plus the working tree; compared with
the diagnostic runs (729cb3f, before the wrap fix).

| | first words up after (median) | wrong | hidden | cues |
|---|---|---|---|---|
| public, diagnostic | 5.3 s (20 of 20 over 3 s) | 0.390 | 0.226 | 330/414 |
| public, rule on | 3.9 s (14 of 20) | 0.377 | 0.205 | 333/414 |
| church, diagnostic | 3.3 s | 0.283 | 0.178 | 170/225 |
| church, rule on | 4.0 s | 0.286 | 0.201 | 171/225 |

- Talk benchmark: **0 slide changes in 265 min**, as before the rule.
- Public: the rule fired on 15 of 20 songs. Without Vision (the song the wrap
  fix changed): wrong 0.384 -> 0.372, hidden 0.227 -> 0.208.
- Church: the rule fired once in nine recordings, so the church numbers are
  run-to-run variation, not the rule. The hidden change is the phone
  recording (185523, 0.247 -> 0.406; the rule never fired on it, and it is
  the known coin toss on the lyric hold); the other eight read 0.169 ->
  0.175.
- The gain is smaller than replay's (2.6 s, church 3.5 s). Replay had no
  band-playing test; in real time that test is the likely reason the rule
  rarely fires on the church set and fires later on the public one (the band
  signal needs 6 s of music). Not checked.

Still off by default.

### Why the start rule rarely fires on the church set (2026-11-14, `start_why*.py`)

Per song start, from the real-time traces: when the first clear scorer pass
arrived, when the transcript's first two lyric words arrived, when the rule
fired. The band-playing test is not the main reason (it held the rule back on
one church recording, 213825, and delayed it 2 s on one public song).

- **On the church recordings the transcript is as quick as the scorer.**
  Transcript words arrive 3.5-4.9 s after the first word (public: 4-6.5 s);
  the scorer's first clear pass arrives at 2.4-10 s (public: 1-3 s). The rule
  only fires when it beats the transcript.
- Three of nine recordings start with singing in the first 1.5 s, and the
  scorer cannot make a pass until it has 6 s of audio. This is the
  recordings, not a service (the engine is running before a song starts).
- On three, the opening line is the same as or close to another line, so the
  "1.0 ahead of every other line" test fails (213617, 214054, 213104).

Dropping the lead test (any line at -1.5 or -2.0) fires on those too, but
replay is unchanged (church hidden 0.171 -> 0.170, cues 170 -> 171; public
the same), and it gives 2 false releases in the church blank minutes at
-2.0. Not worth changing: the rule stays as benchmarked. The church set has
little to gain at song starts by this route; the gain is on songs the
transcript is slow on.

**2026-11-16: turned on by default** (user's call). `AUTO_PRESENTER_START_ON_LINE=0` turns it off. Changelog line added.
