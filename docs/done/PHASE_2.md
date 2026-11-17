# Phase 2 — Setlist + presentation mode + debug panel scaffold

## Goal

A usable manual presenter app: build a setlist by searching the library, then drive the presentation manually with arrow keys. Black slides with bold white centered text. Auto-hide bottom bar showing context. Debug panel (hidden by default) ready to receive AI signals in later phases. By the end of this phase, you could in principle use this app for a service — manually — and the AI engine slots in cleanly later because the presentation state machine is the AI's interface.

**Phase 1 carry-overs to verify here:**
- Popularity sort produces meaningful ordering once setlists exist. Add a Phase 1 → Phase 2 regression test that creates setlists referencing songs A/B/C and asserts the library list's "Popular" sort ranks them correctly.

## Prerequisites

- Phase 0 (foundation)
- Phase 1 (songs in the library)

## Scope

**In:**
- Setlist builder UI: search-and-add flow, drag-to-reorder, recent setlists list
- Setlist data: SQLite-backed via `setlists` and `setlist_items` tables (Phase 0 schema)
- Manual presentation mode: fullscreen, black bg, white centered text
- Keyboard navigation: ←/→ slides, ↑/↓ songs, B blank, W white, F11 fullscreen, Esc exit
- Auto-hide bottom bar: current + 2 next slides, current + 2 next songs
- Display picker (multi-monitor support, single-screen v1)
- Crash-recovery: relaunching restores last setlist + last slide
- Debug panel scaffold: hidden by default, `Cmd/Ctrl+D` toggles, panels for "Transcript", "Belief state", "Tunable params" — empty placeholders that future phases fill
- Presentation state machine designed as the future AI's API

**Out:**
- Audio capture + ASR (Phase 4)
- AI matcher (Phase 5+)
- Operator second-monitor view (architecture supports it; UI lands later)
- Logo / holding slides between songs

## Implementation steps

### 1. Setlist data layer

`sidecar/auto_presenter/library/setlist.py`:
- `def create_setlist(name) -> Setlist`
- `def add_item(setlist_id, song_id, position=None)`
- `def reorder(setlist_id, item_ids: list[str])` (atomic)
- `def remove(setlist_id, item_id)`
- `def get_setlist(setlist_id) -> Setlist`
- `def list_setlists(limit=20, sort="recent") -> list[Setlist]`

DB writes go through Tauri (Rust owns the writer); Python only reads. Setlist position field updated via single SQL transaction.

### 2. Setlist builder UI

`src/components/setlist/SetlistBuilder.tsx`:

Layout:
- Left pane: setlist (drag-to-reorder list of song chips with title + remove button)
- Right pane: library search (typeahead, same FTS5 backend as Phase 1) — clicking a result adds it to the setlist
- Top: setlist name (editable), "Start service" button (CTA), "..." menu (rename, duplicate, delete, export)

Behavior:
- Adding a song appends to end (configurable insert position with shift+click → insert above selected)
- Drag-and-drop reorder via `dnd-kit`
- Keyboard:
  - `/` focus search
  - Arrow keys in search results
  - `Enter` adds top result
  - `Cmd/Ctrl+Enter` starts service immediately
- Auto-save on every change

`src/components/setlist/SetlistRow.tsx`: drag handle, title, "remove" icon-button.

`src/components/setlist/SearchPane.tsx`: typeahead with results showing title + first-line preview + section count badge.

### 3. Recent + home screen

`src/components/home/HomeScreen.tsx`:
- "Continue last service" big card (if a recent unfinished session exists)
- "Recent setlists" — last 5
- "+ New setlist" CTA
- Library count + library shortcut

This is the default landing when the app opens.

### 4. Presentation state machine

This is the most important architectural piece in Phase 2 because it's the AI's eventual interface.

**Phase 2 implementation note (decision logged 2026-05-06):** The plan
originally located the controller in the Python sidecar. We're shipping
it in TypeScript instead, because the Phase 2 sidecar doesn't yet do
anything with presentation state, so a WS round-trip per keypress is
pure latency. When Phase 5's AI lands, the AI dispatches *actions*
(advance/jump-to-slide) and reads the broadcast state — it doesn't need
to own it. The Python re-implementation, if we choose to do one in
Phase 5, is straightforward.

For reference, the original Python-side sketch was:

`sidecar/auto_presenter/presentation/state.py`:

```python
@dataclass(frozen=True)
class PresentationState:
    setlist_id: str
    item_idx: int                   # which setlist item
    slide_idx: int                  # which slide within current item's song
    blank_mode: Literal["none","black","white"] = "none"

class PresentationController:
    """Pure state machine. UI subscribes to state changes; commands mutate."""
    def next_slide(self) -> None: ...
    def prev_slide(self) -> None: ...
    def next_song(self) -> None: ...
    def prev_song(self) -> None: ...
    def jump_to(self, item_idx: int, slide_idx: int) -> None: ...
    def toggle_blank(self, kind: Literal["black","white"]) -> None: ...

    @property
    def current_slide(self) -> Slide | None: ...
    @property
    def upcoming_slides(self, n: int = 2) -> list[Slide]: ...
    @property
    def upcoming_songs(self, n: int = 2) -> list[Song]: ...
```

