# Finding a song that isn't in the setlist

November 2026. Spontaneous off-setlist import (`OffSetlistWatcher`,
`sidecar/auto_presenter/decision/library_recovery.py`) never fired on real
recordings. This is why, and what replaced it.

## What was wrong

The watcher waited for the tracker to look lost, meaning `display_marginal`
under 0.18 for 8 s, then searched the library and imported a clear winner.

**The gates never saw imports.** `run_follower_eval.py` and
`run_speech_negatives.py` both started the engine with a library that had no
songs, so the watcher was never created. The "0 false changes" numbers never
covered imports. Both scripts now take `--song-library <library.db>`.

**The marginal gate blocked it.** The probe below played seven church
recordings with the sung song taken out of the setlist and the real 367-song
library behind the engine. Once the song started, the marginal gate refused
93–100% of transcript events. The talking gate refused 0–61% and the chord
gate 0%. Nothing was imported. `display_marginal` is conditioned on the top
song, so inside the wrong song it stays above about one over that song's
slide count. It never sits under 0.18 for long.

**The ranking behind it was also wrong.** `_rescore` gives any slide that
shares a few words about 0.74, so the right song (0.77–0.9) rarely clears the
0.12 gap. With only the marginal gate removed, the old watcher imported the
*wrong* song in 2 of 7 probes and made a wrong import in 7 of the 9 golden
services (setlist intact).

**The library holds duplicates.** 53 song pairs share more than half their
word pairs ("Song", "Song (2)", "Song - Artist"). Any rule of the form "clearly
ahead of the runner-up" has to treat them as one song.

## What it does now

Every 2 s of sound, the last 12 s of words are turned into word-pair
fingerprints and matched against every library song **and the setlist's own
slides**. Matches are pooled per song, and songs that share half the smaller
one's word pairs count as one. A library song is imported when:

- it isn't (a copy of) a setlist song,
- it holds at least 4 of the query's pairs, spread over at least 4 different
  words (a Whisper loop such as "hi it's hi it's" repeats one pair),
- those pairs are at least 20% of the query's (chance matches on talk hold
  6–9%, wrong-song matches on singing 14–15%, genuine ones mostly 22–100%),
- it has at least twice as many as any other song, setlist included,
- it wins two checks in a row at least 3 s apart,
- the talking score is under 0.6 (was 0.45: the score rises when the setlist
  match is lost, which is exactly when this runs; at 0.45 it held recovery
  back by up to 100 s), and a chord is recent when the chord analyser is on.

Putting the setlist in the contest answers the question the marginal gate
stood in for ("is this a setlist song?") directly from the words.

## Measured

MLX Whisper small.en, real time, Apple silicon on AC power, Low Power Mode
off. Tools are in `auto-presenter-research/work/offlist_trigger/`:
`probe.py`, `capture.sh`, and `replay.py`, which replays the production
watcher over dumped inputs (`AUTO_PRESENTER_DEBUG_OFFLIST=1` logs every input
it sees). Nothing before the first import depends on the watcher, so a replay
up to that point is exact.

**Probe.** Each recording is played with its sung song taken out of the
setlist. Decoy songs stand in for it, and the real library is behind the
engine. Times are from the song's first lyric cue to the import.

| Recording | Old watcher | New, live | New, replayed on the earlier capture |
| --- | --- | --- | --- |
| 213825_312 | never | +21 s | +16 s |
| 215943_266 | never | never (Whisper heard 99 words) | +65 s (174 words) |
| 225539_315 | never | +59 s | +43 s |
| 213617_974 | never | +175 s | +87 s |
| 214054_974 | never | never | never: Whisper heard 62 words in 8 min |
| 214759_057 | never | +24 s | +20 s |
| 115044_425, 2nd song | never | +534 s | +516 s |

Every import was the right song: 5 of 7 live, 6 of 7 replayed. The live and
replayed columns differ because Whisper hears the same recording differently
from run to run. In 115044_425 the dropped song is sung briefly at 38 s and
weakly heard. It is sung properly from 547 s, so the import came about 25 s
live (7 s replayed) after that.

**Negatives.** Any import here is wrong.

| Set | Old watcher | New |
| --- | --- | --- |
| 9 church goldens, real setlists, real library (59 min) | 0 (never ran) | 0 live; 0 replayed, also 0 with no talking gate and a single check |
| 17 speech clips around past false triggers (16 min) | 0 | 0 replayed, also 0 with no talking gate and a single check |
| Full public speech set, 8 sermons and meetings (265 min) | 0 (never ran) | 0 replayed (see below); also 0 with no talking gate, or with a single check |

The full speech set was played live through the unchanged engine, with the
watcher's inputs dumped, and the new watcher was replayed over them. That is
exact, because the old watcher never imports and nothing before an import
depends on the watcher. That run also made 3 ordinary slide changes on one
meeting recording (IB4001, in the setlist's first song, from talk). They come
from the setlist follower, not from imports, and need their own look.

The talking gate does real work. With it and the confirmation both removed,
a recorded sermon reading Romans 3 aloud wins a check for the library's
"Welcome Loop", a slideshow item that shows that passage. The talking score
there is 0.93–0.99.

## Still open

- Recovery takes 20–60 s, or minutes when Whisper hears little. The two-win
  confirmation costs about 5–70 s of that. Removing it made no wrong imports
  on these sets, but the sets are small.
- One recording (214054_974) gets almost no words from Whisper when its song
  isn't in the setlist. No trigger built on words can work there.
- Checked on engine broadcasts (`import_library_song`), not on the operator's
  screen. The interface path that applies an import didn't change.
