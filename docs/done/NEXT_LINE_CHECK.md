# Next-line check and wider benchmarks

Status as of 2026-11-09. The check is built into the app (step 8), on by default on
Apple silicon with small.en; not committed yet.

## The problem
The app follows Whisper's written words, which arrive about 3 s after they're sung.
That's fine while the band follows the planned order, but when they repeat a section or
go off-order the screen changes late. The **next-line check** is a fast decision on
"has the next line started, and which one?": it listens for the sounds of the first
few words of each likely next line instead of waiting for Whisper to write them.

## What we know
- **Size of the prize (oracle ladder, 2026-11-02):** a perfect decision would fix 115 of
  121 slow changes; perfect phone recognition through the current decision logic, 73.
  So both the hearing and the decision logic matter.
- **Phone head (2026-11-02..04):** a small model (about 1M parameters) reads speech sounds
  from Whisper small.en's encoder, skipping the decoder. Label fixes, 4x more data (FMA),
  encoder fine-tuning, bigger heads and heads that commit to phones more readily were all
  flat. The one real gain was holding the head's weight size fixed: right calls
  at +1.0 s went 35% -> 44%. Training data is FMA songs only.
- **Line-start training (2026-11-05):** we trained the check's decision on FMA line starts.
  On our approved recordings, with mid-line false fires held at 4%:

  | | +0.3 s | +0.5 s | +0.7 s | +1.0 s | +1.5 s |
  |---|---|---|---|---|---|
  | Today's check | 26% | 40% | 42% | 44% | 29% |
  | Settings only tuned | 27% | 44% | 50% | 52% | 44% |
  | Settings + head tuned | 32% | 48% | 49% | 56% | 49% |

  Most of the gain is two settings: about double the per-frame cost of "nothing matches
  yet" (0.3 -> 0.6), and add about 0.25 x the candidate's own match score to the
  "candidate vs current line" comparison. A learned-from-scratch detector never learned.
- **The catch (2026-10-28 replay trace):** in live replays a right answer often didn't
  move the screen. The check was asked at the wrong moment (61 of 145 changes) or from
  the wrong current line (38 of 145). The gains above are standalone; they haven't been
  tested end to end yet.

## Next steps
1. [x] Move working files out of /tmp (macOS deletes them) into the sibling folder
   `../auto-presenter-research/work`; commit the public-data fetcher
   (`research/data/fetch_public_worship.py`).