The AI in Phase 5+ will call `jump_to(item_idx, slide_idx)` exactly the same way the human does — no special API. State changes broadcast over the WebSocket as `presentation_state_changed` events; UI re-renders.

The crucial implication: **the AI integration in Phase 5 is a state-machine-call, not a UI hack.** The UI doesn't know whether a state change came from a key press or the AI; it just renders the new state.

For Phase 2 we ship the same shape in TypeScript at `src/lib/presentation/state.ts` with a `usePresentationController()` hook. Pure functions for the state transitions (cleanly unit-testable), React state for the rendering. The AI in Phase 5 will dispatch actions through the same surface.

### 5. Presentation view

`src/components/presentation/PresentationView.tsx`:
- Full-screen black background (`bg-black`)
- Current slide rendered as bold white text, centered, large (auto-fit to viewport with min/max bounds)
- Font: Inter or Geist, weight 700, no decorations
- White-screen mode: white bg + invisible text
- Black-blank mode: black bg + invisible text (acts as "blackout")
- Smooth crossfade on slide change (200 ms; configurable)

`src/lib/keyboard.ts`:
- Centralized keymap so Settings can show + customize
- Default bindings: ←/→ slides, ↑/↓ songs, B blank-black, W blank-white, F11 fullscreen, Esc exit-fullscreen, Cmd/Ctrl+D debug

### 6. Auto-hide bottom bar

`src/components/presentation/BottomBar.tsx`:
- Two columns:
  - Left ("Slides"): mini-card for current slide (highlighted) + next 2 slides
  - Right ("Songs"): mini-card for current song (highlighted) + next 2 songs
- Show triggers:
  - On any song-change event (auto-show for 3 s)
  - On mouse-near-bottom (within 80 px of viewport bottom)
  - On any keypress, briefly
- Hide trigger: 2 s of no triggers
- Animation: slide up from below with 200 ms ease-out

### 7. Display picker

`src-tauri/src/display.rs`:
- Tauri command `list_displays()` returns `[{id, name, is_primary, bounds}]`
- Tauri command `move_window_to_display(window_label, display_id)` moves the presentation window

`src/components/presentation/StartServiceDialog.tsx`:
- Before entering presentation: which display? (defaults to non-primary if ≥2 displays)
- "Start" button transitions to fullscreen on chosen display
- Stored in setlist's last-used config so subsequent starts skip this dialog

Dual-screen extension (later): add a second window for "operator view"; this just opens another window targeting the primary display while presentation stays on secondary. The state machine and broadcast protocol are unchanged.

### 8. Crash recovery

`sidecar/auto_presenter/presentation/persistence.py`:
- On every state change, write `{setlist_id, item_idx, slide_idx, t}` to `~/AppData/AutoPresenter/last_session.json`
- On app launch, if `last_session.json` exists and `t` is < 12 hours old, show "Continue last service?" dialog on home screen

### 9. Debug panel scaffold

`src/components/debug/DebugPanel.tsx`:
- Floating panel, draggable, default bottom-right of presentation view
- Hidden by default; toggle with `Cmd/Ctrl+D`
- Tabs:
  - "Transcript" — empty placeholder ("ASR not active") for now
  - "Belief state" — empty placeholder
  - "Tunables" — empty placeholder ("No AI engine running")
  - "Audio" — empty placeholder
- Persists tab selection across sessions
- Future phases populate each tab with real data

The point: ship the panel container, keybinding, layout, and tab switcher in Phase 2 so Phases 4–6 just plug content into existing slots.

### 10. Sidecar messaging

`sidecar/auto_presenter/protocol.py` (extending Phase 0 stubs):
- `SessionStart { setlist_id, display_id }`
- `SessionStop {}`
- `PresentationStateChanged { state: PresentationState }`
- `OverrideEvent { kind: "next_slide" | "prev_slide" | "next_song" | "prev_song" | "jump", item_idx?, slide_idx? }`

The UI sends `OverrideEvent` for every keypress; the sidecar's `PresentationController` updates state and broadcasts `PresentationStateChanged` back. Yes, this is a round-trip for every arrow-key press — but it's localhost WebSocket, sub-millisecond, and means the AI engine in Phase 5 has identical semantics for its slide commands.

Optimization for v1: optimistic local update on UI-side, reconciled by server's broadcast. If they diverge (rare, only during AI override conflicts in later phases), server wins.

## Files to create

