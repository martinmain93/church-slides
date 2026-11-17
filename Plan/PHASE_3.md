# Phase 3 — Evaluation harness

## Goal

Build the measurement infrastructure **before** any AI code. Every later phase will be evaluated objectively against this harness. If it isn't built first, you'll be guessing whether changes help.

## Prerequisites

- Phase 0 (Tauri shell + Python sidecar + data model + SQLite)
- Phase 1 (songs in the library — gives realistic test material)
- Phase 2 (setlist + presentation — gives realistic ground-truth scenarios)

## Scope

**Phase 3 Slice 1 (shipped):**
- Eval data types (`Recording`, `GroundTruthEvent`, `Decision`, `DeviationMarker`, `MetricResult`)
- Metric computations: anticipation error, wrong-slide rate, recovery time, override rate
- Replay metadata layer: load/save `Recording` JSON files, iterate ground truth chronologically
- A/B harness: per-recording evaluation + bootstrap-CI comparison report renderer

**Phase 3 Slice 2 (carry-over to Phase 4 — needs live audio):**
- Recording tool: live audio capture + ground-truth slide-change timeline. The
  ground-truth half is trivially derivable from `PresentationController` events
  we already have, but the audio capture half needs `AudioFrame` + sounddevice
  wiring which is Phase 4's deliverable. Land both together to avoid a partial
  recording format.
