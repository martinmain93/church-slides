# Phase 0 — Foundation: Tauri shell + Python sidecar + data model + storage

## Goal

Stand up the cross-language skeleton that everything else builds on: a Tauri desktop app that bundles and spawns a Python sidecar, talks to it over a localhost WebSocket, and persists data to SQLite. By the end of this phase you can run the app, see "hello from Python", and read/write a fixture song to/from disk.

No AI work, no real UI screens, no audio. Just plumbing — but solid plumbing.

## Prerequisites

- Decide team toolchain: Rust (stable), Node 20+, Python 3.11+, `uv` for Python, `pnpm` or `npm` for JS.
- macOS or Linux dev box (Windows works but Tauri toolchain setup is rougher).

## Scope

**In:**
- Tauri 2.x project with React + TypeScript + Vite
- shadcn/ui + Tailwind set up for dark mode
- Python sidecar project (`uv` + Pydantic + FastAPI/websockets)
- PyInstaller bundling proven (sidecar runs from a frozen binary)
- Tauri sidecar binary integration: app spawns Python at launch, kills on close
- Localhost WebSocket protocol with one round-trip message (`ping` → `pong`)
- Pydantic ↔ TypeScript type generation pipeline
- Core data model: Song, Slide, Section, Setlist, SetlistItem (abstract base + concrete `SongSetlistItem`)
- SQLite + FTS5 schema and migration runner
- App data directory layout
- Build/dev workflow: hot-reload React, auto-restart sidecar on Python file change in dev

**Out:**
- Any real UI screens (Phase 1 onward)
- Audio capture / ASR (Phase 4)
- Library editor / setlist builder (Phase 1, 2)
- Presentation mode (Phase 2)
- ProPresenter import (Phase 1)
- Code signing / installer pipeline (Phase 8)

## Implementation steps

### 1. Project layout

```
auto-presenter/
├── src-tauri/                    # Rust + Tauri shell
│   ├── src/
│   │   ├── main.rs
│   │   ├── sidecar.rs            # spawn + lifetime mgmt
│   │   ├── db.rs                 # SQLite migrations + queries
│   │   └── commands.rs           # Tauri commands exposed to JS
│   ├── tauri.conf.json
│   └── Cargo.toml
├── src/                          # React + TypeScript UI
│   ├── App.tsx
│   ├── lib/
│   │   ├── ws.ts                 # WebSocket client
│   │   └── tauri.ts              # Tauri command wrappers
│   └── types/
│       └── generated.ts          # codegen from Pydantic
├── sidecar/                      # Python AI/ASR engine
│   ├── pyproject.toml
│   ├── auto_presenter/
│   │   ├── __init__.py
│   │   ├── main.py               # entrypoint: parse args, start WS server
│   │   ├── server.py             # WebSocket handler
│   │   ├── protocol.py           # Pydantic message schemas
│   │   ├── library/
│   │   │   ├── types.py          # Song, Slide, Section, Setlist
│   │   │   └── parser.py         # text-blob → Song (used in Phase 1)
│   │   └── config.py
│   └── tests/
├── shared/
│   └── codegen.py                # Pydantic → TS types
├── data/
│   └── fixtures/                 # sample songs for testing
├── Plan/                         # this directory
└── package.json
```

### 2. Tauri shell

`src-tauri/Cargo.toml`:
- Tauri 2.x
- `tauri-plugin-shell` (for sidecar)
- `tauri-plugin-fs`
- `rusqlite` with `bundled` + `fts5` features

`src-tauri/tauri.conf.json`:
- `bundle.externalBin: ["sidecar/auto-presenter-sidecar"]`
- Allowlist: `shell.sidecar`, `fs.read/write` to app data dir only

`src-tauri/src/main.rs`:
- Initialize SQLite (run migrations)
- Spawn sidecar (via `tauri_plugin_shell::Sidecar`)
- Sidecar prints assigned WS port to stdout; capture and forward to UI via Tauri event `sidecar-ready { port }`
- Register Tauri commands: `db_query`, `db_execute`, `get_app_data_dir`, `get_sidecar_port`
- Graceful shutdown: kill sidecar on app exit

### 3. Python sidecar

`sidecar/pyproject.toml`:
- `python >= 3.11`
- `websockets`, `pydantic`, `aiosqlite` (read-only access to same DB; writes go through Tauri)
- Dev: `pyinstaller`, `pytest`, `ruff`, `pyright`

`sidecar/auto_presenter/main.py`:
- Parse `--db-path`, `--port` (or `--port=0` for random)
- Bind WS server, print actual port to stdout, then keep serving
- On WS connect: handle `ping`, respond `pong { python_version, sidecar_pid }`

`sidecar/auto_presenter/server.py`:
- Single WS handler dispatches messages by `kind` field
- Pydantic-validated incoming + outgoing
- Heartbeat every 5 s

### 4. Data model (the canonical types)

