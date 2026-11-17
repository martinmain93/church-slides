# Phase 1 — Song library: editor, search, import

## Goal

A clean dark-mode library screen where the operator can write songs in the text-blob format, search the library by title and content, and import existing songs (ProPresenter feature-flagged). Auto-save on every edit. JSON export/import for backup. No setlist or presentation logic yet — that's Phase 2.

## Prerequisites

- Phase 0 complete (Tauri + Python sidecar + SQLite + FTS5 + codegen working)

## Scope

**In:**
- shadcn/ui scaffolding (`components.json`, base components: Button, Input, Dialog, Toast, Card, ScrollArea, DropdownMenu) — **inherited from Phase 0 deferral**
- Library list view: search box (title + content via FTS5), sort options (recent, popular, alphabetical), song row with title + first-line preview
- Song editor: single text area with line-break-delimited slides and `[section]` tags on their own lines; live preview pane showing parsed slides + section labels
- Title field, optional metadata fields (CCLI, author, copyright, default tempo)
- Body-text → Song parser (canonical: live, in Python sidecar; mirrored fast path in TS for editor preview)
- Auto-save with debounce (1 s after last keystroke)
- Library JSON export/import
- ProPresenter 7 import (behind feature flag — `enable_propresenter_import = false` by default)
- Settings page scaffold (theme, default tempo, feature flags)
- Empty-state UX with "Create song" + "Import" CTAs

**Out:**
- Setlist builder (Phase 2)
- Presentation mode (Phase 2)
- AI engine (Phase 3+)
- ChordPro / chord display
- Multi-language support

## Implementation steps

### 1. Body-text format spec

Document this format authoritatively in `docs/SONG_FORMAT.md` so the parser, editor preview, and import logic all agree:

```
[verse 1]
Amazing grace how sweet the sound
That saved a wretch like me

[chorus]
Grace upon grace now covers me
My shepherd and my Savior leads me home

[verse 2]
'Twas grace that taught my heart to fear
And grace my fears relieved
```

Rules:
- Lines starting with `[…]` are section tags. Tag text is normalized: `[verse 1]`, `[Verse 1]`, `[v1]`, `[V1]` all map to `Section(type="verse", number=1)`.
- Empty lines separate slides within a section.
- Single newline = within a slide (slide can have multiple lines).
- A new `[section]` tag starts a new slide automatically.
- Section types recognized: verse, chorus, bridge, tag, intro, outro, pre-chorus, instrumental. Unknown tags become `other` with the tag preserved.

### 2. Parser (Python sidecar — canonical)

`sidecar/auto_presenter/library/parser.py`:
- `def parse_body_text(body: str, song_id: str) -> tuple[list[Slide], list[Section]]`
- Pure function; deterministic IDs (hash of song_id + slide content + order)
- Comprehensive unit tests with edge cases (no section tags, all one section, weird spacing, smart quotes, etc.)

### 3. Parser (TypeScript — fast preview)

`src/lib/parseSong.ts`:
- Same algorithm, ported. Used only for live editor preview.
- Cross-tested: a battery of fixture body-text strings must produce identical outputs from the Python and TS parsers (CI test).

### 4. Library list UI

`src/components/library/LibraryList.tsx`:
- Top bar: search input (debounced 200 ms), sort dropdown, "+ New song" button, "..." menu (import, export)
- Song rows: title (bold), first-line preview (muted), section-count badge, last-edited timestamp on hover
- Click → opens editor for that song
- Keyboard: `↑ ↓` to navigate, `Enter` to open, `/` to focus search

`src/components/library/LibrarySort.tsx`:
- Options: "Recently edited", "Popular" (count of setlists this song has appeared in — see SQL below), "A–Z"

### 5. FTS5 search query

`src-tauri/src/db.rs`:
```sql
SELECT s.* FROM songs s
JOIN songs_fts ON songs_fts.rowid = s.rowid
WHERE songs_fts MATCH :query
ORDER BY bm25(songs_fts);
```