2. [x] **Speech-only benchmark.** Run today's engine over `speech/` (4.5 h of meeting
   chatter and read sermons) with a typical setlist loaded; count slide changes.
   Target: zero. This is the safety baseline before anything makes the engine more eager.
   `research/eval/run_speech_negatives.py`, setlist from the 2026-10-29 service, AI
   active, spoken requests OFF (no hosted key in the test engine: an untested gap).
   **Baseline (2026-11-05): 21 false slide changes in 265 min = 4.7 per hour.**
   Sermons 4 in 75 min; meetings 17 in 190 min. Blanking fired only on silence (harmless).
   Two causes, about half each:
   - *Phrase matches while the engine knew it was talk* (10; singing gate said
     "not these words"): ordinary phrases that happen to be lyrics move the slide anyway,
     e.g. "for all that you've..." -> Jesus We Love You, "preach the gospel to every
     creature" -> Indescribable, "highest level" -> Indescribable. 5 of the 10 were
     "for all that" into the current song. So the not-singing verdict doesn't stop the
     words follower, and cross-song jumps happen on 2-3 shared words.
   - *Talk mistaken for singing* (11; gate said "singing" at low scores 0.2-0.67), then
     jumps to repetitive slides ("day and night, night and day", "we want to see, we want
     to see") that loosely fit any babble.
   Logs: `../auto-presenter-research/work/speechneg/baseline/*.jsonl`.
   **Fix, part 1 (2026-11-05, uncommitted): `decision/plan_gate.py`.** While the singing
   detector reads talk, a move needs the target slide's own words (3 distinct content words
   in the last 8 s, 40% of what was heard). Planned moves (next slide, next song's start)
   need them only once talk is clear (score 0.6, vs 0.5 for other moves); an unplanned
   jump into another song always needs them. Applies to the transcript engine and the
   in-song follower; `off_plan_min_words=0` turns it off.
   - First try (any off-plan move needs words) wrecked singing: 101 of 298 real moves are
     off-plan and sung words are too garbled/late; offline wrong-slide 0.303 -> 0.481.
   - Real-time goldens: 0.303 -> 0.328 overall, but all of it from one recording
     (185523: 0.336 -> 0.554), caused by also gating the **opening-line listener**: real
     song starts read as talk and their words arrive late, so 6 real changes were refused.
     That part is reverted. Excluding it, the other 8 recordings: 0.299 -> 0.300 (neutral).
   - Speech clips (17 clips around the failures, fresh engine each): rule off reproduces
     only 4 of the 21; rule on (with the listener gated) 1. The opening-line listener
     produced 2 of the 4 ("every creature unique in" gain 3.3; "his banner over you" 3.4),
     and its gain doesn't separate talk from real song starts (golden 3.4-5.4).
   - Open: the opening-line listener needs its own discriminator; the goldens with the
     final version (listener ungated) and the full 4.4 h speech run haven't been redone.

   **Fix, part 2 (2026-11-06, uncommitted): a band signal, and the listener's own rules.**
   - `music/band.py`: a band is playing when the chord model's "no chord" probability,
     averaged over 6 s, leaves harmony >= 0.5. On talk it reads "playing" 6% of the time,
     on the goldens "no band" 6%; at the 21 false moves harmony was 0.36 at most.
   - Plan gate: a playing band overrides the talking score (except unplanned jumps into
     another song). Without this the gate cost the goldens 0.306 -> 0.347 on replay (the
     talking score misfires on 15% of one recording's singing); with it, single-song
     recordings are unchanged.
   - Listener: with no band playing, a song change needs a second hearing within 6 s
     (`openings_confirm_without_band`); landing anywhere but a song's first sung slide
     needs gain and lead 0.5 higher (`openings_mid_song_nudge`; real mid-song entries
     cleared by 1.1+, false ones by 0.5).
   - Blank fallback (`song_probation_min_gain`, 1.2): after any AI song change, the
     listener's mean gain for the new song over 2.5-16 s; under the bar, blank until the
     song scores 2.5 or a song changes; an operator's choice is never put on probation.
     Real changes 1.77-5.0, false 0.17-0.75.
   - Results: 17 speech clips 4 -> 0 false changes. Goldens (real time, 9): everything
     off 0.306, on 0.312, with the fallback 0.298 (run-to-run noise ~0.03 per recording;
     song-change times identical). With the safeguards off on long talk clips, all 3
     false song changes were blanked ~16 s later; on the goldens the fallback never
     blanked during a song. **Full 4.4 h speech run: 21 -> 0 false slide changes** (blanking
     only on silence, as before).
   - Not fixed, separate: the start of a set (185523) sits on the first song's blank
     slide ~170 s because the follower doesn't step blank -> slide 1, and the listener
     ignores the song already on screen.
   Fast loop: `scripts/.tmp/plan_gate_sim.py` (offline over captured runs),
   speechneg/clips (16 min), golden_gate/run.sh (65 min), full speech (4.4 h) last.
3. [~] **Port the two retuned settings** into the real next-line check and replay full
   recordings (approved + lyric sets), plus step 2's speech set. This shows whether the
   standalone gain moves the screen and whether false triggers rise.
   **Result (2026-11-06, approved goldens only; event_replay, old hmix head, all logic
   fixes incl. FC_BRK=3, margin 9).** Of the 121 changes that are >2 s late or missed:

   | | Fixed | Mean delay cut | Working broken | Wrong-slide s (all windows) |
   |---|---|---|---|---|
   | Today's settings (g 0.3) | 48 | 2.19 s | 4 | 527 |
   | g 0.6 only | 54 | 2.47 s | 3 | 511 |
   | g 0.8 / 1.0 | 54 / 55 | 2.48 / 2.52 s | 3 | 511 / 508 |
   | Candidate weight 0.25 only | 41 | 1.35 s | 3 | 591 |
   | Both | 49 | 1.14 s | 2 | 586 |

   - Keep the higher "nothing new yet" cost: g 0.6-1.0 is a plateau, use 0.6. Margin 9
     stays best (7: same fixes, more wrong time; 11: 50 fixed, 560 s).
   - Drop the candidate weight: end to end it adds wrong time at every margin (6/9/12).
     The standalone test held false fires at 4%; in the engine the check also gets a
     song-order bonus (FC_PRI) and a free-start alternative, which the weight likely
     double-counts (unverified).
   - **Lyric-video sets (2026-11-06).** The checkpoint behind the old features was lost
     with /tmp, so all 14 recordings were redone with the surviving GRU-512x2 fixed-norm
     head (`dense_hn2_*`, `scripts/.tmp/dense_head.sh`); sets 2-5 got fresh real-time
     traces (`build/spike_music_only/rt_20261106_set*`, wrong-slide 0.45/0.45/0.45/0.59).
     Late-or-missed changes fixed, and wrong-slide seconds (no check -> g 0.3 -> g 0.6):

     | | Target | Fixed g 0.3 | Fixed g 0.6 | Wrong s: none / g 0.3 / g 0.6 |
     |---|---|---|---|---|
     | Approved goldens (9) | 65 | 20 | 26 | 403 / 346 / 328 |
     | Lyric set 1 | 56 | 37 | 44 | 242 / 181 / 149 |
     | Lyric sets 2-5 (never tuned on) | 113 | 67 | 76 | 952 / 808 / 812 |

     - The new head beats the old on the same 10 recordings (g 0.6: 70 vs 54 fixed,
       477 vs 511 wrong s), so it replaces it.
     - On the unseen sets g 0.6 fixes 9 more changes but saves no wrong time: set 5 loses
       26 s on three late-set "next" changes that fire wrong. g 1.0 is no better there
       (75 fixed, 803 s) and breaks more working changes. Keep g 0.6.
     - Broken working changes stay at 2 per group: the check is safe on new material.
   - Speech set: done in step 8, through the app (0 false changes once gated on the band).
   - Knob: `FC_CW` in scripts/.tmp/rails_replay.py. Rebuilt `dense_t_*.npy` timestamps
     (lost with /tmp) reproduce the old hm_new result on all 362 changes.
4. [~] **Grow the golden benchmark** as a separate "public" set:
   - JamendoLyrics (20 songs) -> goldens straight from their hand-made line timings.
   - LibriVox hymns -> fetch public-domain texts, drop read and non-English tracks, then
     `bootstrap_golden.py` with a COPY of the library (`--library`) and a staging
     `--recordings` folder, so the app's own data isn't touched.

   **Built 2026-11-06** in `../auto-presenter-research/public_goldens/` (none approved):
   - `jamendolyrics/`: 20 songs (`research/data/build_public_goldens.py`). Lines paired
     into slides, each distinct slide written once, so a repeat is a jump back; cues at
     the dataset's hand-timed line starts. Two songs have no stanza breaks, so their
     pairing may hide repeats (HILA, Ridgway).
   - `librivox/`: 33 hymns. Of 188 tracks, 40 matched an Open Hymnal title exactly (the
     rest are mostly French/German/Latin carols or hymns Open Hymnal lacks). 4 were read
     aloud (pitch test: share of voiced time on held notes, read 0.02-0.18, sung 0.24+;
     `scripts/.tmp/sung_or_read.py`) and 3 bootstrapped badly (doxology too short; 2 with
     under 50% of heard words on the cued slide; in `rejected/`). Texts in a research-only
     library `work/pub/hymn_library.db`; the kept 33 have 55-88% of heard words on the
     cued slide. Bootstrapped, so machine-timed, not reviewed.
   - Real-time baseline (nosheet, 2026-11-06), wrong-slide share:
     - JamendoLyrics (20): mean 0.490, median 0.463 (0.27 to 0.79). Dense pop/rock/metal mixes; one track (Ridgway) dropped 15 chunks.
     - LibriVox hymns (33): mean 0.233, median 0.216 (0.05 to 0.47). Optimistic: its cues come from the same kind of alignment the follower uses, not from people.
     - For scale: church goldens about 0.30, lyric-video sets 0.45 to 0.59.
     - Use: JamendoLyrics is the hard, independently-timed test; LibriVox is the hymn-shaped one (worst are the long multi-verse carols: O Come All Ye Faithful 0.47, It Is Well 0.43).
5. [x] **Fix when the check is asked:** timing (it's triggered off the slow clock) and
   which line it starts from. The biggest lever left, according to the oracle ladder.
   **Result (2026-11-06, event_replay, hn2 head, g 0.6, margin 9).**
   - Timing is not the problem in the fast-check replay: it decides every 0.25 s on its
     own clock and the flip lands at the next 0.5 s read-out (about 0.25 s on average).
     The slow-clock finding was the old Whisper-scored check.
   - Where the 88 still-slow changes failed (14 recordings): wrong current line 33,
     never beats staying 25, fired after +2 s 10, look-alike too close 7, other 13.
   - Four changes, each kept only if it helped (knobs in scripts/.tmp/rails_replay.py):
     1. `FC_REDONE`: keep refreshing "the line has ended" while its end is still heard.
        It used to fire once, often at the line's start, and expire before the next line.
     2. `FC_WAIT=6` (was 4): stay armed a little longer after a line ends.
     3. `FC_NOW=shown`: listen from the slide on screen, not the engine's belief argmax.
     4. `FC_SELFFLIP` + `FC_SFM=20`: when the check's own line-finder hears the band on
        another line than the screen by a wide margin, show that line. Biggest win.
   - Rejected: a lock keeping the check's own line until it ends (`FC_LOCK`, worse: 139 vs
     146 fixed); line-finder flip at margin 10 (breaks look-alikes like "What a
     beautiful / powerful name it is"); 2-3 tick persistence instead of a higher margin.

   | Set (slow changes) | Before (g 0.6) | + 1-3 | + 4 (all) | Wrong-slide s base / before / all | Broken before / all |
   |---|---|---|---|---|---|
   | Church approved (65) | 26 | 28 | 29 | 403 / 328 / 237 | 2 / 3 |
   | Lyric set 1 (56) | 44 | 48 | 47 | 242 / 149 / 121 | 2 / 3 |
   | Lyric sets 2-5 (113) | 76 | 84 | 86 | 952 / 812 / 501 | 2 / 2 |
   | **Held out:** LibriVox hymns (144) | 41 | 48 | 57 | 670 / 629 / 506 | 4 / 2 |
   | **Held out:** JamendoLyrics (171) | 116 | 123 | 132 | 864 / 685 / 547 | 3 / 8 |

   - Held out (public set, never tuned on): 157 -> 189 of 315 slow changes fixed,
     wrong-slide time 1314 -> 1053 s (-20%; -31% against no check). Broken working
     changes 7 -> 10 of 417, mostly on JamendoLyrics.
   - New standard arm: the old one plus `FC_REDONE=1 FC_WAIT=6 FC_NOW=shown FC_SELFFLIP=1
     FC_SFM=20` (`scripts/.tmp/fc_arm.sh NAME ...`; `PUB=1` replays the public set).
   - Left: wrong current line is still the biggest stage (mostly the lyric sets), then
     never-beats-staying (the hearing, mostly church recordings).
6. [x] **Time it live:** one extra encoder pass on the last 2.5 s per check, plus the head
   and decision; must fit the speed budget before shipping.
   **Plugged in (M1, mains power, Low Power Mode off, 2026-11-09).**

   | Stage per check | Alone: median / p95 | Beside the live engine: median / p95 |
   |---|---|---|
   | Encoder pass, last 2.5 s | 22 / 27 ms | 33-34 / 52-55 ms |
   | Phone head (torch, 1 thread) | 14 / 21 ms | 18-26 / 27-39 ms |
   | Decision | 12 / 18 ms | (not measured beside it) |
   | **Total** | **about 50 ms** | **about 65-75 / 110 ms** |

   - Paced at 4/s for 7 min beside the engine, twice: 0 of 1681 checks late each time.
   - Effect on the live engine (rec_20260818-213104_293, two runs each way):

     | | Alone | With the check |
     |---|---|---|
     | Speech recognition per pass | 738 / 669 ms | 787 / 736 ms (+7-10%) |
     | Engine lag p95 | 0.09 / 0.09 s | 0.19 / 0.20 s |
     | Wrong-slide | 0.369 / 0.381 | 0.421 / 0.371 |
     | Dropped audio | 0 / 0 | 0 / 0 |

     The wrong-slide spread between repeat runs (0.37-0.42) is larger than any gap
     between the arms, so no accuracy effect is visible from two pairs.
   - Verdict: fits comfortably on an M1 (about a quarter of each 250 ms tick) at a cost
     of 7-10% slower speech recognition and +0.1 s engine lag at p95.

   **Worst case: M1 on battery with Low Power Mode ON.**

   | Stage per check | Median | p95 | Runs on |
   |---|---|---|---|
   | Encoder pass, last 2.5 s (Whisper small.en, MLX) | 68-76 ms | 116-133 ms | GPU |
   | Phone head (GRU-512x2, torch, 1 thread) | 34-43 ms | 68-75 ms | CPU |
   | Decision (score every distinct slide, 8-22 per song) | 35 ms | 88 ms | CPU (numpy) |
   | **Total** | **about 140-155 ms** | **about 280 ms** | |

   - Paced at 4/s beside the live engine for 7 min: 1681 checks, 26 (1.5%) finished
     more than 250 ms late (encoder + head only).
   - Effect on the live engine (rec_20260818-213104_293, one run each):
     speech recognition 1681 -> 1956 ms per pass (+16%, p95 +19%), engine lag p95
     0.70 -> 0.88 s, no dropped audio, wrong-slide 0.414 -> 0.425 (single run, likely noise).
   - Verdict: it fits even on the slowest likely Mac, but it slows speech recognition by
     about a sixth there. Cheap cuts before shipping: run the head in MLX (on the GPU,
     not torch), and score only plausible slides in the decision instead of all.
   - Tools: `scripts/.tmp/bench_check.py` (PACE=seconds for the paced mode),
     `scripts/.tmp/live_contention.sh`, `FC_TIME=` in rails_replay for decision cost.
     Results in `work/bench_live/`.
7. [ ] **Investigate the slow start of a set.** Observed on rec_20261002-185523_091: the
   band sings the first lyric at 86 s, but the screen leaves the first song's opening
   blank only at 136-139 s, about 50 s late. That's in every recent run (safeguards off
   and on), so it isn't a regression; it is about 0.07 of that recording's 0.33
   wrong-slide. The original baseline scored the same only by jumping early to the wrong
   song (93 s). Suspected causes, both unconfirmed:
   - The follower doesn't step from the opening blank to slide 1.
   - The opening-line listener ignores the song already on screen.
   First, find how often this happens across all goldens and live sets: the gap between
   the first sung word and the first lyric slide. Then trace why on 185523, with
   `AUTO_PRESENTER_FOLLOWER_TRACE`, before choosing a fix.

Steps 2 and 4 don't depend on anything else; step 3 needs step 2's baseline. Step 7
stands alone.

8. [x] **Build it into the app (2026-11-09, uncommitted).**
   - Code: `sidecar/auto_presenter/follower/next_line.py` (phones from the bundled
     dictionary, the phone model in numpy, the decision ported from the replay's final
     arm, a listener thread running Whisper's encoder on the last 2.5 s every 0.25 s).
     `follower/live.py` applies a heard change at the next readout and moves 30% of the
     belief to the new line. `next_line_check` tunable (on). Model installed by
     `scripts/setup_next_line_model.py`, bundled by `scripts/build_sidecar.py` (8.7 MB,
     Apple-silicon builds only). Health reports `next_line_ms` / `next_line_skipped`.
   - The port matches the replay: 813/814 changes on church + lyric sets, 732/732 public.
   - MLX threading: unevaluated arrays belong to the thread that made them, so the
     check never loads the model; it waits for Whisper's first pass and builds its own
     stream and positional table on its own thread.
   - Real time, JamendoLyrics (20 held-out songs, 74 min) and talk clips (16 min):

     | Version | False moves on talk | Wrong-slide | Perceived | Flickers (<2 s) |
     |---|---|---|---|---|
     | No check (step 4) | 0 | 0.490 | 0.309 | 55 |
     | Check as ported | 17 | 0.306 | 0.343 | 288 |
     | + band playing, not off blank, not back one | 0 | 0.369 | 0.312 | 200 |
     | + 2 s settle after any change (shipped) | 0* | 0.411 | **0.259** | 62 |

     *the settle rule only holds moves back, so the talk result carries over (not re-run).
     Shipped version: 19/20 songs less wrong-slide, 15/20 better perceived, late penalty
     249 -> 89 s, anticipation 1067 -> 260 ms; check ~67 ms a pass, lag p90 0.23 s.
   - Why the rules: on talk the talking score still reads "singing" in a clip's first
     seconds, so only the band signal stops the check. On songs, the follower moves on
     during a line's last bar on purpose and the check, still hearing that line, put it
     back; then follower and check corrected each other within 2 s. Each flicker costs
     2 s in the perceived score, more than the check's gain.
   - Open: the settle rule gives back part of the wrong-slide gain (0.37 -> 0.41) for the
     perceived gain; a smarter rule (e.g. hold only reverts, let forward corrections
     through) might keep both. Church goldens not yet run through the app version.
     Run-to-run noise unmeasured. Results: `work/rt_nl/{jamendolyrics,gated,settle}`,
     analysis scripts in `work/rt_nl/analysis/`.

## Where things are
- Public data: `research/data/` (fetcher, manifest, README) -> `../auto-presenter-research/public_worship`.
- Working files (features, checkpoints, Kaggle outputs): `../auto-presenter-research/work`,
  not committed. Experiment scripts: `scripts/.tmp/` (uncommitted scratch).
- Rule: only public data (FMA, the sets above) goes to Kaggle; the church's own recordings never do.
