# Phase 6 — Particle filter v1 (the big leap)

## Goal

Replace the naive matcher with a probabilistic state-space tracker. Achieve target metrics that get the system to "90% of cases" — the threshold where it's actually useful in production. This is the most important phase.

## Prerequisites

- Phases 0–5 complete; baseline numbers in `docs/done/BASELINE_NUMBERS.md`

## Scope

**In:**
- Library preprocessing: phonetic encoding (Double Metaphone), structural metadata, transition prior tables
- Particle filter over `(song_id, slide_id, position, rate)`
- Phonetic-aware emission likelihood
- Structural transition priors (within-song moves only — cross-song is Phase 7+)
- Lead-time scheduler for anticipation
- Debug panel: belief state visualization (top-3 hypotheses, particle distribution sparkline)
- Debug panel: live-editable tunable parameters
- A/B comparison vs Phase 5 baseline — must beat on every metric

**Out:**
- Deviation widening / re-localization (Phase 7)
- Library-wide recovery (Phase 8)
- LLM supervisor (Phase 8)
- Vocal separation (Phase 9)

## Implementation steps

### 1. Phonetic helpers

`sidecar/auto_presenter/index/phonetic.py`:
- `def metaphone(word: str) -> tuple[str, str]` — primary + secondary code via `metaphone` library
- `def phonetic_match_score(observed: str, expected: str) -> float` — 0..1 score combining primary-code match, secondary-code fallback, and edit distance on raw text
- `def normalize(word: str) -> str` — lowercase, strip punctuation, contractions

### 2. Indexed library (extend Phase 5)

`sidecar/auto_presenter/index/library.py` (extend):
- Add metaphone codes to `IndexedSlide`
- Build per-song `section_index: dict[Section, list[int]]` for fast structural queries

### 3. Transition priors

`sidecar/auto_presenter/tracker/transitions.py`:
- `def within_song_transitions(song: IndexedSong, current_slide_idx: int) -> dict[int, float]`
- Returns probability distribution over next slides given current
- Heuristic baseline:
  - +1 (next sequential slide): 0.85
  - Repeat current section's first slide (chorus repeat etc.): 0.08
  - Jump to known structural anchor (bridge/tag): 0.05
  - Other: 0.02 spread
- Each row sums to 1
- Calibrate weights from real recordings later (after Phase 7 data)

### 4. Particle filter

`sidecar/auto_presenter/tracker/types.py`:
```python
@dataclass
class Particle:
    song_id: str
    slide_idx: int
    position: float       # 0..1, fraction through slide
    rate: float           # words per second (latent tempo)
    weight: float

@dataclass
class BeliefState:
    particles: list[Particle]
    t_seconds: float
    @property
    def top_hypothesis(self) -> tuple[str, int, float]: ...
    @property
    def ess(self) -> float: ...
    @property
    def top_3(self) -> list[tuple[str, int, float]]: ...
```

`sidecar/auto_presenter/tracker/particle_filter.py`:
- `class ParticleFilter: def update(transcript: TranscriptEvent) -> BeliefState`
- Particle count: 500 (tunable via debug panel — see step 9)
- **Predict step** per ASR event with timestamp t:
  - For each particle: `position += rate * (t - last_t) + Gaussian noise`
  - If `position >= 1`: sample next slide from `within_song_transitions`; reset `position = position - 1`
  - Add small noise to `rate` (random walk; std ~0.1 wps)
- **Update step**:
  - For each particle, find expected word at `slot = floor(position * n_words)`
  - Compute `phonetic_match_score(observed_word, expected_word)`
  - New weight = old weight × likelihood (with Gaussian-ish kernel: `0.05 + 0.95 * score`)
- **Resample**: stratified resampling when `ESS < 0.5 * N`

### 5. Resampling

`sidecar/auto_presenter/tracker/resample.py`:
- Stratified resampling
- Reset weights to 1/N after resampling
- Add small jitter to `position` and `rate` to maintain diversity

### 6. Initialization

- At session start: place all particles at `(setlist[0], slide_idx=0, position=0.0, rate=2.0)` with weight 1/N
- If song has `default_tempo_wps` set, seed `rate` from it
- After first slide change is committed, this becomes the prior for the next slide

### 7. Lead-time scheduler

`sidecar/auto_presenter/decision/scheduler.py`:
- Replace the naive matcher's commit logic
- Subscribe to `BeliefState` updates
- Compute `t_end_of_current_slide = t_now + (1 - mode_position) / mode_rate`
- When `t_end_of_current_slide - t_now < lead_time` (default 500 ms):
  - Commit `MatcherDecision(item_idx, slide_idx + 1, ...)` via the director
  - Mark transition committed; don't re-fire for the same slide
- Edge case: `mode_idx` is last slide of song → don't auto-advance; wait for next song's first slide to be the mode

### 8. Director integration

`sidecar/auto_presenter/decision/director.py` (from Phase 5):
- Swap matcher implementation: `ParticleFilter` replaces `NaiveMatcher`
- The output type (`MatcherDecision`) is unchanged — controller, UI, override flow all unaffected

### 9. Debug panel — full belief-state visualization

`src/components/debug/BeliefStateTab.tsx` (extend from Phase 5):
- Top-3 hypotheses with marginal probabilities (horizontal bars)
- Particle distribution sparkline showing entropy + ESS over last 30 s
- Current `mode_rate` (words/sec)
- Current `position` within mode slide

