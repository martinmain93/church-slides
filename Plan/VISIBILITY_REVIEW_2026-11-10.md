# Review of evals, metrics and visibility tooling — 2026-11-10

**Question:** where could our numbers be wrong or flattering, and what can't we
see at all?

**Answer:** the tracking maths is measured carefully, but the headline number
has a blind spot that rewards switching early, the release gate rests on nine
recordings run once each, and several older gates have quietly stopped
measuring what ships. Nothing in the tooling checks that a decision reaches the
screen, and nothing from the field says how often an operator had to correct
the AI.

Read-only review. Two findings were reproduced with a few lines of in-memory
Python (marked *reproduced*); the rest are from reading the code, the stored
results and this morning's release-gate log. No tests, builds or evals were
run.

## Terms

| Term | Meaning |
|---|---|
| Golden | A recording with the correct slide changes marked. *Approved* means a person checked them. |
| Release gate | `research/eval/run_follower_eval.py`: plays goldens into the real engine in real time and scores the AI's slide changes. |
| Wrong-slide rate | Share of time the screen differs from the golden. |
| Perceived score | The headline since 24 October. Weights each kind of wrongness by how bad it looks, timed against the singing. |
| Hidden-line rate | `eval/visible.py`: share of singing time when the line being sung was not on screen. Exists, but the gate does not report it. |
| Anticipation | How early or late the AI's change was against the golden's. |
| Build report | `scripts/perf_report.py`: the 100-second check every release build runs. |

## Findings: numbers that read better than the room

| # | Finding | Evidence |
|---|---|---|
| 1 | The perceived score charges nothing for switching early | reproduced |
| 2 | The release gate is nine recordings, run once each | stored data |
| 3 | Timing medians leave out the worst cases | reproduced |
| 4 | Half the release gate measures a setup nobody runs | today's log |
| 5 | The truth for timing comes from the same kind of model being judged | read |
| 6 | Unapproved goldens load exactly like approved ones | read, stored data |
| 7 | The build report's accuracy check can barely fail | read |
| 8 | Wrong-slide forgiveness only ever removes bad time | read |
| 9 | `perf_check.py` no longer measures the shipping engine | read |
| 10 | Old scoreboards and four tests that never run | read |

### 1. The perceived score charges nothing for switching early

In the app: a screen that jumps to the next slide while the band is still
singing the current one hides the words people need. The headline score calls
that perfect.

The rule (`eval/metrics.py:572-591`) makes the next slide free from the moment
the current slide's last line starts, however long that line is. When a slide's
line breaks can't be found in the library (song edited since, or a public
golden whose song isn't in the library), the whole slide counts as its last
line (`eval/sung_timing.py:184-187`, `:239-240`), so the next slide is free for
the whole slide.

Six slides of 10 seconds, scored three ways:

| Screen behaviour | Wrong-slide | Perceived | Sung line hidden |
|---|---:|---:|---:|
| On time | 0.00 | 0.00 | 0.00 |
| 5 s early (line breaks known) | 0.42 | **0.00** | 0.37 |
| 9 s early (line breaks not found) | 0.75 | **0.00** | 0.73 |
| 4 s late | 0.40 | 0.34 | 0.47 |

The lookahead was raised from 0.8 s to 2.0 s while tuning against this score,
which is the direction the blind spot rewards. In today's gate log the
perceived mean is 0.19 against a wrong-slide mean of 0.31 for the shipping
setup; how much of that gap is this rule is not known.

Ideas: print the hidden-line rate beside the perceived score in the gate; cap
the free-early window (for example the last 2 s of a slide); have the gate
print how many slides had no line breaks found and refuse to score if any.

### 2. The release gate is nine recordings, run once each

Nine approved goldens, 61 minutes. Six are single songs from the 18 August
batch built from audio files; three are multi-song (11, 12 and 3 minutes).
Each is played once. Real-time runs differ from run to run (the GPU is shared
between speech and the follower), and nothing measures by how much, so a
difference of a few hundredths between two engines can't be told from noise.
The same nine have also been the tuning set for months. `eval/compare.py` has
confidence-interval code; the gate doesn't use it.

Ideas: run the gate twice on unchanged code once, to learn the spread; add a
fixed sample of the held-out public set to the gate; approve one live room
recording a month.

### 3. Timing medians leave out the worst cases

- Anticipation only counts cues the AI hit within 5 s (`metrics.py:171`). A
  change 6 s late is dropped, not counted as late. Reproduced: every change
  6 s late gives "no data", not +6000 ms.
- The build report counts a slide "on time" from 2 s early to 5 s late
  (`perf_report.py:509-530`).
- Recovery time drops jumps that were never recovered (`compare.py:137`).

Idea: always print "matched N of M cues" beside a median, and count misses as
the window's edge.

### 4. Half the release gate measures a setup nobody runs

`rt_church.sh` runs `--configs on,nosheet` with no lead sheets. `on` means
"follow songs that have a lead sheet", so with none the follower never engages:
the log shows no follower cost for any `on` run. That arm is the old word
tracker with the follower switched off, which no install runs by default. It
costs about half of a three-hour run. Nothing checks that the follower engaged
for a config that claims it.

