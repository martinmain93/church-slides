# Phase 8 — Production: library-wide recovery, LLM supervisor, telemetry, installer

## Goal

Close the final 5% gap to push the system to and past human-operator parity, **and** ship a production-quality installer. Three independent capabilities land here: the phonetic fingerprint index for full-library recovery, the LLM supervisor as a slow safety net, and the override-re-anchor + telemetry pipeline. Plus the install / update / signing pipeline that makes the app distributable.

## Prerequisites

- Phase 7 complete (within-song + across-setlist deviation handling working)

## Scope

**In:**
- Phonetic fingerprint index (Shazam-style pair-fingerprints over Double Metaphone codes)
- Inverted index + per-slide bloom filter
- Optional HNSW embedding index for semantic backup
- Stage-3 re-localization (full library)
- LLM supervisor (async loop, structured output, corrective injection)
- Human override → authoritative re-anchor (collapse particles)
- Full telemetry pipeline (JSONL events + audio refs, persistent across sessions)
- **PyInstaller bundling of the Python sidecar + Tauri release-mode sidecar spawn** — inherited from Phase 0 deferral
- **`pnpm tauri build` produces a runnable binary that boots end-to-end without `uv` on PATH** — inherited from Phase 0 deferral
- **Cross-platform manual install verification (macOS, Windows, Linux)** — inherited from Phase 0 deferral
- **Automated force-quit orphan-process test** (CI matrix on macOS + Windows) — inherited from Phase 0 deferral
- Code signing: Apple notarization (macOS) and Windows code-signing
- Installer pipeline: Tauri bundler producing signed installers per OS
- Auto-updater: Tauri's built-in updater with signed deltas

**Out:**
- Vocal separation (Phase 9)
- Musical priors (Phase 10)
- Multi-language support
- Per-leader / per-church model adaptation

## Implementation steps

### 1. Fingerprint generation

`sidecar/auto_presenter/index/phonetic.py` (extend):
- `def fingerprints_from_words(metaphones: list[tuple[str,str]]) -> Iterator[tuple[int, int]]`
- For each `i`, for `k in 1..3`: yield `(hash((m_i.primary, m_{i+k}.primary, k)), slot_i)`
- Use `xxhash.xxh64` (fast, low-collision)

### 2. Fingerprint index

`sidecar/auto_presenter/index/fingerprint_index.py`:
```python
@dataclass
class Posting:
    song_id: str
    slide_id: str
    slot: int

class FingerprintIndex:
    def build(library: IndexedLibrary) -> None:
        """O(library_word_count) one-time."""
    def query(words: list[str], confidences: list[float]) -> list[tuple[Candidate, float]]:
        """Top-K (song, slide) candidates by vote count, weighted by ASR confidence."""
    def save(path: Path) -> None: ...
    def load(path: Path) -> None: ...
    def update_song(song: Song) -> None:
        """Incremental rebuild for a single song."""
```
- Internal: `dict[fingerprint_hash, list[Posting]]`
- Voting: each fingerprint match adds `confidence_weight × recency_weight` to `(song_id, slide_id)`
- Top-K extraction via heap
- Persisted to disk under app data dir; rebuilt incrementally on library mutation

### 3. Auxiliary indices

Same module:
- Inverted single-word index: `dict[metaphone_primary, list[Posting]]`
- Per-slide bloom filter: 1 KB `pybloom-live` filter per slide

`sidecar/auto_presenter/index/embedding_index.py` (optional):
- `sentence-transformers` (`all-MiniLM-L6-v2`) per-slide embedding
- `hnswlib` index, cosine
- Off by default; enable via `enable_semantic_backup` flag

### 4. Stage-3 re-localization

`sidecar/auto_presenter/tracker/relocalize.py` (extend Phase 7):
- Stage 3: when stages 1 + 2 fail, query fingerprint index with last 10 s of stable transcript words
- Top-5 candidates → inject 30% of particles, weighted by vote count
- If embedding index enabled and fingerprint distribution is flat (top-1/top-5 < 1.5×), additionally query embedding index and merge

### 5. LLM supervisor

`sidecar/auto_presenter/supervisor/llm_oracle.py`:
```python
class LLMOracle:
    async def run(belief_stream: AsyncIterator[BeliefState], transcript: TranscriptBuffer) -> AsyncIterator[OracleVerdict]:
        """Every 7-10s, evaluate tracker state and emit verdict."""
```
- `OracleVerdict { kind: "confirm" | "redirect" | "uncertain", target?: SlideRef, reasoning: str, confidence: float }`
- Anthropic SDK (default) with Claude Sonnet for cost/latency balance
- **Strictly async** — never blocks the tracker loop; verdicts older than 15 s are discarded
- On `redirect`: dispatch an `OracleObservation` event to the tracker, which collapses particles to the redirect target with **50% weight** (less aggressive than human override — reversible by next strong ASR evidence)

