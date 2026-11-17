# Church Slides — Living UX & Design Review

> **Status:** Living document & implementation tracker (Pass 1 + Pass 2 — Oct 2026)  
> **Evaluated against:** `docs/PRODUCT_PRINCIPLES.md` (*"One product, one voice"*, *"Simple first, even when that means opinionated"*, *"Fewer concepts"*, *"One way to do a thing"*, *"Fixed, good defaults over settings"*, *"Automate what can be automated"*, *"Hide it until it's used"*)

---

## Implementation Plan & Progress Tracker (Split into 3 Reviewable Chunks)

### Chunk 1: Global UI Polish, Cross-Device Controls, Colour, Home & Setlist Decluttering
- [x] **1.1 Cross-device native form control normalization (`src/styles/globals.css`)**: Strip OS-specific Aqua/WebKit gradients from `<input type="checkbox">`, `<input type="radio">`, `<input type="range">`, and harden `<select>` defaults so all controls render identically across all macOS versions, iPhones, iPads, and Android tablets.
- [x] **1.2 Remove green "ready" dot next to `"Church Slides"` (`src/components/SidecarStatusBadge.tsx`)**: Return `null` when `status === "open"` (ready) or `"missing"` (normal offline/manual mode), only showing an indicator when connecting or unavailable/failed.
- [x] **1.3 Purposeful colour across the app (`KeyBadge`, section labels, warm dark palette)**:
  - Use chromatic `KeyBadge` (`src/components/KeyBadge.tsx`) in `LibraryView`, `SetlistBuilder`, and `SongEditor`.
  - Add semantic colour coding for song section headers (`verse`, `chorus`, `bridge`, `pre-chorus`, `tag`, `intro`, `outro`) across `SetlistSongSlides`, `OperatorView`, and `SongPreview`.
  - Warm the dark surface palette in `src/styles/globals.css` slightly toward the brand identity.
- [x] **1.4 Home screen setlist search, pagination, deletion & date formatting (`src/components/home/HomeScreen.tsx`)**:
  - Add setlist search (matching setlist name or song titles inside the setlist) and `"Show all (N)"` expansion when there are more than 6 setlists.
  - Add a hover delete button on setlist rows on Home with confirmation / undo.
  - Auto-prune untouched 0-song setlists when closing `SetlistBuilder` if they were just created and left empty.
  - Format dates older than 7 days as `"Jun 9"` (or `"9 Jun 2025"` across years) instead of raw locale numeric strings.
- [x] **1.5 Setlist Builder decluttering & library search cleanup (`src/components/setlists/SetlistBuilder.tsx`)**:
  - Remove `"OFTEN USED — OR TYPE TO SEARCH TITLES AND LYRICS"` sub-header and update the search input placeholder to `"Search songs or lyrics…"`.
  - Hide songs already in the setlist when the search query is empty (`searchQuery.trim() === ""`); show them with a higher-contrast `"in setlist"` badge when explicitly searched.
  - Simplify setlist song rows ("chips") by removing the first-line lyric snippet and slide count from the collapsed row, combining the expand click target, and hiding the `×` remove button until hover.
  - Move the setlist `Back` button to a clean `←` on the left of the header instead of `✕` next to `⋯`.
- [x] **1.6 Safety & polish fixes across Operator View, Song Editor, and Settings**:
  - **Operator View (`OperatorView.tsx`)**: Add confirmation/undo protection before removing a song mid-service, make slide card hover actions (`Duplicate` / `Delete`) consistent with `SetlistSongSlides`, and fix the `Recording` badge tooltip (`Settings → Other → Developer tools`).
  - **Song Editor (`SongEditor.tsx`, `ScriptureEditor.tsx`)**: Move `Delete song` (`Trash2`) into a `⋯` menu or away from the top-right `Close` button; add an `InfoTip` explaining Scripture's anchor lyric (`line it sits behind`).
  - **Settings (`SettingsPage.tsx`, `DisplayLookDialog.tsx`)**: Tuck the dev `InstructionModelPicker` (with `brew install ollama`) inside a collapsible disclosure in `Settings → AI`, and clarify in `DisplayLookDialog` that lyrics style applies to all setlists on this computer.