For "popular" sort:
```sql
SELECT s.*, COALESCE(p.usage_count, 0) AS usage_count FROM songs s
LEFT JOIN (
  SELECT song_id, COUNT(*) AS usage_count
  FROM setlist_items
  WHERE kind = 'song' GROUP BY song_id
) p ON p.song_id = s.id
ORDER BY usage_count DESC, s.title COLLATE NOCASE;
```

### 6. Song editor

`src/components/library/SongEditor.tsx`:
- Two-column layout: left = monospace textarea bound to `body_text`; right = live preview pane showing each parsed slide as a card with its section label
- Title field at top
- Metadata expanded section (CCLI, author, copyright, default tempo wps) — collapsed by default to reduce clutter
- Auto-save: debounced 1 s after last keystroke; Tauri `db_execute` to update; visual indicator (subtle "Saved · 2s ago" in corner)
- "Delete song" action behind confirmation dialog
- Keyboard: `Cmd/Ctrl+S` forces save (already debounced — the keypress just flushes the pending save)

### 7. JSON export/import

`sidecar/auto_presenter/library/export.py`:
- `def export_library() -> bytes` — emit all songs as a single JSON file (versioned schema)
- `def import_library(blob: bytes, mode: Literal["merge","replace"])`
- Tauri command opens native file picker; sidecar handles parse + DB write

`docs/EXPORT_FORMAT.md`:
- Document the schema with version field for forward compatibility

### 8. ProPresenter import (feature-flagged)

`sidecar/auto_presenter/library/import_propresenter.py`:
- Parses ProPresenter 7 `.pro` files (protobuf format)
- ProPresenter library lives at `~/Documents/ProPresenter/Libraries/<library name>/*.pro`
- Best-effort: extract title + slide text per slide; map to `body_text` heuristically (no section tags from ProPresenter — they use slide labels which we map to closest section type)
- Behind `enable_propresenter_import` feature flag in `Settings`
- Show preview of imported song before confirming write

This module is allowed to be incomplete or absent at v1 release. The flag stays `false` until the parser is robust on a corpus of real `.pro` files.

### 9. Settings page

`src/components/settings/SettingsPage.tsx`:
- Theme: system/light/dark (default: dark)
- Default tempo: words-per-second prior used by AI engine (default 2.0; only relevant from Phase 5 onward)
- Feature flags: ProPresenter import, debug panel keybinding (Phase 2 default `Cmd+D`), telemetry (Phase 8)
- About: version, sidecar version, app data dir path, export library button

### 10. Empty state + onboarding

When library is empty:
- Big card: "Get started"
- Three CTAs: "Create your first song" / "Import library JSON" / "Import from ProPresenter" (last hidden if flag off)
- Link to format docs

## Files to create

| Path | Purpose |
|---|---|
| `docs/SONG_FORMAT.md` | Authoritative format spec |
| `docs/EXPORT_FORMAT.md` | JSON export schema |
| `sidecar/auto_presenter/library/parser.py` | Python parser (canonical) |
| `sidecar/auto_presenter/library/export.py` | Library JSON export/import |
| `sidecar/auto_presenter/library/import_propresenter.py` | PP7 import (feature-flagged) |
| `sidecar/auto_presenter/library/queries.py` | DB read helpers |
| `src/lib/parseSong.ts` | TS preview parser |
| `src/components/library/LibraryList.tsx` | List screen |
| `src/components/library/LibrarySort.tsx` | Sort dropdown |
| `src/components/library/SongEditor.tsx` | Editor screen |
| `src/components/library/SongPreview.tsx` | Right-pane parsed-slide preview |
| `src/components/library/MetadataSection.tsx` | Collapsible metadata |
| `src/components/settings/SettingsPage.tsx` | Settings |
| `src/components/common/SearchBar.tsx`, `IconButton.tsx`, `Toast.tsx` | Shared UI primitives |
| `src/lib/sortSongs.ts` | Sort logic on the JS side (uses popularity count from SQL) |
| `src-tauri/src/db.rs` (extended) | FTS5 query, popularity SQL |
| `tests/sidecar/test_parser.py` | 30+ fixture body-text → expected slides |
| `tests/sidecar/test_export.py` | JSON roundtrip |
| `tests/sidecar/test_import_propresenter.py` | If parser is implemented |
| `tests/parser_parity_test.py` | Cross-tests Python and TS parsers |
| `src/__tests__/SongEditor.test.tsx` | Edit + autosave behavior |
| `src/__tests__/LibraryList.test.tsx` | Search + sort behavior |
| `data/fixtures/songs/*.txt` | 20+ realistic test songs |