The result file also records no commit, machine, power state or date
(`run_follower_eval.py:412`). This morning the gate ran while my own short
scripts ran, and a test run was going beside it when I looked; the results
won't show either.

Ideas: fail a run whose follower didn't engage when the config says it should;
stamp results with commit, dirty flag, machine and power state; record other
heavy processes seen at start.

### 5. The truth for timing comes from the same kind of model being judged

Sung timing and the hidden-line rate both come from an offline Whisper
transcript. Where Whisper can't make out the words, the timing falls back to an
estimate and the hidden-line rate skips the passage. Those are the passages
where the live engine, also Whisper, is most likely to be wrong. The gate
doesn't print what share of cues was measured, so a recording that is mostly
estimates looks the same as one that is fully measured.

Idea: print the measured share per recording; use the newer vocal aligner
(already used to re-time drafts) for the truth, so it differs from the engine.

### 6. Unapproved goldens load exactly like approved ones

`eval/replay.py:60-68` overlays any `golden.json`, approved or not. There are
15 unapproved ones, nine of them re-timed by machine yesterday. Only the
hand-written list in `rt_church.sh` keeps them out of a gate run. The build
report's fixture is also machine-estimated.

Idea: an `approved_only` default in the loader, with a loud flag to override.

### 7. The build report's accuracy check can barely fail

Seven cues, machine-estimated, on a 100-second clip. Expected is 6 of 7 with a
median delay of 3.5 s; it warns at 5 and fails at 2 (`perf_report.py:586-618`).
The Intel and Windows builds measure no slide following at all, because both
build machines fail the machine check. 0.2.0 newly offers Assist on exactly
those computers, with no accuracy check behind it in any build.

Idea: a short hand-approved fixture; run the processor path on it with replayed
audio so slow build machines can still score it.

### 8. Wrong-slide forgiveness only ever removes bad time

After an unexpected jump, 2 s is excused, but only if the screen was wrong
(`metrics.py:320-341`). Right in that window counts for you; wrong doesn't
count. Blank-slide cues are excluded on the grounds that blanking "ships off"
(`metrics.py:105-124`); it has been on by default since 16 October, so a
shipped feature is unscored either way.

### 9. `perf_check.py` no longer measures the shipping engine

It uses a 4 s / 0.5 s speech window, not the shipping 6 s / 1 s
(`perf_check.py:103-106`), times only the word tracker (the follower decides
slides on Apple silicon), compares against a baseline from 3 September, and is
called by neither the release script nor CI.

Idea: delete it, since the build report covers the same ground on the real
engine.

### 10. Old scoreboards and four tests that never run

`Plan/REALTIME_EVAL.md` (1 September) and `Plan/BASELINE_NUMBERS.md` (August)
describe engines that no longer exist. `run_realtime_eval.py` still stamps
decisions when the words were sung, not when the engine could act, which was
measured to understate lateness badly. The four files in `tests/integration/`
sit outside the sidecar's test path, so neither the release nor CI runs them.

Lower: the gate stamps each decision with the last audio-level tick, up to
0.1 s early; the mean is per recording, so a 3-minute clip weighs as much as a
12-minute set; on/off settings are dropped from a recording's saved
configuration (`server.py:974-978`).

## Gaps: things we can't see

| # | Gap | Idea |
|---|---|---|
| A | No check that a decision reaches the screen. The gate has no operator; the long-service scenario clicks, edits and switches modes but only measures speed and memory. All three top findings in this morning's engine review were invisible to every tool. | Scripted-operator tests with pass/fail: the engine's idea of the screen matches what was shown; the AI resumes within a set time of a click; no song jump after a setlist edit. Fast, in-memory, in the normal test run. |
| B | Nothing from the field says how well the AI did. The service count has "by AI" and "by hand" but a correction looks the same as ordinary manual use. | Count hand changes within 5 s of an AI change to a different slide, behind-warnings, and which AI tier ran. Numbers only, same opt-in; the privacy page would need the new items. |
| C | A recording keeps what was shown, not what the engine decided and held back, or why it moved. The reason is only in a log that rotates. | Save each decision with its reason and whether it was shown. |
| D | Talk-only, Assist and the held-out set are separate manual runs. | One gate command that runs all of them with fixed pass marks. |
| E | No accuracy number for the processor path in any automatic run. | A small subset of the gate with `--backend whisper`. |
| F | No current scoreboard. Results live in work folders and memory notes. | One file the gate appends to: date, commit, each score. |

## Suggested order

1. Hidden-line rate in the gate, and results stamped with commit and machine (findings 1, 4).
2. Scripted-operator tests (gap A).
3. Learn the run-to-run spread (finding 2).
4. Field correction count (gap B).
5. Approved-only loading, drop the dead `on` arm, retire `perf_check.py` (findings 6, 4, 9).

## What looked sound

- The gate drives the real server in real time with production settings; earlier harnesses that built their own engine are gone.
- The wrong-slide rate has a strict mode and it is kept.
- The long-service suite records commit and machine and compares with the previous run.
- The release refuses to build in Low Power Mode.
- Error reports strip speech, names and paths, and send nothing without consent.