`src/components/debug/TunablesTab.tsx`:
- Live-editable sliders/inputs for:
  - `n_particles` (200–2000)
  - `lead_time_ms` (0–1000)
  - `rate_noise_std` (0.01–0.5)
  - `position_noise_std` (0–0.1)
  - `emission_floor` (0–0.2)
- Changes apply immediately to the running tracker (subscribe to a Pydantic `TunableConfig` BehaviorSubject equivalent)
- "Reset to defaults" button
- "Save current as default" persists to settings

This UI is critical for the next 1–2 weeks of tuning. Spend time making it useful.

### 10. Sweep harness

`research/dead_ends/particle_filter/sweep_pf.py`:
- Grid over `n_particles ∈ {200, 500, 1000}`, `lead_time ∈ {300, 500, 700}ms`, `rate_noise_std ∈ {0.05, 0.1, 0.2}`
- Run on the full eval set; output table of metrics per config
- Pick best config; commit to `docs/done/BASELINE_NUMBERS.md` as the new "Phase 6" row

## Files to create

| Path | Purpose |
|---|---|
| `sidecar/auto_presenter/index/phonetic.py` | Metaphone helpers + match score |
| `sidecar/auto_presenter/index/library.py` (extend) | Add phonetic encoding |
| `sidecar/auto_presenter/tracker/types.py` | Particle, BeliefState |
| `sidecar/auto_presenter/tracker/particle_filter.py` | Core filter |
| `sidecar/auto_presenter/tracker/emission.py` | Phonetic emission likelihood |
| `sidecar/auto_presenter/tracker/transitions.py` | Within-song prior tables |
| `sidecar/auto_presenter/tracker/resample.py` | Stratified resampling |
| `sidecar/auto_presenter/decision/scheduler.py` | Lead-time advance committer |
| `sidecar/auto_presenter/config/tunables.py` | Live-tunable params |
| `src/components/debug/BeliefStateTab.tsx` (extend) | Top-3 + sparkline |
| `src/components/debug/TunablesTab.tsx` | Live parameter editor |
| `research/dead_ends/particle_filter/sweep_pf.py` | Hyperparameter sweep |
| `tests/sidecar/index/test_phonetic.py` | Metaphone correctness, score behavior |
| `tests/sidecar/tracker/test_particle_filter.py` | Synthetic perfect-input |
| `tests/sidecar/tracker/test_emission.py` | Likelihood unit tests |
| `tests/sidecar/tracker/test_transitions.py` | Prior distribution sanity |
| `tests/sidecar/tracker/test_resample.py` | ESS recovery after resample |
| `tests/sidecar/decision/test_scheduler.py` | Lead-time threshold |
| `tests/integration/test_pf_pipeline.py` | Full eval set must beat baseline |

## Success criteria

- [ ] **Anticipation error**: median |error| < 500 ms on eval set; median signed error in `[-500ms, 0ms]` (early on average)
- [ ] **Wrong-slide rate**: < 5% on eval set (down from ~30% baseline)
- [ ] **No flicker**: same-slide commits don't bounce within 1 s
- [ ] **Compute budget**: per-token update < 1 ms on target laptop hardware
- [ ] **Synthetic stability**: tracker on perfect-ASR synthetic input stays on the right slide for an entire song
- [ ] **A/B regression**: every metric strictly better than Phase 5 numbers in `BASELINE_NUMBERS.md`
- [ ] **Live-tunable parameters**: can change `lead_time_ms` in the debug panel during a running session and see effect immediately
- [ ] Hyperparameter sweep run; chosen config documented in `BASELINE_NUMBERS.md`

## Tests required before phase complete

**Unit:**
- `test_phonetic.py`: known sound-alike pairs score high; unrelated words score low; specific test for "hosanna"/"hose-anna"
- `test_particle_filter.py`: synthetic perfect transcript → top hypothesis matches ground truth at every step
- `test_emission.py`: likelihood is monotone in match score
- `test_transitions.py`: every row sums to 1.0; structural priors look sane on fixture songs
- `test_scheduler.py`: synthetic position trajectory at known rate → advance fires at correct time

**Integration:**
- `test_pf_pipeline.py`: every Phase 3 recording, full eval, all metrics meet targets
- A/B comparison: `compare(naive_pipeline, pf_pipeline)` shows strict improvement on every recording

**Performance:**
- 60-second clip, 500 particles → end-to-end real-time-factor < 1.0
- Memory: pipeline RSS stable over 30-minute file replay

**Regression:**
- Snapshot test: golden output on a chosen recording. Future changes must justify diff.

## Risks

- **Particle count tuning is empirical.** Mitigation: sweep harness; live-tunable in debug panel; default validated by eval set.
- **Tempo (`rate`) estimation unstable early in song.** Mitigation: initialize from setlist song-tempo metadata if available; otherwise prior of 2 wps; allow 5-second warmup before trusting tempo.
- **Phonetic encoding is English-specific.** Mitigation: scope V1 to English; document explicitly.
- **Emission likelihood is multiplicative — one bad word can crash a particle.** Mitigation: floor likelihood at 0.05 even for total mismatch; tune via sweep + live debug panel.
- **Position drift on long slides.** Mitigation: discrete word-slot snapping when an observed word strongly matches a specific expected slot.

## Effort estimate

10–14 days. The filter itself is ~1 week; the tuning + debug-panel UX + sweep harness is the rest.
