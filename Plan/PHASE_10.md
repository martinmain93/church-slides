# Phase 10 — Optional: V2 musical priors

## Goal

Polish — incorporate non-lyric musical signals to improve specific edge cases that lyric-only matching can't solve well: anticipating the first slide before any vocals are sung (instrumental intros), detecting unplanned songs from their musical character before lyrics arrive, and aligning slide changes to musical bar boundaries. **Optional** — only enter this phase if Phase 8 evaluation shows specific gaps in these areas.

## Prerequisites

- Phase 8 complete (and Phase 9 complete *if* it was needed)
- Eval data showing concrete gaps that musical signals would close:
  - First-slide-up time > 1 s after instrumental intro (lyric matcher inevitably starts late)
  - Unplanned songs taking > 6 s to detect
  - Manual operator preference for bar-aligned slide changes

## Scope

**In:**
- Vocal onset detection (librosa-based)
- Chord/key extraction (madmom or essentia) as a secondary signal
- Audio fingerprinting (Chromaprint) over per-song reference recordings — for known-song detection from intro audio
- Integration of musical signals as additional particle-filter evidence streams
- Beat/downbeat tracking for bar-aligned advance commits (configurable; some operators may prefer it off)

**Out:**
- Real-time melody/chord transcription beyond what existing libs provide
- Custom-trained music ID models
- Per-song reference recordings beyond what's already available
- Multi-instrument breakdown beyond vocal-onset

## Implementation steps

### 1. Vocal onset detection

`sidecar/auto_presenter/audio/onset.py`:
- librosa `onset_detect` with vocal-frequency band emphasis (filter to ~80 Hz–500 Hz fundamental range first)
- `class VocalOnsetDetector: def stream(audio: Iterator[AudioFrame]) -> Iterator[OnsetEvent]`
- `OnsetEvent { t_seconds: float, strength: float, type: Literal["vocal_entry"] }`
- During an instrumental intro (no recent ASR words), the first detected vocal onset triggers a candidate "first vocal of next slide" injection into the tracker

### 2. Chord / key extraction

`sidecar/auto_presenter/audio/chord.py`:
- madmom `CRFChordRecognitionProcessor` or essentia equivalent
- Emits chord-change events at ~2 Hz
- Used as a secondary signal: a song change is more plausible when the chord progression resets (e.g. tonic chord at downbeat after a section change)
- Fold into transition prior weighting in `tracker/musical_priors.py`

### 3. Audio fingerprinting

`sidecar/auto_presenter/index/audio_fingerprint.py`:
- Chromaprint (`acoustid` Python binding) for compact fingerprints
- Pre-compute fingerprint per song from a reference recording (studio version, prior live recording, or rehearsal)
- At session time: rolling fingerprint of incoming audio, match against library fingerprints
- Tolerant to key shifts via Chromaprint's design
- Output: top-3 candidate songs with match scores → fold into transition priors during instrumental sections

### 4. Beat / downbeat tracking

`sidecar/auto_presenter/audio/beat.py`:
- madmom `DBNDownBeatTrackingProcessor`
- Emits `BeatEvent { t_seconds, beat_idx, is_downbeat }`
- `decision/scheduler.py` extension: when `bar_aligned_advance == True`, snap commits to nearest upcoming downbeat (within ±300 ms of original lead-time target)

### 5. Musical priors fold-in

`sidecar/auto_presenter/tracker/musical_priors.py`:
- `class MusicalPriors: def update(belief: BeliefState, signals: MusicalSignals) -> BeliefState`
- Up-weights particles whose expected song matches fingerprint + chord evidence
- Down-weights particles inconsistent with detected onsets (e.g. claiming mid-slide while audio is silent)

### 6. Reference recordings ingestion

`scripts/ingest_reference.py`:
- CLI to attach a reference recording (mp3/wav) to a song in the library
- Computes fingerprint + stores alongside song metadata
- Songs without reference recordings simply opt out of fingerprint detection — graceful degradation

`src/components/library/SongEditor.tsx` (extend Phase 1):
- "Attach reference recording" action in metadata section
- File picker, fingerprint progress, success indicator
- Allow re-recording if church updates arrangement

### 7. Settings

