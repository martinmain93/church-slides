# Performance audit — 2026-09-26

Scope: main app (operator view) and band screens; AI on vs off; many band
devices; long services / large setlists. CPU and memory over time, leaks, and
anything that scales worse than linearly. Branch `band-screens` (uncommitted
work as of this date).

## How it was measured

| What | How |
|---|---|
| Real app | `tauri dev` (development React, debug Rust), driven through the MCP bridge. Re-checked in production React with `tauri build --debug --no-bundle` (same Rust, production frontend). |
| Processes | Sampled every 2 s: Tauri app (Rust + band server), WebKit WebContent (the UI), Python sidecar (AI). CPU from cumulative CPU time; memory as RSS **and** `footprint` (phys_footprint — what Activity Monitor shows; RSS hid most of the growth because macOS compresses idle pages). |
| Long services | Two throwaway setlists: 6 songs (79 slide cards) and 60 songs (611 cards, 12.3k DOM nodes) with synthetic chord charts on every song. Slide changes driven by an in-page stepper (keydown → two frames = one step), 1,000–1,500 steps per run. |
| AI on/off | Golden recording `rec_20260818-213617_974` (5 min) injected into the 60-song session, once with AI off, once active. Sidecar's own `pipeline_health` (ASR ms, matcher ms, lag, drops) captured alongside. |
| Band screens | Node WebSocket clients (0/10/50/200 devices) during 200-step sweeps; the real band page in Chromium (JS heap + long-animation-frame script time). |
| Isolated | `mlx_whisper` memory over 300 passes (active vs cache, with/without a cache limit); matcher cost vs setlist size (1→120 songs); a simulated 2-hour service (24 laps of the recording into one engine, tracemalloc). |

Test data (setlists, synthetic charts) was removed afterwards. Recordings
created by the test sessions were left in place (see the end of this file).

## Findings, ranked

### 1. Every slide change re-renders every slide card in the operator view — HIGH, easy fix

`useRecordingSession` returns a fresh `{ notifySlideChange, updateSetlistContent }`
object each render (`src/lib/recordingClient.ts:178`), although both functions
are stable. `OperatorView.applyMutation` depends on `recording`, and
`onEditLyrics / onDuplicate / onDeleteSlide / onAddSlide / onReorder / onRemoveSong`
depend on `applyMutation`, so all six change identity on every render and
`memo(SongBlock)` never bails out.

Evidence (fiber props identity before/after one step): 73/73 `SlideThumb`
re-rendered (6 songs), 611/611 (60 songs); the six callbacks are exactly the
changed props.

| Setlist | Dev build p50 / p95 per step | Prod build p50 / p95 |
|---|---|---|
| 6 songs | 31 / 34 ms | — |
| 60 songs | 82 / 128 ms | 32 / 40 ms |

