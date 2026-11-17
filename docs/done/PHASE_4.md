# Phase 4 — ASR baseline + audio setup wizard

## Goal

Streaming, word-timestamped transcript working end-to-end from microphone or file, plus a polished audio-setup UX. By the end of this phase, the operator can configure their audio source through a wizard, see live transcripts in the debug panel, and the eval harness can replay recordings through the ASR pipeline.

No matching logic yet — this phase validates the audio path and ASR quality before any AI engine work.

## Prerequisites

- Phase 0 (foundation)
- Phase 1 (library)
- Phase 2 (setlist + presentation + debug panel scaffold)
- Phase 3 (eval harness)

## Scope

**Slice 1A (shipped):**
- `AudioFrame` data type (16 kHz mono float32, monotonic timestamps)
- `AudioCapture` protocol + `FileCapture` (WAV → 100 ms frames, real-time or batch pacing, auto-downmix to mono, linear resample to 16 kHz)
- `Word`, `TranscriptEvent` types with `is_final` + `segment_id`
- `ASRBackend` protocol + `MockASRBackend` (deterministic, schedule-driven)
- Synthetic WAV test fixtures via `wave` + numpy — no binary fixtures committed

**Slice 1B (shipped):**
- `WhisperBackend` wrapping faster-whisper — batch mode, word-level timestamps, language/beam/VAD knobs
- Dependency-injected model: tests use a mocked `WhisperModel`, no network or model download
- `.load()` factory for the production path (downloads `tiny.en`/`large-v3` from HF on first call)
- Optional real-model integration test gated on `RUN_WHISPER_TESTS=1`

**Slice 1C (shipped):**
- `SlidingWindowWrapper` — composes any `ASRBackend` into incremental transcription. Rolling buffer of `window_seconds` audio; runs the wrapped backend every `step_seconds`; partitions output by `stable_margin_seconds` into final (committed, never re-emitted) and partial (soft hints for the matcher).
- End-of-stream flush so trailing words always reach the matcher.
- Idempotent under repeated `transcribe()` calls (state resets per call).