### Chunk 2: Rethinking the Chord Editor for Non-Technical Users
- [x] **2.1 Replace inline `A[G]mazing` bracket editing (`SongEditor.tsx`, `SongPreview.tsx`, `editorText.ts`)**:
  - Support natural two-line **"Chords over Lyrics"** editing & pasting (`parseTextChart` / `renderChordRows`) so lyrics are never split mid-word by `[G]` brackets.
  - Add interactive **click-word-to-place/edit chord** in the right-hand Slide Preview with a quick diatonic chord palette for the song's key (`C  Dm  Em  F  G  Am`), character-level chord dragging, a 12-option Major/Relative-Minor custom Key Picker, and a `♯` / `♭` toggle.
  - 📌 **Future Follow-up Note:** Revisit the **Song Editor** (`SongEditor.tsx` & `SongPreview.tsx`) in a dedicated future UX pass for deeper layout and workflow refinement.

### Chunk 3: Consolidating `SetlistBuilder` and `OperatorView` into One Unified Setlist Workspace
- [~] **3.1 Unified 3-pane Setlist Workspace** — *same layout, still two screens*:
  - [x] One consistent 3-column layout for prep and live (`[Setlist Rail] | [Continuous Slide Grid] | [Songs + Scripture Tabs]`).
  - [x] Auto-collapse global navigation when inside a setlist (`← Home` in top-left) while keeping a clear visual boundary between **Prep mode** (`Start service` CTA) and **Live mode** (`Audience fullscreen`, `AI: FOLLOWING`, `Show Blank Slide`).
  - [x] One slide card for both (`setlists/SetlistSlideCard.tsx`), one thumbnail background (`stageThumbnailStyle`), one arrow-key model (`lib/slideKeys.ts`).
  - [ ] Still open: `SetlistBuilder` (prep) and `OperatorView` (live) are separate components, so Start service remounts the workspace. Merging them into one component with a live/prep mode is the remaining step; it touches the live session hook, so it wants its own change and a live run.

---

## Part 1: Architectural & Product Proposals (Pass 2 Deep Dive)

### 1.1 Consolidating `SetlistBuilder` and `OperatorView` into One Unified Setlist Workspace

**Proposal:** Merge `SetlistBuilder` (`src/components/setlists/SetlistBuilder.tsx`) and `OperatorView` (`src/components/presentation/OperatorView.tsx`) into a single unified view, keeping the setlist song list on the far left and collapsing the global app navigation (`Home`, `Library`, `Feedback`, `Settings`) into compact persistent controls when inside a setlist.

#### Why this is a strong fit for the Product Principles
Right now, `SetlistBuilder` and `OperatorView` are ~80% duplicates of the same mental model, arranged in slightly different columns:
- **`SetlistBuilder`**: `[App Sidebar] | [Song List + Inline Expandable Slide Grid] | [Library Search]`
- **`OperatorView`**: `[Song Minimap + Blank Button] | [Continuous Slide Grid] | [Library Search + Scripture Tab]`

Having two separate screens violates **"Fewer concepts"** and **"One way to do a thing"** in several ways:
1. **Two different ways to view & edit a setlist's slides**: In `SetlistBuilder`, you click a `>` chevron to expand `SetlistSongSlides` inside a narrow middle column. In `OperatorView`, all songs' slides are laid out in a continuous center scroll canvas (`SongBlock`) with a song minimap on the left.
2. **A jarring layout shift when clicking "Start service"**: Everything on screen rearranges when going live, forcing a volunteer to re-orient between "prep layout" and "live layout".
3. **Missing Scripture during prep**: `LiveScripturePanel` is available as a tab on the right side of `OperatorView`, but is completely absent from `SetlistBuilder`.

#### Guardrails for the Unified Design
- **Explicit "Prep" vs. "Live" state in the top bar**:
  - **When not live (Prep)**: Clicking a slide selects or edits it; the top bar shows the `Start service` CTA.
  - **When live (Presenting)**: Clicking a slide projects it immediately (`Go to slide`); the active slide gets the thick white live ring (and amber AI suggestion ring in Assist mode); `Show Blank Slide` (`B`) and the `AI: FOLLOWING / ASSISTING` pill become active.