## Success criteria

- [x] Library list shows all songs in DB with FTS5 search working (`<200 ms` for 5000-song queries)
- [x] Sort by popular returns songs ordered by setlist appearance count — SQL ships in Phase 1; **meaningful ordering** verified in Phase 2 once setlists exist (popularity table is empty in Phase 1 so all songs tie at 0 — fallback alphabetical)
- [x] Editor parses body text live; preview matches Python parser output
- [x] Auto-save fires within 1 s of last edit; no save spam during active typing
- [x] JSON export → import roundtrip preserves every song bit-identical
- [x] Editor handles 1000+-line bodies without lag (rare but possible for medleys)
- [x] Empty state guides a brand-new user from zero to first song in < 1 minute
- [x] Settings page reads/writes flags persistently
- [ ] If ProPresenter import shipped: 90% of test `.pro` files parse without crash — **DEFERRED**: flag + scaffolding ship in Phase 1, full parser ships when a corpus of real `.pro` files is available. Tracked as carry-over below.

**Deferrals out of Phase 1 (recorded for traceability):**
- ProPresenter 7 `.pro` parser body — flag + UI + Tauri command stub ship now; parsing implementation ships in a later patch release once we have ≥ 5 real `.pro` files to test against. Plan section explicitly authorizes this: *"This module is allowed to be incomplete or absent at v1 release."*
- Popularity sort *with non-empty data* — verified at the end of Phase 2 once setlists exist.

## Tests required before phase complete

**Unit:**
- `test_parser.py`: 30+ fixture inputs covering: standard form, no tags, mixed-case tags, smart quotes, Windows line endings, blank lines abuse, unicode, very long slides
- `test_export.py`: roundtrip preserves every field; version-mismatched import surfaces clear error
- `parser_parity_test.py`: every fixture body-text produces identical Slide+Section list from Python and TS parsers (literal JSON-equality)

**Integration:**
- Create song via editor → close app → reopen → song persists with all metadata
- Search "amazing grace" returns expected song; search "wretch like me" (content match) also returns it
- Sort by popular: insert songs A,B,C; create setlists using A 5x, B 2x, C 0x; verify order A,B,C
- Export → wipe DB → import → library identical (songs table, FTS5 populated, popularity preserved)

**Manual:**
- Edit a 50-slide medley; verify smooth scroll + autosave latency
- ProPresenter import (if implemented): try with 5+ real church `.pro` files, verify titles + lyrics roughly correct

## Risks

- **Parser parity drift.** Mitigation: CI test that runs both parsers on every fixture; commit fixture corpus.
- **ProPresenter format complexity.** Mitigation: ship without it if it's not solid by Phase 1 deadline. The feature flag exists for this reason.
- **FTS5 query syntax footguns** (e.g. user types `Christ's` and breaks the query). Mitigation: sanitize input before MATCH; fall back to LIKE on parse error.
- **Auto-save loss on crash mid-debounce.** Mitigation: also flush save on window blur and on app close; persist a transient "draft" copy until save completes.
- **Editor UX feels off.** Mitigation: have someone non-technical try it in week 1; iterate.

## Effort estimate

7–10 days (PP import adds 3–5 if attempted seriously).