| Path | Purpose |
|---|---|
| `sidecar/auto_presenter/library/setlist.py` | Setlist CRUD |
| `sidecar/auto_presenter/presentation/state.py` | PresentationState + Controller |
| `sidecar/auto_presenter/presentation/persistence.py` | last_session.json |
| `sidecar/auto_presenter/protocol.py` (extend) | Session + override messages |
| `src/components/home/HomeScreen.tsx` | Landing |
| `src/components/setlist/SetlistBuilder.tsx` | Setlist editor |
| `src/components/setlist/SearchPane.tsx`, `SetlistRow.tsx` | Builder pieces |
| `src/components/setlist/StartServiceDialog.tsx` | Pre-flight display picker |
| `src/components/presentation/PresentationView.tsx` | Slide renderer |
| `src/components/presentation/BottomBar.tsx` | Auto-hide context bar |
| `src/components/debug/DebugPanel.tsx` | Scaffold + tabs |
| `src/lib/keyboard.ts` | Keymap |
| `src/lib/presentationClient.ts` | WS wrapper for presentation events |
| `src-tauri/src/display.rs` | Multi-monitor enumeration |
| `tests/sidecar/test_presentation_state.py` | State transitions are correct |
| `tests/sidecar/test_setlist.py` | CRUD + reorder atomicity |
| `src/__tests__/PresentationView.test.tsx` | Slide rendering |
| `src/__tests__/BottomBar.test.tsx` | Show/hide triggers |
| `src/__tests__/SetlistBuilder.test.tsx` | Search-and-add flow |

## Success criteria

- [x] Build a 5-song setlist in under 30 seconds (timed)
- [x] Start service → window goes full-screen on selected display, first slide shown
- [x] All keyboard bindings work as documented
- [x] Bottom bar shows on song transition, auto-hides after 2 s, shows on mouse-near-bottom
- [x] Blank-black and blank-white toggle on B and W; toggling again returns to current slide
- [x] Force-quit during a service → relaunch → "Continue last service?" appears, click → resume on correct slide *(persistence + Home card shipped; verified manually by leaving a record in localStorage and reloading)*
- [x] Debug panel toggles cleanly; all four tabs render their (empty) placeholders
- [x] No flicker or jank on slide changes (60 fps animation)
- [ ] Multi-display picker correctly enumerates monitors; "Start" puts window on chosen one — **DEFERRED to Phase 8**: enumeration + cross-screen positioning needs the Tauri runtime monitor API (`@tauri-apps/api/webviewWindow` + `availableMonitors`). Single-display flow ships now (window stays on the screen it's on); multi-display picker dialog lands alongside the production-build work in Phase 8 where we already have a Tauri runtime in front of us.
- [x] Setlist builder search returns correct songs (FTS5 from Phase 1)

## Tests required before phase complete

**Unit:**
- `test_presentation_state.py`: synthetic command sequences → expected state at each step (next/prev slide at song boundaries; song-level navigation; blank toggles)
- `test_setlist.py`: reorder is atomic (no intermediate inconsistent state); concurrent reorder + add doesn't lose items
- `BottomBar.test.tsx`: jest fake-timers verify show/hide windows
- `PresentationView.test.tsx`: long lyric → text scales; blank modes render correctly

**Integration:**
- Build setlist + start service + arrow-key through entire setlist → end state matches expected slide
- Force-quit mid-service + relaunch → state restored
- Cross-display: start on display 2 → ESC → start again → window correctly returns to display 2

**Manual:**
- Use the app to "present" a real song you sing along to. Does it feel right? Are key bindings discoverable?
- Try with a 30-song setlist; reorder via drag — smooth?
- Multi-monitor setup: verify presentation goes to correct screen and stays there

**Performance:**
- Slide change latency end-to-end (key press → pixel) < 50 ms
- Setlist builder typeahead < 100 ms for 5000-song library

## Risks

- **WebSocket round-trip on every keypress.** Mitigation: optimistic local state in UI; reconcile on server broadcast. Latency on localhost is sub-millisecond — basically not measurable — but optimism keeps the perceived path even faster.
- **Multi-monitor edge cases** (display unplugged mid-service, display config changes). Mitigation: fall back to primary display gracefully; surface a toast.
- **Auto-hide bar feels jumpy.** Mitigation: dwell timers + hysteresis (1 s show + 2 s hide); manual on/off in settings if needed.
- **Crash recovery dialog appears spuriously** if user just closes normally. Mitigation: clear `last_session.json` on clean exit; only resurface dialog on truly abnormal close.
- **Drag-to-reorder libraries are surprisingly fiddly to get right.** Mitigation: use `dnd-kit` (battle-tested); test with keyboard-only nav too for accessibility.

## Effort estimate

8–12 days. The presentation state machine + multi-monitor + crash recovery + bottom bar polish is more work than it sounds.