`sidecar/auto_presenter/library/types.py`:

```python
from typing import Literal
from pydantic import BaseModel, Field
from datetime import datetime

SectionType = Literal[
    "verse", "chorus", "bridge", "tag",
    "intro", "outro", "pre-chorus", "instrumental", "other"
]

class Section(BaseModel):
    type: SectionType
    number: int = 1                   # verse 1 vs verse 2

class Slide(BaseModel):
    id: str                            # ULID
    song_id: str
    section: Section
    order_in_song: int                 # global slide order within song
    order_in_section: int
    lyrics: str                        # raw text shown
    words: list[str]                   # tokenized for matcher

class Song(BaseModel):
    id: str
    title: str
    body_text: str                     # the canonical text-blob form
    slides: list[Slide]                # derived from body_text on parse
    section_order: list[Section]       # ordered list, derived
    ccli: str | None = None
    author: str | None = None
    copyright: str | None = None
    default_tempo_wps: float | None = None     # words per second; particle-filter prior
    created_at: datetime
    updated_at: datetime

class SetlistItem(BaseModel):
    """Abstract base — concrete subclasses tagged via `kind`."""
    id: str
    kind: str
    order: int

class SongSetlistItem(SetlistItem):
    kind: Literal["song"] = "song"
    song_id: str

class Setlist(BaseModel):
    id: str
    name: str
    items: list[SongSetlistItem]       # v1: only songs; schema accommodates extension
    created_at: datetime
    last_used_at: datetime | None = None
```

### 5. SQLite schema

`src-tauri/migrations/001_init.sql`:

```sql
CREATE TABLE songs (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  body_text TEXT NOT NULL,
  ccli TEXT, author TEXT, copyright TEXT,
  default_tempo_wps REAL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE VIRTUAL TABLE songs_fts USING fts5(
  title, body_text, content='songs', content_rowid='rowid',
  tokenize='porter unicode61'
);

CREATE TRIGGER songs_ai AFTER INSERT ON songs BEGIN
  INSERT INTO songs_fts(rowid, title, body_text) VALUES (new.rowid, new.title, new.body_text);
END;
-- + au, ad triggers for update/delete

CREATE TABLE setlists (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  last_used_at INTEGER
);

CREATE TABLE setlist_items (
  id TEXT PRIMARY KEY,
  setlist_id TEXT NOT NULL REFERENCES setlists(id) ON DELETE CASCADE,
  kind TEXT NOT NULL DEFAULT 'song',
  song_id TEXT REFERENCES songs(id) ON DELETE RESTRICT,
  position INTEGER NOT NULL
);
CREATE INDEX setlist_items_setlist ON setlist_items(setlist_id, position);
```

Slides are NOT a separate table — they're derived from `songs.body_text` at read time. Single source of truth for the lyrics.

### 6. PyInstaller bundling

`sidecar/build.sh`:
- `uv run pyinstaller --onefile --name auto-presenter-sidecar auto_presenter/main.py`
- Targets the platform-specific output Tauri expects (e.g. `auto-presenter-sidecar-aarch64-apple-darwin`)
- Output goes to `src-tauri/binaries/` per Tauri sidecar conventions
- Must be re-runnable on every dev `tauri dev` cycle (or use `--watch` mode)

For dev mode, allow direct `python -m auto_presenter` instead of frozen binary — speeds up iteration. Tauri config picks one based on `cargo` profile.

### 7. Type codegen

`shared/codegen.py`:
- Read every Pydantic model from `auto_presenter.protocol` + `auto_presenter.library.types`
- Emit `src/types/generated.ts` with TypeScript interfaces and Zod schemas
- Run via `uv run codegen` script in `pyproject.toml`; commit output

### 8. WebSocket protocol stubs

`sidecar/auto_presenter/protocol.py`:
- Discriminated union `ServerMessage = PingMessage | PongMessage | ErrorMessage`
- All messages have `kind` field for discrimination
- Stub messages for future use: `SessionStart`, `SessionStop`, `SlideChange`, `BeliefState`, `TranscriptPartial`, `Override`. Define them now (empty bodies if needed) so generated TS types exist; populate fields in later phases.

### 9. Hello-world UI

`src/App.tsx`:
- Connect to WebSocket using port from `sidecar-ready` event
- Send `ping`, render the `pong` reply
- Render an empty list of songs from a `db_query` Tauri command
- Insert one fixture song via `db_execute`
- Re-render the list

This proves: Tauri works, sidecar works, WS round-trip works, SQLite works, codegen types match. That's the entire phase deliverable.

### 10. Dev/build workflow

- `pnpm dev` runs Vite dev server
- `pnpm tauri dev` runs the desktop app pointing at the dev server, with Python sidecar running directly (not bundled) for hot reload
- `pnpm tauri build` produces a signed (in Phase 8) production binary with PyInstaller-frozen sidecar

## Files to create

