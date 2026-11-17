# Church Slides — Architecture & Approach

> _(Internal codename in the repo: "auto-presenter". Repo dir name: `Auto-Presenter-2`. User-facing product name: **Church Slides**.)_

## Context

A desktop application that presents church song slides during worship and uses AI to listen to the music and switch slides automatically — putting the right slide up *before* the singing of that slide begins, the way a skilled human operator would.

The AI slide-switching is the **core product**. The rest of the app (song editor, setlist builder, presentation mode) exists to feed the AI engine and let humans override gracefully. This document covers the entire app, with the AI engine as the centerpiece.

**Building from scratch** as a Tauri (Rust shell) + React (UI) + Python (AI sidecar) desktop app. No migration concerns from prior versions.

The core engineering problems for the AI engine, in order of importance:

1. **State estimation under noisy evidence**: given a streaming, error-prone transcript, infer the most-likely current `(song, slide, position-within-slide)`.
2. **Anticipation**: forecast when the current slide's last word ends, so we can advance ~300–700 ms before it.
3. **Deviation recovery**: detect when sung words stop matching the expected next slide, then re-localize across the song's other sections, the setlist, and (if needed) the whole library — without flickering between hypotheses on every transient ASR error.
4. **Graceful human override**: a manual click is an authoritative observation that should re-anchor the AI's belief state, not fight it.

The naive "take a 5-second transcript window, fuzzy-match it to all slides, pick the best" is reactive (always behind), unstable (jitters on ASR noise), and has no notion of structural priors. The right framing is **probabilistic state-space tracking**, not search.

---

