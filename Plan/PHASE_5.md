# Phase 5 — Naive matcher (AI takes control)

## Goal

End-to-end working AI loop with a deliberately simple matcher. This establishes the **baseline numbers** that Phase 6 must beat. Critical: this phase exists to wire the AI into the presentation state machine and to land the override-flow plumbing — *not* to solve the matching problem. The naive matcher is supposed to be mediocre.

## Prerequisites

- Phase 0–4 complete

## Scope

**In:**
- Setlist-scoped indexing for matcher use (tokenize, normalize)
- Naive matcher: rolling 5-second transcript window → fuzzy match against current song's slides
- AI integration over the existing WebSocket protocol: matcher emits `SlideChangeMessage` events; the React `PresentationView` applies them via the existing `jumpTo()` transition in `src/lib/presentation/state.ts` — the same call path used by the keyboard handler
- Override flow: when the operator presses an arrow key during an AI-controlled session, the UI fires the existing `RecordSlideChangeNotification` (with `source="override"`) so the sidecar fans it out to the matcher and the recording sink; the matcher transitions to `following_human` and resumes tracking after a cooldown
- AI status pill in UI (idle / tracking / following human) — basic version
- Debug panel "Belief state" tab populated with naive matcher's per-slide score
- Baseline metric run on the eval set; numbers committed to `Plan/BASELINE_NUMBERS.md`

**Out:**
- Particle filter (Phase 6)
- Anticipation / lead-time (Phase 6)
- Deviation handling (Phase 7)
- Library-wide recovery (Phase 8)
- LLM supervisor (Phase 8)
- Full telemetry persistence (Phase 8)

## Implementation steps

### 1. Setlist-scoped indexing for matcher

**Setlist context flow (decision logged 2026-05-15):** the sidecar does not
read the SQLite library directly. Instead, the UI pushes the resolved setlist
content (all `Slide.words` for every item in the setlist) with the existing
`StartAudioSessionRequest` — this avoids a second code path over the database
and keeps the matcher trivially testable from synthetic fixtures.

`sidecar/auto_presenter/protocol.py` (extend):
- Add `setlist_content: SetlistContent | None` field to `StartAudioSessionRequest`
  where `SetlistContent` carries the per-item slide word lists the matcher needs.
- `SetlistContent { items: list[SetlistItemContent] }`
- `SetlistItemContent { item_idx: int, song_id: str, slides: list[list[str]] }`
  (each slide is its tokenized word list, matching `Slide.words` from the
  library types — already the canonical tokenization)

`sidecar/auto_presenter/index/library.py`:
- `def build_index(content: SetlistContent) -> IndexedSetlist` — preprocess
  the supplied setlist into per-item, per-slide tokenized lookup structures
  the matcher can scan over.
- No DB access, no caching across sessions: the index is built fresh from the
  WS payload each time a session starts and discarded on stop.

`sidecar/auto_presenter/index/types.py`:
- `IndexedSlide { item_idx: int, slide_idx: int, words: list[str] }`
- `IndexedItem { item_idx: int, song_id: str, slides: list[IndexedSlide] }`
- `IndexedSetlist { items: list[IndexedItem] }`

### 2. Naive matcher

`sidecar/auto_presenter/matcher/naive.py`:
- `class NaiveMatcher: def update(event: TranscriptEvent) -> MatcherDecision | None`
- Maintains rolling 5-second window of finalized words
- For each update: compute fuzzy score (`rapidfuzz.fuzz.partial_ratio`) of the window against each slide of *current song only*
- Best score above threshold → emit `MatcherDecision`
- Suppress duplicate same-slide decisions

`sidecar/auto_presenter/matcher/types.py`:
- `MatcherDecision { item_idx: int, slide_idx: int, t_seconds: float, confidence: float }`

### 3. AI ↔ presentation state integration

**Architectural note (decision logged 2026-05-06 in PHASE_2.md):** the
presentation state machine lives in TypeScript (`src/lib/presentation/state.ts`)
rather than the Python sidecar. There is no `PresentationController` on
the Python side. The AI integration therefore runs over the existing
WebSocket protocol rather than through a Python method call.

