# Architecture

How Church Slides is put together, as it runs today. For why it ended up this
way, follow the links into [done/](done/) and [research/](research/).

## The pieces

```
┌──────────────────────── the church's computer ────────────────────────┐
│                                                                        │
│  Interface (React, src/)          App shell (Rust, Tauri 2, src-tauri/)│
│  operator, audience and    ◄────► windows and screens, SQLite library, │
│  band windows                     audio devices, band-screen server,   │
│        │                          setlist sharing, MCP server, updater │
│        │ local WebSocket                    │ starts and watches       │
│        ▼                                    ▼                          │
│  AI engine (Python, sidecar/)  ◄────────────┘                          │
│  microphone → speech recognition → follower → slide decisions          │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
        │ LAN                                   │ internet (optional)
        ▼                                       ▼
  band and leader screens               online band relay (relay/), in the
  (phones, tablets)                     church's own Cloudflare account
```

| Part | Folder | What it owns |
| --- | --- | --- |
| App shell | `src-tauri/` | Windows and displays (`screens.rs`), the SQLite library and its migrations (`db.rs`, `migrations/`), starting and restarting the engine (`sidecar.rs`), band screens on the LAN (`band.rs`), operator-to-operator sharing over Bonjour (`peer_sync.rs`), imports from other presentation apps (`other_apps.rs`), the MCP server for AI assistants (`mcp/`), and crash reports (`telemetry.rs`). |
| Interface | `src/` | Every screen: Home, Library and song editor, the setlist workspace (used both before and during a service), Settings, the debug panel. Talks to the shell through Tauri commands (`src/lib/tauri.ts`) and to the engine over a WebSocket (`src/lib/sidecarClient.ts`). |
| AI engine | `sidecar/` | Listening and deciding. Optional: with the AI off, or the engine missing, the app presents normally. |
| Online relay | `relay/` | A Cloudflare Worker and Durable Object that pass slide snapshots to band devices when the church's Wi-Fi keeps them apart. Each church deploys it to its own account. |
| Usage stats | `stats/` | A small Worker that receives opt-in usage counts from official builds. |
| Website | `site/` | The download page, changelog and privacy pages (Netlify). |
| Shared types | `shared/codegen.py` | Turns the engine's Pydantic models (`sidecar/auto_presenter/protocol.py`) into `src/types/generated.ts`. Run `pnpm codegen` after changing the protocol. |

## Rules the design keeps

- **The service never stops.** The operator window is the source of truth
  for what's on screen. The engine only suggests moves, and the shell
  doesn't depend on it, so if the engine crashes or restarts the slides and
  band screens carry on.
- **Everything runs locally.** Audio never leaves the computer unless the
  church turns on a hosted feature (spoken requests and voice charting can
  use Groq; see Settings → AI).
- **The library lives in the shell.** The Rust side owns `library.db`. The
  engine only reads it (for off-setlist songs), and the MCP server opens it
  directly, while the app notices changes and refreshes (`library_watch.rs`).
- **The engine socket is the app's.** It binds to 127.0.0.1 on a random
  port and accepts only the app's own window (origin check in
  `sidecar/auto_presenter/server.py`).

## The AI engine

```
microphone ─► capture ─► Whisper, sliding 6 s window ─► timed words
                │                                         │
                │                                         ▼
                ├──► next-line check (phones, ~1 s) ─► forward filter: where in
                │                                      which song (tracker/)
                └──► chords and beats (music/) ──────► score follower (follower/)
                                                          │
                                                          ▼
                     talk and silence gates (decision/singing.py, plan_gate.py)
                                                          │
                                                          ▼
                     lead-time scheduler: move just before the line starts
                                                          │
                                                          ▼
                                     decision ─► interface ─► screen
```

| Stage | Code | Notes |
| --- | --- | --- |
| Capture | `audio/`, `pipeline/audio_session.py` | One capture thread fans frames out to the stages below. |
| Speech | `asr/` | Whisper: MLX on Apple silicon, CTranslate2 (faster-whisper) elsewhere. A machine check (`asr/machine_check.py`) picks the model, or offers Assist only. |
| Next-line check | `follower/next_line.py` | Listens for the first sounds of each likely next line, so the slide can change about a second after the line starts rather than waiting ~3 s for Whisper's words. Apple silicon only. |
| Where are we | `tracker/forward.py` | A dense forward filter over every word position in the setlist, with repeats and jumps built into its transitions. Built by `decision/production.py`. |
| Score follower | `follower/` | Follows a song's melody and chords against its lead sheet, or against what it learned from the slides alone. |
| Gates | `decision/singing.py`, `decision/plan_gate.py`, `follower/talk.py` | Blank the screen during silence; stop talk between songs moving the slides. |
| Scheduler | `decision/scheduler.py` | Times each move to land just before the band reaches the line. |
| Off-setlist songs | `index/` | A phonetic fingerprint of the whole library, for a song that isn't in tonight's set. |
| Spoken requests | `decision/instructions.py` | "Go to the chorus", "bring up John 3:16". |
| Assist | `decision/assist.py` | The operator changes the slides; the AI highlights where it thinks the band is. |

Everything else in `sidecar/auto_presenter/` serves the library side:
importers and chord-sheet reading (`leadsheet/`, `chords/`), meaning search
(`library_search/`), scripture (`scripture/`), hymns (`hymns/`), and service
recordings for evaluation (`recording/`). Each `server_*.py` file wires one
of these to the WebSocket.

## Models

| Model | Used for | Where it comes from |
| --- | --- | --- |
| Whisper (base.en, small.en) | Speech | Downloaded on first use into the app's data folder |
| Next-line phone model | Next-line check | Bundled; trained by this project, MIT ([release](https://github.com/martinmain93/church-slides-releases/releases/tag/models-next-line-ngru512x2)) |
| BTC chord model | Chords and beats | Bundled (`scripts/setup_chord_model.py`) |
| e5-small-v2 | Library search by meaning | Bundled (`scripts/setup_search_model.py`) |

`scripts/setup_dev.sh --models` installs the bundled models in a source
checkout. Each model's licence is listed under Settings → Open-source
licences.

## Measuring the engine

Changes to the engine are judged on recorded services, never on feel:

- **Golden recordings**: real services with a human-checked timeline of
  slide changes (`research/eval/bootstrap_golden.py`, `annotate.py`).
- **Real-time gate**: `research/eval/run_follower_eval.py` plays recordings
  through the engine the app runs, at real speed.
- **Performance gate**: `scripts/perf_check.py` against
  `scripts/perf_baseline.json`, run by every release build.

[CONTRIBUTING.md](../CONTRIBUTING.md) lists the numbers a change has to
keep, and [research/README.md](../research/README.md) covers the tools.

## Releases

`scripts/release.sh` builds both Macs locally, then publishes to the public
[releases repo](https://github.com/martinmain93/church-slides-releases) that
the in-app updater reads. `release.sh --windows` adds the Windows installer
from a GitHub Actions run. Crash reports and usage stats are on only in
official builds: the addresses come from `scripts/release.local.env`, which
isn't in the repo, so a build from a fork sends nothing.