| Path | Purpose |
|---|---|
| `package.json`, `pnpm-lock.yaml` | JS deps |
| `src-tauri/Cargo.toml`, `src-tauri/tauri.conf.json` | Tauri config |
| `src-tauri/src/main.rs`, `sidecar.rs`, `db.rs`, `commands.rs` | Rust shell |
| `src-tauri/migrations/001_init.sql` | SQLite schema |
| `src/App.tsx`, `src/main.tsx`, `src/lib/ws.ts`, `src/lib/tauri.ts` | Hello-world UI |
| `src/types/generated.ts` | Codegen output (committed) |
| `src/styles/globals.css` | Tailwind base |
| `tailwind.config.ts`, `components.json` | shadcn/ui config |
| `sidecar/pyproject.toml`, `sidecar/uv.lock` | Python deps |
| `sidecar/auto_presenter/main.py` | Sidecar entrypoint |
| `sidecar/auto_presenter/server.py` | WS server |
| `sidecar/auto_presenter/protocol.py` | All Pydantic messages (stubs for future) |
| `sidecar/auto_presenter/library/types.py` | Song, Slide, Section, Setlist |
| `sidecar/auto_presenter/config.py` | App data path resolution |
| `sidecar/build.sh` | PyInstaller wrapper |
| `shared/codegen.py` | Pydantic → TS |
| `data/fixtures/song_amazing_grace.json` | Fixture for hello-world test |
| `tests/sidecar/test_protocol.py` | Schema roundtrip |
| `tests/sidecar/test_main.py` | Sidecar starts, accepts ping |
| `src-tauri/tests/test_db.rs` | SQLite migration + insert + query |
| `src/__tests__/App.test.tsx` | UI renders pong |
| `README.md` | Setup instructions |

## Success criteria

- [x] `pnpm tauri dev` opens a window showing "pong from Python (3.11.x, pid 1234)" and a one-row song list
- [ ] `pnpm tauri build` produces a runnable binary that does the same thing — **DEFERRED to Phase 8** (PyInstaller bundling + sidecar release-mode spawn live there alongside code signing)
- [x] SQLite migrations run idempotently on launch; FTS5 index populated by triggers
- [x] Sidecar dies cleanly when the app closes (no orphan processes verified by `ps`) — implemented via Tauri sidecar `kill_on_drop`; cross-platform automated verification deferred to Phase 8
- [x] WebSocket reconnects automatically if sidecar restarts in dev
- [x] Pydantic → TS codegen produces types that match by hand-comparison; CI step fails if codegen is stale
- [x] All defined Pydantic message types are present (even if stubbed) so future phases just fill in fields

## Tests required before phase complete

**Unit:**
- `test_protocol.py`: every message type roundtrips through JSON serialization (parametrized)
- `test_main.py`: spawn the sidecar in a subprocess, connect WS, send ping, assert pong shape
- `test_db.rs`: migration runs cleanly, insert + select roundtrips a song, FTS5 trigger populates the virtual table
- `App.test.tsx`: render with a mocked WS, verify pong text appears

**Integration:**
- End-to-end: `pnpm tauri dev` boots, send ping over real WS, assert reply within 1 s

**Manual:**
- ~~Build + install on macOS, Windows, Linux (each on its target). App launches, hello-world works.~~ **DEFERRED to Phase 8** (depends on PyInstaller bundling + signed installers).
- ~~Force-quit the app; verify no orphan Python process~~ **DEFERRED to Phase 8**. Behavior is implemented (`kill_on_drop(true)` + `tauri-plugin-shell` lifecycle), but the cross-platform automated test lives with the rest of the install/distribution work.

**Deferrals out of Phase 0 (recorded for traceability):**
- `pnpm tauri build` production binary path (PyInstaller + release-mode sidecar spawn) → **Phase 8**
- `components.json` / shadcn-ui scaffold → **Phase 1** (where the first real forms/dialogs land)
- Cross-platform manual install verification → **Phase 8**
- Force-quit orphan-process automated test → **Phase 8**

## Risks

- **Tauri sidecar feature evolves between versions.** Mitigation: pin Tauri version; document in README.
- **PyInstaller produces large binaries (~150–200 MB even before models).** Mitigation: investigate alternatives (PyOxidizer, shiv) only if size is a real problem in Phase 8.
- **Pydantic-to-TS codegen drift.** Mitigation: CI check that committed `generated.ts` matches a freshly run codegen (bit-identical).
- **SQLite + FTS5 + Rust toolchain interactions on Windows.** Mitigation: `rusqlite` `bundled` feature ships its own SQLite; verify in CI on a clean Windows runner.
- **WebSocket port conflicts.** Mitigation: bind to `127.0.0.1:0` and let the kernel pick a free port; sidecar prints actual port to stdout.

## Effort estimate

5–8 days. Most of the time is in build-tooling and the cross-language plumbing, not feature code.