**Deferred to a future slice (not blocking matcher work):**
- stable-ts upgrade for word timestamps (DTW alignment improves on faster-whisper's native timestamps for the lead-time scheduler in Phase 6). Phase 6 will evaluate whether the native timestamps are sufficient.

**Slice 2A (shipped):**
- `LiveCapture` (sounddevice) — callback-driven, bounded queue, drops-on-overflow rather than block PortAudio
- Audio device enumeration over WS protocol
- Audio input picker in Settings with graceful "AI engine offline" fallback in browser dev

**Slice 2B-1 (shipped):**
- `AudioSession` orchestrator (capture → `SlidingWindowWrapper` → broadcast)
- Singleton in server with broadcast helper to all connected WS clients
- Mock backend wired into the session for end-to-end demo paths
- Debug panel Transcript tab populated with live event stream

**Slice 2B-2 (shipped):**
- `WhisperBackend.load(model_size)` wired into the audio session
- Loaded on a worker thread (`asyncio.to_thread`) so model download/init doesn't block the WS event loop
- Model cached across stop/restart at the same size; invalidated when size changes
- DebugPanel backend + model picker

**Slice 3 (shipped):**
- `RecordingSink` writes audio frames to incremental WAV + ground-truth slide changes to JSON
- `AudioSession` exposes a `frame_observer` hook the sink subscribes to
- Server: `start_audio_session` with `record=true` opens a `RecordingSink`; `record_slide_change` notifications append to it; stop finalizes the recording on a worker thread and broadcasts `recording_finalized`
- Recording manager: list / delete / export-as-zip
- Per-app `record_services: bool = true` setting; `PresentationView` auto-starts a recording when enabled and pipes slide changes to it
- Recordings list in Settings with export + delete

**Deferred to a Phase 4 polish slice (not blocking Phase 5):**
- First-launch audio setup wizard (the make-or-break UX for non-technical operators)
- Audio level meter in the input picker
- Model-download progress UX (HF Hub progress callback → broadcast → progress bar)
- Hardware-aware model auto-selection on first launch
- Shadow-mode wrapper around the AI matcher — meaningless until Phase 5's matcher exists, so it lands alongside the Phase 5 work

**Out:**
- Any matching/decision logic (Phase 5)
- Vocal separation (Phase 9)

## Implementation steps

### 1. Audio capture

`sidecar/auto_presenter/audio/capture.py`:
- `class AudioCapture(Protocol): def stream() -> Iterator[AudioFrame]`
- `LiveCapture(device_index)` using `sounddevice`
- `FileCapture(path, realtime: bool)` — used by eval replay
- `AudioFrame { samples: np.ndarray, t_start: float, t_end: float, sample_rate: int }`
- 16 kHz mono enforced; resample if needed
- Frame size: ~100 ms (1600 samples)
- Tauri command exposes `list_audio_devices()` to UI

### 2. ASR backend interface

`sidecar/auto_presenter/asr/backend.py`:
- `class ASRBackend(Protocol): def stream(audio: Iterator[AudioFrame]) -> Iterator[TranscriptEvent]`
- Lifecycle: `start()`, `stop()`, `is_ready()`

`sidecar/auto_presenter/asr/types.py`:
- `Word { text: str, t_start: float, t_end: float, confidence: float }`
- `TranscriptEvent { words: list[Word], is_final: bool, segment_id: int }`
  - `is_final=False` partials may be revised by future events with the same `segment_id`
  - `is_final=True` is committed and immutable

### 3. Whisper streaming wrapper

`sidecar/auto_presenter/asr/whisper_stream.py`:
- `faster-whisper` for inference, `stable-ts` for word-level timestamps
- Sliding audio buffer (~6 s); transcribe every 0.5–1 s chunk with overlap
- Stabilization: a partial word survives N consecutive transcriptions → promote to final
- Configurable: model size, device, chunk_seconds, overlap_seconds, language hint
- Hardware-aware default model selection on first run:
  - NVIDIA CUDA / Apple Silicon MPS: `large-v3`
  - x86 macOS / modern laptop CPU: `medium`
  - Older hardware: `small` with cloud-Deepgram option

### 4. Stub Deepgram backend

`sidecar/auto_presenter/asr/deepgram.py`:
- Implements `ASRBackend` Protocol
- Not wired up by default; raises clear error with instructions
- Cloud fallback path documented for Phase 8

### 5. VAD

`sidecar/auto_presenter/asr/vad.py`:
- Silero VAD wrapper
- Emits `VoiceActivityEvent { t_start, t_end, active }`
- Used later for instrumental detection; build the hook now even if not yet consumed

### 6. Model download UX

`sidecar/auto_presenter/asr/model_manager.py`:
- Detect available hardware on first run
- Recommend a model; let user override
- Download to app data dir with progress events sent over WS to UI
- Cache; skip re-download if already present

`src/components/onboarding/ModelDownloadDialog.tsx`:
- Progress bar
- "What's this?" explainer
- Cancel + retry

### 7. Audio setup wizard (the critical UX)

`src/components/onboarding/AudioSetupWizard.tsx`:

Multi-step flow:

**Step 1 — Choose your input**
- Two big cards:
  - "Soundboard line-out (recommended)" with a 30-second video tutorial showing the cable + interface setup
  - "Microphone (fallback)" with a note: "Quality may be lower; we'll do our best"
- Result feeds into a stored `audio.input_source: "vocal_aux" | "microphone"` config

**Step 2 — Pick the device**
- Dropdown of all enumerated input devices (from `list_audio_devices`)
- Live VU meter so they can confirm signal is reaching the device
- "Test with a song" button: prompts them to play any worship recording on their phone near the input; live transcript appears below
- If words appear → "Looks great! Save and continue"
- If not → in-line troubleshooting (levels, sample rate, device permissions)

**Step 3 — Confirm**
- Saves the audio profile (sticky across sessions)
- Returns to home screen

The wizard is shown automatically on first launch and from Settings → Audio anytime.

`src/components/settings/AudioSettings.tsx`:
- Re-runs the wizard
- Shows current device + test button
- Fall-back option: enable cloud ASR (Deepgram)

### 8. Pipeline orchestrator (ASR-only)

`sidecar/auto_presenter/pipeline.py`:
- `class Pipeline: def run() -> None`
- Wires `AudioCapture → ASRBackend → broadcaster`
- `broadcaster` emits `TranscriptPartialEvent` / `TranscriptFinalEvent` over the WS
- Used now for debug panel; extended in Phase 5+ to feed the matcher

### 9. Debug panel — Transcript tab

`src/components/debug/TranscriptTab.tsx`:
- Live stream of partial + final words with timestamps
- Color-codes: gray for partial, white for final
- Copy-to-clipboard button
- Scrolls automatically; click to pause auto-scroll
- Audio level meter visible at top
- Useful for: visual inspection of ASR quality, timing accuracy, hard words

### 10. CLI

`research/eval/run_asr.py`:
- `--input mic|file://path` `--backend whisper|deepgram` `--model medium|large-v3`
- Prints each `TranscriptEvent` to stdout
- Used by eval harness for batch experiments

## Files to create

| Path | Purpose |
|---|---|
| `sidecar/auto_presenter/audio/types.py` | AudioFrame |
| `sidecar/auto_presenter/audio/capture.py` | LiveCapture + FileCapture |
| `sidecar/auto_presenter/asr/types.py` | Word, TranscriptEvent |
| `sidecar/auto_presenter/asr/backend.py` | ASRBackend Protocol |
| `sidecar/auto_presenter/asr/whisper_stream.py` | faster-whisper + stable-ts |
| `sidecar/auto_presenter/asr/deepgram.py` | Stub cloud backend |
| `sidecar/auto_presenter/asr/vad.py` | Silero VAD wrapper |
| `sidecar/auto_presenter/asr/model_manager.py` | Detect HW + manage downloads |
| `sidecar/auto_presenter/pipeline.py` | ASR-only pipeline |
| `src/components/onboarding/AudioSetupWizard.tsx` | First-launch wizard |
| `src/components/onboarding/ModelDownloadDialog.tsx` | Progress UI |
| `src/components/settings/AudioSettings.tsx` | Re-run wizard, manage devices |
| `src/components/debug/TranscriptTab.tsx` | Debug panel populator |
| `src-tauri/src/audio.rs` | `list_audio_devices` Tauri command |
| `research/eval/run_asr.py` | Streaming ASR CLI |
| `tests/sidecar/asr/fixtures/short_clip.wav` | ~5 s sung phrase |
| `tests/sidecar/asr/fixtures/short_clip.json` | Expected words + timestamps |
| `tests/sidecar/asr/test_whisper_stream.py` | WER + timestamp tests |
| `tests/sidecar/audio/test_capture.py` | Frame timing tests |

## Success criteria

- [ ] Audio setup wizard takes a non-technical user from "I have nothing" to "AI sees my words" in under 3 minutes — **DEFERRED to Phase 4 polish slice** (no wizard yet; Settings → Audio input picker + Debug panel Test Microphone is the entry point for power users)
- [x] Streams audio from file at correct real-time pace via `FileCapture`
- [x] Backend swap works: same downstream code consumes mock or Whisper (Slice 2B-2)
- [x] Debug panel transcript tab updates live during a session (Slice 2B-1)
- [x] Live recording captures audio + ground-truth events to disk (Slice 3) — produces the golden-benchmark data for Phase 5
- [ ] Streams audio from live capture without buffer underruns over 5+ minutes — code path shipped (`LiveCapture`); needs real desktop validation alongside golden recording
- [ ] WER < 25% on clean vocal-aux / < 35% on full mix — **needs golden benchmark recording** (Phase 5 prerequisite)
- [ ] Median latency from utterance to finalized transcript event < 2 s — **needs golden benchmark recording**
- [ ] Word-level timestamps within 100 ms of ground truth — **needs golden benchmark recording**
- [ ] Model download with progress — **DEFERRED to Phase 4 polish slice** (first load blocks behind "Starting…" state; acceptable for v1)
- [ ] 5-minute pipeline run: no memory growth — **needs real desktop validation**

Three measurement-based criteria above require golden-benchmark recordings. Per the phase-plan agreement, those land *at the start of Phase 5* alongside the naive matcher's baseline measurement. The recording infrastructure that captures them (Slice 3) is shipped here.

## Tests required before phase complete

**Unit:**
- `test_capture.py`: `FileCapture` emits frames with correct `t_start` progression; `LiveCapture` mockable
- `test_whisper_stream.py`: known sung clip → produces words; word list matches expected to within edit distance N; word timestamps within 100 ms of ground truth

**Performance:**
- Real-time-factor test: process a 60-second clip in < 60 s on target hardware (Apple Silicon laptop, no GPU)
- Memory test: 10-minute file replay, RSS growth < 100 MB

**Integration:**
- End-to-end mic capture → transcript appears in debug panel, run for 5 minutes, no crash
- Eval harness: replay every Phase 3 recording → metrics computed (WER stats logged for baseline)

**Manual:**
- Run audio setup wizard from scratch on a fresh install; document any friction points
- Listen to a Phase 3 recording while watching the live transcript in the debug panel; spot-check accuracy

## Risks

- **Singing accuracy below threshold for the matcher.** Mitigation: Whisper is the best available; if accuracy is bad, defer Phase 5 and jump to Phase 9 (vocal separation) early.
- **stable-ts latency.** Mitigation: tune to run on finalized segments only; partial events use raw faster-whisper word timings.
- **Sliding window flicker.** Mitigation: stabilization rule (N consecutive matches before promoting to final). Document the criterion clearly so the matcher in Phase 5+ can rely on it.
- **GPU vs CPU performance gap is large.** Mitigation: hardware probe + automatic model size selection; user can override.
- **Audio setup wizard fails for some users.** Mitigation: include a "send diagnostic" button that bundles `list_audio_devices` output, recent errors, hardware info into a zip for support tickets.
- **Wizard UX skipped by power users who break their config.** Mitigation: settings page can re-run the wizard anytime.

## Effort estimate

8–11 days. ASR core is ~1 week; the wizard polish + model download + multi-device UX is the rest.