`sidecar/auto_presenter/decision/director.py`:
- Subscribes to `MatcherDecision` events from the matcher.
- When confidence > threshold, broadcast a `SlideChangeMessage` over WS
  with `source="ai"`, `item_idx`, `slide_idx`, and `confidence`.
- The React `PresentationView` consumes `slide_change` events from the
  sidecar and calls the existing `jumpTo(itemIdx, slideIdx)` transition
  from `state.ts`. The same function is already used by the keyboard
  handler — there is no new render path.

The TS state machine stays the single source of truth for what's on
screen. The director's job is to *propose* a new slide identity; the UI
applies the proposal via `jumpTo()`. If the AI is in shadow mode
(see step 8), the UI ignores the event and the director logs it without
broadcasting; if it's active, the UI applies it.

Wiring details:
- Extend `src/lib/transcriptStream.ts` (or add `src/lib/aiDecisions.ts`)
  with a hook that subscribes to `slide_change` broadcasts and exposes
  the latest AI suggestion to `PresentationView`.
- `PresentationView` re-uses the same `dispatch()` it already uses for
  arrow keys; the only new field is `source: "ai"` on the resulting
  log/telemetry entry.
- Override events from the user remain UI-originated and don't round-trip
  through the sidecar to mutate state (Phase 2 decision). The UI just
  posts a `RecordSlideChangeNotification` with `source="override"` so
  the recording sink captures it.

### 4. Override + AI status

`sidecar/auto_presenter/decision/director.py` (extended):
- Maintains `ai_state: Literal["idle", "tracking", "following_human"]`
- Subscribes to override events via the existing `RecordSlideChangeNotification`
  flow (decision logged 2026-05-15): the UI already fires this message with
  `source="manual"` or `source="override"` whenever the operator advances a
  slide; the sidecar's `_handle_slide_change` fans it out to both the
  recording sink and the director. No new WS message is needed.
- On receipt of an operator-sourced slide change: `ai_state = "following_human"`
  for 5 s, then back to `"tracking"`.
- Broadcasts `AIStatusEvent` over WS so the status pill in the UI can
  render the current mode.
- The presentation state itself has already changed in the UI by the
  time the override message arrives; the director is informational
  here, not authoritative.

`src/components/presentation/AIStatusPill.tsx`:
- Small badge in top-right of presentation view
- States: idle (gray) / tracking (green pulse) / following human (blue, 5 s)
- Clickable → opens debug panel

### 5. Session start/stop wiring

The "Start service" button from Phase 2 already starts a session. Now extend:
- If audio is configured (Phase 4 wizard run), default the AI to active
- If not, run in manual-only mode and show a one-time toast: "AI engine not configured — running manually"
- "Toggle AI on/off" in the bottom bar's overflow menu — operator can quickly switch between manual and AI modes

### 6. Debug panel — Belief state tab

`src/components/debug/BeliefStateTab.tsx`:
- For naive matcher: bar chart of per-slide fuzzy score in current song
- Highlights AI's currently-committed slide
- This UI is reused in Phase 6 with richer data (top-3 hypotheses with marginals); it's worth shipping a real version now so the visualization patterns are stable.

### 7. Baseline metrics + comparison harness wiring