`sidecar/auto_presenter/supervisor/prompts.py`:
- System prompt: domain context (church worship, song structure)
- Per-call user prompt: last 10 s transcript with timestamps, top-3 tracker hypotheses with marginals, full setlist (titles + first lines), recent override history (last 3)
- Output schema enforced via Anthropic tool-use
- Use prompt caching on the static parts (setlist, library summary) — cost target < $0.05 per 90-minute service

### 6. Human override (full version)

`sidecar/auto_presenter/decision/override.py`:
- `def apply_override(filter: ParticleFilter, item_idx: int, slide_idx: int) -> None`
- Collapse all particle weights onto particles consistent with `(song_of(slide), slide_idx, position=0.0, rate=mode_rate)`
- Boost lead-time + lost-mode threshold for 5 seconds (give human's choice slack)
- Log to telemetry: transcript ±10 s, belief state at moment of override, the override itself
- Emit `ai_state="following_human"` for 5 s, then back to `tracking`

### 7. Telemetry

`sidecar/auto_presenter/service/telemetry.py`:
- Structured JSONL log per session: `~/AppData/AutoPresenter/sessions/<session_id>/events.jsonl`
- Event types:
  - `transcript_partial` (down-sampled to 2 Hz)
  - `transcript_final`
  - `belief_state` (every 500 ms: top-3 + ess + stage)
  - `decision` (every commit)
  - `override`
  - `oracle_verdict`
  - `lost_mode_enter` / `lost_mode_exit`
  - `audio_recording_started` / `audio_recording_stopped`
- Audio recorded alongside events at session level (basically every live session generates a Phase 3-style recording for free)
- Gzip on session close

`src/components/sessions/SessionsList.tsx`:
- Settings → Sessions tab
- List of past sessions with stats summary
- "Replay through pipeline" → re-runs against current code, shows metric diff
- "Export for support" → bundles session into a zip

### 8. Code signing + installer pipeline

**macOS:**
- Apple Developer account ($99/yr)
- Developer ID Application certificate
- Tauri config: `bundle.macOS.signingIdentity = "Developer ID Application: ..."`
- `xcrun notarytool` for notarization
- `xcrun stapler` to staple ticket
- CI step (GitHub Actions) handles signing on a macOS runner; secrets in repo secrets

**Windows:**
- EV or OV code-signing cert (~$300–500/yr; OV is sufficient with reputation seasoning)
- `signtool.exe` after Tauri builds the .msi
- Winget submission (post-launch) for friction-free install

**Linux:**
- Unsigned AppImage (Linux norms differ; users accept this)
- Optional: `.deb` and `.rpm` for distro repos

### 9. Auto-updater

`src-tauri/tauri.conf.json`:
- `bundle.updater = { active: true, endpoints: ["https://updates.autopresenter.com/{{target}}-{{arch}}/{{current_version}}"] }`
- Updates server hosts signed update manifests + delta packages
- `tauri-plugin-updater` installed; UI prompts on app launch when update available
- Background download; restart prompt; "skip this version" option

Set up a minimal updates server (S3 + CloudFront with signed manifest JSON suffices for v1).

### 10. Final WebSocket protocol

`sidecar/auto_presenter/protocol.py` (final):
- Sidecar → UI: `slide_change`, `belief_state`, `transcript_partial`, `ai_status`, `oracle_verdict_announced`, `lost_mode_change`
- UI → Sidecar: `human_override`, `session_start`, `session_stop`, `library_update`, `tunable_param_changed`

### 11. End-to-end live shadow → active rollout

- Default `ai_mode` to `shadow` for the v1 release
- After 2 weeks of live shadow data showing override rate < 1/song, flip default to `active`
- Provide an `ai_mode = off` for users who want pure manual

## Files to create

| Path | Purpose |
|---|---|
| `sidecar/auto_presenter/index/phonetic.py` (extend) | `fingerprints_from_words` |
| `sidecar/auto_presenter/index/fingerprint_index.py` | Build, query, persist |
| `sidecar/auto_presenter/index/embedding_index.py` | HNSW backup |
| `sidecar/auto_presenter/tracker/relocalize.py` (extend) | Stage 3 |
| `sidecar/auto_presenter/supervisor/llm_oracle.py` | Async oracle loop |
| `sidecar/auto_presenter/supervisor/prompts.py` | Prompt templates |
| `sidecar/auto_presenter/supervisor/types.py` | OracleVerdict |
| `sidecar/auto_presenter/decision/override.py` | Full override re-anchor |
| `sidecar/auto_presenter/service/telemetry.py` | JSONL logger |
| `src/components/sessions/SessionsList.tsx` | Session history UI |
| `src/components/sessions/SessionDetail.tsx` | Drill-in |
| `src/components/sessions/ExportButton.tsx` | Bundle for support |
| `src/components/presentation/AIStatusPill.tsx` (extend) | All states final |
| `.github/workflows/release.yml` | Sign + notarize + bundle + publish |
| `infra/updates/manifest.json.template` | Update server template |
| `tests/sidecar/index/test_fingerprint_index.py` | Build + query correctness |
| `tests/sidecar/index/test_fingerprint_perf.py` | 1000-song latency benchmark |
| `tests/sidecar/supervisor/test_llm_oracle.py` | Mocked LLM responses |
| `tests/sidecar/decision/test_override.py` | Re-anchor behavior |
| `tests/integration/test_unplanned_song.py` | Library-wide recovery |
| `tests/integration/test_override.py` | Click → re-anchor |
| `tests/integration/test_full_eval.py` | All metrics, all recordings, final targets |

## Success criteria

- [ ] **Wrong-slide rate** < 2% on full eval set (final target)
- [ ] **Override rate** < 1 per song on real services (live shadow data over 2 weeks)
- [ ] **Recovery from unplanned song**: median < 6 s once vocals start
- [ ] **LLM supervisor false-redirect rate** < 5% (verified against ground truth)
- [ ] **Fingerprint index**: build for 1000-song library < 30 s; p99 query latency < 2 ms
- [ ] **Index memory**: < 200 MB for 1000 songs
- [ ] **LLM cost**: < $0.05 per 90-minute service (with prompt caching)
- [ ] **Telemetry**: 100% of events logged with audio timestamps
- [ ] **Override behavior**: human click → AI follows immediately → resumes tracking smoothly within 5 s
- [ ] **Signed installers** for macOS and Windows; both pass OS warnings on a clean machine
- [ ] **Auto-updater**: dev → release → beta-tester gets update prompt within 24 hours
- [ ] **Session export** zip is < 10 MB and reproducibly replays through the pipeline
- [ ] **`pnpm tauri build` produces a runnable binary** with the PyInstaller-frozen sidecar embedded; app boots end-to-end on a machine with no `uv`/Python installed *(inherited from Phase 0)*
- [ ] **Force-quit no-orphan test** runs in CI on macOS + Windows: kill the app abruptly, assert no `auto-presenter-sidecar` process remains *(inherited from Phase 0)*
- [ ] **Manual cross-platform install** on macOS, Windows, Linux completes hello-world flow *(inherited from Phase 0)*
- [ ] **Multi-display picker** in Start-Service flow: enumerate monitors, let the operator pick one, position the presentation window on it *(inherited from Phase 2)*

## Tests required before phase complete

**Unit:**
- `test_fingerprint_index.py`: known library + transcript window → expected top candidates; collision rate < 5% on synthetic library
- `test_fingerprint_perf.py`: synthesize 1000-song library; build < 30 s; query p99 < 2 ms over 10000 random queries
- `test_llm_oracle.py`: mock the Anthropic client; for each `OracleVerdict` kind, assert correct downstream injection
- `test_override.py`: synthetic belief state + override → particles collapse correctly; lead-time + threshold raised for 5 s
- `test_telemetry.py`: every event type roundtrips through JSONL serializer

**Integration:**
- `test_unplanned_song.py`: library has song X not in setlist; recording starts singing X; AI re-anchors to X within 6 s
- `test_full_eval.py`: full eval set, all four target metrics met; A/B vs Phase 7 shows improvement on hard cases

**Live shadow:**
- Run the AI in shadow mode on at least 4 real services. Compare logged AI decisions to operator's actual clicks. Override rate < 1/song must hold.

**Performance:**
- Fingerprint query < 2 ms with 1000-song library
- LLM call latency p99 < 3 s
- Total session memory growth over 90 minutes < 200 MB

**Distribution:**
- Fresh macOS install: download installer, run, app launches, no Gatekeeper warnings
- Fresh Windows install: download installer, run, app launches, no SmartScreen blocking
- Linux: AppImage launches; reasonable user-prompt about executable bit

**Regression:**
- Full eval-set snapshot regression vs Phase 7

## Risks

- **LLM latency variability** can desync with the tracker. Mitigation: oracle is strictly async with bounded staleness — verdicts older than 15 s discarded.
- **LLM hallucinated redirects** could send AI to wrong slides confidently. Mitigation: oracle redirects collapse particles less aggressively (50% weight) than human overrides; reversible by next strong ASR evidence; human can always override.
- **Index memory blow-up at very large libraries.** Mitigation: posting-list compression (delta-encoded slot indices); bloom-filter prefilter; consider on-disk index for libraries > 5000 songs.
- **Telemetry log size**. Mitigation: rotating files per session; transcript_partial down-sampled; gzip on session close.
- **LLM cost surprise.** Mitigation: hard rate limit (max 1 call per 7 s); prompt caching on stable context; cost telemetry per session with daily cap.
- **Code-signing certs are time-bombs.** Mitigation: monitor cert expiry; renew 60 days before expiry; document in runbook.
- **Auto-updater bricking installs** on a bad release. Mitigation: staged rollout (10% → 50% → 100%); ability to roll back via signed manifest revision; skip-version option for users.

## Effort estimate

15–22 days. Roughly: 5–7 days fingerprint index + library-wide recovery, 4–5 days LLM oracle, 2 days override + telemetry polish, 4–8 days code signing + installer + auto-updater (this is a real chunk of work, often underestimated).