`src/components/settings/AISettings.tsx` (extend):
- Toggle: bar-aligned slide advance
- Show: how many library songs have reference recordings

## Files to create

| Path | Purpose |
|---|---|
| `sidecar/auto_presenter/audio/onset.py` | Vocal onset detector |
| `sidecar/auto_presenter/audio/chord.py` | Chord/key extractor |
| `sidecar/auto_presenter/audio/beat.py` | Beat/downbeat tracker |
| `sidecar/auto_presenter/index/audio_fingerprint.py` | Chromaprint reference index |
| `sidecar/auto_presenter/tracker/musical_priors.py` | Fold musical signals into particle weights |
| `sidecar/auto_presenter/decision/scheduler.py` (extend) | Bar-aligned advance |
| `scripts/ingest_reference.py` | Reference-recording ingestion CLI |
| `src/components/library/SongEditor.tsx` (extend) | Attach reference UI |
| `src/components/settings/AISettings.tsx` (extend) | Bar-align toggle |
| `tests/sidecar/audio/test_onset.py` | Onset detection on test clip |
| `tests/sidecar/audio/test_chord.py` | Chord extraction matches expected key |
| `tests/sidecar/audio/test_beat.py` | Downbeat alignment on test clip |
| `tests/sidecar/index/test_audio_fingerprint.py` | Match correctness against reference |
| `tests/integration/test_instrumental_intro.py` | First-slide-up timing improvement |
| `tests/integration/test_unplanned_song_with_fingerprint.py` | Faster unplanned song detection |
| `tests/integration/test_no_regression_v2.py` | Full eval set unchanged on clean cases |

## Success criteria

- [ ] **First-slide-up time** after instrumental intro: median < 1 s after vocals start (improvement from Phase 8's 2–3 s)
- [ ] **Unplanned-song detection from intro**: when a reference recording exists, > 50% of unplanned-song cases detected before the first vocal phrase ends
- [ ] **No regression** on Phase 8 metrics for clean services (within bootstrap CI)
- [ ] **Bar-aligned advance**: when enabled, commits align within ±100 ms of nearest downbeat
- [ ] **Graceful degradation**: songs without reference recordings still work via lyrics-only path

## Tests required before phase complete

**Unit:**
- `test_onset.py`: synthetic clip with known vocal entry → onset detected within 100 ms
- `test_chord.py`: studio-key clip → extracted key matches annotation
- `test_beat.py`: known-tempo clip → downbeats within 50 ms of annotated
- `test_audio_fingerprint.py`: query == reference → top match score > 0.9; query of unrelated song → match score < 0.3

**Integration:**
- `test_instrumental_intro.py`: recording with known instrumental intro length → first slide up within target after vocal onset
- `test_unplanned_song_with_fingerprint.py`: recording starts unplanned song that has a reference; fingerprint detection happens before lyric-based localization
- `test_no_regression_v2.py`: full Phase 8 eval set + Phase 10 musical priors → no metric regression

**Performance:**
- Combined musical-priors compute < 200 ms latency overhead per ASR token
- Memory: chord + beat + fingerprint indices < 50 MB additional

**Manual:**
- Live demo on a service with multiple songs starting via instrumental intros; observe slide-up timing visually
- Audit chord/key signal: does it correlate with section transitions on a real recording?

## Risks

- **Reference recordings rarely exist** for all library songs. Mitigation: graceful degradation; fingerprint detection is purely additive — songs without references work via lyrics.
- **Live audio differs from studio reference** (tempo, key, arrangement). Mitigation: Chromaprint is somewhat tolerant; consider per-church reference recordings (last week's service) to capture the band's actual style.
- **Bar-aligned advance can feel laggy** when downbeat is far from natural slide-end. Mitigation: only snap when within ±300 ms of original target; otherwise commit on time.
- **Madmom + essentia + librosa + Chromaprint is a heavy dependency stack.** Mitigation: each module is opt-in via config; choose only the signals that earn their keep on eval data.
- **Diminishing returns.** Mitigation: measure the gap before committing. If Phase 8 already meets all targets, this phase is pure polish — don't build it for its own sake.

## Effort estimate

7–10 days (skip if not justified by Phase 8 eval data).