Cost is linear in total slides in the set, not in the current song. Each step
allocated 0.5–1.4 MB of garbage in the dev build (see #7).

Fix: `useMemo` the returned object. `SongBlock` also rebuilds `realSlides` and a
new `items` array for `SortableContext` per render; once the memo holds, that
only runs for the two songs whose `currentSlideIdx` changed.

### 2. With the AI on, every manual move renders the whole operator view twice — HIGH, easy fix

Each manual move produces one `ai_status` broadcast (39 for 40 moves — the
director reports `following_human`). `useAiStatus` stores a new object on
every message even when nothing changed (`src/lib/aiDecisions.ts`,
`setSnapshot({...})`), and `useLiveAiMode` pulls it into `OperatorView` via
`usePresentationSession`, so the full grid renders again.

| | AI off | AI on |
|---|---|---|
| Dev p50 per step (60 songs) | 82 ms | 137 ms |
| Prod p50 / p95 | 32 / 40 ms | 42 / 58 ms |
| Prod webview CPU while stepping | 30 % | 60 % |

Fix: bail out when fields are unchanged (`setSnapshot(prev => same ? prev : next)`),
and/or have `useLiveAiMode` subscribe to `mode` only. #1's fix makes the echo
render cheap anyway; this removes it.

### 3. Sidecar GPU memory grows without bound through a service — HIGH for long services

Nothing sets an MLX cache limit. MLX keeps freed Metal buffers for reuse, so the
cache grows while the model's real working set does not:

| Passes (1/s ≈ seconds of audio) | Active | Cache | Peak live |
|---|---|---|---|
| 1 | 462 MB | 756 MB | 1,171 MB |
| 100 | 462 MB | 947 MB | 1,171 MB |
| 200 | 462 MB | 1,027 MB | 1,171 MB |
| 300 | 462 MB | **2,165 MB** | 1,172 MB |

Live, the sidecar's footprint was 3.9–4.1 GB (3.4 GB of it Metal buffers)
after ~20 min of transcription; RSS reported ~0.9 GB throughout. A production
run with only ambient mic audio reached 1.7 GB in ~9 minutes.

Cost of capping (150 passes each, sequential — some thermal drift):
no limit 1,043 ms/pass (last block); 1 GB cap 1,083 ms (+4 %); 256 MB cap
1,134 ms (+9 %). Recommendation: `mx.set_cache_limit(1 GiB)` when the MLX
backend loads, and `mx.clear_cache()` when a session stops (free between services).

### 4. Band devices can end up with two live connections after waking — MEDIUM (correctness + load)

`useBandConnection.reconnectNow` closes a socket that is still connecting and
opens a new one; in a real browser the old socket's `close` event arrives later,
its `onclose` still runs `scheduleRetry()`, and a third socket replaces the
second without closing it. Reproduced with an async-close fake socket: one
wake (`visibilitychange` + `online` while reconnecting) → 4 sockets created,
**2 left live**. The orphan receives every broadcast (double state updates),
counts as an extra device on the operator's badge, and re-arms the shared
watchdog timer so the real socket's silence detection is lost. Each wake during
a reconnect can add another.

Fix: in `onclose`/`onmessage`, ignore events from any socket that is not
`socketRef.current`; detach handlers (`sock.onclose = null` etc.) before
closing a superseded socket.

### 5. "AI off" still runs the full ASR + matcher — MEDIUM (product decision)

With recording on (the default), every operator session transcribes the room
for the whole service; `ai_mode="off"` only stops the director from acting.
Same audio, 60 songs:

| | AI off | AI on |
|---|---|---|
| ASR pass p50 / p95 | 650 / 790 ms | 647 / 741 ms |
| Matcher p50 / p95 | 18 / 59 ms | 14 / 46 ms |
| Sidecar CPU (process) | 11.5 % | 10.0 % |
| Webview CPU | 14.6 % | 16.1 % |
| Dropped audio | 0 | 13 chunks (see #6) |

GPU time is not in these CPU figures. If "off" does not need live
transcripts, skipping ASR when off saves the whole pipeline and avoids #3; if
it does (shadow data, instant switch-on), skipping only the matcher is cheap.

### 6. Occasional multi-second ASR passes drop audio — LOW-MEDIUM, investigate

Across ~300 passes: p95 ≈ 790 ms, but single passes of 4.5 s and 6.4 s; the
6.4 s pass dropped 13 chunks (~1.3 s of audio). The sidecar was CPU-busy
throughout (not stalled), consistent with the decoder running long on
repetitive sung audio. One `footprint` sample of the sidecar coincided with
the start of that pass, so tooling interference isn't fully excluded. Worth
bounding decode length per pass.

### 7. Development builds balloon in WebKit (not a production leak) — INFO

In `tauri dev` the UI process reached **3.6 GB** after ~3,300 slide changes
and did not give it back while the page lived; reload → 110 MB. In
production React the identical workload stays bounded: 193 MB → 470–615 MB
during 1,500 steps → 243 MB after; a second 1,500-step run with AI + band
stayed within 233–317 MB. Chromium running the dev bundle kept a flat JS
heap floor (48.6 → 50.9 MB over 250 steps). So: no app-level reference leak;
dev React's allocation volume (driven by #1) outruns WebKit's collector.
Long `tauri dev` sessions will slow down and swap — reload them.

### 8. Band server re-serialises the full setlist for every connecting device — LOW

The setlist snapshot is 275 KB for 60 songs with charts; position updates are
116 bytes. The server keeps snapshots as a JSON tree and serialises per
client on connect (and on `Lagged`), and the broadcast channel clones the
string per receiver.

| Devices | App CPU while stepping (debug Rust) | Connect p50 (all at once) | Updates delivered |
|---|---|---|---|
| 0 | 1.2 % | — | — |
| 10 | 1.5 % | 41 ms | 100 % |
| 50 | 2.0 % | 287 ms | 100 % |
| 200 | 19.9 % (connect burst, 55 MB serialised) | 923 ms | 100 % |

Operator step latency was unchanged at every device count (82 ms dev). Fine for
realistic bands; if wanted, store pre-serialised `Arc<str>`/`Utf8Bytes`.

### 9. Band page: song changes cost 40–90 ms of script on a desktop — LOW (phones)

Per position update the band page is <1 ms of script; heap 19 → 59 → 16 MB
over 300 updates (no leak). Song changes spike to 38–88 ms: `ChordLyrics`
isn't memoised and every word is its own span. `alignSlides` is not the cost
(≤1.2 ms per song, 14 ms for all 60). On an old phone this could be
150–400 ms at a song change — worth checking on real hardware.

### 10. Smaller items

- **Whisper progress bars:** `verbose=False` *enables* tqdm in the whisper
  convention (`verbose=None` silences it) — one progress line per ASR pass
  (1,141 in ~20 min) into sidecar stderr.
- **Live matcher is ~6× its offline cost** (p50 18 vs 3 ms at 60 songs) — most
  likely GIL contention with the ASR thread. `matcher_ms` in the Perf tab
  includes that contention.
- **Minimap:** at 60 songs the song rows overlap each other (layout bug).
- **Dev tool:** `inject_audio` restarts the session with the original start
  request's `ai_mode`, silently turning the operator's AI switch back off.

## Checked and clean

- **Enter/exit leaks:** 10 operator enter/exit cycles — sidecar subscriptions
  5 → 0 every time, DOM back to 91 nodes, status listeners constant.
- **Frontend live stores** (transcript, levels, belief, fast channel, spoken
  instructions, music) are all capped.
- **Sidecar long-lived lists** are windowed or cleared per song; simulated
  2-hour service: traced Python memory flat at 55.17 MB from lap 1 to lap 23,
  per-update cost flat. One-time ~50 MB for the CMU pronouncing dictionary.
- **Matcher vs setlist size** is sublinear: p50 1.3 → 4.5 ms and p95 2.1 → 8.4 ms
  for 6 → 120 songs; index build 22 ms at 120 songs.
- **Band device counter** returns to 0 after disconnects; no server-side
  session leak.
- **Tauri IPC:** 3,000 `band_publish` calls cost ~5 KB each of webview memory.
- **Idle audio levels** (10/s) re-render only the waveform indicator.

## Fixes and retest (same day)

Fixed #1–#4, plus the progress-bar item from #10. #5 (AI off still transcribes)
is a product decision and is unchanged. Retested in the production-React build
on the same 60-song set with synthetic charts, same stepper, same recordings.

| Change | Where |
|---|---|
| `useRecordingSession` returns a memoised object | `src/lib/recordingClient.ts` |
| `useLiveAiMode` holds only the mode (a string), so repeated `ai_status` messages don't re-render; `useAiStatus` keeps its old snapshot when nothing changed | `src/lib/aiDecisions.ts` |
| MLX cache capped at 1 GiB before the first pass; released at the end of every session (in the session thread, so a file running out counts too — the first attempt in the server's stop handler missed that path) | `asr/mlx_backend.py`, `pipeline/audio_session.py` |
| `verbose=None` (no per-pass progress bar) | `asr/mlx_backend.py` |
| Band client ignores events from any socket that isn't current and detaches a superseded socket's handlers before closing it | `src/lib/band/useBandConnection.ts` |

Regression tests: recording-hook identity, `useLiveAiMode` render count,
band wake-while-reconnecting (async-close fake socket), MLX cap/release
(fake `mlx.core`), and session end releases the cache. Each new frontend test
fails without its fix. Full suites: 395 frontend + 1,058 sidecar passing.

| Measure (60 songs, production React) | Before | After |
|---|---|---|
| Cards redrawn per slide change | 671 of 671 | 7 (current song; 14 on a song change) |
| Step latency p50 / p95, AI off | 32 / 40 ms | 16 / 32 ms |
| Step latency p50 / p95, AI on + band + 20 devices | 42 / 58 ms | 16 / 33 ms |
| Cards redrawn by the AI status echo | 671 | 0 (4 per move, all from the move) |
| Webview CPU per step, AI off | ~37 ms | ~20 ms |
| Webview CPU per step, AI on + band | ~63 ms | ~26 ms |
| Webview footprint during 1,500 steps | 470–615 MB | ~195 MB |
| Sidecar GPU buffers after ~38 min of audio | 3.4 GB and rising | 1.49 GB, flat from minute 15 to 38 |
| Band device connections after 203 wakes across a server restart (real browser) | (2 after one wake, in the fake-socket repro) | exactly 1 |

Also verified: the band page served from the app's embedded production assets
(`http://<host>:7777/band`, previously untested) loads and renders chords.
Webview memory over 38 minutes of live AI traffic sawtooths between ~200 and
~600 MB and keeps returning to ~220 MB — collected, not leaked.

Cache release at session end, confirmed live: an operator session holding
1,479 MB of GPU buffers dropped to 464 MB (the model alone) on Exit; sidecar
footprint 1,973 → 958 MB.


## Follow-up: no transcription while the AI is off (#5)

Decision (user, same day): with the AI off nothing listens — no live
transcript, and **no spoken instructions either**.

- `AudioSession.set_transcribing()`: off, frames still reach the recorder,
  level meter and health reports but are never queued for ASR, so the ASR
  thread idles. Resuming needs nothing special: the window trims by audio
  time, so pre-pause audio is pushed out instead of stitched across the gap.
- `server_state.transcription_needed()` = no director (a transcript-only
  session, e.g. the Settings transcript test) or AI mode not "off".
  `sync_transcribing()` applies it at session start and on the AI switch.
  The spoken-instructions observer also ignores anything heard while the AI
  is off (a pass already in flight when it's switched off).
- On pausing, the ASR thread releases MLX's cached buffers once its last
  pass has finished. Two wrong versions were caught on the way: releasing in
  `set_transcribing` raced the pass in flight (1.49 → 1.23 GB, not 0.46), and
  releasing only when the thread next woke never happened in the usual case —
  between passes it is parked on the queue and, paused, no frame arrives
  (1.48 GB thirty seconds later). Pausing now posts a wake-up token.

Measured live (one-song set, injected golden audio):

| | Sidecar CPU | GPU buffers | Footprint |
|---|---|---|---|
| AI off (whole phase) | 0.6–0.9 % | 464 MB (model only) | ~740–790 MB |
| AI on | 9–29 % | ~1.43–1.49 GB (at the cap) | ~1.97 GB |
| AI switched off mid-song | 0.8–0.9 % within seconds | 1,436 → 464 MB | 794 MB |
| AI switched back on | 29 % | back to 1.43 GB | — |

Before this change the AI-off session cost ~11 % CPU plus GPU and grew GPU
memory like the AI-on one. The operator view starts with the AI off, so a
service run without the AI now does no transcription at all.

Tests: rule table (including "spoken instructions on, AI off → not
listening"), capture continues while ASR is paused, resume, release after the
in-flight pass, release from an idle-parked ASR thread (fails without the
wake-up), voice ignored with the AI off. 1,070 sidecar and 395 frontend tests
pass. One frontend run (of 12) failed once while the app was being killed
under load; the failing test wasn't captured and 11 subsequent runs passed.

Test sessions created 20 recordings (`rec_20260926-*`), left in place.
