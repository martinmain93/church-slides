# Undo support — recommendation

**Status:** design proposal, not yet implemented
**Date:** 2026-06-03

## TL;DR

The app has **no undo/redo anywhere**. The only safety nets are confirm dialogs
(`src/lib/confirm.tsx`) and a couple of "Reset" buttons. The newly shipped
per-setlist slide editor (`SetlistSongSlides.tsx`) makes delete and reorder
**immediate and irreversible**, which is the sharpest gap — operators are
editing under time pressure (live service prep) and a mis-click loses a slide
with no recovery short of "Reset", which nukes *all* edits.

**Recommend a phased approach:**

1. **Now (small):** add a localized per-editor undo/redo stack to
   `SetlistSongSlides.tsx` (history of `EditableSlide[]`, `Cmd/Ctrl+Z` /
   `Cmd/Ctrl+Shift+Z`), reusing the existing debounced-persist path so undo
   re-persists automatically.
2. **Soon (small):** add an **"Undo" action button to the toast system** and
   use it for the genuinely lossy, cross-component actions that *aren't* simple
   list edits — primarily **setlist item removal** (currently immediate, with
   no confirm at all).
3. **Later (optional):** factor the in-memory history into a reusable
   `useUndoableHistory<T>()` hook if/when a second editor needs it (e.g.
   SongEditor body). Don't build the abstraction before the second caller.

---

## 1. Where undo matters most

Ranked by blast radius × likelihood × current safety:

| Action | Location | Current guard | Reversible? | Priority |
|---|---|---|---|---|
| **Slide delete** | `SetlistSongSlides.handleDelete` | none | only via Reset (loses all edits) | **High** |
| **Slide reorder** | `SetlistSongSlides.handleDragEnd` | none | no | **High** |
| **Slide edit (lyrics)** | `SetlistSongSlides.handleEdit` | none | no | Medium |
| **Setlist item remove** | `SetlistBuilder.handleRemove` | **none** (immediate, no confirm) | no | **High** |
| **Setlist song reorder** | `SetlistBuilder.handleDragEnd` | none | no | Medium |
| Song body edit | `SongEditor` (autosave) | none | no | Low–Medium |
| Song delete | `SongEditor.handleDelete` | confirm dialog | no | Low (already guarded) |
| Setlist delete | `SetlistBuilder` | confirm dialog | no | Low (already guarded) |

Two things stand out beyond the slide editor:

- **`SetlistBuilder.handleRemove` removes a setlist item immediately with no
  confirm and no undo** (lines 143–151). That's arguably a worse gap than the
  slide editor, because removal is a one-shot destructive op with no Reset to
  fall back on. It's the strongest candidate for the toast-Undo affordance.
- Slide *edits* are lower priority because the text isn't truly lost the instant
  you type — but a delete or reorder is.

## 2. Options & tradeoffs

### (a) Localized per-editor undo stack — in `SetlistSongSlides`
Keep a `past: EditableSlide[][]` / `future: EditableSlide[][]` around the existing
`slides` state. Every `mutate()` pushes the prior list onto `past`. `undo()` pops
`past` → current, pushes current → `future`, and (crucially) routes through the
same dirty/persist path so the override is re-saved.

- **+** Smallest, most contained change; the editor already centralizes every
  change through `mutate()` (one chokepoint to instrument).
- **+** Natural fit: undo here means "restore the previous `EditableSlide[]`",
  which is exactly the unit we already debounce-persist.
- **+** Reconciles cleanly with persistence — undo is just another `mutate`, so
  the 600ms debounce re-persists the reverted list for free.