## Top-level architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Tauri Desktop App                         │
│                                                              │
│   ┌────────────────────┐         ┌────────────────────┐     │
│   │  React UI (TS)     │◄───WS──►│  Python Sidecar    │     │
│   │  - Song editor     │         │  - Audio capture   │     │
│   │  - Setlist builder │         │  - ASR (Whisper)   │     │
│   │  - Presentation    │         │  - Particle filter │     │
│   │  - Debug panel     │         │  - LLM supervisor  │     │
│   └────────────────────┘         └────────────────────┘     │
│           │                                                  │
│           ▼                                                  │
│   ┌────────────────────┐                                     │
│   │  Tauri Rust core   │                                     │
│   │  - File I/O        │                                     │
│   │  - SQLite (FTS5)   │                                     │
│   │  - Multi-monitor   │                                     │
│   │  - Sidecar mgmt    │                                     │
│   └────────────────────┘                                     │
└─────────────────────────────────────────────────────────────┘
```

**Why this split:**
- **Tauri shell (Rust)** owns app lifecycle, window management, file system, SQLite, multi-monitor presentation, OS audio device enumeration. Single signed binary per OS. Tiny memory footprint vs Electron.
- **React UI (TypeScript)** owns all human-facing screens. Same code path whether running in Tauri or in dev (`npm run dev`). Component library: **shadcn/ui** (Radix + Tailwind) for clean modern dark-mode look. Icons: **Lucide**.
- **Python sidecar** owns ASR, particle filter, LLM oracle, anything ML. Bundled via PyInstaller as a binary the Tauri shell spawns at app launch (Tauri's first-class "sidecar binary" feature). All ML libraries (faster-whisper, stable-ts, demucs, sentence-transformers, anthropic) stay in their native ecosystem.
- Communication: **localhost WebSocket** between React and Python. Random port assigned at app launch. Strongly-typed Pydantic schemas mirrored as TypeScript types via codegen.
- Storage: **SQLite** in app data dir (`~/Library/Application Support/AutoPresenter/library.db` on macOS, equivalent on Win/Linux). FTS5 for content search. Periodic JSON export for backup.

**No auth in v1.** Single-machine desktop app. Auth re-enters scope only with cloud sync, paid licensing, or cloud telemetry upload.

---

## Feature scope summary

### Library and editing
- Songs are stored as a single text blob with line breaks delimiting slides.
- Section tags `[chorus]`, `[verse 1]`, `[bridge]`, `[tag]`, etc. on their own lines mark section boundaries; slides inherit the most recent tag.
- Title field separate from body.
- Optional metadata: CCLI number, author, copyright, default tempo (used as particle-filter prior).
- Library list with search by title and content (FTS5).
- ProPresenter 7 import (best-effort; behind feature flag — ship without if it slips).
- JSON export/import for backup.
- "Recent" and "popular" sort options on the library list. Popularity = count of setlists this song has appeared in (no explicit star/favorite UI).

### Setlist
- Setlists are ordered lists of items. Item types are abstract from day one — v1 ships only `SongSetlistItem`, but the schema accommodates `AnnouncementItem`, `ScriptureItem`, etc. without migration.
- Builder UI: typeahead search by title or content; click-to-add; reorder via drag.
- Recent setlists list on the home screen.
- Auto-save on every edit.

### Presentation mode
- Default: black background, bold white centered text.
- Keyboard navigation:
  - `←` / `→`: previous / next slide
  - `↑` / `↓`: previous / next song
  - `B`: blank black screen toggle
  - `W`: white screen toggle
  - `F11` / `Cmd+Ctrl+F`: fullscreen toggle
  - `Esc`: exit fullscreen
  - `Cmd/Ctrl+D`: toggle debug panel
- Bottom bar (auto-hide): shows current slide + next 2 slides on the left, current song + next 2 songs on the right. Reveals on song transitions and on mouse-near-bottom; fades after 2 s idle.
- Display picker: choose which monitor to project on. Single-screen v1 puts everything on the chosen monitor; designed as a state machine that supports a separate "operator view" on a second monitor in the future without rework.
- Auto-recover after crash: relaunching restores last setlist + slide position.

### Debug panel (developer / power-user, hidden by default)
- Triggered by `Cmd/Ctrl+D` in any mode.
- Live ASR transcript (partial + final).
- Top-3 belief-state hypotheses with marginal probabilities.
- Particle distribution sparkline.
- Live-editable tunable parameters: particle count, lead-time (ms), confidence thresholds, rate noise. Changes apply immediately.
- Audio level meter + chosen input device.
- This is the AI-tuning iteration loop. It earns its keep through Phases 5–8.

### AI engine
- Audio capture from configured input (line-in, mic, USB interface).
- Real-time streaming ASR (faster-whisper + stable-ts for word-level timestamps).
- Particle filter over `(song, slide, fractional_position, rate)` with phonetic-aware emission likelihood and structural transition priors.
- Lead-time scheduler advances slides ~500 ms before the predicted end of current slide.
- Deviation handling: confidence trigger + staged widening (within song → setlist → full library).
- LLM supervisor (Claude/GPT-4o, slow loop ~10 s) as a corrective oracle for ambiguous cases.
- Human override: any manual slide click instantly re-anchors the belief state.
- Full telemetry: every belief snapshot, decision, override, oracle verdict logged to JSONL with audio reference for offline replay.

---

## Long-list — technology survey with notes

### A. Audio capture

| Option | Strong on | Weak on |
|---|---|---|
| USB mic on the laptop | Zero setup | Picks up congregation, room reverb, AC noise — terrible signal for ASR |
| **Soundboard line-out → USB audio interface** | Cleanest possible signal; what FOH engineers already do for recording | Requires a cable run + interface; per-venue setup |
| Network audio (Dante/AES67) from digital mixer | Pristine, multi-channel (could grab vocal mic alone) | Niche; assumes a digital console |
| Tap an aux send dedicated to "vocals only" | Very clean — best possible input for ASR | Requires sound engineer cooperation each service |

The vocal-aux-send is the single biggest accuracy lever you have. Worth designing around.

### B. Vocal isolation (optional pre-ASR step)

| Option | Strong on | Weak on |
|---|---|---|
| **Demucs (htdemucs)** | State-of-art separation; isolates vocals from full mix | Compute-heavy; real-time only on GPU or with smaller variants |
| Spleeter (Deezer) | Lightweight, faster | Lower quality than Demucs |
| Open-Unmix | Open, decent | Outclassed by Demucs |
| MDX-Net family | Top of leaderboards | Heavier than Demucs for marginal gain |
| Real-time variants (Demucs-streamer, RTNeural) | Bounded latency | Quality drops vs offline |

Skipping separation is fine if vocal-aux-send is available. If not, real-time Demucs is a force multiplier for ASR accuracy on singing.

### C. Speech-to-text (the singing case)

Singing is harder than speech: held vowels, melisma, vibrato, music bleed, harmonies. Most ASR is speech-trained — Whisper happens to handle singing notably well because its training corpus included songs.

| Option | Strong on | Weak on |
|---|---|---|
| **Whisper (OpenAI, large-v3)** offline | Best-in-class accuracy on singing; multilingual; punctuation | Not natively streaming; full-file inference |
| **faster-whisper** (CTranslate2) | Whisper accuracy with 4× speedup; INT8 viable on CPU | Same streaming caveat — needs sliding-window wrapper |
| **whisper.cpp** | Runs on consumer hardware, including Apple Silicon Metal | Slightly behind faster-whisper on speed |
| **WhisperLive / whisper-streaming / RealtimeSTT** | Sliding-window wrappers that emit partial transcripts; ~1–3 s latency | Partial revisions can flicker the matcher if not handled |
| **WhisperX / stable-ts** | Word-level timestamps via forced alignment — exactly what we need for anticipation | Adds a second pass; integrate with care for streaming |
| **NVIDIA Parakeet / Canary (NeMo)** | Lowest latency self-host option (~150 ms); strong WER | GPU required; weaker on singing than Whisper |
| **Deepgram Nova-3 streaming** | ~300 ms latency; robust to noise/music; commercial reliability | Cloud cost; church internet may be flaky |
| **AssemblyAI Universal-Streaming** | Streaming, low latency, good word timings | Cloud, cost |
| **Google STT v2 streaming** | Mature, multilingual | Singing accuracy lower than Whisper |
| **OpenAI Realtime API / GPT-4o-transcribe** | Streaming, very low latency | Cloud, ongoing cost, less control |
| **Vosk** | Tiny, fully offline, no GPU | Notably lower accuracy on singing |
| **Coqui / DeepSpeech** | Open, embeddable | Outdated accuracy |
| **wav2vec2 / HuBERT custom** | Customizable | Engineering burden, no streaming out-of-box |
| **DALI/lyric-aligned research models** | Designed for sung lyrics | Research-grade, not production-streaming |

### D. Matching / state-estimation algorithms

| Option | Strong on | Weak on |
|---|---|---|
| Fuzzy substring match (your v1) | Trivial to implement | Reactive, jittery, no prediction, no priors |
| **Forced alignment (DTW) of ASR stream → expected lyrics** | Gives word-level position-within-slide, enables anticipation | Brittle when off-script; needs a fallback |
| **Hidden Markov Model** over (song, slide, sub-position) | Textbook fit; transition priors model song structure cleanly; Viterbi/forward-backward give state + uncertainty | State space large if naive; needs care to keep tractable |
| **Particle filter / sequential Monte Carlo** | Handles continuous sub-position and tempo cleanly; multi-hypothesis natural | More moving parts than HMM; tuning resampling |
| **Beam search over a slide-graph** | Simple to reason about; bounded compute | Loses probability semantics — harder to threshold |
| **CRF / sequence labeling** | Discriminative | Awkward fit; HMM is cleaner here |
| **Phonetic-aware edit distance** (Metaphone, Double Metaphone, Soundex) | Robust to ASR sound-alike errors ("hosanna" vs "hose Anna") | Loses information; combine with rather than replace word match |
| **Embedding similarity** (sentence-transformers per slide) | Semantic robustness | Loses sub-slide timing; can match wrong-but-related slide |
| **LLM-as-matcher** | Excellent on ambiguous deviations; can explain | High latency, cost, jitter, hallucination risk; bad as primary controller |
| **LLM-as-supervisor** (slow loop validating fast tracker) | Best of both — fast HMM-style tracker + occasional LLM correction | Adds plumbing and a second SLA |

### E. Anticipation / timing

| Option | Strong on | Weak on |
|---|---|---|
| **Word-level timestamps + lead-time advance** | Direct, easy to tune | Requires word-timestamped ASR (whisperX, stable-ts, or commercial) |
| Rolling tempo estimator (words/sec or syllables/sec) | Cheap; smooths noise | Indirect — better as input to the predictor than as primary signal |
| Per-section gap priors (chorus→verse typical gap) | Helpful for inter-slide silence handling | Need data; per-song variance is high |
| Voice Activity Detection (Silero VAD, WebRTC VAD) | Silence detection for instrumentals, song endings | Doesn't help mid-singing |
| Beat/tempo tracking (librosa, madmom) | Could trigger on bar boundaries | Heavy for marginal gain in v1 |

### F. Deviation detection

| Option | Strong on | Weak on |
|---|---|---|
| Confidence-drop trigger on tracker marginal | Direct readout from the model itself | Needs a probabilistic tracker (HMM/PF) — not available with pure DTW |
| Multi-hypothesis tracking (k-best particles/states) | Natural alternative-tracking | Tuning, debugging |
| **Background "search" thread** evaluating all slides in setlist when primary confidence drops | Cheap re-anchor when band repeats chorus / jumps | Need to stop primary from prematurely chasing noise |
| LLM tiebreak on ambiguous re-localization | Smart on hard cases (e.g. medley, improvised bridge) | Latency, cost |

### G. Music-side priors (V2 territory, but worth listing)

| Option | Strong on | Weak on |
|---|---|---|
| Audio fingerprinting (Chromaprint/AcoustID) | "Which song just started" from intro alone | Needs reference audio per song |
| Chroma/key/chord detection (Essentia, madmom) | Could detect song change; light compute | Indirect signal |
| Beat/downbeat tracking | Aligning slide changes to musical bar | Worth it for V2 polish |
| Onset detection | Detect first vocal entry → trigger first slide | Useful! Cheap. Could land in V1 |

### G1. Section-progression priors from harmony — *raised 2026-08-21*

The idea: chord-progression shape is a strong predictor of *where in the form*
a song is. A pre-chorus lift, a turnaround into a verse, the four-bar build
before a chorus — these are recognisable without knowing the key or the tempo,
and they arrive **before the words do**. That is exactly the signal
anticipation lacks today, because every current signal is lyric-derived and
therefore cannot fire until someone sings.

Two ways to obtain it, and the design should do both:

1. **Online, within a single play.** Track chroma → chord estimates as the
   song runs and learn its repeating structure live. Self-similarity over
   chroma is the standard tool and is cheap. Benefits the first play of a song
   nobody has recorded yet.
2. **Persisted per song, across plays.** Store sectioned fingerprints in the
   library DB so the second and subsequent plays start with a prior. Must be
   **key- and tempo-agnostic**: store relative-interval chord sequences
   (Roman-numeral / pitch-class-interval form, not absolute chords) and
   beat-relative durations (not seconds). A band that capos up or plays it
   slower must hit the same fingerprint.

Why this is likely worth real effort rather than polish: measurement in Phase 7
found the tracker spends **27-41% of a repeat-heavy song in lost mode**,
overwhelmingly because repeated sections are lyrically identical. Chorus
instance 1 and chorus instance 3 have the *same words* — lyrics genuinely
cannot separate them. Harmony and arrangement often can (different intro,
different dynamic, a bridge modulation, a drop). This attacks the one class of
ambiguity the current architecture is structurally blind to, rather than tuning
around it.

Prerequisite: needs Phase 8's evaluation data to size the win before building.
Dependency-wise, chroma is cheap (librosa/Essentia); the cost risk is running it
concurrently with ASR on the same core budget, so measure against
`Plan/PERF_BASELINE.json` before committing.

### G2. Non-vocal passages — gating the expensive path — *raised 2026-08-21*

**Status as measured on 2026-08-21: we do not do this at all.** The findings,
so the next reader starts from fact rather than assumption:

- `sidecar/auto_presenter/asr/vad.py` exists — 325 lines, `EnergyVAD` and a
  Silero wrapper — and has **zero importers and no test file**. It was built
  and never wired in. Treat it as unproven code, not as a component.
- Whisper's own VAD is deliberately off (`vad_filter=False` in
  `asr/whisper_backend.py`). That was a correct, evidence-backed decision:
  enabling it discarded 60 of 349 *sung* words. Singing is not speech-shaped,
  and speech VADs cut it. Any gating built here must be validated against sung
  audio, not speech.
- The matcher already short-circuits cheaply on a wordless event —
  `ParticleFilter.update` ticks the clock and returns a snapshot without
  running predict or the per-word emission loop. So the matcher is *not* where
  the waste is.
- The waste is the **ASR pass itself**, which runs every step unconditionally.
  It owns the 1000 ms budget the whole pipeline is scheduled around, so it is
  the only bypass worth having.

The stronger argument for doing this is correctness, not CPU. An instrumental
passage does not produce "no words" — Whisper hallucinates plausible lyrics
into silence, and those fabricated words are fed to the particle filter as
evidence indistinguishable from real singing. A bypass that never runs ASR on a
non-vocal passage removes a class of poisoned evidence, and the CPU saving is a
side effect. Sequence: measure how often it actually happens on the goldens
first — the instrumental fraction of a worship set is not yet quantified here,
and this should not be built on the strength of the story alone.

Note the interaction with G1: chroma-based section tracking wants to run
*during* exactly the instrumental passages ASR would be skipping, so the two
features share an audio front-end and should be designed together rather than
bolted on separately.

### H. Plumbing

| Option | Strong on | Weak on |
|---|---|---|
| **Tauri (Rust) shell + Python sidecar** | Single signed binary, native UX, full Python ML ecosystem | Two-language build pipeline |
| Pure Electron + Node | Single-language stack | ML ecosystem in JS is thinner; bigger memory footprint |
| Pure Tauri (Rust everywhere, ML via ONNX) | Minimal footprint | Rust ML ecosystem lags; iteration speed costly during AI tuning |
| Native desktop (SwiftUI/WPF) per OS | Best-in-class native UX | Triple build effort |
| Web app + cloud Python | Zero local install | Cloud cost; latency; offline operation impossible |

---

## Short-list — recommended architecture

Pick a **probabilistic tracker** as the brain, with an **LLM supervisor** as the slow safety net, in a **Tauri-shell desktop app** with a **Python sidecar** for the AI core.

### 1. Audio: vocal-aux-send → USB interface (preferred); USB mic + real-time vocal separation (fallback)

The bottleneck on every downstream component is signal quality. The single highest-leverage decision in this whole system is to source audio from the soundboard, not a room mic. Document this as a venue-setup requirement; if it can't be met, real-time Demucs becomes mandatory.

### 2. ASR: faster-whisper (large-v3) with sliding-window streaming, word-level timestamps via stable-ts

Reasoning:
- Whisper handles **sung lyrics** materially better than every other ASR option, which is the dominant accuracy lever.
- faster-whisper gives you real-time on a modern laptop CPU at small/medium sizes, GPU at large — practical to run locally in any church.
- stable-ts gives word-level timestamps from the same model — *required* for anticipation: you can't advance 500 ms before the last word of a slide if you don't know when each word landed.
- Local-first means no internet dependency on Sunday morning.
- Cloud alternative if you'd rather not ship a model: Deepgram Nova-3 has the best singing latency-quality tradeoff among hosted options. Treat as a swappable backend behind one interface.

Guardrails:
- Whisper streaming wrappers emit revisable partials. Treat partials as soft evidence; only commit to a state change when partials stabilize or a finalized segment confirms.
- Tune chunk length around 1 s and overlap around 0.3 s.

### 3. Matcher: particle filter over `(song, slide, fractional_position, rate)` — the core innovation

State per particle:
- `song_id`
- `slide_id` (within the song)
- `position`: continuous 0..1, fraction through the slide
- `rate`: words-per-second of the singer right now (slow-changing latent variable)

At each ASR token (with timestamp + confidence):
1. **Predict step**: advance every particle's `position` by `rate × Δt`. If `position > 1`, transition to the next slide per the song's structure with prior-weighted choices.
2. **Update step**: re-weight each particle by emission likelihood — how well does the just-arrived word match the expected word at that particle's `position`? Use a **phonetic-aware edit distance** (Double Metaphone) so that "hosanna" ≈ "hose Anna" doesn't crash the likelihood.
3. **Resample** when effective sample size drops.

Transition priors (precomputed from the setlist + library):
- Within a slide → next slide: highest probability.
- Within a song → repeat current section (chorus most likely, verse less): structural prior from song metadata.
- Within a song → jump to bridge, tag, ending: medium-low.
- Setlist → current song's start of next planned song: medium during inter-song gap, low otherwise.
- Setlist → other planned song (out-of-order): low.
- Library → any other song: very low — but non-zero so unplanned songs are recoverable.

Why this design:
- Naturally produces both **point estimate** (current slide = mode) and **uncertainty** (entropy of the particle weights) — the latter is your deviation trigger.
- The continuous `position` and `rate` give you direct anticipation: project forward to find when `position` will hit 1, and advance lead-time before that.
- Multi-hypothesis: when band starts an unexpected chorus repeat, particles split — you don't lose the alternate hypothesis just because the dominant one is "wrong."
- A single, tunable knob (lead-time) controls how aggressively the system anticipates.

If a particle filter feels too custom, an HMM with a discretized position grid (e.g. 8 sub-positions per slide) is a near-equivalent fallback that's easier to debug.

### 4. Anticipation: lead-time scheduler driven by tracker state

Maintain a continuously updated estimate of `t_end_of_slide = now + (1 - position) / rate`. When `t_end_of_slide - now < lead_time` (default 500 ms; per-church configurable), commit the slide advance. Treat the *commit* as a separate event from the *prediction* — once committed, don't undo.

Special cases:
- **Instrumental intro**: VAD says no vocals, tracker says we're between songs. Prior says "next song's slide 1 imminent." Show slide 1 of next song after a short timeout (e.g. 2 s of vocal silence + tempo onset detected).
- **Instrumental break / interlude mid-song**: VAD silence + tracker holds current slide unless we cross structural boundary; show next slide once vocals resume on its first words.
- **Song ending / cooldown**: VAD silence + tracker confidence collapses → hold last slide.

### 5. Deviation handling: confidence trigger + indexed re-localization

When effective sample size or top-state marginal probability drops below a threshold for more than ~1 s, widen the search. With a 1000+ song library, *how* you widen matters — see section 5b for the indexing that makes this sub-millisecond rather than seconds.

The widening is staged:
1. **Within the current song's sections** (cheap, ~10 slides) — handles the most common case (chorus repeat, jump to bridge).
2. **Across the setlist** (cheap, ~50–100 slides) — handles song reordering.
3. **Across the entire library** via the phonetic fingerprint index (sub-millisecond regardless of library size) — handles unplanned songs.

Once the widened distribution converges back to a peak, normal tracking resumes.

### 5b. Library-scale indexing: pre-computed phonetic fingerprints

**The cost analysis.** A 1000-song library has ~200,000 words. Steady-state tracking is fine — particles concentrate in the current song and you're only scoring emission likelihood against ~20–50 slides per ASR token. The library-scale cost only matters during re-localization. Without an index, that's a full scan over 200k words for every token while the tracker is uncertain — too slow. With the right index, it's a handful of hash lookups regardless of library size.

**The index (Shazam-style, but on phonetic word codes).** Pre-computed once when the library is built or modified:

For every song in the library:
1. Tokenize each slide into words.
2. Encode each word via Double Metaphone (gives you `(primary_code, secondary_code)` per word).
3. Generate **fingerprints** as pairs of nearby phonetic codes plus their offset:
   ```
   fingerprint = hash(metaphone(w_i), metaphone(w_{i+k}), k)   for k in 1..3
   ```
4. Insert into a hash table: `fingerprint → [(song_id, slide_id, slot_index), ...]`.

This is the same trick Shazam uses on spectrogram peak pairs, applied to phonetic word codes. Pair-based fingerprints are vastly more discriminating than single-word lookups — common words like "the" or "Lord" generate millions of single-word matches, but the *pair* "the-Lord-with-offset-2" is much rarer.

Auxiliary structures (also pre-computed):
- **Inverted index**: `metaphone_word → list of (song_id, slide_id, slot_index)` — for cheap single-word backup queries.
- **Per-slide bloom filter** of metaphone codes — sub-microsecond "could this slide possibly contain this word" prefilter.
- **Optional: HNSW embedding index** over per-slide sentence embeddings — semantic backup when the singer paraphrases or improvises a line. FAISS or hnswlib.

**Re-localization query.** When the tracker triggers a widened search:
1. Take the last ~10 s of stabilized ASR words from the transcript window.
2. Phonetically encode them.
3. Generate the same fingerprint pairs.
4. Hash-lookup each → accumulate votes in a `(song_id, slide_id) → count` map, optionally weighted by ASR confidence and recency.
5. Top-K candidates by vote count become the seed for new particles injected into the filter.

This is sub-millisecond on commodity hardware for any reasonable library size, and *naturally tolerates ASR errors* — a few wrong words just don't contribute votes; the rest still spike the right answer.

**Steady-state remains cheap.** During normal tracking, you don't query the global index at all — particles already know which slides to score against. The index is dormant overhead (RAM only) until the tracker says "I'm lost." Memory cost for 1000 songs is on the order of 50–200 MB, easily kept in RAM.

### 6. LLM supervisor (slow loop, ~5–10 s cadence)

Independently of the fast tracker, run an LLM call every ~10 seconds with:
- The last ~10 s of transcript
- The fast tracker's current top-3 hypotheses with marginal probabilities
- The full setlist + (compressed) library index
- The recent override history

Ask: "Is the tracker on the right slide? If not, which slide?" Use Claude Sonnet (or Haiku for cost) with structured output. The supervisor's job is **not** to control slides directly — it's to inject a corrective re-anchoring observation back into the tracker if it's confidently wrong. This handles the genuinely hard cases (medley, key change, leader breaking into an unrelated worship chorus) where local matching can't recover.

### 7. Human override: authoritative observation, not a fight

When the human clicks slide X (or presses arrow keys):
- Inject a delta-spike observation into the tracker: collapse particle weights to particles consistent with `(current_song = song_of(X), slide = X, position = 0)`.
- Briefly *raise* the lead-time and the deviation-trigger threshold for the next ~5 s — give the human's choice the benefit of the doubt before second-guessing it.
- Log every override with the surrounding transcript and the AI's belief state at the moment. This is **gold** training/eval data.
- Show a small status badge: "AI handed off to you" / "AI is following you" / "AI has caught up." Makes the collaboration legible.

Critical principle: the AI should never feel like it's wrestling the human. One click = one full re-anchor.

### 8. Plumbing details (Tauri shell + Python sidecar)

- **Tauri's sidecar binary feature** is the recommended pattern: declare the PyInstaller-frozen Python executable in `tauri.conf.json` `bundle.externalBin`. Tauri spawns it on app launch and kills it on app close.
- **Localhost WebSocket** between React and Python on a random port chosen at launch. Python reports its port back to Tauri via stdout; Tauri forwards to React via Tauri events.
- **Pydantic schemas → TypeScript types** via `pydantic-to-typescript` or similar; commit generated types to repo.
- **Single Tauri command** `start_session(setlist_id)` opens the WS, loads the library + setlist into the Python side, and starts audio capture.
- **Models download on first run**, cached in app data dir. Whisper model size selected based on detected hardware (MPS / CUDA / CPU).
- **Code signing**: Apple Developer ($99/yr) for macOS notarization; Windows code-signing cert (~$300–500/yr) to avoid SmartScreen warnings; Linux unsigned AppImage. Set up before public release.

---

## Implementation phasing

| Phase | Scope | Why this order |
|---|---|---|
| **0. Foundation** | Tauri + React + Python sidecar scaffold; data model; SQLite + FTS5; sidecar spawn + WS handshake | Everything builds on this |
| **1. Song library** | Text-blob editor with `[section]` tags; library list with FTS5 search; ProPresenter import (feature-flagged); JSON export/import | Need songs to test anything |
| **2. Setlist + presentation + debug panel scaffold** | Setlist builder; popular/recent sort; manual presentation mode (fullscreen, B/W, arrow keys, auto-hide bar); display picker; debug panel hidden by default | Get to a usable manual app; debug panel ready to receive AI signals later |
| **3. Eval harness** | Recording, ground-truth annotation, replay engine, metrics, A/B comparator | Required before any AI work |
| **4. ASR baseline + audio setup wizard** | faster-whisper + stable-ts streaming; audio config wizard; debug panel shows live transcript | Validates audio path + ASR quality |
| **5. Naive matcher (AI takes control)** | Sliding-window fuzzy matcher; AI integration with presentation state machine; override flow | Establishes baseline AI numbers |
| **6. Particle filter v1** | Phonetic encoding; particle filter; lead-time anticipation; debug panel shows belief state | The big leap to 90% of cases |
| **7. Deviation handling** | Confidence trigger + within-song + setlist widening | Chorus repeats, song reorders |
| **8. Production: library-wide recovery + LLM supervisor + telemetry + installer** | Phonetic fingerprint index; LLM oracle; full telemetry; installer with code signing; auto-updater | Ship-ready |
| **9. Optional: vocal separation (Demucs)** | Real-time Demucs when input is full mix | Skip if Phase 8 metrics already meet bar |
| **10. Optional: V2 musical priors** | Onset detection, chord/key, audio fingerprinting | Skip if Phase 8 metrics already meet bar |

Each phase has its own detailed implementation plan in `PHASE_<n>.md` alongside this file.

---

## Verification approach

You can't tell if the AI is working without an evaluation harness, built in Phase 3. Targets:

- **Anticipation error** (ms): median |error| < 500 ms after Phase 6, predominantly on the early side.
- **Wrong-slide rate**: < 5% after Phase 6; < 2% after Phase 8.
- **Override rate**: < 1 per song after Phase 8.
- **Recovery time** after deviation: < 4 s after Phase 7.
- **A/B harness**: every phase gated on strict improvement vs the prior phase on the eval set.
- **Live shadow mode**: run the AI in production but only log decisions, don't actually advance. Compare to operator's actual clicks. Lowest-risk way to measure on real services.

---

## Deferred / out of scope for v1

Captured here so they don't get lost:

- Login / cloud auth (re-enters scope with cloud sync or paid licensing)
- Cloud library sync between devices
- Logo / holding slides between songs
- Bible verse display
- Setlist export/print for the band
- Announcements / scripture / non-song setlist items (data model accommodates them; UI is song-only in v1)
- Chord display alongside lyrics (ChordPro support)
- Operator view on second monitor (architecture supports it; UI lands later)
- Service-playback recording surfaced as a feature (telemetry already records audio under the hood)
- Per-leader / per-church model adaptation
- Multi-language support (v1 is English-only)