`research/dead_ends/naive_matcher/run_baseline.py`:
- Iterates every recording under `data/recordings/` (or
  `~/Library/Application Support/AutoPresenter/recordings/` when running
  against the app's own captures)
- Runs naive matcher on each via the shared `research/eval/eval_pipeline.py`
  entrypoint (already shipped from Phase 3 work)
- Computes all four metrics
- Writes `Plan/BASELINE_NUMBERS.md` — committed to repo. **Phase 6 must
  strictly beat these numbers.**

### 8. Shadow mode by default

For the next 2 phases, default AI mode to **shadow** (decisions logged, slides not actually changed) on real services. This protects the operator while you iterate on quality. Toggle in Settings: "AI mode: off / shadow / active". Default for v1 release: `shadow` until validated; final release: `active`.

## Files to create

| Path | Purpose |
|---|---|
| `sidecar/auto_presenter/index/types.py` | Indexed types |
| `sidecar/auto_presenter/index/library.py` | Build + cache index |
| `sidecar/auto_presenter/matcher/types.py` | MatcherDecision |
| `sidecar/auto_presenter/matcher/naive.py` | Fuzzy matcher |
| `sidecar/auto_presenter/decision/director.py` | Matcher → SlideChange broadcaster + ai_state machine |
| `sidecar/auto_presenter/decision/types.py` | AIStatusEvent |
| `sidecar/auto_presenter/protocol.py` (extend) | AIStatusEvent, SetlistContent field on StartAudioSessionRequest (SlideChangeMessage + RecordSlideChangeNotification already exist) |
| `src/lib/aiDecisions.ts` | WS subscription → applies `SlideChange` via `jumpTo()` |
| `src/components/presentation/AIStatusPill.tsx` | Status badge |
| `src/components/debug/BeliefStateTab.tsx` | Per-slide score chart |
| `src/components/settings/AISettings.tsx` | Mode toggle (off/shadow/active) |
| `research/dead_ends/naive_matcher/run_baseline.py` | Baseline measurement |
| `Plan/BASELINE_NUMBERS.md` | Committed baseline metrics |
| `tests/sidecar/matcher/test_naive.py` | Unit tests on fixture transcripts |
| `tests/sidecar/decision/test_director.py` | AI/human interleave behavior |
| `tests/integration/test_ai_pipeline_e2e.py` | End-to-end |

## Success criteria

- [ ] Pipeline runs end-to-end: live mic → ASR → naive matcher → director → `SlideChangeMessage` broadcast → React `PresentationView.jumpTo()` updates the display
- [ ] AI status pill correctly cycles: idle → tracking → following human → tracking
- [ ] Override during AI session: AI follows human, resumes tracking after 5 s
- [ ] **Baseline metrics committed** in `Plan/BASELINE_NUMBERS.md`:
  - Wrong-slide rate (expected ~30%)
  - Anticipation error (expected positive median, late by 2–3 s)
  - Override rate
  - Recovery time on deviation fixtures
- [ ] Shadow mode works: AI runs but doesn't actually change slides; decisions logged
- [ ] Debug panel belief-state tab updates live; visualization is readable
- [ ] No flicker / bouncing on naive matcher decisions (debounced via "no duplicate consecutive same-slide commits")

## Tests required before phase complete

**Unit:**
- `test_naive.py`: known transcript window + library → expected slide; threshold behavior
- `test_director.py`: synthetic interleave of `MatcherDecision`s and `RecordSlideChangeNotification`s with `source="override"` → correct `ai_state` transitions; correct broadcast of `SlideChangeMessage`/`AIStatusEvent`

**Integration:**
- `test_ai_pipeline_e2e.py`: replay a recording through full pipeline → produces decision stream → metrics computed; matches snapshot
- Override during AI session: synthetic test sends a `RecordSlideChangeNotification` mid-session → director updates `ai_state` to `following_human` → broadcasts `AIStatusEvent` → pill UI updates

**Manual:**
- Live demo: sing a known song; AI advances slides (badly, but it advances)
- Override demo: sing into a known section; press arrow keys to force a deviation; verify AI stops fighting and follows

## Risks

- **Scope creep — making the naive matcher "smarter" wastes the phase.** Mitigation: hard rule — Phase 5's matcher is the documented `partial_ratio` algorithm. Improvements go in Phase 6.
- **Director loops** if AI re-issues a decision the controller just acted on. Mitigation: debounce on commit; AI doesn't fire same slide twice within 1 s.
- **Baseline numbers vary across recordings.** Mitigation: report median + p25/p75; commit per-recording numbers to `BASELINE_NUMBERS.md`.
- **Override flow feels laggy.** Mitigation: optimistic UI from Phase 2 already handles this; verify with a stopwatch that the 5-second "following human" UX feels right.

## Effort estimate

5–7 days.