- **Single-Screen Mode (`PresentationView.tsx`) remains separate**:
  - When a user is on a single laptop screen (`Single screen — Present full-screen on this screen`), the whole screen *is* the projector (`SlideStage` + auto-hiding `BottomBar`).
- **Navigation chrome**:
  - Keep the full left sidebar on `Home`, `Library`, and `Settings` (where `Settings` expands its 5 sub-pages).
  - Collapse it when inside a Setlist, using a clean `← Home` button on the top-left of the Setlist header.

---

### 1.2 Searching & Managing Old Setlists (`HomeScreen.tsx`)

**Current state:**
- `HomeScreen.tsx` slices `setlists.slice(0, 6)`.
- There is no search input for setlists, no "View all" pagination, and no way to delete a setlist from Home.
- Clicking `+ New setlist` immediately inserts a row into SQLite. If a user clicks `+ New setlist` and backs out without adding songs, empty rows pile up and push real services past the 6-item limit.

**Design:**
1. **Auto-prune untouched empty setlists** when closing `SetlistBuilder`.
2. **Search & "Show all" on Home**: Filter across both setlist titles *and* the titles of songs inside each setlist, plus a `"Show all (N)"` toggle and a hover delete button on each row.

---

### 1.3 Rethinking the Chord Editor for Non-Technical Users (`SongEditor.tsx`)

**Why the current `[G]` inline editor feels janky (`src/lib/chords/editorText.ts`):**
1. **Musicians think in two lines (chords *above* words), not inline markup**: Splicing `A[G]mazing` breaks word legibility and spellcheck.
2. **Bracket overload**: `[verse 1]` and `[C]` share bracket syntax.
3. **Split-brain between left and right panes**: You type `[G]` in monospace on the left while looking at the right pane (`SongPreview`) to see if the blue chord landed on the right syllable.

**Redesign (Chunk 2):**
- Format the text chord view as standard **Chords over Lyrics** (two-line format, already parsed by `parseTextChart`), and allow clicking words directly in `SongPreview` to place/edit/remove chords with a diatonic key palette.

---

### 1.4 Exploring More Colour Across the App

1. **Chromatic Key Colours (`src/lib/chords/keyColor.ts` & `src/components/KeyBadge.tsx`)**:
   - The codebase already has an OKLCH 12-key color wheel (`KEY_COLORS`) calibrated so white text passes WCAG AA on every swatch, but `KeyBadge` was only used on mobile `LeaderSheets.tsx`. We will bring `KeyBadge` into `LibraryView`, `SetlistBuilder`, and `SongEditor`.
2. **Semantic colour coding for Song Section labels (`verse`, `chorus`, `bridge`, `tag`)**:
   - Give section badges on slide cards distinct, tasteful hues (`chorus` = warm amber, `bridge` = violet, `pre-chorus` = sky, `tag`/`outro` = emerald, `verse` = stone/muted) so operators can visually scan a 25-slide grid in a split second.
3. **Warm dark surfaces (`src/styles/globals.css`)**:
   - Gently warm the dark background/card/border tokens toward the brand identity.

---

## Part 2: Cross-Device Native UI Control Rendering Investigation

### Root Cause
In `src/styles/globals.css:67-81`, native `<select>` elements were given `-webkit-appearance: none; appearance: none;` because WebKit on newer macOS versions draws native dark-mode Aqua controls with a metallic vertical gradient bezel. However, `<input type="checkbox">` (18 instances), `<input type="radio">` (3 instances), and `<input type="range">` (2 instances) still used `appearance: auto`, causing OS-dependent gradient bezels and inconsistent system accent colors across different Macs and tablets.

### Solution (`src/styles/globals.css`)
Normalize `input[type="checkbox"]`, `input[type="radio"]`, `input[type="range"]`, and `select` globally in `src/styles/globals.css` using `-webkit-appearance: none; appearance: none;` with flat borders, flat fills, and embedded SVG checkmarks/dots.