- Post-hoc annotation tool (mark slide changes against playback)
- `Replayer.audio_frames()` — pumps WAV → `AudioFrame` stream
- Live shadow mode wrapper (AI runs alongside a real session but only logs
  decisions; doesn't actually advance)
- Recordings UI surface in Settings → Developer

**Out:**
- Any ASR, matching, tracker, or UI work for the AI engine itself
- Cloud telemetry upload (Phase 8)

## Implementation steps

### 1. Recording tool

`sidecar/auto_presenter/eval/record.py`:
- Captures 16 kHz mono WAV via `sounddevice`
- Hook into the existing `PresentationController` from Phase 2: every state change is automatically captured as a `GroundTruthEvent` with the same monotonic clock as the audio
- Output: `~/AppData/AutoPresenter/recordings/<session_id>/audio.wav`, `ground_truth.csv`, `setlist.json`, `meta.json`
- "Start recording" toggle in the debug panel during a real service — the operator just runs the service normally; recording is automatic

This is the killer property: **every real service the operator runs becomes a recording for free**, because the presentation state changes are already known. No separate annotation step needed for live services.

### 2. Post-hoc annotation tool

`research/eval/annotate.py`:
- For recordings imported from elsewhere (e.g. last week's audio + a hand-written slide log)
- Plays a recording with a simple desktop UI (could be a route in the React app behind `/annotate`)
- Operator clicks "next slide" while listening; records timestamps
- Saves over the original or to a sibling file

### 3. Data types

`sidecar/auto_presenter/eval/types.py`:
- `GroundTruthEvent { t_seconds: float, item_idx: int, slide_idx: int }`
- `Recording { audio_path: Path, ground_truth: list[GroundTruthEvent], setlist_id: str }`
- `Decision { t_seconds: float, item_idx: int, slide_idx: int, source: Literal["ai","override"] }`
- `MetricResult { name: str, value: float, ci: tuple[float,float] | None }`
- `DeviationMarker { t_seconds: float, kind: Literal["chorus_repeat","jump","reorder","unplanned"] }` — added by hand for known-deviation eval recordings

### 4. Replay engine

`sidecar/auto_presenter/eval/replay.py`:
- `class Replayer: def stream(recording: Recording, speed: float = 1.0) -> Iterator[AudioFrame]`
- Emits frames at correct wall-clock cadence (real-time when speed=1, faster for batch eval)
- Also emits the ground-truth timeline for comparison
- Crucially: the replayer is interchangeable with `LiveMicCapture` — same `AudioFrame` interface — so any pipeline that runs live also runs on recordings

### 5. Metrics

`sidecar/auto_presenter/eval/metrics.py`:

```python
def anticipation_error_ms(decisions, ground_truth) -> list[float]:
    """Per-slide signed offset (negative=early, positive=late)."""

def wrong_slide_rate(decisions, ground_truth) -> float:
    """Fraction of total time the displayed slide != ground-truth slide."""

def recovery_time_ms(decisions, ground_truth, deviation_markers) -> list[float]:
    """ms from a marked deviation to the next correct slide."""

def override_rate(decisions) -> float:
    """Overrides per song."""

def false_lost_rate(belief_traces) -> float:
    """Spurious lost-mode entries on clean recordings (Phase 7+ uses this)."""
```

Each returns raw per-event values; aggregate to summary stats (median, p90) elsewhere.

### 6. A/B harness

`sidecar/auto_presenter/eval/compare.py`:
- Takes two pipeline factories + a list of recordings
- Runs each on each, computes all metrics, prints side-by-side table
- Bootstrap CIs (1000 resamples) so improvements are falsifiable
- Output formats: console table, JSON, Markdown report (commit-ready)

### 7. Live shadow mode

`sidecar/auto_presenter/eval/shadow.py`:
- Decorator/wrapper around the AI pipeline that hijacks decisions: log them but don't dispatch to the `PresentationController`
- During a real service, operator runs as usual (manual control); AI's decisions logged side-by-side for offline comparison
- Lowest-risk way to measure on real services. Use this through Phases 5+ before turning AI control on for real.

### 8. CLI

`research/eval/eval_pipeline.py`:
- `--recording <path>` `--pipeline <stub|naive|particle_filter>` `--output report.md`
- For batch eval across many recordings: `--recordings <dir>`

### 9. UI: recordings list

`src/components/eval/RecordingsList.tsx` (small, in Settings → Developer):
- Lists recorded sessions with metadata (date, duration, song count)
- "Replay through pipeline" action invokes `eval_pipeline` and shows the resulting metrics report inline
- "Open ground-truth annotator" for editing the timeline post-hoc

### 10. Library fixture

`data/fixtures/library_for_eval.json`:
- Hand-author 10–20 songs covering the deviation cases you want to test (long chorus, complex bridges, short tag, etc.)
- Use this as the eval-set library reference

## Files to create

| Path | Purpose |
|---|---|
| `sidecar/auto_presenter/eval/types.py` | GroundTruthEvent, Recording, Decision, etc. |
| `sidecar/auto_presenter/eval/record.py` | Live recording capture (hooks PresentationController) |
| `sidecar/auto_presenter/eval/replay.py` | Replay engine |
| `sidecar/auto_presenter/eval/metrics.py` | Four+ metric functions |
| `sidecar/auto_presenter/eval/compare.py` | A/B harness |
| `sidecar/auto_presenter/eval/shadow.py` | Shadow-mode wrapper |
| `research/eval/annotate.py` | Post-hoc annotation CLI |
| `research/eval/eval_pipeline.py` | Batch eval CLI |
| `src/components/eval/RecordingsList.tsx` | Recordings UI in Settings |
| `data/fixtures/library_for_eval.json` | Eval-set library |
| `data/recordings/.gitkeep` | Dir |
| `tests/sidecar/eval/test_metrics.py` | Synthetic decision streams → metric values |
| `tests/sidecar/eval/test_replay.py` | Replay roundtrip timing |
| `tests/sidecar/eval/test_compare.py` | A/B harness |

## Success criteria

- [ ] Running a real service via the Phase 2 manual app automatically produces a recording (audio + ground truth) on disk
- [ ] Replay any recording at 1× and N× speed without timing drift
- [ ] All metric functions computed correctly on synthetic input (verified by tests)
- [ ] A/B comparator emits readable Markdown report with bootstrap CIs
- [ ] Shadow mode can be toggled on during a manual service; logs AI decisions without affecting actual slides
- [ ] At least **3 real recordings** captured by end of phase
- [ ] Target: 5+ recordings before starting Phase 5; 10+ before Phase 6

## Tests required before phase complete

**Unit:**
- `test_metrics.py`: synthetic decision streams with known offsets → metric values match analytically computed answers
- `test_replay.py`: replay at speed=1.0 emits frames within 50 ms of expected wall-clock; at speed=10.0 emits in correct order with proportional timing
- `test_compare.py`: identical pipelines → bootstrap CI overlaps zero; pipelines that differ on a synthetic case have disjoint CIs

**Integration:**
- Run a 30-second simulated service in the Phase 2 manual app with recording on; verify resulting `audio.wav` + `ground_truth.csv` line up to within 100 ms

**Manual:**
- Walk through end-to-end: capture, view recording in UI, replay against a stub pipeline, read the report

## Risks

- **Audio capture format mismatch.** Mitigation: enforce 16 kHz mono in `record.py`; reject other formats with clear error.
- **Clock drift between audio and presentation events.** Mitigation: single monotonic clock source; both audio frame timestamps and `PresentationStateChanged` timestamps come from `time.monotonic()` in the sidecar.
- **Recordings get large.** Mitigation: 16 kHz mono WAV is ~30 MB per 30 minutes — fine. Compress to FLAC on session close (lossless, ~50% smaller).
- **Manual service operators forget to enable recording.** Mitigation: default ON in dev builds; opt-out toggle in settings; visible recording indicator.

## Effort estimate

5–7 days. The hooks into the Phase 2 PresentationController make recording trivial; most of the work is metrics + A/B harness + UI surface.