- **−** Scoped to this component; doesn't help SetlistBuilder/SongEditor.
- **−** Coarse granularity (whole-list snapshots), but the lists are tiny
  (a song's slides), so memory is a non-issue.

### (b) Reusable `useUndoableState` / history hook across editors
Generic `useUndoableHistory<T>(initial)` returning `{ state, set, undo, redo,
canUndo, canRedo, reset }`, applied to the slide list, the song body, and the
setlist item list.

- **+** One implementation, consistent UX, testable in isolation.
- **+** Pays off if multiple editors adopt undo.
- **−** Each editor persists differently (slide override vs. `updateSong`
  autosave vs. `reorderSetlist`/`removeSetlistItem` Tauri calls). A generic
  *state* hook doesn't solve the *persistence reconciliation*, which is the hard
  part and is per-call-site anyway. Risk of building an abstraction that still
  needs bespoke glue at every site.
- **−** SongEditor body undo partly overlaps the browser's native textarea undo
  already — marginal value, more surface.
- **Verdict:** right *eventual* shape, premature as a first step. Extract it from
  (a) once there's a second real caller.

### (c) Toast-based "Undo" affordance after destructive actions
After a delete/remove, show a toast with an **Undo** button that reverses the op
(e.g. re-insert the removed setlist item, restore the deleted slide). Requires
**extending the toast API**, which currently has no action-button support
(`src/lib/toast.tsx` — `ToastApi` is `show/success/error/dismiss` only).

- **+** Best UX for one-shot, cross-component destructive ops — especially
  `SetlistBuilder.handleRemove`, where there's no in-editor history to lean on.
- **+** Discoverable; doesn't depend on the user knowing a keybind.
- **−** Needs a toast API change (add optional `action: { label, onClick }`).
- **−** Time-boxed (toast auto-dismisses) — not a full history, just last-action
  rescue. Fine for "remove", weak for "I made 5 edits, step back through them".
- **−** For server-backed ops (remove item), undo must re-call a Tauri command;
  the original ordering/position must be captured before removal.

### (d) Do nothing — rely on confirm dialogs + Reset
- **+** Zero work.
- **−** Confirm-on-every-delete is fatiguing and slows live prep; the slide
  editor deliberately made delete frictionless, so adding a confirm there would
  regress the UX it was designed for. Reset is all-or-nothing. Not adequate for
  the High-priority rows above.

## 3. Recommended approach & scope

**Do (a) + (c), skip (b) for now.**

### Phase 1 — Per-editor undo in `SetlistSongSlides` *(recommended first)*
- Add `past`/`future` snapshot stacks around `slides`. Push in `mutate()`;
  clear `future` on any new mutation.
- `undo()`/`redo()` set `slides` through the dirty path so the debounced
  `setSetlistItemSlides` re-persists the reverted list. (Reset/SaveToLibrary
  should clear both stacks.)
- Keybindings: `Cmd/Ctrl+Z` undo, `Cmd/Ctrl+Shift+Z` redo, scoped to the editor
  (see §4). Optionally surface small Undo/Redo buttons next to Reset for
  discoverability.
- Cap history (e.g. 50 entries) — cheap insurance; lists are tiny.
- **Scope:** ~half a day. One file, plus a couple of unit tests on the
  push/undo/redo/persist reconciliation.

### Phase 2 — Toast "Undo" for setlist item removal
- Extend `ToastApi.show` to accept an optional `action: { label: string;
  onClick: () => void }`, render a button in `ToastItem`, keep it non-sticky but
  give it a longer duration (~8s).
- In `SetlistBuilder.handleRemove`, capture the removed item + its index, then
  show a success toast with **Undo** that re-adds it (re-call the add/reorder
  Tauri path to restore position) and refreshes.
- **Scope:** ~half a day (toast API change is the bulk; first consumer is small).

### Phase 3 — Extract reusable hook *(only when a 2nd editor needs it)*
- Lift Phase 1's stack logic into `useUndoableHistory<T>()` and reuse for, e.g.,
  SongEditor body. Defer until there's a concrete second caller.
- **Scope:** ~half a day to extract + migrate, when justified.

**Persistence reconciliation (applies throughout):** undo must re-persist, not
just mutate UI state. Phase 1 gets this for free by routing undo through the
existing dirty/debounce path. Phase 2 must re-issue the Tauri command. Never let
undo leave the UI and the DB disagreeing.

## 4. Keybinding considerations

- The presentation view owns a **global** `keydown` handler driven by
  `src/lib/presentation/keymap.ts`. It does **not** currently bind `Cmd/Ctrl+Z`,
  so there's no direct collision — but undo bindings should live **local to the
  editor**, not in the global presentation map, since the two contexts never
  coexist on screen and undo is meaningless in presentation mode.
- Editors live inside form fields (textarea). When the slide textarea is focused,
  the browser's **native textarea undo** also responds to `Cmd/Ctrl+Z`. To avoid
  fighting it: only intercept `Cmd/Ctrl+Z` for the *list-level* history when the
  event target is **not** an editable field actively being edited (i.e. when
  `editingUid` is null / focus is on a card, not the textarea). While typing in a
  slide, let native textarea undo win; for delete/reorder/structural changes, our
  stack handles it.
- Attach the listener on the editor's root container (or a `useEffect` guarded by
  the editor being mounted/expanded) and `preventDefault()` only when we actually
  handle it, so unhandled keys fall through.
- Consider the macOS redo convention `Cmd+Shift+Z` plus the Windows/Linux
  `Ctrl+Y` as an alternate.

## Recommendation summary

Start with **Phase 1** (localized slide-editor undo) — highest value, smallest
blast radius, and it slots straight into the existing `mutate()` + debounce
plumbing. Follow with **Phase 2** (toast Undo for setlist item removal) to cover
the one-shot cross-component delete that has no in-editor history and currently
not even a confirm. Treat the reusable hook as a **later refactor**, extracted
from working code once a second editor genuinely needs it. Total near-term
scope: **~1 day**.
